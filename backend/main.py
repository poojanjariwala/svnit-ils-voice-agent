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
#   POST /voice/answer/{business_id}    → Vonage webhook (incoming call)
#   POST /voice/event/{business_id}     → Vonage webhook (speech result)
#
# Integrates with:
#   - database.py   (Hardik)
#   - doc_processor.py, llm_service.py, voice_service.py (Henali)
#
# TODO: Add full FastAPI application here
# ─────────────────────────────────────────────
import os
import json
import uuid
import logging
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, Form, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from vonage import Auth, Vonage
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
from database import create_campaign, create_single_outbound, get_campaign_with_calls, get_outbound_for_owner, mark_outbound
from auth import get_current_user, hash_password, verify_password, create_token
from models import User, Lead, Campaign, OutboundCall
import phone_service
from phone_service import place_call, vonage_configured, twilio_configured, active_provider, parse_call_list
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

# VONAGE CONFIG
VONAGE_API_KEY = os.getenv("VONAGE_API_KEY", "")
VONAGE_API_SECRET = os.getenv("VONAGE_API_SECRET", "")
VONAGE_PHONE_NUMBER = os.getenv("VONAGE_PHONE_NUMBER", "")
def _public() -> str:
    """
    Public URL used in NCCO audio links and webhook URLs.
    Precedence: runtime override file (written by make_call.py so a new
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

if VONAGE_API_KEY and VONAGE_API_SECRET:
    vonage_client = Vonage(Auth(api_key=VONAGE_API_KEY, api_secret=VONAGE_API_SECRET))
    voice_client = vonage_client.voice
    logger.info("✅ Vonage client initialized")
else:
    logger.warning("⚠️ Vonage credentials not configured")

# IN-MEMORY STORAGE FOR CALL SESSIONS
# Key: f"{business_id}:{call_uuid}" → {"history": [...], "silences": int}
CALL_SESSIONS = {}
MAX_CALL_SESSIONS = 500  # prevent unbounded growth

# Vonage NCCO language codes per business language
VONAGE_LANG_MAP = {
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


def _speech_input_action(business_id: str, lang_code: str) -> dict:
    """Build the NCCO speech-input action that routes back to /voice/event"""
    return {
        "action": "input",
        "type": ["speech"],
        "speech": {
            "language": VONAGE_LANG_MAP.get(lang_code, "en-US"),
            "endOnSilence": 1.5,
        },
        "eventUrl": [f"{_public()}/voice/event/{business_id}"],
        "eventMethod": "POST",
    }

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
        
        webhook_url = f"{_public()}/voice/answer/{business_id}"
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
                "interest": l.interest,
                "intent": l.intent,
                "scheduled_for": l.scheduled_for,
                "notes": l.notes,
                "status": l.status,
                "created_at": l.created_at.isoformat(),
            }
            for l in leads
        ]
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


def _answer_url_for(business_id: str, provider: str = None) -> str:
    """Voice answer URL for the active telephony provider (TwiML or NCCO)."""
    prefix = "twilio" if (provider or active_provider()) == "twilio" else "voice"
    return f"{_public()}/{prefix}/answer/{business_id}"


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
    """Is phone calling connected? (plain-language answer for the UI)"""
    return {"connected": vonage_configured() or twilio_configured(), "provider": active_provider()}


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
    provider = active_provider()
    cb = f"{_public()}/twilio/status/{business_id}" if provider == "twilio" else None
    result = place_call(number, _answer_url_for(business_id, provider), status_callback=cb)
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
            answer_url = _answer_url_for(business_id)
            status_cb = f"{_public()}/twilio/status/{business_id}" if active_provider() == "twilio" else None
            
            # Simulator mode: when telephony isn't fully wired (no rented number),
            # run the same campaign with a simulated customer so the owner still
            # sees the whole flow — opener, replies, transcripts, leads. Clearly labeled.
            sim_mode = not (twilio_configured() or vonage_configured())
            if sim_mode:
                logger.info(f"Campaign {campaign_id}: running in SIMULATOR mode (no Vonage number)")
            
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
    return {"total": len(rows), "calls": [_outbound_dict(c) for c in rows]}

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
    
    you_said = transcribe_audio(tmp_path, biz.language)
    try:
        os.remove(tmp_path)
    except OSError:
        pass
    
    if not you_said:
        return {"you": "", "meera": "", "audio": None, "heard_nothing": True}
    
    session = _try_session(business_id)
    response_text = get_agent_response(
        business_name=biz.name,
        knowledge_base=biz.knowledge_base,
        language_code=biz.language,
        customer_query=you_said,
        conversation_history=session["history"],
    )
    audio_filename = await synthesize_speech(response_text, biz.language)
    return {"you": you_said, "meera": response_text, "audio": f"/audio/{audio_filename}", "heard_nothing": False}


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
    response_text = get_agent_response(
        business_name=biz.name,
        knowledge_base=biz.knowledge_base,
        language_code=biz.language,
        customer_query=text,
        conversation_history=session["history"],
    )
    audio_filename = await synthesize_speech(response_text, biz.language)
    return {"you": text, "meera": response_text, "audio": f"/audio/{audio_filename}"}


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
# VONAGE VOICE WEBHOOKS
# ============================================================================

@app.post("/voice/answer/{business_id}", tags=["Voice"])
async def voice_answer(business_id: str, db: Session = Depends(get_db)):
    """Vonage calls this when customer calls"""
    logger.info(f"Voice answer for: {business_id}")
    
    business = get_business_record(db, business_id)
    if not business:
        logger.error(f"Business not found: {business_id}")
        return Response(content='[]', media_type="application/json")
    
    # Greetings in 3 languages (spoken like a real receptionist picking up)
    greetings = {
        "hi": f"नमस्ते जी, {business.name} में आपका स्वागत है। मैं मीरा बात कर रही हूँ, बताइए मैं आपकी क्या मदद कर सकती हूँ?",
        "gu": f"નમસ્તે જી, {business.name} માં આપનું સ્વાગત છે. હું મીરા બોલું છું, કહો જી, હું તમની શું મદદ કરી શકું?",
        "en": f"Hello ji, welcome to {business.name}! This is Meera speaking — how can I help you today?",
    }
    
    greeting = greetings.get(business.language, greetings["en"])
    
    # Greeting spoken by the SAME Edge voice as the answers —
    # one consistent human voice for the whole call.
    greeting_audio = await synthesize_speech(greeting, business.language)
    ncco = [
        {"action": "play", "url": [f"{_public()}/audio/{greeting_audio}"]},
        _speech_input_action(business_id, business.language),
    ]
    
    increment_call_count(db, business_id)
    logger.info(f"Greeting played for {business_id}")
    
    return Response(content=json.dumps(ncco), media_type="application/json")

@app.post("/voice/outbound-answer/{outbound_id}", tags=["Voice"])
async def outbound_answer(outbound_id: str, db: Session = Depends(get_db)):
    """Vonage bridges here when WE call a customer — Meera opens personally"""
    from llm_service import generate_outbound_opener

    oc = db.query(OutboundCall).filter(OutboundCall.id == outbound_id).first()
    if not oc:
        return Response(content='[]', media_type="application/json")
    biz = get_business_record(db, oc.business_id)
    if not biz:
        return Response(content='[]', media_type="application/json")
    
    logger.info(f"Outbound call answered: {oc.caller_number} (agent {biz.id})")
    opener = generate_outbound_opener(
        business_name=biz.name,
        knowledge_base=biz.knowledge_base,
        language_code=biz.language,
        customer_name=oc.caller_name,
        customer_info=oc.info,
    )
    
    call_id = f"out-{oc.id}"
    start_call(db, biz.id, call_id)
    # Seed the conversation so Meera remembers she opened the call
    session_key = f"{biz.id}:oc-{oc.id}"
    if session_key not in CALL_SESSIONS and len(CALL_SESSIONS) >= MAX_CALL_SESSIONS:
        CALL_SESSIONS.pop(next(iter(CALL_SESSIONS)))
    CALL_SESSIONS[session_key] = {
        "history": [{"role": "assistant", "content": opener}],
        "silences": 0,
    }
    
    opener_audio = await synthesize_speech(opener, biz.language)
    try:
        save_conversation(db, str(uuid.uuid4())[:12], call_id, "(outbound call — agent calling)", opener)
    except Exception as e:
        logger.warning(f"Outbound opener save failed: {e}")
    ncco = [
        {"action": "play", "url": [f"{_public()}/audio/{opener_audio}"]},
        {
            "action": "input",
            "type": ["speech"],
            "speech": {"language": VONAGE_LANG_MAP.get(biz.language, "en-US"), "endOnSilence": 1.5},
            "eventUrl": [f"{_public()}/voice/event/{biz.id}?oc={oc.id}"],
            "eventMethod": "POST",
        },
    ]
    return Response(content=json.dumps(ncco), media_type="application/json")

@app.post("/voice/event/{business_id}", tags=["Voice"])
async def voice_event(business_id: str, request: Request, db: Session = Depends(get_db)):
    """Vonage sends speech results here"""
    logger.info(f"Voice event for: {business_id}")
    
    business = get_business_record(db, business_id)
    if not business:
        logger.error(f"Business not found: {business_id}")
        return {"status": "error"}
    
    # Parse Vonage request body (speech results + call uuid)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    logger.info(f"Voice event payload keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload)}")
    
    # Extract the caller's transcribed speech (handle known Vonage shapes)
    customer_query = ""
    if isinstance(payload, dict):
        results = payload.get("speech_results") or []
        if results and isinstance(results, list):
            customer_query = (results[0] or {}).get("text", "") or ""
        if not customer_query:
            # fallback: transcript/transcription fields
            customer_query = payload.get("transcript") or payload.get("transcription") or ""
    customer_query = (customer_query or "").strip()
    
    call_uuid = (payload.get("call_uuid") or payload.get("uuid") or payload.get("conversation_uuid") or "") if isinstance(payload, dict) else ""
    # Outbound call? The eventUrl carries ?oc={outbound_id} — reuse its seeded session
    oc_id = request.query_params.get("oc")
    if oc_id:
        session_key = f"{business_id}:oc-{oc_id}"
        call_id = f"out-{oc_id}"
        session = CALL_SESSIONS.setdefault(session_key, {"history": [], "silences": 0})
    else:
        session_key = f"{business_id}:{call_uuid}"
        if session_key not in CALL_SESSIONS and len(CALL_SESSIONS) >= MAX_CALL_SESSIONS:
            CALL_SESSIONS.pop(next(iter(CALL_SESSIONS)))  # drop oldest session
        session = CALL_SESSIONS.setdefault(session_key, {"history": [], "silences": 0})
        call_id = start_call(db, business_id, call_uuid)
    
    # Caller said nothing → reprompt; after 2 consecutive silences, wrap up warmly
    if not customer_query:
        session["silences"] = session.get("silences", 0) + 1
        logger.warning(f"No speech result from caller (silence #{session['silences']})")
        
        if session["silences"] >= 2:
            # ── CALL OVER: extract lead info for the owner's dashboard ──
            if session["history"]:
                try:
                    info = extract_lead_info(session["history"])
                    lead = create_lead_record(db, business_id, call_id, info)
                    if lead:
                        logger.info(f"Lead saved: {lead.caller_name} / {lead.caller_number} ({lead.intent})")
                except Exception as e:
                    logger.warning(f"Lead extraction failed: {e}")
            complete_call(db, call_id)
            CALL_SESSIONS.pop(session_key, None)
            farewell_audio = await synthesize_speech(
                FAREWELL_TEXTS.get(business.language, FAREWELL_TEXTS["en"]),
                business.language,
            )
            ncco = [
                {"action": "play", "url": [f"{_public()}/audio/{farewell_audio}"]},
            ]
            return Response(content=json.dumps(ncco), media_type="application/json")
        
        reprompt_audio = await synthesize_speech(
            REPROMPT_TEXTS.get(business.language, REPROMPT_TEXTS["en"]),
            business.language
        )
        ncco = [
            {"action": "play", "url": [f"{_public()}/audio/{reprompt_audio}"]},
            _speech_input_action(business_id, business.language),
        ]
        return Response(content=json.dumps(ncco), media_type="application/json")
    
    session["silences"] = 0
    
    # Get response from Groq LLM (with conversation memory)
    response_text = get_agent_response(
        business_name=business.name,
        knowledge_base=business.knowledge_base,
        language_code=business.language,
        customer_query=customer_query,
        conversation_history=session["history"],
    )
    
    # Save this Q&A turn for the owner's transcript view
    try:
        save_conversation(db, str(uuid.uuid4())[:12], call_id, customer_query, response_text)
    except Exception as e:
        logger.warning(f"Conversation save failed: {e}")
    
    # Synthesize speech (Edge TTS - free, async, sanitized for human delivery)
    audio_filename = await synthesize_speech(response_text, business.language)
    audio_url = f"{_public()}/audio/{audio_filename}"
    
    # Play the answer, then keep listening for the next question
    ncco = [
        {"action": "play", "url": [audio_url]},
        _speech_input_action(business_id, business.language),
    ]
    
    return Response(content=json.dumps(ncco), media_type="application/json")

# ============================================================================
# TWILIO (free-trial path) — TwiML twins of the Vonage NCCO routes above.
# Same brain, same sessions, same lead pipeline; only the wire format differs.
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
async def twilio_answer(business_id: str, db: Session = Depends(get_db)):
    """Twilio bridges here when a customer's phone rings — TwiML twin of /voice/answer"""
    business = get_business_record(db, business_id)
    if not business:
        return Response(content="<Response><Hangup/></Response>", media_type="application/xml")

    greetings = {
        "hi": f"नमस्ते जी, {business.name} में आपका स्वागत है। मैं मीरा बात कर रही हूँ, बताइए मैं आपकी क्या मदद कर सकती हूँ?",
        "gu": f"નમસ્તે જી, {business.name} માં આપનું સ્વાગત છે. હું મીરા બોલું છું, કહો જી, હું તમની શું મદદ કરી શકું?",
        "en": f"Hello ji, welcome to {business.name}! This is Meera speaking — how can I help you today?",
    }
    greeting_audio = await synthesize_speech(greetings.get(business.language, greetings["en"]), business.language)
    increment_call_count(db, business_id)
    xml = "<Response>" + _twiml_play(greeting_audio) + _twiml_gather(business_id, business.language) + "</Response>"
    return Response(content=xml, media_type="application/xml")


