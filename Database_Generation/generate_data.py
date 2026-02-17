import json
import random
import os
import uuid
import boto3
from faker import Faker
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

AWS_REGION = os.getenv("AWS_REGION")
BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME")

# Initialize S3 client
s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)

fake = Faker()
random.seed(1995)
Faker.seed(1995)

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR) + "/Database_Generation"

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data")
CLAIMS_DIR = os.path.join(OUTPUT_DIR, "claims")

os.makedirs(CLAIMS_DIR, exist_ok=True)

# Config
NUM_POLICIES = 50
NUM_CLAIMS = 300

STATES = ["FL", "CA", "TX", "NY", "IL"]
POLICY_TYPES = ["auto", "home"]

LOSS_TYPES = {
    "auto": ["auto_collision", "auto_theft"],
    "home": ["home_fire", "water_damage"]
}

DOCUMENT_RULES = {
    "auto_collision": ["photo_front_damage.jpg", "repair_invoice.pdf"],
    "auto_theft": ["police_report.pdf"],
    "home_fire": ["fire_report.pdf", "damage_photos.zip"],
    "water_damage": ["plumber_report.pdf", "damage_photos.zip"]
}


def upload_to_s3(local_path, s3_key):
    s3.upload_file(local_path, BUCKET_NAME, s3_key)
    print(f"Uploaded {s3_key} to S3")


def generate_policies():
    policies = {}

    for i in range(NUM_POLICIES):
        prefix = random.choice(["AUTO", "HOME"])
        policy_id = f"{prefix}-{10000 + i}"
        policy_type = "auto" if prefix == "AUTO" else "home"

        policies[policy_id] = {
            "policy_id": policy_id,
            "customer_name": fake.name(),
            "state": random.choice(STATES),
            "coverage_limit": random.choice([10000, 15000, 25000, 50000]),
            "deductible": random.choice([500, 1000, 2000]),
            "policy_type": policy_type,
            "active": random.choice([True, True, True, False])
        }

    return policies


def generate_claim(policy):
    claim_id = f"CLM-{uuid.uuid4().hex[:8].upper()}"
    loss_type = random.choice(LOSS_TYPES[policy["policy_type"]])

    required_docs = DOCUMENT_RULES[loss_type]

    if random.random() < 0.7:
        submitted_docs = required_docs
    else:
        submitted_docs = required_docs[:-1]

    estimated_damage = random.randint(1000, 60000)

    return {
        "claim_id": claim_id,
        "policy_id": policy["policy_id"],
        "loss_type": loss_type,
        "state": policy["state"],
        "estimated_damage": estimated_damage,
        "documents_submitted": submitted_docs,
        "status": "submitted"
    }


def generate_claims(policies):
    policy_list = list(policies.values())

    for _ in range(NUM_CLAIMS):
        policy = random.choice(policy_list)
        claim = generate_claim(policy)

        local_path = os.path.join(CLAIMS_DIR, f"{claim['claim_id']}.json")

        with open(local_path, "w") as f:
            json.dump(claim, f, indent=4)

        upload_to_s3(local_path, f"claims/{claim['claim_id']}.json")


def save_and_upload_json(data, filename, s3_key):
    local_path = os.path.join(OUTPUT_DIR, filename)

    with open(local_path, "w") as f:
        json.dump(data, f, indent=4)

    upload_to_s3(local_path, s3_key)


def main():
    print("Generating policies...")
    policies = generate_policies()
    save_and_upload_json(policies, "policies.json", "data/policies.json")

    print("Uploading document rules...")
    save_and_upload_json(DOCUMENT_RULES, "document_rules.json", "data/document_rules.json")

    print("Generating claims...")
    generate_claims(policies)

    print("Done! All data uploaded to S3.")


if __name__ == "__main__":
    main()
