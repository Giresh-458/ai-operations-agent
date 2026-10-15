import json
import re
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from langgraph.types import Command
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.schemas import TaskRequest, TaskResponse, TaskListResponse, AuditLogResponse, ProviderValidationRequest, ApprovalRequest
from app.models.database_models import Task, AuditLog
from app.agent.graph import agent_app
from app.agent.state import AgentState
from app.agent.nodes import log_audit, set_runtime_api_key, clear_runtime_api_key, is_valid_key
from app.config import settings

router = APIRouter()


def _redact_sensitive_text(value: str) -> str:
    value = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]", value)
    value = re.sub(r"\b\d{4}(?:[- ]\d{4}){3}\b", "[REDACTED_CARD_NUMBER]", value)
    value = re.sub(
        r"(?i)(credit\s*card|card number|card|cc)(\D{0,30})(?:\d[ -]?){13,19}\b",
        lambda match: match.group(1) + match.group(2) + "[REDACTED_CARD_NUMBER]",
        value,
    )
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value)
    return value

def run_agent(task_id: int, request_text: str):
    state: AgentState = {
        "task_id": task_id,
        "user_request": request_text,
        "intent": None,
        "parameters": None,
        "validation_result": None,
        "validation_message": None,
        "selected_tool": None,
        "tool_result": None,
        "status": "PENDING",
        "retry_count": 0,
        "requires_human": None,
        "final_response": None
    }
    # This invoke is synchronous, so it will block. 
    # For a real background task, it's fine.
    agent_app.invoke(state, config={"configurable": {"thread_id": str(task_id)}})

@router.get("/health")
def health_check():
    return {"status": "ok"}

@router.post("/validate_provider")
def validate_provider(req: ProviderValidationRequest):
    if not req.provider:
        raise HTTPException(status_code=400, detail="Provider not selected")
    if not req.api_key:
        raise HTTPException(status_code=400, detail="API key is required")
    if req.provider not in {"OpenAI", "Groq"}:
        raise HTTPException(status_code=400, detail="Unsupported provider")
    provider_key = req.api_key.strip()
    if not is_valid_key(provider_key):
        raise HTTPException(status_code=400, detail="A valid provider API key is required.")

    try:
        from langchain_openai import ChatOpenAI

        if req.provider == "OpenAI":
            llm = ChatOpenAI(model=settings.OPENAI_MODEL, api_key=provider_key, max_retries=0)
            llm.invoke("Hello")
            return {"message": "OpenAI connected successfully.", "model": settings.OPENAI_MODEL}
        elif req.provider == "Groq":
            llm = ChatOpenAI(model=settings.GROQ_MODEL, api_key=provider_key, base_url="https://api.groq.com/openai/v1", max_retries=0)
            llm.invoke("Hello")
            return {"message": "Groq connected successfully.", "model": settings.GROQ_MODEL}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Provider connection failed; verify the provider and key.")

