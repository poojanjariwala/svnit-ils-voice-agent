# backend/database.py
# ─────────────────────────────────────────────
# Owner  : Hardik
# Branch : feature/hardik-database
# Task   : SQLAlchemy engine, session, and CRUD helpers
#
# Functions to implement:
#   - get_db()                  → session dependency
#   - create_business_record()
#   - get_business_record()
#   - get_all_businesses()
#   - increment_call_count()
#   - create_call_record()
#   - complete_call()
#   - get_business_calls()
#   - save_conversation()
#   - get_business_analytics()
#   - get_system_analytics()
#
# TODO: Add full database implementation here
# ─────────────────────────────────────────────
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Business, Call, Conversation, Analytics, Lead, Campaign, OutboundCall
from datetime import datetime, timedelta
import uuid

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./business_voice_agents.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_business_record(db, business_id: str, owner_id: str, name: str, language: str, knowledge_base: str):
    business = Business(
        id=business_id,
        owner_id=owner_id,
        name=name,
        language=language,
        knowledge_base=knowledge_base,
        created_at=datetime.now()
    )
    db.add(business)
    db.commit()
    db.refresh(business)
    return business

def get_business_record(db, business_id: str):
    return db.query(Business).filter(Business.id == business_id).first()

def get_all_businesses(db):
    return db.query(Business).all()

def get_businesses_by_owner(db, owner_id: str):
    return db.query(Business).filter(Business.owner_id == owner_id).order_by(Business.created_at.desc()).all()

def get_owner_analytics(db, owner_id: str):
    """System analytics scoped to one owner's agents"""
    businesses = get_businesses_by_owner(db, owner_id)
    biz_ids = [b.id for b in businesses]
    calls = db.query(Call).filter(Call.business_id.in_(biz_ids)).all() if biz_ids else []
    return {
        "total_businesses": len(businesses),
        "total_calls": len(calls),
        "languages": list(set(b.language for b in businesses)),
        "timestamp": datetime.now().isoformat()
    }


def start_call(db, business_id: str, call_uuid: str = "") -> str:
    """Create a Call record when a call starts; returns its id"""
    call_id = call_uuid or str(uuid.uuid4())[:12]
    existing = db.query(Call).filter(Call.id == call_id).first()
    if existing:
        return call_id
    call = Call(id=call_id, business_id=business_id, start_time=datetime.now(), status="in_progress")
    db.add(call)
    db.commit()
    return call_id


def save_conversation(db, conv_id: str, call_id: str, user_msg: str, agent_resp: str):
    conversation = Conversation(
        id=conv_id or str(uuid.uuid4())[:12],
        call_id=call_id,
        user_message=user_msg,
        agent_response=agent_resp,
        timestamp=datetime.now()
    )
    db.add(conversation)
    db.commit()


def get_call_conversations(db, call_id: str):
    return db.query(Conversation).filter(Conversation.call_id == call_id).order_by(Conversation.timestamp).all()


