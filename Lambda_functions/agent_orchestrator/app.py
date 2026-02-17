import json
import os
import time
import logging
import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime
import re

# =====================================================
# Logging Setup (Structured)
# =====================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def log_event(stage, data):
    logger.info(json.dumps({
        "stage": stage,
        "data": data
    }))

# =====================================================
# AWS Clients
# =====================================================

lambda_client = boto3.client("lambda")
bedrock = boto3.client("bedrock-runtime")
dynamodb = boto3.resource("dynamodb")

# =====================================================
# Environment Variables (Validated)
# =====================================================

def get_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

ROUTER_MODEL = get_env("AWS_BEDROCK_ROUTER_MODEL")
SYNTH_MODEL = get_env("AWS_BEDROCK_SYNTH_MODEL")
FALLBACK_MODEL = get_env("AWS_BEDROCK_FALLBACK_MODEL")

GET_POLICY_FUNCTION = get_env("GET_POLICY_FUNCTION")
CHECK_DOC_FUNCTION = get_env("CHECK_DOC_FUNCTION")
GET_CLAIM_FUNCTION = get_env("GET_CLAIM_FUNCTION")

CONVERSATION_TABLE = get_env("CONVERSATION_TABLE")

table = dynamodb.Table(CONVERSATION_TABLE)

# =====================================================
# Utility: Safe JSON Parsing
# =====================================================

def safe_json_parse(text):
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise

# =====================================================
# Bedrock Invocation (Converse API)
# =====================================================

def call_model(model_id, prompt, temperature=0, max_tokens=600):
    start = time.time()

    response = bedrock.converse(
        modelId=model_id,
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": temperature
        },
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}]
            }
        ]
    )

    latency = round(time.time() - start, 3)
    log_event("bedrock_call", {"model": model_id, "latency": latency})

    return response["output"]["message"]["content"][0]["text"]

# =====================================================
# Router
# =====================================================

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
        raw = call_model(ROUTER_MODEL, prompt, temperature=0)
    except Exception as e:
        log_event("router_primary_failed", str(e))
        raw = call_model(FALLBACK_MODEL, prompt, temperature=0)

    decision = safe_json_parse(raw)

    if decision.get("confidence") == "low":
        log_event("router_low_confidence", decision)
        # escalate to stronger model
        raw = call_model(SYNTH_MODEL, prompt, temperature=0)
        decision = safe_json_parse(raw)

    log_event("routing_decision", decision)
    return decision

# =====================================================
# Tool Invocation
# =====================================================

def invoke_lambda(function_name, payload):
    start = time.time()

    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload)
    )

    result = json.loads(response["Payload"].read())

    latency = round(time.time() - start, 3)
    log_event("tool_invocation", {
        "function": function_name,
        "latency": latency
    })

    # unwrap API Gateway style lambdas
    if isinstance(result, dict) and "body" in result:
        try:
            return json.loads(result["body"])
        except Exception:
            return result["body"]

    return result

def invoke_selected_tool(decision, user_query):
    tool = decision.get("tool")

    mapping = {
        "get_policy_details": GET_POLICY_FUNCTION,
        "check_document_requirements": CHECK_DOC_FUNCTION,
        "get_claim_status": GET_CLAIM_FUNCTION
    }

    function_name = mapping.get(tool)

    if not function_name:
        return {"error": "Invalid tool selected"}

    return invoke_lambda(function_name, {"query": user_query})

# =====================================================
# Memory
# =====================================================

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
            "timestamp": datetime.utcnow().isoformat(),
            "user": user_query,
            "assistant": assistant_reply
        }
    )

# =====================================================
# Synthesis
# =====================================================

def build_synthesis_prompt(user_query, tool_result, history):
    history_text = ""
    for item in reversed(history):
        history_text += f"\nUser: {item['user']}\nAssistant: {item['assistant']}\n"

    return f"""
    You are NorthStar, a professional insurance AI assistant.

    You operate inside a regulated insurance environment.
    Accuracy is critical.

    Conversation history:
    {history_text}

    Current user question:
    {user_query}

    Verified internal system data:
    {json.dumps(tool_result, indent=2)}

    Instructions:

    1. Use ONLY the verified internal data provided.
    2. Do NOT invent coverage, limits, dates, or amounts.
    3. If information is missing, say clearly what is unavailable.
    4. Maintain a professional, calm, and clear tone.
    5. Provide concise but complete explanations.
    6. Avoid internal technical wording.
    7. Never mention internal tools or system architecture.

    If the internal data contains an error field, explain politely that the request could not be fulfilled.

    Your response should:
    - Directly answer the user.
    - Be natural and conversational.
    - Be precise and trustworthy.
    """

def generate_response(user_query, tool_result, history):

    if isinstance(tool_result, dict) and "error" in tool_result:
        return "I'm sorry, but I couldn't retrieve the requested information at this time."

    prompt = build_synthesis_prompt(user_query, tool_result, history)

    try:
        return call_model(SYNTH_MODEL, prompt, temperature=0.4)
    except Exception as e:
        log_event("synth_primary_failed", str(e))
        return call_model(FALLBACK_MODEL, prompt, temperature=0.2)

# =====================================================
# Lambda Handler
# =====================================================

def lambda_handler(event, context):

    try:
        log_event("incoming_event", event)

        body = json.loads(event.get("body", "{}"))
        user_query = body.get("query")
        session_id = body.get("session_id", "default")

        if not user_query:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'query'"})
            }

        # 1. Memory
        history = get_conversation_history(session_id)

        # 2. Routing
        decision = route_query(user_query)

        # 3. Tool
        tool_result = invoke_selected_tool(decision, user_query)

        # 4. Synthesis
        final_answer = generate_response(user_query, tool_result, history)

        # 5. Persist memory
        store_message(session_id, user_query, final_answer)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "answer": final_answer,
                "tool_used": decision.get("tool"),
                "confidence": decision.get("confidence")
            })
        }

    except Exception as e:
        logger.exception("Unhandled error")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"})
        }