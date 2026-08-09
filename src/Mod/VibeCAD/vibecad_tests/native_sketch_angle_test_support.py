# SPDX-License-Identifier: LGPL-2.1-or-later

"""Angle-specific behavior for the shared fake Sketcher host."""

from __future__ import annotations

import math


class FakeSketchAngleMixin:
    """Minimal geometric behavior exercised by Native Angle tests."""

    @staticmethod
    def _line_direction(geometry, position: int = 1) -> tuple[float, float]:
        dx = float(geometry.EndPoint.x) - float(geometry.StartPoint.x)
        dy = float(geometry.EndPoint.y) - float(geometry.StartPoint.y)
        if position == 2:
            dx = -dx
            dy = -dy
        length = math.hypot(dx, dy)
        if length <= 1.0e-12:
            raise ValueError("Fake zero-length line has no direction.")
        return dx / length, dy / length

    def _constraint_line_direction(
        self,
        geometry_index: int,
        position: int,
    ) -> tuple[float, float]:
        if geometry_index == -1:
            return 1.0, 0.0
        if geometry_index == -2:
            return 0.0, 1.0
        return self._line_direction(self.getGeometry(geometry_index), position)

    def _set_line_ray_angle(
        self,
        geometry_index: int,
        position: int,
        angle: float,
    ) -> None:
        if geometry_index < 0:
            raise ValueError("Fake fixed geometry cannot rotate.")
        geometry = self.getGeometry(geometry_index)
        if geometry.TypeId != "Part::GeomLineSegment":
            raise ValueError("Fake angle solver requires a straight line.")
        direction_x = math.cos(angle)
        direction_y = math.sin(angle)
        length = math.hypot(
            float(geometry.EndPoint.x) - float(geometry.StartPoint.x),
            float(geometry.EndPoint.y) - float(geometry.StartPoint.y),
        )
        if length <= 1.0e-12:
            raise ValueError("Fake zero-length line cannot rotate.")
        if position == 2:
            geometry.StartPoint.x = float(geometry.EndPoint.x) + length * direction_x
            geometry.StartPoint.y = float(geometry.EndPoint.y) + length * direction_y
            return
        geometry.EndPoint.x = float(geometry.StartPoint.x) + length * direction_x
        geometry.EndPoint.y = float(geometry.StartPoint.y) + length * direction_y

    def _rotate_line_about_point(
        self,
        geometry_index: int,
        point: tuple[float, float],
        angle: float,
    ) -> None:
        if geometry_index < 0:
            raise ValueError("Fake fixed geometry cannot rotate.")
        geometry = self.getGeometry(geometry_index)
        if geometry.TypeId != "Part::GeomLineSegment":
            raise ValueError("Fake via-point solver requires a straight line.")
        cosine = math.cos(angle)
        sine = math.sin(angle)
        for endpoint in (geometry.StartPoint, geometry.EndPoint):
            dx = float(endpoint.x) - point[0]
            dy = float(endpoint.y) - point[1]
            endpoint.x = point[0] + dx * cosine - dy * sine
            endpoint.y = point[1] + dx * sine + dy * cosine

    def _solve_angle(self, constraint) -> None:
        value = float(constraint.Value)
        if constraint.Third > -2000:
            point = self.getPoint(constraint.Third, constraint.ThirdPos)
            current = self.calculateAngleViaPoint(
                constraint.First,
                constraint.Second,
                float(point.x),
                float(point.y),
            )
            self._rotate_line_about_point(
                constraint.Second,
                (float(point.x), float(point.y)),
                value - current,
            )
            return
        if constraint.Second > -2000:
            first = self._constraint_line_direction(
                constraint.First,
                constraint.FirstPos,
            )
            if constraint.Second >= 0:
                self._set_line_ray_angle(
                    constraint.Second,
                    constraint.SecondPos,
                    math.atan2(first[1], first[0]) + value,
                )
                return
            second = self._constraint_line_direction(
                constraint.Second,
                constraint.SecondPos,
            )
            self._set_line_ray_angle(
                constraint.First,
                constraint.FirstPos,
                math.atan2(second[1], second[0]) - value,
            )
            return
        geometry = self.getGeometry(constraint.First)
        if geometry.TypeId == "Part::GeomLineSegment":
            self._set_line_ray_angle(constraint.First, 1, value)
            return
        if geometry.TypeId == "Part::GeomArcOfCircle":
            geometry.LastParameter = float(geometry.FirstParameter) + value
            geometry.StartPoint = geometry._point(geometry.FirstParameter)
            geometry.EndPoint = geometry._point(geometry.LastParameter)
            return
        raise ValueError("Fake angle solver received unsupported geometry.")

    def isPointOnCurve(self, index: int, x: float, y: float) -> bool:
        geometry = self.getGeometry(index)
        if geometry.TypeId == "Part::GeomLineSegment":
            dx = float(geometry.EndPoint.x) - float(geometry.StartPoint.x)
            dy = float(geometry.EndPoint.y) - float(geometry.StartPoint.y)
            length = math.hypot(dx, dy)
            if length <= 1.0e-12:
                return False
            distance = abs(
                dx * (float(y) - float(geometry.StartPoint.y))
                - dy * (float(x) - float(geometry.StartPoint.x))
            ) / length
            return distance <= 1.0e-7
        if geometry.TypeId in {"Part::GeomCircle", "Part::GeomArcOfCircle"}:
            radius = math.hypot(
                float(x) - float(geometry.Center.x),
                float(y) - float(geometry.Center.y),
            )
            return math.isclose(
                radius,
                float(geometry.Radius),
                rel_tol=1.0e-9,
                abs_tol=1.0e-7,
            )
        return False

    def calculateAngleViaPoint(
        self,
        first_index: int,
        second_index: int,
        _x: float,
        _y: float,
    ) -> float:
        first = self._constraint_line_direction(first_index, 1)
        second = self._constraint_line_direction(second_index, 1)
        return math.atan2(
            first[0] * second[1] - first[1] * second[0],
            first[0] * second[0] + first[1] * second[1],
        )
