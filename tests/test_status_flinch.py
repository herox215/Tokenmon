"""Tests for the flinch volatile status handler."""
from __future__ import annotations

import random
from dataclasses import replace

from tokenmon.battle.engine import (
    AttackEvent,
    StatusPreventedEvent,
    plan_turn,
)
from tokenmon.battle.models import BattleStats, Move
from tokenmon.battle.status import (
    StatusState,
    VOLATILE_REGISTRY,
    VolatileStatus,
)
from tokenmon.battle.status_handlers import flinch  # noqa: F401  (registers handlers)


TACKLE = Move(
    key="tackle", name="Tackle", type="normal", category="physical",
    power=40, accuracy=100, pp=35,
)
BITE_ALWAYS = Move(
    key="bite", name="Bite", type="dark", category="physical",
    power=60, accuracy=100, pp=25, flinch_chance=100,
)
BITE_SOMETIMES = Move(
    key="bite", name="Bite", type="dark", category="physical",
    power=60, accuracy=100, pp=25, flinch_chance=30,
)
HEADBUTT_NEVER = Move(
    key="headbutt", name="Headbutt", type="normal", category="physical",
    power=70, accuracy=100, pp=15, flinch_chance=0,
)


def _mon(
    *, name="Mon", speed=100, hp=100, types=("normal",),
    attack=100, defense=100, sp_attack=100, sp_defense=100,
    moves=(TACKLE,), status=None,
) -> BattleStats:
    return BattleStats(
        species_dex_id=1, level=20, types=types,
        hp_max=hp, hp_current=hp,
        attack=attack, defense=defense,
        sp_attack=sp_attack, sp_defense=sp_defense,
        speed=speed,
        moves=moves, move_pps=tuple(m.pp for m in moves),
        name=name,
        status=status if status is not None else StatusState(),
    )


def _handlers():
    return VOLATILE_REGISTRY[VolatileStatus.FLINCH]


# --- can_inflict ---------------------------------------------------------


def test_can_inflict_true_for_healthy():
    mon = _mon()
    assert _handlers().can_inflict(mon) is True


def test_can_inflict_false_when_already_flinching():
    mon = _mon(status=StatusState(flinch=True))
    assert _handlers().can_inflict(mon) is False


# --- on_inflict ----------------------------------------------------------


def test_on_inflict_sets_flag_and_emits_no_events():
    mon = _mon()
    new_mon, events = _handlers().on_inflict(
        mon,
        attacker=mon, move=TACKLE, actor="player", target_side="opp",
        rng=random.Random(0),
    )
    assert new_mon.status.flinch is True
    assert events == []


def test_on_inflict_noop_when_already_flinching():
    mon = _mon(status=StatusState(flinch=True))
    new_mon, events = _handlers().on_inflict(
        mon,
        attacker=mon, move=TACKLE, actor="player", target_side="opp",
        rng=random.Random(0),
    )
    assert new_mon.status.flinch is True
    assert events == []
    assert new_mon is mon or new_mon == mon


def test_on_inflict_preserves_other_status_fields():
    from tokenmon.battle.status import NonVolatileStatus
    initial = StatusState(
        non_volatile=NonVolatileStatus.POISON, nv_counter=2,
        confusion_turns=3, flinch=False,
    )
    mon = _mon(status=initial)
    new_mon, _ = _handlers().on_inflict(
        mon,
        attacker=mon, move=TACKLE, actor="player", target_side="opp",
        rng=random.Random(0),
    )
    assert new_mon.status.flinch is True
    assert new_mon.status.non_volatile == NonVolatileStatus.POISON
    assert new_mon.status.nv_counter == 2
    assert new_mon.status.confusion_turns == 3


# --- pre_action ----------------------------------------------------------


def test_pre_action_with_flinch_clears_flag_and_blocks():
    mon = _mon(name="Pidgey", status=StatusState(flinch=True))
    result = _handlers().pre_action(mon, "opp", rng=random.Random(0))
    assert result.can_act is False
    assert result.new_stats.status.flinch is False
    assert len(result.events) == 1
    ev = result.events[0]
    assert isinstance(ev, StatusPreventedEvent)
    assert ev.side == "opp"
    assert ev.status == VolatileStatus.FLINCH.value
    assert "flinched" in ev.message.lower()
    assert "Pidgey" in ev.message


def test_pre_action_without_flinch_is_noop():
    mon = _mon(status=StatusState(flinch=False))
    result = _handlers().pre_action(mon, "player", rng=random.Random(0))
    assert result.can_act is True
    assert result.new_stats.status.flinch is False
    assert result.events == []


def test_pre_action_always_clears_flag_when_it_fires():
    mon = _mon(status=StatusState(flinch=True))
    result = _handlers().pre_action(mon, "player", rng=random.Random(0))
    assert result.new_stats.status.flinch is False


