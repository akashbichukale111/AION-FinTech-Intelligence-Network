"""
Module 10: Financial Copilot & Executive Intelligence Models
"""
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text
)
from app.core.database import Base
from app.core.types import Money, utcnow


class CopilotConversation(Base):
    __tablename__ = "m10_copilot_conversations"

    conversation_id = Column(String(64), primary_key=True, index=True)
    title = Column(String(255), default="Executive Financial Inquiry")
    user_id = Column(String(64), default="usr_admin_01")
    
    context_modules = Column(JSON, default=lambda: ["M01", "M02", "M03", "M04", "M05", "M06", "M07", "M08", "M09"])
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CopilotMessage(Base):
    __tablename__ = "m10_copilot_messages"

    message_id = Column(String(64), primary_key=True, index=True)
    conversation_id = Column(String(64), ForeignKey("m10_copilot_conversations.conversation_id"), index=True)
    
    sender = Column(String(20), default="USER") # USER or ASSISTANT
    content = Column(Text, nullable=False)
    
    structured_data = Column(JSON, default=dict)
    referenced_records = Column(JSON, default=list) # e.g. ["tx_123", "dispute_456"]
    confidence_score = Column(Money, default=95.0)
    
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ExecutiveBriefing(Base):
    __tablename__ = "m10_executive_briefings"

    briefing_id = Column(String(64), primary_key=True, index=True)
    title = Column(String(255), default="Daily Executive Financial Summary")
    briefing_date = Column(DateTime(timezone=True), default=utcnow, index=True)
    
    net_inflow_today = Column(Money, default=0.0)
    paypal_settlement_volume = Column(Money, default=0.0)
    disputes_requiring_action = Column(Integer, default=0)
    unresolved_risk_alerts = Column(Integer, default=0)
    reconciliation_match_rate = Column(Money, default=99.4)
    runway_projection_days = Column(Integer, default=180)
    
    key_highlights = Column(JSON, default=list)
    recommended_decisions = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=utcnow)
