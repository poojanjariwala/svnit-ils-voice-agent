# backend/models.py
# ─────────────────────────────────────────────
# Owner  : Hardik
# Branch : feature/hardik-database
# Task   : Define SQLAlchemy ORM models
#
# Tables to implement:
#   - Business   → stores registered businesses
#   - Call       → logs each incoming call
#   - Conversation → stores each Q&A turn
#   - Analytics  → daily aggregated stats
#
# TODO: Add full model definitions here
# ─────────────────────────────────────────────
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class Business(Base):
    __tablename__ = "businesses"
    
    id = Column(String(50), primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    language = Column(String(5), nullable=False)
    knowledge_base = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    total_calls = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

class Call(Base):
    __tablename__ = "calls"
    
    id = Column(String(50), primary_key=True)
    business_id = Column(String(50), ForeignKey("businesses.id"), nullable=False, index=True)
    phone_number = Column(String(20), nullable=True)
    duration_seconds = Column(Integer, default=0)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True)
    status = Column(String(20), default="in_progress")
    transcribed_text = Column(Text, nullable=True)

class Conversation(Base):
    __tablename__ = "conversations"
    
    id = Column(String(50), primary_key=True)
    call_id = Column(String(50), ForeignKey("calls.id"), nullable=False, index=True)
    user_message = Column(Text, nullable=False)
    agent_response = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.now, nullable=False)

class Analytics(Base):
    __tablename__ = "analytics"
    
    id = Column(String(50), primary_key=True)
    business_id = Column(String(50), ForeignKey("businesses.id"), nullable=False, index=True)
    date = Column(String(10), nullable=False)
    total_calls = Column(Integer, default=0)
    total_duration_seconds = Column(Integer, default=0)
    average_response_time = Column(Integer, default=0)
