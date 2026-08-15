# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact-state helpers for constraint-toggle GUI lifecycle gates."""

from __future__ import annotations

from typing import Any

import FreeCADGui as Gui

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)


def selection_state(document: Any) -> tuple:
    return tuple(
        (
            str(item.ObjectName),
            tuple(str(value) for value in item.SubElementNames),
        )
        for item in Gui.Selection.getSelectionEx(document.Name)
    )


def sketch_records(
    sketch: Any,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    return (
        tuple(
            serialize_sketch_geometry(sketch, index)
            for index in range(int(sketch.GeometryCount))
        ),
        tuple(
            serialize_sketch_constraint(sketch, index)
            for index in range(int(sketch.ConstraintCount))
        ),
    )


def solver_issues(sketch: Any) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(int(value) for value in getattr(sketch, attribute))
        for attribute in (
            "ConflictingConstraints",
            "RedundantConstraints",
            "PartiallyRedundantConstraints",
            "MalformedConstraints",
        )
    )


def expression_records(sketch: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(path), str(expression)) for path, expression in sketch.ExpressionEngine
    )
