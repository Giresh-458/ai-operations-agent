from pydantic import BaseModel
from typing import Optional, Dict, Any, List

class TaskRequest(BaseModel):
    request: Optional[str] = None
    user_request: Optional[str] = None
    provider: Optional[str] = "OpenAI"
    api_key: Optional[str] = None

    def get_request_text(self) -> str:
        return self.request or self.user_request or ""

class TaskResponse(BaseModel):
    task_id: int
    status: str
    intent: Optional[str] = None
    selected_tool: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    requires_human: Optional[str] = None

class TaskListResponse(BaseModel):
    id: int
    request: str
    intent: Optional[str] = None
    status: str

class ProviderValidationRequest(BaseModel):
    provider: str
    api_key: str

class AuditLogResponse(BaseModel):
    action: str
    status: str

class IntentOutput(BaseModel):
    intent: str
    parameters: Dict[str, Any]
