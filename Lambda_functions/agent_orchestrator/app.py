import json
import os
import time
import logging
import boto3
from boto3.dynamodb.conditions import Key
from datetime import datetime
import re
import requests
from requests_aws4auth import AWS4Auth

# =====================================================
# Logging
# =====================================================

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def log(stage, data):
    logger.info(json.dumps({"stage": stage, "data": data}))

# =====================================================
# Environment
# =====================================================

def get_env(name, required=True):
    value = os.environ.get(name)
    if required and not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value

ROUTER_MODEL = get_env("AWS_BEDROCK_ROUTER_MODEL")
SYNTH_MODEL = get_env("AWS_BEDROCK_SYNTH_MODEL")
FALLBACK_MODEL = get_env("AWS_BEDROCK_FALLBACK_MODEL")

GET_POLICY_FUNCTION = get_env("GET_POLICY_FUNCTION")
CHECK_DOC_FUNCTION = get_env("CHECK_DOC_FUNCTION")
GET_CLAIM_FUNCTION = get_env("GET_CLAIM_FUNCTION")

CONVERSATION_TABLE = get_env("CONVERSATION_TABLE")

OPENSEARCH_ENDPOINT = get_env("OPENSEARCH_ENDPOINT")
RAG_INDEX = get_env("RAG_INDEX")

AWS_REGION = os.environ.get("AWS_REGION", "eu-north-1")

# =====================================================
# AWS Clients
# =====================================================

lambda_client = boto3.client("lambda")
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(CONVERSATION_TABLE)

# =====================================================
# OpenSearch Auth (Refreshable)
# =====================================================

def get_awsauth():
    session = boto3.Session()
    credentials = session.get_credentials()
    frozen = credentials.get_frozen_credentials()

    return AWS4Auth(
        frozen.access_key,
        frozen.secret_key,
        AWS_REGION,
        "aoss",
        session_token=frozen.token
    )

# =====================================================
# Utilities
# =====================================================

def safe_json(text):
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise

# =====================================================
# Embedding
# =====================================================

def embed_text(text):
    response = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0",
        body=json.dumps({"inputText": text}),
        contentType="application/json",
        accept="application/json"
    )

    result = json.loads(response["body"].read())
    return result["embedding"]

# =====================================================
# RAG Retrieval
# =====================================================

def retrieve_context(query, top_k=3):
    try:
        vector = embed_text(query)

        search_body = {
            "size": top_k,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": vector,
                        "k": top_k
                    }
                }
            }
        }

        response = requests.post(
            f"{OPENSEARCH_ENDPOINT}/{RAG_INDEX}/_search",
            auth=get_awsauth(),
            json=search_body,
            timeout=5
        )

        if response.status_code != 200:
            log("rag_error", response.text)
            return []

        hits = response.json().get("hits", {}).get("hits", [])
        texts = [h["_source"].get("text", "") for h in hits if "_source" in h]

        log("rag_hits", len(texts))
        return texts

    except Exception as e:
        log("rag_exception", str(e))
        return []

# =====================================================
# Bedrock Converse
# =====================================================

def call_model(model_id, prompt, temperature=0.2, max_tokens=700):
    start = time.time()

    response = bedrock.converse(
        modelId=model_id,
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": temperature
        },
        messages=[{
            "role": "user",
            "content": [{"text": prompt}]
        }]
    )

    latency = round(time.time() - start, 3)
    log("bedrock_call", {"model": model_id, "latency": latency})

    return response["output"]["message"]["content"][0]["text"]

# =====================================================
# Router
# =====================================================

def build_router_prompt(user_query):
    return f"""
    You are a deterministic routing engine for NorthStar.

    You must select EXACTLY ONE internal tool.

    STRICT RULES:
    - Output ONLY valid JSON.
    - No explanations outside JSON.
    - No markdown.
    - No additional text.
    - Never answer the user question.

    Available tools:

    1. get_policy_details
    Use for coverage, benefits, limits, inclusions, exclusions, or policy content.

    2. check_document_requirements
    Use for required documents, paperwork, submission requirements.

    3. get_claim_status
    Use for claim progress, payment status, claim IDs.

    If unsure, choose the most conservative tool.

    Response format:
    {{
    "tool": "<tool_name>",
    "confidence": "high|medium|low",
    "reason": "<one sentence>"
    }}

    User request:
    \"\"\"{user_query}\"\"\"
    """

def route_query(query):
    try:
        raw = call_model(ROUTER_MODEL, build_router_prompt(query), temperature=0)
    except Exception as e:
        log("router_primary_failed", str(e))
        raw = call_model(FALLBACK_MODEL, build_router_prompt(query), temperature=0)

    decision = safe_json(raw)
    log("routing_decision", decision)
    return decision

# =====================================================
# Entity Extraction
# =====================================================

