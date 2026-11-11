"""
PayPal Transaction Search & Sync Service
Retrieves transaction history from /v1/reporting/transactions and normalizes it.
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
from app.integrations.paypal.client import paypal_client

logger = logging.getLogger("aura.paypal.transactions")


class PayPalTransactionService:
    @staticmethod
    async def list_transactions(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        page_size: int = 100,
        page: int = 1
    ) -> Dict[str, Any]:
        """
        Retrieves transactions from the PayPal Transaction Search API.
        Default queries the last 30 days.
        """
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=30)

        # PayPal expects ISO 8601 strings: YYYY-MM-DDTHH:MM:SSZ
        params = {
            "start_date": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_size": min(page_size, 500),
            "page": page,
            "fields": "all"
        }

        status_code, response = await paypal_client.request(
            method="GET",
            endpoint="/v1/reporting/transactions",
            params=params
        )

        if status_code != 200:
            logger.warning(
                f"Transaction search API returned {status_code}: {response}. "
                "Note: Reporting API requires TRANSACTION_SEARCH permission on the PayPal App."
            )
            return {
                "success": False,
                "status_code": status_code,
                "error": response,
                "transactions": []
            }

        transaction_details = response.get("transaction_details", [])
        return {
            "success": True,
            "total_items": response.get("total_items", len(transaction_details)),
            "total_pages": response.get("total_pages", 1),
            "transactions": transaction_details,
            "raw": response
        }
