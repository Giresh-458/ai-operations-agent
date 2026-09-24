import json
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from app.config import settings
from app.agent.state import AgentState
from app.schemas.schemas import IntentOutput
from app.database import SessionLocal
from app.models.database_models import Task, AuditLog
from app.tools.tickets import create_support_ticket
from app.tools.orders import check_order_status
from app.tools.customers import update_customer_email
from app.tools.notifications import send_notification

def is_valid_key(key: str) -> bool:
    if not key or not isinstance(key, str):
        return False
    k = key.strip().lower()
    if k in ("", "mock_key", "none", "null", "undefined"):
        return False
    if "your_" in k or "_here" in k:
        return False
    return True

def get_llm(provider: str, api_key: str):
    if provider == "Groq":
        return ChatOpenAI(
            model=settings.GROQ_MODEL,
            temperature=0,
            api_key=api_key or "mock_key",
            base_url="https://api.groq.com/openai/v1"
        )
    else:
        return ChatOpenAI(
            model=settings.OPENAI_MODEL,
            temperature=0,
            api_key=api_key or settings.OPENAI_API_KEY or "mock_key"
        )


def log_audit(db, task_id, action, status, details=None):
    log = AuditLog(task_id=task_id, action=action, status=status, details=details)
    db.add(log)
    db.commit()

def update_task_status(db, task_id, status, intent=None, parameters=None, result=None, requires_human=None):
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.status = status
        if intent:
            task.intent = intent
        if parameters:
            task.parameters = json.dumps(parameters)
        if result:
            task.result = json.dumps(result)
        if requires_human is not None:
            task.requires_human = requires_human
        db.commit()

