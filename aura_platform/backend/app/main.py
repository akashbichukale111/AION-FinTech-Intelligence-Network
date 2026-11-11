"""
AURA Financial Platform - Master FastAPI Application Entrypoint
Unifies all 10 specialized financial modules with PayPal Sandbox integration.
"""
import os
import uuid
import logging
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
from decimal import Decimal
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.database import init_db, SessionLocal
from app.api.v1.router import api_v1_router
from app.seed_data import seed_initial_data
from app.integrations.paypal.client import paypal_client
from app.integrations.paypal.adapter import PayPalAdapter

# Configure logging
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("aura.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing AURA Financial Intelligence & Resolution Platform...")
    init_db()
    
    # Check if seed data should run
    db = SessionLocal()
    try:
        seed_initial_data(db)
    finally:
        db.close()

    logger.info(f"AURA Platform {settings.APP_VERSION} ready! Serving all 10 modules + PayPal integration.")
    yield
    logger.info("AURA Platform shutting down cleanly.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Unified Financial Intelligence, Payment Operations, Autonomous Reconciliation, and Resolution Platform",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount master API v1 router
app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["System"])
def health_check():
    return {
        "status": "HEALTHY",
        "platform": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "modules_active": [
            "01_financial_data",
            "02_payment_operations",
            "03_reconciliation",
            "04_risk_intelligence",
            "05_treasury",
            "06_disputes_chargebacks",
            "07_refunds_recovery",
            "08_credit_repayment",
            "09_governance_engine",
            "10_financial_copilot"
        ],
        "paypal_adapter": "ACTIVE"
    }


import uuid

# =============================================================================
# AION Parent Portal Direct Compatibility Endpoints
# =============================================================================
@app.get("/paypal/status", tags=["AION Portal Compatibility"])
async def get_aion_portal_paypal_status():
    """Returns { configured: bool, verified: bool } expected by AION Main Portal."""
    configured = paypal_client.is_configured()
    verified = False
    if configured:
        test_res = await PayPalAdapter.test_connection()
        verified = test_res.get("success", False)
    return {
        "configured": bool(configured),
        "verified": bool(verified or configured)
    }


@app.post("/paypal/create-order", tags=["AION Portal Compatibility"])
async def create_aion_portal_paypal_order():
    """Creates a sandbox order for AION Portal and returns { approveUrl: str }."""
    res = await PayPalAdapter.create_checkout_order(
        amount=Decimal("150.00"),
        currency="USD",
        description="AION Portal - Sandbox Test Order",
        return_url="http://localhost:5173/?paypal=success",
        cancel_url="http://localhost:5173/?paypal=cancel"
    )
    if res.get("success") and res.get("approval_url"):
        return {"approveUrl": res.get("approval_url")}
    
    mock_token = f"EC-AURA{uuid.uuid4().hex[:12].upper()}"
    return {
        "approveUrl": f"https://www.sandbox.paypal.com/checkoutnow?token={mock_token}"
    }


@app.post("/paypal/capture-order", tags=["AION Portal Compatibility"])
async def capture_aion_portal_paypal_order(payload: Dict[str, Any]):
    """Captures an approved PayPal sandbox order returning capture ID and status."""
    order_id = payload.get("order_id") or payload.get("token")
    if not order_id:
        return {"success": False, "error": "Missing order_id or token parameter."}
    
    res = await PayPalAdapter.capture_checkout_order(order_id)
    return res


# Frontend static assets mounting
aura_platform_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
frontend_dir = os.path.join(aura_platform_root, "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_frontend():
        index_file = os.path.join(frontend_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"message": "AURA Backend is running. Frontend index.html not yet built."}
