"""Cashflow reconciler — orchestrates CEX API fetch + on-chain query + matching.

High-level entry point for the feature.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from ..api import CexApiClient, build_cex_client
from ..matching import CashflowMatcher
from ..matching.engine import CashflowSummary
from ..models import CexRecord, OnChainRecord

_LOGGER = logging.getLogger(__name__)


class CashflowReconciler:
    """Orchestrates fetching CEX records + on-chain data + matching.

    Usage:
        async with CashflowReconciler() as reconciler:
            summary = await reconciler.reconcile(
                exchange="binance",
                api_key="xxx",
                api_secret="yyy",
                start_time=1700000000,
                end_time=1710000000,
            )
            print(reconciler.format_summary(summary))
    """

    def __init__(
        self,
        onchain_provider: Optional[Any] = None,
        matcher: Optional[CashflowMatcher] = None,
    ):
        self.onchain_provider = onchain_provider
        self.matcher = matcher or CashflowMatcher()
        self._cex_client: Optional[CexApiClient] = None

    async def reconcile(
        self,
        exchange: str,
        api_key: str,
        api_secret: str,
        start_time: int,
        end_time: int,
        currency: str = "ADA",
        api_passphrase: Optional[str] = None,
        **cex_kwargs: Any,
    ) -> CashflowSummary:
        """Full reconciliation: fetch CEX → fetch on-chain → match."""
        _LOGGER.info("Starting CEX cashflow reconciliation for %s (%s→%s)",
                     exchange, start_time, end_time)

        # 1. Build CEX API client
        cex_kwargs["api_key"] = api_key
        cex_kwargs["api_secret"] = api_secret
        if api_passphrase:
            cex_kwargs["api_passphrase"] = api_passphrase

        self._cex_client = build_cex_client(exchange, **cex_kwargs)

        # 2. Fetch CEX records
        _LOGGER.info("Fetching %s records from %s...", currency, exchange)
        cex_records = await self._cex_client.get_all_records(
            currency=currency,
            start_time=start_time,
            end_time=end_time,
        )
        _LOGGER.info("  → %d CEX records fetched", len(cex_records))

        # 3. Fetch on-chain data (if provider available)
        onchain_records: list[OnChainRecord] = []
        if self.onchain_provider is not None:
            _LOGGER.info("Fetching on-chain data...")
            onchain_records = await self._fetch_onchain(
                cex_records, currency
            )
            _LOGGER.info("  → %d on-chain records", len(onchain_records))

        # 4. Run matching pipeline
        _LOGGER.info("Running cashflow matching pipeline...")
        summary = await self.matcher.match_all(cex_records, onchain_records)

        # Auto-save to cache
        try:
            from .persistence import save_cashflow
            save_cashflow(summary)
        except Exception:
            pass

        return summary

    async def reconcile_with_records(
        self,
        exchange: str,
        cex_records: list[CexRecord],
        onchain_records: Optional[list[OnChainRecord]] = None,
    ) -> CashflowSummary:
        """Reconcile with already-fetched records (skips API calls)."""
        if onchain_records is None:
            onchain_records = []
        return await self.matcher.match_all(cex_records, onchain_records)

    async def _fetch_onchain(
        self,
        cex_records: list[CexRecord],
        currency: str,
    ) -> list[OnChainRecord]:
        """Fetch on-chain data for CEX records that have txid."""
        if self.onchain_provider is None:
            return []

        records: list[OnChainRecord] = []

        for cex in cex_records:
            txid_val = cex.txid or ""
            if not txid_val.strip():
                continue
            tx_hash = txid_val.strip()
            try:
                tx_data = await self.onchain_provider.get_transaction_utxos(
                    tx_hash
                )
                for out in tx_data.get("outputs") or []:
                    records.append(OnChainRecord(
                        tx_hash=tx_hash,
                        output_index=out.out_ref.output_index,
                        address=out.address,
                        amount_ada=out.ada,
                        block_time=0,  # may be filled by timestamp enrichment
                        direction="incoming" if cex.is_deposit else "outgoing",
                        utxo_node_id=out.out_ref.node_id(),
                    ))
            except Exception as e:
                _LOGGER.warning("Failed to fetch on-chain tx %s: %s", tx_hash, e)

        return records

    @staticmethod
    def format_summary(summary: CashflowSummary) -> str:
        """Format reconciliation summary as readable text."""
        lines = [
            f"Cashflow Reconciliation — {summary.exchange.upper()} ({summary.currency})",
            f"Period: {summary.start_time} → {summary.end_time}",
            "",
            f"  CEX records:     {summary.total_matches + summary.total_unmatched_cex}",
            f"  On-chain UTXOs:  {summary.total_matches + summary.total_unmatched_onchain}",
            f"  Matched:         {summary.total_matches}",
            f"  Unmatched CEX:   {summary.total_unmatched_cex}",
            f"  Unmatched on-chain: {summary.total_unmatched_onchain}",
            f"  Match rate:      {summary.match_rate * 100:.1f}%",
            "",
            f"  CEX Inflow:      {summary.total_cex_inflow:.2f} {summary.currency}",
            f"    Matched:       {summary.matched_inflow:.2f} {summary.currency} ({summary.inflow_match_rate * 100:.1f}%)",
            f"  CEX Outflow:     {summary.total_cex_outflow:.2f} {summary.currency}",
            f"    Matched:       {summary.matched_outflow:.2f} {summary.currency} ({summary.outflow_match_rate * 100:.1f}%)",
        ]

        # Unmatched detail
        if summary.unmatched_cex_records:
            lines.extend([
                "",
                f"  Unmatched CEX records ({len(summary.unmatched_cex_records)}):",
            ])
            for r in summary.unmatched_cex_records[:10]:
                txid_str = r.txid[:16] + "…" if r.txid and len(r.txid) > 16 else (r.txid or "no txid")
                lines.append(
                    f"    [{r.tx_type}] {r.amount:.2f} ADA → {r.address[:12]}… "
                    f"txid={txid_str} time={r.timestamp}"
                )
            if len(summary.unmatched_cex_records) > 10:
                lines.append(f"    … and {len(summary.unmatched_cex_records) - 10} more")

        # Errors
        if summary.errors:
            lines.extend(["", "  Errors:"])
            for e in summary.errors:
                lines.append(f"    {e}")

        return "\n".join(lines)

    async def aclose(self) -> None:
        if self._cex_client is not None:
            await self._cex_client.aclose()
            self._cex_client = None

    async def __aenter__(self) -> "CashflowReconciler":
        return self

    async def __aexit__(self, *args) -> None:
        await self.aclose()
