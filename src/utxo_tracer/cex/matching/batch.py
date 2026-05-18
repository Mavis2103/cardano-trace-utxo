"""Batch withdrawal matching for 1 CEX txid → N on-chain outputs.

CEX batch withdrawals: one transaction sends to multiple user addresses.
The CEX API returns N separate records (one per user), all with the SAME txid.

This module is called as Stage 1b — after basic TXID anchor matching, but before
the simple 1:1 TXID match assigns ALL tx outputs to ONE CEX record.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models import CashflowMatch, CexRecord, OnChainRecord
from .confidence import score_match_txid

_LOGGER = logging.getLogger(__name__)


def match_batch_withdrawals(
    cex_records: list[CexRecord],
    onchain_records: list[OnChainRecord],
) -> tuple[list[CashflowMatch], list[CexRecord], list[OnChainRecord]]:
    """Match batch withdrawals: group CEX records by txid, then match by output address.

    Steps:
    1. Group CEX records by txid
    2. For txid groups with >1 record: potential batch
    3. Build on-chain output lookup by tx_hash
    4. Match each CEX record to output by address + amount
    5. Unmatched CEX records go back to the pool
    """
    # Group CEX records by txid
    cex_by_txid: dict[str, list[CexRecord]] = {}
    for cex in cex_records:
        txid_val = cex.txid or ""
        if txid_val.strip():
            txid = txid_val.strip()
            cex_by_txid.setdefault(txid, []).append(cex)

    # Build on-chain output lookup by tx_hash
    tx_outputs: dict[str, list[OnChainRecord]] = {}
    for oc in onchain_records:
        tx_outputs.setdefault(oc.tx_hash, []).append(oc)

    matches: list[CashflowMatch] = []
    unmatched_cex: list[CexRecord] = []
    used_onchain: set[int] = set()
    used_txids: set[str] = set()

    # Collect records without txid — they can't be batch-matched here
    no_txid_records = [r for r in cex_records if not r.has_txid or not (r.txid or "").strip()]

    # Process only txid groups with >1 CEX record (= potential batch)
    for txid, cex_group in cex_by_txid.items():
        if len(cex_group) <= 1:
            unmatched_cex.extend(cex_group)
            continue

        outputs = tx_outputs.get(txid, [])
        if not outputs:
            unmatched_cex.extend(cex_group)
            continue

        # Try to match each CEX record to a specific output by address
        remaining_cex: list[CexRecord] = []
        matched_output_indices: set[int] = set()

        for cex in cex_group:
            best_idx: Optional[int] = None
            best_score = 0.0

            for j, oc in enumerate(outputs):
                if j in matched_output_indices:
                    continue

                # Match by address (primary signal)
                addr_match = cex.address.strip() == oc.address.strip()
                if not addr_match:
                    continue

                # Match by amount (with fee tolerance)
                diff = abs(cex.amount - oc.amount_ada)
                fee_diff = abs(diff - cex.fee) if cex.fee > 0 else diff
                amount_ok = diff < 0.001 or (fee_diff < 0.001)

                if addr_match and amount_ok:
                    score = 0.95 if diff < 0.001 else 0.85
                    if score > best_score:
                        best_score = score
                        best_idx = j

            if best_idx is not None:
                matched_output_indices.add(best_idx)
                matched_output = [outputs[best_idx]]
                # Score similar to TXID anchor but with address specificity
                score = best_score
                evidence = [
                    f"batch withdrawal: txid {txid[:16]}...",
                    f"address match: {cex.address[:12]}...",
                    f"amount: {cex.amount} ADA (on-chain: {outputs[best_idx].amount_ada})",
                ]
                if abs(cex.amount - outputs[best_idx].amount_ada) > 0.001:
                    fee_adj = outputs[best_idx].amount_ada + cex.fee
                    evidence.append(f"fee-adjusted: {cex.amount} ≈ {outputs[best_idx].amount_ada} + {cex.fee} fee")

                matches.append(CashflowMatch(
                    cex_record=cex,
                    onchain_records=matched_output,
                    confidence=score,
                    match_type="batch_txid",
                    evidence=evidence,
                ))
                used_txids.add(txid)
            else:
                remaining_cex.append(cex)

        # Any CEX records in this batch that couldn't be matched
        unmatched_cex.extend(remaining_cex)

    # Figure out which on-chain records were used
    # These are the ones matched via batch processing
    for i, oc in enumerate(onchain_records):
        txid = oc.tx_hash
        # If this tx was processed as a batch and the output was matched
        if txid in used_txids and oc.address.strip() in {
            m.cex_record.address.strip()
            for m in matches
            for mc in m.onchain_records
        }:
            # Check if this specific output was matched
            for m in matches:
                for mc in m.onchain_records:
                    if mc.tx_hash == oc.tx_hash and mc.output_index == oc.output_index:
                        used_onchain.add(i)
                        break

    # Also handle non-batch CEX records (single per txid)
    for txid, cex_group in cex_by_txid.items():
        if txid in used_txids or len(cex_group) > 1:
            continue  # already handled
        # Single CEX record per txid — may still be a batch on the on-chain side
        # but from CEX perspective it's 1 record
        unmatched_cex.extend(cex_group)

    # Re-inject records without txid (they were never in cex_by_txid)
    unmatched_cex.extend(no_txid_records)

    remaining_onchain = [
        oc for i, oc in enumerate(onchain_records) if i not in used_onchain
    ]

    return matches, unmatched_cex, remaining_onchain
