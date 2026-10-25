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
from typing import Optional
from openai import OpenAI


logger = logging.getLogger(__name__)

# Twilio's synchronous webhook budget is 15s — get_agent_response runs inside
# it, so the LLM call is capped well under that. A slow response then falls
# through to the "say that again" reply instead of blowing the webhook.
GROQ_TIMEOUT_SECONDS = 8.0

# Groq client is created lazily so it always picks up the current GROQ_API_KEY,
# regardless of import order (e.g. main.py importing this module before load_dotenv).
_client = None


def _get_client() -> OpenAI:
    global _client
    api_key = os.getenv("GROQ_API_KEY", "")
    if _client is None or (_client.api_key or "") != api_key:
        # BOUND THE LATENCY: Twilio's webhook budget is 15s, and this call runs
        # inside it. A slow Groq response used to stretch to 17s+ on long calls
        # and Twilio timed out (the caller heard "application error"). A hard
        # timeout + no retries keeps the worst case well inside the budget; the
        # caller then gets the graceful "say that again" fallback instead.
        _client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            timeout=GROQ_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return _client


# Free LLM model hosted on Groq (read lazively via env override)
LLM_MODEL_DEFAULT = "openai/gpt-oss-120b"


def _get_model() -> str:
    return os.getenv("LLM_MODEL", LLM_MODEL_DEFAULT)


