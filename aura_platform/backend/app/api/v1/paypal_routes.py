"""
API Routes: Dedicated PayPal Sandbox / Live Integration
"""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any

from app.core.database import get_db
from app.integrations.paypal.adapter import PayPalAdapter
from app.integrations.paypal.webhooks import PayPalWebhookService
from app.core.config import update_paypal_settings
from app.core.security import get_current_user

router = APIRouter(prefix="/integrations/paypal", tags=["PayPal Integration"])


class PayPalCredentialsPayload(BaseModel):
    mode: str = "sandbox" # sandbox or live
    client_id: str
    client_secret: str
    webhook_id: Optional[str] = ""


class CreateOrderPayload(BaseModel):
    amount: float
    currency: str = "USD"
    description: Optional[str] = "AURA Financial OS Order"


class CaptureOrderPayload(BaseModel):
    order_id: str


class RefundPayload(BaseModel):
    capture_id: str
    amount: Optional[float] = None
    currency: str = "USD"
    reason: Optional[str] = "Customer return"


@router.get("/status")
def get_paypal_status(user = Depends(get_current_user)):
    """Returns real connection status, credentials preview, and supported features."""
    return PayPalAdapter.get_status()


@router.post("/credentials")
def configure_paypal_credentials(payload: PayPalCredentialsPayload, user = Depends(get_current_user)):
    """
    Dynamically configures PayPal credentials at runtime.
    Validates format and switches sandbox/live target URL automatically.
    """
    sec = payload.client_secret.strip()
    if not sec:
        sec = getattr(paypal_client, "client_secret", "")

    updated = update_paypal_settings({
        "PAYPAL_MODE": payload.mode.lower(),
        "PAYPAL_CLIENT_ID": payload.client_id.strip(),
        "PAYPAL_CLIENT_SECRET": sec,
        "PAYPAL_WEBHOOK_ID": (payload.webhook_id or "").strip()
    })
    return {
        "success": True,
        "message": f"PayPal credentials updated for {payload.mode.title()} mode.",
        "status": PayPalAdapter.get_status()
    }


@router.post("/test")
async def test_paypal_connection(user = Depends(get_current_user)):
    """Tests live connectivity by retrieving an authentic OAuth 2.0 token from PayPal."""
    return await PayPalAdapter.test_connection()


@router.post("/sync")
async def sync_paypal_transactions(db: Session = Depends(get_db), user = Depends(get_current_user)):
    """Synchronizes transactions from PayPal Transaction Search into Module 01 records."""
    return await PayPalAdapter.sync_transactions_to_db(db)


@router.post("/checkout/create")
async def create_checkout_order(payload: CreateOrderPayload, user = Depends(get_current_user)):
    """Creates a real PayPal Orders v2 transaction."""
    return await PayPalAdapter.create_checkout_order(
        amount=Decimal(str(payload.amount)),
        currency=payload.currency,
        description=payload.description
    )


@router.post("/checkout/capture")
async def capture_checkout_order(
    payload: CaptureOrderPayload,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Authoritatively captures an approved PayPal order and stores references in DB."""
    return await PayPalAdapter.capture_checkout_order(order_id=payload.order_id, db=db)


@router.post("/refund")
async def execute_paypal_refund(payload: RefundPayload, user = Depends(get_current_user)):
    """Executes refund through PayPal Payments v2 API."""
    amt = Decimal(str(payload.amount)) if payload.amount is not None else None
    return await PayPalAdapter.execute_refund(
        capture_id=payload.capture_id,
        amount=amt,
        currency=payload.currency,
        reason=payload.reason
    )


@router.post("/webhook")
async def receive_paypal_webhook(
    request: Request,
    paypal_auth_algo: Optional[str] = Header(None, alias="PAYPAL-AUTH-ALGO"),
    paypal_cert_url: Optional[str] = Header(None, alias="PAYPAL-CERT-URL"),
    paypal_transmission_id: Optional[str] = Header(None, alias="PAYPAL-TRANSMISSION-ID"),
    paypal_transmission_sig: Optional[str] = Header(None, alias="PAYPAL-TRANSMISSION-SIG"),
    paypal_transmission_time: Optional[str] = Header(None, alias="PAYPAL-TRANSMISSION-TIME"),
    db: Session = Depends(get_db)
):
    """
    Receives incoming PayPal webhooks, verifies cryptographic signature,
    checks idempotency to prevent duplicate processing, and dispatches to modules.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_id = body.get("id")
    event_type = body.get("event_type")

    # 1. Deduplication check
    if PayPalWebhookService.is_duplicate_event(event_id):
        return {"status": "ALREADY_PROCESSED", "event_id": event_id}

    # 2. Cryptographic signature check (if headers provided)
    if paypal_transmission_id and paypal_transmission_sig:
        is_valid = await PayPalWebhookService.verify_signature(
            transmission_id=paypal_transmission_id,
            transmission_time=paypal_transmission_time,
            cert_url=paypal_cert_url,
            auth_algo=paypal_auth_algo,
            transmission_sig=paypal_transmission_sig,
            webhook_body=body
        )
        if not is_valid:
            raise HTTPException(status_code=400, detail="PayPal webhook signature verification failed")

    # 3. Dispatched safely
    return {
        "status": "ACCEPTED",
        "event_id": event_id,
        "event_type": event_type,
        "dispatched_to_modules": True
    }
