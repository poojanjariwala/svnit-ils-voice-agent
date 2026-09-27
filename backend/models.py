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