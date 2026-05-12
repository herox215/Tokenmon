# Tokenmon

Local LLM-API token tracker with a macOS menubar app. 🥚

Runs one HTTP reverse proxy per provider (Anthropic by default, OpenRouter
optional), records every request's token usage in SQLite, and shows today's
total in the macOS menubar. Click the egg for a per-model breakdown,
estimated USD cost, and a per-day Pokémon (gen-1) you level up by using LLMs.

## How it works

```
┌──────────────┐     ANTHROPIC_BASE_URL      ┌─────────────────┐    HTTPS    ┌────────────────────┐
│ Claude Code  │ ──────────────────────────► │ tokenmon proxy  │ ──────────► │ api.anthropic.com  │
│ (or any SDK) │                             │ 127.0.0.1:8788  │ ◄────────── │                    │
└──────────────┘ ◄────────────────────────── └────────┬────────┘             └────────────────────┘
                       passthrough                    │
                                                      ▼
                                         ┌────────────────────────┐
                                         │ ~/.tokenmon/usage.db   │
                                         └────────────┬───────────┘
                                                      │ poll every 30s
                                                      ▼
                                         ┌────────────────────────┐
                                         │ 🥚 menubar app         │
                                         └────────────────────────┘
```

The proxy parses `usage` from `/v1/messages` (Anthropic) and
`/chat/completions` (OpenAI-compatible) responses — streaming SSE and
non-streaming — and writes one row per request.

## Features

