import os
import uuid
import json
import boto3
import requests
import streamlit as st
from boto3.dynamodb.conditions import Key

# =====================================================
# Environment Variables (from GitHub secrets)
# =====================================================

API_URL = os.environ["AGENT_API"]
DYNAMO_TABLE = os.environ["CONVERSATION_TABLE"]
AWS_REGION = os.environ["AWS_REGION"]

# =====================================================
# AWS Setup
# =====================================================

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMO_TABLE)

# =====================================================
# Page Config
# =====================================================

st.set_page_config(
    page_title="NorthStar Insurance AI",
    page_icon="🤖",
    layout="wide"
)

st.title("🤖 NorthStar Insurance Agent")

# =====================================================
# Session State Initialization
# =====================================================

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

# =====================================================
# Sidebar - Chat Management
# =====================================================

with st.sidebar:

    st.header("💬 Chats")

    if st.button("➕ New Chat"):
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()

    # Load past sessions from DynamoDB
    response = table.scan(
        ProjectionExpression="session_id"
    )

    sessions = list(
        set(item["session_id"] for item in response.get("Items", []))
    )

    for s in sessions:
        if st.button(f"🗂 {s[:8]}...", key=s):
            st.session_state.session_id = s

            history = table.query(
                KeyConditionExpression=Key("session_id").eq(s),
                ScanIndexForward=True
            )

            st.session_state.messages = []

            for item in history.get("Items", []):
                st.session_state.messages.append({
                    "role": "user",
                    "content": item["user"]
                })
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": item["assistant"]
                })

            st.rerun()

# =====================================================
# Display Chat Messages
# =====================================================

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# =====================================================
# User Input
# =====================================================

if prompt := st.chat_input("Ask about your policy or claim..."):

    # Add user message
    st.session_state.messages.append({
        "role": "user",
        "content": prompt
    })

    with st.chat_message("user"):
        st.markdown(prompt)

    # Call API
    try:
        response = requests.post(
            API_URL,
            json={
                "query": prompt,
                "session_id": st.session_state.session_id
            },
            timeout=20
        )

        data = response.json()
        assistant_reply = data.get("answer", "No response received.")

    except Exception as e:
        assistant_reply = "⚠️ Unable to reach the agent service."

    # Add assistant message
    st.session_state.messages.append({
        "role": "assistant",
        "content": assistant_reply
    })

    with st.chat_message("assistant"):
        st.markdown(assistant_reply)