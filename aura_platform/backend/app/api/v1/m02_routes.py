"""
API Routes: Module 02 - Payment Operations & Routing
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.m02_payment_ops import PaymentAttempt, ProviderPerformanceMetric, RoutingRecommendation
from app.core.security import get_current_user

router = APIRouter(prefix="/payments", tags=["Module 02 - Payments"])


@router.get("/attempts")
def list_payment_attempts(
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=500)
):
    attempts = db.query(PaymentAttempt).order_by(PaymentAttempt.created_at.desc()).limit(limit).all()
    return {
        "total": len(attempts),
        "attempts": [
            {
                "attempt_id": a.attempt_id,
                "order_id": a.order_id,
                "capture_id": a.capture_id,
                "amount": str(a.amount),
                "currency": a.currency,
                "provider": a.provider,
                "status": a.status,
                "payer_email": a.payer_email,
                "latency_ms": a.latency_ms,
                "retry_count": a.retry_count,
                "created_at": a.created_at.isoformat() if a.created_at else None
            }
            for a in attempts
        ]
    }


@router.get("/performance")
def get_provider_performance(db: Session = Depends(get_db), user = Depends(get_current_user)):
    metrics = db.query(ProviderPerformanceMetric).all()
    return {
        "providers": [
            {
                "provider_name": m.provider_name,
                "total_volume": str(m.total_volume),
                "total_count": m.total_count,
                "success_count": m.success_count,
                "failure_count": m.failure_count,
                "success_rate": str(m.success_rate),
                "avg_latency_ms": m.avg_latency_ms,
                "health_status": m.health_status
            }
            for m in metrics
        ]
    }


@router.get("/routing-recommendations")
def get_routing_recommendations(db: Session = Depends(get_db), user = Depends(get_current_user)):
    recs = db.query(RoutingRecommendation).all()
    return {
        "recommendations": [
            {
                "recommendation_id": r.recommendation_id,
                "currency": r.currency,
                "recommended_provider": r.recommended_provider,
                "expected_success_rate": str(r.expected_success_rate),
                "estimated_cost_basis": str(r.estimated_cost_basis),
                "rationale": r.rationale
            }
            for r in recs
        ]
    }
