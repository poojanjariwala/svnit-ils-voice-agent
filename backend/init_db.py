# backend/init_db.py
# ─────────────────────────────────────────────
# Owner  : Hardik
# Branch : feature/hardik-database
# Task   : One-time script to initialize the SQLite database
#
# When run, this script should:
#   1. Read DATABASE_URL from environment
#   2. Create SQLAlchemy engine
#   3. Call Base.metadata.create_all() to create all tables
#   4. Print success confirmation with table names
#
# Usage:
#   cd backend
#   python init_db.py
#
# TODO: Add full initialization script here
# ─────────────────────────────────────────────
import os
from sqlalchemy import create_engine
from models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./business_voice_agents.db")

print(f"📦 Initializing database: {DATABASE_URL}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

print("📝 Creating tables...")
Base.metadata.create_all(bind=engine)

print("✅ Database initialized successfully!")
print("📊 Tables created:")
print("   ├─ businesses")
print("   ├─ calls")
print("   ├─ conversations")
print("   └─ analytics")
