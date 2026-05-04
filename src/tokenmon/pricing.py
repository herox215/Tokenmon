"""Anthropic model pricing.

Prices in USD per 1M tokens. Sources: anthropic.com pricing page.
Update when Anthropic changes pricing or adds models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute cache write; 1h cache differs


PRICES: dict[str, ModelPrice] = {
    # Claude 4.x family (per 1M tokens, USD)
    "claude-opus-4-7":   ModelPrice(input=15.00, output=75.00, cache_read=1.50, cache_write=18.75),
    "claude-opus-4-6":   ModelPrice(input=15.00, output=75.00, cache_read=1.50, cache_write=18.75),
    "claude-opus-4-5":   ModelPrice(input=15.00, output=75.00, cache_read=1.50, cache_write=18.75),
    "claude-sonnet-4-6": ModelPrice(input=3.00,  output=15.00, cache_read=0.30, cache_write=3.75),
    "claude-sonnet-4-5": ModelPrice(input=3.00,  output=15.00, cache_read=0.30, cache_write=3.75),
    "claude-haiku-4-5":  ModelPrice(input=1.00,  output=5.00,  cache_read=0.10, cache_write=1.25),
    # Legacy 3.x kept for any in-flight callers
    "claude-3-5-sonnet": ModelPrice(input=3.00,  output=15.00, cache_read=0.30, cache_write=3.75),
    "claude-3-5-haiku":  ModelPrice(input=0.80,  output=4.00,  cache_read=0.08, cache_write=1.00),
    "claude-3-opus":     ModelPrice(input=15.00, output=75.00, cache_read=1.50, cache_write=18.75),
}


def _resolve(model: str) -> ModelPrice | None:
    if model in PRICES:
        return PRICES[model]
    # Anthropic model IDs often have date suffixes (e.g. "claude-opus-4-7-20260101"
    # or "claude-opus-4-7[1m]"). Strip trailing decorations and try a prefix match.
    base = model.split("[", 1)[0]
    for key, price in PRICES.items():
        if base.startswith(key):
            return price
    # Fall back to OpenRouter's live pricing catalogue (cached under
    # ~/.tokenmon/openrouter_pricing.json with a 24h TTL).
    try:
        from tokenmon.pricing_remote import lookup as remote_lookup
        return remote_lookup(model)
    except Exception:
        log.exception("remote pricing lookup failed")
        return None


def cost_for(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> tuple[float, bool]:
    """Return (cost_in_usd, has_pricing) for one request's usage. When the
    model isn't in the pricing table, returns (0.0, False) so callers can
    surface a coverage percentage instead of silently undercounting cost."""
    price = _resolve(model)
    if price is None:
        log.warning("no pricing for model %s — counted as $0", model)
        return 0.0, False
    cost = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_creation_tokens * price.cache_write
    ) / 1_000_000
    return cost, True


def has_pricing(model: str) -> bool:
    return _resolve(model) is not None
