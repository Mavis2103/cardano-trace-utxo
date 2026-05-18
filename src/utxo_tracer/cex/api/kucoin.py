"""KuCoin CEX API client.

API docs:
  Deposit list:  GET /api/v1/deposits
  Withdraw list: GET /api/v1/withdrawals

Auth: KC-API-KEY + KC-API-SIGN (HMAC-SHA256) + KC-API-PASSPHRASE + KC-API-TIMESTAMP
Rate limit: 30 req / 3 sec
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any, Optional

from .base import CexApiClient
from ..models import CexRecord


class KuCoinClient(CexApiClient):
    """KuCoin exchange API client for ADA deposit/withdrawal history."""

    exchange_name = "kucoin"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        base_url: str = "https://api.kucoin.com",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._base_url = base_url.rstrip("/")
        self._session: Optional[Any] = None

    async def _get_session(self):
        if self._session is None:
            import httpx
            self._session = httpx.AsyncClient(timeout=30.0)
        return self._session

    def _sign_headers(self, method: str, endpoint: str, body: str = "") -> dict[str, str]:
        """Generate KuCoin HMAC-SHA256 signature headers."""
        now = str(int(time.time() * 1000))
        msg = now + method.upper() + endpoint + body
        sig = base64.b64encode(
            hmac.new(
                self._api_secret.encode("utf-8"),
                msg.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        # Encrypt passphrase with same secret
        passphrase_sig = base64.b64encode(
            hmac.new(
                self._api_secret.encode("utf-8"),
                self._api_passphrase.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        return {
            "KC-API-KEY": self._api_key,
            "KC-API-SIGN": sig,
            "KC-API-TIMESTAMP": now,
            "KC-API-PASSPHRASE": passphrase_sig,
            "KC-API-KEY-VERSION": "2",
        }

    async def _request(self, endpoint: str, params: dict) -> dict:
        session = await self._get_session()
        url = f"{self._base_url}{endpoint}"

        # Build query string
        query_parts = []
        for k, v in params.items():
            if v is not None:
                query_parts.append(f"{k}={v}")
        query = "&".join(query_parts)
        full_url = f"{url}?{query}" if query else url
        full_path = f"{endpoint}?{query}" if query else endpoint

        headers = self._sign_headers("GET", full_path)
        resp = await session.get(full_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "200000":
            raise RuntimeError(f"KuCoin API error: {data.get('msg', data.get('code', 'unknown'))}")
        return data.get("data", [])

    def _to_record(self, item: dict, tx_type: str) -> CexRecord:
        amount = float(item.get("amount", 0))
        return CexRecord(
            exchange=self.exchange_name,
            record_id=item.get("id", ""),
            tx_type=tx_type,
            currency=item.get("currency", "ADA").upper(),
            amount=amount,
            fee=float(item.get("fee", 0)),
            address=item.get("walletAddress", ""),
            txid=item.get("txHash") or item.get("walletTxId") or None,
            status=self._normalise_status(item.get("status")),
            timestamp=int(item.get("createAt", 0)) // 1000,  # ms → s
            raw=item,
        )

    @staticmethod
    def _normalise_status(status: Optional[str]) -> str:
        if not status:
            return "pending"
        if status.upper() in ("SUCCESS", "SUCCEED"):
            return "success"
        if status.upper() in ("FAILURE", "FAIL"):
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
            "currency": currency.upper(),
            "pageSize": min(limit, 500),
            "currentPage": (offset // limit) + 1,
        }
        if start_time is not None:
            params["startAt"] = start_time
        if end_time is not None:
            params["endAt"] = end_time
        try:
            data = await self._request("/api/v1/deposits", params)
        except Exception:
            return []
        if isinstance(data, list):
            return [self._to_record(item, "deposit") for item in data]
        if isinstance(data, dict):
            items = data.get("items", [])
            return [self._to_record(item, "deposit") for item in items]
        return []

    async def get_withdrawals(
        self,
        currency: str = "ADA",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CexRecord]:
        params: dict[str, Any] = {
            "currency": currency.upper(),
            "pageSize": min(limit, 500),
            "currentPage": (offset // limit) + 1,
        }
        if start_time is not None:
            params["startAt"] = start_time
        if end_time is not None:
            params["endAt"] = end_time
        try:
            data = await self._request("/api/v1/withdrawals", params)
        except Exception:
            return []
        if isinstance(data, list):
            return [self._to_record(item, "withdrawal") for item in data]
        if isinstance(data, dict):
            items = data.get("items", [])
            return [self._to_record(item, "withdrawal") for item in items]
        return []

    async def health_check(self) -> bool:
        try:
            data = await self._request("/api/v1/deposits", {"currency": "ADA", "pageSize": 1})
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.aclose()
            self._session = None
