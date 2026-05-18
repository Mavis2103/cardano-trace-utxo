"""Consolidation pattern detection.

CEX consolidation: after receiving user deposits into individual deposit addresses,
the CEX consolidates funds into a hot wallet via a multi-input transaction.

Pattern:
  TX: Inputs  = [addr_deposit_1 (1000 ADA), addr_deposit_2 (2000 ADA), ...]
      Outputs = [addr_hot_wallet (3000 ADA)]

This module detects such patterns and links deposit records to their
corresponding consolidation inputs, enabling full cashflow traceability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..models import CashflowMatch, CexRecord, OnChainRecord

_LOGGER = logging.getLogger(__name__)


@dataclass
class ConsolidationPattern:
    """A detected CEX consolidation transaction."""

    tx_hash: str
    input_count: int
    total_input_ada: float
    hot_wallet_address: str
    hot_wallet_output_ada: float
    input_addresses: list[str]
    block_time: int


def detect_consolidations(
    onchain_records: list[OnChainRecord],
    cex_deposit_addresses: set[str],
    min_inputs: int = 3,
    min_total_ada: float = 500.0,
) -> list[ConsolidationPattern]:
    """Detect CEX consolidation transactions in on-chain data.

    Args:
        onchain_records: All on-chain records in the window
        cex_deposit_addresses: Set of known CEX deposit addresses
        min_inputs: Minimum inputs to flag as consolidation (default: 3)
        min_total_ada: Minimum total ADA to flag (default: 500)

    Returns:
        List of detected consolidation patterns
    """
    # Group on-chain records by tx_hash
    tx_groups: dict[str, dict[str, list[OnChainRecord]]] = {}
    for oc in onchain_records:
        tx_groups.setdefault(oc.tx_hash, {}).setdefault(
            "inputs" if oc.direction == "incoming" else "outputs", []
        ).append(oc)

    consolidations: list[ConsolidationPattern] = []

    for tx_hash, groups in tx_groups.items():
        inputs = groups.get("inputs", [])
        outputs = groups.get("outputs", [])

        # Skip if not enough inputs
        if len(inputs) < min_inputs:
            continue

        # Check if inputs include CEX deposit addresses
        input_addrs = set(oc.address for oc in inputs)
        cex_inputs = input_addrs & cex_deposit_addresses
        if not cex_inputs:
            continue

        # Check if outputs go to a single address (hot wallet)
        output_addrs = set(oc.address for oc in outputs)
        if len(output_addrs) != 1:
            continue

        hot_wallet = output_addrs.pop()
        total_input = sum(oc.amount_ada for oc in inputs)
        total_output = sum(oc.amount_ada for oc in outputs)

        if total_output < min_total_ada and total_input < min_total_ada:
            continue

        consolidations.append(ConsolidationPattern(
            tx_hash=tx_hash,
            input_count=len(inputs),
            total_input_ada=round(total_input, 6),
            hot_wallet_address=hot_wallet,
            hot_wallet_output_ada=round(total_output, 6),
            input_addresses=list(input_addrs),
            block_time=max((oc.block_time for oc in inputs), default=0),
        ))

    # Sort by total ADA descending
    consolidations.sort(key=lambda c: c.total_input_ada, reverse=True)
    return consolidations


def link_cex_records_to_consolidation(
    cex_records: list[CexRecord],
    consolidation: ConsolidationPattern,
) -> list[CexRecord]:
    """Find which CEX deposit records correspond to this consolidation's inputs."""
    linked: list[CexRecord] = []
    consolidation_addrs = set(consolidation.input_addresses)

    for cex in cex_records:
        if not cex.is_deposit:
            continue
        if cex.address.strip() in consolidation_addrs:
            linked.append(cex)

    return linked
