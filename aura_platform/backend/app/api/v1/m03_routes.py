"""
API Routes: Module 03 - Autonomous Reconciliation
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.m03_reconciliation import ReconRun, ReconMatch, ReconException
from app.workflows.orchestrator import WorkflowOrchestrator
from app.core.security import get_current_user

router = APIRouter(prefix="/reconciliation", tags=["Module 03 - Reconciliation"])


@router.post("/runs/trigger")
def trigger_recon_run(db: Session = Depends(get_db), user = Depends(get_current_user)):
    """Triggers an autonomous reconciliation run (Workflow 2)."""
    return WorkflowOrchestrator.execute_reconciliation(db)


@router.get("/runs")
def list_recon_runs(db: Session = Depends(get_db), user = Depends(get_current_user)):
    runs = db.query(ReconRun).order_by(ReconRun.created_at.desc()).limit(15).all()
    return {
        "runs": [
            {
                "run_id": r.run_id,
                "scope": r.scope,
                "status": r.status,
                "total_records_analyzed": r.total_records_analyzed,
                "matched_count": r.matched_count,
                "exception_count": r.exception_count,
                "matched_amount": str(r.matched_amount),
                "unmatched_amount": str(r.unmatched_amount),
                "match_rate": str(r.match_rate),
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in runs
        ]
    }


@router.get("/exceptions")
def list_recon_exceptions(
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200)
):
    exceptions = db.query(ReconException).order_by(ReconException.created_at.desc()).limit(limit).all()
    return {
        "exceptions": [
            {
                "exception_id": e.exception_id,
                "run_id": e.run_id,
                "record_id": e.record_id,
                "provider_tx_id": e.provider_tx_id,
                "exception_type": e.exception_type,
                "discrepancy_amount": str(e.discrepancy_amount),
                "currency": e.currency,
                "status": e.status,
                "resolution_notes": e.resolution_notes
            }
            for e in exceptions
        ]
    }
