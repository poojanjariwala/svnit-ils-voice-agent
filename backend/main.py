# backend/main.py
# ─────────────────────────────────────────────
# Owner  : Poojan
# Branch : feature/poojan-backend
# Task   : FastAPI application entry point
#
# Endpoints to implement:
#   GET  /                              → health check
#   POST /api/business                  → register new business + upload doc
#   GET  /api/business/{business_id}    → get business details
#   GET  /api/businesses                → list all businesses
#   GET  /api/analytics/overview        → system-wide analytics
#   POST /twilio/answer/{business_id}   → Twilio webhook (incoming call)
#   POST /twilio/event/{business_id}    → Twilio webhook (speech result)
#
# Integrates with:
#   - database.py   (Hardik)
#   - doc_processor.py, llm_service.py, voice_service.py (Henali)
#
# TODO: Add full FastAPI application here
# ─────────────────────────────────────────────
import os
import uuid
import time
import asyncio
import functools
import html
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, Form, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import requests
from sqlalchemy.orm import Session
from pathlib import Path

# Load .env BEFORE importing services (they read API keys at import/init time)
load_dotenv()

from doc_processor import extract_text, clean_and_chunk
from llm_service import get_agent_response
from voice_service import download_recording, transcribe_audio, synthesize_speech, AUDIO_DIR
from database import SessionLocal, get_db, create_business_record, get_business_record, get_all_businesses
from database import increment_call_count, create_call_record, complete_call, get_system_analytics
from database import get_businesses_by_owner, get_owner_analytics
from database import save_conversation, create_lead_record, get_leads_for_business, get_leads_for_owner
from database import start_call, get_call_transcripts_for_owner
from database import migrate_missing_columns, apply_lead_details, DETAIL_FIELDS, init_schema
from database import create_campaign, create_single_outbound, get_campaign_with_calls, get_outbound_for_owner, mark_outbound
from auth import get_current_user, hash_password, verify_password, create_token
from models import User, Lead, Campaign, OutboundCall, Conversation
import phone_service
from phone_service import place_call, twilio_configured, parse_call_list
from twilio_security import verify_twilio_request
from llm_service import extract_lead_info, generate_outbound_opener

# LOGGING
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create audio directory
Path(AUDIO_DIR).mkdir(exist_ok=True)

