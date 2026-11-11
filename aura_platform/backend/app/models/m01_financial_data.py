"""
Module 01: Unified Financial Data Intelligence Models
Provides normalized financial transaction records, ingestion batches, and data quality reports.
"""
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text, Enum as SQLEnum
)
from sqlalchemy.orm import relationship
from app.core.database import Base
from app.core.types import Money, utcnow, FinancialRecordType, RecordStatus, ProviderName


class DataSource(Base):
    __tablename__ = "m01_data_sources"

    id = Column(Integer, primary_key=True, index=True)
    source_name = Column(String(100), unique=True, nullable=False, index=True)
    provider = Column(String(50), default=ProviderName.PAYPAL.value, nullable=False)
    source_type = Column(String(50), default="API", nullable=False)  # API, CSV, SFTP
    status = Column(String(50), default="ACTIVE", nullable=False)
    sync_frequency = Column(String(50), default="REALTIME")
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    config_metadata = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class ImportBatch(Base):
    __tablename__ = "m01_import_batches"

    batch_id = Column(String(64), primary_key=True, index=True)
    source_name = Column(String(100), nullable=False, index=True)
    provider_name = Column(String(50), default=ProviderName.PAYPAL.value)
    filename = Column(String(255), nullable=True)
    total_records = Column(Integer, default=0)
    valid_records = Column(Integer, default=0)
    failed_records = Column(Integer, default=0)
    quality_score = Column(Money, default=100.0)
    status = Column(String(50), default="PROCESSING")  # PROCESSING, COMPLETED, FAILED
    error_summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    records = relationship("FinancialRecord", back_populates="batch", cascade="all, delete-orphan")


class FinancialRecord(Base):
    __tablename__ = "m01_financial_records"

    record_id = Column(String(64), primary_key=True, index=True)
    batch_id = Column(String(64), ForeignKey("m01_import_batches.batch_id"), nullable=True, index=True)
    provider_name = Column(String(50), default=ProviderName.PAYPAL.value, index=True)
    provider_tx_id = Column(String(128), unique=True, index=True, nullable=True)  # Avoid duplicate imports
    
    amount = Column(Money, nullable=False)
    currency = Column(String(10), default="USD", nullable=False, index=True)
    record_type = Column(String(50), default=FinancialRecordType.PAYMENT.value, index=True)
    status = Column(String(50), default=RecordStatus.SETTLED.value, index=True)
    
    payer_id = Column(String(128), nullable=True, index=True)
    payer_email = Column(String(255), nullable=True, index=True)
    payer_name = Column(String(255), nullable=True)
    
    merchant_id = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    
    raw_payload = Column(JSON, default=dict)
    normalized_at = Column(DateTime(timezone=True), default=utcnow)
    transaction_time = Column(DateTime(timezone=True), default=utcnow, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    batch = relationship("ImportBatch", back_populates="records")

    __table_args__ = (
        Index("idx_m01_rec_tx_prov", "provider_name", "provider_tx_id"),
        Index("idx_m01_rec_time_curr", "transaction_time", "currency"),
    )


class DataQualityReport(Base):
    __tablename__ = "m01_data_quality_reports"

    report_id = Column(String(64), primary_key=True)
    batch_id = Column(String(64), index=True)
    completeness_score = Column(Money, default=100.0)
    validity_score = Column(Money, default=100.0)
    uniqueness_score = Column(Money, default=100.0)
    overall_quality_score = Column(Money, default=100.0)
    anomaly_count = Column(Integer, default=0)
    issues_detected = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=utcnow)