@router.post("/tasks", response_model=TaskResponse)
def create_task(req: TaskRequest, db: Session = Depends(get_db)):
    req_text = req.get_request_text().strip()
    if not req_text:
        raise HTTPException(status_code=422, detail="Request text must not be empty.")
    if len(req_text) > 4000:
        raise HTTPException(status_code=422, detail="Request text must be at most 4000 characters.")
    if req.provider not in {"OpenAI", "Groq"}:
        raise HTTPException(status_code=422, detail="Provider must be OpenAI or Groq.")

    provider_key = req.api_key.strip() if isinstance(req.api_key, str) else None
    if req.api_key is not None and not is_valid_key(provider_key or ""):
        raise HTTPException(status_code=422, detail="A valid provider API key is required.")
    configured_key = settings.OPENAI_API_KEY if req.provider == "OpenAI" else ""
    active_key = provider_key or configured_key
    if not is_valid_key(active_key) and not settings.ALLOW_OFFLINE_MODE:
        raise HTTPException(
            status_code=503,
            detail="A provider API key is required; deterministic offline mode is disabled.",
        )

    task = Task(user_request=_redact_sensitive_text(req_text), status="PENDING")
    db.add(task)
    db.commit()
    db.refresh(task)
    
    log_audit(db, task.id, "TASK_CREATED", "SUCCESS")

    # Block high-confidence PII before it enters a checkpoint or model prompt.
    contains_sensitive_pii = bool(
        re.search(r"\b\d{4}(?:[- ]\d{4}){3}\b", req_text)
        or re.search(r"\b\d{3}-\d{2}-\d{4}\b", req_text)
        or re.search(r"(?i)(credit\s*card|card number|card|cc)(\D{0,30})(?:\d[ -]?){13,19}\b", req_text)
    )
    if contains_sensitive_pii:
        result = {
            "message": "Security Guardrail Triggered: Request contains sensitive PII (Credit Card or SSN)."
        }
        task.status = "FAILED"
        task.result = json.dumps(result)
        db.commit()
        log_audit(db, task.id, "GUARDRAIL_CHECK", "FAILED", "Sensitive PII detected before checkpointing")
        return TaskResponse(
            task_id=task.id,
            status=task.status,
            intent=None,
            selected_tool=None,
            parameters={},
            result=result,
            requires_human=None,
        )

    # In a full setup we might run this in the background, but for this project
    # returning the final state directly makes it easier to use without polling.
    # However, since they want "WAITING_FOR_APPROVAL", a synchronous block would be okay.
    state: AgentState = {
        "task_id": task.id,
        "user_request": req_text,
        "provider": req.provider,
        "intent": None,
        "parameters": None,
        "validation_result": None,
        "validation_message": None,
        "selected_tool": None,
        "tool_result": None,
        "status": "PENDING",
        "retry_count": 0,
        "requires_human": None,
        "final_response": None
    }
    
    # Run the graph synchronously
    set_runtime_api_key(task.id, provider_key)
    try:
        result_state = agent_app.invoke(
            state, config={"configurable": {"thread_id": str(task.id)}}
        )
    finally:
        clear_runtime_api_key(task.id)
    
    # Refresh task from DB
    db.commit()
    db.refresh(task)
    
    res = {}
    if task.result:
        res = json.loads(task.result)
        if "message" in res and result_state.get("final_response"):
            pass
        elif result_state.get("final_response"):
            res["message"] = result_state.get("final_response")
            
    if not res and result_state.get("final_response"):
        res = {"message": result_state.get("final_response")}
        
    params = json.loads(task.parameters) if task.parameters else {}
    return TaskResponse(
        task_id=task.id,
        status=task.status,
        intent=task.intent,
        selected_tool=result_state.get("selected_tool") if "result_state" in locals() else task.intent,
        parameters=params,
        result=res,
        requires_human=task.requires_human
    )

@router.get("/tasks", response_model=list[TaskListResponse])
def get_tasks(db: Session = Depends(get_db)):
    tasks = db.query(Task).order_by(Task.id.desc()).all()
    return [TaskListResponse(id=t.id, request=t.user_request, intent=t.intent, status=t.status) for t in tasks]

@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    res = None
    if task.result:
        res = json.loads(task.result)
        
    params = json.loads(task.parameters) if task.parameters else {}
    return TaskResponse(
        task_id=task.id,
        status=task.status,
        intent=task.intent,
        selected_tool=task.intent,
        parameters=params,
        result=res,
        requires_human=task.requires_human
    )

@router.get("/tasks/{task_id}/logs")
@router.get("/tasks/{task_id}/audit")
def get_task_logs(task_id: int, db: Session = Depends(get_db)):
    logs = db.query(AuditLog).filter(AuditLog.task_id == task_id).order_by(AuditLog.id.asc()).all()
    return [
        {
            "action": l.action,
            "status": l.status,
            "details": l.details,
            "created_at": l.created_at.isoformat() if l.created_at else None
        }
        for l in logs
    ]

