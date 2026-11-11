"""
API Routes: Module 09 - Agentic Decision & Governance Engine
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.models.m09_governance import GovernanceAgent, GovernancePolicy, ApprovalRequest, GovernanceAuditLog
from app.workflows.orchestrator import WorkflowOrchestrator
from app.core.security import get_current_user

router = APIRouter(prefix="/governance", tags=["Module 09 - Governance Engine"])


class ApprovalActionPayload(BaseModel):
    approver_name: str = "Chief Financial Officer"
    notes: Optional[str] = None


@router.get("/agents")
def list_governance_agents(db: Session = Depends(get_db), user = Depends(get_current_user)):
    agents = db.query(GovernanceAgent).all()
    return {
        "agents": [
            {
                "agent_id": a.agent_id,
                "agent_name": a.agent_name,
                "domain": a.domain,
                "status": a.status,
                "autonomy_level": a.autonomy_level,
                "total_actions_proposed": a.total_actions_proposed,
                "total_actions_approved": a.total_actions_approved,
                "last_active_at": a.last_active_at.isoformat() if a.last_active_at else None
            }
            for a in agents
        ]
    }


@router.get("/approvals")
def list_approval_requests(db: Session = Depends(get_db), user = Depends(get_current_user)):
    requests = db.query(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).all()
    return {
        "approvals": [
            {
                "request_id": r.request_id,
                "agent_id": r.agent_id,
                "action_type": r.action_type,
                "target_entity_type": r.target_entity_type,
                "target_entity_id": r.target_entity_id,
                "amount": str(r.amount),
                "currency": r.currency,
                "status": r.status,
                "urgency": r.urgency,
                "requested_by": r.requested_by,
                "approved_by": r.approved_by,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in requests
        ]
    }


@router.post("/approvals/{request_id}/approve")
def approve_action(
    request_id: str,
    payload: ApprovalActionPayload,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Executes governed action authoritatively and writes cryptographic audit log (Workflow 8)."""
    return WorkflowOrchestrator.approve_governance_action(
        db=db,
        request_id=request_id,
        approver_name=payload.approver_name
    )


@router.get("/audit-logs")
def list_audit_logs(db: Session = Depends(get_db), user = Depends(get_current_user)):
    logs = db.query(GovernanceAuditLog).order_by(GovernanceAuditLog.timestamp.desc()).limit(50).all()
    return {
        "logs": [
            {
                "log_id": l.log_id,
                "event_type": l.event_type,
                "actor_id": l.actor_id,
                "actor_type": l.actor_type,
                "module_source": l.module_source,
                "entity_ref": l.entity_ref,
                "details": l.details,
                "timestamp": l.timestamp.isoformat() if l.timestamp else None
            }
            for l in logs
        ]
    }
