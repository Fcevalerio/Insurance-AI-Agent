import json
import boto3
import os

lambda_client = boto3.client("lambda")
bedrock = boto3.client("bedrock-runtime")

PRIMARY_MODEL = os.environ["AWS_BEDROCK_PRIMARY_MODEL"]
FALLBACK_MODEL = os.environ["AWS_BEDROCK_FALLBACK_MODEL"]

SYSTEM_PROMPT = """
You are NorthStar, an intelligent insurance AI assistant.

Your role:
- Understand the user's request.
- Select the most appropriate internal tool.
- Never hallucinate information.
- If unsure, choose the safest relevant tool.

Available tools:

1. get_policy_details
   Use when user asks about coverage, benefits, policy status.

2. check_document_requirements
   Use when user asks what documents are required.

3. get_claim_status
   Use when user asks about claim progress, claim ID, or payout status.

Rules:
- Only choose ONE tool.
- Respond ONLY in valid JSON.
- Do not include explanations outside JSON.

Response format:
{
  "tool": "<tool_name>",
  "reason": "<short explanation>"
}
"""


def call_bedrock(user_query):
    body = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_query}
        ],
        "max_tokens": 300,
        "temperature": 0
    }

    try:
        response = bedrock.invoke_model(
            modelId=PRIMARY_MODEL,
            body=json.dumps(body)
        )
    except Exception as e:
        print("Primary model failed, using fallback:", str(e))
        response = bedrock.invoke_model(
            modelId=FALLBACK_MODEL,
            body=json.dumps(body)
        )

    response_body = json.loads(response["body"].read())
    content = response_body["content"][0]["text"]

    return json.loads(content)


def invoke_lambda(function_name, payload):
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload)
    )
    return json.loads(response["Payload"].read())


def lambda_handler(event, context):
    body = json.loads(event.get("body", "{}"))
    user_query = body.get("query", "")

    decision = call_bedrock(user_query)

    tool = decision.get("tool")

    if tool == "get_policy_details":
        result = invoke_lambda(os.environ["GET_POLICY_FUNCTION"], {})

    elif tool == "check_document_requirements":
        result = invoke_lambda(os.environ["CHECK_DOC_FUNCTION"], {})

    elif tool == "get_claim_status":
        result = invoke_lambda(os.environ["GET_CLAIM_FUNCTION"], {})

    else:
        result = {
            "message": "I'm not sure how to assist with that request yet."
        }

    return {
        "statusCode": 200,
        "body": json.dumps({
            "agent_decision": decision,
            "response": result
        })
    }