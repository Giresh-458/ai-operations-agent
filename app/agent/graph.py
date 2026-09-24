from langgraph.graph import StateGraph, END
from app.agent.state import AgentState
from app.agent.nodes import (
    understand_request,
    validate_request,
    select_tool,
    execute_tool,
    check_result,
    retry_node,
    final_response,
    retrieve_docs,
    grade_docs,
    rewrite_query,
    generate_answer,
    guardrail_check
)

def build_graph():
    workflow = StateGraph(AgentState)
    
    workflow.add_node("understand_request", understand_request)
    workflow.add_node("validate_request", validate_request)
    workflow.add_node("select_tool", select_tool)
    workflow.add_node("execute_tool", execute_tool)
    workflow.add_node("check_result", check_result)
    workflow.add_node("retry", retry_node)
    workflow.add_node("final_response", final_response)
    
    # RAG Nodes
    workflow.add_node("retrieve_docs", retrieve_docs)
    workflow.add_node("grade_docs", grade_docs)
    workflow.add_node("rewrite_query", rewrite_query)
    workflow.add_node("generate_answer", generate_answer)
    
    # Guardrail Node
    workflow.add_node("guardrail_check", guardrail_check)
    
    workflow.set_entry_point("guardrail_check")
    
    def guardrail_router(state: AgentState):
        if state.get("is_safe") == False:
            return "final_response"
        return "understand_request"
        
    workflow.add_conditional_edges(
        "guardrail_check",
        guardrail_router,
        {"final_response": "final_response", "understand_request": "understand_request"}
    )
    
    def intent_router(state: AgentState):
        if state.get("intent") == "KNOWLEDGE_QUERY":
            return "retrieve_docs"
        return "validate_request"
        
    workflow.add_conditional_edges(
        "understand_request",
        intent_router,
        {"retrieve_docs": "retrieve_docs", "validate_request": "validate_request"}
    )
    
    # RAG Flow
    workflow.add_edge("retrieve_docs", "grade_docs")
    
    def grading_router(state: AgentState):
        if state.get("is_relevant"):
            return "generate_answer"
        if state.get("retry_count", 0) < 2:
            return "rewrite_query"
        # If still not relevant after retries, just try to answer anyway or gracefully decline
        return "generate_answer"
        
    workflow.add_conditional_edges(
        "grade_docs",
        grading_router,
        {"generate_answer": "generate_answer", "rewrite_query": "rewrite_query"}
    )
    
    workflow.add_edge("rewrite_query", "retrieve_docs")
    workflow.add_edge("generate_answer", END)
    
    def validation_router(state: AgentState):
        if state.get("status") == "FAILED" or not state.get("validation_result"):
            return "final_response"
        return "select_tool"
        
    workflow.add_conditional_edges(
        "validate_request",
        validation_router,
        {"final_response": "final_response", "select_tool": "select_tool"}
    )
    
    workflow.add_edge("select_tool", "execute_tool")
    workflow.add_edge("execute_tool", "check_result")
    
    def retry_router(state: AgentState):
        if state.get("status") == "ERROR":
            return "retry"
        return "final_response"
        
    workflow.add_conditional_edges(
        "check_result",
        retry_router,
        {"retry": "retry", "final_response": "final_response"}
    )
    
    def after_retry_router(state: AgentState):
        if state.get("status") == "FAILED":
            return "final_response"
        return "execute_tool"
        
    workflow.add_conditional_edges(
        "retry",
        after_retry_router,
        {"final_response": "final_response", "execute_tool": "execute_tool"}
    )
    
    workflow.add_edge("final_response", END)
    
    return workflow.compile()

agent_app = build_graph()
