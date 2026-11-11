"""
API Routes: Module 05 - Treasury & Cash Flow Intelligence
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.m05_treasury import AccountBalance, CashFlowSnapshot, TreasuryForecast
from app.workflows.orchestrator import WorkflowOrchestrator
from app.core.security import get_current_user

router = APIRouter(prefix="/treasury", tags=["Module 05 - Treasury"])


@router.post("/refresh")
def refresh_treasury(db: Session = Depends(get_db), user = Depends(get_current_user)):
    """Refreshes cash flow snapshot and runway forecast (Workflow 4)."""
    return WorkflowOrchestrator.update_treasury_analytics(db)


@router.get("/balances")
def get_treasury_balances(db: Session = Depends(get_db), user = Depends(get_current_user)):
    balances = db.query(AccountBalance).all()
    return {
        "balances": [
            {
                "balance_id": b.balance_id,
                "account_name": b.account_name,
                "provider": b.provider,
                "currency": b.currency,
                "current_balance": str(b.current_balance),
                "available_balance": str(b.available_balance),
                "pending_settlement": str(b.pending_settlement),
                "reserved_amount": str(b.reserved_amount),
                "last_updated_at": b.last_updated_at.isoformat() if b.last_updated_at else None
            }
            for b in balances
        ]
    }


@router.get("/forecast")
def get_treasury_forecast(db: Session = Depends(get_db), user = Depends(get_current_user)):
    forecast = db.query(TreasuryForecast).order_by(TreasuryForecast.created_at.desc()).first()
    if not forecast:
        return {"forecast": None}
    return {
        "forecast": {
            "forecast_id": forecast.forecast_id,
            "forecast_horizon_days": forecast.forecast_horizon_days,
            "predicted_runway_days": forecast.predicted_runway_days,
            "predicted_closing_balance": str(forecast.predicted_closing_balance),
            "liquidity_health_score": forecast.liquidity_health_score,
            "recommendations": forecast.recommendations
        }
    }
