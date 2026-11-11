"""
AURA Financial Platform - Database Engine & Session Management
"""
import logging
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.core.config import settings

logger = logging.getLogger("aura.database")

# Create engine
connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    echo=False
)

# Enable WAL mode and foreign keys for SQLite
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI database dependency yielding an isolated transaction session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initializes tables for all 10 AURA modules."""
    # Import all models to ensure they are registered with Base.metadata
    from app.models import (
        m01_financial_data,
        m02_payment_ops,
        m03_reconciliation,
        m04_risk_intelligence,
        m05_treasury,
        m06_disputes,
        m07_refunds,
        m08_credit,
        m09_governance,
        m10_copilot
    )
    logger.info("Creating all unified database tables across all 10 modules...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified.")
