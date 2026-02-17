import json
import boto3
import os

lambda_client = boto3.client("lambda")

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

    # Basic routing logic (temporary until Bedrock added)
    if "policy" in user_query.lower():
        result = invoke_lambda(
            os.environ["GET_POLICY_FUNCTION"],
            {"policy_id": "POL123"}
        )

    elif "claim" in user_query.lower():
        result = invoke_lambda(
            os.environ["GET_CLAIM_FUNCTION"],
            {"claim_id": "CLM456"}
        )

    else:
        result = {"message": "I don’t understand the request yet."}

    return {
        "statusCode": 200,
        "body": json.dumps(result)
    }