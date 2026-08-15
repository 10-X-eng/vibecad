# SPDX-License-Identifier: LGPL-2.1-or-later

"""Coincident-specific behavior for the shared fake Sketcher host."""

from __future__ import annotations

import math


class FakeSketchCoincidentMixin:
    """Minimal solver behavior exercised by Native Coincident tests."""

    def _refresh_center_dependent_geometry(self, index: int) -> None:
        if index < 0:
            return
        geometry = self.getGeometry(index)
        if geometry.TypeId in {"Part::GeomArcOfCircle", "Part::GeomArcOfEllipse"}:
            geometry.StartPoint = geometry._point(geometry.FirstParameter)
            geometry.EndPoint = geometry._point(geometry.LastParameter)

    def _move_exact_point(
        self,
        index: int,
        position: int,
        x: float,
        y: float,
    ) -> None:
        self._set_point_coordinate(index, position, "x", x)
        self._set_point_coordinate(index, position, "y", y)
        if position == 3:
            self._refresh_center_dependent_geometry(index)

    def _solve_coincident(self, constraint) -> None:
        first = self.getPoint(constraint.First, constraint.FirstPos)
        second = self.getPoint(constraint.Second, constraint.SecondPos)
        if constraint.Second >= 0:
            self._move_exact_point(
                constraint.Second,
                constraint.SecondPos,
                float(first.x),
                float(first.y),
            )
            return
        if constraint.First >= 0:
            self._move_exact_point(
                constraint.First,
                constraint.FirstPos,
                float(second.x),
                float(second.y),
            )
            return
        raise ValueError("Fake Coincident solver cannot move two fixed points.")

    def _line_projection(self, geometry, x: float, y: float) -> tuple[float, float]:
        delta_x = float(geometry.EndPoint.x) - float(geometry.StartPoint.x)
        delta_y = float(geometry.EndPoint.y) - float(geometry.StartPoint.y)
        length_squared = delta_x * delta_x + delta_y * delta_y
        if length_squared <= 1.0e-24:
            raise ValueError("Fake zero-length line cannot receive a point.")
        ratio = (
            (x - float(geometry.StartPoint.x)) * delta_x
            + (y - float(geometry.StartPoint.y)) * delta_y
        ) / length_squared
        return (
            float(geometry.StartPoint.x) + ratio * delta_x,
            float(geometry.StartPoint.y) + ratio * delta_y,
        )

    @staticmethod
    def _circle_projection(geometry, x: float, y: float) -> tuple[float, float]:
        delta_x = x - float(geometry.Center.x)
        delta_y = y - float(geometry.Center.y)
        distance = math.hypot(delta_x, delta_y)
        if distance <= 1.0e-12:
            return (
                float(geometry.Center.x) + float(geometry.Radius),
                float(geometry.Center.y),
            )
        scale = float(geometry.Radius) / distance
        return (
            float(geometry.Center.x) + delta_x * scale,
            float(geometry.Center.y) + delta_y * scale,
        )

    def _solve_point_on_object(self, constraint) -> None:
        if constraint.First < 0:
            raise ValueError("Fake PointOnObject solver requires a movable point.")
        point = self.getPoint(constraint.First, constraint.FirstPos)
        curve = self.getGeometry(constraint.Second)
        if self.isPointOnCurve(
            constraint.Second,
            float(point.x),
            float(point.y),
        ):
            return
        if curve.TypeId == "Part::GeomBSplineCurve" and any(
            math.isclose(float(point.x), float(interpolation_point.x), abs_tol=1.0e-9)
            and math.isclose(
                float(point.y),
                float(interpolation_point.y),
                abs_tol=1.0e-9,
            )
            for interpolation_point in curve._interpolation_points
        ):
            return
        if curve.TypeId == "Part::GeomLineSegment":
            target = self._line_projection(curve, float(point.x), float(point.y))
        elif curve.TypeId in {"Part::GeomCircle", "Part::GeomArcOfCircle"}:
            target = self._circle_projection(curve, float(point.x), float(point.y))
        else:
            raise ValueError("Fake PointOnObject solver received unsupported geometry.")
        self._move_exact_point(
            constraint.First,
            constraint.FirstPos,
            target[0],
            target[1],
        )
