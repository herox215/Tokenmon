"""Proxy health checks + restart helpers used by the menubar app.

These are file-disjoint from TokenmonApp's UI logic — they touch only
config, the provider registry, the proxy HOST, and ``launchctl``. Keeping
them isolated means a health-check tweak doesn't churn the menubar code.
"""
from __future__ import annotations

import logging
import os
import subprocess
from urllib.error import URLError
from urllib.request import urlopen

from tokenmon import config
from tokenmon.proxy import HOST

log = logging.getLogger("tokenmon.menubar.health")


def active_provider_endpoints() -> list[tuple[str, str]]:
    """Return [(provider_name, health_url), ...] for everything that
    proxy_providers config currently lists."""
    from tokenmon.providers import load as load_provider
    out: list[tuple[str, str]] = []
    for name in (config.get("proxy_providers") or ["anthropic"]):
        try:
            strategy = load_provider(name)
        except ValueError:
            log.warning("unknown provider in config: %s", name)
            continue
        out.append((name, f"http://{HOST}:{strategy.default_port}/healthz"))
    return out


def ping(url: str, timeout: float = 1.0) -> bool:
    """True iff ``url`` returns 200 within ``timeout`` seconds."""
    try:
        with urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (URLError, TimeoutError, OSError):
        return False


def proxy_health() -> tuple[bool, list[str]]:
    """Returns (all_up, down_providers). all_up=True even when there are
    no configured providers (nothing to fail)."""
    down: list[str] = []
    for name, url in active_provider_endpoints():
        if not ping(url):
            down.append(name)
    return (len(down) == 0), down


def restart_proxies_via_launchctl() -> tuple[bool, str]:
    """Restart every configured provider's proxy via launchctl.

    Returns (all_ok, message). The message is "Proxies neugestartet" on
    success or a "; "-joined list of "name: error" strings on partial /
    full failure.
    """
    from tokenmon.launchd import proxy_label

    failures: list[str] = []
    for name in (config.get("proxy_providers") or ["anthropic"]):
        label = proxy_label(name)
        try:
            result = subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                failures.append(
                    f"{name}: {result.stderr.strip() or f'exit {result.returncode}'}"
                )
        except (subprocess.SubprocessError, OSError) as exc:
            failures.append(f"{name}: {exc}")
    if not failures:
        return True, "Proxies neugestartet"
    return False, "; ".join(failures)
