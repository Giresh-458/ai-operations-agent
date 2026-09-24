import os
import time
import requests
import streamlit as st

# Configure Page
st.set_page_config(
    page_title="AI Operations Platform",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Backend URL configuration (Streamlit Cloud secrets, environment variables, or local default)
try:
    cloud_secret_url = st.secrets.get("BACKEND_URL")
except Exception:
    cloud_secret_url = None

BACKEND_URL = (os.getenv("BACKEND_URL") or cloud_secret_url or "http://localhost:8080").rstrip("/")

# Initialize Session State
if "connected" not in st.session_state:
    st.session_state.connected = False
if "provider" not in st.session_state:
    st.session_state.provider = "Groq"
if "api_key" not in st.session_state:
    st.session_state.api_key = ""
if "active_model" not in st.session_state:
    st.session_state.active_model = ""
if "current_task" not in st.session_state:
    st.session_state.current_task = None
if "input_text" not in st.session_state:
    st.session_state.input_text = ""

# Sidebar: Provider & Model Configuration
with st.sidebar:
    st.title("Configuration")
    st.subheader("LLM Provider")

    if not st.session_state.connected:
        provider_choice = st.selectbox(
            "Select Provider",
            options=["Groq", "OpenAI"],
            index=0 if st.session_state.provider == "Groq" else 1
        )
        api_key_input = st.text_input(
            f"Enter {provider_choice} API Key",
            type="password",
            placeholder=f"gsk_... or sk-...",
            value=st.session_state.api_key
        )

        if st.button("Connect / Start Agent", use_container_width=True, type="primary"):
            if not api_key_input.strip():
                st.error("Please enter a valid API key.")
            else:
                with st.spinner(f"Validating {provider_choice} connection..."):
                    try:
                        resp = requests.post(
                            f"{BACKEND_URL}/validate_provider",
                            json={"provider": provider_choice, "api_key": api_key_input.strip()},
                            timeout=15
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            st.session_state.connected = True
                            st.session_state.provider = provider_choice
                            st.session_state.api_key = api_key_input.strip()
                            st.session_state.active_model = data.get("model", "Default")
                            st.success(f"Connected to {provider_choice}")
                            st.rerun()
                        else:
                            detail = resp.json().get("detail", resp.text)
                            st.error(f"Connection failed: {detail}")
                    except Exception as e:
                        st.error(f"Cannot reach backend at {BACKEND_URL}: {str(e)}")
    else:
        st.success("Provider Connected")
        st.markdown(f"**Provider:** `{st.session_state.provider}`")
        st.markdown(f"**Active Model:** `{st.session_state.active_model}`")
        st.markdown("**Status:** `Ready to process requests`")

        if st.button("Change Provider / Key", use_container_width=True):
            st.session_state.connected = False
            st.session_state.api_key = ""
            st.session_state.active_model = ""
            st.session_state.current_task = None
            st.rerun()

    st.divider()
    st.subheader("Active Guardrails")
    st.markdown("- **PII Protection**: Scans for credit card numbers and SSNs")
    st.markdown("- **Prompt Injection Defense**: Evaluates prompt safety")
    st.markdown("- **Human-in-the-Loop**: Active for critical operations")
    st.markdown("- **Corrective RAG**: PostgreSQL pgvector semantic retrieval")

# Main Content Area
st.title("AI Operations Automation Platform")
st.caption("Multi-Agent System built with LangGraph, Corrective RAG (pgvector), Guardrails and Tool Execution")

if not st.session_state.connected:
    st.warning("Agent is disconnected. Please select your LLM provider and enter an API key in the sidebar to begin.")
    st.info("Supported models include Groq openai/gpt-oss-120b and OpenAI gpt-4o-mini.")
else:
    # Quick Test Presets
    st.subheader("Quick Presets")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("Check Order 5003", use_container_width=True):
            st.session_state.input_text = "Check the status of order 5003"
    with col2:
        if st.button("Return Policy (RAG)", use_container_width=True):
            st.session_state.input_text = "What is the return policy?"
    with col3:
        if st.button("Critical Ticket (Approval)", use_container_width=True):
            st.session_state.input_text = "Create a critical ticket for customer 101 with issue Payment gateway failed"
    with col4:
        if st.button("Test Guardrail (PII)", use_container_width=True):
            st.session_state.input_text = "My credit card is 4111-2222-3333-4444"

    # User Request Input
    user_request = st.text_area(
        "Enter operational command:",
        value=st.session_state.input_text,
        height=100,
        placeholder="e.g., Check the status of order 5001, or What is our return policy?"
    )

    col_btn, col_clear = st.columns([1, 5])
    with col_btn:
        submit_btn = st.button("Run Agent", type="primary", use_container_width=True)

    if submit_btn and user_request.strip():
        with st.spinner("Analyzing request and executing workflow..."):
            try:
                payload = {
                    "request": user_request.strip(),
                    "user_request": user_request.strip(),
                    "provider": st.session_state.provider,
                    "api_key": st.session_state.api_key
                }
                resp = requests.post(f"{BACKEND_URL}/tasks", json=payload, timeout=45)
                if resp.status_code == 200:
                    st.session_state.current_task = resp.json()
                    st.toast("Task completed successfully.")
                else:
                    st.error(f"Execution Error: {resp.text}")
            except Exception as e:
                st.error(f"Failed to communicate with backend: {str(e)}")

    # Display Current Task Results
    if st.session_state.current_task:
        task = st.session_state.current_task
        task_id = task.get("task_id") or task.get("id")

        st.divider()
        st.subheader(f"Task #{task_id} Execution Details")

        status = task.get("status", "UNKNOWN")
        requires_human = task.get("requires_human")

        # Status Banner
        if status == "COMPLETED":
            st.success(f"Status: **{status}**")
        elif requires_human == "WAITING" or status == "WAITING_FOR_APPROVAL":
            st.warning("Waiting for human approval: This operation was flagged as critical and requires confirmation.")
        elif status == "FAILED":
            st.error(f"Status: **{status}**")
        else:
            st.info(f"Status: **{status}**")

        # Human in the loop action buttons
        if requires_human == "WAITING":
            col_app, col_rej = st.columns([1, 1])
            with col_app:
                if st.button("Approve Critical Action", type="primary", use_container_width=True):
                    with st.spinner("Processing approval..."):
                        app_resp = requests.post(
                            f"{BACKEND_URL}/tasks/{task_id}/approve",
                            json={"provider": st.session_state.provider, "api_key": st.session_state.api_key}
                        )
                        if app_resp.status_code == 200:
                            st.session_state.current_task = app_resp.json()
                            st.success("Action approved and executed.")
                            st.rerun()
                        else:
                            st.error(f"Approval failed: {app_resp.text}")
            with col_rej:
                if st.button("Reject Action", use_container_width=True):
                    with st.spinner("Processing rejection..."):
                        rej_resp = requests.post(f"{BACKEND_URL}/tasks/{task_id}/reject")
                        if rej_resp.status_code == 200:
                            st.session_state.current_task = rej_resp.json()
                            st.info("Action rejected.")
                            st.rerun()
                        else:
                            st.error(f"Rejection failed: {rej_resp.text}")

        # Metrics Overview
        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("Detected Intent", task.get("intent") or "None")
        with m2:
            st.metric("Retries", task.get("retry_count", 0))
        with m3:
            st.metric("Human Approval", task.get("requires_human") or "Not Required")

        # Response Output
        st.markdown("### Final Agent Response")
        result_content = task.get("result")
        if isinstance(result_content, dict):
            msg = result_content.get("message") or result_content.get("error") or str(result_content)
        else:
            msg = str(result_content or "No response returned.")

        st.info(msg)

        # Extracted Parameters
        with st.expander("View Extracted Parameters", expanded=False):
            st.json(task.get("parameters") or {})

        # Audit Logs & Observability
        with st.expander("View Audit Logs & Observability Traces", expanded=True):
            try:
                audit_resp = requests.get(f"{BACKEND_URL}/tasks/{task_id}/logs", timeout=10)
                if audit_resp.status_code != 200:
                    audit_resp = requests.get(f"{BACKEND_URL}/tasks/{task_id}/audit", timeout=10)
                if audit_resp.status_code == 200:
                    logs = audit_resp.json()
                    if logs:
                        log_data = []
                        for l in logs:
                            log_data.append({
                                "Action / Stage": l.get("action"),
                                "Status": l.get("status"),
                                "Details": l.get("details") or "",
                                "Timestamp": l.get("created_at")
                            })
                        st.table(log_data)
                    else:
                        st.write("No audit logs found for this task.")
                else:
                    st.write("Unable to fetch audit logs.")
            except Exception as e:
                st.write(f"Error loading audit trail: {str(e)}")
