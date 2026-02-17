import json
import boto3
import os

s3 = boto3.client("s3")

BUCKET = os.environ["AWS_S3_BUCKET_NAME"]
DATA_PREFIX = os.environ.get("AWS_INSURANCE_DATA", "")

POLICIES_CACHE = None


def load_policies():
    global POLICIES_CACHE

    if POLICIES_CACHE is None:
        response = s3.get_object(
            Bucket=BUCKET,
            Key=f"{DATA_PREFIX}/policies.json"
        )
        POLICIES_CACHE = json.loads(response["Body"].read())

    return POLICIES_CACHE


def lambda_handler(event, context):
    policy_id = event.get("policy_id")

    if not policy_id:
        return {"error": "policy_id is required"}

    try:
        policies = load_policies()
        policy = policies.get(policy_id)

        if not policy:
            return {"error": "Policy not found"}

        return policy

    except Exception as e:
        return {"error": str(e)}