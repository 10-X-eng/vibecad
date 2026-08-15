# SPDX-License-Identifier: LGPL-2.1-or-later

"""Equal behavior for the shared fake Sketcher host."""

from __future__ import annotations

import math


_CIRCULAR = {"Part::GeomCircle", "Part::GeomArcOfCircle"}
_ELLIPTIC = {"Part::GeomEllipse", "Part::GeomArcOfEllipse"}


class FakeSketchEqualMixin:
    def _equal_values(self, geometry):
        if geometry.TypeId == "Part::GeomLineSegment":
            return (
                math.hypot(
                    float(geometry.EndPoint.x) - float(geometry.StartPoint.x),
                    float(geometry.EndPoint.y) - float(geometry.StartPoint.y),
                ),
            )
        if geometry.TypeId in _CIRCULAR:
            return (float(geometry.Radius),)
        if (
            geometry.TypeId in _ELLIPTIC
            or geometry.TypeId == "Part::GeomArcOfHyperbola"
        ):
            return float(geometry.MajorRadius), float(geometry.MinorRadius)
        if geometry.TypeId == "Part::GeomArcOfParabola":
            return (float(geometry.Focal),)
        raise ValueError("Fake Equal received unsupported geometry.")

    def _equal_fixed(self, index: int) -> bool:
        return index < 0 or bool(self.GeometryFacadeList[index].Blocked)

    @staticmethod
    def _refresh_equal_curve(geometry) -> None:
        if hasattr(geometry, "_point") and hasattr(geometry, "FirstParameter"):
            geometry.StartPoint = geometry._point(geometry.FirstParameter)
            geometry.EndPoint = geometry._point(geometry.LastParameter)

    def _sync_bspline_weight(self, geometry_index: int, value: float) -> None:
        for constraint in self.Constraints:
            if (
                constraint.Type != "InternalAlignment"
                or constraint.First != geometry_index
                or constraint.FirstPos != 3
                or constraint.Second < 0
            ):
                continue
            spline = self.getGeometry(constraint.Second)
            if spline.TypeId != "Part::GeomBSplineCurve":
                continue
            pole_index = int(
                getattr(
                    constraint,
                    "InternalAlignmentIndex",
                    constraint.SecondPos,
                )
            )
            spline._weights[pole_index] = float(value)

    def _set_equal_values(self, index: int, values: tuple[float, ...]) -> None:
        geometry = self.getGeometry(index)
        if geometry.TypeId == "Part::GeomLineSegment":
            old_x = float(geometry.EndPoint.x) - float(geometry.StartPoint.x)
            old_y = float(geometry.EndPoint.y) - float(geometry.StartPoint.y)
            old_length = math.hypot(old_x, old_y)
            geometry.EndPoint.x = (
                float(geometry.StartPoint.x) + old_x * values[0] / old_length
            )
            geometry.EndPoint.y = (
                float(geometry.StartPoint.y) + old_y * values[0] / old_length
            )
            return
        if geometry.TypeId in _CIRCULAR:
            geometry.Radius = values[0]
            self._refresh_equal_curve(geometry)
            self._sync_bspline_weight(index, values[0])
            return
        if (
            geometry.TypeId in _ELLIPTIC
            or geometry.TypeId == "Part::GeomArcOfHyperbola"
        ):
            geometry.MajorRadius, geometry.MinorRadius = values
            self._refresh_equal_curve(geometry)
            return
        if geometry.TypeId == "Part::GeomArcOfParabola":
            geometry.Focal = values[0]
            geometry.Focus.x = float(geometry.Center.x) + values[0] * float(
                geometry.XAxis.x
            )
            geometry.Focus.y = float(geometry.Center.y) + values[0] * float(
                geometry.XAxis.y
            )
            self._refresh_equal_curve(geometry)
            return
        raise ValueError("Fake Equal received unsupported geometry.")

    def _solve_equal(self, constraint) -> None:
        first = self.getGeometry(constraint.First)
        second = self.getGeometry(constraint.Second)
        first_values = self._equal_values(first)
        second_values = self._equal_values(second)
        if len(first_values) != len(second_values):
            raise ValueError("Fake Equal received incompatible geometry.")
        if all(
            math.isclose(first_value, second_value, abs_tol=1.0e-12)
            for first_value, second_value in zip(
                first_values,
                second_values,
                strict=True,
            )
        ):
            return
        if not self._equal_fixed(constraint.Second):
            self._set_equal_values(constraint.Second, first_values)
        elif not self._equal_fixed(constraint.First):
            self._set_equal_values(constraint.First, second_values)
        else:
            raise ValueError("Fake Equal cannot move two fixed edges.")
