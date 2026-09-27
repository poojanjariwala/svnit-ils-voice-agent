# backend/llm_service.py
# ─────────────────────────────────────────────
# Owner  : Henali
# Branch : feature/henali-voice-services
# Task   : Claude AI integration for generating customer support responses
#
# Functions to implement:
#   - get_agent_response(
#       business_name, knowledge_base, language_code,
#       customer_query, conversation_history
#     ) → str
#
# System prompts required for:
#   - "hi" → Hindi instructions + knowledge base
#   - "gu" → Gujarati instructions + knowledge base
#   - "en" → English instructions + knowledge base
#
# Uses: Anthropic Claude API (claude-opus-4-6 model)
# Fallback responses must be implemented for all 3 languages
#
# TODO: Add full LLM service implementation here
# ─────────────────────────────────────────────
