from typing import TypedDict, Optional, Dict, Any, List

class AgentState(TypedDict):
    task_id: int
    user_request: str
    provider: Optional[str]
    api_key: Optional[str]
    intent: Optional[str]
    parameters: Optional[Dict[str, Any]]
    validation_result: Optional[bool]
    validation_message: Optional[str]
    selected_tool: Optional[str]
    tool_result: Optional[Dict[str, Any]]
    status: str
    retry_count: int
    requires_human: Optional[str]
    final_response: Optional[str]
    
    # RAG specific fields
    search_query: Optional[str]
    documents: Optional[List[Dict[str, Any]]]
    is_relevant: Optional[bool]
    
    # Guardrail fields
    is_safe: Optional[bool]
