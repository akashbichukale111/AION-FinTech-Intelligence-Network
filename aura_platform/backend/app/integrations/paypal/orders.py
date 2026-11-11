"""
PayPal Orders v2 API Integration
Authoritative creation, retrieval, and capture of orders.
"""
import uuid
import logging
from typing import Dict, Any, Optional
from decimal import Decimal
from app.integrations.paypal.client import paypal_client

logger = logging.getLogger("aura.paypal.orders")


class PayPalOrdersService:
    @staticmethod
    async def create_order(
        amount: Decimal,
        currency: str = "USD",
        description: Optional[str] = None,
        custom_id: Optional[str] = None,
        return_url: str = "http://localhost:5173/?paypal=success",
        cancel_url: str = "http://localhost:5173/?paypal=cancel",
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates an authoritative PayPal Order (Orders v2).
        Returns order ID, status, and approval URL for the buyer.
        """
        if not idempotency_key:
            idempotency_key = f"order_{uuid.uuid4().hex}"

        formatted_amount = f"{Decimal(str(amount)):.2f}"

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": custom_id or f"unit_{uuid.uuid4().hex[:12]}",
                    "description": description or "AURA Intelligence Transaction",
                    "custom_id": custom_id,
                    "amount": {
                        "currency_code": currency.upper(),
                        "value": formatted_amount
                    }
                }
            ],
            "application_context": {
                "brand_name": "AURA Financial OS",
                "landing_page": "BILLING",
                "user_action": "PAY_NOW",
                "return_url": return_url,
                "cancel_url": cancel_url
            }
        }

        status_code, response = await paypal_client.request(
            method="POST",
            endpoint="/v2/checkout/orders",
            json_data=payload,
            idempotency_key=idempotency_key
        )

        if status_code not in (200, 201):
            logger.error(f"Failed to create PayPal order: {status_code} - {response}")
            return {
                "success": False,
                "status_code": status_code,
                "error": response
            }

        # Extract approval URL
        approval_url = None
        for link in response.get("links", []):
            if link.get("rel") == "approve":
                approval_url = link.get("href")
                break

        return {
            "success": True,
            "order_id": response.get("id"),
            "status": response.get("status"),
            "approval_url": approval_url,
            "raw": response
        }

    @staticmethod
    async def get_order(order_id: str) -> Dict[str, Any]:
        """Retrieves details of an existing PayPal order."""
        status_code, response = await paypal_client.request(
            method="GET",
            endpoint=f"/v2/checkout/orders/{order_id}"
        )
        return {
            "success": status_code == 200,
            "status_code": status_code,
            "order": response
        }

    @staticmethod
    async def capture_order(order_id: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Authoritatively captures an approved PayPal order.
        Guarantees idempotency to avoid double capturing.
        """
        if not idempotency_key:
            idempotency_key = f"cap_{uuid.uuid4().hex}"

        status_code, response = await paypal_client.request(
            method="POST",
            endpoint=f"/v2/checkout/orders/{order_id}/capture",
            json_data={},
            idempotency_key=idempotency_key
        )

        if status_code not in (200, 201):
            logger.error(f"Failed to capture order {order_id}: {status_code} - {response}")
            return {
                "success": False,
                "status_code": status_code,
                "error": response
            }

        # Extract capture reference
        capture_id = None
        purchase_units = response.get("purchase_units", [])
        if purchase_units:
            payments = purchase_units[0].get("payments", {})
            captures = payments.get("captures", [])
            if captures:
                capture_id = captures[0].get("id")

        return {
            "success": True,
            "order_id": response.get("id"),
            "status": response.get("status"),
            "capture_id": capture_id,
            "raw": response
        }
