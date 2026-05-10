# Tokenmon — Project Notes

## Running the menubar app

Tokenmon ships as a real `.app` bundle (`dist/Tokenmon.app`, bundle id
`com.tokenmon.menubar`). It used to be just `python -m tokenmon.menubar`,
but macOS' Privacy/TCC system can't grant Screen Recording (needed for
the companion-chat window-context capture) to a free-floating Python
script — it keys permissions on the app bundle, not the interpreter.

The bundle is **alias mode** (built with `py2app -A`) — so it contains
no Python code, only a tiny bootstrap that loads `src/tokenmon/...` at
runtime. Code edits go live without a rebuild.

The menubar app holds a process-singleton lock at `~/.tokenmon/menubar.lock`
(via `fcntl.flock`). Starting a second instance fails fast with the active
PID printed to stderr — no more accidental double icons in the topbar.

### Start / restart

A LaunchAgent (`~/Library/LaunchAgents/com.tokenmon.menubar.plist`) keeps
the app alive in the background and points its `ProgramArguments` at
`dist/Tokenmon.app/Contents/MacOS/Tokenmon`.

```bash
# Restart after editing any Python source — most common case.
launchctl kickstart -k gui/$(id -u)/com.tokenmon.menubar

# Or open the .app directly (e.g. when LaunchAgent is unloaded).
open dist/Tokenmon.app
```

### Rebuild the .app

Only needed when you change `setup.py`, `Info.plist`, the entry script
(`scripts/tokenmon_app.py`), bundle id, icon, or add a new top-level
package outside `tokenmon/`.

```bash
launchctl unload ~/Library/LaunchAgents/com.tokenmon.menubar.plist
rm -rf build dist
uv run python setup.py py2app -A
launchctl load ~/Library/LaunchAgents/com.tokenmon.menubar.plist
```

TCC-permissions persist across rebuilds as long as the bundle stays at
`dist/Tokenmon.app` and `CFBundleIdentifier` stays `com.tokenmon.menubar`.
Move or rename the bundle and macOS treats it as a new app — the user
has to re-grant Screen Recording.

### Stop

```bash
launchctl unload ~/Library/LaunchAgents/com.tokenmon.menubar.plist
# or one-shot:
pkill -f "Tokenmon.app/Contents/MacOS/Tokenmon"
```

A stale lockfile from a crashed process is detected automatically — `flock`
sees no holder and the next start succeeds without manual cleanup.

### Why alias mode and not a full build

Full py2app builds (`py2app` without `-A`) bundle a self-contained Python
framework into the .app — slower to produce, ~100 MB, and any code change
needs a rebuild. Alias mode references the active venv's interpreter
and source tree, so the .app is ~200 KB and code edits are instant.

Notable caveat for alias mode: `py2app` 0.28 + `setuptools` ≥ 80
collide because py2app rejects any `install_requires` on the
Distribution, and modern setuptools auto-fills it from
`pyproject.toml`'s `[project.dependencies]`. We work around that with
the `_NoRequiresDistribution` subclass in `setup.py` plus a
`setuptools<80` pin in dev-deps. If py2app ever publishes ≥ 0.29 this
workaround can be deleted.

## Tests

```bash
uv run pytest        # full suite, ~318 tests, < 5 s
```

Tests are DB- and network-isolated via autouse fixtures in `tests/conftest.py`
(`_isolate_db` redirects `DB_PATH` to `tmp_path`; `_isolate_sprites` stubs
`pokemon.ensure_sprite` so no HTTP calls leak).

UI tests that need AppKit guard with `pytest.importorskip("AppKit")` so the
suite still runs cleanly on non-macOS CI.

## Popover architecture

`src/tokenmon/popover/` is split into infrastructure and pane controllers:

```
popover/
  _main.py          # TokenmonPopover: sidebar, _show_pane registry, lifecycle
  widgets.py        # UI atoms (NSView subclasses, layout constants)
  animation.py      # NSTimer-driven step runners + step builders (pure)
  _actions.py       # Pure helpers (title_for_action, etc.)
  _handlers.py      # _ActionHandler — generic NSObject click bridge
  panes/
    base.py         # PaneController plain-Python base
    usage.py        # UsageController
    tokendex.py     # TokendexController + PokedexDetailController
    box.py          # BoxController + _NicknameInlineHandler
    items.py        # ItemsController + claim animation
    encounter.py    # EncounterController + _ItemRowHandler + reveal
    pokemon.py      # PokemonController + pat-animation
    catch_animation.py  # CatchAnimationController (ephemeral pane)
```

Each pane controller owns its own state and click-handler GC anchors via
`PaneController._handlers`. `TokenmonPopover._show_pane` instantiates the
matching controller from a dict registry, calls `build_view()`, and stores
the controller on `self._current_controller` so cross-pane triggers
(`_begin_catch_animation`, `_begin_catch_reveal`, `_pat_step`,
`_claim_step` …) can route to the active controller.

### When adding a new pane

1. Add the pane id to `popover/widgets.py` and put it in the sidebar list
   in `TokenmonPopover.initWithApp_`.
2. Create `popover/panes/<name>.py` with `class XxxController(PaneController)`
   and a `build_view() -> NSView`. Anchor any `NSObject` handlers (button
   targets, NSTimer targets) on `self._handlers`.
3. Register the controller in the dict in `TokenmonPopover._build_controller_view`.
4. Add at least a smoke test in `tests/test_pane_<name>.py` using a
   `_FakePopover` stand-in. See `tests/test_pane_usage.py` for the pattern.

Don't put pane-specific state on `TokenmonPopover` unless it has to survive
across in-pane re-renders (e.g. `_claim_active`, `_pending_reveal_pokemon`).

## Companion chat window-context

When the user double-clicks the companion sprite, the chat panel
captures a snapshot of whatever window the user was looking at and
shows it in the transcript header.

`src/tokenmon/context/`:

```
context/
  snapshot.py                  # ContextSnapshot dataclass — platform-neutral
  resolver.py                  # picks provider by platform; 0.5 s cache TTL
  providers/
    base.py                    # ContextProvider Protocol + run_subprocess
    macos_screenshot.py        # *** the only wired-up provider ***
    macos_appscript.py         # Safari (kept, unwired — better-fidelity option)
    macos_terminal.py          # Terminal/iTerm2/Kitty/WezTerm (kept, unwired)
```

`macos_screenshot.ScreenshotOCRProvider` is the sole provider in
`build_default_resolver()`. It calls `CGWindowListCreateImage` on the
focused window, then `VNRecognizeTextRequest` (Apple's Vision
framework, local + offline) for OCR. One Screen Recording permission
covers every app — Kitty without remote-control, Slack, VS Code, etc.

The AppleScript / per-terminal providers are still in the codebase
because they yield structured data (URLs, cwd, scrollback) that OCR
loses, but wiring them back in is a one-line change in
`build_default_resolver`. Don't reach for them unless OCR proves
insufficient — the universal coverage of one-permission OCR is the
whole point.

Capture is invoked from `Overlay.show_chat` *before* `makeKeyAndOrderFront`
so `NSWorkspace.frontmostApplication()` still points at the user's
previous app. The snapshot is rendered via
`ContextSnapshot.short_summary()` in the transcript header and cleared
in `Overlay.hide_chat()` so it doesn't accumulate across opens.
