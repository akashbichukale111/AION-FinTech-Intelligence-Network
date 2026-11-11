"""
Module 08: Credit, Repayment & Early Warning Intelligence Models
"""
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text
)
from app.core.database import Base
from app.core.types import Money, utcnow


class BorrowerProfile(Base):
    __tablename__ = "m08_borrower_profiles"

    borrower_id = Column(String(64), primary_key=True, index=True)
    borrower_ref = Column(String(64), unique=True, index=True)
    legal_name = Column(String(200), nullable=False)
    risk_rating = Column(String(10), default="A") # AAA, AA, A, BBB, BB, B, CCC
    
    total_exposure = Column(Money, default=0.0)
    available_credit = Column(Money, default=0.0)
    default_probability = Column(Money, default=0.015)
    
    early_warning_status = Column(String(50), default="NORMAL") # NORMAL, WATCHLIST, IMPAIRED, DEFAULT
    created_at = Column(DateTime(timezone=True), default=utcnow)


class CreditFacility(Base):
    __tablename__ = "m08_credit_facilities"

    facility_id = Column(String(64), primary_key=True, index=True)
    borrower_id = Column(String(64), ForeignKey("m08_borrower_profiles.borrower_id"), index=True)
    
    facility_type = Column(String(50), default="REVOLVING_LINE") # TERM_LOAN, REVOLVING_LINE
    credit_limit = Column(Money, nullable=False)
    utilized_amount = Column(Money, default=0.0)
    currency = Column(String(10), default="USD")
    interest_rate_pct = Column(Money, default=8.5)
    
    status = Column(String(50), default="ACTIVE")
    maturity_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class RepaymentSchedule(Base):
    __tablename__ = "m08_repayment_schedules"

    schedule_id = Column(String(64), primary_key=True)
    facility_id = Column(String(64), ForeignKey("m08_credit_facilities.facility_id"), index=True)
    
    installment_number = Column(Integer, default=1)
    due_date = Column(DateTime(timezone=True), nullable=False, index=True)
    amount_due = Column(Money, nullable=False)
    principal_due = Column(Money, nullable=False)
    interest_due = Column(Money, default=0.0)
    
    amount_paid = Column(Money, default=0.0)
    status = Column(String(50), default="SCHEDULED") # SCHEDULED, PAID, DELINQUENT, DEFAULTED
    linked_settlement_id = Column(String(128), nullable=True) # Module 01/02 reference
    paid_at = Column(DateTime(timezone=True), nullable=True)
