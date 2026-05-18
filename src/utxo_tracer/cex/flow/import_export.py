"""CSV import for CEX records — offline/replay mode.

Allows users to import CEX deposit/withdrawal data from a CSV file
when CEX API is unavailable or for testing/replay purposes.

Expected CSV format:
  type,amount,fee,address,txid,timestamp,status,currency,exchange,record_id

  type:      'deposit' | 'withdrawal'
  amount:    ADA amount (float)
  fee:       CEX fee in ADA (float)
  address:   Cardano address
  txid:      on-chain transaction hash (optional)
  timestamp: Unix epoch seconds (int)
  status:    'success' | 'pending' | 'failed'
  currency:  'ADA' (default)
  exchange:  'binance', 'kucoin', etc.
  record_id: CEX internal ID (optional)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Optional

from ..models import CexRecord


def import_from_csv(path: str, exchange: Optional[str] = None) -> list[CexRecord]:
    """Load CEX records from a CSV file.

    Args:
        path: Path to CSV file
        exchange: Override exchange name (if not in CSV column)

    Returns:
        List of CexRecord objects
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    records: list[CexRecord] = []

    with open(p, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                record = _row_to_record(row, exchange)
                if record is not None:
                    records.append(record)
            except (ValueError, KeyError) as e:
                # Skip invalid rows
                continue

    return records


def import_from_json(path: str, exchange: Optional[str] = None) -> list[CexRecord]:
    """Load CEX records from a JSON file.

    Expected JSON format:
      [
        {
          "type": "withdrawal",
          "amount": 5000.0,
          "fee": 0.17,
          "address": "addr1...",
          "txid": "abc...",
          "timestamp": 1700000000,
          "status": "success",
          "currency": "ADA",
          "exchange": "binance",
          "record_id": "w123"
        },
        ...
      ]
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = data.get("records", [])

    records: list[CexRecord] = []
    for item in data:
        try:
            record = _dict_to_record(item, exchange)
            if record is not None:
                records.append(record)
        except (ValueError, KeyError):
            continue

    return records


# ── CSV Template ──────────────────────────────────────────────

CSV_TEMPLATE_HEADER = "type,amount,fee,address,txid,timestamp,status,currency,exchange,record_id"
CSV_TEMPLATE_EXAMPLE = """type,amount,fee,address,txid,timestamp,status,currency,exchange,record_id
withdrawal,5000.0,0.17,addr1q9cz456muh...,abc123def456...,1700000000,success,ADA,binance,w001
withdrawal,1000.0,0.17,addr1x89ksjnf...,def456abc789...,1700036000,success,ADA,binance,w002
deposit,200.5,0.0,addr1z9el99gcm...,,1700072000,success,ADA,binance,d001
"""


def write_csv_template(path: str) -> None:
    """Write a CSV template file for users to fill in."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CSV_TEMPLATE_EXAMPLE, encoding="utf-8")
    return


# ── Helpers ──────────────────────────────────────────────────


def _row_to_record(row: dict[str, str], exchange_override: Optional[str] = None) -> Optional[CexRecord]:
    """Convert a CSV row dict to CexRecord."""
    try:
        amount = float(row.get("amount", 0))
    except (ValueError, TypeError):
        return None

    try:
        timestamp = int(row.get("timestamp", 0))
    except (ValueError, TypeError):
        return None

    exchange = exchange_override or row.get("exchange", "").strip().lower()
    if not exchange:
        return None

    tx_type = row.get("type", "").strip().lower()
    if tx_type not in ("deposit", "withdrawal"):
        return None

    return CexRecord(
        exchange=exchange,
        record_id=row.get("record_id", "").strip(),
        tx_type=tx_type,
        currency=row.get("currency", "ADA").strip().upper(),
        amount=amount,
        fee=float(row.get("fee", 0)),
        address=row.get("address", "").strip(),
        txid=row.get("txid", "").strip() or None,
        status=row.get("status", "success").strip().lower(),
        timestamp=timestamp,
        raw=dict(row),
    )


def _dict_to_record(item: dict[str, Any], exchange_override: Optional[str] = None) -> Optional[CexRecord]:
    """Convert a JSON dict to CexRecord."""
    try:
        amount = float(item.get("amount", 0))
    except (ValueError, TypeError):
        return None

    try:
        timestamp = int(item.get("timestamp", 0))
    except (ValueError, TypeError):
        return None

    exchange = exchange_override or str(item.get("exchange", "")).strip().lower()
    if not exchange:
        return None

    tx_type = str(item.get("type", "")).strip().lower()
    if tx_type not in ("deposit", "withdrawal"):
        return None

    return CexRecord(
        exchange=exchange,
        record_id=str(item.get("record_id", "")),
        tx_type=tx_type,
        currency=str(item.get("currency", "ADA")).upper(),
        amount=amount,
        fee=float(item.get("fee", 0)),
        address=str(item.get("address", "")),
        txid=str(item.get("txid", "")).strip() or None,
        status=str(item.get("status", "success")).lower(),
        timestamp=timestamp,
        raw=item,
    )
