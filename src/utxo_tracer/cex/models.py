"""CEX cashflow data models for deposit/withdrawal reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ── CEX API Records ──────────────────────────────────────────────


@dataclass
class CexRecord:
    """A single deposit or withdrawal record from a CEX API."""

    exchange: str             # 'binance', 'kucoin', 'bybit', ...
    record_id: str            # CEX internal ID (e.g. withdrawId, depositId)
    tx_type: str              # 'deposit' | 'withdrawal'
    currency: str             # 'ADA' (for now)
    amount: float             # In ADA units
    fee: float                # CEX fee in ADA (0 if unknown)
    address: str              # On-chain Cardano address
    txid: Optional[str]       # On-chain transaction hash (anchor key!)
    status: str               # 'success' | 'pending' | 'failed'
    timestamp: int            # Unix epoch seconds (CEX server time)
    raw: dict[str, Any] = field(default_factory=dict)  # original API payload

    def __post_init__(self) -> None:
        self.currency = self.currency.upper()

    @property
    def is_withdrawal(self) -> bool:
        return self.tx_type == "withdrawal"

    @property
    def is_deposit(self) -> bool:
        return self.tx_type == "deposit"

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def has_txid(self) -> bool:
        return bool(self.txid)


# ── On-Chain Records ─────────────────────────────────────────────


@dataclass
class OnChainRecord:
    """A single UTXO matched to a CEX flow, or a full tx view."""

    tx_hash: str
    output_index: int
    address: str
    amount_ada: float
    block_time: int               # Unix epoch seconds (Cardano block time)
    direction: str                # 'incoming' | 'outgoing' relative to user
    utxo_node_id: str             # tx_hash:output_index
    related_cex: Optional[str] = None  # CEX name if address is recognised
    is_cex_address: bool = False

    @property
    def out_ref(self) -> str:
        return f"{self.tx_hash}#{self.output_index}"


# ── Match Results ────────────────────────────────────────────────


@dataclass
class CashflowMatch:
    """A match between a CEX record and one or more on-chain UTXOs."""

    cex_record: CexRecord
    onchain_records: list[OnChainRecord]
    confidence: float             # 0.0 – 1.0
    match_type: str               # 'txid' | 'address_amount_time' | 'fee_adjusted' | 'mcmf' | 'fuzzy'
    evidence: list[str] = field(default_factory=list)

    @property
    def onchain_amount(self) -> float:
        return sum(r.amount_ada for r in self.onchain_records)

    @property
    def amount_diff(self) -> float:
        return abs(self.cex_record.amount - self.onchain_amount)

    @property
    def is_perfect_match(self) -> bool:
        return self.match_type == "txid" and self.amount_diff < 0.0001


# ── Reconciliation Summary ───────────────────────────────────────


@dataclass
class CashflowSummary:
    """Overall reconciliation result between CEX and on-chain for a time range."""

    exchange: str
    currency: str
    start_time: int
    end_time: int
    total_matches: int = 0
    total_unmatched_cex: int = 0
    total_unmatched_onchain: int = 0
    total_cex_inflow: float = 0.0    # deposits to CEX (user → CEX)
    total_cex_outflow: float = 0.0   # withdrawals from CEX (CEX → user)
    matched_inflow: float = 0.0
    matched_outflow: float = 0.0
    matches: list[CashflowMatch] = field(default_factory=list)
    unmatched_cex_records: list[CexRecord] = field(default_factory=list)
    unmatched_onchain_records: list[OnChainRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        total = self.total_matches + self.total_unmatched_cex
        if total == 0:
            return 1.0
        return self.total_matches / total

    @property
    def inflow_match_rate(self) -> float:
        if self.total_cex_inflow == 0:
            return 1.0
        return self.matched_inflow / self.total_cex_inflow

    @property
    def outflow_match_rate(self) -> float:
        if self.total_cex_outflow == 0:
            return 1.0
        return self.matched_outflow / self.total_cex_outflow