def test_pre_action_uses_default_label_when_name_blank():
    mon = _mon(name="", status=StatusState(flinch=True))
    result_p = _handlers().pre_action(mon, "player", rng=random.Random(0))
    result_o = _handlers().pre_action(mon, "opp", rng=random.Random(0))
    assert "Your Pokémon" in result_p.events[0].message
    assert "Foe" in result_o.events[0].message


# --- engine integration --------------------------------------------------


def test_flinch_chance_100_always_flinches_defender_when_attacker_faster():
    fast = _mon(name="Fast", speed=200, moves=(BITE_ALWAYS,))
    slow = _mon(name="Slow", speed=20, moves=(TACKLE,))
    for seed in range(50):
        events = plan_turn(
            fast, slow,
            player_move=BITE_ALWAYS, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        prevented = [
            e for e in events
            if isinstance(e, StatusPreventedEvent)
            and e.status == VolatileStatus.FLINCH.value
        ]
        slow_attacks = [
            e for e in events
            if isinstance(e, AttackEvent) and e.actor == "opp"
        ]
        assert len(prevented) == 1, f"seed {seed}: missing flinch prevent"
        assert prevented[0].side == "opp"
        assert slow_attacks == [], (
            f"seed {seed}: slower defender attacked despite flinch_chance=100"
        )


def test_flinch_chance_zero_never_flinches():
    fast = _mon(name="Fast", speed=200, moves=(HEADBUTT_NEVER,))
    slow = _mon(name="Slow", speed=20, hp=500, moves=(TACKLE,))
    for seed in range(50):
        events = plan_turn(
            fast, slow,
            player_move=HEADBUTT_NEVER, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        prevented = [
            e for e in events
            if isinstance(e, StatusPreventedEvent)
            and e.status == VolatileStatus.FLINCH.value
        ]
        assert prevented == [], f"seed {seed}: flinched with flinch_chance=0"


def test_flinch_blocks_slower_defender_attack_2000_seeds():
    fast = _mon(name="Fast", speed=200, moves=(BITE_ALWAYS,))
    slow = _mon(name="Slow", speed=20, hp=500, moves=(TACKLE,))
    for seed in range(2000):
        events = plan_turn(
            fast, slow,
            player_move=BITE_ALWAYS, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        slow_attacks = [
            e for e in events
            if isinstance(e, AttackEvent) and e.actor == "opp"
        ]
        assert slow_attacks == [], (
            f"seed {seed}: slower defender attacked despite flinch_chance=100"
        )


def test_flinch_clears_for_next_turn():
    fast = _mon(name="Fast", speed=200, hp=500, moves=(BITE_ALWAYS,))
    slow = _mon(name="Slow", speed=20, hp=500, moves=(TACKLE,))
    rng = random.Random(0)

    events_t1 = plan_turn(
        fast, slow,
        player_move=BITE_ALWAYS, opp_move=TACKLE,
        rng=rng,
    )
    prevented_t1 = [
        e for e in events_t1
        if isinstance(e, StatusPreventedEvent)
        and e.status == VolatileStatus.FLINCH.value
    ]
    slow_attacks_t1 = [
        e for e in events_t1
        if isinstance(e, AttackEvent) and e.actor == "opp"
    ]
    assert len(prevented_t1) == 1
    assert slow_attacks_t1 == []

    fast_after = fast
    slow_after = slow
    for ev in events_t1:
        if isinstance(ev, AttackEvent) and ev.actor == "player":
            slow_after = replace(slow_after, hp_current=ev.defender_hp_after)

    slow_after_cleared = replace(
        slow_after,
        status=replace(slow_after.status, flinch=False),
    )

    events_t2 = plan_turn(
        fast_after, slow_after_cleared,
        player_move=TACKLE, opp_move=TACKLE,
        rng=rng,
    )
    prevented_t2 = [
        e for e in events_t2
        if isinstance(e, StatusPreventedEvent)
        and e.status == VolatileStatus.FLINCH.value
    ]
    slow_attacks_t2 = [
        e for e in events_t2
        if isinstance(e, AttackEvent) and e.actor == "opp"
    ]
    assert prevented_t2 == [], "flinch should be gone on the next turn"
    assert len(slow_attacks_t2) == 1, (
        "slower defender should attack normally on the turn after flinch"
    )


def test_flinch_chance_30_observable_distribution():
    fast = _mon(name="Fast", speed=200, hp=500, moves=(BITE_SOMETIMES,))
    slow = _mon(name="Slow", speed=20, hp=500, moves=(TACKLE,))
    flinch_count = 0
    total = 500
    for seed in range(total):
        events = plan_turn(
            fast, slow,
            player_move=BITE_SOMETIMES, opp_move=TACKLE,
            rng=random.Random(seed),
        )
        if any(
            isinstance(e, StatusPreventedEvent)
            and e.status == VolatileStatus.FLINCH.value
            for e in events
        ):
            flinch_count += 1
    assert 0 < flinch_count < total, (
        f"flinch_chance=30 produced extreme count {flinch_count}/{total}"
    )
