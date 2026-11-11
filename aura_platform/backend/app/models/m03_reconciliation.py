"""
Module 03: Autonomous Reconciliation Models
"""
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text
)
from app.core.database import Base
from app.core.types import Money, utcnow, ReconStatus


class ReconRun(Base):
    __tablename__ = "m03_recon_runs"

    run_id = Column(String(64), primary_key=True, index=True)
    scope = Column(String(100), default="PAYPAL_SETTLEMENT_VS_LEDGER")
    status = Column(String(50), default="COMPLETED")
    
    total_records_analyzed = Column(Integer, default=0)
    matched_count = Column(Integer, default=0)
    exception_count = Column(Integer, default=0)
    
    matched_amount = Column(Money, default=0.0)
    unmatched_amount = Column(Money, default=0.0)
    match_rate = Column(Money, default=100.0)
    
    summary_metrics = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)


class ReconMatch(Base):
    __tablename__ = "m03_recon_matches"

    match_id = Column(String(64), primary_key=True, index=True)
    run_id = Column(String(64), ForeignKey("m03_recon_runs.run_id"), nullable=False, index=True)
    
    internal_record_id = Column(String(64), index=True, nullable=True) # Ref Module 01
    provider_tx_id = Column(String(128), index=True, nullable=True)     # PayPal Tx ID
    
    matched_amount = Column(Money, nullable=False)
    currency = Column(String(10), default="USD")
    confidence_score = Column(Money, default=100.0)
    match_type = Column(String(50), default=ReconStatus.EXACT_MATCH.value)
    
    matched_at = Column(DateTime(timezone=True), default=utcnow)


class ReconException(Base):
    __tablename__ = "m03_recon_exceptions"

    exception_id = Column(String(64), primary_key=True, index=True)
    run_id = Column(String(64), ForeignKey("m03_recon_runs.run_id"), nullable=False, index=True)
    
    record_id = Column(String(64), nullable=True)
    provider_tx_id = Column(String(128), nullable=True)
    
    exception_type = Column(String(50), default=ReconStatus.AMOUNT_MISMATCH.value)
    discrepancy_amount = Column(Money, default=0.0)
    currency = Column(String(10), default="USD")
    
    status = Column(String(50), default="OPEN")  # OPEN, INVESTIGATING, RESOLVED
    resolution_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
