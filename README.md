# Tokenmon

Local Anthropic API token tracker with a macOS menubar app. 🥚

Runs an HTTP reverse proxy in front of `api.anthropic.com`, records every
request's token usage in SQLite, and shows today's total in the macOS menubar.
Click the egg for a per-model breakdown and estimated USD cost.

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

# 2. Install LaunchAgents (proxy + menubar autostart at login)
uv run tokenmon install

# 3. Tell Anthropic SDKs / Claude Code to use the proxy.
#    Add this to your ~/.zshrc (or ~/.bashrc):
echo 'export ANTHROPIC_BASE_URL=http://127.0.0.1:8788' >> ~/.zshrc

# 4. Restart Claude Code (or any other Anthropic SDK client) so it picks up the env var.
source ~/.zshrc
```

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