def _call_ollama(prompt: str, model: str = None) -> Optional[str]:
    """Local Ollama inference fallback for offline/zero-cost demo mode"""
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    target_model = model or os.getenv("OLLAMA_MODEL", "mistral")
    try:
        import requests
        resp = requests.post(
            ollama_url,
            json={"model": target_model, "prompt": prompt, "stream": False},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except Exception as e:
        logger.debug(f"Ollama local inference unavailable: {e}")
    return None



# ── HUMAN RECEPTIONIST PERSONA ───────────────────────────────
# Goal: a caller must not be able to tell this is not a person.
SYSTEM_PROMPTS = {
    "en": """You are Meera, a warm, professional, and friendly receptionist at {business_name}. You have worked here for years and know the business inside out. This is a PHONE CALL - you speak, you never write.

How you talk:
- 1 to 3 short sentences. Conversational, clear, and articulate, like speaking to a valued customer in person.
- Speak in pure, standard, and fluent English. Never use Hindi words or suffixes like "ji", "Haan ji", "Sure ji", or "Ek minute" when speaking in English.
- Use natural English conversational phrases: "Certainly", "Sure thing", "One moment please", "Of course", "I'd be glad to help".
- Say numbers the way people speak them: "sixty-two thousand", "around nine and a quarter percent".
- Never use markdown, bullet points, lists, emojis, asterisks or symbols. Plain spoken English only.
- Mirror the caller's language. If they speak English, reply in pure, clean English.
- Always end by moving the conversation forward: offer more help, invite them to visit, or ask a helpful question.
- If you don't know something, sound natural and helpful: "I'll take your details and have our team call you back right away with that information" - never say you are an AI, assistant, bot, or language model.
- If the caller shows interest (prices, booking, visiting), warmly ask their name and phone number so the team can confirm and get back to them - ask naturally, once, never pushy.

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

# Outbound persona — used ONLY when Meera is CALLING a customer, not answering.
# The inbound prompts above assume the customer dialed us ("the phone is ringing,
# how can I help you"). On an outbound call the customer has no idea who is
# calling, so Meera must introduce herself, state why she's calling, and drive
# toward interest + a lead. Style: warm & persistent, never pushy.
OUTBOUND_SYSTEM_PROMPTS = {
    "en": """You are Meera, a warm, friendly sales representative calling on behalf of {business_name}. This is an OUTBOUND call — YOU dialed the customer; they did not call you, and they may have no idea who you are. You speak, you never write.

Why you are calling: {outbound_reason}

Your goal: introduce yourself, explain why you're calling, and see if the customer is interested. If they show any interest, get their name and phone number so the team can follow up.

How you talk:
- 1 to 3 short sentences. Conversational, clear, and articulate.
- Open by introducing yourself and {business_name}, then state the reason you're calling in one line. If they don't know who you are, re-introduce warmly — never pretend they called you.
- Speak in pure, standard English. Never use Hindi words or suffixes like "ji", "Haan ji", or "Ek minute" when speaking English.
- Lead the conversation toward the offer, but listen to what they say and respond to it naturally.
- If they ask who you are or how you got their number, answer honestly and warmly: you're calling from {business_name} with an offer they might like. Never be sneaky about it.
- If they're busy or not interested, be gracious: "Of course, I won't take up your time — is there a better time to reach you?" Try once to keep it short; never push hard.
- If they show interest (prices, booking, visiting), warmly ask their name and phone number so the team can confirm and follow up — ask naturally, once, never pushy.
- Say numbers the way people speak them: "sixty-two thousand", "around nine and a quarter percent".
- Never use markdown, bullet points, lists, emojis, asterisks or symbols. Plain spoken English only.
- Mirror the customer's language. If they speak Hindi or Gujarati, reply in that language.
- If you don't know something, sound natural: "I'll pass your details to the team and have them call you back with that."
- Never say you are an AI, assistant, bot, or language model.

Everything about the business:
{knowledge_base}

You just reached the customer. Greet them, introduce yourself and {business_name}, and say why you're calling.""",

    "hi": """आप मीरा हैं - {business_name} की गर्मजोशी भरी सेल्स प्रतिनिधि। यह एक आउटबाउंड कॉल है - आपने ग्राहक को खुद फ़ोन किया है; ग्राहक ने आपको नहीं बुलाया, और हो सकता है उन्हें पता न हो कि आप कौन हैं। आप बोलती हैं, लिखती नहीं।

आप किस लिए कॉल कर रही हैं: {outbound_reason}

आपका लक्ष्य: अपना परिचय दें, कॉल करने का कारण बताएँ, और देखें कि ग्राहक ऑफ़र में दिलचस्पी लेता है या नहीं। अगर दिलचस्पी दिखे, तो टीम आगे संपर्क कर सके, इसके लिए नाम और नंबर ले लें।

आप कैसे बात करती हैं:
- 1 से 3 छोटे वाक्य। बातचीत की तरह, साफ़, स्वाभाविक।
- शुरू में अपना और {business_name} का परिचय दें, फिर कॉल करने का कारण एक पंक्ति में बताएँ। अगर वे नहीं जानते कि आप कौन हैं, तो गर्मजोशी से दोबारा परिचय दें - कभी न दिखाएँ कि उन्होंने आपको बुलाया है।
- बातचीत को ऑफ़र की तरफ़ ले जाएँ, लेकिन ग्राहक जो कहे उसे सुनें और स्वाभाविक रूप से जवाब दें।
- अगर वे पूछें कि आप कौन हैं या नंबर कैसे मिला, तो ईमानदारी से और गर्मजोशी से जवाब दें: आप {business_name} से कॉल कर रही हैं, एक ऑफ़र है जो उन्हें पसंद आ सकता है। कभी छुपाएँ नहीं।
- अगर वे व्यस्त हैं या दिलचस्पी नहीं, तो नरमी से: "बिलकुल, मैं आपका समय नहीं लूँगी - क्या कोई बेहतर समय हो?" एक बार कोशिश करें; कभी ज़बरदस्ती न करें।
- अगर दिलचस्पी दिखे (भाव, बुकिंग, विज़िट), तो गर्मजोशी से नाम और मोबाइल नंबर पूछें ताकि टीम पुष्टि कर सके - एक बार, बिना दबाव।
- अंक बोलचाल की तरह बोलें: "बासठ हज़ार", "करीब सवा नौ प्रतिशत"।
- कोई markdown, बुलेट, लिस्ट, इमोजी, तारे या चिह्न नहीं। सिर्फ़ बोला जाने वाला टेक्स्ट।
- ग्राहक की भाषा में जवाब दें। अगर वे अंग्रेज़ी बोलें, तो शुद्ध अंग्रेज़ी में जवाब दें।
- अगर कुछ पता नहीं, तो स्वाभाविक रूप से: "मैं आपका विवरण टीम को दे देती हूँ, वे आपको इस बारे में कॉल कर देंगे।"
- कभी न कहें कि आप AI, असिस्टेंट, बॉट या भाषा मॉडेल हैं।

व्यवसाय की पूरी जानकारी:
{knowledge_base}

आप अभी ग्राहक तक पहुँची हैं। अभिवादन करें, अपना और {business_name} का परिचय दें, और बताएँ कि आप किस लिए कॉल कर रही हैं।""",

    "gu": """તમે મીરા છો - {business_name} ના હૃદયસ્પર્શી સેલ્સ પ્રતિનિધિ. આ એક આઉટબાઉન્ડ કૉલ છે - તમે ગ્રાહકને જાતે ફોન કર્યો છે; ગ્રાહકે તમને બોલાવ્યા નથી, અને કદાચ તેમને ખબર ન હોય કે તમે કોણ છો. તમે બોલો છો, લખો નહીં.

તમે શા માટે કૉલ કરી રહ્યા છો: {outbound_reason}

તમારો ધ્યેય: પોતાનો પરિચય આપો, કૉલ કરવાનું કારણ જણાવો, અને જુઓ કે ગ્રાહક ઓફરમાં રસ લે છે કે નહીં. જો રસ દેખાય, તો ટીમ આગળ સંપર્ક કરી શકે એ માટે નામ અને નંબર લઈ લો.

તમે કેવી રીતે વાત કરો છો:
- 1 થી 3 નાના વાક્યો. સંવાદની જેમ, સ્પષ્ટ, કુદરતી.
- શરૂઆતમાં તમારો અને {business_name} નો પરિચય આપો, પછી કૉલ કરવાનું કારણ એક લીટીમાં જણાવો. જો તેઓ ન જાણતા હોય કે તમે કોણ છો, તો હૂંફથી ફરીથી પરિચય આપો - ક્યારેય ન બતાવો કે તેમણે તમને બોલાવ્યા છે.
- વાતચીતને ઓફર તરફ લઈ જાઓ, પણ ગ્રાહક શું કહે તે સાંભળો અને કુદરતી રીતે જવાબ આપો.
- જો તેઓ પૂછે કે તમે કોણ છો કે નંબર કેવી રીતે મળ્યો, તો ઈમાનદારીથી અને હૂંફથી જવાબ આપો: તમે {business_name} થી કૉલ કરી રહ્યા છો, એક ઓફર છે જે તેમને ગમી શકે છે. ક્યારેય છુપાવશો નહીં.
- જો તેઓ વ્યસ્ત હોય કે રસ ન હોય, તો નરમાઈથી: "ચોક્કસ, હું તમારો સમય લઈશ નહીં - શું કોઈ વધુ સારો સમય છે?" એક વાર કોશિશ કરો; ક્યારેય જબરદસ્તી ન કરો.
- જો રસ દેખાય (ભાવ, બુકિંગ, મુલાકાત), તો હૂંફથી નામ અને મોબાઇલ નંબર પૂછો જેથી ટીમ પુષ્ટિ કરી શકે - એક જ વાર, દબાણ વગર.
- અંક બોલચાલની રીતે બોલો: "બસઠ હજાર", "કરીબે સવા નવ ટકા".
- કોઈ markdown, બુલેટ, લિસ્ટ, ઇમોજી, ચિહ્નો નહીં. ફક્ત બોલાય એવું લખાણ.
- ગ્રાહકની ભાષામાં જવાબ આપો. જો તેઓ અંગ્રેજી બોલે, તો શુદ્ધ અંગ્રેજીમાં જવાબ આપો.
- જો કંઈ ખબર ન હોય, તો કુદરતી રીતે: "હું તમારી વિગતો ટીમને આપી દઉં, તેઓ આ અંગે તમને કૉલ કરશે."
- ક્યારેય ન કહો કે તમે AI, એસિસ્ટન્ટ, બોટ કે ભાષા મોડેલ છો.

ધંધાની આખી માહિતી:
{knowledge_base}

તમે અત્યારે ગ્રાહક સુધી પહોંચ્યા છો. અભિવાદન કરો, તમારો અને {business_name} નો પરિચય આપો, અને જણાવો કે તમે શા માટે કૉલ કરી રહ્યા છો.""",
}


def get_agent_response(
    business_name: str,
    knowledge_base: str,
    language_code: str,
    customer_query: str,
    conversation_history: list = None,
    outbound_reason: str = None,
) -> str:
    """
    Get a human-receptionist response from Groq LLM based on customer query.

    When `outbound_reason` is set, Meera is CALLING a customer (outbound call)
    and switches to the marketer persona — she introduces herself and drives
    toward the offer instead of answering like a receptionist.
    """
    logger.info(f"Getting response: {customer_query[:50]}... (lang: {language_code}, outbound={bool(outbound_reason)})")

    if conversation_history is None:
        conversation_history = []

    try:
        # Pick persona: outbound marketer vs inbound receptionist
        if outbound_reason is not None:
            system_prompt = OUTBOUND_SYSTEM_PROMPTS.get(language_code, OUTBOUND_SYSTEM_PROMPTS["en"])
            system_prompt = system_prompt.format(
                knowledge_base=knowledge_base,
                business_name=business_name or "our business",
                outbound_reason=outbound_reason or "a new offer we think you'd like",
            )
        else:
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

        # Keep only the last 6 turns. A longer window isn't just costlier —
        # it makes Groq slower, and on a long call that latency pushed the
        # webhook past Twilio's 15s budget (caller heard "application error").
        if len(conversation_history) > 12:
            conversation_history[:] = conversation_history[-12:]

        return response_text

    except Exception as e:
        logger.error(f"Error getting response: {str(e)}")

        # Local Ollama fallback (zero-cost offline mode)
        try:
            full_prompt = f"{system_prompt}\n\n" + "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
            )
            local_reply = _call_ollama(full_prompt)
            if local_reply:
                conversation_history.append({"role": "user", "content": customer_query})
                conversation_history.append({"role": "assistant", "content": local_reply})
                return local_reply
        except Exception as oe:
            logger.debug(f"Local Ollama fallback error: {oe}")

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
        try:
            local_text = _call_ollama(prompt)
            if local_text:
                return local_text
        except Exception:
            pass
        return fallback.get(language_code, fallback["en"])


