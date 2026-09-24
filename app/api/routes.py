import json
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.schemas import TaskRequest, TaskResponse, TaskListResponse, AuditLogResponse, ProviderValidationRequest
from app.models.database_models import Task, AuditLog
from app.agent.graph import agent_app
from app.agent.state import AgentState
from app.agent.nodes import log_audit

router = APIRouter()

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
    agent_app.invoke(state)

@router.get("/health")
def health_check():
    return {"status": "ok"}

@router.post("/validate_provider")
def validate_provider(req: ProviderValidationRequest):
    if not req.provider:
        raise HTTPException(status_code=400, detail="Provider not selected")
    if not req.api_key:
        raise HTTPException(status_code=400, detail="API key is required")
    
    try:
        from langchain_openai import ChatOpenAI
        from app.config import settings
        
        if req.provider == "OpenAI":
            llm = ChatOpenAI(model=settings.OPENAI_MODEL, api_key=req.api_key, max_retries=0)
            llm.invoke("Hello")
            return {"message": "OpenAI connected successfully.", "model": settings.OPENAI_MODEL}
        elif req.provider == "Groq":
            llm = ChatOpenAI(model=settings.GROQ_MODEL, api_key=req.api_key, base_url="https://api.groq.com/openai/v1", max_retries=0)
            llm.invoke("Hello")
            return {"message": "Groq connected successfully.", "model": settings.GROQ_MODEL}
        else:
            raise HTTPException(status_code=400, detail="Unsupported provider")
    except Exception as e:
        print(f"Provider validation failed: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Connection failed: {str(e)}")

@router.post("/tasks", response_model=TaskResponse)
def create_task(req: TaskRequest, db: Session = Depends(get_db)):
    req_text = req.get_request_text()
    task = Task(user_request=req_text, status="PENDING")
    db.add(task)
    db.commit()
    db.refresh(task)
    
    log_audit(db, task.id, "TASK_CREATED", "SUCCESS")
    
    # In a full setup we might run this in the background, but for this project
    # returning the final state directly makes it easier to use without polling.
    # However, since they want "WAITING_FOR_APPROVAL", a synchronous block would be okay.
    state: AgentState = {
        "task_id": task.id,
        "user_request": req_text,
        "provider": req.provider,
        "api_key": req.api_key,
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
    result_state = agent_app.invoke(state)
    
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
def approve_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task or task.status != "WAITING_FOR_APPROVAL":
        raise HTTPException(status_code=400, detail="Task is not waiting for approval")
        
    log_audit(db, task_id, "HUMAN_APPROVED", "SUCCESS")
    
    # Resume the graph by setting requires_human to APPROVED and re-invoking
    # In LangGraph you'd normally use checkpointing for this, but for simplicity
    # we can recreate the state up to the execution step.
    state: AgentState = {
        "task_id": task_id,
        "user_request": task.user_request,
        "intent": task.intent,
        "parameters": json.loads(task.parameters) if task.parameters else {},
        "validation_result": True,
        "validation_message": None,
        "selected_tool": task.intent,
        "tool_result": None,
        "status": "RUNNING",
        "retry_count": task.retry_count,
        "requires_human": "APPROVED",
        "final_response": None
    }
    
    from app.agent.nodes import execute_tool, check_result, final_response, select_tool
    state = execute_tool(state)
    state = check_result(state)
    state = final_response(state)
    db.commit() # End current transaction to see changes from SessionLocal
    task = db.query(Task).filter(Task.id == task_id).first()
    if state.get("status") in ["COMPLETED", "FAILED", "ERROR"]:
        task.status = state.get("status") if state.get("status") != "ERROR" else "FAILED"
        if state.get("tool_result"):
            task.result = json.dumps(state.get("tool_result"))
        db.commit()
    db.refresh(task)
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
        result=res
    )

@router.post("/tasks/{task_id}/reject")
def reject_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task or task.status != "WAITING_FOR_APPROVAL":
        raise HTTPException(status_code=400, detail="Task is not waiting for approval")
        
    task.status = "REJECTED"
    task.requires_human = "REJECTED"
    task.result = json.dumps({"message": "Task rejected by human."})
    db.commit()
    
    log_audit(db, task_id, "HUMAN_REJECTED", "SUCCESS")
    
    return {"status": "REJECTED"}

@router.get('/database')
def get_database_state(db: Session = Depends(get_db)):
    from app.models.database_models import Customer, Order, SupportTicket, Notification
    customers = db.query(Customer).all()
    orders = db.query(Order).all()
    tickets = db.query(SupportTicket).all()
    notifications = db.query(Notification).all()
    return {
        'customers': [{'id': c.id, 'name': c.name, 'email': c.email} for c in customers],
        'orders': [{'id': o.id, 'customer_id': o.customer_id, 'status': o.status} for o in orders],
        'tickets': [{'id': t.id, 'customer_id': t.customer_id, 'issue': t.issue, 'priority': t.priority, 'status': t.status} for t in tickets],
        'notifications': [{'id': n.id, 'customer_id': n.customer_id, 'message': n.message, 'status': n.status} for n in notifications]
    }

