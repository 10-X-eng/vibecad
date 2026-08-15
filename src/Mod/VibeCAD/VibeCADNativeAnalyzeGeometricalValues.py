# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong value and support-face preparation for FEM geometrical features."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import SimpleNamespace
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometricalState import canonical_axis_angle
from VibeCADNativeAnalyzeTargets import (
    PreparedGeometryReference,
    prepare_geometry_references,
)


_SECTION_VARIABLES = {
    "section_force": "Section Force",
    "heat_flux": "Heat Flux",
    "drag_stress": "Drag Stress",
    "electric_flux": "Electric Flux",
}


@dataclass(frozen=True, slots=True)
class PreparedGeometricalValues:
    kind: str
    native: dict[str, Any]
    definition: dict[str, Any]

    def normalized(self) -> dict[str, Any]:
        return dict(self.definition)


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite number.") from exc
    if not math.isfinite(number) or abs(number) > 1.0e30:
        raise NativeAnalyzeError(f"{field} must be finite and within +/-1e30.")
    return number


def _rotation(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"axis", "angle_degrees"}:
        raise NativeAnalyzeError(
            "coordinate_system.rotation must contain only axis and angle_degrees."
        )
    axis = value["axis"]
    if not isinstance(axis, Mapping) or set(axis) != {"x", "y", "z"}:
        raise NativeAnalyzeError(
            "coordinate_system.rotation.axis must contain only x, y, and z."
        )
    components = {
        component: _finite(
            axis[component],
            field=f"coordinate_system.rotation.axis.{component}",
        )
        for component in ("x", "y", "z")
    }
    if math.sqrt(sum(number * number for number in components.values())) <= 1.0e-15:
        raise NativeAnalyzeError(
            "coordinate_system.rotation.axis must have non-zero length."
        )
    angle = _finite(
        value["angle_degrees"],
        field="coordinate_system.rotation.angle_degrees",
    )
    if abs(angle) > 360000.0:
        raise NativeAnalyzeError(
            "coordinate_system.rotation.angle_degrees must be within +/-360000."
        )
    return canonical_axis_angle(SimpleNamespace(**components), angle)


def prepare_geometrical_values(kind: str, value: Any) -> PreparedGeometricalValues:
    if kind == "plane_rotation":
        if value not in (None, {}):
            raise NativeAnalyzeError("A plane-rotation feature has no value settings.")
        return PreparedGeometricalValues(kind, {}, {})
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError(
            "The FEM geometrical feature settings must be one typed object."
        )
    raw = dict(value)
    if kind == "section_print":
        if set(raw) != {"variable"}:
            raise NativeAnalyzeError(
                "Section-print settings must contain only variable."
            )
        variable = str(raw["variable"] or "")
        native = _SECTION_VARIABLES.get(variable)
        if native is None:
            raise NativeAnalyzeError(
                "variable must be section_force, heat_flux, drag_stress, or electric_flux."
            )
        return PreparedGeometricalValues(
            kind,
            {"Variable": native},
            {"variable": variable},
        )
    if kind != "transform" or set(raw) != {"coordinate_system"}:
        raise NativeAnalyzeError(
            "Local-transform settings must contain only coordinate_system."
        )
    system = raw["coordinate_system"]
    if not isinstance(system, Mapping):
        raise NativeAnalyzeError("coordinate_system must be one typed object.")
    system = dict(system)
    mode = str(system.get("kind", "") or "")
    if mode == "cylindrical" and set(system) == {"kind"}:
        return PreparedGeometricalValues(
            kind,
            {"TransformType": "Cylindrical"},
            {"coordinate_system": {"kind": "cylindrical"}},
        )
    if mode == "rectangular" and set(system) == {"kind", "rotation"}:
        rotation = _rotation(system["rotation"])
        return PreparedGeometricalValues(
            kind,
            {
                "TransformType": "Rectangular",
                "Rotation": rotation,
            },
            {
                "coordinate_system": {
                    "kind": "rectangular",
                    "rotation": rotation,
                }
            },
        )
    raise NativeAnalyzeError(
        "coordinate_system must be either {kind: cylindrical} or "
        "{kind: rectangular, rotation: {axis: {x, y, z}, angle_degrees}}."
    )


def apply_geometrical_values(obj: Any, prepared: PreparedGeometricalValues) -> None:
    if not isinstance(prepared, PreparedGeometricalValues):
        raise TypeError("prepared must be PreparedGeometricalValues")
    if prepared.kind == "transform":
        obj.TransformType = prepared.native["TransformType"]
        rotation = prepared.native.get("Rotation")
        if rotation is not None:
            import FreeCAD

            axis = rotation["axis"]
            obj.Rotation = FreeCAD.Rotation(
                FreeCAD.Vector(axis["x"], axis["y"], axis["z"]),
                rotation["angle_degrees"],
            )
        return
    for name, item in prepared.native.items():
        setattr(obj, name, item)