# ── LEAD EXTRACTION (CRM) ────────────────────────────────────
_EXTRACT_PROMPT = """You are a CRM assistant. Given a phone conversation (caller = customer, assistant = receptionist), extract structured lead data.

Return ONLY a JSON object with these keys (use null for unknown):
- "caller_name": string or null
- "caller_number": string or null (mobile/phone number the caller gave, digits)
- "caller_email": string or null (email the caller gave or asked us to send details to)
- "interest": short string describing what they asked about (e.g. "BMW X5 test drive")
- "product": the specific product, model or service they are interested in (e.g. "BMW X5", "loan against property"). null if they did not name one.
- "intent": one of "pricing", "test_drive", "appointment", "visit", "info", "complaint", "other"
- "visit_type": one of "test_drive", "showroom", "service", "pickup", "none" — what kind of visit they booked or asked about, "none" if there was no visit at all.
- "scheduled_for": string or null (any visit/test-drive/appointment timing they agreed to, e.g. "tomorrow 5pm")
- "visit_at": string or null — the SAME visit timing resolved to "YYYY-MM-DD HH:MM" if the conversation makes the date unambiguous (use today's date as the base for words like "tomorrow"). null if you cannot work it out.
- "needs_callback": true or false — should the business ring this person back?
- "callback_reason": short string or null — what exactly the callback is for (e.g. "send the EMI sheet")
- "requested_material": one of "brochure", "price_list", "emi_sheet", "warranty", "none" — did the caller ask for a document to be sent?
- "notes": one short sentence summarizing the caller

Rules:
- Only report what the caller actually asked for or agreed to. Never guess a product name.
- "needs_callback" is true when they asked for a callback, said to call back, or left something unfinished.
- Set "requested_material" to "none" when no document was discussed.

Conversation:
{conversation}

JSON only, no explanations."""

