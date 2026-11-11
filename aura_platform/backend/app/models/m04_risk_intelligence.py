"""
Module 04: Fraud, Scam & Risk Intelligence Models
"""
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text
)
from app.core.database import Base
from app.core.types import Money, utcnow, RiskLevel


class RiskAssessment(Base):
    __tablename__ = "m04_risk_assessments"

    assessment_id = Column(String(64), primary_key=True, index=True)
    transaction_id = Column(String(128), index=True, nullable=False) # PayPal or internal record
    
    amount = Column(Money, nullable=False)
    currency = Column(String(10), default="USD")
    
    risk_score = Column(Integer, default=15, index=True)  # 0 to 100
    risk_level = Column(String(20), default=RiskLevel.LOW.value, index=True)
    fraud_probability = Column(Money, default=0.02)
    
    velocity_anomaly = Column(Boolean, default=False)
    geographic_mismatch = Column(Boolean, default=False)
    device_fingerprint_flag = Column(Boolean, default=False)
    
    triggered_rules = Column(JSON, default=list)
    explanation = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)


class RiskAlert(Base):
    __tablename__ = "m04_risk_alerts"

    alert_id = Column(String(64), primary_key=True, index=True)
    assessment_id = Column(String(64), ForeignKey("m04_risk_assessments.assessment_id"), nullable=True)
    
    severity = Column(String(20), default=RiskLevel.HIGH.value, index=True)
    alert_title = Column(String(255), nullable=False)
    entity_ref = Column(String(128), nullable=True) # Payer email or transaction ID
    status = Column(String(50), default="UNRESOLVED", index=True) # UNRESOLVED, REVIEWED, DISMISSED, ACTIONED
    action_taken = Column(String(100), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=utcnow)
