"""Per-pane controllers for the popover.

Each module owns the build_view() for one pane plus its local state
machine and click handlers. ``TokenmonPopover._show_pane`` instantiates
the matching controller, calls ``build_view()``, and stores the
controller on ``self._current_controller`` so cross-pane triggers
(``_begin_catch_animation`` etc.) can route to the active controller.
"""
