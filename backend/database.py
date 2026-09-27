import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Business, Call, Conversation, Analytics
from datetime import datetime, timedelta

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

def create_business_record(db, business_id: str, name: str, language: str, knowledge_base: str):
    business = Business(
        id=business_id,
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