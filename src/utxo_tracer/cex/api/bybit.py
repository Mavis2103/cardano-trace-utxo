"""Bybit CEX API client (V5).

API docs:
  Deposit history:  GET /v5/asset/deposit/query-record
  Withdraw history: GET /v5/asset/withdraw/query-record

Auth: HMAC-SHA256 (api_key + api_secret + timestamp)
Rate limit: 50 req/s (weight-based, 1-5 weight per request)
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import urlencode

from .base import CexApiClient
from ..models import CexRecord


class BybitClient(CexApiClient):
    """Bybit exchange API client for ADA deposit/withdrawal history."""

    exchange_name = "bybit"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.bybit.com",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._session: Optional[Any] = None

    async def _get_session(self):
        if self._session is None:
            import httpx
            self._session = httpx.AsyncClient(timeout=30.0)
        return self._session

    def _sign(self, params: dict, recv_window: int = 5000) -> dict:
        """Generate Bybit V5 signature headers."""
        ts = str(int(time.time() * 1000))
        payload = dict(params)
        payload.update({
            "api_key": self._api_key,
            "timestamp": ts,
            "recv_window": str(recv_window),
        })
        # Sort params and build query string
        query = urlencode(sorted(payload.items()))
        sig = hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["sign"] = sig
        return payload

    async def _request(self, endpoint: str, params: dict) -> dict:
        session = await self._get_session()
        signed = self._sign(params)
        url = f"{self._base_url}{endpoint}"
        resp = await session.get(url, params=signed)
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit error {data.get('retCode')}: {data.get('retMsg', 'unknown')}")
        return data.get("result", {})

    def _to_record(self, item: dict, tx_type: str) -> CexRecord:
        amount = float(item.get("amount", 0))
        txid_val = item.get("txID") or item.get("txid") or None
        return CexRecord(
            exchange=self.exchange_name,
            record_id=item.get("depositId" if tx_type == "deposit" else "withdrawId", ""),
            tx_type=tx_type,
            currency=item.get("coin", "ADA").upper(),
            amount=amount,
            fee=float(item.get("fee", 0)),
            address=item.get("toAddress" if tx_type == "withdrawal" else "fromAddress", ""),
            txid=txid_val,
            status=self._normalise_status(item.get("status", "0")),
            timestamp=int(item.get("timestamp", 0)) // 1000 if tx_type == "deposit" else int(item.get("timestamp", 0)) // 1000,
            raw=item,
        )

    @staticmethod
    def _normalise_status(status) -> str:
        """Bybit V5 status: 0=unknown, 1=to_confirm, 2=processing, 3=success, 4=cancel."""
        s = str(status)
        if s == "3":
            return "success"
        if s in ("4",):
            return "failed"
        return "pending"

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
            "limit": min(limit, 50),
        }
        if start_time is not None:
            params["startTime"] = start_time * 1000
        if end_time is not None:
            params["endTime"] = end_time * 1000
        # Bybit uses cursor-based pagination via `cursor` param
        cursor = ""
        all_items: list[dict] = []
        for _ in range(20):  # max 20 pages
            if cursor:
                params["cursor"] = cursor
            try:
                result = await self._request("/v5/asset/deposit/query-record", params)
                items = result.get("rows", [])
                all_items.extend(items)
                if len(items) < limit:
                    break
                cursor = result.get("nextPageCursor", "")
                if not cursor:
                    break
            except Exception:
                break
        return [self._to_record(item, "deposit") for item in all_items]

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
            "limit": min(limit, 50),
        }
        if start_time is not None:
            params["startTime"] = start_time * 1000
        if end_time is not None:
            params["endTime"] = end_time * 1000
        cursor = ""
        all_items: list[dict] = []
        for _ in range(20):
            if cursor:
                params["cursor"] = cursor
            try:
                result = await self._request("/v5/asset/withdraw/query-record", params)
                items = result.get("rows", [])
                all_items.extend(items)
                if len(items) < limit:
                    break
                cursor = result.get("nextPageCursor", "")
                if not cursor:
                    break
            except Exception:
                break
        return [self._to_record(item, "withdrawal") for item in all_items]

    async def health_check(self) -> bool:
        try:
            result = await self._request("/v5/asset/deposit/query-record", {
                "coin": "ADA", "limit": 1,
            })
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.aclose()
            self._session = None
