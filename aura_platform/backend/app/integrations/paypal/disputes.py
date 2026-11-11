"""
PayPal Customer Disputes API Integration
Handles dispute listing, details retrieval, evidence preparation, and actions.
"""
import logging
from typing import Dict, Any, List, Optional
from app.integrations.paypal.client import paypal_client

logger = logging.getLogger("aura.paypal.disputes")


class PayPalDisputesService:
    @staticmethod
    async def list_disputes(
        dispute_state: Optional[str] = None,
        page_size: int = 50
    ) -> Dict[str, Any]:
        """
        Lists disputes from PayPal Disputes API (/v1/customer/disputes).
        """
        params: Dict[str, Any] = {"page_size": min(page_size, 50)}
        if dispute_state:
            params["dispute_state"] = dispute_state

        status_code, response = await paypal_client.request(
            method="GET",
            endpoint="/v1/customer/disputes",
            params=params
        )

        if status_code != 200:
            logger.warning(
                f"PayPal Disputes API returned {status_code}: {response}. "
                "Disputes API requires DISPUTES permission in PayPal Developer Dashboard."
            )
            return {
                "success": False,
                "status_code": status_code,
                "error": response,
                "items": []
            }

        return {
            "success": True,
            "items": response.get("items", []),
            "total_items": response.get("total_items", len(response.get("items", []))),
            "links": response.get("links", [])
        }

    @staticmethod
    async def get_dispute(dispute_id: str) -> Dict[str, Any]:
        """Retrieves comprehensive details of a single dispute."""
        status_code, response = await paypal_client.request(
            method="GET",
            endpoint=f"/v1/customer/disputes/{dispute_id}"
        )
        return {
            "success": status_code == 200,
            "status_code": status_code,
            "dispute": response
        }

    @staticmethod
    async def provide_evidence(
        dispute_id: str,
        evidence_type: str,
        notes: str,
        tracking_info: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Submits evidence for an open dispute (/v1/customer/disputes/{id}/provide-evidence).
        """
        payload: Dict[str, Any] = {
            "evidence_type": evidence_type, # e.g. PROOF_OF_DELIVERY, PROOF_OF_REFUND
            "notes": notes
        }
        if tracking_info:
            payload["tracking_info"] = tracking_info

        status_code, response = await paypal_client.request(
            method="POST",
            endpoint=f"/v1/customer/disputes/{dispute_id}/provide-evidence",
            json_data=payload
        )

        return {
            "success": status_code in (200, 204),
            "status_code": status_code,
            "response": response
        }

    @staticmethod
    async def accept_claim(dispute_id: str, note_to_payer: Optional[str] = None) -> Dict[str, Any]:
        """Accepts buyer liability for a dispute."""
        payload: Dict[str, Any] = {}
        if note_to_payer:
            payload["note_to_payer"] = note_to_payer

        status_code, response = await paypal_client.request(
            method="POST",
            endpoint=f"/v1/customer/disputes/{dispute_id}/accept-claim",
            json_data=payload
        )

        return {
            "success": status_code in (200, 204),
            "status_code": status_code,
            "response": response
        }
