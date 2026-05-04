"""LaunchAgent install/uninstall for the proxy and menubar processes.

Generates per-provider proxy plists in ~/Library/LaunchAgents pointing at the
current venv's Python (so the install survives `uv sync` but breaks if you
move the project — re-run `tokenmon install` after moving).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from tokenmon.providers import load as load_provider
from tokenmon.storage import DB_DIR

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LEGACY_PROXY_LABEL = "com.tokenmon.proxy"  # pre-multi-provider single agent
PROXY_LABEL_PREFIX = "com.tokenmon.proxy"
MENUBAR_LABEL = "com.tokenmon.menubar"


def proxy_label(provider: str) -> str:
    return f"{PROXY_LABEL_PREFIX}.{provider}"


def _base_plist(label: str) -> dict:
    return {
        "Label": label,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(DB_DIR / f"{label}.out.log"),
        "StandardErrorPath": str(DB_DIR / f"{label}.err.log"),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        },
        "ProcessType": "Interactive",
    }


def _proxy_plist(provider: str) -> dict:
    label = proxy_label(provider)
    plist = _base_plist(label)
    strategy = load_provider(provider)
    plist["ProgramArguments"] = [
        sys.executable, "-m", "tokenmon.proxy",
        "--provider", provider,
        "--port", str(strategy.default_port),
    ]
    return plist


def _menubar_plist() -> dict:
    plist = _base_plist(MENUBAR_LABEL)
    plist["ProgramArguments"] = [sys.executable, "-m", "tokenmon.menubar"]
    return plist


def _write_plist(label: str, plist_data: dict) -> Path:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    path = LAUNCH_AGENTS_DIR / f"{label}.plist"
    with path.open("wb") as f:
        plistlib.dump(plist_data, f)
    return path


def _uid() -> int:
    return os.getuid()


def _bootstrap(plist_path: Path) -> tuple[bool, str]:
    domain = f"gui/{_uid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist_path)],
                   capture_output=True, text=True)
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True, text=True,
    )
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def _bootout_label(label: str) -> tuple[bool, str]:
    domain = f"gui/{_uid()}"
    plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
    if not plist_path.exists():
        return True, ""
    result = subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)],
        capture_output=True, text=True,
    )
    if result.returncode == 0 or "could not find" in (result.stderr + result.stdout).lower():
        return True, ""
    return False, (result.stderr or result.stdout).strip()


def _existing_proxy_labels() -> list[str]:
    """Find every previously installed proxy plist, including legacy ones,
    so install() / uninstall() can clean them up."""
    if not LAUNCH_AGENTS_DIR.exists():
        return []
    labels = []
    for path in LAUNCH_AGENTS_DIR.glob(f"{PROXY_LABEL_PREFIX}*.plist"):
        labels.append(path.stem)
    return labels


def install(providers: list[str]) -> list[str]:
    """Install proxy LaunchAgents for the given providers + the menubar agent.
    Cleans up any legacy single-proxy install and any provider plists that
    aren't in the new list."""
    msgs: list[str] = []
    desired_proxy_labels = {proxy_label(p) for p in providers}
    # Remove any old proxy plists that aren't in the new desired set (and the
    # legacy un-suffixed one).
    for label in _existing_proxy_labels():
        if label in desired_proxy_labels:
            continue
        ok, info = _bootout_label(label)
        path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if path.exists():
            path.unlink()
        msgs.append(f"{label}: removed{'' if ok else f' (warning: {info})'}")

    # Install desired providers.
    for provider in providers:
        try:
            plist = _proxy_plist(provider)
        except ValueError as exc:
            msgs.append(f"{provider}: SKIPPED — {exc}")
            continue
        path = _write_plist(plist["Label"], plist)
        ok, info = _bootstrap(path)
        msgs.append(
            f"{plist['Label']}: {'loaded' if ok else f'FAILED — {info}'}"
        )

    # Menubar agent (always present).
    plist = _menubar_plist()
    path = _write_plist(MENUBAR_LABEL, plist)
    ok, info = _bootstrap(path)
    msgs.append(f"{MENUBAR_LABEL}: {'loaded' if ok else f'FAILED — {info}'}")
    return msgs


def uninstall() -> list[str]:
    msgs: list[str] = []
    for label in _existing_proxy_labels() + [MENUBAR_LABEL]:
        ok, info = _bootout_label(label)
        path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if path.exists():
            path.unlink()
        msgs.append(f"{label}: {'unloaded' if ok else f'FAILED — {info}'}")
    return msgs


def status() -> list[str]:
    msgs: list[str] = []
    domain = f"gui/{_uid()}"
    for label in sorted(_existing_proxy_labels()) + [MENUBAR_LABEL]:
        result = subprocess.run(
            ["launchctl", "print", f"{domain}/{label}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            msgs.append(f"{label}: not loaded")
            continue
        pid = state = "?"
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid ="):
                pid = line.split("=", 1)[1].strip()
            elif line.startswith("state ="):
                state = line.split("=", 1)[1].strip()
        msgs.append(f"{label}: state={state} pid={pid}")
    return msgs
