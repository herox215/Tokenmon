"""LaunchAgent install/uninstall for the proxy and menubar processes.

Generates two .plist files in ~/Library/LaunchAgents pointing at the current
venv's Python (so the install survives `uv sync` but breaks if you move the
project — re-run `tokenmon install` after moving).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from tokenmon.storage import DB_DIR

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PROXY_LABEL = "com.tokenmon.proxy"
MENUBAR_LABEL = "com.tokenmon.menubar"


def _plist(label: str, module: str) -> dict:
    log_dir = DB_DIR
    return {
        "Label": label,
        "ProgramArguments": [sys.executable, "-m", module],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_dir / f"{label}.out.log"),
        "StandardErrorPath": str(log_dir / f"{label}.err.log"),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        },
        "ProcessType": "Interactive",
    }


def _write_plist(label: str, module: str) -> Path:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    path = LAUNCH_AGENTS_DIR / f"{label}.plist"
    with path.open("wb") as f:
        plistlib.dump(_plist(label, module), f)
    return path


def _uid() -> int:
    return os.getuid()


def _bootstrap(plist_path: Path) -> tuple[bool, str]:
    domain = f"gui/{_uid()}"
    # bootout first to make this idempotent (ignore errors)
    subprocess.run(["launchctl", "bootout", domain, str(plist_path)],
                   capture_output=True, text=True)
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True, text=True,
    )
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def _bootout(label: str) -> tuple[bool, str]:
    domain = f"gui/{_uid()}"
    plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
    result = subprocess.run(
        ["launchctl", "bootout", domain, str(plist_path)],
        capture_output=True, text=True,
    )
    # bootout returns non-zero if not loaded, treat that as success
    if result.returncode == 0 or "could not find" in (result.stderr + result.stdout).lower():
        return True, ""
    return False, (result.stderr or result.stdout).strip()


def install() -> list[str]:
    msgs: list[str] = []
    for label, module in ((PROXY_LABEL, "tokenmon.proxy"), (MENUBAR_LABEL, "tokenmon.menubar")):
        path = _write_plist(label, module)
        ok, info = _bootstrap(path)
        msgs.append(f"{label}: {'loaded' if ok else f'FAILED — {info}'} ({path})")
    return msgs


def uninstall() -> list[str]:
    msgs: list[str] = []
    for label in (PROXY_LABEL, MENUBAR_LABEL):
        ok, info = _bootout(label)
        path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if path.exists():
            path.unlink()
        msgs.append(f"{label}: {'unloaded' if ok else f'FAILED — {info}'}")
    return msgs


def status() -> list[str]:
    msgs: list[str] = []
    domain = f"gui/{_uid()}"
    for label in (PROXY_LABEL, MENUBAR_LABEL):
        result = subprocess.run(
            ["launchctl", "print", f"{domain}/{label}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            msgs.append(f"{label}: not loaded")
            continue
        pid = "?"
        state = "?"
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid ="):
                pid = line.split("=", 1)[1].strip()
            elif line.startswith("state ="):
                state = line.split("=", 1)[1].strip()
        msgs.append(f"{label}: state={state} pid={pid}")
    return msgs
