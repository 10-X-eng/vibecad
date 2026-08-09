# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared real-GUI fixture for B-spline presentation layers."""

from __future__ import annotations

from typing import Any

import FreeCAD as App
import Part


CUBIC_POLES = (
    (-12.0, -3.0),
    (-4.0, 8.0),
    (6.0, -7.0),
    (14.0, 2.0),
)


def add_cubic_bspline(sketch: Any) -> int:
    curve = Part.BSplineCurve(
        [App.Vector(x, y) for x, y in CUBIC_POLES],
        [4, 4],
        [0.0, 1.0],
        False,
        3,
        [1.0, 1.0, 1.0, 1.0],
        False,
    )
    return int(sketch.addGeometry(curve, False))
