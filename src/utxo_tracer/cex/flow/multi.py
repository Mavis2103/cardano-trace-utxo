"""Multi-CEX reconciliation — aggregate across multiple exchanges."""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..models import CashflowSummary, CexRecord
from .persistence import save_cashflow
from .reconciler import CashflowReconciler

_LOGGER = logging.getLogger(__name__)


async def multi_cex_reconcile(
    exchanges: list[dict[str, Any]],
    start_time: int,
    end_time: int,
    currency: str = "ADA",
    onchain_provider: Any = None,
) -> dict[str, CashflowSummary]:
    """Run reconciliation across multiple exchanges.

    Args:
        exchanges: List of dicts with keys:
            - exchange: str (binance, kucoin, etc.)
            - api_key: str
            - api_secret: str
            - api_passphrase: Optional[str]
            - base_url: Optional[str]
        start_time: Unix epoch start
        end_time: Unix epoch end
        currency: Currency to reconcile
        onchain_provider: Optional Cardano provider

    Returns:
        Dict of {exchange_name: CashflowSummary}
    """
    results: dict[str, CashflowSummary] = {}

    for config in exchanges:
        name = config.get("exchange", "")
        if not name:
            continue

        _LOGGER.info("Reconciling %s...", name)
        try:
            async with CashflowReconciler(
                onchain_provider=onchain_provider,
            ) as reconciler:
                summary = await reconciler.reconcile(
                    exchange=name,
                    api_key=config["api_key"],
                    api_secret=config["api_secret"],
                    api_passphrase=config.get("api_passphrase"),
                    start_time=start_time,
                    end_time=end_time,
                    currency=currency,
                    base_url=config.get("base_url"),
                )
                results[name] = summary
                # Auto-save
                save_cashflow(summary)
        except Exception as e:
            _LOGGER.error("Failed to reconcile %s: %s", name, e)
            results[name] = CashflowSummary(
                exchange=name,
                currency=currency,
                start_time=start_time,
                end_time=end_time,
                errors=[str(e)],
            )

    return results


def format_multi_summary(results: dict[str, CashflowSummary]) -> str:
    """Format multi-CEX reconciliation results as text."""
    lines = [
        "Multi-CEX Cashflow Reconciliation",
        "=" * 60,
        "",
    ]

    total_matches = 0
    total_unmatched = 0
    total_inflow = 0.0
    total_outflow = 0.0
    matched_inflow = 0.0
    matched_outflow = 0.0

    for exchange, s in sorted(results.items()):
        lines.append(f"  {exchange.upper()}:")
        lines.append(f"    Records: {s.total_matches + s.total_unmatched_cex}")
        lines.append(f"    Matched: {s.total_matches}  (rate: {s.match_rate * 100:.1f}%)")
        lines.append(f"    Inflow:  {s.total_cex_inflow:.2f} → matched {s.matched_inflow:.2f}")
        lines.append(f"    Outflow: {s.total_cex_outflow:.2f} → matched {s.matched_outflow:.2f}")
        if s.errors:
            lines.append(f"    Errors:  {', '.join(s.errors)}")
        lines.append("")

        total_matches += s.total_matches
        total_unmatched += s.total_unmatched_cex
        total_inflow += s.total_cex_inflow
        total_outflow += s.total_cex_outflow
        matched_inflow += s.matched_inflow
        matched_outflow += s.matched_outflow

    lines.append("-" * 60)
    lines.append(f"  TOTALS:")
    lines.append(f"    Exchanges: {len(results)}")
    lines.append(f"    Records:   {total_matches + total_unmatched}")
    lines.append(f"    Matched:   {total_matches} / {total_matches + total_unmatched}")
    lines.append(f"    Inflow:    {total_inflow:.2f} (matched: {matched_inflow:.2f})")
    lines.append(f"    Outflow:   {total_outflow:.2f} (matched: {matched_outflow:.2f})")

    return "\n".join(lines)
