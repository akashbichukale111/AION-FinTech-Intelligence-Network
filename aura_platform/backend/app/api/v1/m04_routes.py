"""
API Routes: Module 04 - Fraud & Risk Intelligence
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.m04_risk_intelligence import RiskAssessment, RiskAlert
from app.workflows.orchestrator import WorkflowOrchestrator
from app.core.security import get_current_user

router = APIRouter(prefix="/risk", tags=["Module 04 - Risk Intelligence"])


@router.post("/evaluate")
def run_risk_evaluation(db: Session = Depends(get_db), user = Depends(get_current_user)):
    """Triggers risk evaluation workflow on all unassessed records (Workflow 3)."""
    return WorkflowOrchestrator.evaluate_risk_on_records(db)


@router.get("/assessments")
def list_risk_assessments(
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200)
):
    assessments = db.query(RiskAssessment).order_by(RiskAssessment.risk_score.desc()).limit(limit).all()
    return {
        "assessments": [
            {
                "assessment_id": a.assessment_id,
                "transaction_id": a.transaction_id,
                "amount": str(a.amount),
                "currency": a.currency,
                "risk_score": a.risk_score,
                "risk_level": a.risk_level,
                "fraud_probability": str(a.fraud_probability),
                "triggered_rules": a.triggered_rules,
                "explanation": a.explanation,
                "created_at": a.created_at.isoformat() if a.created_at else None
            }
            for a in assessments
        ]
    }


@router.get("/alerts")
def list_risk_alerts(db: Session = Depends(get_db), user = Depends(get_current_user)):
    alerts = db.query(RiskAlert).order_by(RiskAlert.created_at.desc()).limit(25).all()
    return {
        "alerts": [
            {
                "alert_id": a.alert_id,
                "severity": a.severity,
                "alert_title": a.alert_title,
                "entity_ref": a.entity_ref,
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None
            }
            for a in alerts
        ]
    }
