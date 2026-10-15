import json
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from app.config import settings
from app.agent.state import AgentState
from app.schemas.schemas import IntentOutput
from app.database import SessionLocal, engine
from app.models.database_models import Task, AuditLog
from app.tools.tickets import create_support_ticket
from app.tools.orders import check_order_status
from app.tools.customers import update_customer_email
from app.tools.notifications import send_notification
from langgraph.types import interrupt

# Runtime credentials are kept out of checkpointed LangGraph state.
_RUNTIME_API_KEYS = {}


def set_runtime_api_key(task_id: int, api_key: str | None) -> None:
    if api_key:
        _RUNTIME_API_KEYS[task_id] = api_key


def clear_runtime_api_key(task_id: int) -> None:
    _RUNTIME_API_KEYS.pop(task_id, None)


def get_runtime_api_key(state: AgentState) -> str:
    return _RUNTIME_API_KEYS.get(state["task_id"]) or settings.OPENAI_API_KEY

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
        active_key = get_runtime_api_key(state)
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
            llm = get_llm(state.get("provider", "OpenAI"), get_runtime_api_key(state))
            chain = prompt | llm | parser
            output = chain.invoke({"request": state["user_request"], "format_instructions": parser.get_format_instructions()})
            
        state["intent"] = output.intent
        state["parameters"] = output.parameters
        update_task_status(db, task_id, "VALIDATING", intent=output.intent, parameters=output.parameters)
        log_audit(db, task_id, "INTENT_DETECTED", "SUCCESS", json.dumps({"intent": output.intent}))
    except Exception as e:
        log_audit(db, task_id, "INTENT_DETECTED", "FAILED", type(e).__name__)
        state["status"] = "FAILED"
        state["final_response"] = "The request could not be interpreted safely. Please retry with a clearer request."
        update_task_status(db, task_id, "FAILED", result={"message": state["final_response"]})
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
                
        invalid = []
        import re

        def positive_int(value):
            return isinstance(value, int) and not isinstance(value, bool) and value > 0

        if intent == "CREATE_TICKET":
            if "customer_id" in params and not positive_int(params["customer_id"]):
                invalid.append("customer_id must be a positive integer")
            if "issue" in params and (not isinstance(params["issue"], str) or not params["issue"].strip() or len(params["issue"]) > 1000):
                invalid.append("issue must be a non-empty string of at most 1000 characters")
            if "priority" in params:
                if not isinstance(params["priority"], str) or params["priority"].upper() not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                    invalid.append("priority must be LOW, MEDIUM, HIGH, or CRITICAL")
                else:
                    params["priority"] = params["priority"].upper()
        elif intent == "CHECK_ORDER":
            if "order_id" in params and not positive_int(params["order_id"]):
                invalid.append("order_id must be a positive integer")
        elif intent == "UPDATE_EMAIL":
            if "customer_id" in params and not positive_int(params["customer_id"]):
                invalid.append("customer_id must be a positive integer")
            if "new_email" in params and (not isinstance(params["new_email"], str) or len(params["new_email"]) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", params["new_email"])):
                invalid.append("new_email must be a valid email address")
        elif intent == "SEND_NOTIFICATION":
            if "customer_id" in params and not positive_int(params["customer_id"]):
                invalid.append("customer_id must be a positive integer")
            if "message" in params and (not isinstance(params["message"], str) or not params["message"].strip() or len(params["message"]) > 2000):
                invalid.append("message must be a non-empty string of at most 2000 characters")

        if missing:
            state["validation_result"] = False
            state["validation_message"] = f"Please provide the missing information: {', '.join(missing)}."
            log_audit(db, task_id, "VALIDATION_FAILED", "SUCCESS", f"Missing: {missing}")
        elif invalid:
            state["validation_result"] = False
            state["validation_message"] = "Invalid request parameters: " + "; ".join(invalid) + "."
            log_audit(db, task_id, "VALIDATION_FAILED", "SUCCESS", state["validation_message"])
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
        log_audit(db, task_id, "VALIDATION_STARTED", "FAILED", type(e).__name__)
        state["status"] = "FAILED"
        state["validation_result"] = False
        state["validation_message"] = "Request validation failed. No operation was executed."
        update_task_status(db, task_id, "FAILED", result={"message": state["validation_message"]})
    finally:
        db.close()
        
    return state

def request_human_approval(state: AgentState) -> AgentState:
    """Pause until the API resumes this task with an explicit decision."""
    decision = interrupt({
        "type": "human_approval_required",
        "task_id": state["task_id"],
        "intent": state.get("intent"),
        "parameters": state.get("parameters", {}),
        "message": "Approve or reject this critical operation.",
    })
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        if decision == "approved":
            state["requires_human"] = "APPROVED"
            state["status"] = "RUNNING"
            update_task_status(db, task_id, "RUNNING", requires_human="APPROVED")
            log_audit(db, task_id, "HUMAN_APPROVED", "SUCCESS")
        else:
            state["requires_human"] = "REJECTED"
            state["status"] = "REJECTED"
            state["final_response"] = "Task rejected by human."
            update_task_status(db, task_id, "REJECTED", result={"message": state["final_response"]}, requires_human="REJECTED")
            log_audit(db, task_id, "HUMAN_REJECTED", "SUCCESS")
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
        # Keep internal database/provider details out of API responses and audit logs.
        state["tool_result"] = {"error": "Operation failed. Verify the supplied IDs and try again."}
        log_audit(db, task_id, "TOOL_EXECUTED", "FAILED", type(e).__name__)
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
        if state.get("is_safe") is False:
            state["status"] = "FAILED"
            state["final_response"] = state.get("validation_message", "Security guardrail blocked this request.")
            update_task_status(db, task_id, "FAILED", result={"message": state["final_response"]})
            log_audit(db, task_id, "TASK_BLOCKED", "FAILED")
        elif state.get("status") == "REJECTED" or state.get("requires_human") == "REJECTED":
            state["status"] = "REJECTED"
            state["final_response"] = "Task rejected by human."
            update_task_status(db, task_id, "REJECTED", result={"message": state["final_response"]}, requires_human="REJECTED")
        elif state.get("requires_human") == "WAITING":
            state["final_response"] = "Human approval is required before creating this critical ticket."
        elif not state.get("validation_result"):
            state["status"] = "FAILED"
            state["final_response"] = state.get("validation_message", "Validation failed.")
            update_task_status(db, task_id, "FAILED", result={"message": state["final_response"]})
        elif state.get("status") == "COMPLETED":
            result = state.get("tool_result", {})
            if state.get("intent") == "CHECK_ORDER":
                state["final_response"] = f"Order {result.get('order_id')} is {result.get('status')}."
            elif state.get("intent") == "CREATE_TICKET":
                state["final_response"] = f"Ticket {result.get('ticket_id')} created."
            elif state.get("intent") == "UPDATE_EMAIL":
                state["final_response"] = f"Customer {result.get('customer_id')} email updated successfully."
            elif state.get("intent") == "SEND_NOTIFICATION":
                state["final_response"] = f"Notification {result.get('notification_id')} sent."
            else:
                state["final_response"] = json.dumps(result)
            update_task_status(db, task_id, "COMPLETED", result=result)
            log_audit(db, task_id, "TASK_COMPLETED", "SUCCESS")
        elif state.get("status") in ("FAILED", "ERROR"):
            state["status"] = "FAILED"
            error = state.get("tool_result", {}).get("error", "")
            state["final_response"] = "Task failed to complete. " + str(error)
            update_task_status(db, task_id, "FAILED", result={"error": state["final_response"]})
            log_audit(db, task_id, "TASK_FAILED", "FAILED")
    finally:
        db.close()
    return state

from app.models.database_models import KnowledgeArticle
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import or_


def _query_terms(text: str) -> set[str]:
    import re
    stop_words = {"the", "and", "for", "what", "is", "are", "was", "were", "how", "to", "a", "an", "of", "in", "on", "my", "your", "with"}
    return {token.rstrip("s") for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2 and token not in stop_words}


def _lexical_score(query: str, title: str, content: str) -> int:
    return len(_query_terms(query) & _query_terms(title + " " + content))

def retrieve_docs(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    query = state.get("search_query") or state.get("parameters", {}).get("query") or state["user_request"]
    state["search_query"] = query
    
    try:
        log_audit(db, task_id, "RAG_RETRIEVE", "STARTED", "Knowledge query received; text redacted")
        
        # Try Vector Search
        from app.config import settings
        embedding_key = settings.OPENAI_API_KEY
        if not is_valid_key(embedding_key) and state.get("provider", "OpenAI") == "OpenAI":
            embedding_key = get_runtime_api_key(state)
        if engine.dialect.name == "postgresql" and is_valid_key(embedding_key):
            embeddings = OpenAIEmbeddings(api_key=embedding_key, max_retries=1)
            vector = embeddings.embed_query(query)
            articles = db.query(KnowledgeArticle).order_by(KnowledgeArticle.embedding.cosine_distance(vector)).limit(3).all()
        else:
            # Deterministic lexical fallback for SQLite/offline mode: never return arbitrary documents.
            terms = _query_terms(query)
            if terms:
                filters = [KnowledgeArticle.title.ilike(f"%{term}%") | KnowledgeArticle.content.ilike(f"%{term}%") for term in terms]
                articles = db.query(KnowledgeArticle).filter(or_(*filters)).limit(10).all()
                articles = sorted(articles, key=lambda article: _lexical_score(query, article.title or "", article.content or ""), reverse=True)
            else:
                articles = []
                
        docs = [{"title": a.title, "content": a.content} for a in articles]
        state["documents"] = docs
        log_audit(db, task_id, "RAG_RETRIEVE", "SUCCESS", json.dumps([d["title"] for d in docs]))
    except Exception as e:
        log_audit(db, task_id, "RAG_RETRIEVE", "FAILED", type(e).__name__)
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
            
        active_key = get_runtime_api_key(state)
        if not is_valid_key(active_key):
            query_terms = _query_terms(query)
            required_overlap = min(2, len(query_terms))
            relevant_docs = [doc for doc in docs if required_overlap > 0 and _lexical_score(query, doc.get("title", ""), doc.get("content", "")) >= required_overlap]
            state["documents"] = relevant_docs
            state["is_relevant"] = bool(relevant_docs)
            log_audit(db, task_id, "RAG_GRADE", "SUCCESS", f"Offline relevant documents: {len(relevant_docs)}")
            return state
            
        llm = get_llm(state.get("provider", "OpenAI"), get_runtime_api_key(state))
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a grader assessing relevance of a retrieved document to a user question. "
                       "Answer ONLY with 'yes' if relevant or 'no' if irrelevant. No other text."),
            ("user", "Question: {question}\n\nDocument: {document}")
        ])
        
        chain = prompt | llm
        
        relevant_docs = []
        for doc in docs:
            result = chain.invoke({"question": query, "document": doc["content"]})
            if result.content.strip().lower() in {"yes", "yes."}:
                relevant_docs.append(doc)
        state["documents"] = relevant_docs
        state["is_relevant"] = bool(relevant_docs)
        log_audit(db, task_id, "RAG_GRADE", "SUCCESS", f"Relevant documents: {len(relevant_docs)}")
    except Exception:
        log_audit(db, task_id, "RAG_GRADE", "FAILED", "Relevance grading failed")
        state["documents"] = []
        state["is_relevant"] = False
    finally:
        db.close()
    return state

