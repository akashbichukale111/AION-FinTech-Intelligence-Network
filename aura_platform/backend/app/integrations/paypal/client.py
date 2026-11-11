"""
PayPal REST API Client
Handles server-side OAuth 2.0 client credentials, token expiry caching, auto-refresh,
and normalized HTTP calls to PayPal Sandbox & Live environments.
"""
import time
import logging
from typing import Dict, Any, Optional, Tuple
import httpx
from app.core.config import get_paypal_settings

logger = logging.getLogger("aura.paypal.client")


class PayPalClient:
    def __init__(self):
        self._cached_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def config(self) -> Dict[str, Any]:
        return get_paypal_settings()

    @property
    def mode(self) -> str:
        return self.config.get("PAYPAL_MODE", "sandbox").lower()

    @property
    def client_id(self) -> str:
        return (self.config.get("PAYPAL_CLIENT_ID") or "").strip()

    @property
    def client_secret(self) -> str:
        return (self.config.get("PAYPAL_CLIENT_SECRET") or "").strip()

    @property
    def base_url(self) -> str:
        url = self.config.get("PAYPAL_BASE_URL")
        if not url:
            url = "https://api-m.sandbox.paypal.com" if self.mode == "sandbox" else "https://api-m.paypal.com"
        return url.rstrip("/")

    @property
    def webhook_id(self) -> str:
        return (self.config.get("PAYPAL_WEBHOOK_ID") or "").strip()

    def is_configured(self) -> bool:
        """Checks if Client ID and Secret are provided."""
        return bool(self.client_id and self.client_secret)

    async def get_access_token(self, force_refresh: bool = False) -> str:
        """
        Retrieves or refreshes an OAuth 2.0 bearer token using client credentials grant.
        Includes safe in-memory caching with 60-second buffer before expiry.
        """
        if not self.is_configured():
            raise ValueError("PayPal credentials are not configured. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.")

        # Check existing cached token
        now = time.time()
        if not force_refresh and self._cached_token and now < (self._token_expires_at - 60):
            return self._cached_token

        token_url = f"{self.base_url}/v1/oauth2/token"
        headers = {
            "Accept": "application/json",
            "Accept-Language": "en_US",
        }
        data = {"grant_type": "client_credentials"}

        logger.info(f"Requesting new PayPal OAuth token from {token_url} (mode: {self.mode})...")

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(
                    token_url,
                    data=data,
                    headers=headers,
                    auth=(self.client_id, self.client_secret)
                )
                
                if response.status_code != 200:
                    err_msg = f"PayPal Auth Failed ({response.status_code}): {response.text}"
                    logger.error(err_msg)
                    raise RuntimeError(err_msg)

                payload = response.json()
                self._cached_token = payload.get("access_token")
                expires_in = payload.get("expires_in", 3600)
                self._token_expires_at = now + float(expires_in)
                logger.info(f"PayPal OAuth token acquired successfully (valid for {expires_in}s)")
                return self._cached_token

            except httpx.RequestError as exc:
                logger.error(f"Network error connecting to PayPal token endpoint: {exc}")
                raise RuntimeError(f"PayPal connectivity error: {exc}")

    async def request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        idempotency_key: Optional[str] = None
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Performs an authenticated request against the PayPal REST API.
        Automatically injects Authorization, PayPal-Request-Id (idempotency), and standard headers.
        """
        token = await self.get_access_token()
        url = f"{self.base_url}{endpoint}" if endpoint.startswith("/") else f"{self.base_url}/{endpoint}"

        req_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "PayPal-Request-Id": idempotency_key or f"aura_idem_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        }
        if headers:
            req_headers.update(headers)

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    json=json_data,
                    params=params,
                    headers=req_headers
                )
                
                # Check for 204 No Content
                if response.status_code == 204:
                    return 204, {}

                try:
                    res_json = response.json()
                except Exception:
                    res_json = {"raw_response": response.text}

                return response.status_code, res_json

            except httpx.RequestError as exc:
                logger.error(f"HTTP request error during {method} {url}: {exc}")
                return 503, {"error": "PAYPAL_SERVICE_UNAVAILABLE", "message": str(exc)}


paypal_client = PayPalClient()