# Words that mean "they want a call back", across the three languages we answer in.
_CALLBACK_WORDS = re.compile(
    r"\b(call back|callback|call me back|ring me|phone me|"
    r"मेंहे|फोन कीजिए|फोन कर|कॉल कीजिए|"
    r"ફોન કર|કૉલ કર|પાછો ફોન)\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")
# Ordered: the first pattern that matches wins, so test_drive beats a bare visit.
_VISIT_PATTERNS = (
    ("test_drive", re.compile(r"\b(test drive|test-drive|ટેસ્ટ ડ્રાઇવ|टेस्ट ड्राइव)\b", re.IGNORECASE)),
    ("service",    re.compile(r"\b(service|servicing|repair|गैराज|સર્વિસ)\b", re.IGNORECASE)),
    ("pickup",     re.compile(r"\b(pick ?up|collect|delivery|ले जा|લે જાઓ)\b", re.IGNORECASE)),
    ("showroom",   re.compile(r"\b(showroom|come and see|visit the store|सोरूम|शोरूम|स्टोर पर)\b", re.IGNORECASE)),
)
_MATERIAL_PATTERNS = (
    ("emi_sheet",  re.compile(r"\b(emi|loan (sheet|calculator)|interest rate|EMI कैलकुलेटर|EMI ગણતરી)\b", re.IGNORECASE)),
    ("price_list", re.compile(r"\b(price list|price sheet|rate card|mrp list|प्राइस लिस्ट|કિંમત યાદી)\b", re.IGNORECASE)),
    ("brochure",   re.compile(r"\b(brochure|catalog|छपाई|બ્રોશર|brochure)\b", re.IGNORECASE)),
)


