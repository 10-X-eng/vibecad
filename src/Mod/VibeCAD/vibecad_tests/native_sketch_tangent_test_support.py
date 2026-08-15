# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tangent behavior for the shared fake Sketcher host."""

from __future__ import annotations

import math


class FakeSketchTangentMixin:
    @staticmethod
    def _delta(geometry) -> tuple[float, float]:
        return (
            float(geometry.EndPoint.x) - float(geometry.StartPoint.x),
            float(geometry.EndPoint.y) - float(geometry.StartPoint.y),
        )

    @staticmethod
    def _set_line_endpoint(
        geometry,
        point,
        position: int,
        direction: tuple[float, float],
    ) -> None:
        current = FakeSketchTangentMixin._delta(geometry)
        length = math.hypot(*current)
        direction_length = math.hypot(*direction)
        unit = direction[0] / direction_length, direction[1] / direction_length
        if position == 1:
            geometry.StartPoint.x = float(point.x)
            geometry.StartPoint.y = float(point.y)
            geometry.EndPoint.x = float(point.x) + unit[0] * length
            geometry.EndPoint.y = float(point.y) + unit[1] * length
        else:
            geometry.EndPoint.x = float(point.x)
            geometry.EndPoint.y = float(point.y)
            geometry.StartPoint.x = float(point.x) - unit[0] * length
            geometry.StartPoint.y = float(point.y) - unit[1] * length

    @staticmethod
    def _parallel(
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> bool:
        denominator = math.hypot(*first) * math.hypot(*second)
        if denominator <= 1.0e-24:
            return False
        return abs(first[0] * second[1] - first[1] * second[0]) <= (
            1.0e-9 * denominator
        )

    @staticmethod
    def _tangent_at(geometry, point, position: int) -> tuple[float, float]:
        if geometry.TypeId == "Part::GeomLineSegment":
            return FakeSketchTangentMixin._delta(geometry)
        if geometry.TypeId in {"Part::GeomCircle", "Part::GeomArcOfCircle"}:
            radial = (
                float(point.x) - float(geometry.Center.x),
                float(point.y) - float(geometry.Center.y),
            )
            return -radial[1], radial[0]
        if geometry.TypeId == "Part::GeomBSplineCurve":
            span = max(
                1.0e-7,
                abs(float(geometry.LastParameter) - float(geometry.FirstParameter))
                * 1.0e-6,
            )
            if position == 1:
                first = geometry.value(float(geometry.FirstParameter))
                second = geometry.value(float(geometry.FirstParameter) + span)
            else:
                first = geometry.value(float(geometry.LastParameter) - span)
                second = geometry.value(float(geometry.LastParameter))
            return float(second.x) - float(first.x), float(second.y) - float(first.y)
        return 0.0, 0.0

    def _simple_tangent_satisfied(self, constraint) -> bool:
        first = self.getGeometry(constraint.First)
        second = self.getGeometry(constraint.Second)
        if first.TypeId == second.TypeId == "Part::GeomLineSegment":
            return self._parallel(self._delta(first), self._delta(second))
        if "LineSegment" in first.TypeId or "LineSegment" in second.TypeId:
            line = first if "LineSegment" in first.TypeId else second
            circle = second if line is first else first
            delta = self._delta(line)
            distance = abs(
                delta[0] * (
                    float(circle.Center.y) - float(line.StartPoint.y)
                )
                - delta[1]
                * (float(circle.Center.x) - float(line.StartPoint.x))
            ) / math.hypot(*delta)
            return math.isclose(
                distance,
                float(circle.Radius),
                rel_tol=1.0e-9,
                abs_tol=1.0e-7,
            )
        if not all(hasattr(item, "Center") for item in (first, second)):
            return False
        center_distance = math.hypot(
            float(second.Center.x) - float(first.Center.x),
            float(second.Center.y) - float(first.Center.y),
        )
        return any(
            math.isclose(center_distance, target, rel_tol=1.0e-9, abs_tol=1.0e-7)
            for target in (
                float(first.Radius) + float(second.Radius),
                abs(float(first.Radius) - float(second.Radius)),
            )
        )

    def _endpoint_tangent_satisfied(self, constraint) -> bool:
        first_point = self.getPoint(constraint.First, constraint.FirstPos)
        first_tangent = self._tangent_at(
            self.getGeometry(constraint.First),
            first_point,
            constraint.FirstPos,
        )
        if constraint.SecondPos:
            second_point = self.getPoint(constraint.Second, constraint.SecondPos)
            if math.hypot(
                float(second_point.x) - float(first_point.x),
                float(second_point.y) - float(first_point.y),
            ) > 1.0e-7:
                return False
            second_tangent = self._tangent_at(
                self.getGeometry(constraint.Second),
                second_point,
                constraint.SecondPos,
            )
        else:
            if not self.isPointOnCurve(
                constraint.Second,
                float(first_point.x),
                float(first_point.y),
            ):
                return False
            second_tangent = self._tangent_at(
                self.getGeometry(constraint.Second),
                first_point,
                0,
            )
        return self._parallel(first_tangent, second_tangent)

    def _via_point_tangent_satisfied(self, constraint) -> bool:
        point = self.getPoint(constraint.Third, constraint.ThirdPos)
        if not all(
            self.isPointOnCurve(index, float(point.x), float(point.y))
            for index in (constraint.First, constraint.Second)
        ):
            return False
        return self._parallel(
            self._tangent_at(self.getGeometry(constraint.First), point, 0),
            self._tangent_at(self.getGeometry(constraint.Second), point, 0),
        )

    def _tangent_satisfied(self, constraint) -> bool:
        if constraint.Third > -2000:
            return self._via_point_tangent_satisfied(constraint)
        if constraint.FirstPos:
            return self._endpoint_tangent_satisfied(constraint)
        return self._simple_tangent_satisfied(constraint)

    def _solve_simple_tangent(self, constraint) -> None:
        first = self.getGeometry(constraint.First)
        second = self.getGeometry(constraint.Second)
        first_type = first.TypeId
        second_type = second.TypeId
        if first_type == second_type == "Part::GeomLineSegment":
            reference, movable = (
                (first, second) if constraint.Second >= 0 else (second, first)
            )
            self._set_line_endpoint(
                movable,
                movable.StartPoint,
                1,
                self._delta(reference),
            )
            return
        if "LineSegment" in first_type or "LineSegment" in second_type:
            line = first if "LineSegment" in first_type else second
            circle = second if line is first else first
            delta = self._delta(line)
            length = math.hypot(*delta)
            side = delta[0] * (
                float(circle.Center.y) - float(line.StartPoint.y)
            ) - delta[1] * (float(circle.Center.x) - float(line.StartPoint.x))
            sign = 1.0 if side >= 0.0 else -1.0
            projection = (
                (float(circle.Center.x) - float(line.StartPoint.x)) * delta[0]
                + (float(circle.Center.y) - float(line.StartPoint.y)) * delta[1]
            ) / (length * length)
            circle.Center.x = (
                float(line.StartPoint.x)
                + projection * delta[0]
                - sign * delta[1] * float(circle.Radius) / length
            )
            circle.Center.y = (
                float(line.StartPoint.y)
                + projection * delta[1]
                + sign * delta[0] * float(circle.Radius) / length
            )
            return
        first_center = first.Center
        second_center = second.Center
        direction = (
            float(second_center.x) - float(first_center.x),
            float(second_center.y) - float(first_center.y),
        )
        direction_length = math.hypot(*direction)
        target = float(first.Radius) + float(second.Radius)
        second.Center.x = float(first_center.x) + direction[0] * target / direction_length
        second.Center.y = float(first_center.y) + direction[1] * target / direction_length

    def _solve_endpoint_curve_tangent(self, constraint) -> None:
        point = self.getPoint(constraint.First, constraint.FirstPos)
        first = self.getGeometry(constraint.First)
        second = self.getGeometry(constraint.Second)
        if second.TypeId != "Part::GeomLineSegment" or constraint.Second < 0:
            raise ValueError("Fake endpoint Tangent requires one editable target line.")
        self._set_line_endpoint(second, point, 1, self._delta(first))
        constraint.Value = -math.pi / 2.0

    def _solve_endpoint_endpoint_tangent(self, constraint) -> None:
        if constraint.Second >= 0:
            point = self.getPoint(constraint.First, constraint.FirstPos)
            reference = self.getGeometry(constraint.First)
            movable = self.getGeometry(constraint.Second)
            position = constraint.SecondPos
        elif constraint.First >= 0:
            point = self.getPoint(constraint.Second, constraint.SecondPos)
            reference = self.getGeometry(constraint.Second)
            movable = self.getGeometry(constraint.First)
            position = constraint.FirstPos
        else:
            raise ValueError("Fake endpoint Tangent cannot move fixed curves.")
        self._set_line_endpoint(movable, point, position, self._delta(reference))
        constraint.Value = -math.pi / 2.0

    def _solve_via_point_tangent(self, constraint) -> None:
        point = self.getPoint(constraint.Third, constraint.ThirdPos)
        first = self.getGeometry(constraint.First)
        second = self.getGeometry(constraint.Second)
        if first.TypeId != "Part::GeomLineSegment" or second.TypeId != "Part::GeomLineSegment":
            raise ValueError("Fake via-point Tangent requires lines.")
        reference, movable = (
            (first, second) if constraint.Second >= 0 else (second, first)
        )
        self._set_line_endpoint(movable, point, 1, self._delta(reference))
        constraint.Value = -math.pi / 2.0

    def _solve_tangent(self, constraint) -> None:
        if self._tangent_satisfied(constraint):
            if constraint.FirstPos or constraint.Third > -2000:
                constraint.Value = -math.pi / 2.0
            return
        if constraint.Third > -2000:
            self._solve_via_point_tangent(constraint)
        elif constraint.FirstPos and constraint.SecondPos:
            self._solve_endpoint_endpoint_tangent(constraint)
        elif constraint.FirstPos:
            self._solve_endpoint_curve_tangent(constraint)
        else:
            self._solve_simple_tangent(constraint)