@app.post("/twilio/outbound-answer/{outbound_id}", tags=["Voice"])
async def twilio_outbound_answer(outbound_id: str, db: Session = Depends(get_db)):
    """Twilio bridges here when WE call a customer — TwiML twin of /voice/outbound-answer"""
    from llm_service import generate_outbound_opener

    oc = db.query(OutboundCall).filter(OutboundCall.id == outbound_id).first()
    biz = get_business_record(db, oc.business_id) if oc else None
    if not oc or not biz:
        return Response(content="<Response><Hangup/></Response>", media_type="application/xml")

    opener = generate_outbound_opener(
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
    CALL_SESSIONS[session_key] = {"history": [{"role": "assistant", "content": opener}], "silences": 0}

    opener_audio = await synthesize_speech(opener, biz.language)
    try:
        save_conversation(db, str(uuid.uuid4())[:12], call_id, "(outbound call — agent calling)", opener)
    except Exception as e:
        logger.warning(f"Outbound opener save failed: {e}")

    xml = "<Response>" + _twiml_play(opener_audio) + _twiml_gather(biz.id, biz.language, oc.id) + "</Response>"
    return Response(content=xml, media_type="application/xml")


@app.post("/twilio/event/{business_id}", tags=["Voice"])
async def twilio_event(business_id: str, request: Request, db: Session = Depends(get_db)):
    """Twilio posts speech results here — TwiML twin of /voice/event"""
    business = get_business_record(db, business_id)
    if not business:
        return Response(content="<Response><Hangup/></Response>", media_type="application/xml")

    # Twilio sends form-encoded webhooks (accept JSON too, for tests)
    payload = {}
    try:
        payload = dict(await request.form())
    except Exception:
        payload = {}
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
                    info = extract_lead_info(session["history"])
                    lead = create_lead_record(db, business_id, call_id, info)
                    if lead:
                        logger.info(f"Lead saved: {lead.caller_name} / {lead.caller_number} ({lead.intent})")
                except Exception as e:
                    logger.warning(f"Lead extraction failed: {e}")
            complete_call(db, call_id)
            CALL_SESSIONS.pop(session_key, None)
            farewell_audio = await synthesize_speech(
                FAREWELL_TEXTS.get(business.language, FAREWELL_TEXTS["en"]), business.language,
            )
            return _reply(_twiml_play(farewell_audio), "<Hangup/>")

        reprompt_audio = await synthesize_speech(
            REPROMPT_TEXTS.get(business.language, REPROMPT_TEXTS["en"]), business.language,
        )
        return _reply(_twiml_play(reprompt_audio), _twiml_gather(business_id, business.language, oc_id))

    session["silences"] = 0

    response_text = get_agent_response(
        business_name=business.name,
        knowledge_base=business.knowledge_base,
        language_code=business.language,
        customer_query=customer_query,
        conversation_history=session["history"],
    )
    try:
        save_conversation(db, str(uuid.uuid4())[:12], call_id, customer_query, response_text)
    except Exception as e:
        logger.warning(f"Conversation save failed: {e}")

    audio_filename = await synthesize_speech(response_text, business.language)
    return _reply(_twiml_play(audio_filename), _twiml_gather(business_id, business.language, oc_id))


@app.post("/twilio/status/{business_id}", tags=["Voice"])
async def twilio_status(business_id: str, request: Request, db: Session = Depends(get_db)):
    """Twilio posts the final call status here — save the lead even when
    the caller hangs up before the farewell (2-silence wrap-up never ran)"""
    try:
        payload = dict(await request.form())
    except Exception:
        payload = {}
    call_status = (payload.get("CallStatus") or "").strip().lower()
    call_sid = (payload.get("CallSid") or "").strip()
    logger.info(f"Twilio call status: {call_status} (sid {call_sid[:16]}…)")

    session_key = f"{business_id}:{call_sid}"
    session = CALL_SESSIONS.get(session_key)
    if call_status == "completed" and session and session.get("history"):
        try:
            info = extract_lead_info(session["history"])
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
    logger.info(f"Public URL: {_public()}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