# FASTAPI APP
app = FastAPI(
    title="AI Voice Agent",
    description="Scalable AI voice agent for business customer care",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# STATIC FILES
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")
# NOTE: the frontend is mounted at "/" at the BOTTOM of this file, after all
# API/voice routes are registered — a "/" mount registered earlier would
# swallow every request before the routes can match.

def _public() -> str:
    """
    Public URL used in TwiML audio links and webhook URLs.
    Precedence: runtime override file (written by start_tunnel.py so a new
    tunnel URL applies WITHOUT restarting the server) > .env > localhost.
    """
    override = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public_url.txt")
    try:
        with open(override, encoding="utf-8") as f:
            val = f.read().strip()
            if val:
                return val
    except OSError:
        pass
    return os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

# IN-MEMORY STORAGE FOR CALL SESSIONS
# Key: f"{business_id}:{call_uuid}" → {"history": [...], "silences": int}
CALL_SESSIONS = {}
MAX_CALL_SESSIONS = 500  # prevent unbounded growth

# BCP-47 voice codes per business language (<Say> / Gather)
LANG_ATTR = {
    "hi": "hi-IN",
    "gu": "gu-IN",
    "en": "en-US",
}

# Reprompt / farewell texts per business language (spoken like a person)
REPROMPT_TEXTS = {
    "hi": "जी? मैं सुन रही हूँ, बताइए ना।",
    "gu": "હા જી? હું સાંભળું છું, કહો ના.",
    "en": "Yes? I'm listening, go ahead.",
}
FAREWELL_TEXTS = {
    "hi": "ठीक है जी, बात करने के लिए धन्यवाद। आपका दिन शुभ हो, जी।",
    "gu": "ઠીક છે જી, વાત કરવા આભાર. તમારો દિવસ શુભ રહે જી.",
    "en": "Okay ji, thank you for calling. Have a lovely day!",
}


# ─────────────────────────────────────────────────────────────
# PRODUCTION HARDENING — a webhook must never 5xx.
#
# A 5xx (or an exception FastAPI turns into one) makes Twilio play its
# "application error" and hang up on a live caller. Every webhook therefore degrades
# to a spoken apology in the call's own language, while the real stack
# trace is logged for us. Audio failures degrade the same way: the text
# we were going to PLAY is SPoken by the provider's own TTS instead.
# ─────────────────────────────────────────────────────────────

APOLOGY_TEXTS = {
    "hi": "क्षमा कीजिए जी, अभी तकनीकी समस्या है। कृपया थोड़ी देर बाद फिर से कॉल कीजिए। धन्यवाद।",
    "gu": "ક્ષમા કરજો જી, હમણાં ટેકનિકલ સમસ્યા છે. મહેરબાની કરીને થોડીકવાર પછી ફરી કોલ કરજો. આભાર.",
    "en": "Sorry ji, there's a technical problem right now. Please call again in a little while. Thank you.",
}

# Greeting/opener audio cache: (business_id, language, text) → filename.
# Keeps /twilio/answer from hitting Edge TTS on every
# single ring. Bounded so it can never grow without limit.
AUDIO_CACHE: Dict[tuple, str] = {}
MAX_AUDIO_CACHE = 64

# Edge TTS circuit breaker. On hosts where Edge TTS is blocked (cloud VMs get
# a 403 from speech.platform.bing.com), every turn wastes ~0.7s on a failing
# WebSocket before falling back to <Say>. Stacked across a call, that latency
# races Twilio's webhook budget and causes intermittent "Application Error"
# mid-call. After a few consecutive failures, stop trying Edge TTS and go
# straight to Twilio's own <Say> voice — fast and deterministic.
# On a host where Edge TTS works, the streak resets on every success and the
# circuit never opens, so behaviour there is unchanged.
_TTS_FAILURE_STREAK = 0
_TTS_CIRCUIT_THRESHOLD = 2
_TTS_CIRCUIT_OPEN = False


def _record_tts_success() -> None:
    global _TTS_FAILURE_STREAK
    _TTS_FAILURE_STREAK = 0


def _record_tts_failure() -> None:
    global _TTS_FAILURE_STREAK, _TTS_CIRCUIT_OPEN
    _TTS_FAILURE_STREAK += 1
    if _TTS_FAILURE_STREAK >= _TTS_CIRCUIT_THRESHOLD and not _TTS_CIRCUIT_OPEN:
        _TTS_CIRCUIT_OPEN = True
        logger.warning(
            "Edge TTS failed %d times in a row — opening the circuit and using "
            "the provider's built-in voice for the rest of this run.",
            _TTS_FAILURE_STREAK,
        )


def _synth(text: str, language: str, business_id: str = None, cache: bool = False) -> Any:
    """synthesize_speech, optionally memoised per business+language+text.

    Lives in main.py (not voice_service) so tests that monkeypatch
    main.synthesize_speech still intercept it. Cache hits also check the
    file is still on disk — a purged audio_files/ must not serve 404s.
    """
    async def _run() -> str:
        key = (business_id, language, text)
        if cache and business_id:
            hit = AUDIO_CACHE.get(key)
            if hit and os.path.exists(os.path.join(AUDIO_DIR, hit)):
                logger.debug(f"Audio cache hit for {business_id}")
                return hit
        filename = await synthesize_speech(text, language)
        if cache and business_id:
            if len(AUDIO_CACHE) >= MAX_AUDIO_CACHE:
                AUDIO_CACHE.pop(next(iter(AUDIO_CACHE)))  # FIFO, oldest first
            AUDIO_CACHE[key] = filename
        return filename
    return _run()


def _twiml_say_fragment(text: str, language: str) -> str:
    lang = LANG_ATTR.get(language, "en-US")
    return f'<Say language="{lang}">{html.escape(text)}</Say>'


def _twiml_apology_response(language: str) -> Response:
    body = "<Response>" + _twiml_say_fragment(APOLOGY_TEXTS.get(language, APOLOGY_TEXTS["en"]), language) + "</Response>"
    return Response(content=body, media_type="application/xml")


async def _twiml_speech(text: str, language: str, business_id: str = None, cache: bool = False) -> str:
    """TwiML <Play>…</Play>, falling back to <Say> with the SAME text."""
    if not _TTS_CIRCUIT_OPEN:
        try:
            filename = await _synth(text, language, business_id, cache)
            _record_tts_success()
            return f"<Play>{_public()}/audio/{filename}</Play>"
        except Exception:
            _record_tts_failure()
            logger.exception("TTS failed — falling back to <Say> so the caller still hears us")
    return _twiml_say_fragment(text, language)


def _language_for(business_id: Any = None, outbound_id: Any = None) -> str:
    """Language of the live call, for the apology voice. Never raises."""
    db = None
    try:
        biz_id = business_id
        if not biz_id and outbound_id:
            db = SessionLocal()
            oc = db.query(OutboundCall).filter(OutboundCall.id == outbound_id).first()
            biz_id = oc.business_id if oc else None
        if biz_id:
            db = db or SessionLocal()
            biz = get_business_record(db, str(biz_id))
            if biz and biz.language in LANG_ATTR:
                return biz.language
    except Exception:
        logger.warning("Could not resolve call language for the apology response", exc_info=True)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    return "en"


def twilio_webhook_guard(name: str):
    """Turn any unexpected exception in a webhook into a spoken apology the
    caller can actually hear — 200 + valid TwiML, never a 5xx.

    HTTPException is re-raised on purpose: the 403 from
    verify_twilio_request is the signature check, and swallowing it would
    hand anyone on the internet a 200 for a forged request.
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                raise
            except Exception:
                logger.exception(f"twilio webhook '{name}' raised — returning an apology instead of a 5xx")
                lang = _language_for(kwargs.get("business_id"), kwargs.get("outbound_id"))
                return _twiml_apology_response(lang)
        return wrapper
    return decorator


# ── WIZARD KNOWLEDGE-BASE ASSEMBLY ──────────────────────────
# The owner never sees "knowledge base" jargon: they answer friendly
# questions in the wizard, and we assemble the document the AI reads.

CATEGORY_LABELS = {
    "store": "Retail store",
    "showroom": "Car / vehicle showroom",
    "finance": "Finance / loans firm",
    "agency": "Service agency",
    "clinic": "Clinic / healthcare",
    "realestate": "Real estate",
    "other": "Business",
}


def assemble_knowledge_base(
    name: str,
    language: str,
    category: str = "",
    city: str = "",
    hours: str = "",
    products: str = "",
    extra: str = "",
    doc_text: str = "",
) -> str:
    """Build one clean knowledge document from wizard answers + uploaded file"""
    sections = []

    cat = CATEGORY_LABELS.get(category, category or "Business")
    sections.append(f"BUSINESS: {name} ({cat})")

    if city:
        sections.append(f"LOCATION: {city}")
    if hours:
        sections.append(f"OPENING HOURS: {hours}")
    if products:
        sections.append("PRODUCTS / SERVICES & PRICES:\n" + products.strip())
    if extra:
        sections.append("MORE DETAILS:\n" + extra.strip())
    if doc_text:
        sections.append("FROM THE OWNER'S DOCUMENT:\n" + doc_text.strip())

    sections.append(
        "CUSTOMER SERVICE POLICY: Be warm and helpful. If something is not "
        "listed here, take the caller's name and mobile number and tell them "
        "the team will call back with the details."
    )
    return "\n\n".join(sections)


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/api/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "running",
        "version": "1.1.0",
        "timestamp": datetime.now().isoformat()
    }

# ----------------------------------------------------------------------------
# AUTH
# ----------------------------------------------------------------------------

@app.post("/api/auth/signup", tags=["Auth"])
async def signup(request: Request, db: Session = Depends(get_db)):
    """Create a self-serve account: {"email", "name", "password"}"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be JSON")
    
    email = (body.get("email") or "").strip().lower()
    name = (body.get("name") or "").strip()
    password = body.get("password") or ""
    
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required")
    if not name:
        raise HTTPException(400, "Name is required")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "An account with this email already exists")
    
    user = User(
        id=str(uuid.uuid4())[:8],
        email=email,
        name=name,
        password_hash=hash_password(password),
        created_at=datetime.now(),
    )
    db.add(user)
    db.commit()
    
    logger.info(f"New account: {email}")
    return {
        "success": True,
        "token": create_token(user.id),
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "message": "Account created! Your agent console is ready."
    }

