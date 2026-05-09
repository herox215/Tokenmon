"""Tests for the PokeAPI move-data fetcher.

We never hit the network in tests — ``urllib.request.urlopen`` is
monkey-patched. The cache file lives under ``~/.tokenmon/moves_cache.json``
which the existing ``_isolate_db`` autouse fixture redirects per test.
"""
from __future__ import annotations

import io
import json

import pytest


@pytest.fixture(autouse=True)
def _reset_module_cache():
    """Clear the in-memory cache before each test so disk redirection
    via the autouse db fixture takes effect."""
    from tokenmon import moves_remote
    moves_remote.clear_cache()
    # Also reset the module-level CACHE_PATH because the test redirects
    # DB_DIR — re-import isn't enough since it was bound at import time.
    from tokenmon.storage import DB_DIR
    moves_remote.CACHE_PATH = DB_DIR / "moves_cache.json"
    yield
    moves_remote.clear_cache()


def _fake_payload(name="tackle", type_="normal", category="physical",
                  power=40, accuracy=100, pp=35, priority=0,
                  effect_entries=None, flavor_text_entries=None,
                  effect_chance=None,
                  ailment="none", ailment_chance=0, flinch_chance=0) -> dict:
    payload = {
        "name": name,
        "type": {"name": type_},
        "damage_class": {"name": category},
        "power": power,
        "accuracy": accuracy,
        "pp": pp,
        "priority": priority,
        "meta": {
            "ailment": {"name": ailment},
            "ailment_chance": ailment_chance,
            "flinch_chance": flinch_chance,
        },
    }
    if effect_entries is not None:
        payload["effect_entries"] = effect_entries
    if flavor_text_entries is not None:
        payload["flavor_text_entries"] = flavor_text_entries
    if effect_chance is not None:
        payload["effect_chance"] = effect_chance
    return payload


class _FakeResponse:
    def __init__(self, payload: dict):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_get_move_returns_typed_move(monkeypatch, db_path):
    from tokenmon import moves_remote

    def fake_urlopen(req, timeout):
        return _FakeResponse(_fake_payload())

    monkeypatch.setattr(moves_remote.urllib.request, "urlopen", fake_urlopen)
    move = moves_remote.get_move_data("tackle")
    assert move is not None
    assert move.key == "tackle"
    assert move.type == "normal"
    assert move.category == "physical"
    assert move.power == 40
    assert move.accuracy == 100
    assert move.pp == 35


def test_get_move_caches_to_disk(monkeypatch, db_path):
    """First call hits the network; second call sees the cache."""
    from tokenmon import moves_remote

    calls = {"n": 0}

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        return _FakeResponse(_fake_payload())

    monkeypatch.setattr(moves_remote.urllib.request, "urlopen", fake_urlopen)
    a = moves_remote.get_move_data("tackle")
    b = moves_remote.get_move_data("tackle")
    assert a == b
    assert calls["n"] == 1


def test_status_move_has_null_power(monkeypatch, db_path):
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(
            _fake_payload("growl", category="status", power=None, accuracy=100),
        ),
    )
    move = moves_remote.get_move_data("growl")
    assert move is not None
    assert move.category == "status"
    assert move.power is None


def test_never_miss_move_has_null_accuracy(monkeypatch, db_path):
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(
            _fake_payload("swift", power=60, accuracy=None),
        ),
    )
    move = moves_remote.get_move_data("swift")
    assert move is not None
    assert move.accuracy is None


def test_network_failure_returns_none(monkeypatch, db_path):
    from tokenmon import moves_remote

    def fake_urlopen(req, timeout):
        raise OSError("simulated timeout")

    monkeypatch.setattr(moves_remote.urllib.request, "urlopen", fake_urlopen)
    assert moves_remote.get_move_data("nonexistent") is None


def test_malformed_payload_returns_none(monkeypatch, db_path):
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse({"name": "x"}),  # missing fields
    )
    assert moves_remote.get_move_data("x") is None


def test_unknown_category_returns_none(monkeypatch, db_path):
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(
            _fake_payload(category="bogus"),
        ),
    )
    assert moves_remote.get_move_data("tackle") is None


def test_description_substitutes_effect_chance(monkeypatch, db_path):
    """``$effect_chance`` placeholders must be filled with the numeric
    chance from the payload — otherwise the UI would literally show
    ``may have $effect_chance% chance``."""
    from tokenmon import moves_remote
    payload = _fake_payload(
        name="body-slam",
        effect_chance=30,
        effect_entries=[{
            "effect": "Inflicts regular damage. May paralyze.",
            "short_effect": "Inflicts damage; $effect_chance% chance to paralyze.",
            "language": {"name": "en"},
        }],
    )
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(payload),
    )
    move = moves_remote.get_move_data("body-slam")
    assert move is not None
    assert move.description == (
        "Inflicts damage; 30% chance to paralyze."
    )


