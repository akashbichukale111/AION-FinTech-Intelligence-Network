"""
API Routes: Module 08 - Credit, Repayment & Early Warning Intelligence
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.m08_credit import BorrowerProfile, CreditFacility, RepaymentSchedule
from app.workflows.orchestrator import WorkflowOrchestrator
from app.core.security import get_current_user

router = APIRouter(prefix="/credit", tags=["Module 08 - Credit & Repayments"])


@router.get("/borrowers")
def list_borrowers(db: Session = Depends(get_db), user = Depends(get_current_user)):
    borrowers = db.query(BorrowerProfile).all()
    return {
        "borrowers": [
            {
                "borrower_id": b.borrower_id,
                "borrower_ref": b.borrower_ref,
                "legal_name": b.legal_name,
                "risk_rating": b.risk_rating,
                "total_exposure": str(b.total_exposure),
                "available_credit": str(b.available_credit),
                "default_probability": str(b.default_probability),
                "early_warning_status": b.early_warning_status
            }
            for b in borrowers
        ]
    }


@router.post("/borrowers/{borrower_id}/evaluate-warning")
def evaluate_borrower_early_warning(
    borrower_id: str,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Correlates borrower debt service with real treasury cash flows (Workflow 7)."""
    return WorkflowOrchestrator.evaluate_credit_and_cashflow(db, borrower_id)


@router.get("/facilities")
def list_facilities(db: Session = Depends(get_db), user = Depends(get_current_user)):
    facilities = db.query(CreditFacility).all()
    return {
        "facilities": [
            {
                "facility_id": f.facility_id,
                "borrower_id": f.borrower_id,
                "facility_type": f.facility_type,
                "credit_limit": str(f.credit_limit),
                "utilized_amount": str(f.utilized_amount),
                "currency": f.currency,
                "interest_rate_pct": str(f.interest_rate_pct),
                "status": f.status
            }
            for f in facilities
        ]
    }
