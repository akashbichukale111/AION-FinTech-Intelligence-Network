"""
API Routes: End-to-End Financial Workflows Trigger & Verification
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.workflows.orchestrator import WorkflowOrchestrator
from app.core.security import get_current_user

router = APIRouter(prefix="/workflows", tags=["Cross-Module Workflows"])


@router.post("/execute-all")
def execute_all_workflows(db: Session = Depends(get_db), user = Depends(get_current_user)):
    """
    Executes the complete cross-module pipeline:
    Workflow 1 -> Workflow 2 -> Workflow 3 -> Workflow 4 -> Workflow 7 -> Workflow 9
    """
    w1 = WorkflowOrchestrator.sync_records_to_payments(db)
    w2 = WorkflowOrchestrator.execute_reconciliation(db)
    w3 = WorkflowOrchestrator.evaluate_risk_on_records(db)
    w4 = WorkflowOrchestrator.update_treasury_analytics(db)
    w9 = WorkflowOrchestrator.generate_executive_briefing(db)

    return {
        "status": "ALL_WORKFLOWS_COMPLETED",
        "results": {
            "workflow_01_payments": w1,
            "workflow_02_reconciliation": w2,
            "workflow_03_risk": w3,
            "workflow_04_treasury": w4,
            "workflow_09_copilot_briefing": w9
        }
    }
