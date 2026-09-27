"""Database module for SQLAlchemy models and CRUD operations"""
from sqlalchemy import create_engine, Column, String, DateTime, Integer, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
import os

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Business(Base):
    __tablename__ = "businesses"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    language = Column(String, nullable=False)
    knowledge_base = Column(Text, nullable=False)
    total_calls = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class CallRecord(Base):
    __tablename__ = "calls"
    
    id = Column(String, primary_key=True)
    business_id = Column(String, nullable=False)
    customer_query = Column(Text, nullable=False)
    agent_response = Column(Text, nullable=True)
    duration = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

# Create tables
Base.metadata.create_all(bind=engine)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# CRUD Operations
def create_business_record(db: Session, business_id: str, name: str, language: str, knowledge_base: str):
    """Create a new business record"""
    business = Business(id=business_id, name=name, language=language, knowledge_base=knowledge_base)
    db.add(business)
    db.commit()
    db.refresh(business)
    return business

def get_business_record(db: Session, business_id: str):
    """Get business by ID"""
    return db.query(Business).filter(Business.id == business_id).first()

def get_all_businesses(db: Session):
    """Get all businesses"""
    return db.query(Business).all()

def increment_call_count(db: Session, business_id: str):
    """Increment call count for a business"""
    business = db.query(Business).filter(Business.id == business_id).first()
    if business:
        business.total_calls += 1
        db.commit()
        db.refresh(business)
    return business

def create_call_record(db: Session, call_id: str, business_id: str, customer_query: str):
    """Create a new call record"""
    call = CallRecord(id=call_id, business_id=business_id, customer_query=customer_query)
    db.add(call)
    db.commit()
    db.refresh(call)
    return call

def complete_call(db: Session, call_id: str, agent_response: str, duration: int):
    """Complete a call record"""
    call = db.query(CallRecord).filter(CallRecord.id == call_id).first()
    if call:
        call.agent_response = agent_response
        call.duration = duration
        call.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(call)
    return call

def get_system_analytics(db: Session):
    """Get system-wide analytics"""
    total_businesses = db.query(Business).count()
    total_calls = sum([b.total_calls for b in db.query(Business).all()])
    total_calls_record = db.query(CallRecord).count()
    
    return {
        "total_businesses": total_businesses,
        "total_calls": total_calls,
        "total_call_records": total_calls_record,
        "timestamp": datetime.utcnow().isoformat()
    }
