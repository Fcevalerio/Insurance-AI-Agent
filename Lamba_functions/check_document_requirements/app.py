import json
import boto3
import os

s3 = boto3.client("s3")

BUCKET = os.environ["AWS_S3_BUCKET_NAME"]
DATA_PREFIX = os.environ.get("AWS_INSURANCE_DATA", "")

DOCUMENT_RULES_CACHE = None


def load_document_rules():
    global DOCUMENT_RULES_CACHE

    if DOCUMENT_RULES_CACHE is None:
        response = s3.get_object(
            Bucket=BUCKET,
            Key=f"{DATA_PREFIX}/document_rules.json"
        )
        DOCUMENT_RULES_CACHE = json.loads(response["Body"].read())

    return DOCUMENT_RULES_CACHE


def lambda_handler(event, context):
    loss_type = event.get("loss_type")
    submitted_docs = event.get("documents_submitted", [])

    if not loss_type:
        return {"error": "loss_type is required"}

    try:
        rules = load_document_rules()
        required_docs = rules.get(loss_type, [])

        missing = [doc for doc in required_docs if doc not in submitted_docs]

        return {
            "required_documents": required_docs,
            "missing_documents": missing,
            "complete": len(missing) == 0
        }

    except Exception as e:
        return {"error": str(e)}