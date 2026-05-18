"""CEX API client factory.

Default credentials loaded from (priority: highest→lowest):
  1. Explicit kwargs passed to build_cex_client()
  2. Config file (~/.utxo-tracer/config.json -> cex.<exchange>)
  3. Env vars (BINANCE_API_KEY, BINANCE_API_SECRET, etc.)

Usage:
    client = build_cex_client("binance")
    # → uses BINANCE_API_KEY env var automatically

    client = build_cex_client("binance", api_key="xxx", api_secret="yyy")
    # → explicit override
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .base import CexApiClient
from .binance import BinanceClient
from .bybit import BybitClient
from .kucoin import KuCoinClient
from .okx import OKXClient

_LOGGER = logging.getLogger(__name__)

# Registry of available CEX implementations
_CEX_REGISTRY: dict[str, type[CexApiClient]] = {
    "binance": BinanceClient,
    "bybit": BybitClient,
    "kucoin": KuCoinClient,
    "okx": OKXClient,
}

# Env var → config key mapping
_ENV_MAP: dict[str, dict[str, str]] = {
    # (exchange, key) → env var name
    "binance": {
        "api_key": "BINANCE_API_KEY",
        "api_secret": "BINANCE_API_SECRET",
    },
    "bybit": {
        "api_key": "BYBIT_API_KEY",
        "api_secret": "BYBIT_API_SECRET",
    },
    "kucoin": {
        "api_key": "KUCOIN_API_KEY",
        "api_secret": "KUCOIN_API_SECRET",
        "api_passphrase": "KUCOIN_API_PASSPHRASE",
    },
    "okx": {
        "api_key": "OKX_API_KEY",
        "api_secret": "OKX_API_SECRET",
        "api_passphrase": "OKX_API_PASSPHRASE",
    },
}


def register_cex_client(name: str, client_class: type[CexApiClient]) -> None:
    """Register a new CEX client implementation (for extensibility)."""
    _CEX_REGISTRY[name.lower()] = client_class


def get_available_exchanges() -> list[str]:
    """Return list of registered exchange names."""
    return sorted(_CEX_REGISTRY.keys())


def _load_cex_creds(exchange: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load CEX credentials with priority: overrides > config > env.

    Returns a dict with keys like 'api_key', 'api_secret', etc.
    """
    creds: dict[str, Any] = {}
    name = exchange.lower().strip()

    # 1. Env vars (lowest priority for overrides, but highest as default)
    env_map = _ENV_MAP.get(name, {})
    for key, env_var in env_map.items():
        val = os.environ.get(env_var)
        if val:
            creds[key] = val

    # 2. Config file (~/.utxo-tracer/config.json)
    try:
        from ...config import load_config
        cfg = load_config()
        cex_section = (cfg.get("cex") or {}).get(name, {})
        for key in env_map:
            if key in cex_section and cex_section[key]:
                creds[key] = cex_section[key]
    except Exception:
        pass

    # 3. Explicit overrides (highest priority)
    if overrides:
        for key, val in overrides.items():
            if val is not None:
                creds[key] = val

    return creds


def build_cex_client(
    exchange: str,
    **kwargs: Any,
) -> CexApiClient:
    """Construct a CEX API client. Credentials loaded from env/config by default.

    Priority: explicit kwargs > config.json > env vars.

    Usage:
        client = build_cex_client("binance")
        # → uses BINANCE_API_KEY env var

        client = build_cex_client("binance", api_key="xxx")
        # → explicit API key, API secret from env/config
    """
    name = exchange.lower().strip()
    cls = _CEX_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown exchange '{exchange}'. Available: {', '.join(get_available_exchanges())}"
        )

    # Load credentials from env/config/kwargs
    creds = _load_cex_creds(name, kwargs)

    if name == "binance":
        api_key = creds.get("api_key")
        api_secret = creds.get("api_secret")
        if not api_key or not api_secret:
            _missing_creds(name, "api_key", "api_secret")
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            base_url=kwargs.get("base_url") or creds.get("base_url", "https://api.binance.com"),
        )
    elif name == "bybit":
        api_key = creds.get("api_key")
        api_secret = creds.get("api_secret")
        if not api_key or not api_secret:
            _missing_creds(name, "api_key", "api_secret")
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            base_url=kwargs.get("base_url") or creds.get("base_url", "https://api.bybit.com"),
        )
    elif name == "kucoin":
        api_key = creds.get("api_key")
        api_secret = creds.get("api_secret")
        api_passphrase = creds.get("api_passphrase")
        if not api_key or not api_secret or not api_passphrase:
            _missing_creds(name, "api_key", "api_secret", "api_passphrase")
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            base_url=kwargs.get("base_url") or creds.get("base_url", "https://api.kucoin.com"),
        )
    elif name == "okx":
        api_key = creds.get("api_key")
        api_secret = creds.get("api_secret")
        api_passphrase = creds.get("api_passphrase")
        if not api_key or not api_secret or not api_passphrase:
            _missing_creds(name, "api_key", "api_secret", "api_passphrase")
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            base_url=kwargs.get("base_url") or creds.get("base_url", "https://www.okx.com"),
        )

    # Fallback: pass all creds to constructor
    return cls(**creds)


def build_cex_client_from_config(
    exchange: str,
    config: dict[str, Any],
) -> Optional[CexApiClient]:
    """Build a CEX client from a config dict section (legacy)."""
    section = config.get(exchange) or {}
    if not section.get("api_key"):
        _LOGGER.warning("No api_key configured for exchange '%s'", exchange)
        return None
    try:
        return build_cex_client(exchange, **section)
    except Exception as e:
        _LOGGER.warning("Failed to build CEX client for '%s': %s", exchange, e)
        return None


def _missing_creds(exchange: str, *keys: str) -> None:
    """Print helpful error for missing credentials."""
    env_vars = [_ENV_MAP.get(exchange, {}).get(k, f"{exchange.upper()}_{k.upper()}") for k in keys]
    msg = (
        f"Missing credentials for '{exchange}'. "
        f"Set env vars: {' '.join(env_vars)}"
    )
    raise ValueError(msg)
