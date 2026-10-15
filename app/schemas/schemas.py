from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List, Literal

class TaskRequest(BaseModel):
    request: Optional[str] = Field(default=None, max_length=4000)
    user_request: Optional[str] = Field(default=None, max_length=4000)
    provider: Optional[str] = "OpenAI"
    api_key: Optional[str] = Field(default=None, max_length=512)

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
    api_key: str = Field(min_length=1, max_length=512)

class ApprovalRequest(BaseModel):
    api_key: Optional[str] = Field(default=None, max_length=512)


class AuditLogResponse(BaseModel):
    action: str
    status: str

class IntentOutput(BaseModel):
    intent: Literal[
        "CREATE_TICKET",
        "CHECK_ORDER",
        "UPDATE_EMAIL",
        "SEND_NOTIFICATION",
        "KNOWLEDGE_QUERY",
        "UNSUPPORTED",
    ]
    parameters: Dict[str, Any]
