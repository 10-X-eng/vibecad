# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong value preparation for FEM mechanical support conditions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


_AXES = (("x", "X"), ("y", "Y"), ("z", "Z"))


@dataclass(frozen=True, slots=True)
class PreparedSupportValues:
    kind: str
    native: dict[str, Any]
    definition: dict[str, Any]
    allowed_reference_kinds: frozenset[str]

    def normalized(self) -> dict[str, Any]:
        return dict(self.definition)


def _finite(value: Any, *, field: str, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise NativeAnalyzeError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeAnalyzeError(f"{field} must be a finite number.") from exc
    if not math.isfinite(number) or abs(number) > 1.0e30:
        raise NativeAnalyzeError(f"{field} must be finite and within +/-1e30.")
    if nonnegative and number < 0.0:
        raise NativeAnalyzeError(f"{field} must be zero or positive.")
    return number


def _formula(value: Any, *, field: str) -> str:
    expression = str(value or "")
    if not expression or len(expression) > 512:
        raise NativeAnalyzeError(f"{field} must contain 1 to 512 characters.")
    if any(character in expression for character in ("\r", "\n", "\x00")):
        raise NativeAnalyzeError(
            f"{field} must be one line and contain no null character."
        )
    return expression


def _closed_axes(value: Any, *, field: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeAnalyzeError(f"{field} must contain exactly x, y, and z.")
    result = {}
    for axis in ("x", "y", "z"):
        item = value[axis]
        if not isinstance(item, Mapping):
            raise NativeAnalyzeError(f"{field}.{axis} must be one typed object.")
        result[axis] = dict(item)
    return result


def _reference_node(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise NativeAnalyzeError(
            "condition.reference_node_mm must contain exactly x, y, and z."
        )
    return {
        axis: _finite(
            value[axis],
            field=f"condition.reference_node_mm.{axis}",
        )
        for axis in ("x", "y", "z")
    }


def _rigid_translation(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    axes = _closed_axes(value, field="condition.translation")
    native: dict[str, Any] = {}
    definition: dict[str, Any] = {}
    for axis, suffix in _AXES:
        item = axes[axis]
        mode = str(item.get("kind", "") or "")
        if mode == "free" and set(item) == {"kind"}:
            native[f"TranslationalMode{suffix}"] = "Free"
            native[f"translation_{axis}"] = 0.0
            native[f"Force{suffix}"] = 0.0
            definition[axis] = {"kind": "free"}
        elif mode == "prescribed" and set(item) == {"kind", "displacement_mm"}:
            number = _finite(
                item["displacement_mm"],
                field=f"condition.translation.{axis}.displacement_mm",
            )
            native[f"TranslationalMode{suffix}"] = "Constraint"
            native[f"translation_{axis}"] = number
            native[f"Force{suffix}"] = 0.0
            definition[axis] = {"kind": mode, "displacement_mm": number}
        elif mode == "load" and set(item) == {"kind", "force_n"}:
            number = _finite(
                item["force_n"],
                field=f"condition.translation.{axis}.force_n",
            )
            native[f"TranslationalMode{suffix}"] = "Load"
            native[f"translation_{axis}"] = 0.0
            native[f"Force{suffix}"] = number
            definition[axis] = {"kind": mode, "force_n": number}
        else:
            raise NativeAnalyzeError(
                f"condition.translation.{axis} must be free, prescribed displacement_mm, or load force_n."
            )
    return native, definition


def _rigid_rotation(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    axes = _closed_axes(value, field="condition.rotation")
    native: dict[str, Any] = {}
    definition: dict[str, Any] = {}
    prescribed = [0.0, 0.0, 0.0]
    for index, (axis, suffix) in enumerate(_AXES):
        item = axes[axis]
        mode = str(item.get("kind", "") or "")
        if mode == "free" and set(item) == {"kind"}:
            native[f"RotationalMode{suffix}"] = "Free"
            native[f"Moment{suffix}"] = 0.0
            definition[axis] = {"kind": "free"}
        elif mode == "prescribed" and set(item) == {"kind", "rotation_degrees"}:
            number = _finite(
                item["rotation_degrees"],
                field=f"condition.rotation.{axis}.rotation_degrees",
            )
            prescribed[index] = number
            native[f"RotationalMode{suffix}"] = "Constraint"
            native[f"Moment{suffix}"] = 0.0
            definition[axis] = {"kind": mode, "rotation_degrees": number}
        elif mode == "load" and set(item) == {"kind", "moment_n_mm"}:
            number = _finite(
                item["moment_n_mm"],
                field=f"condition.rotation.{axis}.moment_n_mm",
            )
            native[f"RotationalMode{suffix}"] = "Load"
            native[f"Moment{suffix}"] = number
            definition[axis] = {"kind": mode, "moment_n_mm": number}
        else:
            raise NativeAnalyzeError(
                f"condition.rotation.{axis} must be free, prescribed rotation_degrees, or load moment_n_mm."
            )
    angle = math.sqrt(sum(number * number for number in prescribed))
    native["rotation"] = {
        "axis": (
            [number / angle for number in prescribed]
            if angle > 1.0e-15
            else [0.0, 0.0, 1.0]
        ),
        "angle_degrees": angle,
    }
    return native, definition


def _prepare_rigid_body(value: Mapping[str, Any]) -> PreparedSupportValues:
    if set(value) != {"reference_node_mm", "translation", "rotation"}:
        raise NativeAnalyzeError(
            "Rigid-body condition must contain only reference_node_mm, translation, and rotation."
        )
    reference_node = _reference_node(value["reference_node_mm"])
    translation_native, translation = _rigid_translation(value["translation"])
    rotation_native, rotation = _rigid_rotation(value["rotation"])
    return PreparedSupportValues(
        "rigid_body",
        {
            "reference_node_mm": reference_node,
            **translation_native,
            **rotation_native,
        },
        {
            "reference_node_mm": reference_node,
            "translation": translation,
            "rotation": rotation,
        },
        frozenset({"Vertex", "Edge", "Face"}),
    )


def _displacement_translation(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    axes = _closed_axes(value, field="condition.translation")
    native: dict[str, Any] = {}
    definition: dict[str, Any] = {}
    for axis, suffix in _AXES:
        item = axes[axis]
        mode = str(item.get("kind", "") or "")
        native[f"{axis}Displacement"] = 0.0
        native[f"{axis}DisplacementFormula"] = ""
        native[f"has{suffix}Formula"] = False
        if mode == "free" and set(item) == {"kind"}:
            native[f"{axis}Free"] = True
            definition[axis] = {"kind": "free"}
        elif mode == "value" and set(item) == {"kind", "displacement_mm"}:
            number = _finite(
                item["displacement_mm"],
                field=f"condition.translation.{axis}.displacement_mm",
            )
            native[f"{axis}Free"] = False
            native[f"{axis}Displacement"] = number
            definition[axis] = {"kind": mode, "displacement_mm": number}
        elif mode == "formula" and set(item) == {"kind", "expression"}:
            expression = _formula(
                item["expression"],
                field=f"condition.translation.{axis}.expression",
            )
            native[f"{axis}Free"] = False
            native[f"has{suffix}Formula"] = True
            native[f"{axis}DisplacementFormula"] = expression
            definition[axis] = {"kind": mode, "expression": expression}
        else:
            raise NativeAnalyzeError(
                f"condition.translation.{axis} must be free, value displacement_mm, or formula expression."
            )
    return native, definition


def _displacement_rotation(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    axes = _closed_axes(value, field="condition.rotation")
    native: dict[str, Any] = {}
    definition: dict[str, Any] = {}
    for axis, _suffix in _AXES:
        item = axes[axis]
        mode = str(item.get("kind", "") or "")
        if mode == "free" and set(item) == {"kind"}:
            native[f"rot{axis}Free"] = True
            native[f"{axis}Rotation"] = 0.0
            definition[axis] = {"kind": "free"}
        elif mode == "value" and set(item) == {"kind", "rotation_degrees"}:
            number = _finite(
                item["rotation_degrees"],
                field=f"condition.rotation.{axis}.rotation_degrees",
            )
            native[f"rot{axis}Free"] = False
            native[f"{axis}Rotation"] = number
            definition[axis] = {"kind": mode, "rotation_degrees": number}
        else:
            raise NativeAnalyzeError(
                f"condition.rotation.{axis} must be free or value rotation_degrees."
            )
    return native, definition


def _prepare_displacement(value: Mapping[str, Any]) -> PreparedSupportValues:
    if set(value) != {"translation", "rotation", "flow_surface_force"}:
        raise NativeAnalyzeError(
            "Displacement condition must contain only translation, rotation, and flow_surface_force."
        )
    translation_native, translation = _displacement_translation(value["translation"])
    rotation_native, rotation = _displacement_rotation(value["rotation"])
    flow = value["flow_surface_force"]
    if type(flow) is not bool:
        raise NativeAnalyzeError("condition.flow_surface_force must be true or false.")
    all_free = all(item["kind"] == "free" for item in (*translation.values(), *rotation.values()))
    if flow and not all_free:
        raise NativeAnalyzeError(
            "flow_surface_force requires every translation and rotation axis to be free."
        )
    if not flow and all_free:
        raise NativeAnalyzeError(
            "A displacement condition must constrain at least one axis or enable flow_surface_force."
        )
    return PreparedSupportValues(
        "displacement",
        {
            **translation_native,
            **rotation_native,
            "useFlowSurfaceForce": flow,
        },
        {
            "translation": translation,
            "rotation": rotation,
            "flow_surface_force": flow,
        },
        frozenset({"Vertex", "Edge", "Face"}),
    )


def _prepare_spring(value: Mapping[str, Any]) -> PreparedSupportValues:
    required = {
        "normal_stiffness_n_m",
        "tangential_stiffness_n_m",
        "elmer_component",
    }
    if set(value) != required:
        raise NativeAnalyzeError(
            "Spring condition must contain normal_stiffness_n_m, tangential_stiffness_n_m, and elmer_component."
        )
    normal = _finite(
        value["normal_stiffness_n_m"],
        field="condition.normal_stiffness_n_m",
        nonnegative=True,
    )
    tangential = _finite(
        value["tangential_stiffness_n_m"],
        field="condition.tangential_stiffness_n_m",
        nonnegative=True,
    )
    component = str(value["elmer_component"] or "")
    if component not in {"normal", "tangential"}:
        raise NativeAnalyzeError("condition.elmer_component must be normal or tangential.")
    selected = normal if component == "normal" else tangential
    if selected <= 0.0:
        raise NativeAnalyzeError(
            "The stiffness selected for Elmer must be greater than zero."
        )
    definition = {
        "normal_stiffness_n_m": normal,
        "tangential_stiffness_n_m": tangential,
        "elmer_component": component,
    }
    return PreparedSupportValues(
        "spring",
        {
            "NormalStiffness": normal,
            "TangentialStiffness": tangential,
            "ElmerStiffness": (
                "Normal Stiffness" if component == "normal" else "Tangential Stiffness"
            ),
        },
        definition,
        frozenset({"Face"}),
    )


def prepare_support_values(kind: str, value: Any) -> PreparedSupportValues:
    if kind == "fixed":
        if value not in (None, {}):
            raise NativeAnalyzeError("A fixed condition has no value settings.")
        return PreparedSupportValues(
            kind,
            {},
            {},
            frozenset({"Vertex", "Edge", "Face"}),
        )
    if not isinstance(value, Mapping):
        raise NativeAnalyzeError("condition must be one typed FEM support object.")
    raw = dict(value)
    if kind == "rigid_body":
        return _prepare_rigid_body(raw)
    if kind == "displacement":
        return _prepare_displacement(raw)
    if kind == "spring":
        return _prepare_spring(raw)
    raise NativeAnalyzeError("The requested FEM support-condition kind is unavailable.")


def apply_support_values(obj: Any, prepared: PreparedSupportValues) -> None:
    if not isinstance(prepared, PreparedSupportValues):
        raise TypeError("prepared must be PreparedSupportValues")
    native = prepared.native
    if prepared.kind == "rigid_body":
        import FreeCAD

        node = native["reference_node_mm"]
        obj.ReferenceNode = FreeCAD.Vector(node["x"], node["y"], node["z"])
        obj.Displacement = FreeCAD.Vector(
            native["translation_x"],
            native["translation_y"],
            native["translation_z"],
        )
        rotation = native["rotation"]
        obj.Rotation = FreeCAD.Rotation(
            FreeCAD.Vector(*rotation["axis"]),
            rotation["angle_degrees"],
        )
        for _axis, suffix in _AXES:
            setattr(obj, f"Force{suffix}", f"{native[f'Force{suffix}']} N")
            setattr(obj, f"Moment{suffix}", f"{native[f'Moment{suffix}']} N*mm")
            setattr(obj, f"TranslationalMode{suffix}", native[f"TranslationalMode{suffix}"])
            setattr(obj, f"RotationalMode{suffix}", native[f"RotationalMode{suffix}"])
        return
    if prepared.kind == "displacement":
        for axis, suffix in _AXES:
            setattr(obj, f"{axis}Free", native[f"{axis}Free"])
            setattr(obj, f"{axis}Displacement", f"{native[f'{axis}Displacement']} mm")
            setattr(obj, f"has{suffix}Formula", native[f"has{suffix}Formula"])
            setattr(
                obj,
                f"{axis}DisplacementFormula",
                native[f"{axis}DisplacementFormula"],
            )
            setattr(obj, f"rot{axis}Free", native[f"rot{axis}Free"])
            setattr(obj, f"{axis}Rotation", f"{native[f'{axis}Rotation']} deg")
        obj.useFlowSurfaceForce = native["useFlowSurfaceForce"]
        return
    if prepared.kind == "spring":
        obj.NormalStiffness = f"{native['NormalStiffness']} N/m"
        obj.TangentialStiffness = f"{native['TangentialStiffness']} N/m"
        obj.ElmerStiffness = native["ElmerStiffness"]
