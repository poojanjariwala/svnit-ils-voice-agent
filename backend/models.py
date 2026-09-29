# backend/models.py
# ─────────────────────────────────────────────
# Owner  : Hardik
# Branch : feature/hardik-database
# Task   : Define SQLAlchemy ORM models
#
# Tables:
#   - User         → self-serve account (email + password)
#   - Business     → a voice agent (owned by a User)
#   - Call         → logs each incoming call
#   - Conversation → stores each Q&A turn
#   - Analytics    → daily aggregated stats
# ─────────────────────────────────────────────
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(String(50), primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

class Business(Base):
    __tablename__ = "businesses"

    id = Column(String(50), primary_key=True)
    owner_id = Column(String(50), ForeignKey("users.id"), nullable=False, index=True)
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

class Lead(Base):
    """A potential customer captured from a call — the conversion record."""
    __tablename__ = "leads"

    id = Column(String(50), primary_key=True)
    business_id = Column(String(50), ForeignKey("businesses.id"), nullable=False, index=True)
    call_id = Column(String(50), ForeignKey("calls.id"), nullable=True, index=True)
    caller_number = Column(String(20), nullable=True, index=True)
    caller_name = Column(String(255), nullable=True)
    interest = Column(Text, nullable=True)          # what they asked about
    intent = Column(String(50), nullable=True)      # pricing | test_drive | appointment | info | complaint | other
    scheduled_for = Column(String(255), nullable=True)  # e.g. "tomorrow 5pm" (free text)
    notes = Column(Text, nullable=True)
    status = Column(String(30), default="new")      # new | contacted | converted | closed
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class Campaign(Base):
    """A batch of outbound calls (e.g. from an uploaded Excel list)"""
    __tablename__ = "campaigns"

    id = Column(String(50), primary_key=True)
    business_id = Column(String(50), ForeignKey("businesses.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=True)
    total = Column(Integer, default=0)
    status = Column(String(30), default="ready")   # ready | running | completed
    created_at = Column(DateTime, default=datetime.now, nullable=False)


class OutboundCall(Base):
    """One outbound call to a customer (single or part of a campaign)"""
    __tablename__ = "outbound_calls"

    id = Column(String(50), primary_key=True)
    campaign_id = Column(String(50), ForeignKey("campaigns.id"), nullable=True, index=True)
    business_id = Column(String(50), ForeignKey("businesses.id"), nullable=False, index=True)
    caller_name = Column(String(255), nullable=True)
    caller_number = Column(String(20), nullable=False)
    info = Column(Text, nullable=True)               # why we're calling them
    status = Column(String(30), default="pending")   # pending | calling | done | failed
    call_uuid = Column(String(80), nullable=True)
    detail = Column(Text, nullable=True)             # outcome / error in plain words
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    called_at = Column(DateTime, nullable=True)

class Analytics(Base):
    __tablename__ = "analytics"

    id = Column(String(50), primary_key=True)
    business_id = Column(String(50), ForeignKey("businesses.id"), nullable=False, index=True)
    date = Column(String(10), nullable=False)
    total_calls = Column(Integer, default=0)
    total_duration_seconds = Column(Integer, default=0)
    average_response_time = Column(Integer, default=0)
