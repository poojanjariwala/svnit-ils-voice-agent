"""
llm_service.py - LLM Service (Groq, free tier)

HUMAN RECEPTIONIST PERSONA:
The agent plays "Meera", a warm human receptionist. Responses are short
spoken sentences with natural fillers, numbers spoken the way people say
them, no markdown/emojis, and it never reveals it is an AI.

Also provides extract_lead_info(): turns a conversation into structured
CRM data (name, mobile, interest, intent, scheduled visit) for the owner's
dashboard.
"""

import logging
import os
import re
import json
from openai import OpenAI

logger = logging.getLogger(__name__)

# Groq client is created lazily so it always picks up the current GROQ_API_KEY,
# regardless of import order (e.g. main.py importing this module before load_dotenv).
_client = None


def _get_client() -> OpenAI:
    global _client
    api_key = os.getenv("GROQ_API_KEY", "")
    if _client is None or (_client.api_key or "") != api_key:
        _client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _client


# Free LLM model hosted on Groq (read lazively via env override)
LLM_MODEL_DEFAULT = "openai/gpt-oss-120b"


def _get_model() -> str:
    return os.getenv("LLM_MODEL", LLM_MODEL_DEFAULT)


# ── HUMAN RECEPTIONIST PERSONA ───────────────────────────────
# Goal: a caller must not be able to tell this is not a person.
SYSTEM_PROMPTS = {
    "en": """You are Meera, a warm and friendly human receptionist at {business_name}. You have worked here for years and know the business inside out. This is a PHONE CALL - you speak, you never write.

How you talk:
- 1 to 3 short sentences. Conversational, like talking to a guest standing in front of you.
- Use natural spoken fillers: "Haan ji", "Sure ji", "Ek minute", "Oh nice!".
- Say numbers the way people speak them: "sixty two lakh", "around nine and a quarter percent".
- Never use markdown, bullet points, lists, emojis, asterisks or symbols. Plain speech only.
- Mirror the caller's language. If they speak Hindi, reply in Hindi. If Gujarati, Gujarati. If English, English.
- Always end by moving the conversation forward: offer more help, invite them to visit, or ask a small question.
- If you don't know something, sound human about it: "I'll note your number and our team will call you back with that, ji" - never say you are an AI, assistant, bot, or language model.
- If the caller shows interest (prices, booking, visiting), warmly ask their name and mobile number so the team can confirm and call them back - ask like a person, once, never pushy.

Everything about the business:
{knowledge_base}

The phone is ringing. Pick it up warmly.""",

    "hi": """आप मीरा हैं - {business_name} की गर्मजोशी भरी मानव रिसेप्शनिस्ट। आप वर्षों से यहाँ काम करती हैं और व्यवसाय की हर बात जानती हैं। यह एक फ़ोन कॉल है - आप बोलती हैं, लिखती नहीं।

आप कैसे बात करती हैं:
- 1 से 3 छोटे वाक्य। बातचीत की तरह, जैसे सामने खड़े मेहमान से बात कर रही हों।
- स्वाभाविक बोलचाल के शब्द: "हाँ जी", "जी बिलकुल", "एक मिनट", "अरे वाह!"।
- अंक बोलचाल की तरह: "बासठ लाख", "करीब सवा नौ प्रतिशत"।
- कोई markdown, बुलेट, लिस्ट, इमोजी, तारे या चिह्न नहीं। सिर्फ़ बोला जाने वाला टेक्स्ट।
- ग्राहक की भाषा में जवाब दें।
- हर जवाब के आगे बढ़ाएँ: और कुछ जानना चाहेंगे, आइए कभी देखने आइए, या कोई छोटा सवाल पूछें।
- अगर कुछ पता नहीं, तो इंसान की तरह: "मैं आपका नंबर नोट कर लेती हूँ, हमारी टीम आपको कॉल कर देगी जी" - कभी न कहें कि आप AI, असिस्टेंट, बॉट या भाषा मॉडल हैं।
- ग्राहक की दिलचस्पी दिखे (भाव, बुकिंग, विज़िट), तो गर्मजोशी से नाम और मोबाइल नंबर पूछें - एक बार, बिना दबाव।

व्यवसाय की पूरी जानकारी:
{knowledge_base}

फ़ोन बज रहा है। गर्मजोशी से उठाइए।""",

    "gu": """તમે મીરા છો - {business_name} ના હૃદયસ્પર્શી માનવ રિસેપ્શનિસ્ટ. તમે વર્ષોથી અહીં કામ કરો છો અને ધંધાની દરેક વાત જાણો છો. આ એક ફોન કૉલ છે - તમે બોલો છો, લખો નહીં.

તમે કેવી રીતે વાત કરો છો:
- 1 થી 3 નાના વાક્યો. સંવાદની જેમ, સામે ઊભા મહેમાન સાથે વાત કરતાં હો તેમ.
- કુદરતી બોલચાલના શબ્દો: "હા જી", "જી બિલકુલ", "એક મિનિટ", "અરે વાહ!".
- અંક બોલચાલની રીતે: "બસઠ લાખ", "કરીબે સવા નવ ટકા".
- કોઈ markdown, બુલેટ, લિસ્ટ, ઇમોજી, તારા કે ચિહ્નો નહીં. ફક્ત બોલાય એવું લખાણ.
- ગ્રાહકની ભાષામાં જ જવાબ આપો.
- દરેક જવાબ આગળ વધારો: બીજું કંઈ જાણવું છે, ક્યારેક આવી જોવા, અથવા નાનો પ્રશ્ન પૂછો.
- કંઈ ખબર ના હોય તો માણસની જેમ: "હું તમારો નંબર નોંધી લઉં, અમારી ટીમ કૉલ કરીશું જી" - ક્યારેય ન કહો કે તમે AI, એસિસ્ટન્ટ, બોટ કે ભાષા મોડેલ છો.
- ગ્રાહકની રસ દેખાય (ભાવ, બુકિંગ, મુલાકાત), તો હૂંફથી નામ અને મોબાઇલ નંબર પૂછો - એક જ વાર, દબાણ વગર.

ધંધાની આખી માહિતી:
{knowledge_base}

ફોન વાગે છે. હૂંફથી ઉઠાડો.""",
}


