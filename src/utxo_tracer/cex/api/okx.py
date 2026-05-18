"""OKX CEX API client (V5).

API docs:
  Deposit history:   GET /api/v5/asset/deposit-history
  Withdrawal history: GET /api/v5/asset/withdrawal-history

Auth: OK-ACCESS-KEY + OK-ACCESS-SIGN (HMAC-SHA256) + OK-ACCESS-TIMESTAMP + OK-ACCESS-PASSPHRASE
Rate limit: 20 req / 2 sec (by API key)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any, Optional

from .base import CexApiClient
from ..models import CexRecord


class OKXClient(CexApiClient):
    """OKX exchange API client for ADA deposit/withdrawal history."""

    exchange_name = "okx"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        base_url: str = "https://www.okx.com",
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

    def _sign_headers(self, method: str, endpoint: str, params: dict = None) -> dict[str, str]:
        """Generate OKX signature headers (HMAC-SHA256)."""
        ts = str(int(time.time()))
        query = ""
        if params:
            query = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
        path = endpoint + ("?" + query if query else "")
        msg = ts + method.upper() + path
        sig = base64.b64encode(
            hmac.new(
                self._api_secret.encode("utf-8"),
                msg.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        return {
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": sig,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self._api_passphrase,
        }

    async def _request(self, endpoint: str, params: dict = None) -> list:
        session = await self._get_session()
        headers = self._sign_headers("GET", endpoint, params or {})
        url = self._base_url + endpoint
        if params:
            qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
            url = url + "?" + qs
        resp = await session.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise RuntimeError(f"OKX error {data.get('code')}: {data.get('msg', 'unknown')}")
        return data.get("data", [])

    def _to_record(self, item: dict, tx_type: str) -> CexRecord:
        amount = float(item.get("amt", 0))
        return CexRecord(
            exchange=self.exchange_name,
            record_id=item.get("wdId" if tx_type == "withdrawal" else "depId", ""),
            tx_type=tx_type,
            currency=item.get("ccy", "ADA").upper(),
            amount=amount,
            fee=float(item.get("fee", 0)),
            address=item.get("from" if tx_type == "deposit" else "to", ""),
            txid=item.get("txId") or None,
            status=self._normalise_status(item.get("state", "0")),
            timestamp=int(item.get("ts", 0)) // 1000,
            raw=item,
        )

    @staticmethod
    def _normalise_status(state: str) -> str:
        """OKX deposit: 0=wait, 1=confirm, 2=success, 3=fail
        OKX withdrawal: 0=wait, 1=cancel, 2=pass, 3=fail, 4=block, 5=success, 6=processing, 7=hold"""
        if state in ("2", "5"):
            return "success"
        if state in ("3", "1", "4"):
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
            "ccy": currency.upper(),
            "limit": str(min(limit, 100)),
        }
        if start_time is not None:
            params["before"] = str(start_time)
        if end_time is not None:
            params["after"] = str(end_time)
        try:
            data = await self._request("/api/v5/asset/deposit-history", params)
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
            "ccy": currency.upper(),
            "limit": str(min(limit, 100)),
        }
        if start_time is not None:
            params["before"] = str(start_time)
        if end_time is not None:
            params["after"] = str(end_time)
        try:
            data = await self._request("/api/v5/asset/withdrawal-history", params)
        except Exception:
            return []
        return [self._to_record(item, "withdrawal") for item in data]

    async def health_check(self) -> bool:
        try:
            data = await self._request("/api/v5/asset/deposit-history", {"ccy": "ADA", "limit": "1"})
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.aclose()
            self._session = None
