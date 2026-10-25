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
import sys

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import create_engine
from models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./business_voice_agents.db")

print(f"📦 Initializing database: {DATABASE_URL}")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)

print("📝 Creating tables...")
Base.metadata.create_all(bind=engine)

print("✅ Database initialized successfully!")
print("📊 Tables ready:")
for table_name in Base.metadata.tables.keys():
    print(f"   ├─ {table_name}")

