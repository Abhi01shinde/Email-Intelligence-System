from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, JSON, UniqueConstraint
from sqlalchemy.sql import func
from database import Base
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


# ─── ORM Models ───────────────────────────────────────────────────────────────

class Email(Base):
    __tablename__ = "emails"
    __table_args__ = (
        UniqueConstraint("account_email", "gmail_id", name="uq_emails_account_gmail_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_email = Column(String, index=True, nullable=True)
    gmail_id = Column(String, index=True)
    sender = Column(String)
    sender_email = Column(String)
    recipient = Column(String)
    subject = Column(String)
    body = Column(Text)
    body_snippet = Column(String)
    classification = Column(String)        # Urgent / Task / Informational / Spam
    priority_score = Column(Integer, default=5)
    deadline = Column(String, nullable=True)
    commitment_phrases = Column(JSON, default=list)
    action_items = Column(JSON, default=list)
    mood = Column(String, nullable=True)   # Stressed / Professional / Friendly / Demanding
    stress_score = Column(Integer, default=0)  # 0-100 — UNIQUE FEATURE
    suggested_action = Column(String)
    ai_summary = Column(Text, nullable=True)
    ai_reply_draft = Column(Text, nullable=True)
    ai_reasoning = Column(Text, nullable=True)
    language = Column(String, default="en")
    is_read = Column(Boolean, default=False)
    is_replied = Column(Boolean, default=False)
    is_deferred = Column(Boolean, default=False)
    label_applied = Column(String, nullable=True)
    processed_at = Column(DateTime, default=func.now())
    received_at = Column(DateTime, nullable=True)


class Commitment(Base):
    __tablename__ = "commitments"

    id = Column(Integer, primary_key=True, index=True)
    account_email = Column(String, index=True, nullable=True)
    email_id = Column(Integer)
    email_subject = Column(String)
    sender = Column(String)
    commitment_text = Column(Text)
    deadline = Column(String, nullable=True)
    status = Column(String, default="pending")   # pending / completed / missed
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    account_email = Column(String, index=True, nullable=True)
    email_id = Column(Integer, nullable=True)
    title = Column(String)
    description = Column(Text, nullable=True)
    priority = Column(String, default="medium")   # low / medium / high / critical
    priority_score = Column(Integer, default=5)
    deadline = Column(String, nullable=True)
    status = Column(String, default="pending")   # pending / in_progress / completed / missed
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id = Column(Integer, primary_key=True, index=True)
    account_email = Column(String, index=True, nullable=True)
    email_id = Column(Integer, nullable=True)
    email_subject = Column(String, nullable=True)
    sender = Column(String, nullable=True)
    decision_taken = Column(String)
    reason = Column(Text)
    classification = Column(String, nullable=True)
    cognitive_load_at_time = Column(Integer, nullable=True)
    was_suppressed = Column(Boolean, default=False)
    suppression_reason = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=func.now())


class SenderProfile(Base):
    """UNIQUE FEATURE: AI-built intelligence profile for each sender"""
    __tablename__ = "sender_profiles"

    id = Column(Integer, primary_key=True, index=True)
    account_email = Column(String, index=True, nullable=True)
    email = Column(String, unique=True, index=True)
    sender_email = Column(String, index=True, nullable=True)
    name = Column(String)
    email_count = Column(Integer, default=0)
    avg_priority = Column(Float, default=5.0)
    avg_stress_score = Column(Float, default=0.0)
    common_topics = Column(JSON, default=list)
    communication_style = Column(String, nullable=True)  # e.g. "Formal & Direct"
    typical_urgency = Column(String, nullable=True)       # Low / Medium / High
    reliability_score = Column(Float, default=50.0)       # 0-100
    notes = Column(Text, nullable=True)
    last_email_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class CognitiveLoadSnapshot(Base):
    __tablename__ = "cognitive_load_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    account_email = Column(String, index=True, nullable=True)
    load_score = Column(Integer)
    active_tasks = Column(Integer, default=0)
    overdue_tasks = Column(Integer, default=0)
    pending_commitments = Column(Integer, default=0)
    unread_urgent = Column(Integer, default=0)
    threshold = Column(Integer, default=10)
    is_suppressing = Column(Boolean, default=False)
    recorded_at = Column(DateTime, default=func.now())


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

class EmailSchema(BaseModel):
    id: int
    gmail_id: str
    sender: str
    sender_email: str
    subject: str
    body_snippet: Optional[str]
    classification: Optional[str]
    priority_score: Optional[int]
    deadline: Optional[str]
    commitment_phrases: Optional[list]
    action_items: Optional[list]
    mood: Optional[str]
    stress_score: Optional[int]
    suggested_action: Optional[str]
    ai_summary: Optional[str]
    ai_reply_draft: Optional[str]
    ai_reasoning: Optional[str]
    language: Optional[str]
    is_read: bool
    is_replied: bool
    processed_at: Optional[datetime]
    received_at: Optional[datetime]

    class Config:
        from_attributes = True


class CommitmentSchema(BaseModel):
    id: int
    email_id: Optional[int]
    email_subject: Optional[str]
    sender: Optional[str]
    commitment_text: str
    deadline: Optional[str]
    status: str
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class TaskSchema(BaseModel):
    id: int
    email_id: Optional[int]
    title: str
    description: Optional[str]
    priority: str
    priority_score: int
    deadline: Optional[str]
    status: str
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class DecisionLogSchema(BaseModel):
    id: int
    email_id: Optional[int]
    email_subject: Optional[str]
    sender: Optional[str]
    decision_taken: str
    reason: str
    classification: Optional[str]
    cognitive_load_at_time: Optional[int]
    was_suppressed: bool
    suppression_reason: Optional[str]
    timestamp: Optional[datetime]

    class Config:
        from_attributes = True


class SenderProfileSchema(BaseModel):
    id: int
    email: str
    name: str
    email_count: int
    avg_priority: float
    avg_stress_score: float
    common_topics: Optional[list]
    communication_style: Optional[str]
    typical_urgency: Optional[str]
    reliability_score: float
    notes: Optional[str]
    last_email_at: Optional[datetime]

    class Config:
        from_attributes = True
