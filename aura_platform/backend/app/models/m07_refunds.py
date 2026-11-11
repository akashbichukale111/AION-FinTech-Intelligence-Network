"""
Module 07: Intelligent Refunds & Financial Recovery Models
"""
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text
)
from app.core.database import Base
from app.core.types import Money, utcnow, RefundStatus, ProviderName


class RefundRequest(Base):
    __tablename__ = "m07_refund_requests"

    request_id = Column(String(64), primary_key=True, index=True)
    transaction_id = Column(String(128), index=True, nullable=False) # Original transaction / capture
    provider = Column(String(50), default=ProviderName.PAYPAL.value)
    
    amount = Column(Money, nullable=False)
    currency = Column(String(10), default="USD")
    reason = Column(String(255), default="CUSTOMER_REQUEST")
    
    status = Column(String(50), default=RefundStatus.REQUESTED.value, index=True)
    eligibility_score = Column(Money, default=100.0)
    is_eligible = Column(Boolean, default=True)
    
    duplicate_guard_passed = Column(Boolean, default=True)
    dispute_shield_passed = Column(Boolean, default=True) # Ensure no active dispute
    requires_approval = Column(Boolean, default=False)
    
    provider_refund_id = Column(String(128), unique=True, index=True, nullable=True) # PayPal refund ID
    idempotency_key = Column(String(128), unique=True, index=True, nullable=True)
    
    execution_details = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RefundRecoveryCase(Base):
    __tablename__ = "m07_refund_recovery_cases"

    case_id = Column(String(64), primary_key=True)
    refund_id = Column(String(64), ForeignKey("m07_refund_requests.request_id"), index=True)
    dispute_reference = Column(String(128), nullable=True)
    
    recovered_amount = Column(Money, default=0.0)
    currency = Column(String(10), default="USD")
    status = Column(String(50), default="IN_RECOVERY")
    recovery_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
