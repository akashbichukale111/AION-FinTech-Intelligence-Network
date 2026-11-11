"""
PayPal Payments v2 Integration (Captures & Refunds)
"""
import uuid
import logging
from typing import Dict, Any, Optional
from decimal import Decimal
from app.integrations.paypal.client import paypal_client

logger = logging.getLogger("aura.paypal.payments")


class PayPalPaymentsService:
    @staticmethod
    async def get_capture(capture_id: str) -> Dict[str, Any]:
        """Retrieves details of a captured payment."""
        status_code, response = await paypal_client.request(
            method="GET",
            endpoint=f"/v2/payments/captures/{capture_id}"
        )
        return {
            "success": status_code == 200,
            "status_code": status_code,
            "capture": response
        }

    @staticmethod
    async def refund_capture(
        capture_id: str,
        amount: Optional[Decimal] = None,
        currency: str = "USD",
        note_to_payer: Optional[str] = None,
        invoice_id: Optional[str] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a full or partial refund for a captured payment.
        Guarantees idempotency via PayPal-Request-Id header.
        """
        if not idempotency_key:
            idempotency_key = f"ref_{uuid.uuid4().hex}"

        payload: Dict[str, Any] = {}
        if amount is not None:
            formatted_amount = f"{Decimal(str(amount)):.2f}"
            payload["amount"] = {
                "value": formatted_amount,
                "currency_code": currency.upper()
            }
        if note_to_payer:
            payload["note_to_payer"] = note_to_payer
        if invoice_id:
            payload["invoice_id"] = invoice_id

        status_code, response = await paypal_client.request(
            method="POST",
            endpoint=f"/v2/payments/captures/{capture_id}/refund",
            json_data=payload,
            idempotency_key=idempotency_key
        )

        if status_code not in (200, 201):
            logger.error(f"Failed to execute refund for capture {capture_id}: {status_code} - {response}")
            return {
                "success": False,
                "status_code": status_code,
                "error": response
            }

        return {
            "success": True,
            "refund_id": response.get("id"),
            "status": response.get("status"), # COMPLETED, PENDING, FAILED
            "capture_id": capture_id,
            "raw": response
        }

    @staticmethod
    async def get_refund(refund_id: str) -> Dict[str, Any]:
        """Retrieves details of a refund."""
        status_code, response = await paypal_client.request(
            method="GET",
            endpoint=f"/v2/payments/refunds/{refund_id}"
        )
        return {
            "success": status_code == 200,
            "status_code": status_code,
            "refund": response
        }
