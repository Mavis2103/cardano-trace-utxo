"""Core cashflow matching engine.

Multi-stage pipeline:
  Stage 1: TXID Anchor — exact on-chain tx hash match (98% of records)
  Stage 2: Address + Amount + Time greedy match
  Stage 3: Fee-adjusted matching
  Stage 4: MCMF optimization for remaining ambiguity
  Stage 5: Fuzzy / review for edge cases
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models import CashflowMatch, CashflowSummary, CexRecord, OnChainRecord
from .batch import match_batch_withdrawals
from .confidence import score_match_no_txid, score_match_txid
from .mcmf import mcmf_match

_LOGGER = logging.getLogger(__name__)


# ── Default configuration ───────────────────────────────────────

DEFAULT_WINDOW_SECONDS = 3600        # 1 hour default time window
DEFAULT_TIGHT_WINDOW = 1800          # 30 min tight window
DEFAULT_CONFIDENCE_THRESHOLD = 0.50  # minimum confidence for auto-match
DEFAULT_FEE_ADA = 0.17               # typical Cardano tx fee (lovelace)


class CashflowMatcher:
    """Matches CEX records against on-chain UTXO data.

    Usage:
        matcher = CashflowMatcher(window_seconds=3600)
        matches = await matcher.match_all(cex_records, onchain_records)
    """

    def __init__(
        self,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        tight_window: int = DEFAULT_TIGHT_WINDOW,
        min_confidence: float = DEFAULT_CONFIDENCE_THRESHOLD,
        default_fee: float = DEFAULT_FEE_ADA,
    ):
        self.window_seconds = window_seconds
        self.tight_window = tight_window
        self.min_confidence = min_confidence
        self.default_fee = default_fee

    # ── Public API ──────────────────────────────────────────────

    async def match_all(
        self,
        cex_records: list[CexRecord],
        onchain_records: list[OnChainRecord],
    ) -> CashflowSummary:
        """Run full multi-stage matching pipeline."""
        if not cex_records:
            return CashflowSummary(
                exchange="",
                currency="ADA",
                start_time=0,
                end_time=0,
            )

        exchange = cex_records[0].exchange
        currency = cex_records[0].currency
        start_time = min(r.timestamp for r in cex_records)
        end_time = max(r.timestamp for r in cex_records)

        # Pre-filter on-chain to a reasonable window
        windowed_onchain = self._filter_onchain_by_window(
            onchain_records, cex_records
        )

        cex_success = [r for r in cex_records if r.is_success]
        unmatched_cex: list[CexRecord] = list(cex_success)
        unmatched_onchain: list[OnChainRecord] = list(windowed_onchain)
        matches: list[CashflowMatch] = []

        # Stage 1: TXID Anchor
        _LOGGER.info("Stage 1: TXID anchor matching...")
        stage1_matches, unmatched_cex, unmatched_onchain = self._match_by_txid(
            unmatched_cex, unmatched_onchain
        )
        matches.extend(stage1_matches)
        _LOGGER.info("  → %d matches via txid", len(stage1_matches))

        # Stage 1b: Batch withdrawal detection (multi-output tx, same txid)
        if unmatched_cex and unmatched_onchain:
            _LOGGER.info("Stage 1b: Batch withdrawal matching...")
            stage1b_matches, unmatched_cex, unmatched_onchain = match_batch_withdrawals(
                unmatched_cex, unmatched_onchain
            )
            matches.extend(stage1b_matches)
            _LOGGER.info("  → %d matches via batch txid", len(stage1b_matches))

        # Stage 2: Greedy (address + amount + time)
        if unmatched_cex and unmatched_onchain:
            _LOGGER.info("Stage 2: Greedy address+amount+time matching...")
            stage2_matches, unmatched_cex, unmatched_onchain = self._match_greedy(
                unmatched_cex, unmatched_onchain, self.tight_window
            )
            matches.extend(stage2_matches)
            _LOGGER.info("  → %d matches via greedy", len(stage2_matches))

        # Stage 3: Fee-adjusted
        if unmatched_cex and unmatched_onchain:
            _LOGGER.info("Stage 3: Fee-adjusted matching...")
            stage3_matches, unmatched_cex, unmatched_onchain = self._match_fee_adjusted(
                unmatched_cex, unmatched_onchain, self.tight_window
            )
            matches.extend(stage3_matches)
            _LOGGER.info("  → %d matches via fee-adjusted", len(stage3_matches))

        # Stage 4: MCMF optimization
        if unmatched_cex and unmatched_onchain:
            _LOGGER.info("Stage 4: MCMF optimization...")
            stage4_matches, unmatched_cex, unmatched_onchain = self._match_mcmf(
                unmatched_cex, unmatched_onchain
            )
            matches.extend(stage4_matches)
            _LOGGER.info("  → %d matches via MCMF", len(stage4_matches))

        # Stage 5: Wide-window fuzzy
        if unmatched_cex and unmatched_onchain:
            _LOGGER.info("Stage 5: Wide-window fuzzy matching...")
            stage5_matches, unmatched_cex, unmatched_onchain = self._match_greedy(
                unmatched_cex, unmatched_onchain, self.window_seconds * 2
            )
            matches.extend(stage5_matches)
            _LOGGER.info("  → %d matches via fuzzy", len(stage5_matches))

        # Build summary
        total_inflow = sum(r.amount for r in cex_records if r.is_deposit)
        total_outflow = sum(r.amount for r in cex_records if r.is_withdrawal)
        matched_inflow = sum(
            m.cex_record.amount for m in matches if m.cex_record.is_deposit
        )
        matched_outflow = sum(
            m.cex_record.amount for m in matches if m.cex_record.is_withdrawal
        )

        return CashflowSummary(
            exchange=exchange,
            currency=currency,
            start_time=start_time,
            end_time=end_time,
            total_matches=len(matches),
            total_unmatched_cex=len(unmatched_cex),
            total_unmatched_onchain=len(unmatched_onchain),
            total_cex_inflow=total_inflow,
            total_cex_outflow=total_outflow,
            matched_inflow=matched_inflow,
            matched_outflow=matched_outflow,
            matches=matches,
            unmatched_cex_records=unmatched_cex,
            unmatched_onchain_records=unmatched_onchain,
        )

    # ── Stage 1: TXID Anchor ─────────────────────────────────────

    def _match_by_txid(
        self,
        cex_records: list[CexRecord],
        onchain_records: list[OnChainRecord],
    ) -> tuple[list[CashflowMatch], list[CexRecord], list[OnChainRecord]]:
        """Match CEX records that have txid to on-chain by transaction hash."""
        # Build tx_hash → onchain index
        tx_map: dict[str, list[OnChainRecord]] = {}
        for oc in onchain_records:
            tx_map.setdefault(oc.tx_hash, []).append(oc)

        matches: list[CashflowMatch] = []
        unmatched: list[CexRecord] = []
        matched_txids: set[str] = set()

        for cex in cex_records:
            if not cex.has_txid:
                unmatched.append(cex)
                continue

            txid_val = cex.txid or ""
            tx_hash = txid_val.strip()
            if tx_hash in tx_map:
                matched_onchain = tx_map[tx_hash]
                confidence, evidence = score_match_txid(cex, matched_onchain)
                matches.append(CashflowMatch(
                    cex_record=cex,
                    onchain_records=matched_onchain,
                    confidence=confidence,
                    match_type="txid",
                    evidence=evidence,
                ))
                matched_txids.add(tx_hash)
            else:
                unmatched.append(cex)

        # Filter out matched onchain
        remaining_onchain = [
            oc for oc in onchain_records if oc.tx_hash not in matched_txids
        ]

        return matches, unmatched, remaining_onchain

    # ── Stage 2+5: Greedy Temporal+Amount+Address ────────────────

    def _match_greedy(
        self,
        cex_records: list[CexRecord],
        onchain_records: list[OnChainRecord],
        window_seconds: int,
    ) -> tuple[list[CashflowMatch], list[CexRecord], list[OnChainRecord]]:
        """Greedy matching by address (exact + stake-key), amount, time."""
        matches: list[CashflowMatch] = []
        unmatched_cex: list[CexRecord] = []
        used_onchain: set[int] = set()

        # Pre-compute stake key cache for on-chain addresses
        sk_cache: dict[str, Optional[str]] = {}
        for oc in onchain_records:
            if oc.address not in sk_cache:
                sk_cache[oc.address] = _extract_stake_key(oc.address)

        # Sort CEX records by time (oldest first)
        sorted_cex = sorted(cex_records, key=lambda r: r.timestamp)

        for cex in sorted_cex:
            best_score = 0.0
            best_indices: list[int] = []
            best_evidence: list[str] = []
            cex_sk = _extract_stake_key(cex.address)

            for j, oc in enumerate(onchain_records):
                if j in used_onchain:
                    continue

                time_diff = abs(cex.timestamp - oc.block_time)
                if time_diff > window_seconds:
                    continue

                # Score with stake-key awareness
                score, evidence = score_match_no_txid(
                    cex, [oc], window_seconds
                )

                # Bonus: boost score if addresses share a stake key
                if score < 0.85 and cex_sk and sk_cache.get(oc.address):
                    if cex_sk == sk_cache[oc.address]:
                        # Stake key match = likely same CEX entity
                        score = max(score, 0.65)
                        evidence.append(f"same stake key: {cex_sk[:12]}...")

                if score > best_score:
                    best_score = score
                    best_indices = [j]
                    best_evidence = evidence

            if best_score >= self.min_confidence and best_indices:
                matched = [onchain_records[j] for j in best_indices]
                matches.append(CashflowMatch(
                    cex_record=cex,
                    onchain_records=matched,
                    confidence=best_score,
                    match_type="address_amount_time",
                    evidence=best_evidence,
                ))
                for j in best_indices:
                    used_onchain.add(j)
            else:
                unmatched_cex.append(cex)

        remaining_onchain = [
            oc for i, oc in enumerate(onchain_records) if i not in used_onchain
        ]

        return matches, unmatched_cex, remaining_onchain

    # ── Stage 3: Fee-Adjusted ────────────────────────────────────

    def _match_fee_adjusted(
        self,
        cex_records: list[CexRecord],
        onchain_records: list[OnChainRecord],
        window_seconds: int,
    ) -> tuple[list[CashflowMatch], list[CexRecord], list[OnChainRecord]]:
        """Match with fee tolerance: CEX amount = onchain amount + fee."""
        matches: list[CashflowMatch] = []
        unmatched_cex: list[CexRecord] = []
        used_onchain: set[int] = set()

        for cex in cex_records:
            best_score = 0.0
            best_idx: Optional[int] = None

            for j, oc in enumerate(onchain_records):
                if j in used_onchain:
                    continue
                time_diff = abs(cex.timestamp - oc.block_time)
                if time_diff > window_seconds:
                    continue

                # Fee-adjusted amount check
                fee = cex.fee if cex.fee > 0 else self.default_fee
                expected = oc.amount_ada + fee
                diff = abs(cex.amount - expected)
                amount_ok = diff < 0.001 or (diff / max(cex.amount, 0.001)) < 0.01

                # Address match
                addr_ok = (
                    cex.address.strip() == oc.address.strip()
                )

                if amount_ok and addr_ok:
                    score = 0.65
                    if score > best_score:
                        best_score = score
                        best_idx = j

            if best_idx is not None and best_score >= self.min_confidence:
                matched = [onchain_records[best_idx]]
                matches.append(CashflowMatch(
                    cex_record=cex,
                    onchain_records=matched,
                    confidence=best_score,
                    match_type="fee_adjusted",
                    evidence=[
                        f"fee-adjusted: {cex.amount} ≈ {matched[0].amount_ada} + {cex.fee or self.default_fee} fee"
                    ],
                ))
                used_onchain.add(best_idx)
            else:
                unmatched_cex.append(cex)

        remaining_onchain = [
            oc for i, oc in enumerate(onchain_records) if i not in used_onchain
        ]
        return matches, unmatched_cex, remaining_onchain

    # ── Stage 4: MCMF ────────────────────────────────────────────

    def _match_mcmf(
        self,
        cex_records: list[CexRecord],
        onchain_records: list[OnChainRecord],
    ) -> tuple[list[CashflowMatch], list[CexRecord], list[OnChainRecord]]:
        """Solve remaining ambiguity via min-cost max-flow."""
        matched_pairs = mcmf_match(cex_records, onchain_records)

        matches: list[CashflowMatch] = []
        matched_cex: set[int] = set()
        matched_onchain: set[int] = set()

        for cex_idx, onchain_idxs, cost in matched_pairs:
            matched_cex.add(cex_idx)
            onchain_list = [onchain_records[j] for j in onchain_idxs]
            matched_onchain.update(onchain_idxs)

            total_amt = sum(oc.amount_ada for oc in onchain_list)
            diff = abs(cex_records[cex_idx].amount - total_amt)
            score = max(0.3, 1.0 - cost / 10.0)

            matches.append(CashflowMatch(
                cex_record=cex_records[cex_idx],
                onchain_records=onchain_list,
                confidence=score,
                match_type="mcmf",
                evidence=[f"mcmf assignment (cost={cost:.4f}, diff={diff:.4f} ADA)"],
            ))

        unmatched_cex = [
            r for i, r in enumerate(cex_records) if i not in matched_cex
        ]
        remaining_onchain = [
            oc for i, oc in enumerate(onchain_records) if i not in matched_onchain
        ]

        return matches, unmatched_cex, remaining_onchain

# ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _filter_onchain_by_window(
        onchain: list[OnChainRecord],
        cex_records: list[CexRecord],
        buffer_seconds: int = 7200,
    ) -> list[OnChainRecord]:
        if not cex_records or not onchain:
            return onchain
        min_ts = min(r.timestamp for r in cex_records) - buffer_seconds
        max_ts = max(r.timestamp for r in cex_records) + buffer_seconds
        return [
            oc for oc in onchain
            if min_ts <= oc.block_time <= max_ts
        ]


def _extract_stake_key(address: str) -> Optional[str]:
    """Extract stake key portion from a Cardano Shelley address.

    For addr1q... (base): stake key is in chars ~52-100.
    For addr1v.../addr1w... (enterprise): no stake key → None.
    For addr1x.../addr1z... (script): has stake key.
    For Byron/stake/unknown: None.

    This is a heuristic — for full decoding see cex/bech32.py.
    """
    if not address or len(address) < 60:
        return None
    if address.startswith("addr1"):
        # Base addresses: addr1q... or addr1x... or addr1z...
        # Stake key portion is the last ~48 chars
        if address[4] in ("q", "x", "z"):
            return address[-48:] if len(address) > 60 else None
        # Enterprise addresses: addr1v... or addr1w... → no stake key
        if address[4] in ("v", "w"):
            return None
    return None
