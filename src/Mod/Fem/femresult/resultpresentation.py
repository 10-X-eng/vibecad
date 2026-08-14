# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact non-document presentation of legacy FEM mechanical results."""

from __future__ import annotations

from dataclasses import dataclass
import math

import FreeCAD


RESULT_PRESENTATION_FIELDS = (
    "none",
    "displacement_magnitude",
    "displacement_x",
    "displacement_y",
    "displacement_z",
    "temperature",
    "von_mises_stress",
    "maximum_principal_stress",
    "minimum_principal_stress",
    "maximum_shear_stress",
    "mass_flow_rate",
    "network_pressure",
    "equivalent_plastic_strain",
)

_FIELD_SOURCES = {
    "displacement_magnitude": ("DisplacementLengths", None),
    "displacement_x": ("DisplacementVectors", 0),
    "displacement_y": ("DisplacementVectors", 1),
    "displacement_z": ("DisplacementVectors", 2),
    "temperature": ("Temperature", None),
    "von_mises_stress": ("vonMises", None),
    "maximum_principal_stress": ("PrincipalMax", None),
    "minimum_principal_stress": ("PrincipalMin", None),
    "maximum_shear_stress": ("MaxShear", None),
    "mass_flow_rate": ("MassFlowRate", None),
    "network_pressure": ("NetworkPressure", None),
    "equivalent_plastic_strain": ("Peeq", None),
}

_DIALOG_FIELDS = {
    "none": "None",
    "displacement_magnitude": "Uabs",
    "displacement_x": "U1",
    "displacement_y": "U2",
    "displacement_z": "U3",
    "temperature": "Temp",
    "von_mises_stress": "Sabs",
    "maximum_principal_stress": "MaxPrin",
    "minimum_principal_stress": "MinPrin",
    "maximum_shear_stress": "MaxShear",
    "mass_flow_rate": "MFlow",
    "network_pressure": "NPress",
    "equivalent_plastic_strain": "Peeq",
}


@dataclass(frozen=True)
class PreparedResultPresentation:
    result: object
    mesh: object
    field: str
    values: tuple
    value_range: tuple | None
    deformation_scale: float
    visible: bool
    previous: dict


def _is_live(document, obj):
    try:
        return (
            document is not None
            and obj is not None
            and obj.Document is document
            and document.getObject(str(obj.Name)) is obj
            and document.getObject(int(obj.ID)) is obj
        )
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False


def _is_mechanical_result(result):
    try:
        return bool(result.isDerivedFrom("Fem::FemResultObject"))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _mesh_for_result(result):
    document = getattr(result, "Document", None)
    if not _is_live(document, result) or not _is_mechanical_result(result):
        raise ValueError("Result presentation requires one exact mechanical result")
    mesh = getattr(result, "Mesh", None)
    if not _is_live(document, mesh) or not mesh.isDerivedFrom("Fem::FemMeshObject"):
        raise ValueError("The mechanical result has no exact live FEM result mesh")
    return mesh


def _vector_component(value, component):
    try:
        return float((value.x, value.y, value.z)[component])
    except (AttributeError, IndexError, TypeError, ValueError):
        try:
            return float(value[component])
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("A displacement vector has invalid components") from exc


def _field_values(result, field):
    if field == "none":
        return ()
    source = _FIELD_SOURCES.get(field)
    if source is None:
        raise ValueError(f"Unsupported FEM result presentation field: {field}")
    property_name, component = source
    raw = tuple(getattr(result, property_name, ()) or ())
    values = tuple(
        _vector_component(value, component) if component is not None else float(value)
        for value in raw
    )
    if not values:
        raise ValueError(f"The result has no values for {field.replace('_', ' ')}")
    if any(not math.isfinite(value) for value in values):
        raise ValueError(
            f"The result contains non-finite {field.replace('_', ' ')} values"
        )
    return values


def available_result_presentation_fields(result):
    """Return the small semantic field set currently displayable by the result."""

    _mesh_for_result(result)
    return tuple(
        field
        for field in RESULT_PRESENTATION_FIELDS
        if field == "none" or _field_is_available(result, field)
    )


def _field_is_available(result, field):
    source = _FIELD_SOURCES.get(field)
    if source is None:
        return False
    try:
        return len(getattr(result, source[0], ()) or ()) > 0
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False


def _proxy_state(result):
    proxy = getattr(getattr(result, "ViewObject", None), "Proxy", None)
    state = getattr(proxy, "_vibecad_result_presentation", None)
    if not isinstance(state, dict) or state.get("object_id") != int(result.ID):
        return None
    return state


def result_presentation_state(result):
    """Read concise current presentation state without inspecting node colors."""

    mesh = _mesh_for_result(result)
    recorded = _proxy_state(result)
    visible = bool(getattr(mesh.ViewObject, "Visibility", False))
    available = list(available_result_presentation_fields(result))
    if recorded is None:
        return {
            "managed": False,
            "field": "unknown",
            "deformation_scale": None,
            "visible": visible,
            "available_fields": available,
        }
    field = str(recorded.get("field") or "unknown")
    return {
        "managed": True,
        "field": field,
        "deformation_scale": float(recorded.get("deformation_scale", 0.0)),
        "visible": visible,
        "available_fields": available,
        **(
            {"range": list(recorded["range"])}
            if recorded.get("range") is not None
            else {}
        ),
    }


