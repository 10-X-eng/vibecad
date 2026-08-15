# SPDX-License-Identifier: LGPL-2.1-or-later

"""Small fake Sketcher host shared by Native Sketch domain unit tests."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
import sys

import VibeCADNativeSketchTargets as target_module
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger
from vibecad_tests.native_sketch_angle_test_support import FakeSketchAngleMixin
from vibecad_tests.native_sketch_coincident_test_support import (
    FakeSketchCoincidentMixin,
)
from vibecad_tests.native_sketch_horizontal_vertical_test_support import (
    FakeSketchHorizontalVerticalMixin,
)
from vibecad_tests.native_sketch_parallel_test_support import (
    FakeSketchParallelMixin,
)
from vibecad_tests.native_sketch_perpendicular_test_support import (
    FakeSketchPerpendicularMixin,
)
from vibecad_tests.native_sketch_tangent_test_support import FakeSketchTangentMixin
from vibecad_tests.native_sketch_equal_test_support import FakeSketchEqualMixin
from vibecad_tests.native_sketch_symmetric_test_support import (
    FakeSketchSymmetricMixin,
)
from vibecad_tests.native_sketch_block_test_support import FakeSketchBlockMixin
from vibecad_tests.native_sketch_group_test_support import FakeSketchGroupMixin
from vibecad_tests.native_sketch_driving_test_support import (
    FakeSketchDrivingMixin,
)
from vibecad_tests.native_sketch_active_test_support import FakeSketchActiveMixin


class FakeDocument:
    Uid = "native-sketch-geometry-document"
    Name = "NativeSketchGeometryDocument"

    def __init__(self) -> None:
        self.Objects = []

    def getObject(self, name: str):
        return next((obj for obj in self.Objects if obj.Name == name), None)


class FakePoint:
    TypeId = "Part::GeomPoint"

    def __init__(self, vector) -> None:
        self.X = float(vector.x)
        self.Y = float(vector.y)
        self.Z = float(vector.z)


class FakeLine:
    TypeId = "Part::GeomLineSegment"
    FirstParameter = 0.0
    LastParameter = 1.0

    def __init__(self, start=None, end=None) -> None:
        self.StartPoint = start or SimpleNamespace(x=0.0, y=0.0, z=0.0)
        self.EndPoint = end or SimpleNamespace(x=5.0, y=0.0, z=0.0)


class FakeExternalExtension:
    def __init__(self, reference: str, *, defining: bool = False) -> None:
        self.Ref = reference
        self._flags = {"Defining": bool(defining)}

    def testFlag(self, flag: str) -> bool:
        return bool(self._flags.get(str(flag), False))

    def setFlag(self, flag: str, state: bool) -> None:
        self._flags[str(flag)] = bool(state)


class FakeExternalLine(FakeLine):
    def __init__(self, reference: str, *, defining: bool = False) -> None:
        super().__init__()
        self.extension = FakeExternalExtension(reference, defining=defining)

    def getExtensionOfType(self, type_id: str):
        if type_id != "Sketcher::ExternalGeometryExtension":
            raise ValueError("Unsupported fake geometry extension.")
        return self.extension


class FakeCircle:
    TypeId = "Part::GeomCircle"

    def __init__(self, center, axis, radius: float) -> None:
        self.Center = center
        self.Location = center
        self.Axis = axis
        self.Radius = float(radius)

    def isClosed(self) -> bool:
        return True


class FakeArc:
    TypeId = "Part::GeomArcOfCircle"

    def __init__(self, circle: FakeCircle, first: float, last: float) -> None:
        self.Center = circle.Center
        self.Location = circle.Center
        self.Axis = circle.Axis
        self.Radius = circle.Radius
        self.FirstParameter = float(first)
        self.LastParameter = float(last)
        self.StartPoint = self._point(first)
        self.EndPoint = self._point(last)

    def _point(self, parameter: float):
        return SimpleNamespace(
            x=self.Center.x + self.Radius * math.cos(parameter),
            y=self.Center.y + self.Radius * math.sin(parameter),
            z=0.0,
        )

    def isClosed(self) -> bool:
        return False


class FakeEllipse:
    TypeId = "Part::GeomEllipse"

    def __init__(self, center, major_radius: float, minor_radius: float) -> None:
        self.Center = center
        self.Location = center
        self.Axis = SimpleNamespace(x=0.0, y=0.0, z=1.0)
        self.MajorRadius = float(major_radius)
        self.MinorRadius = float(minor_radius)
        self.XAxis = SimpleNamespace(x=1.0, y=0.0, z=0.0)

    def isClosed(self) -> bool:
        return True


class FakeEllipticalArc:
    TypeId = "Part::GeomArcOfEllipse"

    def __init__(self, ellipse: FakeEllipse, first: float, last: float) -> None:
        self.Center = ellipse.Center
        self.Location = ellipse.Center
        self.Axis = SimpleNamespace(x=0.0, y=0.0, z=1.0)
        self.XAxis = ellipse.XAxis
        self.MajorRadius = ellipse.MajorRadius
        self.MinorRadius = ellipse.MinorRadius
        self.FirstParameter = float(first)
        self.LastParameter = float(last)
        self.StartPoint = self._point(first)
        self.EndPoint = self._point(last)

    def _point(self, parameter: float):
        minor_axis = (-self.XAxis.y, self.XAxis.x)
        return SimpleNamespace(
            x=(
                self.Center.x
                + self.MajorRadius * math.cos(parameter) * self.XAxis.x
                + self.MinorRadius * math.sin(parameter) * minor_axis[0]
            ),
            y=(
                self.Center.y
                + self.MajorRadius * math.cos(parameter) * self.XAxis.y
                + self.MinorRadius * math.sin(parameter) * minor_axis[1]
            ),
            z=0.0,
        )

    def isClosed(self) -> bool:
        return False


class FakeHyperbola:
    def __init__(self, center, major_radius: float, minor_radius: float) -> None:
        self.Center = center
        self.MajorRadius = float(major_radius)
        self.MinorRadius = float(minor_radius)
        self.XAxis = SimpleNamespace(x=1.0, y=0.0, z=0.0)


class FakeHyperbolicArc:
    TypeId = "Part::GeomArcOfHyperbola"

    def __init__(self, hyperbola: FakeHyperbola, first: float, last: float) -> None:
        self.Center = hyperbola.Center
        self.Location = hyperbola.Center
        self.Axis = SimpleNamespace(x=0.0, y=0.0, z=1.0)
        self.XAxis = hyperbola.XAxis
        self.MajorRadius = hyperbola.MajorRadius
        self.MinorRadius = hyperbola.MinorRadius
        self.FirstParameter = float(first)
        self.LastParameter = float(last)
        self.StartPoint = self._point(first)
        self.EndPoint = self._point(last)

    def _point(self, parameter: float):
        minor_axis = (-self.XAxis.y, self.XAxis.x)
        return SimpleNamespace(
            x=(
                self.Center.x
                + self.MajorRadius * math.cosh(parameter) * self.XAxis.x
                + self.MinorRadius * math.sinh(parameter) * minor_axis[0]
            ),
            y=(
                self.Center.y
                + self.MajorRadius * math.cosh(parameter) * self.XAxis.y
                + self.MinorRadius * math.sinh(parameter) * minor_axis[1]
            ),
            z=0.0,
        )

    def isClosed(self) -> bool:
        return False


class FakeParabola:
    def __init__(self, focus, vertex, _axis) -> None:
        self.Center = vertex
        self.Focus = focus
        delta_x = float(focus.x) - float(vertex.x)
        delta_y = float(focus.y) - float(vertex.y)
        self.Focal = math.hypot(delta_x, delta_y)
        self.XAxis = SimpleNamespace(
            x=delta_x / self.Focal,
            y=delta_y / self.Focal,
            z=0.0,
        )


class FakeParabolicArc:
    TypeId = "Part::GeomArcOfParabola"

    def __init__(self, parabola: FakeParabola, first: float, last: float) -> None:
        self.Center = parabola.Center
        self.Location = parabola.Center
        self.Focus = parabola.Focus
        self.Axis = SimpleNamespace(x=0.0, y=0.0, z=1.0)
        self.XAxis = parabola.XAxis
        self.Focal = parabola.Focal
        self.FirstParameter = float(first)
        self.LastParameter = float(last)
        self.StartPoint = self._point(first)
        self.EndPoint = self._point(last)

    def _point(self, parameter: float):
        transverse_axis = (-self.XAxis.y, self.XAxis.x)
        axial_distance = parameter * parameter / (4.0 * self.Focal)
        return SimpleNamespace(
            x=(
                self.Center.x
                + axial_distance * self.XAxis.x
                + parameter * transverse_axis[0]
            ),
            y=(
                self.Center.y
                + axial_distance * self.XAxis.y
                + parameter * transverse_axis[1]
            ),
            z=0.0,
        )

    def isClosed(self) -> bool:
        return False


class FakeBSpline:
    TypeId = "Part::GeomBSplineCurve"

    def __init__(
        self,
        poles=None,
        multiplicities=None,
        knots=None,
        periodic=False,
        degree=1,
        weights=None,
        _check_rational=False,
    ) -> None:
        self._poles = list(poles or ())
        self._multiplicities = [int(value) for value in (multiplicities or ())]
        self._knots = [float(value) for value in (knots or ())]
        self._periodic = bool(periodic)
        self._degree = int(degree)
        self._weights = [float(value) for value in (weights or ())]
        self._interpolation_points = []
        if not self._poles:
            self._poles = [
                SimpleNamespace(x=0.0, y=0.0, z=0.0),
                SimpleNamespace(x=1.0, y=0.0, z=0.0),
            ]
            self._multiplicities = [2, 2]
            self._knots = [0.0, 1.0]
            self._weights = [1.0, 1.0]
            self._degree = 1
        self._refresh()

    def _refresh(self) -> None:
        self.Degree = self._degree
        self.NbPoles = len(self._poles)
        self.NbKnots = len(self._knots)
        self.FirstParameter = self._knots[0]
        self.LastParameter = self._knots[-1]
        self.StartPoint = self.value(self.FirstParameter)
        self.EndPoint = self.value(self.LastParameter)

    @staticmethod
    def _point_between(first, second, fraction: float):
        return SimpleNamespace(
            x=(1.0 - fraction) * first.x + fraction * second.x,
            y=(1.0 - fraction) * first.y + fraction * second.y,
            z=(1.0 - fraction) * first.z + fraction * second.z,
        )

    def _interpolated_poles(self, count: int):
        points = self._interpolation_points
        if self._periodic:
            if len(points) == 2:
                return [
                    points[0],
                    self._point_between(points[0], points[1], 1.0 / 3.0),
                    self._point_between(points[0], points[1], 2.0 / 3.0),
                    points[1],
                    self._point_between(points[1], points[0], 1.0 / 3.0),
                    self._point_between(points[1], points[0], 2.0 / 3.0),
                ]
            result = [*points, self._point_between(points[-1], points[0], 0.5)]
            assert len(result) == count
            return result
        if len(points) == 2:
            return [
                points[0],
                self._point_between(points[0], points[1], 1.0 / 3.0),
                self._point_between(points[0], points[1], 2.0 / 3.0),
                points[1],
            ]
        if len(points) == 3:
            return [
                points[0],
                points[1],
                self._point_between(points[1], points[2], 0.5),
                points[2],
            ]
        result = [points[0], self._point_between(points[0], points[1], 0.5)]
        result.extend(points[1:-1])
        result.extend([self._point_between(points[-2], points[-1], 0.5), points[-1]])
        assert len(result) == count
        return result

    def interpolate(self, points, periodic=False) -> None:
        self._interpolation_points = list(points)
        self._periodic = bool(periodic)
        cumulative = [0.0]
        for first, second in zip(
            self._interpolation_points,
            self._interpolation_points[1:],
        ):
            cumulative.append(
                cumulative[-1]
                + math.dist(
                    (first.x, first.y, first.z),
                    (second.x, second.y, second.z),
                )
            )
        if self._periodic:
            first = self._interpolation_points[-1]
            second = self._interpolation_points[0]
            cumulative.append(
                cumulative[-1]
                + math.dist(
                    (first.x, first.y, first.z),
                    (second.x, second.y, second.z),
                )
            )
        self._degree = min(len(self._interpolation_points) - 1, 3)
        if self._periodic:
            pole_count = (
                6
                if len(self._interpolation_points) == 2
                else len(self._interpolation_points) + 1
            )
        else:
            pole_count = (
                len(self._interpolation_points) + 2
                if len(self._interpolation_points) >= 4
                else len(self._interpolation_points)
            )
        self._poles = list(self._interpolation_points)
        if pole_count != len(self._poles):
            self._poles = self._interpolated_poles(pole_count)
        self._knots = (
            cumulative
            if self._periodic or len(cumulative) != 3
            else [cumulative[0], cumulative[-1]]
        )
        self._multiplicities = [1] * len(self._knots)
        if self._periodic:
            if len(self._interpolation_points) == 2:
                self._multiplicities = [3] * len(self._knots)
            else:
                self._multiplicities[0] = 2
                self._multiplicities[-1] = 2
        else:
            self._multiplicities[0] = self._degree + 1
            self._multiplicities[-1] = self._degree + 1
        self._weights = [1.0] * len(self._poles)
        self._refresh()

    def increaseDegree(self, degree: int) -> None:
        target = max(self._degree, int(degree))
        self._degree = target
        if self._interpolation_points:
            if self._periodic:
                pole_count = (
                    6
                    if len(self._interpolation_points) == 2
                    else len(self._interpolation_points) + 1
                )
            else:
                pole_count = (
                    4
                    if len(self._interpolation_points) <= 3
                    else len(self._interpolation_points) + 2
                )
            self._poles = self._interpolated_poles(pole_count)
            if not self._periodic:
                self._multiplicities[0] = target + 1
                self._multiplicities[-1] = target + 1
            self._weights = [1.0] * pole_count
        self._refresh()

    def getPoles(self):
        return list(self._poles)

    def getWeights(self):
        return list(self._weights)

    def getKnots(self):
        return list(self._knots)

    def getMultiplicities(self):
        return list(self._multiplicities)

    def isRational(self) -> bool:
        return len(set(self._weights)) > 1

    def isPeriodic(self) -> bool:
        return self._periodic

    def isClosed(self) -> bool:
        return self._periodic or (
            self.StartPoint.x == self.EndPoint.x
            and self.StartPoint.y == self.EndPoint.y
            and self.StartPoint.z == self.EndPoint.z
        )

    def value(self, parameter: float):
        if self._periodic:
            period = self.LastParameter - self.FirstParameter
            if period <= 0.0:
                return self._poles[0]
            normalized = (float(parameter) - self.FirstParameter) % period
            scaled = normalized * len(self._poles) / period
            first = int(math.floor(scaled)) % len(self._poles)
            second = (first + 1) % len(self._poles)
            fraction = scaled - math.floor(scaled)
            return SimpleNamespace(
                x=(1.0 - fraction) * self._poles[first].x
                + fraction * self._poles[second].x,
                y=(1.0 - fraction) * self._poles[first].y
                + fraction * self._poles[second].y,
                z=(1.0 - fraction) * self._poles[first].z
                + fraction * self._poles[second].z,
            )
        if parameter <= self.FirstParameter:
            return self._poles[0]
        if parameter >= self.LastParameter:
            return self._poles[-1]
        expanded = []
        for knot, multiplicity in zip(
            self._knots,
            self._multiplicities,
            strict=True,
        ):
            expanded.extend([knot] * multiplicity)
        pole_last = len(self._poles) - 1
        span = next(
            index
            for index in range(self._degree, pole_last + 1)
            if expanded[index] <= parameter < expanded[index + 1]
        )
        points = [
            [
                float(self._poles[span - self._degree + offset].x),
                float(self._poles[span - self._degree + offset].y),
                float(self._poles[span - self._degree + offset].z),
            ]
            for offset in range(self._degree + 1)
        ]
        for level in range(1, self._degree + 1):
            for offset in range(self._degree, level - 1, -1):
                knot_index = span - self._degree + offset
                denominator = (
                    expanded[knot_index + self._degree - level + 1]
                    - expanded[knot_index]
                )
                alpha = (
                    0.0
                    if denominator == 0.0
                    else (parameter - expanded[knot_index]) / denominator
                )
                points[offset] = [
                    (1.0 - alpha) * points[offset - 1][axis]
                    + alpha * points[offset][axis]
                    for axis in range(3)
                ]
        return SimpleNamespace(
            x=points[self._degree][0],
            y=points[self._degree][1],
            z=points[self._degree][2],
        )


class FakeConstraint:
    def __init__(self, constraint_type: str, *references: int) -> None:
        self.AlignmentType = ""
        self.InternalAlignmentIndex = -1
        if constraint_type == "Group" and len(references) == 1:
            raw_elements = references[0]
            if not isinstance(raw_elements, list) or len(raw_elements) % 2:
                raise TypeError("Fake Group constraint elements are invalid.")
            elements = tuple(
                (int(raw_elements[index]), int(raw_elements[index + 1]))
                for index in range(0, len(raw_elements), 2)
            )
            if len(elements) < 3:
                raise TypeError("Fake Group constraint needs a handle and two members.")
            padded = (*elements, (-2000, 0), (-2000, 0), (-2000, 0))
            self.Type = "Group"
            self.First, self.FirstPos = padded[0]
            self.Second, self.SecondPos = padded[1]
            self.Third, self.ThirdPos = padded[2]
            self.Elements = elements
            self.Text = ""
            self.Font = ""
            self.IsTextHeight = True
            self.Value = 0.0
            self.Name = ""
            self.Driving = True
            self.IsActive = True
            self.InVirtualSpace = False
            self.LabelDistance = 10.0
            self.LabelPosition = 0.0
            return
        if constraint_type == "Text" and len(references) == 4:
            raw_elements, text, font, is_height = references
            if not isinstance(raw_elements, list) or len(raw_elements) % 2:
                raise TypeError("Fake Text constraint elements are invalid.")
            elements = tuple(
                (int(raw_elements[index]), int(raw_elements[index + 1]))
                for index in range(0, len(raw_elements), 2)
            )
            padded = (*elements, (-2000, 0), (-2000, 0), (-2000, 0))
            self.Type = "Text"
            self.First, self.FirstPos = padded[0]
            self.Second, self.SecondPos = padded[1]
            self.Third, self.ThirdPos = padded[2]
            self.Elements = elements
            self.Text = str(text)
            self.Font = Path(str(font)).stem
            self.IsTextHeight = bool(is_height)
            self.Value = 0.0
            self.Name = ""
            self.Driving = True
            self.IsActive = True
            self.InVirtualSpace = False
            self.LabelDistance = 10.0
            self.LabelPosition = 0.0
            return
        active = True
        driving = True
        if (
            len(references) >= 2
            and type(references[-2]) is bool
            and type(references[-1]) is bool
        ):
            active = references[-2]
            driving = references[-1]
            references = references[:-2]
        value = 0.0
        if (
            constraint_type in {"PerpendicularViaPoint", "TangentViaPoint"}
            and len(references) == 4
        ):
            first, second, third, third_pos = references
            first_pos = second_pos = 0
            constraint_type = (
                "Perpendicular"
                if constraint_type == "PerpendicularViaPoint"
                else "Tangent"
            )
        elif constraint_type in {"Perpendicular", "Symmetric"} and len(references) == 5:
            first, first_pos, second, second_pos, third = references
            third_pos = 0
        elif (
            constraint_type in {"Distance", "DistanceX", "DistanceY", "Angle"}
            and len(references) == 5
        ):
            first, first_pos, second, second_pos, value = references
            third, third_pos = -2000, 0
            value = float(value)
        elif constraint_type == "AngleViaPoint" and len(references) == 5:
            first, second, third, third_pos, value = references
            first_pos = second_pos = 0
            constraint_type = "Angle"
            value = float(value)
        elif constraint_type == "Distance" and len(references) == 4:
            first, first_pos, second, value = references
            second_pos, third, third_pos = 0, -2000, 0
            value = float(value)
        elif constraint_type == "Distance" and len(references) == 3:
            first, second, value = references
            first_pos, second_pos, third, third_pos = 0, 0, -2000, 0
            value = float(value)
        elif constraint_type == "Distance" and len(references) == 2:
            first, value = references
            first_pos, second, second_pos, third, third_pos = (
                0,
                -2000,
                0,
                -2000,
                0,
            )
            value = float(value)
        elif (
            constraint_type in {"DistanceX", "DistanceY", "Angle"}
            and len(references) == 3
        ):
            first, first_pos, value = references
            second, second_pos, third, third_pos = -2000, 0, -2000, 0
            value = float(value)
        elif constraint_type in {"Radius", "Diameter"} and len(references) == 2:
            first, value = references
            first_pos, second, second_pos, third, third_pos = (
                0,
                -2000,
                0,
                -2000,
                0,
            )
            value = float(value)
        elif constraint_type == "Angle" and len(references) == 2:
            first, value = references
            first_pos, second, second_pos, third, third_pos = (
                0,
                -2000,
                0,
                -2000,
                0,
            )
            value = float(value)
        elif constraint_type == "Weight" and len(references) == 2:
            first = references[0]
            first_pos, second, second_pos, third, third_pos = 0, -2000, 0, -2000, 0
            value = float(references[1])
        elif constraint_type.startswith("InternalAlignment:") and len(references) == 4:
            first, first_pos, second, internal_index = references
            second_pos, third, third_pos = 0, -2000, 0
            self.AlignmentType = constraint_type.rsplit("::", 1)[-1]
            self.InternalAlignmentIndex = int(internal_index)
            constraint_type = "InternalAlignment"
        elif len(references) == 1:
            first = references[0]
            first_pos, second, second_pos, third, third_pos = 0, -2000, 0, -2000, 0
        elif len(references) == 2:
            first, second = references
            first_pos, second_pos, third, third_pos = 0, 0, -2000, 0
        elif len(references) == 3:
            first, first_pos, second = references
            second_pos, third, third_pos = 0, -2000, 0
        elif len(references) == 4:
            first, first_pos, second, second_pos = references
            third, third_pos = -2000, 0
        elif len(references) == 6:
            first, first_pos, second, second_pos, third, third_pos = references
        else:
            raise TypeError("Unsupported fake Sketcher constraint constructor.")
        self.Type = constraint_type
        self.First = first
        self.FirstPos = first_pos
        self.Second = second
        self.SecondPos = second_pos
        self.Third = third
        self.ThirdPos = third_pos
        self.Value = value
        self.Name = ""
        self.Driving = driving
        self.IsActive = active
        self.InVirtualSpace = False
        self.LabelDistance = 10.0
        self.LabelPosition = 0.0
        self.Elements = tuple(
            (geometry, position)
            for geometry, position in (
                (self.First, self.FirstPos),
                (self.Second, self.SecondPos),
                (self.Third, self.ThirdPos),
            )
            if geometry > -2000
        )
        self.Text = ""
        self.Font = ""
        self.IsTextHeight = True


def fake_facade(
    geometry,
    index: int,
    *,
    construction: bool = False,
    internal_type: str = "",
):
    return SimpleNamespace(
        Geometry=geometry,
        Id=100 + index,
        Construction=construction,
        Blocked=False,
        InternalType=internal_type,
        GeometryLayerId=0,
        Tag="",
    )


class FakeSketch(
    FakeSketchActiveMixin,
    FakeSketchDrivingMixin,
    FakeSketchAngleMixin,
    FakeSketchCoincidentMixin,
    FakeSketchHorizontalVerticalMixin,
    FakeSketchParallelMixin,
    FakeSketchPerpendicularMixin,
    FakeSketchTangentMixin,
    FakeSketchEqualMixin,
    FakeSketchSymmetricMixin,
    FakeSketchBlockMixin,
    FakeSketchGroupMixin,
):
    Name = "Sketch"
    Label = "Sketch"
    TypeId = "Sketcher::SketchObject"

    def __init__(self, document: FakeDocument) -> None:
        self.Document = document
        self.Geometry = [FakeLine()]
        self.GeometryFacadeList = [fake_facade(self.Geometry[0], 0)]
        self.Constraints = []
        self.ExpressionEngine = []
        self.GeometryCount = 1
        self.ConstraintCount = 0
        self.ExternalGeo = [FakeLine(), FakeLine()]
        self.ExternalGeometry = []
        self.ExternalTypes = []
        self.MalformedConstraints = []
        self.DoF = 4
        self.FullyConstrained = False
        self.ConflictingConstraints = []
        self.RedundantConstraints = []
        self.PartiallyRedundantConstraints = []
        self.FeasibilityOverride = None
        self.OpenVertices = []
        self.Shape = SimpleNamespace(Wires=[], Faces=[])
        self._persistent_geometry_tags = False
        self._next_geometry_tag = 0
        document.Objects.append(self)

    def isDerivedFrom(self, type_id: str) -> bool:
        return type_id == self.TypeId

    def isValid(self) -> bool:
        return True

    def getGeometryId(self, index: int) -> int:
        return int(self.GeometryFacadeList[index].Id)

    def getConstruction(self, index: int) -> bool:
        return bool(self.GeometryFacadeList[index].Construction)

    def getGeometry(self, index: int):
        if index == -1:
            return FakeLine(
                SimpleNamespace(x=0.0, y=0.0, z=0.0),
                SimpleNamespace(x=1.0, y=0.0, z=0.0),
            )
        if index == -2:
            return FakeLine(
                SimpleNamespace(x=0.0, y=0.0, z=0.0),
                SimpleNamespace(x=0.0, y=1.0, z=0.0),
            )
        if index <= -3:
            return self.ExternalGeo[-index - 1]
        return self.Geometry[index]

    def getPoint(self, index: int, position: int):
        if index == -1 and position == 1:
            return SimpleNamespace(x=0.0, y=0.0, z=0.0)
        geometry = self.getGeometry(index)
        if geometry.TypeId == "Part::GeomPoint" and position == 1:
            return SimpleNamespace(x=geometry.X, y=geometry.Y, z=geometry.Z)
        if position == 1 and hasattr(geometry, "StartPoint"):
            return geometry.StartPoint
        if position == 2 and hasattr(geometry, "EndPoint"):
            return geometry.EndPoint
        if position == 3 and hasattr(geometry, "Center"):
            return geometry.Center
        raise ValueError("Unsupported fake Sketch point reference.")

    def isPointOnCurve(self, index: int, x: float, y: float) -> bool:
        geometry = self.getGeometry(index)
        point_x = float(x)
        point_y = float(y)
        if geometry.TypeId == "Part::GeomLineSegment":
            start_x = float(geometry.StartPoint.x)
            start_y = float(geometry.StartPoint.y)
            delta_x = float(geometry.EndPoint.x) - start_x
            delta_y = float(geometry.EndPoint.y) - start_y
            length_squared = delta_x * delta_x + delta_y * delta_y
            if length_squared <= 1.0e-24:
                return False
            parameter = (
                (point_x - start_x) * delta_x + (point_y - start_y) * delta_y
            ) / length_squared
            cross = delta_x * (point_y - start_y) - delta_y * (point_x - start_x)
            return (index in {-1, -2} or -1.0e-9 <= parameter <= 1.0 + 1.0e-9) and abs(
                cross
            ) <= 1.0e-7 * math.sqrt(length_squared)
        if geometry.TypeId in {"Part::GeomCircle", "Part::GeomArcOfCircle"}:
            radius = math.hypot(
                point_x - float(geometry.Center.x),
                point_y - float(geometry.Center.y),
            )
            return math.isclose(
                radius,
                float(geometry.Radius),
                rel_tol=1.0e-9,
                abs_tol=1.0e-7,
            )
        return False

    def getStatusString(self) -> str:
        return "Under-constrained"

    def addGeometry(self, geometry, construction: bool) -> int:
        if isinstance(geometry, list):
            return [self.addGeometry(item, construction) for item in geometry]
        index = len(self.Geometry)
        self.Geometry.append(geometry)
        facade = fake_facade(geometry, index, construction=construction)
        if self._persistent_geometry_tags:
            facade.Tag = f"fake-geometry-{self._next_geometry_tag}"
            self._next_geometry_tag += 1
        self.GeometryFacadeList.append(facade)
        self.GeometryCount = len(self.Geometry)
        return index

    def addConstraint(self, constraint):
        if isinstance(constraint, list):
            return [self.addConstraint(item) for item in constraint]
        index = len(self.Constraints)
        self.Constraints.append(constraint)
        if (
            constraint.Type == "InternalAlignment"
            and constraint.AlignmentType
            and 0 <= constraint.First < len(self.GeometryFacadeList)
        ):
            self.GeometryFacadeList[
                constraint.First
            ].InternalType = constraint.AlignmentType
        if constraint.Type in {"DistanceX", "DistanceY"} and constraint.Driving:
            self._solve_axis_distance(
                constraint,
                "x" if constraint.Type == "DistanceX" else "y",
            )
        elif constraint.Type == "Distance" and constraint.Driving:
            self._solve_general_distance(constraint)
        elif constraint.Type in {"Radius", "Diameter"} and constraint.Driving:
            self._solve_radiam(constraint)
        elif constraint.Type == "Angle" and constraint.Driving:
            self._solve_angle(constraint)
        elif constraint.Type == "Coincident":
            self._solve_coincident(constraint)
        elif constraint.Type == "PointOnObject":
            self._solve_point_on_object(constraint)
        elif constraint.Type in {"Horizontal", "Vertical"}:
            self._solve_horizontal_vertical(constraint)
        elif constraint.Type == "Parallel":
            self._solve_parallel(constraint)
        elif constraint.Type == "Perpendicular":
            self._solve_perpendicular(constraint)
        elif constraint.Type == "Tangent":
            self._solve_tangent(constraint)
        elif constraint.Type == "Equal":
            self._solve_equal(constraint)
        elif constraint.Type == "Symmetric":
            self._solve_symmetric(constraint)
        elif constraint.Type == "Block":
            self._solve_block(constraint)
        self.ConstraintCount = len(self.Constraints)
        return index

    def setVirtualSpace(self, indices, virtual: bool) -> None:
        selected = indices if isinstance(indices, list) else [indices]
        for index in selected:
            self.Constraints[int(index)].InVirtualSpace = bool(virtual)

    def getVirtualSpace(self, index: int) -> bool:
        return bool(self.Constraints[int(index)].InVirtualSpace)

    def toggleVirtualSpace(self, index: int) -> None:
        constraint = self.Constraints[int(index)]
        constraint.InVirtualSpace = not bool(constraint.InVirtualSpace)

    def delConstraint(self, index: int, _no_solve: bool = False) -> dict:
        if index < 0 or index >= len(self.Constraints):
            raise IndexError("Fake constraint index is outside the Sketch.")
        deleted = self.Constraints[index]
        del self.Constraints[index]
        if deleted.Type == "Block" and not any(
            item.Type == "Block" and item.First == deleted.First
            for item in self.Constraints
        ):
            self.GeometryFacadeList[deleted.First].Blocked = False
        self.ConstraintCount = len(self.Constraints)
        return {"constraints": {"deleted": [{"index": index}]}}

    def _set_point_coordinate(
        self,
        index: int,
        position: int,
        coordinate: str,
        value: float,
    ) -> None:
        if index < 0:
            raise ValueError("Fake fixed geometry cannot move.")
        geometry = self.Geometry[index]
        if geometry.TypeId == "Part::GeomPoint" and position == 1:
            setattr(geometry, coordinate.upper(), float(value))
            return
        if position == 1 and hasattr(geometry, "StartPoint"):
            setattr(geometry.StartPoint, coordinate, float(value))
            return
        if position == 2 and hasattr(geometry, "EndPoint"):
            setattr(geometry.EndPoint, coordinate, float(value))
            return
        if position == 3 and hasattr(geometry, "Center"):
            setattr(geometry.Center, coordinate, float(value))
            return
        raise ValueError("Unsupported fake Sketch point movement.")

    def _solve_axis_distance(self, constraint, coordinate: str) -> None:
        if constraint.Second > -2000:
            first = self.getPoint(constraint.First, constraint.FirstPos)
            second = self.getPoint(constraint.Second, constraint.SecondPos)
            first_coordinate = float(getattr(first, coordinate))
            if math.isclose(
                float(getattr(second, coordinate)) - first_coordinate,
                float(constraint.Value),
                rel_tol=1.0e-9,
                abs_tol=1.0e-12,
            ):
                return
            self._set_point_coordinate(
                constraint.Second,
                constraint.SecondPos,
                coordinate,
                first_coordinate + float(constraint.Value),
            )
            return
        self._set_point_coordinate(
            constraint.First,
            constraint.FirstPos,
            coordinate,
            float(constraint.Value),
        )

    def _solve_general_distance(self, constraint) -> None:
        value = float(constraint.Value)
        if constraint.FirstPos and constraint.SecondPos:
            first = self.getPoint(constraint.First, constraint.FirstPos)
            second = self.getPoint(constraint.Second, constraint.SecondPos)
            dx = float(second.x) - float(first.x)
            dy = float(second.y) - float(first.y)
            length = math.hypot(dx, dy)
            if length <= 1.0e-12:
                return
            scale = value / length
            self._set_point_coordinate(
                constraint.Second,
                constraint.SecondPos,
                "x",
                float(first.x) + dx * scale,
            )
            self._set_point_coordinate(
                constraint.Second,
                constraint.SecondPos,
                "y",
                float(first.y) + dy * scale,
            )
            return
        if constraint.Second <= -2000:
            geometry = self.getGeometry(constraint.First)
            if geometry.TypeId == "Part::GeomLineSegment":
                dx = float(geometry.EndPoint.x) - float(geometry.StartPoint.x)
                dy = float(geometry.EndPoint.y) - float(geometry.StartPoint.y)
                length = math.hypot(dx, dy)
                if length <= 1.0e-12:
                    return
                geometry.EndPoint.x = float(geometry.StartPoint.x) + dx * value / length
                geometry.EndPoint.y = float(geometry.StartPoint.y) + dy * value / length
            elif geometry.TypeId == "Part::GeomArcOfCircle":
                span = float(geometry.LastParameter) - float(geometry.FirstParameter)
                if span <= 1.0e-12:
                    span += 2.0 * math.pi
                geometry.Radius = value / span
                geometry.StartPoint = geometry._point(geometry.FirstParameter)
                geometry.EndPoint = geometry._point(geometry.LastParameter)
            return
        if constraint.FirstPos and constraint.SecondPos == 0:
            curve = self.getGeometry(constraint.Second)
            point = self.getPoint(constraint.First, constraint.FirstPos)
            if curve.TypeId == "Part::GeomLineSegment":
                dx = float(curve.EndPoint.x) - float(curve.StartPoint.x)
                dy = float(curve.EndPoint.y) - float(curve.StartPoint.y)
                length = math.hypot(dx, dy)
                if length <= 1.0e-12:
                    return
                projection = (
                    (float(point.x) - float(curve.StartPoint.x)) * dx
                    + (float(point.y) - float(curve.StartPoint.y)) * dy
                ) / (length * length)
                base_x = float(curve.StartPoint.x) + projection * dx
                base_y = float(curve.StartPoint.y) + projection * dy
                side = dx * (float(point.y) - base_y) - dy * (float(point.x) - base_x)
                sign = 1.0 if side >= 0.0 else -1.0
                self._set_point_coordinate(
                    constraint.First,
                    constraint.FirstPos,
                    "x",
                    base_x - sign * dy * value / length,
                )
                self._set_point_coordinate(
                    constraint.First,
                    constraint.FirstPos,
                    "y",
                    base_y + sign * dx * value / length,
                )
            elif curve.TypeId in {"Part::GeomCircle", "Part::GeomArcOfCircle"}:
                dx = float(point.x) - float(curve.Center.x)
                dy = float(point.y) - float(curve.Center.y)
                radius = math.hypot(dx, dy)
                if radius <= 1.0e-12:
                    return
                target_radius = (
                    float(curve.Radius) + value
                    if radius >= float(curve.Radius)
                    else float(curve.Radius) - value
                )
                if target_radius <= 1.0e-12:
                    return
                self._set_point_coordinate(
                    constraint.First,
                    constraint.FirstPos,
                    "x",
                    float(curve.Center.x) + dx * target_radius / radius,
                )
                self._set_point_coordinate(
                    constraint.First,
                    constraint.FirstPos,
                    "y",
                    float(curve.Center.y) + dy * target_radius / radius,
                )

    def _solve_radiam(self, constraint) -> None:
        geometry = self.getGeometry(constraint.First)
        geometry.Radius = float(constraint.Value) / (
            2.0 if constraint.Type == "Diameter" else 1.0
        )
        if geometry.TypeId == "Part::GeomArcOfCircle":
            geometry.StartPoint = geometry._point(geometry.FirstParameter)
            geometry.EndPoint = geometry._point(geometry.LastParameter)

    def diagnoseAdditionalConstraints(self, constraints):
        proposed = list(constraints) if isinstance(constraints, list) else [constraints]
        if self.FeasibilityOverride is not None:
            return dict(self.FeasibilityOverride)
        driving_count = sum(bool(item.Driving) for item in proposed)
        return {
            "accepted": True,
            "degrees_of_freedom": max(0, int(self.DoF) - driving_count),
            "solver_status": 0,
            "first_proposed_constraint_index": int(self.ConstraintCount),
            "proposed_constraint_count": len(proposed),
            "conflicting_constraint_indices": [],
            "redundant_constraint_indices": [],
            "partially_redundant_constraint_indices": [],
            "malformed_constraint_indices": [],
        }

    def diagnoseConstraintReplacement(self, replaced_index: int, constraints):
        if replaced_index < 0 or replaced_index >= int(self.ConstraintCount):
            raise IndexError("Fake replacement index is outside the Sketch.")
        proposed = list(constraints) if isinstance(constraints, list) else [constraints]
        if self.FeasibilityOverride is not None:
            return dict(self.FeasibilityOverride)
        driving_count = sum(bool(item.Driving) for item in proposed)
        return {
            "accepted": True,
            "degrees_of_freedom": max(0, int(self.DoF) - driving_count),
            "solver_status": 0,
            "first_proposed_constraint_index": int(self.ConstraintCount) - 1,
            "proposed_constraint_count": len(proposed),
            "conflicting_constraint_indices": [],
            "redundant_constraint_indices": [],
            "partially_redundant_constraint_indices": [],
            "malformed_constraint_indices": [],
        }

    def setConstruction(self, index: int, construction: bool) -> None:
        if index >= 0:
            self.GeometryFacadeList[index].Construction = bool(construction)
            return
        self.ExternalGeo[-index - 1].extension.setFlag("Defining", construction)

    def toggleConstruction(self, index: int) -> None:
        if index >= 0:
            self.setConstruction(index, not self.getConstruction(index))
            return
        extension = self.ExternalGeo[-index - 1].extension
        extension.setFlag("Defining", not extension.testFlag("Defining"))

    def setTextAndFont(
        self,
        constraint_index: int,
        text: str,
        font: str,
        is_height: bool,
        is_construction: bool,
    ) -> None:
        constraint = self.Constraints[constraint_index]
        if constraint.Type != "Text" or not constraint.Elements:
            raise ValueError("Invalid fake Text constraint.")
        handle_index = constraint.Elements[0][0]
        handle = self.Geometry[handle_index]
        delta_x = handle.EndPoint.x - handle.StartPoint.x
        delta_y = handle.EndPoint.y - handle.StartPoint.y
        visible = [character for character in text if not character.isspace()]
        generated = []
        for character_index, _character in enumerate(visible):
            first_fraction = character_index / max(1, len(visible))
            second_fraction = (character_index + 0.4) / max(1, len(visible))
            first = SimpleNamespace(
                x=handle.StartPoint.x + first_fraction * delta_x,
                y=handle.StartPoint.y + first_fraction * delta_y,
                z=0.0,
            )
            second = SimpleNamespace(
                x=handle.StartPoint.x + second_fraction * delta_x,
                y=handle.StartPoint.y + second_fraction * delta_y + 1.0,
                z=0.0,
            )
            generated.append(self.addGeometry(FakeLine(first, second), is_construction))
        constraint.Text = str(text)
        constraint.Font = Path(str(font)).stem
        constraint.IsTextHeight = bool(is_height)
        constraint.Elements = ((handle_index, 0),) + tuple(
            (index, 0) for index in generated
        )
        padded = (*constraint.Elements, (-2000, 0), (-2000, 0), (-2000, 0))
        constraint.First, constraint.FirstPos = padded[0]
        constraint.Second, constraint.SecondPos = padded[1]
        constraint.Third, constraint.ThirdPos = padded[2]

    def exposeInternalGeometry(self, source_index: int) -> dict:
        source = self.Geometry[source_index]
        if source.TypeId == "Part::GeomBSplineCurve":
            return self._expose_bspline_internal(source_index, source)
        if source.TypeId == "Part::GeomArcOfHyperbola":
            return self._expose_hyperbola_internal(source_index, source)
        if source.TypeId == "Part::GeomArcOfParabola":
            return self._expose_parabola_internal(source_index, source)
        center = source.Center
        major_axis = source.XAxis
        minor_axis = (-major_axis.y, major_axis.x)
        focus = math.sqrt(
            source.MajorRadius * source.MajorRadius
            - source.MinorRadius * source.MinorRadius
        )
        geometries = (
            FakeLine(
                SimpleNamespace(
                    x=center.x + source.MajorRadius * major_axis.x,
                    y=center.y + source.MajorRadius * major_axis.y,
                    z=0.0,
                ),
                SimpleNamespace(
                    x=center.x - source.MajorRadius * major_axis.x,
                    y=center.y - source.MajorRadius * major_axis.y,
                    z=0.0,
                ),
            ),
            FakeLine(
                SimpleNamespace(
                    x=center.x + source.MinorRadius * minor_axis[0],
                    y=center.y + source.MinorRadius * minor_axis[1],
                    z=0.0,
                ),
                SimpleNamespace(
                    x=center.x - source.MinorRadius * minor_axis[0],
                    y=center.y - source.MinorRadius * minor_axis[1],
                    z=0.0,
                ),
            ),
            FakePoint(
                SimpleNamespace(
                    x=center.x + focus * major_axis.x,
                    y=center.y + focus * major_axis.y,
                    z=0.0,
                )
            ),
            FakePoint(
                SimpleNamespace(
                    x=center.x - focus * major_axis.x,
                    y=center.y - focus * major_axis.y,
                    z=0.0,
                )
            ),
        )
        before = len(self.Geometry)
        created = []
        for geometry, role in zip(
            geometries,
            (
                "EllipseMajorDiameter",
                "EllipseMinorDiameter",
                "EllipseFocus1",
                "EllipseFocus2",
            ),
            strict=True,
        ):
            index = len(self.Geometry)
            self.Geometry.append(geometry)
            self.GeometryFacadeList.append(
                fake_facade(
                    geometry,
                    index,
                    construction=True,
                    internal_type=role,
                )
            )
            created.append(
                {"geometry_index": index, "geometry_id": 100 + index, "role": role}
            )
        self.GeometryCount = len(self.Geometry)
        for offset, internal_index in enumerate(range(before, before + 4)):
            constraint = FakeConstraint(
                "InternalAlignment",
                internal_index,
                1 if offset >= 2 else 0,
                source_index,
                0,
            )
            self.addConstraint(constraint)
        return {
            "source_geometry_index": source_index,
            "geometry_count_before": before,
            "geometry_count_after": len(self.Geometry),
            "created_count": 3,
            "created": created,
        }

    def _expose_bspline_internal(self, source_index: int, source) -> dict:
        before = len(self.Geometry)
        created = []
        control_indices = [-1] * int(source.NbPoles)
        knot_indices = [-1] * int(source.NbKnots)
        for constraint in self.Constraints:
            if (
                constraint.Type != "InternalAlignment"
                or constraint.Second != source_index
            ):
                continue
            if constraint.AlignmentType == "BSplineControlPoint":
                control_indices[constraint.InternalAlignmentIndex] = constraint.First
            elif constraint.AlignmentType == "BSplineKnotPoint":
                knot_indices[constraint.InternalAlignmentIndex] = constraint.First

        first_control = control_indices[0]
        first_weight_constrained = first_control >= 0 and any(
            constraint.Type == "Weight" and constraint.First == first_control
            for constraint in self.Constraints
        )
        poles = source.getPoles()
        radius = (
            math.dist(
                (poles[0].x, poles[0].y, poles[0].z),
                (poles[1].x, poles[1].y, poles[1].z),
            )
            / 6.0
        )
        for pole_index, existing_index in enumerate(control_indices):
            if existing_index >= 0:
                continue
            geometry = FakeCircle(
                poles[pole_index],
                SimpleNamespace(x=0.0, y=0.0, z=1.0),
                radius,
            )
            index = len(self.Geometry)
            role = "BSplineControlPoint"
            self.Geometry.append(geometry)
            self.GeometryFacadeList.append(
                fake_facade(
                    geometry,
                    index,
                    construction=True,
                    internal_type=role,
                )
            )
            created.append(
                {"geometry_index": index, "geometry_id": 100 + index, "role": role}
            )
            self.addConstraint(
                FakeConstraint(
                    "InternalAlignment:Sketcher::BSplineControlPoint",
                    index,
                    3,
                    source_index,
                    pole_index,
                )
            )
            if pole_index == 0:
                first_control = index
                self.addConstraint(FakeConstraint("Weight", index, 1.0))
                first_weight_constrained = True
            elif first_weight_constrained:
                self.addConstraint(FakeConstraint("Equal", index, first_control))

        for knot_index, (knot, existing_index) in enumerate(
            zip(source.getKnots(), knot_indices, strict=True)
        ):
            if existing_index >= 0:
                continue
            geometry = FakePoint(source.value(knot))
            index = len(self.Geometry)
            role = "BSplineKnotPoint"
            self.Geometry.append(geometry)
            self.GeometryFacadeList.append(
                fake_facade(
                    geometry,
                    index,
                    construction=True,
                    internal_type=role,
                )
            )
            created.append(
                {"geometry_index": index, "geometry_id": 100 + index, "role": role}
            )
            self.addConstraint(
                FakeConstraint(
                    "InternalAlignment:Sketcher::BSplineKnotPoint",
                    index,
                    1,
                    source_index,
                    knot_index,
                )
            )
        self.GeometryCount = len(self.Geometry)
        return {
            "source_geometry_index": source_index,
            "geometry_count_before": before,
            "geometry_count_after": len(self.Geometry),
            "created_count": len(created),
            "created": created,
        }

    def _expose_hyperbola_internal(self, source_index: int, source) -> dict:
        center = source.Center
        major_axis = source.XAxis
        minor_axis = (-major_axis.y, major_axis.x)
        positive_major = SimpleNamespace(
            x=center.x + source.MajorRadius * major_axis.x,
            y=center.y + source.MajorRadius * major_axis.y,
            z=0.0,
        )
        negative_major = SimpleNamespace(
            x=center.x - source.MajorRadius * major_axis.x,
            y=center.y - source.MajorRadius * major_axis.y,
            z=0.0,
        )
        focus = math.sqrt(
            source.MajorRadius * source.MajorRadius
            + source.MinorRadius * source.MinorRadius
        )
        geometries = (
            FakeLine(positive_major, negative_major),
            FakeLine(
                SimpleNamespace(
                    x=positive_major.x + source.MinorRadius * minor_axis[0],
                    y=positive_major.y + source.MinorRadius * minor_axis[1],
                    z=0.0,
                ),
                SimpleNamespace(
                    x=positive_major.x - source.MinorRadius * minor_axis[0],
                    y=positive_major.y - source.MinorRadius * minor_axis[1],
                    z=0.0,
                ),
            ),
            FakePoint(
                SimpleNamespace(
                    x=center.x + focus * major_axis.x,
                    y=center.y + focus * major_axis.y,
                    z=0.0,
                )
            ),
        )
        roles = ("HyperbolaMajor", "HyperbolaMinor", "HyperbolaFocus")
        before = len(self.Geometry)
        created = []
        for geometry, role in zip(geometries, roles, strict=True):
            index = len(self.Geometry)
            self.Geometry.append(geometry)
            self.GeometryFacadeList.append(
                fake_facade(
                    geometry,
                    index,
                    construction=True,
                    internal_type=role,
                )
            )
            created.append(
                {"geometry_index": index, "geometry_id": 100 + index, "role": role}
            )
        self.GeometryCount = len(self.Geometry)
        for offset, internal_index in enumerate(range(before, before + 3)):
            self.addConstraint(
                FakeConstraint(
                    "InternalAlignment",
                    internal_index,
                    1 if offset == 2 else 0,
                    source_index,
                    0,
                )
            )
        return {
            "source_geometry_index": source_index,
            "geometry_count_before": before,
            "geometry_count_after": len(self.Geometry),
            "created_count": 3,
            "created": created,
        }

    def _expose_parabola_internal(self, source_index: int, source) -> dict:
        geometries = (
            FakePoint(source.Focus),
            FakeLine(source.Center, source.Focus),
        )
        roles = ("ParabolaFocus", "ParabolaFocalAxis")
        before = len(self.Geometry)
        created = []
        for geometry, role in zip(geometries, roles, strict=True):
            index = len(self.Geometry)
            self.Geometry.append(geometry)
            self.GeometryFacadeList.append(
                fake_facade(
                    geometry,
                    index,
                    construction=True,
                    internal_type=role,
                )
            )
            created.append(
                {"geometry_index": index, "geometry_id": 100 + index, "role": role}
            )
        self.GeometryCount = len(self.Geometry)
        self.addConstraint(
            FakeConstraint("InternalAlignment", before, 1, source_index, 0)
        )
        self.addConstraint(
            FakeConstraint("InternalAlignment", before + 1, 0, source_index, 0)
        )
        return {
            "source_geometry_index": source_index,
            "geometry_count_before": before,
            "geometry_count_after": len(self.Geometry),
            "created_count": 2,
            "created": created,
        }


def fake_context(document: FakeDocument) -> NativeRuntimeContext:
    state = NativeDocumentStateStore()
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("native-sketch-geometry-unit")
    return NativeRuntimeContext(
        service=object(),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "sketch.edit",
        edit_or_task_active=lambda: True,
    )


def install_fake_sketch_host(monkeypatch):
    document = FakeDocument()
    sketch = FakeSketch(document)
    monkeypatch.setattr(target_module, "active_edit_object", lambda: sketch)
    monkeypatch.setitem(
        sys.modules,
        "FreeCAD",
        SimpleNamespace(
            Vector=lambda x, y, z: SimpleNamespace(x=x, y=y, z=z),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Part",
        SimpleNamespace(
            Point=FakePoint,
            LineSegment=FakeLine,
            Circle=FakeCircle,
            ArcOfCircle=FakeArc,
            Ellipse=FakeEllipse,
            ArcOfEllipse=FakeEllipticalArc,
            Hyperbola=FakeHyperbola,
            ArcOfHyperbola=FakeHyperbolicArc,
            Parabola=FakeParabola,
            ArcOfParabola=FakeParabolicArc,
            BSplineCurve=FakeBSpline,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Sketcher",
        SimpleNamespace(Constraint=FakeConstraint),
    )
    return document, sketch, fake_context(document)


def geometry_target_values(**updates) -> dict[str, object]:
    result = {
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 1,
        "expected_constraint_count": 0,
    }
    result.update(updates)
    return result
