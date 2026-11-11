"""
Module 06: Dispute & Chargeback Resolution Models
"""
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text
)
from app.core.database import Base
from app.core.types import Money, utcnow, DisputeStatus, ProviderName


class DisputeCase(Base):
    __tablename__ = "m06_dispute_cases"

    dispute_id = Column(String(64), primary_key=True, index=True)
    provider_dispute_id = Column(String(128), unique=True, index=True, nullable=True) # e.g. PP-D-12345
    provider = Column(String(50), default=ProviderName.PAYPAL.value, index=True)
    
    transaction_id = Column(String(128), index=True, nullable=False) # Ref Module 01/02
    payer_email = Column(String(255), nullable=True)
    
    disputed_amount = Column(Money, nullable=False)
    currency = Column(String(10), default="USD")
    
    reason = Column(String(100), default="MERCHANDISE_OR_SERVICE_NOT_RECEIVED") # PayPal reason
    lifecycle_stage = Column(String(50), default="INQUIRY") # INQUIRY, CHARGEBACK, PRE_ARBITRATION
    status = Column(String(50), default=DisputeStatus.OPEN.value, index=True)
    
    response_deadline = Column(DateTime(timezone=True), nullable=True, index=True)
    win_probability = Column(Money, default=78.5) # AI estimated win %
    
    recommended_action = Column(String(50), default="CONTEST_WITH_EVIDENCE")
    hateoas_actions = Column(JSON, default=list) # Actions returned by PayPal API
    evidence_submitted = Column(JSON, default=list)
    
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DisputeEvidenceItem(Base):
    __tablename__ = "m06_dispute_evidence"

    evidence_id = Column(String(64), primary_key=True)
    dispute_id = Column(String(64), ForeignKey("m06_dispute_cases.dispute_id"), index=True)
    
    evidence_type = Column(String(50), default="PROOF_OF_DELIVERY")
    document_name = Column(String(255), nullable=False)
    tracking_number = Column(String(128), nullable=True)
    carrier_name = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    
    status = Column(String(50), default="VALIDATED") # VALIDATED, REJECTED, SUBMITTED
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
