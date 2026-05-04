"""Provider strategies for the Tokenmon proxy.

Each strategy plugs a specific upstream API (Anthropic, OpenRouter, ...) into
the generic forwarder in `proxy.py`. The forwarding logic stays the same; only
the parsing of request/response bodies differs.
"""

from tokenmon.providers.anthropic import AnthropicStrategy
from tokenmon.providers.base import ProviderStrategy, StreamingAccumulator

__all__ = ["AnthropicStrategy", "ProviderStrategy", "StreamingAccumulator", "load"]


def load(name: str) -> ProviderStrategy:
    """Resolve a strategy by name. Lazy-imports the OpenAI-compat module so
    Anthropic-only setups don't pay for it."""
    name = name.lower().strip()
    if name == "anthropic":
        return AnthropicStrategy()
    if name in {"openrouter", "openai", "openai-compat"}:
        from tokenmon.providers.openai_compat import openai_compat_for
        return openai_compat_for(name)
    raise ValueError(f"unknown provider: {name!r}")
