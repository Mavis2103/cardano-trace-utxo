"""Confidence scoring for CEX ↔ on-chain matches."""

from __future__ import annotations

from ..models import CashflowMatch, CexRecord, OnChainRecord


# ── Confidence thresholds (empirical, adjust based on real data) ──

_TXID_CONFIDENCE = 1.0        # txid anchor — exact on-chain hash match
_EXACT_ADDR_AMT_TIME = 0.85   # same address + exact amount + close time
_FEE_ADJ_ADDR_TIME = 0.65     # fee-adjusted amount + address + close time
_EXACT_AMT_TIME = 0.50        # exact amount + close time (different addr)
_EXACT_AMT_WIDE_TIME = 0.35   # exact amount + wider window
_FEE_ADJ_WIDE = 0.25          # fee-adjusted + wider window
_FUZZY_LOW = 0.15             # loose match


def score_match_txid(
    cex: CexRecord,
    onchain: list[OnChainRecord],
    window_seconds: int = 3600,
) -> tuple[float, list[str]]:
    """Score a match where the CEX record has a txid.

    This is the anchor case: we already verified the tx hashes match.
    Now check amount and address consistency for confidence tier.
    """
    evidence: list[str] = []
    total_onchain = sum(r.amount_ada for r in onchain)

    evidence.append(f"txid match: {cex.txid}")

    # Amount check
    diff = abs(cex.amount - total_onchain)
    if diff < 0.001:
        evidence.append(f"exact amount match: {cex.amount} ADA")
        # Address check
        addr_match = _any_address_match(cex.address, onchain)
        if addr_match:
            evidence.append(f"address match: {_short_addr(cex.address)}")
            return _TXID_CONFIDENCE, evidence
        else:
            evidence.append(f"address differs (expected: {_short_addr(cex.address)})")
            return _TXID_CONFIDENCE, evidence  # txid still perfect

    else:
        # Fee-adjusted
        fee_diff = abs(diff - cex.fee)
        if fee_diff < 0.001:
            evidence.append(f"fee-adjusted amount match: {cex.amount} = {total_onchain} + {cex.fee} fee")
            return _TXID_CONFIDENCE, evidence
        else:
            evidence.append(f"amount diff: {diff:.6f} ADA (expected fee: {cex.fee})")
            return _TXID_CONFIDENCE * 0.95, evidence


def score_match_no_txid(
    cex: CexRecord,
    onchain: list[OnChainRecord],
    window_seconds: int = 3600,
) -> tuple[float, list[str]]:
    """Score a candidate match WITHOUT txid (algorithmic).

    Uses: amount, address, time proximity.
    """
    evidence: list[str] = []
    total_onchain = sum(r.amount_ada for r in onchain)

    if not onchain:
        return 0.0, ["no on-chain records to match"]

    # 1. Address match
    addr_match = _any_address_match(cex.address, onchain)
    if addr_match:
        evidence.append(f"on address: {_short_addr(cex.address)}")

    # 2. Amount proximity
    diff = abs(cex.amount - total_onchain)
    amount_exact = diff < 0.001
    amount_fee_adjusted = abs(diff - cex.fee) < 0.001 if cex.fee > 0 else False

    if amount_exact:
        evidence.append(f"exact amount: {cex.amount} ADA")
    elif amount_fee_adjusted:
        evidence.append(f"fee-adjusted amount: {cex.amount} = {total_onchain} + {cex.fee} fee")
    else:
        evidence.append(f"amount diff: {diff:.4f} ADA")

    # 3. Time proximity
    time_close = False
    time_wide = False
    if onchain:
        onchain_times = [r.block_time for r in onchain]
        avg_otime = sum(onchain_times) / len(onchain_times)
        time_diff = abs(cex.timestamp - avg_otime)
        time_close = time_diff < window_seconds / 2
        time_wide = time_diff < window_seconds

        if time_close:
            evidence.append(f"within tight time window ({time_diff // 60}m)")
        elif time_wide:
            evidence.append(f"within wide time window ({time_diff // 60}m)")

    # Compute score
    if amount_exact and addr_match and time_close:
        return _EXACT_ADDR_AMT_TIME, evidence
    if amount_fee_adjusted and addr_match and time_close:
        return _FEE_ADJ_ADDR_TIME, evidence
    if amount_exact and time_close:
        return _EXACT_AMT_TIME, evidence
    if amount_exact and time_wide:
        return _EXACT_AMT_WIDE_TIME, evidence
    if addr_match and time_close and not amount_exact:
        return _FEE_ADJ_WIDE, evidence
    if time_close:
        return _FUZZY_LOW, evidence

    return 0.05, evidence


def _any_address_match(cex_addr: str, onchain: list[OnChainRecord]) -> bool:
    if not cex_addr:
        return False
    cex_stripped = cex_addr.strip()
    for oc in onchain:
        if oc.address.strip() == cex_stripped:
            return True
        # Also check address with same stake key
        if _same_stake_key(cex_addr, oc.address):
            return True
    return False


def _same_stake_key(addr_a: str, addr_b: str) -> bool:
    """Rough check if two addresses share a stake key (via prefix)."""
    # For Shelley addresses: addr1q... or addr1v... or addr1w...
    if not (addr_a.startswith("addr1") and addr_b.startswith("addr1")):
        return False
    # They must have the same first 60+ chars to share a stake key
    # Stake key is in bytes 29-57 of decoded bech32, corresponds to
    # roughly chars 50-100 of the encoded address
    common = min(len(addr_a), len(addr_b))
    # Compare the portion after payment part (~first 50 chars for typical addr)
    # This is a heuristic — full stake key extraction is in cex/bech32.py
    if common < 60:
        return False
    # Compare the last portion (stake key part)
    if len(addr_a) >= 70 and len(addr_b) >= 70:
        return addr_a[-20:] == addr_b[-20:]
    return False


def _short_addr(addr: str, front: int = 8, back: int = 6) -> str:
    """Shorten an address for display."""
    if len(addr) <= front + back + 3:
        return addr
    return f"{addr[:front]}…{addr[-back:]}"
