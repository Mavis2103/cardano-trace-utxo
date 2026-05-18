"""Abstract base class for CEX API clients.

All CEX implementations follow this interface so the matching engine
can consume them polymorphically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..models import CexRecord


class CexApiClient(ABC):
    """Base class for fetching deposit/withdrawal history from a CEX."""

    exchange_name: str = "base"

    @abstractmethod
    async def get_deposits(
        self,
        currency: str = "ADA",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CexRecord]:
        """Fetch deposit history (user → CEX)."""

    @abstractmethod
    async def get_withdrawals(
        self,
        currency: str = "ADA",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CexRecord]:
        """Fetch withdrawal history (CEX → user)."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if API key is valid and reachable."""

    async def get_all_records(
        self,
        currency: str = "ADA",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> list[CexRecord]:
        """Fetch ALL deposits + withdrawals in a time range (handles pagination)."""
        deposits = await self._fetch_all_pages(
            self.get_deposits, currency, start_time, end_time
        )
        withdrawals = await self._fetch_all_pages(
            self.get_withdrawals, currency, start_time, end_time
        )
        # Sort by timestamp ascending
        combined = sorted(deposits + withdrawals, key=lambda r: r.timestamp)
        return combined

    async def _fetch_all_pages(
        self,
        method,
        currency: str,
        start_time: Optional[int],
        end_time: Optional[int],
        page_size: int = 100,
    ) -> list[CexRecord]:
        """Paginate through all records."""
        all_records: list[CexRecord] = []
        offset = 0
        while True:
            batch = await method(
                currency=currency,
                start_time=start_time,
                end_time=end_time,
                limit=page_size,
                offset=offset,
            )
            if not batch:
                break
            all_records.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return all_records

    async def aclose(self) -> None:
        """Override to release HTTP sessions."""
        return None