def _validate_result_alignment(result, mesh, values, deformation_scale):
    node_numbers = tuple(getattr(result, "NodeNumbers", ()) or ())
    try:
        mesh_node_count = int(mesh.FemMesh.NodeCount)
    except Exception as exc:
        raise ValueError("The FEM result mesh node count is unavailable") from exc
    if mesh_node_count <= 0:
        raise ValueError("The FEM result mesh contains no nodes")
    if len(node_numbers) != mesh_node_count:
        raise ValueError(
            "The FEM result node identities do not match its result mesh node count"
        )
    if values and len(values) != len(node_numbers):
        raise ValueError("The selected result field does not cover every result node")
    displacement = tuple(getattr(result, "DisplacementVectors", ()) or ())
    if deformation_scale > 0.0 and len(displacement) != len(node_numbers):
        raise ValueError(
            "A nonzero deformation scale requires one displacement vector per result node"
        )


def prepare_result_presentation(result, field, deformation_scale, visible):
    """Validate and freeze one exact legacy-result presentation request."""

    semantic_field = str(field or "").strip()
    if semantic_field not in RESULT_PRESENTATION_FIELDS:
        raise ValueError(
            "field must be one of: " + ", ".join(RESULT_PRESENTATION_FIELDS)
        )
    if isinstance(deformation_scale, bool):
        raise ValueError("deformation_scale must be a finite number")
    try:
        scale = float(deformation_scale)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("deformation_scale must be a finite number") from exc
    if not math.isfinite(scale) or not 0.0 <= scale <= 1_000_000.0:
        raise ValueError("deformation_scale must be between 0 and 1000000")
    if type(visible) is not bool:
        raise ValueError("visible must be a boolean")
    mesh = _mesh_for_result(result)
    values = _field_values(result, semantic_field)
    _validate_result_alignment(result, mesh, values, scale)
    value_range = (min(values), max(values)) if values else None
    return PreparedResultPresentation(
        result,
        mesh,
        semantic_field,
        values,
        value_range,
        scale,
        visible,
        result_presentation_state(result),
    )


def _record(prepared):
    proxy = prepared.result.ViewObject.Proxy
    proxy._vibecad_result_presentation = {
        "object_id": int(prepared.result.ID),
        "field": prepared.field,
        "deformation_scale": prepared.deformation_scale,
        "range": prepared.value_range,
    }
    dialog = getattr(FreeCAD, "FEM_dialog", None)
    if not isinstance(dialog, dict):
        dialog = {
            "animate": [-1, -1, -1, -1, -1, -1, -1],
            "disp_factor_max": 100.0,
        }
        FreeCAD.FEM_dialog = dialog
    dialog.update(
        {
            "result_obj": prepared.result,
            "results_type": _DIALOG_FIELDS[prepared.field],
            "show_disp": prepared.deformation_scale > 0.0,
            "disp_factor": prepared.deformation_scale,
            "disp_factor_max": max(
                float(dialog.get("disp_factor_max", 100.0) or 100.0),
                prepared.deformation_scale,
            ),
        }
    )


def _apply(prepared):
    view = prepared.mesh.ViewObject
    if prepared.field == "none":
        view.NodeColor = {}
        view.ElementColor = {}
        reset_color = getattr(view, "resetNodeColor", None)
        if callable(reset_color):
            reset_color()
    else:
        view.setNodeColorByScalars(
            list(prepared.result.NodeNumbers),
            list(prepared.values),
        )
    if prepared.deformation_scale == 0.0:
        reset_displacement = getattr(view, "resetNodeDisplacement", None)
        if callable(reset_displacement):
            reset_displacement()
        else:
            view.applyDisplacement(0.0)
    else:
        view.setNodeDisplacementByVectors(
            list(prepared.result.NodeNumbers),
            list(prepared.result.DisplacementVectors),
        )
        view.applyDisplacement(prepared.deformation_scale)
    prepared.result.ViewObject.Visibility = prepared.visible
    view.Visibility = prepared.visible
    _record(prepared)


def _restore_unmanaged(prepared):
    view = prepared.mesh.ViewObject
    view.NodeColor = {}
    view.ElementColor = {}
    reset_color = getattr(view, "resetNodeColor", None)
    if callable(reset_color):
        reset_color()
    reset_displacement = getattr(view, "resetNodeDisplacement", None)
    if callable(reset_displacement):
        reset_displacement()
    else:
        view.applyDisplacement(0.0)
    prepared.result.ViewObject.Visibility = bool(prepared.previous["visible"])
    view.Visibility = bool(prepared.previous["visible"])
    proxy = prepared.result.ViewObject.Proxy
    if hasattr(proxy, "_vibecad_result_presentation"):
        del proxy._vibecad_result_presentation


def restore_result_presentation(prepared):
    """Best-effort exact rollback for a failed guarded presentation call."""

    if not isinstance(prepared, PreparedResultPresentation):
        raise TypeError("prepared must be a PreparedResultPresentation")
    previous = prepared.previous
    if not previous.get("managed") or previous.get("field") == "unknown":
        _restore_unmanaged(prepared)
        return
    restored = prepare_result_presentation(
        prepared.result,
        previous["field"],
        previous["deformation_scale"],
        bool(previous["visible"]),
    )
    _apply(restored)


def apply_result_presentation(prepared):
    """Apply and verify one prepared result presentation without a transaction."""

    if not isinstance(prepared, PreparedResultPresentation):
        raise TypeError("prepared must be a PreparedResultPresentation")
    _apply(prepared)
    state = result_presentation_state(prepared.result)
    expected_range = (
        list(prepared.value_range) if prepared.value_range is not None else None
    )
    if (
        state.get("field") != prepared.field
        or state.get("deformation_scale") != prepared.deformation_scale
        or state.get("visible") is not prepared.visible
        or state.get("range") != expected_range
    ):
        restore_result_presentation(prepared)
        raise RuntimeError("The FEM result presentation did not reach its exact state")
    return state