@router.post("/tasks/{task_id}/approve")
def approve_task(task_id: int, req: ApprovalRequest | None = None, db: Session = Depends(get_db)):
    if req and req.api_key and not is_valid_key(req.api_key.strip()):
        raise HTTPException(status_code=422, detail="A valid provider API key is required.")

    # Atomically claim the waiting task so concurrent/double approvals cannot
    # resume the same checkpoint and execute the side effect twice.
    claimed = (
        db.query(Task)
        .filter(Task.id == task_id, Task.status == "WAITING_FOR_APPROVAL")
        .update({Task.status: "APPROVAL_IN_PROGRESS"}, synchronize_session=False)
    )
    db.commit()
    if claimed != 1:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        raise HTTPException(status_code=409, detail="Task is not waiting for approval or has already been handled")
    try:
        if req and req.api_key:
            # The initial request clears process-local credentials after pausing.
            # Restore the user-supplied key only for this resume, never checkpoint it.
            set_runtime_api_key(task_id, req.api_key.strip())
        agent_app.invoke(
            Command(resume="approved"),
            config={"configurable": {"thread_id": str(task_id)}},
        )
    except Exception as exc:
        # Do not reset to WAITING: the operation may already have committed.
        task = db.query(Task).filter(Task.id == task_id).first()
        if task and task.status == "APPROVAL_IN_PROGRESS":
            task.status = "FAILED"
            task.result = json.dumps({"message": "Approval resumption failed. Inspect the audit log before manual recovery."})
            db.commit()
        raise HTTPException(status_code=500, detail="Approval processing failed; inspect task status and audit log.") from exc
    finally:
        clear_runtime_api_key(task_id)
    db.expire_all()
    task = db.query(Task).filter(Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse(
        task_id=task.id,
        status=task.status,
        intent=task.intent,
        selected_tool=task.intent,
        parameters=json.loads(task.parameters) if task.parameters else {},
        result=json.loads(task.result) if task.result else None,
        requires_human=task.requires_human,
    )


@router.post("/tasks/{task_id}/reject")
def reject_task(task_id: int, db: Session = Depends(get_db)):
    # Same atomic claim as approval; each task can receive only one decision.
    claimed = (
        db.query(Task)
        .filter(Task.id == task_id, Task.status == "WAITING_FOR_APPROVAL")
        .update({Task.status: "REJECTION_IN_PROGRESS"}, synchronize_session=False)
    )
    db.commit()
    if claimed != 1:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        raise HTTPException(status_code=409, detail="Task is not waiting for approval or has already been handled")
    try:
        agent_app.invoke(
            Command(resume="rejected"),
            config={"configurable": {"thread_id": str(task_id)}},
        )
    except Exception as exc:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task and task.status == "REJECTION_IN_PROGRESS":
            task.status = "FAILED"
            task.result = json.dumps({"message": "Rejection resumption failed; inspect the audit log."})
            db.commit()
        raise HTTPException(status_code=500, detail="Rejection processing failed; inspect task status and audit log.") from exc
    db.expire_all()
    task = db.query(Task).filter(Task.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse(
        task_id=task.id,
        status=task.status,
        intent=task.intent,
        selected_tool=task.intent,
        parameters=json.loads(task.parameters) if task.parameters else {},
        result=json.loads(task.result) if task.result else None,
        requires_human=task.requires_human,
    )


@router.get('/database')
def get_database_state(db: Session = Depends(get_db)):
    from app.models.database_models import Customer, Order, SupportTicket, Notification
    customers = db.query(Customer).all()
    orders = db.query(Order).all()
    tickets = db.query(SupportTicket).all()
    notifications = db.query(Notification).all()
    return {
        'customers': [{'id': c.id, 'name': c.name} for c in customers],
        'orders': [{'id': o.id, 'customer_id': o.customer_id, 'status': o.status} for o in orders],
        # Ticket issue text can contain user-provided personal data; redact common
        # identifiers before exposing it in the explorer.
        'tickets': [
            {
                'id': t.id,
                'customer_id': t.customer_id,
                'issue': _redact_sensitive_text(t.issue or ""),
                'priority': t.priority,
                'status': t.status,
            }
            for t in tickets
        ],
        # Do not expose customer emails or notification bodies in the explorer.
        'notifications': [{'id': n.id, 'customer_id': n.customer_id, 'status': n.status} for n in notifications]
    }

