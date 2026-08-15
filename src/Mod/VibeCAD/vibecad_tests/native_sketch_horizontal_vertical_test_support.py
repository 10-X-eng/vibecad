# SPDX-License-Identifier: LGPL-2.1-or-later

"""Horizontal/Vertical behavior for the shared fake Sketcher host."""

from __future__ import annotations


class FakeSketchHorizontalVerticalMixin:
    """Minimal solver behavior exercised by automatic axis-alignment tests."""

    def _refresh_horizontal_vertical_geometry(self, index: int) -> None:
        if index < 0:
            return
        geometry = self.getGeometry(index)
        if geometry.TypeId in {"Part::GeomArcOfCircle", "Part::GeomArcOfEllipse"}:
            geometry.StartPoint = geometry._point(geometry.FirstParameter)
            geometry.EndPoint = geometry._point(geometry.LastParameter)

    def _set_alignment_coordinate(
        self,
        index: int,
        position: int,
        coordinate: str,
        value: float,
    ) -> None:
        self._set_point_coordinate(index, position, coordinate, value)
        if position == 3:
            self._refresh_horizontal_vertical_geometry(index)

    def _solve_horizontal_vertical(self, constraint) -> None:
        coordinate = "y" if constraint.Type == "Horizontal" else "x"
        if constraint.Second <= -2000:
            geometry = self.getGeometry(constraint.First)
            if geometry.TypeId != "Part::GeomLineSegment":
                raise ValueError("Fake axis alignment requires a straight line.")
            value = float(getattr(geometry.StartPoint, coordinate))
            self._set_alignment_coordinate(
                constraint.First,
                2,
                coordinate,
                value,
            )
            return
        first = self.getPoint(constraint.First, constraint.FirstPos)
        second = self.getPoint(constraint.Second, constraint.SecondPos)
        if constraint.Second >= 0:
            self._set_alignment_coordinate(
                constraint.Second,
                constraint.SecondPos,
                coordinate,
                float(getattr(first, coordinate)),
            )
            return
        if constraint.First >= 0:
            self._set_alignment_coordinate(
                constraint.First,
                constraint.FirstPos,
                coordinate,
                float(getattr(second, coordinate)),
            )
            return
        raise ValueError("Fake axis alignment cannot move two fixed points.")
