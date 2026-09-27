import os
import uuid
import logging
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, Form, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.orm import Session
from pathlib import Path

from doc_processor import extract_text, clean_and_chunk
from llm_service import get_agent_response
from voice_service import download_recording, transcribe_audio, synthesize_speech, AUDIO_DIR
from database import SessionLocal, get_db, create_business_record, get_business_record, get_all_businesses
from database import increment_call_count, create_call_record, complete_call, get_system_analytics

load_dotenv()

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

# VONAGE CONFIG
VONAGE_API_KEY = os.getenv("VONAGE_API_KEY", "")
VONAGE_API_SECRET = os.getenv("VONAGE_API_SECRET", "")
VONAGE_PHONE_NUMBER = os.getenv("VONAGE_PHONE_NUMBER", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

# Vonage client initialization - will be added in Phase 2
if VONAGE_API_KEY and VONAGE_API_SECRET:
    logger.info("✅ Vonage credentials configured")
else:
    logger.warning("⚠️ Vonage credentials not configured")

# IN-MEMORY STORAGE FOR CALL SESSIONS
CALL_SESSIONS = {}

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "running",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/business", tags=["Business"])
async def create_business(
    name: str = Form(...),
    language: str = Form(...),
    file: UploadFile = None,
    db: Session = Depends(get_db)
):
    """Register a new business with knowledge document"""
    logger.info(f"Creating business: {name} (language: {language})")
    
    # Validation
    if language not in ("hi", "gu", "en"):
        logger.error(f"Invalid language: {language}")
        raise HTTPException(400, "Language must be 'hi', 'gu', or 'en'")
    
    if not file:
        logger.error("No file provided")
        raise HTTPException(400, "Please upload a knowledge document")
    
    if not name or len(name.strip()) == 0:
        logger.error("No business name provided")
        raise HTTPException(400, "Business name cannot be empty")
    
    try:
        # Extract text from file
        logger.info(f"Extracting text from: {file.filename}")
        file_bytes = await file.read()
        raw_text = extract_text(file.filename, file_bytes)
        
        if not raw_text or len(raw_text.strip()) == 0:
            logger.error("Extracted text is empty")
            raise ValueError("The document appears to be empty")
        
        # Clean and chunk text
        knowledge_base = clean_and_chunk(raw_text)
        logger.info(f"Knowledge base created: {len(knowledge_base)} characters")
        
        # Create business in database
        business_id = str(uuid.uuid4())[:8]
        create_business_record(db, business_id, name, language, knowledge_base)
        
        webhook_url = f"{PUBLIC_BASE_URL}/voice/answer/{business_id}"
        logger.info(f"Business created: {business_id} - {name}")
        
        return {
            "success": True,
            "business_id": business_id,
            "name": name,
            "language": language,
            "voice_webhook_url": webhook_url,
            "knowledge_preview": knowledge_base[:200] + "...",
            "message": "Business registered! Copy the webhook URL into Vonage."
        }
        
    except ValueError as e:
        logger.error(f"Document processing error: {str(e)}")
        raise HTTPException(400, f"Document error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(500, "An unexpected error occurred")

@app.get("/api/business/{business_id}", tags=["Business"])
async def get_business(business_id: str, db: Session = Depends(get_db)):
    """Get business details"""
    business = get_business_record(db, business_id)
    if not business:
        raise HTTPException(404, "Business not found")
    
    return {
        "id": business.id,
        "name": business.name,
        "language": business.language,
        "created_at": business.created_at.isoformat(),
        "total_calls": business.total_calls,
        "knowledge_base_size": len(business.knowledge_base),
    }

@app.get("/api/businesses", tags=["Business"])
async def list_businesses(db: Session = Depends(get_db)):
    """List all registered businesses"""
    businesses = get_all_businesses(db)
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
async def get_analytics(db: Session = Depends(get_db)):
    """Get system-wide analytics"""
    return get_system_analytics(db)

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
    
    # Greetings in 3 languages
    greetings = {
        "hi": f"नमस्ते! आप {business.name} से बात कर रहे हैं। कृपया अपना सवाल बताएं।",
        "gu": f"નમસ્તે! તમે {business.name} સાથે વાત કરી રહ્યા છો. કૃપા કરીને તમારો પ્રશ્ન કહો.",
        "en": f"Hello! You've reached {business.name}. Please tell us your question.",
    }
    
    greeting = greetings.get(business.language, greetings["en"])
    
    # Return NCCO (Vonage voice instruction)
    import json
    ncco = [
        {
            "action": "talk",
            "text": greeting,
            "language": "en-US"
        },
        {
            "action": "input",
            "type": ["speech"],
            "speech": {
                "language": "en-US",
                "endOnSilence": 1.5
            },
            "eventUrl": [f"{PUBLIC_BASE_URL}/voice/event/{business_id}"],
            "eventMethod": "POST"
        }
    ]
    
    increment_call_count(db, business_id)
    logger.info(f"Greeting played for {business_id}")
    
    return Response(content=json.dumps(ncco), media_type="application/json")

@app.post("/voice/event/{business_id}", tags=["Voice"])
async def voice_event(business_id: str, db: Session = Depends(get_db)):
    """Vonage sends speech results here"""
    logger.info(f"Voice event for: {business_id}")
    
    business = get_business_record(db, business_id)
    if not business:
        logger.error(f"Business not found: {business_id}")
        return {"status": "error"}
    
    # Get response from Claude
    customer_query = "Customer question"  # Parse from Vonage request body
    
    response_text = get_agent_response(
        business_name=business.name,
        knowledge_base=business.knowledge_base,
        language_code=business.language,
        customer_query=customer_query,
        conversation_history=[]
    )
    
    # Synthesize speech
    audio_filename = synthesize_speech(response_text, business.language)
    audio_url = f"{PUBLIC_BASE_URL}/audio/{audio_filename}"
    
    # Return NCCO to play response
    import json
    ncco = [
        {
            "action": "play",
            "url": [audio_url]
        }
    ]
    
    return Response(content=json.dumps(ncco), media_type="application/json")

# ============================================================================
# STARTUP
# ============================================================================

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 AI Voice Agent Backend Starting...")
    logger.info(f"Public URL: {PUBLIC_BASE_URL}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
