"""
AURA Financial Platform - Core Types & Enums
Exact decimals for financial calculations, UTC timestamps, and shared enums.
"""
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from sqlalchemy.types import TypeDecorator, String, Numeric


def utcnow() -> datetime:
    """Return current UTC time with timezone awareness."""
    return datetime.now(timezone.utc)


class Money(TypeDecorator):
    """
    Exact monetary type.
    Stores as high-precision Numeric(20, 4) in PostgreSQL and String(40) in SQLite
    to guarantee zero floating point precision loss.
    """
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(40))
        return dialect.type_descriptor(Numeric(20, 4, asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        v = Decimal(str(value))
        return str(v) if dialect.name == "sqlite" else v

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(str(value))


class FinancialRecordType(str, Enum):
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    PAYOUT = "PAYOUT"
    TRANSFER = "TRANSFER"
    FEE = "FEE"
    CHARGEBACK = "CHARGEBACK"
    DISPUTE_REVERSAL = "DISPUTE_REVERSAL"
    ADJUSTMENT = "ADJUSTMENT"


class RecordStatus(str, Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"
    CANCELLED = "CANCELLED"


class ProviderName(str, Enum):
    PAYPAL = "PAYPAL"
    STRIPE = "STRIPE"
    ADYEN = "ADYEN"
    INTERNAL_LEDGER = "INTERNAL_LEDGER"
    BANK_WIRE = "BANK_WIRE"


class DisputeStatus(str, Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    SUBMITTED = "SUBMITTED"
    WON = "WON"
    LOST = "LOST"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


class RefundStatus(str, Enum):
    REQUESTED = "REQUESTED"
    ELIGIBLE = "ELIGIBLE"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ReconStatus(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    TOLERANCE_MATCH = "TOLERANCE_MATCH"
    UNMATCHED = "UNMATCHED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    FEE_DISCREPANCY = "FEE_DISCREPANCY"
    EXCEPTION = "EXCEPTION"


class GovernanceActionType(str, Enum):
    EXECUTE_PAYPAL_REFUND = "EXECUTE_PAYPAL_REFUND"
    SUBMIT_DISPUTE_EVIDENCE = "SUBMIT_DISPUTE_EVIDENCE"
    ACCEPT_DISPUTE = "ACCEPT_DISPUTE"
    TRIGGER_RECON_BATCH = "TRIGGER_RECON_BATCH"
    OVERRIDE_RISK_BLOCK = "OVERRIDE_RISK_BLOCK"
    UPDATE_CREDIT_FACILITY = "UPDATE_CREDIT_FACILITY"
    UPDATE_ROUTING_RULE = "UPDATE_ROUTING_RULE"
