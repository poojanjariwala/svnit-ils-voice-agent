"""LLM service module for AI responses using Claude"""

def get_agent_response(
    business_name: str,
    knowledge_base: str,
    language_code: str,
    customer_query: str,
    conversation_history: list
) -> str:
    """Get response from Claude AI using business knowledge base"""
    # Stub implementation - Phase 2
    return f"Thank you for contacting {business_name}. We're processing your request."