def prepare_feature_face(
    document: Any,
    document_uid: str,
    value: Any,
) -> PreparedGeometryReference:
    required = {"object_name", "expected_state_sha256", "subelement"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            "face must contain only object_name, expected_state_sha256, and subelement."
        )
    prepared = prepare_geometry_references(
        document,
        document_uid,
        [
            {
                "object_name": value["object_name"],
                "expected_state_sha256": value["expected_state_sha256"],
                "subelements": [value["subelement"]],
            }
        ],
        allowed_kinds=frozenset({"Face"}),
    )
    return prepared[0]


def feature_face_payload(face: PreparedGeometryReference) -> dict[str, Any]:
    return {
        "object_name": str(face.source.Name),
        "expected_state_sha256": face.expected_state_sha256,
        "subelement": face.subelements[0],
    }


def _face_shape(face: PreparedGeometryReference) -> Any:
    try:
        selected = face.source.Shape.getElement(face.subelements[0])
    except Exception as exc:
        raise NativeAnalyzeError(
            "The exact support face could not be resolved."
        ) from exc
    if str(getattr(selected, "ShapeType", "")) != "Face":
        raise NativeAnalyzeError("The exact geometrical feature support is not a face.")
    return selected


def _is_planar(face: PreparedGeometryReference) -> bool:
    try:
        return bool(_face_shape(face).Surface.isPlanar())
    except Exception:
        return False


def _is_cylindrical(face: PreparedGeometryReference) -> bool:
    try:
        surface = _face_shape(face).Surface
        return str(getattr(surface, "TypeId", "")) == "Part::GeomCylinder" or type(
            surface
        ).__name__ == "Cylinder"
    except Exception:
        return False


def _eligible_transform_conditions(analysis: Any, face: PreparedGeometryReference) -> list[Any]:
    matches = []
    expected_name = face.subelements[0]
    for member in tuple(getattr(analysis, "Group", ()) or ()):
        try:
            supported = member.isDerivedFrom(
                "Fem::ConstraintDisplacement"
            ) or member.isDerivedFrom("Fem::ConstraintForce")
        except Exception:
            supported = False
        if not supported:
            continue
        for raw in tuple(getattr(member, "References", ()) or ()):
            if not isinstance(raw, tuple) or len(raw) != 2:
                continue
            source, names = raw
            names = (names,) if isinstance(names, str) else tuple(names or ())
            if source is face.source and expected_name in names:
                matches.append(member)
                break
    return matches


def validate_feature_face(
    analysis: Any,
    kind: str,
    face: PreparedGeometryReference,
    values: PreparedGeometricalValues,
) -> tuple[Any, ...]:
    if kind == "plane_rotation" and not _is_planar(face):
        raise NativeAnalyzeError(
            "Plane rotation requires one planar face, matching the human editor."
        )
    if kind == "section_print":
        return ()
    if kind != "transform":
        return ()
    conditions = _eligible_transform_conditions(analysis, face)
    if not conditions:
        eligible = []
        for member in tuple(getattr(analysis, "Group", ()) or ()):
            try:
                supported = member.isDerivedFrom(
                    "Fem::ConstraintDisplacement"
                ) or member.isDerivedFrom("Fem::ConstraintForce")
            except Exception:
                supported = False
            if not supported:
                continue
            for raw in tuple(getattr(member, "References", ()) or ()):
                if not isinstance(raw, tuple) or len(raw) != 2:
                    continue
                source, names = raw
                names = (names,) if isinstance(names, str) else tuple(names or ())
                eligible.extend(
                    f"{getattr(source, 'Name', '?')}.{name}"
                    for name in names
                    if str(name).startswith("Face")
                )
        suffix = (
            f" Eligible faces: {', '.join(eligible[:16])}."
            if eligible
            else " The analysis currently has no eligible faces."
        )
        raise NativeAnalyzeError(
            "A local coordinate system requires a face already used by a displacement "
            "boundary condition or force load in the same analysis." + suffix
        )
    mode = values.definition["coordinate_system"]["kind"]
    if mode == "cylindrical" and not _is_cylindrical(face):
        raise NativeAnalyzeError(
            "A cylindrical local coordinate system requires one cylindrical face."
        )
    return tuple(conditions)

