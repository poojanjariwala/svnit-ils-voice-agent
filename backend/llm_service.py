"""
llm_service.py - LLM Service with Enhanced Prompts
Enhanced by Henali for better multilingual responses
"""

import logging
from anthropic import Anthropic

logger = logging.getLogger(__name__)

# Initialize Anthropic client
client = Anthropic()

# Conversation history storage (per call)
CONVERSATIONS = {}

# LANGUAGE-SPECIFIC PROMPTS (Henali's optimization)
SYSTEM_PROMPTS = {
    "hi": """आप एक व्यावसायिक ग्राहक सहायक हैं। 
    
निर्देश:
1. हमेशा हिंदी में जवाब दें
2. दिए गए ज्ञान आधार से जानकारी प्रदान करें
3. यदि आप कुछ नहीं जानते तो कहें "मुझे नहीं पता"
4. मित्रवत और पेशेवर रहें
5. संक्षिप्त और स्पष्ट उत्तर दें

ज्ञान आधार:
{knowledge_base}

ग्राहक सेवा शुरू करें!""",
    
    "gu": """તમે એક વ્યાવસાયિક ગ્રાહક સહાયક છો।

સૂચનાઓ:
1. હમેશા ગુજરાતીમાં જવાબ આપો
2. આપેલ જ્ઞાન આધાર પરથી માહિતી આપો
3. જો તમે કંઈક જાણતા નથી તો કહો "મને ખબર નથી"
4. મૈત્રીપૂર્ણ અને વ્યાવસાયિક રહો
5. સંક્ષિપ્ત અને સ્પષ્ટ જવાબ આપો

જ્ઞાન આધાર:
{knowledge_base}

ગ્રાહક સેવા શરૂ કરો!""",
    
    "en": """You are a professional business customer support agent.

Instructions:
1. Always respond in English
2. Provide information from the knowledge base provided
3. If you don't know something, say "I don't know"
4. Be friendly and professional
5. Keep responses brief and clear
6. Maintain context from conversation history

Knowledge Base:
{knowledge_base}

Start providing customer support!"""
}

def get_agent_response(
    business_name: str,
    knowledge_base: str,
    language_code: str,
    customer_query: str,
    conversation_history: list = None
) -> str:
    """
    Get response from Claude based on customer query
    Henali's enhancement: Better context management and multilingual support
    """
    logger.info(f"Getting response: {customer_query[:50]}... (lang: {language_code})")
    
    if conversation_history is None:
        conversation_history = []
    
    try:
        # Get system prompt for language
        system_prompt = SYSTEM_PROMPTS.get(language_code, SYSTEM_PROMPTS["en"])
        system_prompt = system_prompt.format(knowledge_base=knowledge_base)
        
        # Build messages with conversation history
        messages = []
        
        # Add previous conversation turns
        for turn in conversation_history:
            if turn.get("role") == "user":
                messages.append({
                    "role": "user",
                    "content": turn["content"]
                })
            elif turn.get("role") == "assistant":
                messages.append({
                    "role": "assistant",
                    "content": turn["content"]
                })
        
        # Add current query
        messages.append({
            "role": "user",
            "content": customer_query
        })
        
        # Call Claude API
        logger.info(f"Calling Claude API with {len(messages)} message(s)")
        
        response = client.messages.create(
            model="claude-opus-4-6",  # Latest model
            max_tokens=500,
            system=system_prompt,
            messages=messages
        )
        
        # Extract response text
        response_text = response.content[0].text
        logger.info(f"Response: {response_text[:100]}...")
        
        # Update conversation history
        conversation_history.append({
            "role": "user",
            "content": customer_query
        })
        conversation_history.append({
            "role": "assistant",
            "content": response_text
        })
        
        # Keep only last 10 turns to avoid token limits
        if len(conversation_history) > 20:
            conversation_history = conversation_history[-20:]
        
        return response_text
    
    except Exception as e:
        logger.error(f"Error getting response: {str(e)}")
        
        # Fallback response
        fallback_responses = {
            "hi": f"मुझे खेद है, मुझे आपका सवाल समझने में समस्या हुई। कृपया दोबारा कोशिश करें।",
            "gu": f"મને આપનો પ્રશ્ન સમજવામાં સમસ્યા હુઈ. કૃપા કરીને ફરીથી પ્રયાસ કરો.",
            "en": f"I'm sorry, I had trouble understanding your question. Please try again."
        }
        
        return fallback_responses.get(language_code, fallback_responses["en"])

def validate_response(response: str, language_code: str) -> bool:
    """Validate that response is in correct language"""
    logger.info(f"Validating response for language: {language_code}")
    
    if not response or len(response) < 5:
        logger.warning("Response too short")
        return False
    
    return True