def test_description_falls_back_to_flavor_text(monkeypatch, db_path):
    """No effect entries → use the most recent English flavor text,
    with newlines collapsed to spaces."""
    from tokenmon import moves_remote
    payload = _fake_payload(
        name="tackle",
        flavor_text_entries=[
            {
                "flavor_text": "Old\nflavor.",
                "language": {"name": "en"},
            },
            {
                "flavor_text": "A physical attack\nin which the user\fcharges.",
                "language": {"name": "en"},
            },
            {
                "flavor_text": "Deutsch.",
                "language": {"name": "de"},
            },
        ],
    )
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(payload),
    )
    move = moves_remote.get_move_data("tackle")
    assert move is not None
    # Most recent English entry wins; \n + \f collapse to spaces.
    assert move.description == "A physical attack in which the user charges."


def test_description_empty_when_payload_lacks_text(monkeypatch, db_path):
    """No effect entries, no flavor text → description is empty (and
    the tooltip just hides the description line)."""
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(_fake_payload()),
    )
    move = moves_remote.get_move_data("tackle")
    assert move is not None
    assert move.description == ""


def test_description_survives_cache_round_trip(monkeypatch, db_path):
    """Second call (cache hit) must yield the same description as the
    first — i.e. the cached slice carries enough raw fields for
    ``_parse_move`` to re-derive the text."""
    from tokenmon import moves_remote
    payload = _fake_payload(
        effect_chance=10,
        effect_entries=[{
            "effect": "long form",
            "short_effect": "Quick zap; $effect_chance% paralysis.",
            "language": {"name": "en"},
        }],
    )
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(payload),
    )
    first = moves_remote.get_move_data("tackle")
    second = moves_remote.get_move_data("tackle")  # cache hit
    assert first is not None and second is not None
    assert first.description == "Quick zap; 10% paralysis."
    assert second.description == first.description


def test_old_cache_without_description_fields_returns_empty(
    monkeypatch, db_path,
):
    """Caches written by the pre-Bug-2 module won't have the new keys.
    ``_parse_move`` must not blow up — it should just yield ``description=""``."""
    from tokenmon import moves_remote
    legacy_slice = {
        "name": "tackle",
        "type": {"name": "normal"},
        "damage_class": {"name": "physical"},
        "power": 40,
        "accuracy": 100,
        "pp": 35,
        "priority": 0,
    }
    move = moves_remote._parse_move(legacy_slice)
    assert move is not None
    assert move.description == ""


# --- Ailment / status meta extraction ------------------------------------


def test_status_move_extracts_guaranteed_ailment(monkeypatch, db_path):
    """Toxic-style status moves carry ailment_chance=0 — PokeAPI's
    convention for "guaranteed if the move is a status move"."""
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(_fake_payload(
            name="toxic", type_="poison", category="status",
            power=None, accuracy=90, pp=10,
            ailment="poison", ailment_chance=0,
        )),
    )
    move = moves_remote.get_move_data("toxic")
    assert move is not None
    assert move.ailment == "poison"
    assert move.ailment_chance == 0
    assert move.flinch_chance == 0


def test_secondary_effect_move_extracts_chance(monkeypatch, db_path):
    """Damaging moves with secondary effects (Sludge Bomb, Ice Beam)
    have a non-zero ailment_chance."""
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(_fake_payload(
            name="sludge-bomb", type_="poison", category="special",
            power=90, accuracy=100, pp=10,
            ailment="poison", ailment_chance=30,
        )),
    )
    move = moves_remote.get_move_data("sludge-bomb")
    assert move is not None
    assert move.ailment == "poison"
    assert move.ailment_chance == 30


def test_flinch_move_extracts_flinch_chance(monkeypatch, db_path):
    """Bite / Headbutt / Rock Slide carry flinch_chance independently of
    ailment (ailment is "none" for these moves)."""
    from tokenmon import moves_remote
    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(_fake_payload(
            name="bite", type_="dark", category="physical",
            power=60, accuracy=100, pp=25,
            ailment="none", ailment_chance=0, flinch_chance=30,
        )),
    )
    move = moves_remote.get_move_data("bite")
    assert move is not None
    assert move.ailment == "none"
    assert move.flinch_chance == 30


