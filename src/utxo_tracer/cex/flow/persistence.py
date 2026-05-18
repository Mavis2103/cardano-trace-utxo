"""Cashflow persistence — save/load reconciliation results.

Results are stored in .utxo-cache/cashflow/ as JSON files.
Each file is keyed by exchange + date range.

Example:
  .utxo-cache/cashflow/binance_1700000000_1710000000.json
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from ..models import CashflowMatch, CashflowSummary, CexRecord, OnChainRecord

_LOGGER = logging.getLogger(__name__)

# Cache directory
CACHE_DIR = Path.home() / ".utxo-tracer" / "cashflow"


def ensure_cache_dir() -> None:
    """Create cashflow cache directory if it doesn't exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(exchange: str, start_time: int, end_time: int) -> str:
    return f"{exchange.lower()}_{start_time}_{end_time}"


def _cache_path(key: str) -> Path:
    ensure_cache_dir()
    return CACHE_DIR / f"{key}.json"


def save_cashflow(summary: CashflowSummary) -> Optional[str]:
    """Save cashflow summary to cache. Returns cache key or None."""
    if not summary.exchange or not summary.start_time:
        return None

    key = _cache_key(summary.exchange, summary.start_time, summary.end_time)
    path = _cache_path(key)

    payload = {
        "version": 1,
        "saved_at": int(time.time()),
        "summary": _summary_to_dict(summary),
    }

    path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")
    _LOGGER.info("Saved cashflow to %s", path)
    return key


def load_cashflow(
    exchange: str,
    start_time: int,
    end_time: int,
) -> Optional[CashflowSummary]:
    """Load cashflow summary from cache."""
    key = _cache_key(exchange, start_time, end_time)
    path = _cache_path(key)

    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _dict_to_summary(data.get("summary", {}))
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        _LOGGER.warning("Failed to load cashflow cache: %s", e)
        return None


def list_cached_cashflows() -> list[dict]:
    """List all cached cashflow summaries."""
    ensure_cache_dir()
    results: list[dict] = []
    for f in sorted(CACHE_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            s = data.get("summary", {})
            results.append({
                "key": f.stem,
                "exchange": s.get("exchange", "?"),
                "start": s.get("start_time", 0),
                "end": s.get("end_time", 0),
                "matches": s.get("total_matches", 0),
                "unmatched": s.get("total_unmatched_cex", 0),
                "inflow": s.get("total_cex_inflow", 0),
                "outflow": s.get("total_cex_outflow", 0),
                "saved_at": data.get("saved_at", 0),
            })
        except Exception:
            continue
    return results


def clear_cashflow_cache(exchange: Optional[str] = None) -> int:
    """Clear cached cashflow results. If exchange specified, only clear that one."""
    ensure_cache_dir()
    count = 0
    for f in CACHE_DIR.glob("*.json"):
        if exchange and not f.stem.startswith(exchange.lower()):
            continue
        f.unlink()
        count += 1
    return count


# ── Serialisation helpers ────────────────────────────────────


def _summary_to_dict(s: CashflowSummary) -> dict:
    """Convert CashflowSummary to a JSON-serialisable dict."""
    return {
        "exchange": s.exchange,
        "currency": s.currency,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "total_matches": s.total_matches,
        "total_unmatched_cex": s.total_unmatched_cex,
        "total_unmatched_onchain": s.total_unmatched_onchain,
        "total_cex_inflow": s.total_cex_inflow,
        "total_cex_outflow": s.total_cex_outflow,
        "matched_inflow": s.matched_inflow,
        "matched_outflow": s.matched_outflow,
        "matches": [_match_to_dict(m) for m in s.matches],
        "unmatched_cex_records": [_cex_to_dict(r) for r in s.unmatched_cex_records],
        "unmatched_onchain_records": [_onchain_to_dict(r) for r in s.unmatched_onchain_records],
        "errors": s.errors,
    }


def _dict_to_summary(d: dict) -> CashflowSummary:
    """Convert a dict back to CashflowSummary."""
    return CashflowSummary(
        exchange=d.get("exchange", ""),
        currency=d.get("currency", "ADA"),
        start_time=d.get("start_time", 0),
        end_time=d.get("end_time", 0),
        total_matches=d.get("total_matches", 0),
        total_unmatched_cex=d.get("total_unmatched_cex", 0),
        total_unmatched_onchain=d.get("total_unmatched_onchain", 0),
        total_cex_inflow=d.get("total_cex_inflow", 0),
        total_cex_outflow=d.get("total_cex_outflow", 0),
        matched_inflow=d.get("matched_inflow", 0),
        matched_outflow=d.get("matched_outflow", 0),
        matches=[_dict_to_match(m) for m in d.get("matches", [])],
        unmatched_cex_records=[_dict_to_cex(r) for r in d.get("unmatched_cex_records", [])],
        unmatched_onchain_records=[_dict_to_onchain(r) for r in d.get("unmatched_onchain_records", [])],
        errors=d.get("errors", []),
    )


def _match_to_dict(m: CashflowMatch) -> dict:
    return {
        "cex_record": _cex_to_dict(m.cex_record),
        "onchain_records": [_onchain_to_dict(oc) for oc in m.onchain_records],
        "confidence": m.confidence,
        "match_type": m.match_type,
        "evidence": m.evidence,
    }


def _dict_to_match(d: dict) -> CashflowMatch:
    return CashflowMatch(
        cex_record=_dict_to_cex(d.get("cex_record", {})),
        onchain_records=[_dict_to_onchain(oc) for oc in d.get("onchain_records", [])],
        confidence=d.get("confidence", 0.0),
        match_type=d.get("match_type", "unknown"),
        evidence=d.get("evidence", []),
    )


def _cex_to_dict(r: CexRecord) -> dict:
    return asdict(r)


def _dict_to_cex(d: dict) -> CexRecord:
    return CexRecord(
        exchange=d.get("exchange", ""),
        record_id=d.get("record_id", ""),
        tx_type=d.get("tx_type", ""),
        currency=d.get("currency", "ADA"),
        amount=d.get("amount", 0.0),
        fee=d.get("fee", 0.0),
        address=d.get("address", ""),
        txid=d.get("txid"),
        status=d.get("status", "success"),
        timestamp=d.get("timestamp", 0),
        raw=d.get("raw", {}),
    )


def _onchain_to_dict(oc: OnChainRecord) -> dict:
    return asdict(oc)


def _dict_to_onchain(d: dict) -> OnChainRecord:
    return OnChainRecord(
        tx_hash=d.get("tx_hash", ""),
        output_index=d.get("output_index", 0),
        address=d.get("address", ""),
        amount_ada=d.get("amount_ada", 0.0),
        block_time=d.get("block_time", 0),
        direction=d.get("direction", "outgoing"),
        utxo_node_id=d.get("utxo_node_id", ""),
        related_cex=d.get("related_cex"),
        is_cex_address=d.get("is_cex_address", False),
    )
