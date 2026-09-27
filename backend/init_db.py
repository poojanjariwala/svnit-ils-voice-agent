import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
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