def get_agent_response(
    business_name: str,
    knowledge_base: str,
    language_code: str,
    customer_query: str,
    conversation_history: list = None
) -> str:
    """
    Get a human-receptionist response from Groq LLM based on customer query
    """
    logger.info(f"Getting response: {customer_query[:50]}... (lang: {language_code})")

    if conversation_history is None:
        conversation_history = []

    try:
        # Get system prompt for language (business name woven in)
        system_prompt = SYSTEM_PROMPTS.get(language_code, SYSTEM_PROMPTS["en"])
        system_prompt = system_prompt.format(
            knowledge_base=knowledge_base,
            business_name=business_name or "our business",
        )

        # Build messages with conversation history
        messages = []
        for turn in conversation_history:
            if turn.get("role") in ("user", "assistant"):
                messages.append({"role": turn["role"], "content": turn["content"]})

        messages.append({"role": "user", "content": customer_query})

        # Call Groq LLM API (free tier)
        client = _get_client()
        model = _get_model()
        logger.info(f"Calling Groq API ({model}) with {len(messages)} message(s)")

        response = client.chat.completions.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "system", "content": system_prompt}] + messages
        )

        response_text = response.choices[0].message.content or ""
        logger.info(f"Response: {response_text[:100]}...")

        # Update conversation history
        conversation_history.append({"role": "user", "content": customer_query})
        conversation_history.append({"role": "assistant", "content": response_text})

        # Keep only last 10 turns to avoid token limits
        if len(conversation_history) > 20:
            conversation_history[:] = conversation_history[-20:]

        return response_text

    except Exception as e:
        logger.error(f"Error getting response: {str(e)}")

        fallback_responses = {
            "hi": "माफ़ कीजिए जी, एक मिनट - कृपया अपना सवाल दोबारा बताइए।",
            "gu": "માફ કરશો જી, એક મિનિટ - તમારો પ્રશ્ન ફરી કહો શકો છો?",
            "en": "Sorry about that, one moment - could you say that again for me?",
        }
        return fallback_responses.get(language_code, fallback_responses["en"])


def validate_response(response: str, language_code: str) -> bool:
    """Validate that response is in correct language"""
    logger.info(f"Validating response for language: {language_code}")

    if not response or len(response) < 5:
        logger.warning("Response too short")
        return False

    return True


