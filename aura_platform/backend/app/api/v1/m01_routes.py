"""
API Routes: Module 01 - Unified Financial Data Intelligence
"""
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.m01_financial_data import FinancialRecord, ImportBatch, DataQualityReport, DataSource
from app.core.security import get_current_user

router = APIRouter(prefix="/financial-data", tags=["Module 01 - Financial Data"])


@router.get("/records")
def list_financial_records(
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=500),
    currency: Optional[str] = None,
    record_type: Optional[str] = None
):
    query = db.query(FinancialRecord)
    if currency:
        query = query.filter(FinancialRecord.currency == currency.upper())
    if record_type:
        query = query.filter(FinancialRecord.record_type == record_type.upper())
    records = query.order_by(FinancialRecord.transaction_time.desc()).limit(limit).all()

    return {
        "total": len(records),
        "records": [
            {
                "record_id": r.record_id,
                "provider_name": r.provider_name,
                "provider_tx_id": r.provider_tx_id,
                "amount": str(r.amount),
                "currency": r.currency,
                "record_type": r.record_type,
                "status": r.status,
                "payer_email": r.payer_email,
                "payer_name": r.payer_name,
                "description": r.description,
                "transaction_time": r.transaction_time.isoformat() if r.transaction_time else None
            }
            for r in records
        ]
    }


@router.get("/batches")
def list_import_batches(db: Session = Depends(get_db), user = Depends(get_current_user)):
    batches = db.query(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(20).all()
    return {
        "batches": [
            {
                "batch_id": b.batch_id,
                "source_name": b.source_name,
                "provider_name": b.provider_name,
                "total_records": b.total_records,
                "valid_records": b.valid_records,
                "quality_score": str(b.quality_score),
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else None
            }
            for b in batches
        ]
    }


@router.get("/quality-reports")
def list_quality_reports(db: Session = Depends(get_db), user = Depends(get_current_user)):
    reports = db.query(DataQualityReport).order_by(DataQualityReport.created_at.desc()).limit(10).all()
    return {
        "reports": [
            {
                "report_id": r.report_id,
                "batch_id": r.batch_id,
                "completeness_score": str(r.completeness_score),
                "validity_score": str(r.validity_score),
                "uniqueness_score": str(r.uniqueness_score),
                "overall_quality_score": str(r.overall_quality_score),
                "anomaly_count": r.anomaly_count,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in reports
        ]
    }
