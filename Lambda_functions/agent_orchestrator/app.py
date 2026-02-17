import json
import os
import time
import boto3
from boto3.dynamodb.conditions import Key

# ----------------------------------------
# AWS Clients
# ----------------------------------------

lambda_client = boto3.client("lambda")
bedrock = boto3.client("bedrock-runtime")
dynamodb = boto3.resource("dynamodb")

# ----------------------------------------
# Environment Variables
# ----------------------------------------

ROUTER_MODEL = os.environ["AWS_BEDROCK_ROUTER_MODEL"]
SYNTH_MODEL = os.environ["AWS_BEDROCK_SYNTH_MODEL"]
FALLBACK_MODEL = os.environ["AWS_BEDROCK_FALLBACK_MODEL"]

GET_POLICY_FUNCTION = os.environ["GET_POLICY_FUNCTION"]
CHECK_DOC_FUNCTION = os.environ["CHECK_DOC_FUNCTION"]
GET_CLAIM_FUNCTION = os.environ["GET_CLAIM_FUNCTION"]

CONVERSATION_TABLE = os.environ["CONVERSATION_TABLE"]

table = dynamodb.Table(CONVERSATION_TABLE)

# ----------------------------------------
# Utility: Call Bedrock (Converse API)
# ----------------------------------------

def call_model(model_id, prompt, temperature=0):
    response = bedrock.converse(
        modelId=model_id,
        inferenceConfig={
            "maxTokens": 600,
            "temperature": temperature
        },
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}]
            }
        ]
    )

    return response["output"]["message"]["content"][0]["text"]

# ----------------------------------------
# Router Prompt
# ----------------------------------------

def build_router_prompt(user_query):
    return f"""
    You are the routing engine for NorthStar, an enterprise insurance AI system.

    Your responsibility:
    - Select the single most appropriate internal tool.
    - Do NOT answer the user directly.
    - Do NOT generate explanations outside JSON.
    - If the request is unclear, select the safest relevant tool.

    Available tools:

    1. get_policy_details
    Use for:
    - Coverage questions
    - Policy benefits
    - Policy status
    - Limits or inclusions

    2. check_document_requirements
    Use for:
    - Required documents
    - Supporting paperwork
    - Submission requirements

    3. get_claim_status
    Use for:
    - Claim progress
    - Claim status
    - Payment status
    - Claim ID inquiries

    Decision Rules:
    - Choose EXACTLY one tool.
    - Never invent a new tool.
    - Never leave the tool field empty.
    - Always return valid JSON.

    Response format:
    {{
    "tool": "<tool_name>",
    "confidence": "high | medium | low",
    "reason": "<brief reasoning in one sentence>"
    }}

    User request:
    \"\"\"{user_query}\"\"\"
    """

def route_query(user_query):
    prompt = build_router_prompt(user_query)

    try:
        response = call_model(ROUTER_MODEL, prompt, temperature=0)
    except Exception as e:
        print("Router primary failed:", str(e))
        response = call_model(FALLBACK_MODEL, prompt, temperature=0)

    return json.loads(response)

# ----------------------------------------
# Tool Invocation
# ----------------------------------------

def invoke_lambda(function_name, payload):
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload)
    )

    return json.loads(response["Payload"].read())

def invoke_selected_tool(decision, user_query):
    tool = decision.get("tool")

    if tool == "get_policy_details":
        return invoke_lambda(GET_POLICY_FUNCTION, {"query": user_query})

    elif tool == "check_document_requirements":
        return invoke_lambda(CHECK_DOC_FUNCTION, {"query": user_query})

    elif tool == "get_claim_status":
        return invoke_lambda(GET_CLAIM_FUNCTION, {"query": user_query})

    else:
        return {"error": "Unknown tool selected"}

# ----------------------------------------
# Memory Handling
# ----------------------------------------

def get_conversation_history(session_id, limit=5):
    response = table.query(
        KeyConditionExpression=Key("session_id").eq(session_id),
        ScanIndexForward=False,
        Limit=limit
    )
    return response.get("Items", [])

def store_message(session_id, user_query, assistant_reply):
    table.put_item(
        Item={
            "session_id": session_id,
            "timestamp": str(time.time()),
            "user": user_query,
            "assistant": assistant_reply
        }
    )

# ----------------------------------------
# Response Synthesis
# ----------------------------------------

def build_synthesis_prompt(user_query, tool_result, history):
    history_text = ""
    for item in reversed(history):
        history_text += f"\nUser: {item['user']}\nAssistant: {item['assistant']}\n"

    return f"""
    You are NorthStar, an intelligent insurance assistant.

    Conversation history:
    {history_text}

    User question:
    {user_query}

    Internal tool result:
    {json.dumps(tool_result, indent=2)}

    Instructions:
    - Provide a clear, professional response.
    - Be helpful and concise.
    - Do not invent information.
    - Only use tool results.
    """

def generate_response(user_query, tool_result, history):
    prompt = build_synthesis_prompt(user_query, tool_result, history)

    try:
        return call_model(SYNTH_MODEL, prompt, temperature=0.4)
    except Exception as e:
        print("Synth model failed:", str(e))
        return call_model(FALLBACK_MODEL, prompt, temperature=0.2)

# ----------------------------------------
# Lambda Handler
# ----------------------------------------

def lambda_handler(event, context):

    print("Incoming event:", json.dumps(event))

    body = json.loads(event.get("body", "{}"))
    user_query = body.get("query", "")
    session_id = body.get("session_id", "default")

    if not user_query:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing 'query' field"})
        }

    # 1. Retrieve memory
    history = get_conversation_history(session_id)

    # 2. Routing
    decision = route_query(user_query)
    print("Routing decision:", decision)

    # 3. Tool invocation
    tool_result = invoke_selected_tool(decision, user_query)
    print("Tool result:", tool_result)

    # 4. Generate final answer
    final_answer = generate_response(user_query, tool_result, history)

    # 5. Store conversation
    store_message(session_id, user_query, final_answer)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "answer": final_answer,
            "tool_used": decision.get("tool")
        })
    }