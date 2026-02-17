import json
import boto3
import os
import uuid
import requests
from io import BytesIO
from pypdf import PdfReader
from requests_aws4auth import AWS4Auth

# ------------------------------------------------
# Environment
# ------------------------------------------------

REGION = os.environ["AWS_REGION"]
INDEX_NAME = os.environ["RAG_INDEX"]
OPENSEARCH_ENDPOINT = os.environ["OPENSEARCH_ENDPOINT"]

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime")

session = boto3.Session()
credentials = session.get_credentials()

awsauth = AWS4Auth(
    credentials.access_key,
    credentials.secret_key,
    REGION,
    "aoss",
    session_token=credentials.token
)

# ------------------------------------------------
# Extract PDF Text
# ------------------------------------------------

def extract_text_from_pdf(bucket, key):
    obj = s3.get_object(Bucket=bucket, Key=key)

    pdf_bytes = obj["Body"].read()
    pdf_stream = BytesIO(pdf_bytes)

    reader = PdfReader(pdf_stream)

    text = ""
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted + "\n"

    return text.strip()
# ------------------------------------------------
# Chunk Text
# ------------------------------------------------

def chunk_text(text, chunk_size=1000, overlap=150):
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap

    return chunks

# ------------------------------------------------
# Embed
# ------------------------------------------------

def embed_text(text):
    response = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0",
        body=json.dumps({"inputText": text}),
        contentType="application/json",
        accept="application/json"
    )

    result = json.loads(response["body"].read())
    return result["embedding"]

# ------------------------------------------------
# Index Chunk
# ------------------------------------------------

def index_chunk(chunk_id, text, metadata):
    vector = embed_text(text)

    document = {
        "text": text,
        "embedding": vector,
        "metadata": metadata
    }

    url = f"{OPENSEARCH_ENDPOINT}/{INDEX_NAME}/_doc"

    response = requests.post(
        url,
        auth=awsauth,
        headers={"Content-Type": "application/json"},
        json=document
    )

    if response.status_code not in [200, 201]:
        raise Exception(f"Indexing failed: {response.text}")

# ------------------------------------------------
# Handler (EventBridge Format)
# ------------------------------------------------

def lambda_handler(event, context):

    print("Incoming Event:", json.dumps(event))

    try:
        detail = event["detail"]
        bucket = detail["bucket"]["name"]
        key = detail["object"]["key"]

        # Only process PDFs
        if not key.lower().endswith(".pdf"):
            return {"status": "ignored_non_pdf"}

        print(f"Processing file: {bucket}/{key}")

        text = extract_text_from_pdf(bucket, key)

        if not text:
            return {"status": "empty_document"}

        chunks = chunk_text(text)

        for i, chunk in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            metadata = {
                "source": key,
                "chunk_index": i
            }
            index_chunk(chunk_id, chunk, metadata)

        return {
            "status": "indexed",
            "file": key,
            "chunks": len(chunks)
        }

    except Exception as e:
        print("Error:", str(e))
        raise