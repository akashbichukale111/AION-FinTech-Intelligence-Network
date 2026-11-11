"""
Module 05: Cash Flow & Treasury Intelligence Models
"""
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, JSON, ForeignKey, Index, Text
)
from app.core.database import Base
from app.core.types import Money, utcnow


class AccountBalance(Base):
    __tablename__ = "m05_account_balances"

    balance_id = Column(String(64), primary_key=True)
    account_name = Column(String(100), default="PayPal Sandbox Main Account", index=True)
    provider = Column(String(50), default="PAYPAL", index=True)
    currency = Column(String(10), default="USD", index=True)
    
    current_balance = Column(Money, default=0.0)
    available_balance = Column(Money, default=0.0)
    pending_settlement = Column(Money, default=0.0)
    reserved_amount = Column(Money, default=0.0)
    
    last_updated_at = Column(DateTime(timezone=True), default=utcnow)


class CashFlowSnapshot(Base):
    __tablename__ = "m05_cash_flow_snapshots"

    snapshot_id = Column(String(64), primary_key=True)
    period_date = Column(DateTime(timezone=True), default=utcnow, index=True)
    
    total_inflow = Column(Money, default=0.0)
    total_outflow = Column(Money, default=0.0)
    net_cash_flow = Column(Money, default=0.0)
    
    inflow_by_provider = Column(JSON, default=dict)
    outflow_by_category = Column(JSON, default=dict) # refunds, fees, operational
    
    currency = Column(String(10), default="USD")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class TreasuryForecast(Base):
    __tablename__ = "m05_treasury_forecasts"

    forecast_id = Column(String(64), primary_key=True)
    forecast_horizon_days = Column(Integer, default=30)
    predicted_runway_days = Column(Integer, default=180)
    
    predicted_closing_balance = Column(Money, default=0.0)
    confidence_interval_low = Column(Money, default=0.0)
    confidence_interval_high = Column(Money, default=0.0)
    
    liquidity_health_score = Column(Integer, default=92) # 0-100
    recommendations = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=utcnow)
