"""Binance CEX API client.

API docs:
  Deposit history:  GET /sapi/v1/capital/deposit/hisrec
  Withdraw history: GET /sapi/v1/capital/withdraw/history

Auth: HMAC-SHA256 signature (API key + Secret key)
Rate limit: 1200 req/min (weight-based, these endpoints cost 1 each)
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import urlencode

from .base import CexApiClient
from ..models import CexRecord


class BinanceClient(CexApiClient):
    """Binance exchange API client for ADA deposit/withdrawal history."""

    exchange_name = "binance"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.binance.com",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._session: Optional[Any] = None  # lazy httpx.AsyncClient

    async def _get_session(self):
        if self._session is None:
            import httpx
            self._session = httpx.AsyncClient(timeout=30.0)
        return self._session

    def _sign(self, params: dict) -> dict:
        """Attach timestamp + signature to params (HMAC-SHA256)."""
        payload = dict(params)
        payload["timestamp"] = int(time.time() * 1000)
        query = urlencode(payload)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["signature"] = signature
        return payload

    async def _request(self, endpoint: str, params: dict) -> list[dict]:
        session = await self._get_session()
        signed = self._sign(params)
        url = f"{self._base_url}{endpoint}"
        resp = await session.get(url, params=signed, headers={
            "X-MBX-APIKEY": self._api_key,
        })
        resp.raise_for_status()
        return resp.json()

    def _to_record(
        self,
        item: dict,
        tx_type: str,
    ) -> CexRecord:
        """Parse a Binance API response item into a CexRecord."""
        amount = float(item.get("amount", 0))
        return CexRecord(
            exchange=self.exchange_name,
            record_id=item.get("id", ""),
            tx_type=tx_type,
            currency=item.get("coin", "ADA").upper(),
            amount=amount,
            fee=float(item.get("transactionFee", 0)),
            address=item.get("address", ""),
            txid=item.get("txId") or None,
            status=self._normalise_status(item.get("status", "0")),
            timestamp=int(item.get("insertTime", 0)) // 1000,  # ms → s
            raw=item,
        )

    @staticmethod
    def _normalise_status(status_str) -> str:
        """Binance uses 0=pending, 1=success, 6=credited but pending."""
        if status_str in ("1", 1):
            return "success"
        if status_str in ("6", 6):
            return "pending"  # credited but pending confirmation
        return "pending"

    # ── Public API ──────────────────────────────────────────────

    async def get_deposits(
        self,
        currency: str = "ADA",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CexRecord]:
        params: dict[str, Any] = {
            "coin": currency.upper(),
            "limit": min(limit, 1000),
            "offset": offset,
        }
        if start_time is not None:
            params["startTime"] = start_time * 1000  # seconds → ms
        if end_time is not None:
            params["endTime"] = end_time * 1000
        try:
            data = await self._request("/sapi/v1/capital/deposit/hisrec", params)
        except Exception:
            return []
        return [self._to_record(item, "deposit") for item in data]

    async def get_withdrawals(
        self,
        currency: str = "ADA",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CexRecord]:
        params: dict[str, Any] = {
            "coin": currency.upper(),
            "limit": min(limit, 1000),
            "offset": offset,
        }
        if start_time is not None:
            params["startTime"] = start_time * 1000
        if end_time is not None:
            params["endTime"] = end_time * 1000
        try:
            data = await self._request("/sapi/v1/capital/withdraw/history", params)
        except Exception:
            return []
        return [self._to_record(item, "withdrawal") for item in data]

    async def health_check(self) -> bool:
        """Ping Binance API to verify credentials."""
        try:
            data = await self._request("/sapi/v1/capital/deposit/hisrec", {
                "coin": "ADA",
                "limit": 1,
            })
            # A valid response (even empty list) means auth OK
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.aclose()
            self._session = None
