"""Trainer-name pool tests."""
from __future__ import annotations

import random

from tokenmon.battle.names import NAMES, TITLES, random_trainer_id


def test_pools_have_decent_size():
    assert len(TITLES) >= 15
    assert len(NAMES) >= 25


def test_random_trainer_id_returns_strings():
    title, name = random_trainer_id(random.Random(0))
    assert isinstance(title, str) and title in TITLES
    assert isinstance(name, str) and name in NAMES


def test_same_rng_state_yields_same_id():
    a = random_trainer_id(random.Random(7))
    b = random_trainer_id(random.Random(7))
    assert a == b


def test_pools_have_no_duplicates():
    assert len(set(TITLES)) == len(TITLES)
    assert len(set(NAMES)) == len(NAMES)