def create_lead_record(db, business_id: str, call_id: str, info: dict):
    """Save extracted lead info; skip if there is nothing useful"""
    has_content = any(info.get(k) for k in ("caller_name", "caller_number", "interest", "scheduled_for"))
    if not has_content:
        return None
    lead = Lead(
        id=str(uuid.uuid4())[:12],
        business_id=business_id,
        call_id=call_id,
        caller_number=info.get("caller_number"),
        caller_name=info.get("caller_name"),
        interest=info.get("interest"),
        intent=info.get("intent") or "other",
        scheduled_for=info.get("scheduled_for"),
        notes=info.get("notes"),
        status="new",
        created_at=datetime.now(),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def get_leads_for_business(db, business_id: str):
    return db.query(Lead).filter(Lead.business_id == business_id).order_by(Lead.created_at.desc()).all()


def get_leads_for_owner(db, owner_id: str):
    biz_ids = [b.id for b in get_businesses_by_owner(db, owner_id)]
    if not biz_ids:
        return []
    return db.query(Lead).filter(Lead.business_id.in_(biz_ids)).order_by(Lead.created_at.desc()).all()


def get_call_transcripts_for_owner(db, owner_id: str, limit: int = 50):
    """Recent calls with their conversations, for the owner's dashboard"""
    biz_ids = [b.id for b in get_businesses_by_owner(db, owner_id)]
    if not biz_ids:
        return []
    calls = db.query(Call).filter(Call.business_id.in_(biz_ids)).order_by(Call.start_time.desc()).limit(limit).all()
    out = []
    for c in calls:
        convs = get_call_conversations(db, c.id)
        out.append({
            "call_id": c.id,
            "business_id": c.business_id,
            "start_time": c.start_time.isoformat() if c.start_time else None,
            "status": c.status,
            "turns": [
                {"role": "caller" if cv.user_message else "agent",
                 "caller": cv.user_message, "agent": cv.agent_response,
                 "at": cv.timestamp.isoformat()}
                for cv in convs
            ],
        })
    return out


# ── OUTBOUND CALLS (campaigns + single) ─────────────────────

def create_campaign(db, business_id: str, filename: str, rows: list) -> Campaign:
    """rows: [{name, number, info}] — one OutboundCall per row"""
    campaign = Campaign(
        id=str(uuid.uuid4())[:12],
        business_id=business_id,
        filename=filename,
        total=len(rows),
        status="ready",
        created_at=datetime.now(),
    )
    db.add(campaign)
    db.flush()
    for r in rows:
        db.add(OutboundCall(
            id=str(uuid.uuid4())[:12],
            campaign_id=campaign.id,
            business_id=business_id,
            caller_name=r.get("name") or None,
            caller_number=r["number"],
            info=r.get("info") or None,
            status="pending",
            created_at=datetime.now(),
        ))
    db.commit()
    db.refresh(campaign)
    return campaign


def create_single_outbound(db, business_id: str, number: str, name: str = None, info: str = None) -> OutboundCall:
    oc = OutboundCall(
        id=str(uuid.uuid4())[:12],
        campaign_id=None,
        business_id=business_id,
        caller_name=name,
        caller_number=number,
        info=info,
        status="pending",
        created_at=datetime.now(),
    )
    db.add(oc)
    db.commit()
    db.refresh(oc)
    return oc


def get_campaign_with_calls(db, campaign_id: str):
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        return None, []
    calls = db.query(OutboundCall).filter(OutboundCall.campaign_id == campaign_id).order_by(OutboundCall.created_at).all()
    return campaign, calls


def get_outbound_for_owner(db, owner_id: str, limit: int = 100):
    biz_ids = [b.id for b in get_businesses_by_owner(db, owner_id)]
    if not biz_ids:
        return []
    rows = db.query(OutboundCall).filter(OutboundCall.business_id.in_(biz_ids)).order_by(OutboundCall.created_at.desc()).limit(limit).all()
    return rows


def mark_outbound(oc: OutboundCall, status: str, detail: str = None, call_uuid: str = None):
    oc.status = status
    if detail is not None:
        oc.detail = detail
    if call_uuid:
        oc.call_uuid = call_uuid
    if status in ("calling", "done"):
        oc.called_at = datetime.now()

def increment_call_count(db, business_id: str):
    business = db.query(Business).filter(Business.id == business_id).first()
    if business:
        business.total_calls = business.total_calls + 1
        db.commit()

def create_call_record(db, call_id: str, business_id: str, phone_number: str):
    call = Call(
        id=call_id,
        business_id=business_id,
        phone_number=phone_number,
        start_time=datetime.now(),
        status="in_progress"
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call

def complete_call(db, call_id: str, status: str = "completed"):
    call = db.query(Call).filter(Call.id == call_id).first()
    if call:
        call.end_time = datetime.now()
        call.status = status
        if call.start_time:
            call.duration_seconds = int((call.end_time - call.start_time).total_seconds())
        db.commit()

def get_business_calls(db, business_id: str, days: int = 7):
    start_date = datetime.now() - timedelta(days=days)
    return db.query(Call).filter(
        Call.business_id == business_id,
        Call.start_time >= start_date
    ).all()

def save_conversation(db, conv_id: str, call_id: str, user_msg: str, agent_resp: str):
    conversation = Conversation(
        id=conv_id,
        call_id=call_id,
        user_message=user_msg,
        agent_response=agent_resp,
        timestamp=datetime.now()
    )
    db.add(conversation)
    db.commit()

def get_business_analytics(db, business_id: str):
    business = db.query(Business).filter(Business.id == business_id).first()
    calls = db.query(Call).filter(Call.business_id == business_id).all()
    total_duration = sum((c.duration_seconds or 0) for c in calls)
    
    return {
        "business_id": business_id,
        "name": business.name if business else "Unknown",
        "total_calls": len(calls),
        "total_duration_seconds": total_duration,
        "average_duration": total_duration / len(calls) if calls else 0,
        "language": business.language if business else "Unknown"
    }

def get_system_analytics(db):
    businesses = db.query(Business).all()
    calls = db.query(Call).all()
    conversations = db.query(Conversation).all()
    
    return {
        "total_businesses": len(businesses),
        "total_calls": len(calls),
        "total_conversations": len(conversations),
        "languages": list(set(b.language for b in businesses)),
        "timestamp": datetime.now().isoformat()
    }
