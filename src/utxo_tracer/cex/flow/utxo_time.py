"""UTXO time resolution — get block time for a UTXO from any provider."""

from __future__ import annotations

import logging
from typing import Optional

from ...models import OutRef, UTxONode
from ...providers.base import Provider

_LOGGER = logging.getLogger(__name__)


async def resolve_utxo_time(
    provider: Provider,
    utxo_str: str,
) -> tuple[int, OutRef]:
    """Resolve a UTXO string to (block_time, out_ref).

    The UTXO string can be:
      - Full: <tx_hash>#<output_index>
      - Just tx_hash (uses output_index=0)

    Returns (block_time, out_ref).
    Raises ValueError if UTXO not found or provider can't get time.
    """
    from ...utils import parse_out_ref

    out_ref = parse_out_ref(utxo_str)

    # First, try get_tx_block_time (primary method)
    block_time = await provider.get_tx_block_time(out_ref.tx_hash)
    if block_time is not None:
        return block_time, out_ref

    # Fallback: get the UTXO and try to find its time from tx data
    _LOGGER.info("get_tx_block_time returned None, trying tx_info fallback...")
    try:
        tx_data = await provider.get_transaction_utxos(out_ref.tx_hash)
        # If we got data, the tx exists; but we still don't have block_time
        # from this method. Some providers embed it.
        # Try getting block_time from first output if available
        outputs = tx_data.get("outputs", [])
        if outputs and hasattr(outputs[0], "block_time") and outputs[0].block_time:
            return outputs[0].block_time, out_ref
    except Exception:
        pass

    raise ValueError(
        f"Could not resolve block time for {utxo_str}. "
        f"Provider '{provider.provider_type}' may not support block time queries."
    )


def format_time_window(
    block_time: int,
    window_hours_before: int = 24,
    window_hours_after: int = 24,
) -> tuple[int, int]:
    """Calculate time window around a UTXO's block time."""
    start_time = block_time - (window_hours_before * 3600)
    end_time = block_time + (window_hours_after * 3600)
    return start_time, end_time