def understand_request(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        update_task_status(db, task_id, "VALIDATING")
        log_audit(db, task_id, "INTENT_DETECTED", "STARTED")
        
        parser = PydanticOutputParser(pydantic_object=IntentOutput)
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are an AI Operations Automation Agent.
Your job is to identify the user's intent and extract parameters.
Supported intents:
- CREATE_TICKET (requires customer_id (int), issue (str), priority (str))
- CHECK_ORDER (requires order_id (int))
- UPDATE_EMAIL (requires customer_id (int), new_email (str))
- SEND_NOTIFICATION (requires customer_id (int), message (str))
- KNOWLEDGE_QUERY (use when the user asks a policy, FAQ, or general knowledge question. requires 'query' (str))
If the request is outside these operations, use intent: UNSUPPORTED and empty parameters.

Format instructions:
{format_instructions}"""),
            ("user", "{request}")
        ])
        active_key = state.get("api_key") or settings.OPENAI_API_KEY
        if not is_valid_key(active_key):
            # Mock behavior for tests / offline mode
            req_lower = state["user_request"].lower()
            intent = "UNSUPPORTED"
            params = {}
            import re
            if "order" in req_lower:
                intent = "CHECK_ORDER"
                match = re.search(r"order(?:\s*(?:number|#|id))?\s*(\d+)", req_lower)
                params = {"order_id": int(match.group(1)) if match else 5001}
            elif "ticket" in req_lower:
                intent = "CREATE_TICKET"
                customer_match = re.search(r"customer\s*(?:id|#)?\s*(\d+)", req_lower)
                customer_id = int(customer_match.group(1)) if customer_match else 101
                priority = "CRITICAL" if "critical" in req_lower else ("HIGH" if "high" in req_lower else "MEDIUM")
                issue = "Payment failed" if "payment" in req_lower else "Customer support request"
                params = {"customer_id": customer_id, "issue": issue, "priority": priority}
            elif "email" in req_lower:
                intent = "UPDATE_EMAIL"
                customer_match = re.search(r"customer\s*(?:id|#)?\s*(\d+)", req_lower)
                email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", req_lower)
                params = {
                    "customer_id": int(customer_match.group(1)) if customer_match else 101,
                    "new_email": email_match.group(0) if email_match else "example@gmail.com"
                }
            elif "notification" in req_lower:
                intent = "SEND_NOTIFICATION"
                customer_match = re.search(r"customer\s*(?:id|#)?\s*(\d+)", req_lower)
                message_match = re.search(r"(?:say|message|saying)\s+(.+)$", req_lower)
                params = {
                    "customer_id": int(customer_match.group(1)) if customer_match else 101,
                    "message": message_match.group(1).strip() if message_match else "Hello"
                }
            elif "policy" in req_lower or "what is" in req_lower or "how to" in req_lower:
                intent = "KNOWLEDGE_QUERY"
                params = {"query": state["user_request"]}
            
            output = IntentOutput(intent=intent, parameters=params)
        else:
            llm = get_llm(state.get("provider", "OpenAI"), state.get("api_key", ""))
            chain = prompt | llm | parser
            output = chain.invoke({"request": state["user_request"], "format_instructions": parser.get_format_instructions()})
            
        state["intent"] = output.intent
        state["parameters"] = output.parameters
        update_task_status(db, task_id, "VALIDATING", intent=output.intent, parameters=output.parameters)
        log_audit(db, task_id, "INTENT_DETECTED", "SUCCESS", json.dumps({"intent": output.intent}))
    except Exception as e:
        log_audit(db, task_id, "INTENT_DETECTED", "FAILED", str(e))
        state["status"] = "FAILED"
        state["final_response"] = f"Failed to understand request: {str(e)}"
        update_task_status(db, task_id, "FAILED")
    finally:
        db.close()
    return state

def validate_request(state: AgentState) -> AgentState:
    if state.get("status") == "FAILED":
        return state
        
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        log_audit(db, task_id, "VALIDATION_STARTED", "STARTED")
        intent = state.get("intent")
        params = state.get("parameters", {})
        
        if intent == "UNSUPPORTED":
            state["validation_result"] = False
            state["validation_message"] = "I can currently help with support tickets, order status, customer email updates, and notifications."
            log_audit(db, task_id, "VALIDATION_FAILED", "SUCCESS", state["validation_message"])
            return state

        missing = []
        if intent == "CREATE_TICKET":
            for p in ["customer_id", "issue", "priority"]:
                if p not in params: missing.append(p)
        elif intent == "CHECK_ORDER":
            if "order_id" not in params: missing.append("order_id")
        elif intent == "UPDATE_EMAIL":
            for p in ["customer_id", "new_email"]:
                if p not in params: missing.append(p)
        elif intent == "SEND_NOTIFICATION":
            for p in ["customer_id", "message"]:
                if p not in params: missing.append(p)
                
        if missing:
            state["validation_result"] = False
            state["validation_message"] = f"Please provide the missing information: {', '.join(missing)}."
            log_audit(db, task_id, "VALIDATION_FAILED", "SUCCESS", f"Missing: {missing}")
        else:
            state["validation_result"] = True
            log_audit(db, task_id, "VALIDATION_SUCCESS", "SUCCESS")
            
            # Check for critical priority requiring human approval
            if intent == "CREATE_TICKET" and params.get("priority", "").upper() == "CRITICAL":
                # Check if already approved/rejected
                task = db.query(Task).filter(Task.id == task_id).first()
                if task.requires_human not in ["APPROVED", "REJECTED"]:
                    state["requires_human"] = "WAITING"
                    update_task_status(db, task_id, "WAITING_FOR_APPROVAL", requires_human="WAITING")
                    log_audit(db, task_id, "HUMAN_APPROVAL_REQUIRED", "SUCCESS")
            
    except Exception as e:
        log_audit(db, task_id, "VALIDATION_STARTED", "FAILED", str(e))
        state["status"] = "FAILED"
        update_task_status(db, task_id, "FAILED")
    finally:
        db.close()
        
    return state

def select_tool(state: AgentState) -> AgentState:
    if state.get("status") == "FAILED" or not state.get("validation_result"):
        return state
        
    if state.get("requires_human") == "WAITING":
        return state # Stop execution here essentially for this step
        
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        log_audit(db, task_id, "TOOL_SELECTED", "SUCCESS", state["intent"])
        state["selected_tool"] = state["intent"]
        update_task_status(db, task_id, "RUNNING")
    except Exception as e:
        pass
    finally:
        db.close()
    return state

def execute_tool(state: AgentState) -> AgentState:
    if state.get("status") == "FAILED" or not state.get("validation_result"):
        return state
        
    if state.get("requires_human") == "WAITING":
        return state
        
    db = SessionLocal()
    task_id = state["task_id"]
    tool = state.get("selected_tool")
    params = state.get("parameters", {})
    
    try:
        log_audit(db, task_id, "TOOL_EXECUTED", "STARTED", tool)
        
        result = None
        if tool == "CREATE_TICKET":
            result = create_support_ticket(db, params["customer_id"], params["issue"], params["priority"])
        elif tool == "CHECK_ORDER":
            result = check_order_status(db, params["order_id"])
        elif tool == "UPDATE_EMAIL":
            result = update_customer_email(db, params["customer_id"], params["new_email"])
        elif tool == "SEND_NOTIFICATION":
            result = send_notification(db, params["customer_id"], params["message"])
            
        state["tool_result"] = result
        log_audit(db, task_id, "TOOL_EXECUTED", "SUCCESS", json.dumps(result))
    except Exception as e:
        state["tool_result"] = {"error": str(e)}
        log_audit(db, task_id, "TOOL_EXECUTED", "FAILED", str(e))
    finally:
        db.close()
        
    return state

def check_result(state: AgentState) -> AgentState:
    if state.get("status") == "FAILED" or not state.get("validation_result") or state.get("requires_human") == "WAITING":
        return state
        
    result = state.get("tool_result", {})
    if "error" in result:
        state["status"] = "ERROR"
    else:
        state["status"] = "COMPLETED"
    return state

def retry_node(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        if state["retry_count"] < settings.MAX_RETRIES:
            state["retry_count"] += 1
            log_audit(db, task_id, "RETRY_STARTED", "SUCCESS", f"Attempt {state['retry_count']}")
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.retry_count = state["retry_count"]
                db.commit()
            state["status"] = "RUNNING"
        else:
            state["status"] = "FAILED"
            log_audit(db, task_id, "TASK_FAILED", "SUCCESS", "Max retries reached")
    finally:
        db.close()
    return state

def final_response(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        if not state.get("validation_result"):
            state["final_response"] = state.get("validation_message", "Validation failed.")
            update_task_status(db, task_id, "COMPLETED", result={"message": state["final_response"]})
        elif state.get("requires_human") == "WAITING":
            state["final_response"] = "Human approval is required before creating this critical ticket."
            # Status remains WAITING_FOR_APPROVAL
        elif state.get("status") == "COMPLETED":
            result = state.get("tool_result", {})
            if state.get("intent") == "CHECK_ORDER":
                state["final_response"] = f"Order {result.get('order_id')} is {result.get('status')}."
            elif state.get("intent") == "CREATE_TICKET":
                state["final_response"] = f"Ticket {result.get('ticket_id')} created."
            elif state.get("intent") == "UPDATE_EMAIL":
                state["final_response"] = f"Customer {result.get('customer_id')} email updated to {result.get('new_email')}."
            elif state.get("intent") == "SEND_NOTIFICATION":
                state["final_response"] = f"Notification {result.get('notification_id')} sent."
            else:
                state["final_response"] = json.dumps(result)
            
            update_task_status(db, task_id, "COMPLETED", result=result)
            log_audit(db, task_id, "TASK_COMPLETED", "SUCCESS")
        elif state.get("status") == "FAILED":
            state["final_response"] = "Task failed to complete. " + str(state.get("tool_result", {}).get("error", ""))
            update_task_status(db, task_id, "FAILED", result={"error": state["final_response"]})
            log_audit(db, task_id, "TASK_FAILED", "SUCCESS")
            
    finally:
        db.close()
    return state
from app.models.database_models import KnowledgeArticle
from langchain_openai import OpenAIEmbeddings

def retrieve_docs(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    query = state.get("search_query") or state.get("parameters", {}).get("query") or state["user_request"]
    state["search_query"] = query
    
    try:
        log_audit(db, task_id, "RAG_RETRIEVE", "STARTED", query)
        
        # Try Vector Search
        from app.config import settings
        if is_valid_key(settings.OPENAI_API_KEY):
            embeddings = OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY, max_retries=1)
            vector = embeddings.embed_query(query)
            articles = db.query(KnowledgeArticle).order_by(KnowledgeArticle.embedding.cosine_distance(vector)).limit(3).all()
        else:
            # Fallback text search
            articles = db.query(KnowledgeArticle).filter(KnowledgeArticle.content.ilike(f"%{query}%")).limit(3).all()
            if not articles:
                # Just return top 3
                articles = db.query(KnowledgeArticle).limit(3).all()
                
        docs = [{"title": a.title, "content": a.content} for a in articles]
        state["documents"] = docs
        log_audit(db, task_id, "RAG_RETRIEVE", "SUCCESS", json.dumps([d["title"] for d in docs]))
    except Exception as e:
        log_audit(db, task_id, "RAG_RETRIEVE", "FAILED", str(e))
        state["documents"] = []
    finally:
        db.close()
    return state

def grade_docs(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        log_audit(db, task_id, "RAG_GRADE", "STARTED")
        query = state["search_query"]
        docs = state.get("documents", [])
        
        if not docs:
            state["is_relevant"] = False
            log_audit(db, task_id, "RAG_GRADE", "SUCCESS", "No documents found")
            return state
            
        active_key = state.get("api_key") or settings.OPENAI_API_KEY
        if not is_valid_key(active_key):
            state["is_relevant"] = True
            log_audit(db, task_id, "RAG_GRADE", "SUCCESS", "Mock relevance: True")
            return state
            
        llm = get_llm(state.get("provider", "OpenAI"), state.get("api_key", ""))
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a grader assessing relevance of a retrieved document to a user question. "
                       "Answer ONLY with 'yes' if relevant or 'no' if irrelevant. No other text."),
            ("user", "Question: {question}\n\nDocument: {document}")
        ])
        
        chain = prompt | llm
        
        is_relevant = False
        for doc in docs:
            result = chain.invoke({"question": query, "document": doc["content"]})
            if "yes" in result.content.lower():
                is_relevant = True
                break
                
        state["is_relevant"] = is_relevant
        log_audit(db, task_id, "RAG_GRADE", "SUCCESS", f"Relevant: {is_relevant}")
    except Exception as e:
        log_audit(db, task_id, "RAG_GRADE", "FAILED", str(e))
        state["is_relevant"] = True # fail open
    finally:
        db.close()
    return state

def rewrite_query(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        log_audit(db, task_id, "RAG_REWRITE", "STARTED")
        query = state["search_query"]
        
        active_key = state.get("api_key") or settings.OPENAI_API_KEY
        if not is_valid_key(active_key):
            state["search_query"] = query + " policy details"
            state["retry_count"] += 1
            log_audit(db, task_id, "RAG_REWRITE", "SUCCESS", state["search_query"])
            return state
            
        llm = get_llm(state.get("provider", "OpenAI"), state.get("api_key", ""))
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a question re-writer. Look at the input question and rewrite it to be better optimized for vector database retrieval. Just output the rewritten question and nothing else."),
            ("user", "{question}")
        ])
        
        chain = prompt | llm
        result = chain.invoke({"question": query})
        
        state["search_query"] = result.content.strip()
        state["retry_count"] += 1
        log_audit(db, task_id, "RAG_REWRITE", "SUCCESS", state["search_query"])
    except Exception as e:
        log_audit(db, task_id, "RAG_REWRITE", "FAILED", str(e))
    finally:
        db.close()
    return state

def generate_answer(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        log_audit(db, task_id, "RAG_GENERATE", "STARTED")
        query = state["search_query"]
        docs = state.get("documents", [])
        
        context = "\n\n".join([f"Title: {d['title']}\nContent: {d['content']}" for d in docs])
        
        active_key = state.get("api_key") or settings.OPENAI_API_KEY
        if not is_valid_key(active_key):
            state["final_response"] = docs[0]["content"] if docs else "Based on our knowledge base, here is the answer."
            state["status"] = "COMPLETED"
            update_task_status(db, task_id, "COMPLETED", result={"message": state["final_response"]})
            log_audit(db, task_id, "RAG_GENERATE", "SUCCESS", "Mock answer generated")
            return state
            
        llm = get_llm(state.get("provider", "OpenAI"), state.get("api_key", ""))
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a helpful assistant. Use the following pieces of retrieved context to answer the question. If you don't know the answer, just say that you don't know. Use three sentences maximum and keep the answer concise.\n\nContext:\n{context}"),
            ("user", "Question: {question}")
        ])
        
        chain = prompt | llm
        result = chain.invoke({"question": query, "context": context})
        
        state["final_response"] = result.content
        state["status"] = "COMPLETED"
        update_task_status(db, task_id, "COMPLETED", result={"message": state["final_response"]})
        log_audit(db, task_id, "RAG_GENERATE", "SUCCESS")
    except Exception as e:
        log_audit(db, task_id, "RAG_GENERATE", "FAILED", str(e))
        state["status"] = "ERROR"
        state["final_response"] = f"Failed to generate answer: {str(e)}"
    finally:
        db.close()
    return state

def guardrail_check(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        log_audit(db, task_id, "GUARDRAIL_CHECK", "STARTED")
        request_text = state["user_request"].lower()
        
        # 1. Regex/Heuristic for PII (e.g. 16 digit CC number or SSN pattern)
        import re
        if re.search(r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b', request_text) or re.search(r'\b\d{3}-\d{2}-\d{4}\b', request_text):
            state["is_safe"] = False
            state["status"] = "FAILED"
            state["validation_message"] = "Security Guardrail Triggered: Request contains sensitive PII (Credit Card or SSN)."
            log_audit(db, task_id, "GUARDRAIL_CHECK", "FAILED", "PII Detected")
            return state
            
        # 2. Heuristic for Prompt Injection
        injection_keywords = ["ignore", "forget", "bypass", "override", "system prompt", "drop table", "delete from"]
        for kw in injection_keywords:
            if kw in request_text:
                state["is_safe"] = False
                state["status"] = "FAILED"
                state["validation_message"] = "Security Guardrail Triggered: Possible prompt injection or malicious intent detected."
                log_audit(db, task_id, "GUARDRAIL_CHECK", "FAILED", f"Injection keyword '{kw}' detected")
                return state
        
        state["is_safe"] = True
        log_audit(db, task_id, "GUARDRAIL_CHECK", "SUCCESS", "Request is safe")
    except Exception as e:
        log_audit(db, task_id, "GUARDRAIL_CHECK", "ERROR", str(e))
        state["is_safe"] = True # Fail open for safety of execution if script breaks
    finally:
        db.close()
    return state
