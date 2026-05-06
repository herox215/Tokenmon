"""Player-stats singleton tests."""
from __future__ import annotations

from tokenmon import storage


def test_money_default_zero(db_path):
    assert storage.get_money(db_path) == 0


def test_set_money_persists(db_path):
    storage.set_money(150, path=db_path)
    assert storage.get_money(db_path) == 150


def test_add_money_increments(db_path):
    storage.add_money(50, path=db_path)
    storage.add_money(30, path=db_path)
    assert storage.get_money(db_path) == 80


def test_add_money_floor_at_zero(db_path):
    storage.set_money(20, path=db_path)
    storage.add_money(-100, path=db_path)
    assert storage.get_money(db_path) == 0


def test_set_money_clamps_negative(db_path):
    storage.set_money(-5, path=db_path)
    assert storage.get_money(db_path) == 0


def test_add_money_with_explicit_conn(db_path):
    """Atomic-with-other-writes pattern: caller passes its own
    connection so money + xp + items can be one transaction."""
    from tokenmon.storage._db import _connect
    with _connect(db_path) as conn:
        balance = storage.add_money(40, conn=conn)
    assert balance == 40
    assert storage.get_money(db_path) == 40
