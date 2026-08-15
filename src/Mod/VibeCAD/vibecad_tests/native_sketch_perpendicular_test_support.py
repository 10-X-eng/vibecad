# SPDX-License-Identifier: LGPL-2.1-or-later

"""Perpendicular behavior for the shared fake Sketcher host."""

from __future__ import annotations

import math


class FakeSketchPerpendicularMixin:
    @staticmethod
    def _delta(geometry) -> tuple[float, float]:
        return (
            float(geometry.EndPoint.x) - float(geometry.StartPoint.x),
            float(geometry.EndPoint.y) - float(geometry.StartPoint.y),
        )

    @staticmethod
    def _set_line_about_start(geometry, direction: tuple[float, float]) -> None:
        length = math.hypot(*FakeSketchPerpendicularMixin._delta(geometry))
        direction_length = math.hypot(*direction)
        geometry.EndPoint.x = (
            float(geometry.StartPoint.x) + direction[0] * length / direction_length
        )
        geometry.EndPoint.y = (
            float(geometry.StartPoint.y) + direction[1] * length / direction_length
        )

    def _perpendicular_lines(self, first_index: int, second_index: int) -> None:
        first = self.getGeometry(first_index)
        second = self.getGeometry(second_index)
        if second_index >= 0:
            reference, movable = first, second
        elif first_index >= 0:
            reference, movable = second, first
        else:
            raise ValueError("Fake Perpendicular cannot move two fixed lines.")
        reference_delta = self._delta(reference)
        self._set_line_about_start(
            movable,
            (-reference_delta[1], reference_delta[0]),
        )

    def _perpendicular_line_circle(self, line_index: int, circle_index: int) -> None:
        line = self.getGeometry(line_index)
        circle = self.getGeometry(circle_index)
        delta = self._delta(line)
        length_squared = delta[0] * delta[0] + delta[1] * delta[1]
        if circle_index >= 0:
            ratio = (
                (float(circle.Center.x) - float(line.StartPoint.x)) * delta[0]
                + (float(circle.Center.y) - float(line.StartPoint.y)) * delta[1]
            ) / length_squared
            circle.Center.x = float(line.StartPoint.x) + ratio * delta[0]
            circle.Center.y = float(line.StartPoint.y) + ratio * delta[1]
            return
        if line_index < 0:
            raise ValueError("Fake Perpendicular cannot move fixed circular geometry.")
        line.StartPoint.x = float(circle.Center.x)
        line.StartPoint.y = float(circle.Center.y)
        self._set_line_about_start(line, delta)

    def _solve_simple_perpendicular(self, constraint) -> None:
        first = self.getGeometry(constraint.First)
        second = self.getGeometry(constraint.Second)
        first_type = first.TypeId
        second_type = second.TypeId
        if first_type == second_type == "Part::GeomLineSegment":
            self._perpendicular_lines(constraint.First, constraint.Second)
            return
        if first_type == "Part::GeomLineSegment":
            self._perpendicular_line_circle(constraint.First, constraint.Second)
            return
        self._perpendicular_line_circle(constraint.Second, constraint.First)

    def _move_endpoint_perpendicular(
        self,
        reference_index: int,
        reference_position: int,
        movable_index: int,
        movable_position: int,
    ) -> None:
        reference = self.getGeometry(reference_index)
        movable = self.getGeometry(movable_index)
        reference_delta = self._delta(reference)
        target = self.getPoint(reference_index, reference_position)
        if movable_position == 1:
            length = math.hypot(*self._delta(movable))
            movable.StartPoint.x = float(target.x)
            movable.StartPoint.y = float(target.y)
            direction = (-reference_delta[1], reference_delta[0])
            direction_length = math.hypot(*direction)
            movable.EndPoint.x = float(target.x) + direction[0] * length / direction_length
            movable.EndPoint.y = float(target.y) + direction[1] * length / direction_length
            return
        length = math.hypot(*self._delta(movable))
        movable.EndPoint.x = float(target.x)
        movable.EndPoint.y = float(target.y)
        direction = (-reference_delta[1], reference_delta[0])
        direction_length = math.hypot(*direction)
        movable.StartPoint.x = float(target.x) - direction[0] * length / direction_length
        movable.StartPoint.y = float(target.y) - direction[1] * length / direction_length

    def _solve_endpoint_curve(self, constraint) -> None:
        curve = self.getGeometry(constraint.Second)
        if curve.TypeId != "Part::GeomLineSegment" or constraint.Second < 0:
            raise ValueError("Fake endpoint-to-curve supports one editable line.")
        self._move_endpoint_perpendicular(
            constraint.First,
            constraint.FirstPos,
            constraint.Second,
            1,
        )
        constraint.Value = math.pi / 2.0

    def _solve_endpoint_endpoint(self, constraint) -> None:
        if constraint.Second >= 0:
            self._move_endpoint_perpendicular(
                constraint.First,
                constraint.FirstPos,
                constraint.Second,
                constraint.SecondPos,
            )
        elif constraint.First >= 0:
            self._move_endpoint_perpendicular(
                constraint.Second,
                constraint.SecondPos,
                constraint.First,
                constraint.FirstPos,
            )
        else:
            raise ValueError("Fake endpoint Perpendicular cannot move fixed curves.")
        constraint.Value = math.pi / 2.0

    def _solve_point_pair_line(self, constraint) -> None:
        first = self.getPoint(constraint.First, constraint.FirstPos)
        second = self.getPoint(constraint.Second, constraint.SecondPos)
        line_delta = self._delta(self.getGeometry(constraint.Third))
        length = math.hypot(
            float(second.x) - float(first.x),
            float(second.y) - float(first.y),
        )
        direction = (-line_delta[1], line_delta[0])
        direction_length = math.hypot(*direction)
        self._move_exact_point(
            constraint.Second,
            constraint.SecondPos,
            float(first.x) + direction[0] * length / direction_length,
            float(first.y) + direction[1] * length / direction_length,
        )

    def _solve_via_point(self, constraint) -> None:
        point = self.getPoint(constraint.Third, constraint.ThirdPos)
        first = self.getGeometry(constraint.First)
        second = self.getGeometry(constraint.Second)
        if first.TypeId != "Part::GeomLineSegment" or second.TypeId != "Part::GeomLineSegment":
            raise ValueError("Fake via-point Perpendicular supports lines.")
        if constraint.Second >= 0:
            reference, movable = first, second
        elif constraint.First >= 0:
            reference, movable = second, first
        else:
            raise ValueError("Fake via-point Perpendicular cannot move fixed curves.")
        length = math.hypot(*self._delta(movable))
        reference_delta = self._delta(reference)
        direction = (-reference_delta[1], reference_delta[0])
        direction_length = math.hypot(*direction)
        movable.StartPoint.x = float(point.x)
        movable.StartPoint.y = float(point.y)
        movable.EndPoint.x = float(point.x) + direction[0] * length / direction_length
        movable.EndPoint.y = float(point.y) + direction[1] * length / direction_length
        constraint.Value = math.pi / 2.0

    def _solve_perpendicular(self, constraint) -> None:
        if constraint.Third > -2000:
            if constraint.FirstPos and constraint.SecondPos:
                self._solve_point_pair_line(constraint)
            else:
                self._solve_via_point(constraint)
            return
        if constraint.FirstPos and constraint.SecondPos:
            self._solve_endpoint_endpoint(constraint)
        elif constraint.FirstPos:
            self._solve_endpoint_curve(constraint)
        else:
            self._solve_simple_perpendicular(constraint)
