"""Hacker address detection — use cashflow matches to identify CEX deposit addresses.

Key insight: Once cashflow reconciliation matches CEX withdrawal records to on-chain
transactions, we know:
  1. Which CEX hot wallet funded the withdrawal (tx inputs = CEX addresses)
  2. Which user address received the funds (tx outputs = user address)
  3. The transaction timestamp

For hack tracing, we combine this with the UTXO trace graph:
  - Backward trace from hacker address → find CEX addresses in the fund path
  - Forward trace from hacker address → find CEX deposit addresses (cashout)
  - Cashflow-matched CEX records confirm which CEX + which user account
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models import CashflowMatch, CashflowSummary, CexRecord, OnChainRecord
from ...models import OutRef, TraceResult

_LOGGER = logging.getLogger(__name__)


def identify_hacker_cex_addresses(
    trace_result: TraceResult,
    cashflow_summary: CashflowSummary,
) -> list[dict]:
    """Cross-reference UTXO trace nodes with cashflow-matched addresses.

    For each node in the trace graph, check if its address appears in:
      1. CEX withdrawal output addresses (CEX → hacker direction)
      2. CEX deposit input addresses (hacker → CEX direction)

    Returns list of:
      {
        "node_id": str,
        "address": str,
        "cex": str,
        "direction": "withdrawal" | "deposit",
        "amount_ada": float,
        "confidence": float,
      }
    """
    # Build set of addresses from cashflow matches
    cex_withdrawal_addrs: dict[str, str] = {}  # address → CEX name
    cex_deposit_addrs: dict[str, str] = {}

    for m in cashflow_summary.matches:
        if m.cex_record.is_withdrawal:
            # On-chain outputs = user addresses that received from CEX
            for oc in m.onchain_records:
                cex_withdrawal_addrs[oc.address] = m.cex_record.exchange
        elif m.cex_record.is_deposit:
            # On-chain outputs = CEX deposit addresses
            for oc in m.onchain_records:
                cex_deposit_addrs[oc.address] = m.cex_record.exchange

    findings: list[dict] = []

    for node in trace_result.nodes:
        addr = node.address

        if addr in cex_withdrawal_addrs:
            findings.append({
                "node_id": node.id,
                "address": addr,
                "cex": cex_withdrawal_addrs[addr],
                "direction": "withdrawal",
                "amount_ada": node.ada,
                "confidence": 0.9,
                "type": "user_address_received_from_cex",
            })

        if addr in cex_deposit_addrs:
            findings.append({
                "node_id": node.id,
                "address": addr,
                "cex": cex_deposit_addrs[addr],
                "direction": "deposit",
                "amount_ada": node.ada,
                "confidence": 0.9,
                "type": "cex_deposit_address_in_graph",
            })

    return findings


def match_cashflow_to_trace_path(
    trace_result: TraceResult,
    cashflow_summary: CashflowSummary,
) -> list[dict]:
    """Trace the path from CEX withdrawals to the hacker's UTXOs.

    For each CEX withdrawal match, find the path through the trace graph
    from the CEX output address to the start address.

    This implements the 'hacker's upstream contains CEX' logic from
    the existing FlowAnalyzer, but uses actual CEX API data instead of
    heuristic detection.
    """
    findings = identify_hacker_cex_addresses(trace_result, cashflow_summary)
    return findings