def build_extraction_prompt(query, tool_name):
    return f"""
    You are an argument extraction engine.

    Extract structured arguments required for the tool:
    {tool_name}

    Rules:
    - Return ONLY valid JSON
    - Do NOT explain
    - Do NOT include extra text
    - If a value is missing, return null

    Tool requirements:

    get_policy_details:
        - policy_id (string)

    get_claim_status:
        - claim_id (string)

    check_document_requirements:
        - policy_id (string)

    User query:
    \"\"\"{query}\"\"\"

    Response format:
    {{
      "arguments": {{ ... }}
    }}
    """

def extract_arguments(query, tool_name):
    prompt = build_extraction_prompt(query, tool_name)

    try:
        raw = call_model(SYNTH_MODEL, prompt, temperature=0)
    except Exception as e:
        log("extraction_failed", str(e))
        return {}

    parsed = safe_json(raw)
    return parsed.get("arguments", {})

# =====================================================
# Tool Invocation
# =====================================================

def invoke_tool(decision, query, arguments=None):
    mapping = {
        "get_policy_details": GET_POLICY_FUNCTION,
        "check_document_requirements": CHECK_DOC_FUNCTION,
        "get_claim_status": GET_CLAIM_FUNCTION
    }

    tool_name = decision.get("tool")
    function_name = mapping.get(tool_name)

    if not function_name:
        log("invalid_tool", {"tool": tool_name})
        return {"error": "Invalid tool selected"}

    payload = arguments if arguments else {"query": query}

    start = time.time()

    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload)
        )

        status_code = response.get("StatusCode")

        if status_code != 200:
            log("lambda_status_error", {
                "function": function_name,
                "status": status_code
            })
            return {"error": "Downstream service error"}

        raw_payload = response["Payload"].read()
        result = json.loads(raw_payload)

        # If Lambda returned an error
        if response.get("FunctionError"):
            log("lambda_function_error", {
                "function": function_name,
                "error": result
            })
            return {"error": "Downstream function execution failed"}

        latency = round(time.time() - start, 3)
        log("tool_call", {
            "function": function_name,
            "latency": latency
        })

        # Unwrap API Gateway style responses
        if isinstance(result, dict) and "body" in result:
            try:
                return json.loads(result["body"])
            except Exception:
                return {"error": "Invalid downstream response format"}

        return result

    except Exception as e:
        log("lambda_invoke_exception", str(e))
        return {"error": "Tool invocation failed"}

# =====================================================
# Memory
# =====================================================

def get_history(session_id, limit=5):
    response = table.query(
        KeyConditionExpression=Key("session_id").eq(session_id),
        ScanIndexForward=False,
        Limit=limit
    )
    return response.get("Items", [])

def store_message(session_id, user, assistant):
    table.put_item(
        Item={
            "session_id": session_id,
            "timestamp": datetime.utcnow().isoformat(),
            "user": user,
            "assistant": assistant
        }
    )

# =====================================================
# Synthesis
# =====================================================

def build_synthesis_prompt(user_query, tool_result, history, rag_context):

    return f"""
    You are NorthStar, operating in STRICT EVIDENCE MODE.

    You are NOT allowed to use prior knowledge.
    You are NOT allowed to infer beyond provided data.
    You are NOT allowed to generalize.

    You may ONLY use:

    1. Verified Tool Data
    2. Retrieved Policy Context

    If the answer is not explicitly present in those sources,
    you MUST respond exactly:

    "I did not find references to this in the policy documents."

    ------------------------------------------------------------
    User Question:
    {user_query}

    Verified Tool Data:
    {json.dumps(tool_result, indent=2)}

    Retrieved Policy Context:
    {rag_context}
    ------------------------------------------------------------

    RESPONSE RULES:

    - Extract information directly from the provided data.
    - Quote relevant phrases when possible.
    - Do not add commentary.
    - Do not explain system issues unless tool_result contains an error.
    - Do not include generic industry explanations.
    - Keep the response factual and grounded.

    Provide the final answer now.
    """


def generate_response(query, tool_result, history):

    rag_context = retrieve_context(query)

    # Hard guardrail: prevent hallucination
    if not rag_context and (not tool_result or tool_result == {}):
        return "I did not find references to this in the policy documents."

    prompt = build_synthesis_prompt(query, tool_result, history, rag_context)

    try:
        return call_model(SYNTH_MODEL, prompt, temperature=0.2)
    except Exception as e:
        log("synth_primary_failed", str(e))
        return call_model(FALLBACK_MODEL, prompt, temperature=0.1)

# =====================================================
# Lambda Handler
# =====================================================

def lambda_handler(event, context):
    try:
        log("incoming_event", event)

        body = json.loads(event.get("body", "{}"))
        query = body.get("query")
        session_id = body.get("session_id", "default")

        if not query:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing query"})
            }

        history = get_history(session_id)
        decision = route_query(query)
        tool_name = decision.get("tool")

        arguments = extract_arguments(query, tool_name)

        log("extracted_arguments", arguments)

        tool_result = invoke_tool(decision, query, arguments)

        final_answer = generate_response(query, tool_result, history)

        store_message(session_id, query, final_answer)

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