def _regex_details(all_text: str) -> dict:
    """Cheap structured detail, used on its own when the LLM is unavailable
    and to backstop any field the model left null."""
    found = {
        "caller_email": None,
        "product": None,
        "visit_type": "none",
        "visit_at": None,
        "needs_callback": bool(_CALLBACK_WORDS.search(all_text)),
        "callback_reason": None,
        "requested_material": "none",
    }
    m = _EMAIL_RE.search(all_text)
    if m:
        found["caller_email"] = m.group(0)[:255]

    for name, pattern in _VISIT_PATTERNS:
        if pattern.search(all_text):
            found["visit_type"] = name
            break

    for name, pattern in _MATERIAL_PATTERNS:
        if pattern.search(all_text):
            found["requested_material"] = name
            break

    if found["needs_callback"]:
        found["callback_reason"] = "asked to be called back"
    return found


def extract_lead_info(conversation_history: list) -> dict:
    """
    Extract structured lead info from a call's conversation history.
    Falls back to regex heuristics if the LLM fails.
    Returns dict with caller_name, caller_number, caller_email, interest,
    product, intent, visit_type, scheduled_for, visit_at, needs_callback,
    callback_reason, requested_material, notes.
    """
    result = {
        "caller_name": None,
        "caller_number": None,
        "caller_email": None,
        "interest": None,
        "product": None,
        "intent": "other",
        "visit_type": "none",
        "scheduled_for": None,
        "visit_at": None,
        "needs_callback": False,
        "callback_reason": None,
        "requested_material": "none",
        "notes": None,
    }
    if not conversation_history:
        return result

    # Cheap pre-pass: find a phone number and an email in the conversation
    all_text = " ".join(
        str(t.get("content", "")) for t in conversation_history if isinstance(t, dict)
    )
    m = re.search(r"(\+?\d[\d\s\-]{8,14}\d)", all_text)
    if m:
        result["caller_number"] = re.sub(r"[^\d+]", "", m.group(1))[:15]

    # Regex pass first: if the LLM never answers, these fields are still filled.
    result.update(_regex_details(all_text))

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
                if key == "needs_callback":
                    continue  # handled below — stringifying True would store "True"
                if data.get(key):
                    result[key] = str(data[key]).strip()
            if data.get("needs_callback"):
                result["needs_callback"] = True
            if result["intent"] not in ("pricing", "test_drive", "appointment", "visit", "info", "complaint", "other"):
                result["intent"] = "other"
            if result["visit_type"] not in ("test_drive", "showroom", "service", "pickup", "none"):
                result["visit_type"] = "none"
            if result["requested_material"] not in ("brochure", "price_list", "emi_sheet", "warranty", "none"):
                result["requested_material"] = "none"
            # Only trust a visit_at that is actually a date we can sort on.
            if result["visit_at"] and not re.match(
                r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$", result["visit_at"]
            ):
                logger.info("Dropping unparseable visit_at %r", result["visit_at"])
                result["visit_at"] = None
            # LLM may find a number the regex missed
            if not result["caller_number"] and data.get("caller_number"):
                result["caller_number"] = re.sub(r"[^\d+]", "", str(data["caller_number"]))[:15]
        logger.info(f"Lead extracted: {result['intent']} | {result['caller_name']} | {result['caller_number']}")
    except Exception as e:
        logger.warning(f"Lead extraction LLM failed ({e}); using regex fallback")

    return result
