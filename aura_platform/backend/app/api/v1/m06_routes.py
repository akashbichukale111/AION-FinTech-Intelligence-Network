"""
API Routes: Module 06 - Dispute & Chargeback Resolution
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.models.m06_disputes import DisputeCase, DisputeEvidenceItem
from app.workflows.orchestrator import WorkflowOrchestrator
from app.core.security import get_current_user

router = APIRouter(prefix="/disputes", tags=["Module 06 - Disputes"])


class EvidenceSubmitPayload(BaseModel):
    evidence_type: str = "PROOF_OF_DELIVERY"
    document_name: str
    tracking_number: Optional[str] = None
    carrier_name: Optional[str] = None
    notes: Optional[str] = None


@router.get("/")
def list_disputes(db: Session = Depends(get_db), user = Depends(get_current_user)):
    disputes = db.query(DisputeCase).order_by(DisputeCase.created_at.desc()).all()
    return {
        "disputes": [
            {
                "dispute_id": d.dispute_id,
                "provider_dispute_id": d.provider_dispute_id,
                "provider": d.provider,
                "transaction_id": d.transaction_id,
                "payer_email": d.payer_email,
                "disputed_amount": str(d.disputed_amount),
                "currency": d.currency,
                "reason": d.reason,
                "lifecycle_stage": d.lifecycle_stage,
                "status": d.status,
                "win_probability": str(d.win_probability),
                "recommended_action": d.recommended_action,
                "response_deadline": d.response_deadline.isoformat() if d.response_deadline else None
            }
            for d in disputes
        ]
    }


@router.post("/{dispute_id}/enrich")
def auto_enrich_dispute_evidence(dispute_id: str, db: Session = Depends(get_db), user = Depends(get_current_user)):
    """Auto-enriches dispute case with evidence from modules 01-04 (Workflow 5)."""
    return WorkflowOrchestrator.auto_enrich_dispute(db, dispute_id)


@router.post("/{dispute_id}/evidence")
def attach_evidence(
    dispute_id: str,
    payload: EvidenceSubmitPayload,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    import uuid
    dispute = db.query(DisputeCase).filter(DisputeCase.dispute_id == dispute_id).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute case not found")

    item = DisputeEvidenceItem(
        evidence_id=f"evi_{uuid.uuid4().hex[:12]}",
        dispute_id=dispute_id,
        evidence_type=payload.evidence_type,
        document_name=payload.document_name,
        tracking_number=payload.tracking_number,
        carrier_name=payload.carrier_name,
        notes=payload.notes,
        status="VALIDATED"
    )
    db.add(item)
    db.commit()
    return {"success": True, "evidence_id": item.evidence_id, "status": item.status}
