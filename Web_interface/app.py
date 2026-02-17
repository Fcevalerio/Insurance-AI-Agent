import streamlit as st
import requests
import boto3
import os
import time
import uuid
from boto3.dynamodb.conditions import Key

# ==========================================================
# Environment Variables
# ==========================================================

API_URL = os.environ["AGENT_API"]
DYNAMO_TABLE = os.environ["CONVERSATION_TABLE"]
AWS_REGION = os.environ["AWS_REGION"]

# ==========================================================
# AWS DynamoDB
# ==========================================================

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMO_TABLE)

# ==========================================================
# Page Config
# ==========================================================

st.set_page_config(
    page_title="NorthStar Insurance AI",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================================
# Custom Styling
# ==========================================================

st.markdown("""
<style>
html, body, [class*="css"]  {
    font-family: 'Segoe UI', sans-serif;
}

.stChatMessage {
    padding: 12px;
    border-radius: 12px;
}

.sidebar-title {
    font-size:18px;
    font-weight:600;
    margin-bottom:10px;
}

.session-item {
    padding:8px;
    border-radius:8px;
    margin-bottom:5px;
}

.confidence-badge {
    font-size:12px;
    padding:4px 8px;
    border-radius:6px;
    background-color:#e6f2ff;
    color:#003366;
    display:inline-block;
    margin-top:5px;
}
</style>
""", unsafe_allow_html=True)

# ==========================================================
# Session Management
# ==========================================================

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []

# ==========================================================
# Load Conversations from DynamoDB
# ==========================================================

def list_sessions():
    try:
        response = table.scan(ProjectionExpression="session_id")
        sessions = list(set([item["session_id"] for item in response["Items"]]))
        return sessions
    except Exception:
        return []

def load_session(session_id):
    response = table.query(
        KeyConditionExpression=Key("session_id").eq(session_id),
        ScanIndexForward=True
    )
    items = response.get("Items", [])
    messages = []
    for item in items:
        messages.append({"role": "user", "content": item["user"]})
        messages.append({"role": "assistant", "content": item["assistant"]})
    return messages

# ==========================================================
# Sidebar UI
# ==========================================================

with st.sidebar:
    st.markdown("### 💬 Chats")

    if st.button("➕ New Chat"):
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()

    sessions = list_sessions()

    for session in sessions:
        if st.button(session[:8], key=session):
            st.session_state.session_id = session
            st.session_state.messages = load_session(session)
            st.rerun()

# ==========================================================
# Main UI
# ==========================================================

st.title("🛡️ NorthStar Insurance AI")
st.caption("Enterprise Insurance Assistant powered by Bedrock + RAG")

# Display Chat History
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ==========================================================
# Chat Input
# ==========================================================

if prompt := st.chat_input("Ask about your policy, claim, or documents..."):

    # Show user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call Agent API
    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_response = ""
        confidence_level = ""

        try:
            response = requests.post(
                API_URL,
                json={
                    "query": prompt,
                    "session_id": st.session_state.session_id
                },
                timeout=60
            )

            data = response.json()
            answer = data.get("answer", "No response.")
            confidence_level = data.get("confidence", "unknown")

            # =========================================
            # Typing Animation
            # =========================================
            for word in answer.split():
                full_response += word + " "
                placeholder.markdown(full_response + "▌")
                time.sleep(0.015)

            # Final render
            placeholder.markdown(full_response)

            # Confidence Badge
            if confidence_level:
                st.markdown(
                    f'<div class="confidence-badge">Confidence: {confidence_level}</div>',
                    unsafe_allow_html=True
                )

        except Exception:
            placeholder.markdown("⚠️ Unable to connect to the AI Agent.")
            full_response = "System error."

    st.session_state.messages.append(
        {"role": "assistant", "content": full_response}
    )