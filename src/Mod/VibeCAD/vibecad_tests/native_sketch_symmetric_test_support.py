# SPDX-License-Identifier: LGPL-2.1-or-later

"""Symmetric behavior for the shared fake Sketcher host."""

from __future__ import annotations

import math


class FakeSketchSymmetricMixin:
    def _symmetric_fixed(self, index: int) -> bool:
        return (
            index < 0
            or bool(self.GeometryFacadeList[index].Blocked)
            or any(
                constraint.Type == "Block" and constraint.First == index
                for constraint in self.Constraints
            )
        )

    def _symmetric_point(self, index: int, position: int) -> tuple[float, float]:
        point = self.getPoint(index, position)
        return float(point.x), float(point.y)

    @staticmethod
    def _reflection_about_point(
        point: tuple[float, float],
        reference: tuple[float, float],
    ) -> tuple[float, float]:
        return 2.0 * reference[0] - point[0], 2.0 * reference[1] - point[1]

    @staticmethod
    def _reflection_about_line(
        point: tuple[float, float],
        origin: tuple[float, float],
        delta: tuple[float, float],
    ) -> tuple[float, float]:
        length_squared = delta[0] * delta[0] + delta[1] * delta[1]
        if length_squared <= 1.0e-24:
            raise ValueError("Fake Symmetric cannot use a zero-length line.")
        ratio = (
            (point[0] - origin[0]) * delta[0] + (point[1] - origin[1]) * delta[1]
        ) / length_squared
        projection = (
            origin[0] + ratio * delta[0],
            origin[1] + ratio * delta[1],
        )
        return 2.0 * projection[0] - point[0], 2.0 * projection[1] - point[1]

    def _move_symmetric_subject(
        self,
        constraint,
        target: tuple[float, float],
        *,
        second: bool,
    ) -> None:
        index = constraint.Second if second else constraint.First
        position = constraint.SecondPos if second else constraint.FirstPos
        self._move_exact_point(index, position, target[0], target[1])

    def _solve_symmetric_about_point(self, constraint) -> None:
        first = self._symmetric_point(constraint.First, constraint.FirstPos)
        second = self._symmetric_point(constraint.Second, constraint.SecondPos)
        reference = self._symmetric_point(constraint.Third, constraint.ThirdPos)
        if not self._symmetric_fixed(constraint.Second):
            self._move_symmetric_subject(
                constraint,
                self._reflection_about_point(first, reference),
                second=True,
            )
            return
        if not self._symmetric_fixed(constraint.First):
            self._move_symmetric_subject(
                constraint,
                self._reflection_about_point(second, reference),
                second=False,
            )
            return
        if not self._symmetric_fixed(constraint.Third):
            self._move_exact_point(
                constraint.Third,
                constraint.ThirdPos,
                0.5 * (first[0] + second[0]),
                0.5 * (first[1] + second[1]),
            )
            return
        raise ValueError("Fake Symmetric cannot move three fixed points.")

    def _move_symmetry_line(
        self,
        index: int,
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> None:
        line = self.getGeometry(index)
        old_delta = (
            float(line.EndPoint.x) - float(line.StartPoint.x),
            float(line.EndPoint.y) - float(line.StartPoint.y),
        )
        old_length = math.hypot(*old_delta)
        subject_delta = second[0] - first[0], second[1] - first[1]
        subject_length = math.hypot(*subject_delta)
        if old_length <= 1.0e-12 or subject_length <= 1.0e-12:
            raise ValueError("Fake Symmetric cannot fit a degenerate symmetry line.")
        direction = (
            -subject_delta[1] / subject_length,
            subject_delta[0] / subject_length,
        )
        midpoint = (
            0.5 * (first[0] + second[0]),
            0.5 * (first[1] + second[1]),
        )
        line.StartPoint.x = midpoint[0] - 0.5 * old_length * direction[0]
        line.StartPoint.y = midpoint[1] - 0.5 * old_length * direction[1]
        line.EndPoint.x = midpoint[0] + 0.5 * old_length * direction[0]
        line.EndPoint.y = midpoint[1] + 0.5 * old_length * direction[1]

    def _solve_symmetric_about_line(self, constraint) -> None:
        first = self._symmetric_point(constraint.First, constraint.FirstPos)
        second = self._symmetric_point(constraint.Second, constraint.SecondPos)
        line = self.getGeometry(constraint.Third)
        origin = float(line.StartPoint.x), float(line.StartPoint.y)
        delta = (
            float(line.EndPoint.x) - origin[0],
            float(line.EndPoint.y) - origin[1],
        )
        if not self._symmetric_fixed(constraint.Second):
            self._move_symmetric_subject(
                constraint,
                self._reflection_about_line(first, origin, delta),
                second=True,
            )
            return
        if not self._symmetric_fixed(constraint.First):
            self._move_symmetric_subject(
                constraint,
                self._reflection_about_line(second, origin, delta),
                second=False,
            )
            return
        if not self._symmetric_fixed(constraint.Third):
            self._move_symmetry_line(constraint.Third, first, second)
            return
        raise ValueError("Fake Symmetric cannot move fixed points or symmetry line.")

    def _solve_symmetric(self, constraint) -> None:
        if constraint.ThirdPos:
            self._solve_symmetric_about_point(constraint)
        else:
            self._solve_symmetric_about_line(constraint)
