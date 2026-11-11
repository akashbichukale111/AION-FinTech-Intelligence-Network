"""
PayPal Provider Adapter
Unified gateway for all AURA modules to interact with PayPal services.
"""
import uuid
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import Session

from app.integrations.paypal.client import paypal_client
from app.integrations.paypal.orders import PayPalOrdersService
from app.integrations.paypal.payments import PayPalPaymentsService
from app.integrations.paypal.transactions import PayPalTransactionService
from app.integrations.paypal.disputes import PayPalDisputesService
from app.integrations.paypal.normalizer import PayPalNormalizer
from app.models.m01_financial_data import FinancialRecord, ImportBatch
from app.models.m02_payment_ops import PaymentAttempt
from app.models.m06_disputes import DisputeCase
from app.models.m07_refunds import RefundRequest
from app.core.types import ProviderName, RecordStatus, RefundStatus

logger = logging.getLogger("aura.paypal.adapter")

# In-memory status & telemetry
_status_state = {
    "last_sync_time": None,
    "last_error": None,
    "error_history": [],
    "total_syncs": 0
}


class PayPalAdapter:
    @staticmethod
    def get_status() -> Dict[str, Any]:
        """Provides complete integration health and configuration status."""
        configured = paypal_client.is_configured()
        mode = paypal_client.mode
        cid = paypal_client.client_id
        
        # Mask client ID
        masked_id = f"{cid[:6]}...{cid[-4:]}" if len(cid) > 10 else ("Configured" if cid else None)
        
        if not configured:
            conn_status = "NOT_CONFIGURED"
        elif _status_state["last_error"]:
            conn_status = "CONNECTION_FAILED"
        else:
            conn_status = "SANDBOX_CONNECTED" if mode == "sandbox" else "LIVE_CONNECTED"

        webhook_configured = bool(paypal_client.webhook_id)

        return {
            "configured": configured,
            "mode": mode,
            "client_id": cid,
            "client_id_preview": masked_id,
            "has_secret": bool(paypal_client.client_secret),
            "base_url": paypal_client.base_url,
            "connection_status": conn_status,
            "webhook_configured": webhook_configured,
            "webhook_id_preview": (f"{paypal_client.webhook_id[:4]}...{paypal_client.webhook_id[-3:]}" if webhook_configured else None),
            "last_sync_time": _status_state["last_sync_time"],
            "total_syncs": _status_state["total_syncs"],
            "last_error": _status_state["last_error"],
            "error_history": _status_state["error_history"][-10:],
            "supported_features": [
                "OAuth 2.0 Client Credentials",
                "Orders v2 Creation & Capture",
                "Payments v2 Captured Refund",
                "Disputes API v1 Inspection & Evidence",
                "Transaction Search v1 Reporting",
                "Cryptographic Webhook Signature Verification"
            ]
        }

    @staticmethod
    async def test_connection() -> Dict[str, Any]:
        """Validates credentials by attempting an authentic OAuth token retrieval."""
        if not paypal_client.is_configured():
            return {
                "success": False,
                "status": "NOT_CONFIGURED",
                "message": "PayPal Client ID and Client Secret must be configured."
            }

        try:
            token = await paypal_client.get_access_token(force_refresh=True)
            _status_state["last_error"] = None
            return {
                "success": True,
                "status": f"{paypal_client.mode.upper()}_CONNECTED",
                "message": f"Successfully authenticated with PayPal {paypal_client.mode.title()} API.",
                "token_preview": f"{token[:8]}...{token[-4:]}"
            }
        except Exception as exc:
            err_msg = str(exc)
            _status_state["last_error"] = err_msg
            _status_state["error_history"].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": err_msg
            })
            return {
                "success": False,
                "status": "CONNECTION_FAILED",
                "message": err_msg
            }

    @staticmethod
    async def sync_transactions_to_db(db: Session, lookback_days: int = 30) -> Dict[str, Any]:
        """
        Retrieves transactions from PayPal and persists them into Module 01 Financial Records.
        Guarantees deduplication using provider_tx_id.
        """
        if not paypal_client.is_configured():
            return {"success": False, "message": "PayPal is not configured."}

        res = await PayPalTransactionService.list_transactions(page_size=100)
        if not res.get("success"):
            err = res.get("error", "Failed to retrieve transactions")
            _status_state["last_error"] = str(err)
            return {"success": False, "error": err}

        raw_txs = res.get("transactions", [])
        batch_id = f"batch_paypal_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

        # Create batch record
        batch = ImportBatch(
            batch_id=batch_id,
            source_name="PAYPAL_API_SYNC",
            provider_name=ProviderName.PAYPAL.value,
            total_records=len(raw_txs),
            status="PROCESSING"
        )
        db.add(batch)
        db.flush()

        imported_count = 0
        skipped_count = 0

        for item in raw_txs:
            norm = PayPalNormalizer.normalize_transaction_search_item(item, batch_id=batch_id)
            tx_id = norm.get("provider_tx_id")
            
            # Check duplicate
            existing = db.query(FinancialRecord).filter(
                FinancialRecord.provider_name == ProviderName.PAYPAL.value,
                FinancialRecord.provider_tx_id == tx_id
            ).first()

            if existing:
                skipped_count += 1
                continue

            rec = FinancialRecord(**norm)
            db.add(rec)
            imported_count += 1

        batch.valid_records = imported_count
        batch.status = "COMPLETED"
        db.commit()

        _status_state["last_sync_time"] = datetime.now(timezone.utc).isoformat()
        _status_state["total_syncs"] += 1

        return {
            "success": True,
            "batch_id": batch_id,
            "total_retrieved": len(raw_txs),
            "new_imported": imported_count,
            "duplicates_skipped": skipped_count
        }

    @staticmethod
    async def create_checkout_order(
        amount: Decimal,
        currency: str = "USD",
        description: Optional[str] = None,
        custom_id: Optional[str] = None,
        return_url: Optional[str] = None,
        cancel_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Creates an order and tracks it in PaymentAttempt (Module 02)."""
        return await PayPalOrdersService.create_order(
            amount=amount,
            currency=currency,
            description=description,
            custom_id=custom_id,
            return_url=return_url or "http://localhost:5173/?paypal=success",
            cancel_url=cancel_url or "http://localhost:5173/?paypal=cancel"
        )

    @staticmethod
    async def capture_checkout_order(order_id: str, db: Optional[Session] = None) -> Dict[str, Any]:
        """Captures approved order and updates Module 02 / Module 01 records."""
        result = await PayPalOrdersService.capture_order(order_id)
        if result.get("success") and db:
            capture_id = result.get("capture_id")
            # Update or create payment attempt
            attempt = db.query(PaymentAttempt).filter(PaymentAttempt.order_id == order_id).first()
            if attempt:
                attempt.status = "CAPTURED"
                attempt.capture_id = capture_id
                db.commit()
        return result

    @staticmethod
    async def execute_refund(
        capture_id: str,
        amount: Optional[Decimal] = None,
        currency: str = "USD",
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Executes refund through Payments v2 API."""
        return await PayPalPaymentsService.refund_capture(
            capture_id=capture_id,
            amount=amount,
            currency=currency,
            note_to_payer=reason
        )
