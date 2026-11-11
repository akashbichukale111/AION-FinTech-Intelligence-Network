"""
Module 02: Payment Operations & Routing Models
"""
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text
)
from app.core.database import Base
from app.core.types import Money, utcnow, ProviderName, RecordStatus


class PaymentAttempt(Base):
    __tablename__ = "m02_payment_attempts"

    attempt_id = Column(String(64), primary_key=True, index=True)
    order_id = Column(String(128), index=True, nullable=True)  # PayPal Order ID
    capture_id = Column(String(128), index=True, nullable=True) # PayPal Capture ID
    
    amount = Column(Money, nullable=False)
    currency = Column(String(10), default="USD", nullable=False)
    provider = Column(String(50), default=ProviderName.PAYPAL.value, index=True)
    
    status = Column(String(50), default="INITIATED", index=True)  # INITIATED, APPROVED, CAPTURED, FAILED
    payer_id = Column(String(128), nullable=True)
    payer_email = Column(String(255), nullable=True)
    
    routing_strategy = Column(String(100), default="DYNAMIC_LEAST_COST")
    latency_ms = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)
    
    error_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    
    idempotency_key = Column(String(128), unique=True, index=True, nullable=True)
    metadata_info = Column(JSON, default=dict)
    
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProviderPerformanceMetric(Base):
    __tablename__ = "m02_provider_performance"

    id = Column(Integer, primary_key=True, index=True)
    provider_name = Column(String(50), default=ProviderName.PAYPAL.value, index=True)
    window_period = Column(String(50), default="LAST_24H")
    total_volume = Column(Money, default=0.0)
    total_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    success_rate = Column(Money, default=100.0)
    avg_latency_ms = Column(Integer, default=180)
    health_status = Column(String(50), default="HEALTHY")
    updated_at = Column(DateTime(timezone=True), default=utcnow)


class RoutingRecommendation(Base):
    __tablename__ = "m02_routing_recommendations"

    recommendation_id = Column(String(64), primary_key=True)
    currency = Column(String(10), default="USD")
    recommended_provider = Column(String(50), default=ProviderName.PAYPAL.value)
    expected_success_rate = Column(Money, default=99.2)
    estimated_cost_basis = Column(Money, default=0.02)
    rationale = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