- 📊 **Per-model breakdown** of today's input / output / cache-read tokens.
- 💵 **Estimated USD cost** with a coverage percentage when some models lack
  hardcoded pricing (most of OpenRouter's catalog).
- 🐣 **Pokémon-style XP system.** Each day picks one gen-1 Pokémon you
  level up by using LLMs; longer streaks evolve it. Box + Tokendex panes let
  you browse what you've caught.
- 🤝 **Companion chat.** Double-click the on-screen sprite (or hit
  ⌘⇧Space) to open a chat panel that captures a snapshot of whatever window
  you were looking at via the macOS Vision OCR framework — one Screen
  Recording permission covers every app.
- 🔌 **Multi-provider.** Anthropic + OpenRouter out of the box; the
  proxy is a Strategy pattern so adding another OpenAI-compatible provider
  is small.

## Requirements

- macOS (the menubar uses `rumps`/AppKit; proxy itself is portable).
- Python 3.13.
- [`uv`](https://github.com/astral-sh/uv) for dependency management.

## Setup

```bash
# 1. Clone and install dependencies.
git clone https://github.com/herox215/Tokenmon.git
cd Tokenmon
uv sync

# 2. Build the menubar .app bundle (alias mode — ~200 KB, code edits go
#    live without a rebuild). Required so macOS can grant Screen Recording
#    permission to a stable bundle id.
uv run python setup.py py2app -A

# 3. Install LaunchAgents (proxy + menubar autostart at login).
#    Default = anthropic only. Use --providers to add more.
uv run tokenmon install
# or:
uv run tokenmon install --providers anthropic,openrouter

# 4. Tell each SDK / agent which provider proxy to talk to.
echo 'export ANTHROPIC_BASE_URL=http://127.0.0.1:8788' >> ~/.zshrc       # Claude Code → Anthropic
# OpenRouter — set wherever your OpenCode / SDK reads its base URL,
# e.g. opencode.json:
#   "openrouter": { "baseURL": "http://127.0.0.1:8789/api/v1", ... }

# 5. Restart your agent so it picks up the new base URL.
source ~/.zshrc
```

After this:

- The proxy runs in the background (`KeepAlive=true` via launchd).
- The 🥚 appears in your macOS menubar showing today's total tokens.
- Every Claude Code / OpenRouter call goes through the proxy and is
  recorded.

### Adding OpenRouter (or any OpenAI-compatible provider)

OpenRouter aggregates many models behind an OpenAI-compatible API. Tokenmon
proxies it on a separate port (`:8789` by default).

```bash
uv run tokenmon install --providers anthropic,openrouter
```

Point your client (e.g. OpenCode) at `http://127.0.0.1:8789/api/v1`
instead of `https://openrouter.ai/api/v1`. Tokenmon forwards transparently
and records usage. For streaming requests it also injects
`stream_options.include_usage = true` so the upstream returns token counts —
without that flag, OpenAI-style streams hide them.

## Verify it works

```bash
# 1. Check status
uv run tokenmon status

# 2. Make a test call
curl http://127.0.0.1:8788/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-4-5","max_tokens":50,"messages":[{"role":"user","content":"say hi"}]}'

# 3. Re-run status — should now show 1 request.
uv run tokenmon status
```

## Running the menubar app manually

The LaunchAgent keeps the app alive in the background. To restart it after
editing Python source:

```bash
# Most common case — alias-mode bundle picks up source changes on relaunch.
launchctl kickstart -k gui/$(id -u)/com.tokenmon.menubar

# Or open the .app directly when the LaunchAgent is unloaded.
open dist/Tokenmon.app

# Stop everything.
launchctl unload ~/Library/LaunchAgents/com.tokenmon.menubar.plist
```

The menubar app holds a process-singleton lock at
`~/.tokenmon/menubar.lock` (via `fcntl.flock`); a second instance fails
fast with the active PID printed to stderr, so you can't accidentally
end up with two icons in the topbar.

### Rebuilding the .app

Only needed when you change `setup.py`, `Info.plist`, the entry script
(`scripts/tokenmon_app.py`), bundle id, icon, or add a new top-level
package outside `tokenmon/`.

```bash
launchctl unload ~/Library/LaunchAgents/com.tokenmon.menubar.plist
rm -rf build dist
uv run python setup.py py2app -A
launchctl load ~/Library/LaunchAgents/com.tokenmon.menubar.plist
```

TCC permissions (Screen Recording for companion-chat OCR) persist across
rebuilds as long as the bundle stays at `dist/Tokenmon.app` and
`CFBundleIdentifier` stays `com.tokenmon.menubar`.

## Tests

```bash
uv run pytest        # full suite, ~318 tests, < 5 s
```

Tests are DB- and network-isolated via autouse fixtures in
`tests/conftest.py` (`_isolate_db` redirects `DB_PATH` to `tmp_path`;
`_isolate_sprites` stubs `pokemon.ensure_sprite` so no HTTP calls leak).
UI tests guard with `pytest.importorskip("AppKit")` so the suite still
runs on non-macOS CI.

## Commands

| Command | What it does |
|---|---|
| `tokenmon status` | Show today's usage + LaunchAgent state |
| `tokenmon install` | Install + load LaunchAgents (`--providers anthropic,openrouter`) |
| `tokenmon uninstall` | Stop + remove LaunchAgents |

## Files

- `~/.tokenmon/usage.db` — SQLite database (one row per request)
- `~/.tokenmon/proxy.log` — proxy application log
- `~/.tokenmon/com.tokenmon.proxy.{out,err}.log` — launchd stdout/stderr
- `~/Library/LaunchAgents/com.tokenmon.proxy.<provider>.plist` (one per provider)
- `~/Library/LaunchAgents/com.tokenmon.menubar.plist`

## Troubleshooting

**Menubar shows ⚠️ instead of 🥚** — the proxy isn't reachable on
`127.0.0.1:8788`. Use the menubar's restart-proxy action, or run:

```bash
launchctl kickstart -k gui/$(id -u)/com.tokenmon.proxy.anthropic
tail -f ~/.tokenmon/com.tokenmon.proxy.err.log
```

**Claude Code calls aren't being tracked** — check that
`echo $ANTHROPIC_BASE_URL` prints the proxy URL. Claude Code reads it at
startup, so restart Claude Code after setting the env var.

**Companion chat shows no window context** — grant Screen Recording
permission to `dist/Tokenmon.app` under
System Settings → Privacy & Security → Screen Recording.

**You moved the project directory** — the launchd plists hard-code the
venv path. Re-run `uv run tokenmon install` from the new location.

## Scope

- macOS only for the menubar; the proxy itself is portable Python.
- No retention policy — the SQLite DB grows forever until you `DELETE`
  from it.

## License & disclaimer

Tokenmon's source code is released under the [MIT License](LICENSE).

**This is an unofficial, non-commercial fan project.** Pokémon, Poké Ball,
Pokédex, and every Pokémon name, sprite, and species are trademarks and/or
copyrighted works of Nintendo, Game Freak, and The Pokémon Company. This
project is not affiliated with, endorsed by, or sponsored by any of them.

All Pokémon sprites and species descriptions are fetched at runtime from
the community-maintained [PokeAPI](https://pokeapi.co/) and cached locally
under `~/.tokenmon/`; nothing Pokémon-owned is bundled in this repository.

The MIT License covers only the original code in this repository. It does
**not** grant any rights to the third-party trademarks or copyrighted
content referenced above.
