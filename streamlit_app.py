import os
import time
import requests
import streamlit as st
import pandas as pd

# Configure Page
st.set_page_config(
    page_title="AI Operations Platform | Control Center",
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
    st.session_state.input_text = "Check the status of order 5003"

# Topbar Header
col_header_left, col_header_right = st.columns([3, 1])
with col_header_left:
    st.title("AI Operations Platform")
    st.caption("Natural language → validated tool → database operation")
with col_header_right:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.session_state.connected:
        st.success(f"System Online | {st.session_state.provider} ({st.session_state.active_model})")
    else:
        st.warning("System Offline | Disconnected")

# Sidebar: Provider & Model Configuration
with st.sidebar:
    st.header("Configuration")
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

# Main Navigation Tabs (Identical to HTML Version)
tab_agent, tab_data, tab_history = st.tabs(["Agent Control", "Live Data Explorer", "Task History"])

# ==========================================
# TAB 1: AGENT CONTROL
# ==========================================
with tab_agent:
    # Hero & Quick Presets Section
    st.markdown("#### Ask the agent to perform an operational task")
    st.caption("Try a real database-backed request. Every decision and data change is visible in real time.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("Check Order 5003", use_container_width=True):
            st.session_state.input_text = "Check the status of order 5003"
    with col2:
        if st.button("Critical Ticket (Approval)", use_container_width=True):
            st.session_state.input_text = "Create a critical support ticket for customer 101 because payment failed"
    with col3:
        if st.button("Update Email", use_container_width=True):
            st.session_state.input_text = "Update email for customer 101 to newmail@example.com"
    with col4:
        if st.button("Return Policy (RAG)", use_container_width=True):
            st.session_state.input_text = "What is the return policy for lost packages?"

    # Request Input Box
    st.markdown("##### Operational Request")
    col_input, col_run = st.columns([5, 1])
    with col_input:
        user_request = st.text_input(
            "Request",
            value=st.session_state.input_text,
            label_visibility="collapsed",
            placeholder="e.g. Check the status of order 5003"
        )
    with col_run:
        run_btn = st.button("Run Agent →", type="primary", use_container_width=True)

    if run_btn and user_request.strip():
        if not st.session_state.connected:
            st.error("Please connect your LLM Provider in the sidebar first.")
        else:
            with st.spinner("Agent is analyzing request, validating rules, and executing workflow..."):
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
                        st.toast("Task executed successfully.")
                    else:
                        st.error(f"Execution Error: {resp.text}")
                except Exception as e:
                    st.error(f"Failed to communicate with backend: {str(e)}")

    # Visual Workflow Stages Grid (01 Understand, 02 Execute, 03 Outcome, 04 Data Impact, 05 Audit Trail)
    task = st.session_state.current_task
    task_id = (task.get("task_id") or task.get("id")) if task else None
    status = task.get("status", "READY") if task else "READY"
    intent = task.get("intent", "—") if task else "—"
    selected_tool = task.get("selected_tool") or intent if task else "—"
    params = task.get("parameters") if task else None
    requires_human = task.get("requires_human") if task else None

    st.markdown("---")
    row1_col1, row1_col2 = st.columns(2)

    # 01 - UNDERSTAND: AI Decision
    with row1_col1:
        st.markdown("**01 · UNDERSTAND**")
        st.subheader("AI Decision")
        m_col1, m_col2, m_col3 = st.columns(3)
        with m_col1:
            st.metric("Detected Intent", intent or "None")
        with m_col2:
            st.metric("Selected Tool", selected_tool or "None")
        with m_col3:
            st.metric("Task Status", status)

        st.markdown("**Extracted Parameters**")
        if params:
            st.json(params)
        else:
            st.info("Run a request to see extracted parameters.")

    # 02 - EXECUTE: Agent Workflow & Human Approval
    with row1_col2:
        st.markdown("**02 · EXECUTE**")
        st.subheader("Agent Workflow Pipeline")
        
        step_cols = st.columns(4)
        with step_cols[0]:
            st.info("1. Intent\n\nParse Request")
        with step_cols[1]:
            st.info("2. Validate\n\nBusiness Rules")
        with step_cols[2]:
            st.info("3. Tool\n\nSelect Action")
        with step_cols[3]:
            st.info("4. Database\n\nCommit Change")

        # Human in the loop card
        if requires_human == "WAITING" or status == "WAITING_FOR_APPROVAL":
            st.warning("⚠️ **Human Approval Required**: Critical operations are paused before database execution.")
            col_app, col_rej = st.columns(2)
            with col_app:
                if st.button("Approve Critical Action", type="primary", use_container_width=True):
                    with st.spinner("Processing approval..."):
                        app_resp = requests.post(
                            f"{BACKEND_URL}/tasks/{task_id}/approve",
                            json={"provider": st.session_state.provider, "api_key": st.session_state.api_key}
                        )
                        if app_resp.status_code == 200:
                            st.session_state.current_task = app_resp.json()
                            st.success("Action Approved and Executed.")
                            st.rerun()
                        else:
                            st.error(f"Approval failed: {app_resp.text}")
            with col_rej:
                if st.button("Reject Action", use_container_width=True):
                    with st.spinner("Processing rejection..."):
                        rej_resp = requests.post(f"{BACKEND_URL}/tasks/{task_id}/reject")
                        if rej_resp.status_code == 200:
                            st.session_state.current_task = rej_resp.json()
                            st.info("Action Rejected.")
                            st.rerun()
                        else:
                            st.error(f"Rejection failed: {rej_resp.text}")

    st.markdown("---")
    row2_col1, row2_col2 = st.columns(2)

    # 03 - OUTCOME: Operation Result
    with row2_col1:
        st.markdown("**03 · OUTCOME**")
        st.subheader("Operation Result")
        if task:
            result_content = task.get("result")
            if isinstance(result_content, dict):
                msg = result_content.get("message") or result_content.get("error") or str(result_content)
            else:
                msg = str(result_content or "No response returned.")
            
            if status == "COMPLETED":
                st.success(msg)
            elif status == "FAILED":
                st.error(msg)
            else:
                st.info(msg)

            with st.expander("View raw tool response"):
                st.json(result_content or {})
        else:
            st.info("No operation executed yet.")

    # 04 - EVIDENCE: Data Impact
    with row2_col2:
        st.markdown("**04 · EVIDENCE**")
        st.subheader("Data Impact")
        
        # Fetch live stats from database
        try:
            db_resp = requests.get(f"{BACKEND_URL}/database", timeout=5)
            if db_resp.status_code == 200:
                db_data = db_resp.json()
                orders_cnt = len(db_data.get("orders", []))
                tickets_cnt = len(db_data.get("tickets", []))
                customers_cnt = len(db_data.get("customers", []))
                notif_cnt = len(db_data.get("notifications", []))
            else:
                orders_cnt = tickets_cnt = customers_cnt = notif_cnt = "—"
        except Exception:
            orders_cnt = tickets_cnt = customers_cnt = notif_cnt = "—"

        stat1, stat2, stat3, stat4 = st.columns(4)
        with stat1:
            st.metric("Orders", orders_cnt)
        with stat2:
            st.metric("Tickets", tickets_cnt)
        with stat3:
            st.metric("Customers", customers_cnt)
        with stat4:
            st.metric("Notifications", notif_cnt)

        st.caption("The live data view tab shows exactly what changed in PostgreSQL.")

    # 05 - TRACEABILITY: Audit Trail
    st.markdown("---")
    st.markdown("**05 · TRACEABILITY**")
    st.subheader("Audit Trail")
    if task_id:
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
                    st.dataframe(pd.DataFrame(log_data), use_container_width=True)
                else:
                    st.write("No audit logs found for this task.")
            else:
                st.write("Unable to fetch audit logs.")
        except Exception as e:
            st.write(f"Error loading audit trail: {str(e)}")
    else:
        st.info("Audit events will appear here after execution.")


# ==========================================
# TAB 2: LIVE DATA EXPLORER
# ==========================================
with tab_data:
    st.markdown("### Database Explorer: Live Operational Data")
    st.caption("Records are loaded directly from PostgreSQL and update immediately after agent actions.")

    col_db_head, col_db_refresh = st.columns([5, 1])
    with col_db_refresh:
        refresh_db = st.button("↻ Refresh Database", use_container_width=True)

    try:
        db_resp = requests.get(f"{BACKEND_URL}/database", timeout=10)
        if db_resp.status_code == 200:
            db_state = db_resp.json()
            
            # Overview Metrics
            c_cnt = len(db_state.get("customers", []))
            o_cnt = len(db_state.get("orders", []))
            t_cnt = len(db_state.get("tickets", []))
            n_cnt = len(db_state.get("notifications", []))

            sc1, sc2, sc3, sc4 = st.columns(4)
            with sc1:
                st.metric("Customers", c_cnt)
            with sc2:
                st.metric("Orders", o_cnt)
            with sc3:
                st.metric("Support Tickets", t_cnt)
            with sc4:
                st.metric("Notifications", n_cnt)

            st.divider()

            # Tables Grid
            tbl_c1, tbl_c2 = st.columns(2)
            with tbl_c1:
                st.subheader("Orders (orders)")
                orders_list = db_state.get("orders", [])
                if orders_list:
                    st.dataframe(pd.DataFrame(orders_list), use_container_width=True)
                else:
                    st.info("No orders found.")

                st.subheader("Support Tickets (support_tickets)")
                tickets_list = db_state.get("tickets", [])
                if tickets_list:
                    st.dataframe(pd.DataFrame(tickets_list), use_container_width=True)
                else:
                    st.info("No support tickets found.")

            with tbl_c2:
                st.subheader("Customers (customers)")
                customers_list = db_state.get("customers", [])
                if customers_list:
                    st.dataframe(pd.DataFrame(customers_list), use_container_width=True)
                else:
                    st.info("No customers found.")

                st.subheader("Notifications (notifications)")
                notifications_list = db_state.get("notifications", [])
                if notifications_list:
                    st.dataframe(pd.DataFrame(notifications_list), use_container_width=True)
                else:
                    st.info("No notifications found.")
        else:
            st.error(f"Failed to fetch database state: {db_resp.text}")
    except Exception as e:
        st.error(f"Cannot reach database endpoint at {BACKEND_URL}/database: {str(e)}")


# ==========================================
# TAB 3: TASK HISTORY
# ==========================================
with tab_history:
    st.markdown("### Previous Agent Tasks")
    st.caption("Complete history of operations processed by the agent.")

    col_h_head, col_h_ref = st.columns([5, 1])
    with col_h_ref:
        refresh_hist = st.button("↻ Refresh History", use_container_width=True)

    try:
        tasks_resp = requests.get(f"{BACKEND_URL}/tasks", timeout=10)
        if tasks_resp.status_code == 200:
            tasks_list = tasks_resp.json()
            if tasks_list:
                df_tasks = pd.DataFrame(tasks_list)
                # Rename columns for clarity
                df_tasks.rename(columns={
                    "id": "Task ID",
                    "request": "User Request",
                    "intent": "Detected Intent",
                    "status": "Execution Status"
                }, inplace=True)
                st.dataframe(df_tasks, use_container_width=True)
            else:
                st.info("No tasks recorded yet.")
        else:
            st.error(f"Failed to load task history: {tasks_resp.text}")
    except Exception as e:
        st.error(f"Cannot reach tasks endpoint at {BACKEND_URL}/tasks: {str(e)}")
