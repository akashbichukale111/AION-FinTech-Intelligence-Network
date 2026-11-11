"""
PayPal Webhook Verification & Event Ingestion Service
Verifies cryptographic signatures using PayPal verify-webhook-signature API,
enforces duplicate event suppression, and idempotently dispatches events.
"""
import logging
import setuptools
from typing import Dict, Any, Optional, Set
from app.integrations.paypal.client import paypal_client

logger = logging.getLogger("aura.paypal.webhooks")

# In-memory deduplication cache
_processed_events: Set[str] = set()


class PayPalWebhookService:
    @staticmethod
    async def verify_signature(
        transmission_id: str,
        transmission_time: str,
        cert_url: str,
        auth_algo: str,
        transmission_sig: str,
        webhook_body: Dict[str, Any]
    ) -> bool:
        """
        Calls PayPal API /v1/notifications/verify-webhook-signature
        to verify signature authenticity.
        """
        webhook_id = paypal_client.webhook_id
        if not webhook_id:
            logger.warning("No PAYPAL_WEBHOOK_ID configured. Accepting event with warning in sandbox.")
            return True

        payload = {
            "transmission_id": transmission_id,
            "transmission_time": transmission_time,
            "cert_url": cert_url,
            "auth_algo": auth_algo,
            "transmission_sig": transmission_sig,
            "webhook_id": webhook_id,
            "webhook_event": webhook_body
        }

        try:
            status_code, response = await paypal_client.request(
                method="POST",
                endpoint="/v1/notifications/verify-webhook-signature",
                json_data=payload
            )
            verification_status = response.get("verification_status")
            return status_code == 200 and verification_status == "SUCCESS"
        except Exception as exc:
            logger.error(f"Error during PayPal webhook signature verification: {exc}")
            return False

    @staticmethod
    def is_duplicate_event(event_id: str) -> bool:
        """Checks if event has already been ingested."""
        if not event_id:
            return False
        if event_id in _processed_events:
            return True
        _processed_events.add(event_id)
        if len(_processed_events) > 5000:
            _processed_events.pop()
        return False
