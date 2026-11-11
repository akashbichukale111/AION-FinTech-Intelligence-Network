"""
PayPal Data Normalizer
Converts raw PayPal responses and search payloads into canonical AURA models.
"""
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from app.core.types import FinancialRecordType, RecordStatus, ProviderName, DisputeStatus, RefundStatus


class PayPalNormalizer:
    @staticmethod
    def normalize_transaction_search_item(item: Dict[str, Any], batch_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Normalizes an item from /v1/reporting/transactions into a Module 01 FinancialRecord dict.
        """
        tx_info = item.get("transaction_info", {})
        payer_info = item.get("payer_info", {})
        
        tx_id = tx_info.get("transaction_id", f"pp_tx_{uuid.uuid4().hex[:12]}")
        amount_val = Decimal(str(tx_info.get("transaction_amount", {}).get("value", "0.00")))
        currency = tx_info.get("transaction_amount", {}).get("currency_code", "USD")
        
        # Determine status
        raw_status = tx_info.get("transaction_status", "S")
        status_map = {
            "S": RecordStatus.SETTLED.value,
            "P": RecordStatus.PENDING.value,
            "V": RecordStatus.REVERSED.value,
            "D": RecordStatus.FAILED.value
        }
        status = status_map.get(raw_status, RecordStatus.SETTLED.value)

        # Determine type
        event_code = tx_info.get("transaction_event_code", "T0000")
        if event_code.startswith("T11"):
            rec_type = FinancialRecordType.REFUND.value
        elif event_code.startswith("T12"):
            rec_type = FinancialRecordType.DISPUTE_REVERSAL.value
        elif event_code.startswith("T01") or event_code.startswith("T00"):
            rec_type = FinancialRecordType.PAYMENT.value
        else:
            rec_type = FinancialRecordType.PAYMENT.value

        # Parse date
        init_date_str = tx_info.get("transaction_initiation_date")
        tx_time = datetime.now(timezone.utc)
        if init_date_str:
            try:
                tx_time = datetime.fromisoformat(init_date_str.replace("Z", "+00:00"))
            except Exception:
                pass

        payer_name = None
        if payer_info.get("payer_name"):
            given = payer_info["payer_name"].get("given_name", "")
            surname = payer_info["payer_name"].get("surname", "")
            payer_name = f"{given} {surname}".strip()

        return {
            "record_id": f"rec_{uuid.uuid4().hex[:16]}",
            "batch_id": batch_id,
            "provider_name": ProviderName.PAYPAL.value,
            "provider_tx_id": tx_id,
            "amount": amount_val,
            "currency": currency,
            "record_type": rec_type,
            "status": status,
            "payer_id": payer_info.get("account_id"),
            "payer_email": payer_info.get("email_address"),
            "payer_name": payer_name,
            "description": tx_info.get("transaction_subject") or "PayPal Processed Transaction",
            "raw_payload": item,
            "transaction_time": tx_time
        }

    @staticmethod
    def normalize_dispute_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """Normalizes a dispute from /v1/customer/disputes into Module 06 DisputeCase dict."""
        dispute_id = item.get("dispute_id", f"pp_disp_{uuid.uuid4().hex[:8]}")
        disputed_txs = item.get("disputed_transactions", [])
        tx_id = disputed_txs[0].get("seller_transaction_id", "UNKNOWN_TX") if disputed_txs else "UNKNOWN_TX"
        
        amount_dict = item.get("dispute_amount", {})
        amount = Decimal(str(amount_dict.get("value", "0.00")))
        currency = amount_dict.get("currency_code", "USD")

        raw_status = item.get("status", "OPEN")
        status_map = {
            "OPEN": DisputeStatus.OPEN.value,
            "WAITING_FOR_SELLER_RESPONSE": DisputeStatus.EVIDENCE_REQUIRED.value,
            "UNDER_REVIEW": DisputeStatus.UNDER_REVIEW.value,
            "RESOLVED": DisputeStatus.RESOLVED.value
        }

        deadline_str = item.get("seller_response_due_date")
        deadline = None
        if deadline_str:
            try:
                deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
            except Exception:
                pass

        return {
            "dispute_id": f"disp_{uuid.uuid4().hex[:12]}",
            "provider_dispute_id": dispute_id,
            "provider": ProviderName.PAYPAL.value,
            "transaction_id": tx_id,
            "disputed_amount": amount,
            "currency": currency,
            "reason": item.get("reason", "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"),
            "lifecycle_stage": item.get("dispute_life_cycle_stage", "INQUIRY"),
            "status": status_map.get(raw_status, DisputeStatus.OPEN.value),
            "response_deadline": deadline,
            "hateoas_actions": item.get("links", [])
        }
