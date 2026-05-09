# Tokenmon — Project Notes

## Running the menubar app

The menubar app holds a process-singleton lock at `~/.tokenmon/menubar.lock`
(via `fcntl.flock`). Starting a second instance fails fast with the active
PID printed to stderr — no more accidental double icons in the topbar.

### Start

```bash
# Foreground
uv run python -m tokenmon.menubar

# Background (terminal can close)
nohup uv run python -m tokenmon.menubar > /tmp/tokenmon.log 2>&1 & disown
```

### Replace a running instance

```bash
uv run python -m tokenmon.menubar --restart
```

`--restart` reads the active PID from the lockfile, sends `SIGTERM`, waits up
to 5 s for the lock to release, then takes over. If the old process refuses
to exit it aborts cleanly (exit 1) so you don't end up with two copies.

### Stop

```bash
pkill -f "python.*tokenmon.menubar"
```

A stale lockfile from a crashed process is detected automatically — `flock`
sees no holder and the next start succeeds without manual cleanup.

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
