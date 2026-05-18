"""Force-directed layout + overlap removal for UTXO trace graphs.

Extracted from ``dash_app.py`` so it can be tested, benchmarked, or reused
(e.g. on the server side) without importing Dash/Cytoscape.
"""

from __future__ import annotations

import math
import random
from typing import Optional


# ---------------------------------------------------------------------------
# Fruchterman-Reingold — cell-based repulsion for large graphs
# ---------------------------------------------------------------------------

def layout_fr(
    utxo_ids: list[str],
    utxo_sizes: dict[str, int],
    edge_pairs: list[tuple[str, str]],
    n: int,
    W: int = 1200,
    H: int = 900,
) -> dict[str, dict]:
    """Fruchterman-Reingold with adaptive iterations and cell-based repulsion.

    For small graphs (n ≤ 80) uses exact all-pairs repulsion.
    For larger graphs uses spatial grid hashing → O(n · avg_cell_density)
    instead of O(n²).

    Iterations scale automatically with node count so large graphs converge
    without burning CPU on marginal refinement.
    """
    _r = random
    _r.seed(42)

    if n == 0:
        return {}
    if n == 1:
        return {utxo_ids[0]: {"x": W / 2, "y": H / 2}}

    # ── initial circle placement ──────────────────────────────────
    pos: dict[str, list[float]] = {}
    for i, nid in enumerate(utxo_ids):
        a = 2 * math.pi * i / n
        r = min(W / 2 - 10, max(200, n * 14))
        pos[nid] = [
            W / 2 + r * math.cos(a) + _r.uniform(-30, 30),
            H / 2 + r * math.sin(a) + _r.uniform(-30, 30),
        ]

    # ── adaptive iteration count ─────────────────────────────────
    if   n <= 40:    max_iters = 80
    elif n <= 100:   max_iters = 60
    elif n <= 300:   max_iters = 40
    elif n <= 800:   max_iters = 25
    else:            max_iters = 15   # very large → coarse + overlap only

    # If graph is tiny, do exact all-pairs (faster than building grid)
    USE_EXACT = n <= 80

    k = math.sqrt(W * H / n) * 0.95
    temp = W / 5
    id_to_idx = {nid: i for i, nid in enumerate(utxo_ids)}

    # ── grid parameters ──────────────────────────────────────────
    cell_size = k * 1.8                      # ~2x natural spring length

    for it in range(max_iters):
        t = temp * (1 - it / max_iters)
        disp = {nid: [0.0, 0.0] for nid in utxo_ids}

        # ── repulsion ────────────────────────────────────────────
        if USE_EXACT:
            # Exact all-pairs (faster for small n — no grid overhead)
            for i in range(n):
                u = utxo_ids[i]
                xu, yu = pos[u][0], pos[u][1]
                du = disp[u]
                su = utxo_sizes.get(u, 60) + 40
                for j in range(i + 1, n):
                    v = utxo_ids[j]
                    dx = xu - pos[v][0]
                    dy = yu - pos[v][1]
                    d = max(math.sqrt(dx * dx + dy * dy), 1)
                    f = (k * k / d) * max(su, utxo_sizes.get(v, 60) + 40) / d
                    fx = dx / d * f
                    fy = dy / d * f
                    du[0] += fx
                    du[1] += fy
                    dv = disp[v]
                    dv[0] -= fx
                    dv[1] -= fy
        else:
            # Cell-based repulsion — O(n · avg_cell_density)
            # Build grid mapping (col,row) → list of node IDs
            grid: dict[tuple[int, int], list[str]] = {}
            for nid in utxo_ids:
                key = (int(pos[nid][0] / cell_size),
                       int(pos[nid][1] / cell_size))
                grid.setdefault(key, []).append(nid)

            for u in utxo_ids:
                cu = int(pos[u][0] / cell_size)
                ru = int(pos[u][1] / cell_size)
                xu, yu = pos[u][0], pos[u][1]
                du = disp[u]
                su = utxo_sizes.get(u, 60) + 40
                for dc in (-1, 0, 1):
                    for dr in (-1, 0, 1):
                        for v in grid.get((cu + dc, ru + dr), ()):
                            if v <= u:
                                continue
                            dx = xu - pos[v][0]
                            dy = yu - pos[v][1]
                            d = max(math.sqrt(dx * dx + dy * dy), 1)
                            f = (k * k / d) * max(su, utxo_sizes.get(v, 60) + 40) / d
                            fx = dx / d * f
                            fy = dy / d * f
                            du[0] += fx
                            du[1] += fy
                            dv = disp[v]
                            dv[0] -= fx
                            dv[1] -= fy

        # ── edge attraction (springs) ─────────────────────────────
        for u, v in edge_pairs:
            if u not in pos or v not in pos:
                continue
            dx = pos[v][0] - pos[u][0]
            dy = pos[v][1] - pos[u][1]
            d = max(math.sqrt(dx * dx + dy * dy), 1)
            f = d * d / k
            fx = dx / d * f
            fy = dy / d * f
            du = disp[u]
            du[0] += fx
            du[1] += fy
            dv = disp[v]
            dv[0] -= fx
            dv[1] -= fy

        # ── gravity ───────────────────────────────────────────────
        grav = 0.005 * (10 / max(n, 10))
        for nid in utxo_ids:
            dx = W / 2 - pos[nid][0]
            dy = H / 2 - pos[nid][1]
            pos[nid][0] += dx * grav
            pos[nid][1] += dy * grav

        # ── apply displacement (cooling) ─────────────────────────
        for nid in utxo_ids:
            d = max(math.sqrt(disp[nid][0] ** 2 + disp[nid][1] ** 2), 0.01)
            pos[nid][0] += disp[nid][0] / d * min(abs(disp[nid][0]), t)
            pos[nid][1] += disp[nid][1] / d * min(abs(disp[nid][1]), t)

    return {nid: {"x": pos[nid][0], "y": pos[nid][1]} for nid in utxo_ids}


