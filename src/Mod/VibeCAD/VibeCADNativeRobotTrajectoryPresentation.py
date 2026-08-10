# SPDX-License-Identifier: LGPL-2.1-or-later

"""History presentation semantics for Robot trajectory replacements."""

from __future__ import annotations

from typing import Any

from VibeCADNativeRobotTrajectory import NativeRobotTrajectoryError
from VibeCADNativeRobotTrajectoryState import MAX_TRAJECTORIES


def trajectory_visibility(obj: Any) -> bool:
    view = getattr(obj, "ViewObject", None)
    try:
        return bool(view.Visibility if view is not None else obj.Visibility)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotTrajectoryError(
            "A trajectory replacement has unreadable presentation state."
        ) from exc


def _owner_chain(obj: Any) -> tuple[Any, ...]:
    result = []
    seen = set()
    current = obj
    while current is not None:
        if current in seen or len(result) >= MAX_TRAJECTORIES:
            raise NativeRobotTrajectoryError(
                "A trajectory replacement has malformed History ownership."
            )
        seen.add(current)
        result.append(current)
        current = getattr(current, "VibeCADTimelineOwner", None)
    return tuple(result)


def replacement_presentations(
    old_sources: tuple[Any, ...],
    new_sources: tuple[Any, ...],
) -> tuple[Any, ...]:
    result = []
    for source in (*old_sources, *new_sources):
        for presentation in _owner_chain(source):
            if presentation not in result:
                trajectory_visibility(presentation)
                result.append(presentation)
    return tuple(result)


def _usable_at_history(obj: Any) -> bool:
    document = getattr(obj, "Document", None)
    reader = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if not callable(reader):
        return True
    try:
        return bool(reader(obj))
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as exc:
        raise NativeRobotTrajectoryError(
            "A trajectory replacement has unreadable History state."
        ) from exc


def _active_replacement_inputs(document: Any) -> set[Any]:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        return set()
    hidden = set()
    for operation in tuple(getattr(timeline, "Operations", ()) or ()):
        if not _usable_at_history(operation):
            continue
        for source in tuple(
            getattr(operation, "VibeCADTimelineReplacedInputs", ()) or ()
        ):
            hidden.update(_owner_chain(source))
    return hidden


def expected_replacement_visibility(
    prepared: Any,
    target: Any,
) -> tuple[tuple[Any, bool], ...]:
    if prepared.operation == "edge2_trac":
        return ()
    hidden = _active_replacement_inputs(target.Document)
    for source in prepared.sources:
        hidden.update(_owner_chain(source))
    return tuple(
        (presentation, presentation not in hidden)
        for presentation in prepared.replacement_presentations
    )


def reconcile_replacement_visibility(
    prepared: Any,
    target: Any,
) -> tuple[tuple[Any, bool], ...]:
    expected = expected_replacement_visibility(prepared, target)
    for presentation, visible in expected:
        presentation.Visibility = visible
    return expected
