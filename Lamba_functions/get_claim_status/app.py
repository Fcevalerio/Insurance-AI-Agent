import json
import boto3
import os

s3 = boto3.client("s3")

BUCKET = os.environ["AWS_S3_BUCKET_NAME"]
CLAIMS_PREFIX = os.environ.get("AWS_CLAIMS_DATA", "")


def lambda_handler(event, context):
    claim_id = event.get("claim_id")

    if not claim_id:
        return {"error": "claim_id is required"}

    try:
        response = s3.get_object(
            Bucket=BUCKET,
            Key=f"{CLAIMS_PREFIX}/{claim_id}.json"
        )

        claim = json.loads(response["Body"].read())
        return claim

    except s3.exceptions.NoSuchKey:
        return {"error": "Claim not found"}

    except Exception as e:
        return {"error": str(e)}