# ---------------------------------------------------------------------------
# Overlap removal — grid-based collision
# ---------------------------------------------------------------------------

def layout_overlap_remove(
    positions: dict[str, dict],
    utxo_ids: list[str],
    utxo_sizes: dict[str, int],
    edge_pairs: list[tuple[str, str]],
    n: int,
) -> None:
    """Size-aware overlap removal with grid-based collision detection.

    For large graphs (n > 300) skips the expensive node-edge repulsion
    and uses fewer iterations — the FR layout already separates nodes
    reasonably and Cytoscape's browser-side rendering is interactive.
    """
    if n <= 1:
        return

    # ── dynamic gap scaling ──────────────────────────────────────
    GAP = max(20, min(80, int(15 + 18 * math.log(n))))
    EDGE_GAP = max(40, min(120, int(30 + 22 * math.log(n))))

    # ── adaptive iterations & skip node-edge for large graphs ────
    if n <= 100:
        max_iters = 20
        do_edge_rep = True
    elif n <= 300:
        max_iters = 12
        do_edge_rep = True
    else:
        max_iters = 8
        do_edge_rep = False  # too expensive — O(n · e)

    # ── grid for fast neighbour lookup ────────────────────────────
    cell_w = 120.0
    cell_h = 120.0

    for _ in range(max_iters):
        moved = 0

        # Build spatial grid for this iteration
        grid: dict[tuple[int, int], list[str]] = {}
        for nid in utxo_ids:
            key = (int(positions[nid]["x"] / cell_w),
                   int(positions[nid]["y"] / cell_h))
            grid.setdefault(key, []).append(nid)

        # ── node–node separation via grid neighbours ─────────────
        for nid in utxo_ids:
            cx = int(positions[nid]["x"] / cell_w)
            cy = int(positions[nid]["y"] / cell_h)
            xn, yn = positions[nid]["x"], positions[nid]["y"]
            rn = utxo_sizes.get(nid, 60) / 2
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    for v in grid.get((cx + dc, cy + dr), ()):
                        if v <= nid:
                            continue
                        dx = positions[v]["x"] - xn
                        dy = positions[v]["y"] - yn
                        d = math.sqrt(dx * dx + dy * dy)
                        rv = utxo_sizes.get(v, 60) / 2
                        need = rn + rv + GAP
                        if d < need and d > 0.01:
                            push = (need - d) * 0.3
                            nx, ny = dx / d, dy / d
                            w = rv / (rn + rv)
                            positions[nid]["x"] -= nx * push * (1 - w)
                            positions[nid]["y"] -= ny * push * (1 - w)
                            positions[v]["x"] += nx * push * w
                            positions[v]["y"] += ny * push * w
                            moved += 1

        # ── node–edge repulsion (skip for large graphs) ──────────
        if do_edge_rep and edge_pairs:
            for u, v in edge_pairs:
                if u not in positions or v not in positions:
                    continue
                p1x, p1y = positions[u]["x"], positions[u]["y"]
                p2x, p2y = positions[v]["x"], positions[v]["y"]
                vx = p2x - p1x
                vy = p2y - p1y
                elen = math.sqrt(vx * vx + vy * vy)
                if elen < 1:
                    continue
                ex, ey = vx / elen, vy / elen
                for w in utxo_ids:
                    if w == u or w == v:
                        continue
                    wx, wy = positions[w]["x"], positions[w]["y"]
                    # project w onto edge segment
                    t = ((wx - p1x) * ex + (wy - p1y) * ey) / elen
                    t = max(0.0, min(1.0, t))
                    cx = p1x + t * ex
                    cy = p1y + t * ey
                    dx = wx - cx
                    dy = wy - cy
                    d = math.sqrt(dx * dx + dy * dy)
                    rw = utxo_sizes.get(w, 60) / 2
                    if (d - rw) < EDGE_GAP and d > 0.01:
                        push = (EDGE_GAP - (d - rw)) * 0.3
                        ndx, ndy = dx / d, dy / d
                        positions[w]["x"] += ndx * push
                        positions[w]["y"] += ndy * push
                        moved += 1

        if moved == 0:
            break