@app.post("/api/auth/login", tags=["Auth"])
async def login(request: Request, db: Session = Depends(get_db)):
    """Log in: {"email", "password"} → bearer token"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be JSON")
    
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    
    return {
        "success": True,
        "token": create_token(user.id),
        "user": {"id": user.id, "email": user.email, "name": user.name},
    }

@app.get("/api/auth/me", tags=["Auth"])
async def me(user: User = Depends(get_current_user)):
    """Who am I? (validates the token)"""
    return {"id": user.id, "email": user.email, "name": user.name}

# ----------------------------------------------------------------------------
# BUSINESS (all require a bearer token; owners see only their own agents)
# ----------------------------------------------------------------------------

@app.post("/api/business", tags=["Business"])
async def create_business(
    request: Request,
    name: str = Form(None),
    language: str = Form(...),
    category: str = Form(""),
    city: str = Form(""),
    hours: str = Form(""),
    products: str = Form(""),
    extra: str = Form(""),
    file: UploadFile = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a voice agent from wizard answers (+ optional document), owned by the caller"""
    logger.info(f"Creating business: {name} (language: {language}) for {user.email}")
    
    # Validation
    if language not in ("hi", "gu", "en"):
        logger.error(f"Invalid language: {language}")
        raise HTTPException(400, "Language must be 'hi', 'gu', or 'en'")
    
    if not name or len(name.strip()) == 0:
        logger.error("No business name provided")
        raise HTTPException(400, "Business name cannot be empty")
    
    try:
        # Document is now OPTIONAL — wizard answers alone are enough
        doc_text = ""
        if file and file.filename:
            logger.info(f"Extracting text from: {file.filename}")
            file_bytes = await file.read()
            raw_text = extract_text(file.filename, file_bytes)
            if raw_text and raw_text.strip():
                doc_text = clean_and_chunk(raw_text)
        
        # Assemble the knowledge base from wizard answers + document
        knowledge_base = assemble_knowledge_base(
            name=name,
            language=language,
            category=category,
            city=city,
            hours=hours,
            products=products,
            extra=extra,
            doc_text=doc_text,
        )
        logger.info(f"Knowledge base created: {len(knowledge_base)} characters")
        
        # Create business in database (owned by this user)
        business_id = str(uuid.uuid4())[:8]
        create_business_record(db, business_id, user.id, name, language, knowledge_base)
        
        webhook_url = f"{_public()}/twilio/answer/{business_id}"
        logger.info(f"Business created: {business_id} - {name}")
        
        return {
            "success": True,
            "business_id": business_id,
            "name": name,
            "language": language,
            "voice_webhook_url": webhook_url,
            "knowledge_preview": knowledge_base[:200] + "...",
            "message": "Your voice agent is ready! It will answer calls for your business."
        }
        
    except ValueError as e:
        logger.error(f"Document processing error: {str(e)}")
        raise HTTPException(400, f"Document error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(500, "An unexpected error occurred")

@app.get("/api/business/{business_id}", tags=["Business"])
async def get_business(
    business_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get business details (only if you own it)"""
    business = get_business_record(db, business_id)
    if not business:
        raise HTTPException(404, "Business not found")
    if business.owner_id != user.id:
        raise HTTPException(403, "This agent belongs to another account")
    
    return {
        "id": business.id,
        "name": business.name,
        "language": business.language,
        "created_at": business.created_at.isoformat(),
        "total_calls": business.total_calls,
        "knowledge_base_size": len(business.knowledge_base),
    }

@app.get("/api/businesses", tags=["Business"])
async def list_businesses(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List YOUR agents"""
    businesses = get_businesses_by_owner(db, user.id)
    return {
        "total": len(businesses),
        "businesses": [
            {
                "id": b.id,
                "name": b.name,
                "language": b.language,
                "created_at": b.created_at.isoformat(),
                "total_calls": b.total_calls,
            }
            for b in businesses
        ]
    }

@app.get("/api/analytics/overview", tags=["Analytics"])
async def get_analytics(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Analytics scoped to your agents"""
    return get_owner_analytics(db, user.id)

# ----------------------------------------------------------------------------
# CALL INSIGHTS (the owner's CRM — plain language, zero jargon)
# ----------------------------------------------------------------------------

@app.get("/api/leads", tags=["Insights"])
async def list_leads(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """People who called: numbers, names, what they want, booked visits"""
    leads = get_leads_for_owner(db, user.id)
    return {
        "total": len(leads),
        "leads": [
            {
                "id": l.id,
                "business_id": l.business_id,
                "caller_name": l.caller_name,
                "caller_number": l.caller_number,
                "caller_email": l.caller_email,
                "interest": l.interest,
                "product": l.product,
                "intent": l.intent,
                "visit_type": l.visit_type,
                "scheduled_for": l.scheduled_for,
                "visit_at": l.visit_at,
                "needs_callback": bool(l.needs_callback),
                "callback_reason": l.callback_reason,
                "requested_material": l.requested_material,
                "notes": l.notes,
                "status": l.status,
                "created_at": l.created_at.isoformat(),
            }
            for l in leads
        ]
    }


@app.post("/api/leads/reextract", tags=["Insights"])
async def reextract_lead_details(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-run extraction over stored transcripts to fill in detail columns.

    Leads captured before these fields existed have them empty. This walks the
    owner's calls, re-extracts, and only fills blanks — existing answers and
    any status the owner already set are left alone. Costs one LLM call per call.
    """
    leads = get_leads_for_owner(db, user.id)
    filled = 0
    scanned = 0
    for lead in leads:
        if not lead.call_id:
            continue
        rows = (
            db.query(Conversation)
            .filter(Conversation.call_id == lead.call_id)
            .order_by(Conversation.timestamp)
            .all()
        )
        if not rows:
            continue
        scanned += 1
        # each stored row is one exchange: caller said X, Meera replied Y
        history = []
        for r in rows:
            history.append({"role": "user", "content": r.user_message})
            history.append({"role": "assistant", "content": r.agent_response})
        try:
            info = extract_lead_info(history)
        except Exception as e:
            logger.warning(f"Re-extraction failed for lead {lead.id}: {e}")
            continue
        before = {f: getattr(lead, f, None) for f in DETAIL_FIELDS}
        apply_lead_details(lead, info)
        if any(getattr(lead, f, None) != before[f] for f in DETAIL_FIELDS):
            filled += 1
    db.commit()
    return {
        "leads_scanned": scanned,
        "leads_updated": filled,
        "message": f"Filled details on {filled} of {scanned} callers.",
    }


@app.patch("/api/leads/{lead_id}", tags=["Insights"])
async def update_lead_status(
    lead_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Mark a lead: new → contacted → converted / closed"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be JSON")
    status = body.get("status") or ""
    if status not in ("new", "contacted", "converted", "closed"):
        raise HTTPException(400, "Status must be new, contacted, converted or closed")
    
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead not found")
    biz = get_business_record(db, lead.business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(403, "Not your lead")
    
    lead.status = status
    db.commit()
    return {"success": True, "id": lead.id, "status": lead.status}


@app.get("/api/calls", tags=["Insights"])
async def list_calls(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Recent calls with full transcripts, for YOUR agents only"""
    return {"calls": get_call_transcripts_for_owner(db, user.id)}

# ----------------------------------------------------------------------------
# OUTBOUND CALLS (call one customer or a whole Excel list)
# ----------------------------------------------------------------------------

DIAL_GAP_SECONDS = 6   # breathing room between calls in a campaign
DIALER_STOP = {}       # campaign_id -> True when user cancels


# Provider reachability probe for GET /api/phone/status (30s memo, because
# the dashboard polls and each probe is a real HTTPS round-trip).
_PHONE_STATUS_CACHE = {"checked_at": 0.0, "payload": None}
PHONE_STATUS_TTL = 30.0


def _head_then_get(url: str, timeout: float = 5, **kwargs):
    """HEAD first (the task calls for a HEAD check); some APIs answer 405
    to HEAD, so fall back to GET before declaring the endpoint dead."""
    r = requests.head(url, timeout=timeout, allow_redirects=True, **kwargs)
    if r.status_code in (405, 501):
        r = requests.get(url, timeout=timeout, allow_redirects=True, **kwargs)
    return r


def _probe_twilio() -> dict:
    """Ask Twilio's API whether our account actually answers."""
    now = time.monotonic()
    cached = _PHONE_STATUS_CACHE["payload"]
    if cached and now - _PHONE_STATUS_CACHE["checked_at"] < PHONE_STATUS_TTL:
        return dict(cached)

    connected = reachable = False
    detail = "no provider configured"
    try:
        sid, token = os.getenv("TWILIO_ACCOUNT_SID", ""), os.getenv("TWILIO_AUTH_TOKEN", "")
        if not (sid.startswith("AC") and token):
            detail = "Twilio credentials missing"
        else:
            r = _head_then_get(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                auth=(sid, token),
            )
            reachable = True
            connected = r.status_code == 200
            detail = "Twilio account verified" if connected else f"Twilio answered HTTP {r.status_code}"
    except Exception as exc:
        reachable = connected = False
        detail = f"provider unreachable: {type(exc).__name__}: {exc}"

    payload = {
        "connected": connected,
        "provider": "twilio",
        "reachable": reachable,
        "detail": detail,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    _PHONE_STATUS_CACHE["checked_at"] = now
    _PHONE_STATUS_CACHE["payload"] = payload
    return dict(payload)


PUBLIC_URL_PROBE_ATTEMPTS = 3   # a flaky tunnel answers late, not never
PUBLIC_URL_PROBE_TIMEOUT = 5    # seconds per HTTP attempt
PUBLIC_URL_PROBE_PAUSE = 2      # seconds to wait before retrying


def _public_url_probe(base: str) -> int:
    """One reachability attempt. Returns the HTTP status, or raises."""
    r = _head_then_get(base, timeout=PUBLIC_URL_PROBE_TIMEOUT)
    if r.status_code >= 500:
        # e.g. localtunnel's '503 - Tunnel Unavailable' = URL is stale
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.status_code


def _check_public_url() -> None:
    """Startup self-check: is the URL we hand the provider still alive?

    Tunnel URLs change every restart while .env / public_url.txt keep the old
    one — the classic silent failure where every call 404s and the caller hears
    Twilio's 'application error'. Shout about it at boot instead.

    Retries matter: on a lossy connection (campus Wi-Fi, mobile hotspot) a
    single 5s attempt reports 'tunnel drift' for a tunnel that is perfectly
    alive — a false alarm at boot trains people to ignore the real one.
    """
    base = _public().rstrip("/")
    failures = []
    for attempt in range(1, PUBLIC_URL_PROBE_ATTEMPTS + 1):
        try:
            status = _public_url_probe(base)
            logger.info(
                f"✅ PUBLIC_BASE_URL reachable: {base} "
                f"(HTTP {status}, attempt {attempt}/{PUBLIC_URL_PROBE_ATTEMPTS})"
            )
            return
        except Exception as exc:
            failures.append(f"attempt {attempt}: {exc}")
            logger.info(f"PUBLIC_BASE_URL probe attempt {attempt} failed: {exc}")
            if attempt < PUBLIC_URL_PROBE_ATTEMPTS:
                time.sleep(PUBLIC_URL_PROBE_PAUSE)
    logger.warning(
        "⚠️⚠️⚠️ PUBLIC_BASE_URL is NOT reachable after %d attempts: %s (%s) — "
        "tunnel drift? The URL in .env / backend/public_url.txt no longer "
        "matches the live tunnel, so every incoming and outgoing call will "
        "fail until it is fixed. Run `python backend/start_tunnel.py` and "
        "paste the printed URL into .env (or backend/public_url.txt).",
        PUBLIC_URL_PROBE_ATTEMPTS, base, "; ".join(failures),
    )


async def _probe_public_url_after_boot(delay: float = 2.0) -> None:
    """Run the reachability probe once the server is actually accepting
    connections. During the startup event the socket isn't live yet, so a
    localhost PUBLIC_BASE_URL would always fail there.
    """
    await asyncio.sleep(delay)
    await asyncio.to_thread(_check_public_url)


CALL_WINDOW_SECONDS = 900  # a call older than this cannot still be live


def _close_orphaned_calls(db: Session, rows: list) -> int:
    """Finalise rows stuck in 'calling'.

    A status callback normally closes a call, but one can be lost (tunnel died,
    provider never called back). Without this the console shows a dead call from
    last week as still ringing, and any 'is this call live?' check is a lie.
    A call older than the window cannot still be up, so close it.
    """
    cutoff = datetime.now() - timedelta(seconds=CALL_WINDOW_SECONDS)
    stale = [c for c in rows if c.status == "calling" and c.called_at and c.called_at < cutoff]
    for c in stale:
        c.status = "done"
        c.detail = "Call ended (no status update received)"
    if stale:
        db.commit()
    return len(stale)


def _outbound_dict(oc: OutboundCall) -> dict:
    return {
        "id": oc.id,
        "name": oc.caller_name,
        "number": oc.caller_number,
        "info": oc.info,
        "status": oc.status,
        "detail": oc.detail,
        "called_at": oc.called_at.isoformat() if oc.called_at else None,
    }


@app.get("/api/phone/status", tags=["Outbound"])
async def phone_status(user: User = Depends(get_current_user)):
    """Is phone calling connected? A REAL probe of the provider API —
    'credentials exist in .env' used to be reported as 'connected', so a
    typo or a banned key still showed a green light in the UI."""
    return await asyncio.to_thread(_probe_twilio)


@app.post("/api/call-now/{business_id}", tags=["Outbound"])
async def call_now(
    business_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Call one customer right now: {"number", "name", "info"}"""
    biz = get_business_record(db, business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(404, "Agent not found")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be JSON")
    number = (body.get("number") or "").strip()
    digits = number.replace(" ", "").replace("-", "").replace("+", "")
    if not digits.isdigit() or len(digits) < 10:
        raise HTTPException(400, "That doesn't look like a valid phone number")
    
    oc = create_single_outbound(db, business_id, number, (body.get("name") or "").strip() or None, (body.get("info") or "").strip() or None)
    result = place_call(
        number,
        f"{_public()}/twilio/answer/{business_id}",
        status_callback=f"{_public()}/twilio/status/{business_id}",
    )
    mark_outbound(
        oc,
        "calling" if result["ok"] else "failed",
        detail=result["detail"],
        call_uuid=result.get("call_uuid"),
    )
    db.commit()
    return {"success": result["ok"], "detail": result["detail"], "call": _outbound_dict(oc)}


class _Dialer:
    """Calls a campaign's numbers one by one in a background thread"""

    @staticmethod
    def run(campaign_id: str, business_id: str):
        db = SessionLocal()
        try:
            campaign, calls = get_campaign_with_calls(db, campaign_id)
            if not campaign:
                return
            campaign.status = "running"
            db.commit()
            answer_url = f"{_public()}/twilio/answer/{business_id}"
            status_cb = f"{_public()}/twilio/status/{business_id}"
            biz = get_business_record(db, business_id)
            
            # Simulator mode: when telephony isn't fully wired (no rented number),
            # run the same campaign with a simulated customer so the owner still
            # sees the whole flow — opener, replies, transcripts, leads. Clearly labeled.
            sim_mode = not twilio_configured()
            if sim_mode:
                logger.info(f"Campaign {campaign_id}: running in SIMULATOR mode (no Twilio number)")
            
            for oc in calls:
                if DIALER_STOP.get(campaign_id):
                    break
                if oc.status == "done":
                    continue
                
                if sim_mode:
                    # ── SIMULATED CALL: real Meera brain, fake customer ──
                    mark_outbound(oc, "calling", detail="Calling (simulated)…")
                    db.commit()
                    call_id = f"sim-{oc.id}"
                    start_call(db, oc.business_id, call_id)
                    opener = generate_outbound_opener(
                        business_name=biz.name,
                        knowledge_base=biz.knowledge_base,
                        language_code=biz.language,
                        customer_name=oc.caller_name,
                        customer_info=oc.info,
                    )
                    history = [{"role": "assistant", "content": opener}]
                    try:
                        save_conversation(db, str(uuid.uuid4())[:12], call_id, "(simulated outbound call)", opener)
                    except Exception:
                        pass
                    # A short simulated exchange: customer shows interest, Meera responds
                    sim_replies = [
                        f"Haan ji, tell me — what is the offer?",
                        f"Sounds good, my number is 98{secrets.token_hex(4).isdigit() and ''.join(secrets.choice('0123456789') for _ in range(8))}.",
                    ]
                    for reply in sim_replies:
                        time.sleep(1.5)
                        response_text = get_agent_response(
                            business_name=biz.name,
                            knowledge_base=biz.knowledge_base,
                            language_code=biz.language,
                            customer_query=reply,
                            conversation_history=history,
                        )
                        try:
                            save_conversation(db, str(uuid.uuid4())[:12], call_id, reply, response_text)
                        except Exception:
                            pass
                    # extract the lead from the simulated exchange
                    try:
                        info = extract_lead_info(history)
                        create_lead_record(db, oc.business_id, call_id, info)
                    except Exception:
                        pass
                    complete_call(db, call_id)
                    mark_outbound(oc, "done", detail="Simulated call completed — see Call Insights")
                    db.commit()
                    time.sleep(1)
                    continue
                
                mark_outbound(oc, "calling", detail="Dialing…")
                db.commit()
                result = place_call(oc.caller_number, answer_url, status_callback=status_cb)
                mark_outbound(
                    oc,
                    "calling" if result["ok"] else "failed",
                    detail=result["detail"],
                    call_uuid=result.get("call_uuid"),
                )
                db.commit()
                if not result["ok"]:
                    # Stop the whole campaign on auth/credit problems
                    if "credentials" in result["detail"].lower() or "credit" in result["detail"].lower() or "isn't connected" in result["detail"].lower():
                        for remaining in calls:
                            if remaining.status == "pending":
                                mark_outbound(remaining, "failed", detail="Stopped — " + result["detail"])
                        db.commit()
                        break
                else:
                    time.sleep(DIAL_GAP_SECONDS)
            
            fresh = db.query(Campaign).filter(Campaign.id == campaign_id).first()
            if fresh and not DIALER_STOP.get(campaign_id):
                fresh.status = "completed"
                db.commit()
            DIALER_STOP.pop(campaign_id, None)
            logger.info(f"Campaign {campaign_id} finished")
        finally:
            db.close()


import threading
import time
import secrets


@app.post("/api/campaigns/{business_id}", tags=["Outbound"])
async def upload_campaign(
    business_id: str,
    file: UploadFile = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload an Excel/CSV list of customers to call"""
    biz = get_business_record(db, business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(404, "Agent not found")
    if not file or not file.filename:
        raise HTTPException(400, "Please upload your customer list file")
    
    content = await file.read()
    filename = file.filename.lower()
    try:
        rows = []
        if filename.endswith(".csv"):
            import io, csv
            text = content.decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            rows = [dict(r) for r in reader]
        elif filename.endswith((".xlsx", ".xls")):
            import openpyxl, io
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
            ws = wb.active
            headers = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
            for row_cells in ws.iter_rows(min_row=2, values_only=True):
                if all(v is None for v in row_cells):
                    continue
                rows.append({headers[i]: row_cells[i] for i in range(min(len(headers), len(row_cells)))})
        else:
            raise HTTPException(400, "Please upload an Excel (.xlsx) or CSV file")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File parse failed: {e}")
        raise HTTPException(400, "Couldn't read that file. Please use the sample format (columns: name, mobile, info).")
    
    clean, problems = parse_call_list(rows)
    if not clean:
        detail = "; ".join(problems[:5]) or "No usable phone numbers found"
        raise HTTPException(400, f"No valid numbers found in the file. {detail}")
    
    campaign = create_campaign(db, business_id, file.filename, clean)
    return {
        "success": True,
        "campaign_id": campaign.id,
        "total": campaign.total,
        "problems": problems[:10],
        "calls": [_outbound_dict(oc) for oc in get_campaign_with_calls(db, campaign.id)[1]],
    }


@app.post("/api/campaigns/{campaign_id}/start", tags=["Outbound"])
async def start_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Start calling everyone on the list, one by one"""
    campaign, calls = get_campaign_with_calls(db, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    biz = get_business_record(db, campaign.business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(403, "Not your campaign")
    if campaign.status == "running":
        return {"success": True, "detail": "Already calling"}
    
    DIALER_STOP.pop(campaign_id, None)
    thread = threading.Thread(target=_Dialer.run, args=(campaign_id, campaign.business_id), daemon=True)
    thread.start()
    return {"success": True, "detail": f"Calling {campaign.total} customer(s)…", "total": campaign.total}


@app.post("/api/campaigns/{campaign_id}/stop", tags=["Outbound"])
async def stop_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    campaign, _ = get_campaign_with_calls(db, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    biz = get_business_record(db, campaign.business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(403, "Not your campaign")
    DIALER_STOP[campaign_id] = True
    return {"success": True, "detail": "Stopping after the current call"}


@app.get("/api/campaigns/{campaign_id}", tags=["Outbound"])
async def campaign_detail(
    campaign_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Live progress of a campaign (poll this from the dashboard)"""
    campaign, calls = get_campaign_with_calls(db, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    biz = get_business_record(db, campaign.business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(403, "Not your campaign")
    return {
        "id": campaign.id,
        "status": campaign.status,
        "total": campaign.total,
        "done": sum(1 for c in calls if c.status in ("calling", "done", "failed")),
        "success": sum(1 for c in calls if c.status in ("calling", "done")),
        "failed": sum(1 for c in calls if c.status == "failed"),
        "calls": [_outbound_dict(c) for c in calls],
    }


@app.get("/api/outbound", tags=["Outbound"])
async def list_outbound(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """All outbound calls for your agents, newest first"""
    rows = get_outbound_for_owner(db, user.id)
    _close_orphaned_calls(db, rows)
    return {"total": len(rows), "calls": [_outbound_dict(c) for c in rows]}


@app.post("/api/consent/{business_id}", tags=["Compliance"])
async def set_consent_record(
    business_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Record customer opt-in/opt-out consent for TRAI compliance"""
    biz = get_business_record(db, business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(404, "Agent not found")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be JSON")
    phone = (body.get("phone") or body.get("phone_number") or "").strip()
    if not phone:
        raise HTTPException(400, "phone is required")
    opted_in = bool(body.get("opted_in", True))
    source = (body.get("source") or "manual").strip()
    
    from compliance import record_consent
    c = record_consent(db, business_id, phone, opted_in=opted_in, source=source)
    return {
        "status": "ok",
        "consent": {
            "id": c.id,
            "business_id": c.business_id,
            "phone_number": c.phone_number,
            "opted_in": c.opted_in,
            "source": c.source,
            "opted_in_date": c.opted_in_date.isoformat() if c.opted_in_date else None,
        }
    }


@app.get("/api/consent/{business_id}", tags=["Compliance"])
async def get_consent_records(
    business_id: str,
    phone: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Query TRAI consent status for an agent's customer list"""
    biz = get_business_record(db, business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(404, "Agent not found")
    from compliance import compliance_checker, Consent
    if phone:
        allowed, reason = compliance_checker.can_call_number(phone, business_id=business_id, db=db, ignore_hours=True)
        return {"phone": phone, "allowed_to_call": allowed, "reason": reason}
    records = db.query(Consent).filter(Consent.business_id == business_id).all()
    return {
        "total": len(records),
        "consents": [
            {
                "id": c.id,
                "phone_number": c.phone_number,
                "opted_in": c.opted_in,
                "opted_in_date": c.opted_in_date.isoformat() if c.opted_in_date else None,
                "source": c.source,
            }
            for c in records
        ],
    }


# ----------------------------------------------------------------------------
# TRY YOUR AGENT (talk to Meera right in the browser — free, no phone needed)
# ----------------------------------------------------------------------------

def _try_session(business_id: str) -> dict:
    key = f"try-{business_id}"
    if key not in CALL_SESSIONS and len(CALL_SESSIONS) >= MAX_CALL_SESSIONS:
        CALL_SESSIONS.pop(next(iter(CALL_SESSIONS)))
    return CALL_SESSIONS.setdefault(key, {"history": [], "silences": 0})


@app.post("/api/try/{business_id}/listen", tags=["Try"])
async def try_listen(
    business_id: str,
    file: UploadFile = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Owner speaks into the browser mic → Whisper transcribes → Meera answers with audio"""
    biz = get_business_record(db, business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(404, "Agent not found")
    if not file or not file.filename:
        raise HTTPException(400, "No audio received")
    
    audio_bytes = await file.read()
    tmp_path = os.path.join(AUDIO_DIR, f"try_{uuid.uuid4().hex[:8]}.webm")
    with open(tmp_path, "wb") as f:
        f.write(audio_bytes)
    
    you_said = await asyncio.to_thread(transcribe_audio, tmp_path, biz.language)
    try:
        os.remove(tmp_path)
    except OSError:
        pass
    
    if not you_said:
        return {"you": "", "meera": "", "audio": None, "heard_nothing": True}
    
    session = _try_session(business_id)
    response_text = await asyncio.to_thread(
        get_agent_response,
        business_name=biz.name,
        knowledge_base=biz.knowledge_base,
        language_code=biz.language,
        customer_query=you_said,
        conversation_history=session["history"],
    )
    try:
        audio_filename = await synthesize_speech(response_text, biz.language)
        return {"you": you_said, "meera": response_text, "audio": f"/audio/{audio_filename}", "heard_nothing": False}
    except Exception:
        # No phone line to fall back to here — the browser just plays no audio.
        logger.exception("TTS failed on try/listen — returning the reply without audio")
        return {"you": you_said, "meera": response_text, "audio": None, "heard_nothing": False}


@app.post("/api/try/{business_id}/say", tags=["Try"])
async def try_say(
    business_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Type to Meera (fallback for noisy demo rooms) — same brain as phone calls"""
    biz = get_business_record(db, business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(404, "Agent not found")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body must be JSON")
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Say something first")
    
    session = _try_session(business_id)
    response_text = await asyncio.to_thread(
        get_agent_response,
        business_name=biz.name,
        knowledge_base=biz.knowledge_base,
        language_code=biz.language,
        customer_query=text,
        conversation_history=session["history"],
    )
    try:
        audio_filename = await synthesize_speech(response_text, biz.language)
        return {"you": text, "meera": response_text, "audio": f"/audio/{audio_filename}"}
    except Exception:
        logger.exception("TTS failed on try/say — returning the reply without audio")
        return {"you": text, "meera": response_text, "audio": None}


@app.post("/api/try/{business_id}/reset", tags=["Try"])
async def try_reset(
    business_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Start the conversation over"""
    biz = get_business_record(db, business_id)
    if not biz or biz.owner_id != user.id:
        raise HTTPException(404, "Agent not found")
    CALL_SESSIONS.pop(f"try-{business_id}", None)
    return {"success": True}

# ============================================================================
# TWILIO VOICE WEBHOOKS
# ============================================================================
# ============================================================================

TWILIO_LANG_ATTR = {"hi": "hi-IN", "gu": "gu-IN", "en": "en-US"}


def _twiml_gather(business_id: str, language: str, oc_id: str = None) -> str:
    action = f"{_public()}/twilio/event/{business_id}" + (f"?oc={oc_id}" if oc_id else "")
    lang = TWILIO_LANG_ATTR.get(language, "en-US")
    return (
        f'<Gather input="speech" language="{lang}" speechTimeout="auto" '
        f'action="{action}" method="POST" actionOnEmptyResult="true"/>'
    )


def _twiml_play(audio_filename: str) -> str:
    return f"<Play>{_public()}/audio/{audio_filename}</Play>"


@app.post("/twilio/answer/{business_id}", tags=["Voice"])
@twilio_webhook_guard("twilio-answer")
async def twilio_answer(business_id: str, request: Request, db: Session = Depends(get_db)):
    """Twilio bridges here when a customer dials our number"""
    try:
        form = dict(await request.form())
    except Exception:
        form = {}
    verify_twilio_request(request, form)

    business = get_business_record(db, business_id)
    if not business:
        return Response(content="<Response><Hangup/></Response>", media_type="application/xml")

    greetings = {
        "hi": f"नमस्ते जी, {business.name} में आपका स्वागत है। मैं मीरा बात कर रही हूँ, बताइए मैं आपकी क्या मदद कर सकती हूँ?",
        "gu": f"નમસ્તે જી, {business.name} માં આપનું સ્વાગત છે. હું મીરા બોલું છું, કહો જી, હું તમની શું મદદ કરી શકું?",
        "en": f"Hello ji, welcome to {business.name}! This is Meera speaking — how can I help you today?",
    }
    greeting_audio = await _twiml_speech(
        greetings.get(business.language, greetings["en"]), business.language, business_id, cache=True,
    )
    increment_call_count(db, business_id)
    xml = "<Response>" + greeting_audio + _twiml_gather(business_id, business.language) + "</Response>"
    return Response(content=xml, media_type="application/xml")


@app.post("/twilio/outbound-answer/{outbound_id}", tags=["Voice"])
@twilio_webhook_guard("twilio-outbound-answer")
async def twilio_outbound_answer(outbound_id: str, request: Request, db: Session = Depends(get_db)):
    """Twilio bridges here when WE call a customer — Meera opens personally"""
    from llm_service import generate_outbound_opener

    try:
        form = dict(await request.form())
    except Exception:
        form = {}
    verify_twilio_request(request, form)

    oc = db.query(OutboundCall).filter(OutboundCall.id == outbound_id).first()
    biz = get_business_record(db, oc.business_id) if oc else None
    if not oc or not biz:
        return Response(content="<Response><Hangup/></Response>", media_type="application/xml")

    opener = await asyncio.to_thread(
        generate_outbound_opener,
        business_name=biz.name,
        knowledge_base=biz.knowledge_base,
        language_code=biz.language,
        customer_name=oc.caller_name,
        customer_info=oc.info,
    )
    call_id = f"out-{oc.id}"
    start_call(db, biz.id, call_id)
    session_key = f"{biz.id}:oc-{oc.id}"
    if session_key not in CALL_SESSIONS and len(CALL_SESSIONS) >= MAX_CALL_SESSIONS:
        CALL_SESSIONS.pop(next(iter(CALL_SESSIONS)))
    CALL_SESSIONS[session_key] = {
        "history": [{"role": "assistant", "content": opener}],
        "silences": 0,
        "outbound_reason": oc.info or "",
    }

    opener_audio = await _twiml_speech(opener, biz.language, biz.id, cache=True)
    try:
        save_conversation(db, str(uuid.uuid4())[:12], call_id, "(outbound call — agent calling)", opener)
    except Exception as e:
        logger.warning(f"Outbound opener save failed: {e}")

    xml = "<Response>" + opener_audio + _twiml_gather(biz.id, biz.language, oc.id) + "</Response>"
    return Response(content=xml, media_type="application/xml")


@app.post("/twilio/event/{business_id}", tags=["Voice"])
@twilio_webhook_guard("twilio-event")
async def twilio_event(business_id: str, request: Request, db: Session = Depends(get_db)):
    """Twilio posts the caller's transcribed speech here"""
    business = get_business_record(db, business_id)
    if not business:
        return Response(content="<Response><Hangup/></Response>", media_type="application/xml")

    # Twilio sends form-encoded webhooks (accept JSON too, for tests)
    payload = {}
    try:
        payload = dict(await request.form())
    except Exception:
        payload = {}
    verify_twilio_request(request, payload)
    if not payload:
        try:
            payload = await request.json()
        except Exception:
            payload = {}

    customer_query = (payload.get("SpeechResult") or "").strip() if isinstance(payload, dict) else ""
    if not customer_query and isinstance(payload, dict):
        results = payload.get("speech_results") or []
        if results and isinstance(results, list):
            customer_query = (results[0] or {}).get("text", "") or ""
    customer_query = (customer_query or "").strip()

    call_uuid = (payload.get("CallSid") or payload.get("call_uuid") or payload.get("uuid") or "") if isinstance(payload, dict) else ""
    oc_id = request.query_params.get("oc")
    if oc_id:
        session_key = f"{business_id}:oc-{oc_id}"
        call_id = f"out-{oc_id}"
        session = CALL_SESSIONS.setdefault(session_key, {"history": [], "silences": 0})
    else:
        session_key = f"{business_id}:{call_uuid}"
        if session_key not in CALL_SESSIONS and len(CALL_SESSIONS) >= MAX_CALL_SESSIONS:
            CALL_SESSIONS.pop(next(iter(CALL_SESSIONS)))
        session = CALL_SESSIONS.setdefault(session_key, {"history": [], "silences": 0})
        call_id = start_call(db, business_id, call_uuid)

    def _reply(*parts: str) -> Response:
        return Response(content="<Response>" + "".join(parts) + "</Response>", media_type="application/xml")

    # Caller said nothing → reprompt; after 2 consecutive silences, wrap up warmly
    if not customer_query:
        session["silences"] = session.get("silences", 0) + 1
        if session["silences"] >= 2:
            if session["history"]:
                try:
                    info = await asyncio.to_thread(extract_lead_info, session["history"])
                    lead = create_lead_record(db, business_id, call_id, info)
                    if lead:
                        logger.info(f"Lead saved: {lead.caller_name} / {lead.caller_number} ({lead.intent})")
                except Exception as e:
                    logger.warning(f"Lead extraction failed: {e}")
            complete_call(db, call_id)
            CALL_SESSIONS.pop(session_key, None)
            farewell_audio = await _twiml_speech(
                FAREWELL_TEXTS.get(business.language, FAREWELL_TEXTS["en"]), business.language,
            )
            return _reply(farewell_audio, "<Hangup/>")

        reprompt_audio = await _twiml_speech(
            REPROMPT_TEXTS.get(business.language, REPROMPT_TEXTS["en"]), business.language,
        )
        return _reply(reprompt_audio, _twiml_gather(business_id, business.language, oc_id))

    session["silences"] = 0

    # Blocking Groq call in an async handler with a 15s webhook budget —
    # run it off the event loop so it can't stall every other call.
    response_text = await asyncio.to_thread(
        get_agent_response,
        business_name=business.name,
        knowledge_base=business.knowledge_base,
        language_code=business.language,
        customer_query=customer_query,
        conversation_history=session["history"],
        outbound_reason=session.get("outbound_reason"),
    )
    # The model occasionally hands back nothing. Synthesising an empty string
    # raises inside TTS, which 500s this webhook — the caller would hear dead air
    # for the rest of the call. Speak the same reprompt we use after a pause.
    if not (response_text or "").strip():
        logger.warning(f"Empty agent reply on call {call_id} — speaking the reprompt instead")
        response_text = REPROMPT_TEXTS.get(business.language, REPROMPT_TEXTS["en"])
    try:
        save_conversation(db, str(uuid.uuid4())[:12], call_id, customer_query, response_text)
    except Exception as e:
        logger.warning(f"Conversation save failed: {e}")

    audio_filename = await _twiml_speech(response_text, business.language)
    return _reply(audio_filename, _twiml_gather(business_id, business.language, oc_id))


@app.post("/twilio/status/{business_id}", tags=["Voice"])
@twilio_webhook_guard("twilio-status")
async def twilio_status(business_id: str, request: Request, db: Session = Depends(get_db)):
    """Twilio posts the final call status here — save the lead even when
    the caller hangs up before the farewell (2-silence wrap-up never ran)"""
    try:
        payload = dict(await request.form())
    except Exception:
        payload = {}
    verify_twilio_request(request, payload)
    call_status = (payload.get("CallStatus") or "").strip().lower()
    call_sid = (payload.get("CallSid") or "").strip()
    logger.info(f"Twilio call status: {call_status} (sid {call_sid[:16]}…)")

    session_key = f"{business_id}:{call_sid}"
    session = CALL_SESSIONS.get(session_key)
    if call_status == "completed" and session and session.get("history"):
        try:
            info = await asyncio.to_thread(extract_lead_info, session["history"])
            lead = create_lead_record(db, business_id, call_sid, info)
            if lead:
                logger.info(f"Lead saved on hangup: {lead.caller_name} / {lead.caller_number} ({lead.intent})")
        except Exception as e:
            logger.warning(f"Lead extraction on hangup failed: {e}")
        complete_call(db, call_sid)
    CALL_SESSIONS.pop(session_key, None)
    return Response(content="<Response/>", media_type="application/xml")


# ============================================================================
# FRONTEND (must be registered AFTER all routes above)
# ============================================================================

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

# ============================================================================
# STARTUP
# ============================================================================

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 AI Voice Agent Backend Starting...")
    init_schema()               # a cloud container has no tables unless we make them
    migrate_missing_columns()   # before any query touches leads
    logger.info(f"Public URL: {_public()}")
    # Probe AFTER boot, not during it. Uvicorn runs the startup event before
    # its socket starts accepting connections, so a localhost PUBLIC_BASE_URL
    # can never answer here — probing inline flags a perfectly healthy local
    # run as "tunnel drift". A dead tunnel is a warning to read over coffee,
    # not a reason to refuse to start.
    asyncio.create_task(_probe_public_url_after_boot())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
