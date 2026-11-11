"""
Module 09: Agentic Decision & Governance Engine Models
"""
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text
)
from app.core.database import Base
from app.core.types import Money, utcnow, GovernanceActionType


class GovernanceAgent(Base):
    __tablename__ = "m09_governance_agents"

    agent_id = Column(String(64), primary_key=True, index=True)
    agent_name = Column(String(100), unique=True, nullable=False)
    domain = Column(String(50), default="TREASURY_OPS") # RECON, RISK, DISPUTES, REFUNDS, PAYPAL
    status = Column(String(50), default="ACTIVE") # ACTIVE, PAUSED, RESTRICTED
    autonomy_level = Column(String(50), default="SEMI_AUTONOMOUS") # FULL, SEMI_AUTONOMOUS, ADVISORY
    
    total_actions_proposed = Column(Integer, default=0)
    total_actions_approved = Column(Integer, default=0)
    last_active_at = Column(DateTime(timezone=True), default=utcnow)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class GovernancePolicy(Base):
    __tablename__ = "m09_governance_policies"

    policy_id = Column(String(64), primary_key=True, index=True)
    policy_name = Column(String(200), nullable=False)
    action_type = Column(String(50), default=GovernanceActionType.EXECUTE_PAYPAL_REFUND.value, index=True)
    
    requires_approval_above_amount = Column(Money, default=250.0)
    required_roles = Column(JSON, default=lambda: ["operator", "admin"])
    is_active = Column(Boolean, default=True)
    description = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ApprovalRequest(Base):
    __tablename__ = "m09_approval_requests"

    request_id = Column(String(64), primary_key=True, index=True)
    agent_id = Column(String(64), nullable=True)
    action_type = Column(String(50), nullable=False, index=True)
    
    target_entity_type = Column(String(50), default="TRANSACTION")
    target_entity_id = Column(String(128), nullable=False, index=True)
    
    amount = Column(Money, default=0.0)
    currency = Column(String(10), default="USD")
    
    status = Column(String(50), default="PENDING", index=True) # PENDING, APPROVED, REJECTED, EXECUTED
    urgency = Column(String(20), default="MEDIUM") # LOW, MEDIUM, HIGH, CRITICAL
    
    requested_by = Column(String(100), default="Agent_AutoRefund_Sentinel")
    approved_by = Column(String(100), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class GovernanceAuditLog(Base):
    __tablename__ = "m09_governance_audit_logs"

    log_id = Column(String(64), primary_key=True, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    actor_id = Column(String(100), nullable=False)
    actor_type = Column(String(50), default="SYSTEM") # USER, AGENT, WEBHOOK, SYSTEM
    
    module_source = Column(String(50), default="M09_GOVERNANCE")
    entity_ref = Column(String(128), nullable=True, index=True)
    
    details = Column(JSON, default=dict)
    ip_address = Column(String(45), default="127.0.0.1")
    timestamp = Column(DateTime(timezone=True), default=utcnow, index=True)
