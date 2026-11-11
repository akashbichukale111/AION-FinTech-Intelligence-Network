"""
API Routes: Module 07 - Intelligent Refunds & Financial Recovery
"""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.core.database import get_db
from app.models.m07_refunds import RefundRequest, RefundRecoveryCase
from app.workflows.orchestrator import WorkflowOrchestrator
from app.core.security import get_current_user

router = APIRouter(prefix="/refunds", tags=["Module 07 - Refunds"])


class RefundRequestPayload(BaseModel):
    capture_id: str
    amount: float
    reason: str = "CUSTOMER_REQUEST"


@router.get("/")
def list_refunds(db: Session = Depends(get_db), user = Depends(get_current_user)):
    refunds = db.query(RefundRequest).order_by(RefundRequest.created_at.desc()).all()
    return {
        "refunds": [
            {
                "request_id": r.request_id,
                "transaction_id": r.transaction_id,
                "provider": r.provider,
                "amount": str(r.amount),
                "currency": r.currency,
                "status": r.status,
                "is_eligible": r.is_eligible,
                "requires_approval": r.requires_approval,
                "provider_refund_id": r.provider_refund_id,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in refunds
        ]
    }


@router.post("/request")
def request_refund(
    payload: RefundRequestPayload,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Submits a refund request through the 5-point eligibility & duplicate shield engine (Workflow 6).
    """
    return WorkflowOrchestrator.evaluate_refund_eligibility(
        db=db,
        capture_id=payload.capture_id,
        requested_amount=Decimal(str(payload.amount))
    )
