"""Per-status handler modules.

Each module registers its handlers via ``status.register_non_volatile`` /
``register_volatile`` at import time. Importing this package side-effect-
imports each one so the registries are populated.

Adding a new status:
  1. Create ``battle/status_handlers/<name>.py``.
  2. Define ``can_inflict`` / ``on_inflict`` / ``pre_action`` /
     ``end_of_turn`` / ``modify_attack`` / ``modify_speed`` as needed.
  3. Call ``register_non_volatile(...)`` or ``register_volatile(...)`` at
     module load.
  4. Add the module name to the ``_MODULES`` tuple below so the package
     import wires it.
"""
from __future__ import annotations

# Import each handler module for its registration side effects. Order is
# alphabetical — registries are dicts, so order doesn't affect runtime
# behavior, but keeping it stable helps when reading import errors.
_MODULES = (
    "burn",
    "confusion",
    "flinch",
    "freeze",
    "paralysis",
    "poison",
    "sleep",
)

for _name in _MODULES:
    try:
        __import__(f"{__name__}.{_name}", fromlist=[_name])
    except ModuleNotFoundError:
        # Per-status agent hasn't landed yet — skip silently. The
        # registry will reflect "not implemented" by simply having no
        # handlers for that status. The engine treats missing handlers
        # as no-ops, so the rest of the system still works.
        pass