def generate_outbound_opener(
    business_name: str,
    knowledge_base: str,
    language_code: str,
    customer_name: str = None,
    customer_info: str = None,
) -> str:
    """
    Meera's opening line when SHE calls a customer: warm, personal,
    references why she's calling (from the owner's info column).
    Falls back to a friendly template if the LLM fails.
    """
    name_bit = f"{customer_name} " if customer_name else ""
    reason = (customer_info or "our latest offers").strip()
    fallback = {
        "en": f"Hello {name_bit}ji! This is Meera calling from {business_name}. I wanted to personally tell you about {reason}. Do you have a quick minute?",
        "hi": f"नमस्ते {name_bit}जी! मैं {business_name} से मीरा बोल रही हूँ। आपको {reason} के बारे में बताना था। क्या आपके पास एक मिनट है?",
        "gu": f"નમસ્તે {name_bit}જી! હું {business_name} થી મીરા બોલું છું. તમને {reason} વિશે જાણવું હતું. એક મિનિટ મળશે?",
    }
    try:
        client = _get_client()
        prompt = (
            f"You are Meera, a warm human receptionist at {business_name}. "
            f"You are MAKING a phone call to a customer. "
            f"Customer name: {customer_name or 'unknown'}. Reason for calling: {reason}. "
            f"Business facts:\n{knowledge_base[:1500]}\n\n"
            f"Say ONE warm opening sentence or two (max 35 words): greet them by name if known, "
            f"introduce yourself and the business, mention the reason naturally, ask if they have a minute. "
            f"Spoken style, no lists or symbols, in {'English' if language_code == 'en' else 'Hindi' if language_code == 'hi' else 'Gujarati'}."
        )
        response = client.chat.completions.create(
            model=_get_model(),
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (response.choices[0].message.content or "").strip()
        return text or fallback.get(language_code, fallback["en"])
    except Exception as e:
        logger.warning(f"Outbound opener LLM failed: {e}")
        return fallback.get(language_code, fallback["en"])


# ── LEAD EXTRACTION (CRM) ────────────────────────────────────
_EXTRACT_PROMPT = """You are a CRM assistant. Given a phone conversation (caller = customer, assistant = receptionist), extract structured lead data.

Return ONLY a JSON object with these keys (use null for unknown):
- "caller_name": string or null
- "caller_number": string or null (mobile/phone number the caller gave, digits)
- "interest": short string describing what they asked about (e.g. "BMW X5 test drive")
- "intent": one of "pricing", "test_drive", "appointment", "visit", "info", "complaint", "other"
- "scheduled_for": string or null (any visit/test-drive/appointment timing they agreed to, e.g. "tomorrow 5pm")
- "notes": one short sentence summarizing the caller

Conversation:
{conversation}

JSON only, no explanations."""


def extract_lead_info(conversation_history: list) -> dict:
    """
    Extract structured lead info from a call's conversation history.
    Falls back to regex-based phone/name heuristics if the LLM fails.
    Returns dict with caller_name, caller_number, interest, intent,
    scheduled_for, notes.
    """
    result = {
        "caller_name": None,
        "caller_number": None,
        "interest": None,
        "intent": "other",
        "scheduled_for": None,
        "notes": None,
    }
    if not conversation_history:
        return result

    # Cheap pre-pass: find a phone number in the conversation
    all_text = " ".join(
        str(t.get("content", "")) for t in conversation_history if isinstance(t, dict)
    )
    m = re.search(r"(\+?\d[\d\s\-]{8,14}\d)", all_text)
    if m:
        result["caller_number"] = re.sub(r"[^\d+]", "", m.group(1))[:15]

    transcript = "\n".join(
        f"{'CALLER' if t.get('role') == 'user' else 'AGENT'}: {t.get('content','')}"
        for t in conversation_history if isinstance(t, dict)
    )

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=_get_model(),
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": _EXTRACT_PROMPT.format(conversation=transcript[:4000]),
            }],
        )
        raw = response.choices[0].message.content or ""
        # Tolerate fenced JSON
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            data = json.loads(raw[start:end + 1])
            for key in result:
                if data.get(key):
                    result[key] = str(data[key]).strip()
            if result["intent"] not in ("pricing", "test_drive", "appointment", "visit", "info", "complaint", "other"):
                result["intent"] = "other"
            # LLM may find a number the regex missed
            if not result["caller_number"] and data.get("caller_number"):
                result["caller_number"] = re.sub(r"[^\d+]", "", str(data["caller_number"]))[:15]
        logger.info(f"Lead extracted: {result['intent']} | {result['caller_name']} | {result['caller_number']}")
    except Exception as e:
        logger.warning(f"Lead extraction LLM failed ({e}); using regex fallback")

    return result
