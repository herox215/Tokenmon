"""Trainer-spawn gating tests."""
from __future__ import annotations

import pytest

from tokenmon import trainer
from tokenmon.storage import (
    insert_trainer, mark_trainer_resolved, get_pending_trainer,
)


@pytest.fixture(autouse=True)
def _stub_learnsets(monkeypatch):
    """Don't hit the network — return a small synthetic learnset."""
    from tokenmon import learnsets_remote
    monkeypatch.setattr(
        learnsets_remote, "get_learnset",
        lambda dex_id: [(1, "tackle"), (5, "growl")],
    )


def test_no_spawn_below_min_tokens(db_path, monkeypatch):
    """Tokens below the threshold never spawn."""
    monkeypatch.setattr(trainer, "_RNG", _DummyRNG(0.0))
    assert trainer.maybe_spawn(output_tokens=0, path=db_path) is None
    assert trainer.maybe_spawn(output_tokens=10, path=db_path) is None


def test_force_spawn_bypasses_probability(db_path, monkeypatch):
    monkeypatch.setattr(trainer, "_RNG", _DummyRNG(0.99))
    t = trainer.maybe_spawn(force=True, path=db_path)
    assert t is not None
    pending = get_pending_trainer(db_path)
    assert pending is not None
    assert pending.id == t.id


def test_pending_trainer_blocks_new_spawn(db_path, monkeypatch):
    """If there's already a pending trainer, never spawn another."""
    insert_trainer(
        name="Existing", title="Lass", difficulty="easy",
        seed=1,
        team=[{
            "species_dex_id": 1, "level": 5, "nature": "Hardy",
            "ivs": (0, 0, 0, 0, 0, 0), "move_keys": ("tackle",),
        }],
        path=db_path,
    )
    monkeypatch.setattr(trainer, "_RNG", _DummyRNG(0.0))
    assert trainer.maybe_spawn(force=True, path=db_path) is None


def test_cooldown_blocks_natural_spawn(db_path, monkeypatch):
    """A natural spawn within COOLDOWN_SECONDS of the last is rejected.
    ``force=True`` still bypasses cooldown — used by the debug button."""
    monkeypatch.setattr(trainer, "_RNG", _DummyRNG(0.0))
    first = trainer.maybe_spawn(force=True, path=db_path)
    assert first is not None
    mark_trainer_resolved(first.id, status="ran", path=db_path)
    # Natural spawn (force=False) is blocked by cooldown.
    assert trainer.maybe_spawn(
        output_tokens=2000, force=False, path=db_path,
    ) is None
    # Force bypasses cooldown — debug button needs this.
    assert trainer.maybe_spawn(force=True, path=db_path) is not None


def test_spawn_probability_zero_below_min():
    assert trainer.spawn_probability(0) == 0.0
    assert trainer.spawn_probability(10) == 0.0


def test_spawn_probability_third_of_max():
    """At the cap, probability is roughly ⅓ of (1 - 1/e) — verify it's
    well under the wild-encounter equivalent."""
    p = trainer.spawn_probability(2000)
    assert 0 < p < 0.4


# --- Helpers --------------------------------------------------------------


class _DummyRNG:
    """Minimal RNG stand-in that returns ``r`` from random() and uses
    Python's stdlib for everything else."""
    def __init__(self, r: float):
        self._r = r
        self._counter = 0

    def random(self) -> float:
        return self._r

    def randint(self, a: int, b: int) -> int:
        self._counter += 1
        # Deterministic-ish: return midpoint so tests don't surprise.
        return (a + b) // 2
