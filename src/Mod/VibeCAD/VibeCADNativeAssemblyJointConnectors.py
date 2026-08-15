# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact component-rooted connector targeting for Native Assembly joints."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Callable

from VibeCADNativeTargets import NativeObjectRef, object_reference, resolve_object


MAX_JOINT_CONNECTOR_PATH = 512
_NORMALIZED_CONNECTOR_PATH = re.compile(
    r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*)\.)*"
    r"(?:(?:Face|Edge|Vertex)[1-9][0-9]*)?$"
)
_SHAPE_ELEMENT = re.compile(r"^(Face|Edge|Vertex)([1-9][0-9]*)$")


class NativeAssemblyJointConnectorError(RuntimeError):
    """An exact Assembly joint connector is stale, malformed, or unsupported."""

    def failure(self) -> dict[str, str]:
        return {
            "error_code": "NATIVE_ASSEMBLY_JOINT_CONNECTOR_FAILED",
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class JointConnectorSpec:
    component_ref: NativeObjectRef
    element_path: str
    anchor_path: str
    offset: Any
    expected_component_placement: Any


@dataclass(frozen=True, slots=True)
class ResolvedJointConnector:
    spec: JointConnectorSpec
    component: Any
    reference: Any
    selected_object: Any
    selected_element: Any | None
    selected_anchor: Any | None
    local_frame: Any


def _timeline_active(obj: Any) -> bool:
    try:
        import UtilsAssembly

        return bool(UtilsAssembly.isTimelineOperationActive(obj))
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def placement_summary(placement: Any) -> dict[str, Any]:
    """Serialize a FreeCAD placement as a bounded axis-angle mapping."""

    base = getattr(placement, "Base", None)
    rotation = getattr(placement, "Rotation", None)
    axis = getattr(rotation, "Axis", None)
    angle_radians = float(getattr(rotation, "Angle", 0.0) or 0.0)
    values = (
        float(getattr(base, "x", 0.0)),
        float(getattr(base, "y", 0.0)),
        float(getattr(base, "z", 0.0)),
        float(getattr(axis, "x", 0.0)),
        float(getattr(axis, "y", 0.0)),
        float(getattr(axis, "z", 1.0)),
        angle_radians,
    )
    if not all(math.isfinite(value) for value in values):
        raise NativeAssemblyJointConnectorError(
            "An Assembly placement contains non-finite coordinates."
        )
    axis_values = values[3:6]
    if math.sqrt(sum(value * value for value in axis_values)) < 1.0e-12:
        axis_values = (0.0, 0.0, 1.0)
    return {
        "origin_mm": {
            "x": values[0],
            "y": values[1],
            "z": values[2],
        },
        "rotation": {
            "axis": {
                "x": axis_values[0],
                "y": axis_values[1],
                "z": axis_values[2],
            },
            "angle_degrees": math.degrees(values[6]),
        },
    }


def placement_is_same(first: Any, second: Any, tolerance: float = 1.0e-9) -> bool:
    if first is None or second is None:
        return first is second
    reader = getattr(first, "isSame", None)
    if callable(reader):
        try:
            return bool(reader(second, tolerance))
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return False
    return first == second


def component_placement(component: Any) -> Any:
    placement = getattr(component, "Placement", None)
    if placement is None:
        raise NativeAssemblyJointConnectorError(
            "An exact joint component has no placement."
        )
    return placement


def component_shape_summary(component: Any) -> dict[str, Any] | None:
    shape = getattr(component, "Shape", None)
    if shape is None:
        return None
    try:
        is_null = bool(shape.isNull())
        return {
            "null": is_null,
            "valid": False if is_null else bool(shape.isValid()),
            "solids": len(shape.Solids),
            "faces": len(shape.Faces),
            "edges": len(shape.Edges),
            "vertices": len(shape.Vertexes),
        }
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return None


def _path_parts(path: str, field: str) -> tuple[str, str]:
    value = str(path)
    if (
        len(value) > MAX_JOINT_CONNECTOR_PATH
        or "?" in value
        or _NORMALIZED_CONNECTOR_PATH.fullmatch(value) is None
    ):
        raise NativeAssemblyJointConnectorError(
            f"{field} must be one normalized component-relative object/shape path."
        )
    if not value or value.endswith("."):
        return value, ""
    prefix, separator, element = value.rpartition(".")
    return ((prefix + ".") if separator else "", element)


def validate_connector_paths(element_path: str, anchor_path: str) -> None:
    element_prefix, element_name = _path_parts(element_path, "element_path")
    anchor_prefix, anchor_name = _path_parts(anchor_path, "anchor_path")
    if element_prefix != anchor_prefix:
        raise NativeAssemblyJointConnectorError(
            "A joint connector element and anchor must target the same exact object."
        )
    if not element_name:
        if anchor_name:
            raise NativeAssemblyJointConnectorError(
                "A whole-object joint connector must use the same empty anchor."
            )
        return
    element_match = _SHAPE_ELEMENT.fullmatch(element_name)
    anchor_match = _SHAPE_ELEMENT.fullmatch(anchor_name)
    if element_match is None or anchor_match is None:
        raise NativeAssemblyJointConnectorError(
            "A shape connector must use exact FaceN, EdgeN, or VertexN endpoints."
        )
    element_kind = element_match.group(1)
    anchor_kind = anchor_match.group(1)
    allowed = {
        "Vertex": {"Vertex"},
        "Edge": {"Edge", "Vertex"},
        "Face": {"Face", "Edge", "Vertex"},
    }
    if anchor_kind not in allowed[element_kind]:
        raise NativeAssemblyJointConnectorError(
            f"A {element_kind} connector cannot use a {anchor_kind} anchor."
        )
    if element_kind == "Vertex" and element_name != anchor_name:
        raise NativeAssemblyJointConnectorError(
            "A vertex connector must anchor to that same exact vertex."
        )


def _get_element(shape: Any, name: str, message: str) -> Any:
    reader = getattr(shape, "getElement", None)
    if not callable(reader):
        raise NativeAssemblyJointConnectorError(message)
    try:
        element = reader(name)
    except Exception as exc:
        raise NativeAssemblyJointConnectorError(message) from exc
    if element is None:
        raise NativeAssemblyJointConnectorError(message)
    return element


def _validate_shape_anchor(
    selected_object: Any,
    element_name: str,
    anchor_name: str,
) -> tuple[Any, Any]:
    shape = getattr(selected_object, "Shape", None)
    selected = _get_element(
        shape,
        element_name,
        "The exact joint connector element no longer exists.",
    )
    element_kind = _SHAPE_ELEMENT.fullmatch(element_name).group(1)
    anchor_kind = _SHAPE_ELEMENT.fullmatch(anchor_name).group(1)
    if anchor_kind == "Vertex":
        anchor = _get_element(
            shape,
            anchor_name,
            "The exact joint connector vertex anchor no longer exists.",
        )
    elif element_kind == "Face" and anchor_kind == "Edge":
        anchor = _get_element(
            selected,
            anchor_name,
            "The exact face-local joint connector edge anchor no longer exists.",
        )
        curve_type = str(getattr(getattr(anchor, "Curve", None), "TypeId", ""))
        surface_type = str(
            getattr(getattr(selected, "Surface", None), "TypeId", "")
        )
        if curve_type not in {"Part::GeomCircle", "Part::GeomEllipse"} and not (
            surface_type == "Part::GeomCylinder"
            and curve_type == "Part::GeomBSplineCurve"
        ):
            raise NativeAssemblyJointConnectorError(
                "A face-local edge anchor must identify a supported curve center."
            )
    else:
        anchor = selected
        if element_kind == "Edge" and anchor_kind == "Edge":
            curve_type = str(
                getattr(getattr(selected, "Curve", None), "TypeId", "")
            )
            if curve_type not in {"Part::GeomCircle", "Part::GeomLine"}:
                raise NativeAssemblyJointConnectorError(
                    "An edge-center anchor requires a circular or linear edge."
                )
    return selected, anchor


def _frame_is_finite(placement: Any) -> bool:
    try:
        summary = placement_summary(placement)
    except NativeAssemblyJointConnectorError:
        return False
    return bool(summary)


def resolve_joint_connector(
    document: Any,
    assembly: Any,
    spec: JointConnectorSpec,
    *,
    timeline_reader: Callable[[Any], bool] = _timeline_active,
) -> ResolvedJointConnector:
    """Resolve one exact connector without changing selection or the document."""

    if not isinstance(spec, JointConnectorSpec):
        raise TypeError("spec must be a JointConnectorSpec")
    validate_connector_paths(spec.element_path, spec.anchor_path)
    component = resolve_object(document, spec.component_ref)
    try:
        import UtilsAssembly

        movable = UtilsAssembly.isMovableAssemblyComponent(assembly, component)
    except (ImportError, AttributeError, ReferenceError, RuntimeError, TypeError):
        movable = False
    if not timeline_reader(component) or not movable:
        raise NativeAssemblyJointConnectorError(
            "An exact joint target is not an active movable component of the human-active Assembly."
        )
    if not placement_is_same(
        component_placement(component),
        spec.expected_component_placement,
    ):
        raise NativeAssemblyJointConnectorError(
            "An exact joint component placement changed; read current Assemble state and retry."
        )
    reference = [component, [spec.element_path, spec.anchor_path]]
    selected_object = UtilsAssembly.getObject(reference)
    if selected_object is None or not timeline_reader(selected_object):
        raise NativeAssemblyJointConnectorError(
            "The exact component-rooted joint connector no longer resolves."
        )
    _prefix, element_name = _path_parts(spec.element_path, "element_path")
    _anchor_prefix, anchor_name = _path_parts(spec.anchor_path, "anchor_path")
    selected_element = None
    selected_anchor = None
    if element_name:
        selected_element, selected_anchor = _validate_shape_anchor(
            selected_object,
            element_name,
            anchor_name,
        )
    try:
        local_frame = UtilsAssembly.findPlacement(reference, False)
    except Exception as exc:
        raise NativeAssemblyJointConnectorError(
            "The exact joint connector frame could not be evaluated."
        ) from exc
    if not _frame_is_finite(local_frame):
        raise NativeAssemblyJointConnectorError(
            "The exact joint connector produced a non-finite frame."
        )
    return ResolvedJointConnector(
        spec=spec,
        component=component,
        reference=reference,
        selected_object=selected_object,
        selected_element=selected_element,
        selected_anchor=selected_anchor,
        local_frame=local_frame,
    )


def connector_summary(reference: Any, offset: Any) -> dict[str, Any]:
    try:
        component = reference[0]
        paths = list(reference[1])
        if component is None or len(paths) != 2:
            raise ValueError
    except (AttributeError, IndexError, ReferenceError, TypeError, ValueError) as exc:
        raise NativeAssemblyJointConnectorError(
            "The created joint contains a malformed connector reference."
        ) from exc
    return {
        "component": object_reference(component),
        "element_path": str(paths[0]),
        "anchor_path": str(paths[1]),
        "offset": placement_summary(offset),
    }
