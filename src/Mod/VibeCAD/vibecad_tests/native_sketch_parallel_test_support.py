# SPDX-License-Identifier: LGPL-2.1-or-later

"""Parallel behavior for the shared fake Sketcher host."""

from __future__ import annotations

import math


class FakeSketchParallelMixin:
    def _solve_parallel(self, constraint) -> None:
        first = self.getGeometry(constraint.First)
        second = self.getGeometry(constraint.Second)
        if constraint.Second >= 0:
            reference = first
            movable = second
        elif constraint.First >= 0:
            reference = second
            movable = first
        else:
            raise ValueError("Fake Parallel cannot move two fixed lines.")
        reference_delta = (
            float(reference.EndPoint.x) - float(reference.StartPoint.x),
            float(reference.EndPoint.y) - float(reference.StartPoint.y),
        )
        reference_length = math.hypot(*reference_delta)
        movable_delta = (
            float(movable.EndPoint.x) - float(movable.StartPoint.x),
            float(movable.EndPoint.y) - float(movable.StartPoint.y),
        )
        movable_length = math.hypot(*movable_delta)
        if reference_length == 0.0 or movable_length == 0.0:
            raise ValueError("Fake Parallel requires nonzero lines.")
        movable.EndPoint.x = (
            float(movable.StartPoint.x)
            + reference_delta[0] * movable_length / reference_length
        )
        movable.EndPoint.y = (
            float(movable.StartPoint.y)
            + reference_delta[1] * movable_length / reference_length
        )
