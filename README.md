# Tokenmon

Local LLM-API token tracker with a macOS menubar app. 🥚

Runs one HTTP reverse proxy per provider (Anthropic by default, OpenRouter
optional), records every request's token usage in SQLite, and shows today's
total in the macOS menubar. Click the egg for a per-model breakdown,
estimated USD cost, and a per-day Pokemon (gen-1) you level up by using LLMs.

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

The proxy parses `usage` from `/v1/messages` responses (both streaming SSE
and non-streaming) and writes one row per request.

## Setup

```bash
# 1. Install dependencies
uv sync

# 2. Install LaunchAgents (proxy + menubar autostart at login).
#    Default = anthropic only. Use --providers to add more.
uv run tokenmon install
# or:
uv run tokenmon install --providers anthropic,openrouter

# 3. Tell each SDK / agent which provider proxy to talk to.
echo 'export ANTHROPIC_BASE_URL=http://127.0.0.1:8788' >> ~/.zshrc       # Claude Code → Anthropic
# OpenRouter — set wherever your OpenCode / SDK reads its base URL,
# e.g. config.json:
#   "openrouter": { "baseURL": "http://127.0.0.1:8789/api/v1", ... }

# 4. Restart your agent so it picks up the new base URL.
source ~/.zshrc
```

### Adding OpenRouter (or any OpenAI-compatible provider)

OpenRouter aggregates many models behind an OpenAI-compatible API. Tokenmon
proxies it on a separate port (`:8789` by default).

```bash
uv run tokenmon install --providers anthropic,openrouter
```

Then point your client (e.g. OpenCode) at `http://127.0.0.1:8789/api/v1`
instead of `https://openrouter.ai/api/v1`. Tokenmon forwards transparently
and records usage. For streaming requests Tokenmon also injects
`stream_options.include_usage = true` so the upstream returns token counts —
without that flag, OpenAI-style streams hide them.

For models we don't have hardcoded pricing for (most of OpenRouter's catalog),
the "Geschätzte Kosten" line shows the partial cost plus a coverage
percentage so you know how much of your usage isn't priced.

After this:

- The proxy runs in the background (KeepAlive=true via launchd).
- The 🥚 appears in your macOS menubar showing today's total tokens.
- Every Claude Code call goes through the proxy and is recorded.

## Verify it works

```bash
# 1. Check status
uv run tokenmon status

# 2. Make a test call (replace sk-... with your real Anthropic key)
curl http://127.0.0.1:8788/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-haiku-4-5","max_tokens":50,"messages":[{"role":"user","content":"say hi"}]}'

# 3. Re-run status — should now show 1 request
uv run tokenmon status
```

## Commands

| Command | What it does |
|---|---|
| `tokenmon status` | Show today's usage + LaunchAgent state |
| `tokenmon install` | Install + load LaunchAgents |
| `tokenmon uninstall` | Stop + remove LaunchAgents |

## Files

- `~/.tokenmon/usage.db` — SQLite database (one row per request)
- `~/.tokenmon/proxy.log` — proxy application log
- `~/.tokenmon/com.tokenmon.proxy.{out,err}.log` — launchd stdout/stderr
- `~/Library/LaunchAgents/com.tokenmon.proxy.plist`
- `~/Library/LaunchAgents/com.tokenmon.menubar.plist`

## Troubleshooting

**Menubar shows ⚠️ instead of 🥚** — the proxy isn't reachable on
`127.0.0.1:8788`. Click the menubar item → "Proxy neustarten", or:

```bash
launchctl kickstart -k gui/$(id -u)/com.tokenmon.proxy
tail -f ~/.tokenmon/com.tokenmon.proxy.err.log
```

**Claude Code calls aren't being tracked** — check that
`echo $ANTHROPIC_BASE_URL` prints the proxy URL. Claude Code reads it at
startup, so restart Claude Code after setting the env var.

**You moved the project directory** — the launchd plists hard-code the venv
path. Re-run `uv run tokenmon install` from the new location.

## Scope (v0.1)

- Anthropic only. Other providers (OpenAI, Google, …) are out of scope.
- macOS only. The menubar uses `rumps`/AppKit; the proxy itself is portable.
- No retention policy — the SQLite DB grows forever until you `DELETE` from it.
