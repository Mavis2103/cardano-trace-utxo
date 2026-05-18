"""Min-Cost Max-Flow optimizer for ambiguous CEX ↔ on-chain matching.

Uses networkx or ortools to solve the assignment problem when
multiple CEX records and on-chain UTXOs overlap in time/amount space.

This is the "Stage 4" fallback when TXID anchor + greedy matching
leave unmatched records.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models import CexRecord, OnChainRecord

_LOGGER = logging.getLogger(__name__)


def mcmf_match(
    cex_records: list[CexRecord],
    onchain_records: list[OnChainRecord],
    window_seconds: int = 3600,
    max_candidates: int = 5,
) -> list[tuple[int, list[int], float]]:
    """Solve bipartite matching via MCMF.

    Returns list of (cex_idx, [onchain_indices], cost) for matched pairs.
    Unmatched records are not included.

    Uses a simple greedy heuristic when networkx is not available,
    or Hungarian / min-cost-flow via OR-Tools when installed.
    """
    if not cex_records or not onchain_records:
        return []

    # Try OR-Tools first (best quality)
    try:
        return _mcmf_ortools(cex_records, onchain_records, window_seconds, max_candidates)
    except ImportError:
        pass

    # Try networkx
    try:
        return _mcmf_networkx(cex_records, onchain_records, window_seconds, max_candidates)
    except ImportError:
        pass

    # Fallback: greedy assignment
    _LOGGER.info("MCMF solvers unavailable — using greedy fallback")
    return _mcmf_greedy(cex_records, onchain_records, window_seconds)


# ── Cost function ────────────────────────────────────────────────


def _match_cost(cex: CexRecord, onchain: OnChainRecord) -> float:
    """Cost 0.0 = perfect match, higher = worse.

    Combines time delta, amount delta, and address penalty.
    """
    # Amount difference (normalised)
    if cex.amount == 0:
        amount_penalty = 10.0
    else:
        diff = abs(cex.amount - onchain.amount_ada)
        amount_penalty = diff / cex.amount  # 0 = exact, 1 = 100% different

    # Time difference (normalised to 1 hour)
    time_diff = abs(cex.timestamp - onchain.block_time)
    time_penalty = time_diff / 3600.0

    # Address penalty if addresses don't match
    addr_match = (
        cex.address.strip() == onchain.address.strip()
    )
    addr_penalty = 0.0 if addr_match else 1.0

    # Weighted sum
    return (
        amount_penalty * 3.0   +   # amount is most important
        time_penalty * 1.5     +   # time is important
        addr_penalty * 2.0         # address match is strong signal
    )


# ── OR-Tools implementation ──────────────────────────────────────


def _mcmf_ortools(
    cex_records: list[CexRecord],
    onchain_records: list[OnChainRecord],
    window_seconds: int,
    max_candidates: int,
) -> list[tuple[int, list[int], float]]:
    from ortools.graph.python import min_cost_flow

    n_cex = len(cex_records)
    n_onchain = len(onchain_records)

    # Build flow network
    start_nodes: list[int] = []
    end_nodes: list[int] = []
    capacities: list[int] = []
    unit_costs: list[int] = []

    # Source = n_cex + n_onchain, Sink = +1
    source = n_cex + n_onchain
    sink = source + 1
    total_nodes = sink + 1

    # Source → each CEX record (capacity=1, cost=0)
    for i in range(n_cex):
        start_nodes.append(source)
        end_nodes.append(i)
        capacities.append(1)
        unit_costs.append(0)

    # Each on-chain record → Sink (capacity=1, cost=0)
    for j in range(n_onchain):
        start_nodes.append(n_cex + j)
        end_nodes.append(sink)
        capacities.append(1)
        unit_costs.append(0)

    # CEX → on-chain edges with cost
    INF_COST = 1000000
    for i, cex in enumerate(cex_records):
        # Compute candidate on-chain records sorted by cost
        candidates = []
        for j, oc in enumerate(onchain_records):
            cost = _match_cost(cex, oc)
            if cost < INF_COST * 0.9:
                candidates.append((cost, j))
        candidates.sort()

        # Only connect top-N candidates to keep graph manageable
        for cost, j in candidates[:max_candidates]:
            start_nodes.append(i)
            end_nodes.append(n_cex + j)
            capacities.append(1)
            # Scale cost to int (OR-Tools requires integer costs)
            scaled = max(0, min(int(cost * 10000), INF_COST))
            unit_costs.append(scaled)

        # If no candidates, connect to a dummy sink (unmatched track)
        if not candidates:
            start_nodes.append(i)
            end_nodes.append(sink)
            capacities.append(1)
            unit_costs.append(INF_COST)

    # Solve min-cost flow
    smcf = min_cost_flow.SimpleMinCostFlow()
    all_arcs = smcf.add_arcs_with_capacity_and_unit_cost(
        start_nodes, end_nodes, capacities, unit_costs
    )
    smcf.set_nodes_count(total_nodes)
    supply = min(n_cex, n_onchain)
    smcf.set_node_supply(source, supply)
    smcf.set_node_supply(sink, -supply)

    status = smcf.solve()
    if status != smcf.OPTIMAL:
        _LOGGER.warning("MCMF did not reach optimal: %s", status)
        return []

    # Extract matches
    matches: list[tuple[int, list[int], float]] = []
    for i in range(n_cex):
        matched_onchain: list[int] = []
        for arc in range(all_arcs):
            if smcf.tail(arc) == i and smcf.flow(arc) > 0:
                head = smcf.head(arc)
                if n_cex <= head < n_cex + n_onchain:
                    matched_onchain.append(head - n_cex)
                    break  # 1 CEX → 1 onchain
        if matched_onchain:
            total_cost = sum(
                _match_cost(cex_records[i], onchain_records[j])
                for j in matched_onchain
            )
            matches.append((i, matched_onchain, total_cost))

    return matches


# ── NetworkX implementation ──────────────────────────────────────


def _mcmf_networkx(
    cex_records: list[CexRecord],
    onchain_records: list[OnChainRecord],
    window_seconds: int,
    max_candidates: int,
) -> list[tuple[int, list[int], float]]:
    import networkx as nx

    G = nx.DiGraph()

    n_cex = len(cex_records)
    n_onchain = len(onchain_records)

    source = "source"
    sink = "sink"

    # Source → CEX nodes
    for i in range(n_cex):
        G.add_edge(source, f"c{i}", capacity=1, weight=0)

    # On-chain nodes → Sink
    for j in range(n_onchain):
        G.add_edge(f"o{j}", sink, capacity=1, weight=0)

    # CEX → on-chain edges
    for i, cex in enumerate(cex_records):
        candidates = [
            (j, _match_cost(cex, oc))
            for j, oc in enumerate(onchain_records)
        ]
        candidates.sort(key=lambda x: x[1])
        for j, cost in candidates[:max_candidates]:
            G.add_edge(f"c{i}", f"o{j}", capacity=1, weight=int(cost * 10000))

    # Solve min-cost max-flow
    try:
        flow_dict = nx.min_cost_flow(G)
    except Exception:
        _LOGGER.warning("networkx min_cost_flow failed")
        return []

    matches: list[tuple[int, list[int], float]] = []
    for i in range(n_cex):
        edges = flow_dict.get(f"c{i}", {})
        matched = []
        for key, flow in edges.items():
            if flow > 0 and key.startswith("o"):
                j = int(key[1:])
                matched.append(j)
        if matched:
            total_cost = sum(
                _match_cost(cex_records[i], onchain_records[j])
                for j in matched
            )
            matches.append((i, matched, total_cost))

    return matches


# ── Greedy fallback ──────────────────────────────────────────────


def _mcmf_greedy(
    cex_records: list[CexRecord],
    onchain_records: list[OnChainRecord],
    window_seconds: int,
) -> list[tuple[int, list[int], float]]:
    """Greedy assignment: match lowest-cost pairs iteratively."""
    matched_cex: set[int] = set()
    matched_onchain: set[int] = set()
    matches: list[tuple[int, list[int], float]] = []

    pairs = [
        (i, j, _match_cost(cex_records[i], onchain_records[j]))
        for i in range(len(cex_records))
        for j in range(len(onchain_records))
    ]
    # Sort by cost ascending
    pairs.sort(key=lambda x: x[2])

    for i, j, cost in pairs:
        if i in matched_cex or j in matched_onchain:
            continue
        if cost > 10.0:  # Skip terrible matches
            continue
        matched_cex.add(i)
        matched_onchain.add(j)
        matches.append((i, [j], cost))

    return matches