def test_meta_fields_survive_cache_round_trip(monkeypatch, db_path):
    """First fetch writes ailment + flinch into the cache; second fetch
    reads them back — the cached subset must include the meta block."""
    from tokenmon import moves_remote

    payload = _fake_payload(
        name="will-o-wisp", type_="fire", category="status",
        power=None, accuracy=85, pp=15,
        ailment="burn", ailment_chance=0,
    )
    calls = {"n": 0}

    def fake_urlopen(req, timeout):
        calls["n"] += 1
        return _FakeResponse(payload)

    monkeypatch.setattr(moves_remote.urllib.request, "urlopen", fake_urlopen)
    first = moves_remote.get_move_data("will-o-wisp")
    moves_remote.clear_cache()
    second = moves_remote.get_move_data("will-o-wisp")
    assert first == second
    assert calls["n"] == 1  # second hit served from disk cache
    assert second.ailment == "burn"
    assert second.ailment_chance == 0


def test_legacy_cache_without_meta_falls_back_to_no_status():
    """Cache rows written before this column existed have no ``meta`` —
    parsing them must not crash, and must yield "no status side effect"
    so old caches keep working without a manual wipe (the user's app
    refreshes per-move on demand)."""
    from tokenmon import moves_remote
    legacy_slice = {
        "name": "tackle",
        "type": {"name": "normal"},
        "damage_class": {"name": "physical"},
        "power": 40,
        "accuracy": 100,
        "pp": 35,
        "priority": 0,
    }
    move = moves_remote._parse_move(legacy_slice)
    assert move is not None
    assert move.ailment == "none"
    assert move.ailment_chance == 0
    assert move.flinch_chance == 0


def test_legacy_cache_entry_triggers_refetch(monkeypatch, db_path):
    """A user with a pre-status-migration cache (entries lacking ``meta``)
    must not be stuck without status effects forever. ``get_move_data``
    treats meta-less cache rows as a soft miss and re-fetches so the
    fresh payload (with ailment metadata) overwrites the stale one."""
    from tokenmon import moves_remote

    # Pre-seed a stale cache entry (legacy shape, no meta block).
    moves_remote._loaded = True
    moves_remote._moves = {
        "toxic": {
            "name": "toxic",
            "type": {"name": "poison"},
            "damage_class": {"name": "status"},
            "power": None, "accuracy": 90, "pp": 10, "priority": 0,
            # No "meta" key — pre-migration shape.
        },
    }

    fetched = {"n": 0}

    def fake_urlopen(req, timeout):
        fetched["n"] += 1
        return _FakeResponse(_fake_payload(
            name="toxic", type_="poison", category="status",
            power=None, accuracy=90, pp=10,
            ailment="poison", ailment_chance=0,
        ))

    monkeypatch.setattr(moves_remote.urllib.request, "urlopen", fake_urlopen)
    move = moves_remote.get_move_data("toxic")
    assert move is not None
    assert fetched["n"] == 1, "stale cache entry must trigger a re-fetch"
    assert move.ailment == "poison"
    assert move.ailment_chance == 0


def test_real_move_inflicts_status_via_engine(monkeypatch, db_path):
    """End-to-end: a real-shaped Toxic payload, parsed via moves_remote,
    runs through plan_turn and fires a StatusInflictedEvent. Pins the
    full pipeline — moves_remote → ailment_to_status registry →
    on_inflict — against future regressions where any link breaks."""
    import random
    from tokenmon import moves_remote
    from tokenmon.battle.engine import StatusInflictedEvent, plan_turn
    from tokenmon.battle.models import BattleStats

    monkeypatch.setattr(
        moves_remote.urllib.request, "urlopen",
        lambda req, timeout: _FakeResponse(_fake_payload(
            name="toxic", type_="poison", category="status",
            power=None, accuracy=100, pp=10,
            ailment="poison", ailment_chance=0,
        )),
    )
    toxic = moves_remote.get_move_data("toxic")
    assert toxic is not None

    def mk(name, types):
        return BattleStats(
            species_dex_id=1, level=20, types=types,
            hp_max=100, hp_current=100,
            attack=80, defense=80, sp_attack=80, sp_defense=80, speed=80,
            moves=(toxic,), move_pps=(10,), name=name,
        )

    events = plan_turn(
        mk("Atk", ("normal",)),
        mk("Def", ("grass",)),  # not poison/steel — vulnerable
        player_move=toxic, opp_move=toxic,
        rng=random.Random(0),
    )
    inflicts = [e for e in events if isinstance(e, StatusInflictedEvent)]
    assert any("badly poisoned" in e.message for e in inflicts), (
        f"Toxic should inflict bad-poison; got events: {events}"
    )
