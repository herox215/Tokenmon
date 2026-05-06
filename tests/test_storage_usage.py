"""Usage-table tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tokenmon import storage


def test_insert_usage_round_trip(db_path):
    storage.insert_usage(
        storage.Usage(
            model="claude-x",
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=2,
            cache_creation_tokens=3,
        ),
        path=db_path,
    )
    totals = storage.query_today(path=db_path)
    assert totals.input_tokens == 10
    assert totals.output_tokens == 20
    assert totals.cache_read_tokens == 2
    assert totals.cache_creation_tokens == 3
    assert totals.request_count == 1


def test_query_today_by_model_groups(db_path):
    storage.insert_usage(storage.Usage(model="a", output_tokens=5), path=db_path)
    storage.insert_usage(storage.Usage(model="a", output_tokens=7), path=db_path)
    storage.insert_usage(storage.Usage(model="b", output_tokens=3), path=db_path)
    by_model = storage.query_today_by_model(path=db_path)
    assert by_model["a"].output_tokens == 12
    assert by_model["b"].output_tokens == 3


def test_latest_request_ts_returns_utc(db_path):
    assert storage.latest_request_ts(db_path) is None
    storage.insert_usage(storage.Usage(model="x", output_tokens=1), path=db_path)
    ts = storage.latest_request_ts(db_path)
    assert ts is not None
    assert ts.tzinfo is timezone.utc or ts.utcoffset() == timedelta(0)


def test_query_xp_for_pokemon_sums_output(db_path):
    pid = storage.insert_pokemon(
        caught_date=date.today(), species_dex_id=1, nature="Hardy",
        characteristic="X", path=db_path,
    )
    # Manually insert usage attributed to that pokemon.
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO requests (ts_utc, model, input_tokens, output_tokens, "
        "cache_read_tokens, cache_creation_tokens, trained_pokemon_id) "
        "VALUES (?, 'x', 0, 100, 0, 0, ?)",
        (datetime.now(timezone.utc).isoformat(), pid),
    )
    conn.execute(
        "INSERT INTO requests (ts_utc, model, input_tokens, output_tokens, "
        "cache_read_tokens, cache_creation_tokens, trained_pokemon_id) "
        "VALUES (?, 'x', 0, 50, 0, 0, ?)",
        (datetime.now(timezone.utc).isoformat(), pid),
    )
    conn.commit()
    conn.close()
    assert storage.query_xp_for_pokemon(pid, path=db_path) == 150


def test_query_today_excludes_other_days(db_path):
    """A request stamped to last week shouldn't appear in today's totals."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    last_week = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn.execute(
        "INSERT INTO requests (ts_utc, model, output_tokens) VALUES (?, 'x', 999)",
        (last_week,),
    )
    conn.commit()
    conn.close()
    totals = storage.query_today(path=db_path)
    assert totals.output_tokens == 0


def test_totals_total_tokens_property(db_path):
    t = storage.Totals(input_tokens=1, output_tokens=2, cache_read_tokens=3,
                       cache_creation_tokens=4)
    assert t.total_tokens == 10


def test_query_today_token_buckets_empty_day_returns_all_zero(db_path):
    buckets = storage.query_today_token_buckets(path=db_path)
    assert len(buckets) == 96  # 24 * 60 / 15
    assert all(b == 0 for b in buckets)


def test_query_today_token_buckets_aggregates_into_correct_slot(db_path):
    """Two requests in the same 15-min window land in the same bucket."""
    import sqlite3
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Berlin")
    # Pin to local 09:07 today — bucket index 9*4 + 0 = 36 (covers 09:00-09:15).
    local_now = datetime.now(tz).replace(hour=9, minute=7, second=0, microsecond=0)
    same_bucket = local_now.replace(minute=14)
    other_bucket = local_now.replace(hour=14, minute=22)

    conn = sqlite3.connect(db_path)
    for ts_local, out in (
        (local_now, 100),
        (same_bucket, 50),
        (other_bucket, 200),
    ):
        ts_utc = ts_local.astimezone(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO requests (ts_utc, model, output_tokens) VALUES (?, 'x', ?)",
            (ts_utc, out),
        )
    conn.commit()
    conn.close()

    buckets = storage.query_today_token_buckets(
        tz_name="Europe/Berlin", path=db_path,
    )
    assert buckets[36] == 150  # 09:00-09:15 holds the two grouped rows
    assert buckets[14 * 4 + 1] == 200  # 14:15-14:30 holds the other one
    assert sum(buckets) == 350


def test_query_today_token_buckets_excludes_other_days(db_path):
    """Requests outside today's local-day window do not appear in any bucket."""
    import sqlite3
    from datetime import datetime, timedelta, timezone

    last_week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO requests (ts_utc, model, output_tokens) VALUES (?, 'x', 999)",
        (last_week,),
    )
    conn.commit()
    conn.close()

    buckets = storage.query_today_token_buckets(path=db_path)
    assert sum(buckets) == 0


def test_query_today_token_buckets_invalid_minute_raises(db_path):
    import pytest
    with pytest.raises(ValueError):
        storage.query_today_token_buckets(bucket_minutes=7, path=db_path)


def test_query_today_token_buckets_custom_bucket_size(db_path):
    """A 30-min bucket size returns 48 slots."""
    buckets = storage.query_today_token_buckets(
        bucket_minutes=30, path=db_path,
    )
    assert len(buckets) == 48
