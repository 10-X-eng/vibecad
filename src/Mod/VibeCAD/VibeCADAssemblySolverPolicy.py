# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Assembly solver policy shared by VibeCAD candidate publication."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def suspend_joint_autosolve() -> Iterator[None]:
    """Temporarily disable FreeCAD's interactive solve-on-joint-creation."""

    import Preferences

    preferences = Preferences.preferences()
    previous = bool(preferences.GetBool("SolveInJointCreation", True))
    preferences.SetBool("SolveInJointCreation", False)
    try:
        yield
    finally:
        preferences.SetBool("SolveInJointCreation", previous)


def set_joint_connectors_without_auto_solve(
    joint: Any,
    references: list[Any],
    *,
    preserve_placements: Iterable[Any] = (),
) -> None:
    """Configure one joint without solving a partially constructed graph.

    VibeScript validates and solves the complete Assembly graph explicitly.
    FreeCAD's interactive default solves immediately from
    ``setJointConnectors``; retaining that behavior in a batch candidate makes
    every intermediate graph observable and can strand the native solver on a
    graph that the source never declared as complete.
    """

    snapshots = []
    for component in preserve_placements:
        placement = component.Placement
        copy_placement = getattr(placement, "copy", None)
        snapshots.append(
            (component, copy_placement() if callable(copy_placement) else placement)
        )

    configured = False
    try:
        with suspend_joint_autosolve():
            joint.Proxy.setJointConnectors(joint, references)
        configured = True
    finally:
        # FreeCAD's interactive connector path pre-positions the unconstrained
        # side of every new joint. In a batch graph that silently destroys the
        # source's complete assembled starting pose one joint at a time. Keep
        # the connector frames it derived, but restore the authored component
        # placements before the one explicit complete-graph solve.
        for component, placement in snapshots:
            component.Placement = placement

    if configured and snapshots:
        joint.Proxy.updateJCSPlacements(joint)