def rewrite_query(state: AgentState) -> AgentState:
    db = SessionLocal()
    task_id = state["task_id"]
    try:
        log_audit(db, task_id, "RAG_REWRITE", "STARTED")
        query = state["search_query"]
        
        active_key = get_runtime_api_key(state)
        if not is_valid_key(active_key):
            # Keep the original query in offline mode; generic terms can make unrelated documents look relevant.
            state["retry_count"] += 1
            log_audit(db, task_id, "RAG_REWRITE", "SUCCESS", "Offline query retained")
            return state
            
        llm = get_llm(state.get("provider", "OpenAI"), get_runtime_api_key(state))
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a question re-writer. Look at the input question and rewrite it to be better optimized for vector database retrieval. Just output the rewritten question and nothing else."),
            ("user", "{question}")
        ])
        
        chain = prompt | llm
        result = chain.invoke({"question": query})
        
        state["search_query"] = result.content.strip()
        state["retry_count"] += 1
        log_audit(db, task_id, "RAG_REWRITE", "SUCCESS", "Query rewritten; text redacted")
    except Exception:
        state["retry_count"] = state.get("retry_count", 0) + 1
        log_audit(db, task_id, "RAG_REWRITE", "FAILED", "Query rewrite failed")
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
        if not docs or state.get("is_relevant") is not True:
            state["final_response"] = "I could not find enough relevant information in the knowledge base to answer confidently."
            state["status"] = "COMPLETED"
            update_task_status(db, task_id, "COMPLETED", result={"message": state["final_response"]})
            log_audit(db, task_id, "RAG_GENERATE", "SUCCESS", "Declined due to insufficient relevant evidence")
            return state
        
        context = "\n\n".join([f"Title: {d['title']}\nContent: {d['content']}" for d in docs])
        
        active_key = get_runtime_api_key(state)
        if not is_valid_key(active_key):
            state["final_response"] = "\n\n".join([d["title"] + ": " + d["content"] for d in docs])
            state["status"] = "COMPLETED"
            update_task_status(db, task_id, "COMPLETED", result={"message": state["final_response"]})
            log_audit(db, task_id, "RAG_GENERATE", "SUCCESS", "Mock answer generated")
            return state
            
        llm = get_llm(state.get("provider", "OpenAI"), get_runtime_api_key(state))
        prompt = ChatPromptTemplate.from_messages([
            ("system", "Answer using only the retrieved evidence. If it does not support an answer, say you do not know. Keep the answer concise and cite the title of the source document.\n\nContext:\n{context}"),
            ("user", "Question: {question}")
        ])
        
        chain = prompt | llm
        result = chain.invoke({"question": query, "context": context})
        
        state["final_response"] = result.content
        state["status"] = "COMPLETED"
        update_task_status(db, task_id, "COMPLETED", result={"message": state["final_response"]})
        log_audit(db, task_id, "RAG_GENERATE", "SUCCESS")
    except Exception:
        log_audit(db, task_id, "RAG_GENERATE", "FAILED", "Answer generation failed")
        state["status"] = "FAILED"
        state["final_response"] = "The knowledge answer could not be generated safely. Please try again later."
        update_task_status(db, task_id, "FAILED", result={"message": state["final_response"]})
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
        # Fail closed: do not execute downstream tools if validation itself fails.
        log_audit(db, task_id, "GUARDRAIL_CHECK", "ERROR", "Guardrail evaluation failed")
        state["is_safe"] = False
        state["status"] = "FAILED"
        state["validation_message"] = "Security Guardrail Error: the request could not be safely validated."
    finally:
        db.close()
    return state
