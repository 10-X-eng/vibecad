# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, normalized state for FEM element-definition objects."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeState import is_live
from VibeCADNativeSnapshot import concise_object


_KINDS = {
    "Fem::ElementGeometry1D": "beam_section",
    "Fem::ElementRotation1D": "beam_rotation",
    "Fem::ElementGeometry2D": "shell_thickness",
    "Fem::ElementFluid1D": "fluid_section",
}
_BEAM_SECTIONS = {
    "Rectangular": ("rectangular", ("RectWidth", "RectHeight")),
    "Circular": ("circular", ("CircDiameter",)),
    "Pipe": ("pipe", ("PipeDiameter", "PipeThickness")),
    "Elliptical": ("elliptical", ("Axis1Length", "Axis2Length")),
    "Box": ("box", ("BoxWidth", "BoxHeight", "BoxT1", "BoxT2", "BoxT3", "BoxT4")),
}
_FLUID_SECTIONS = {
    "PIPE MANNING": "pipe_manning",
    "PIPE ENLARGEMENT": "pipe_enlargement",
    "PIPE CONTRACTION": "pipe_contraction",
    "PIPE INLET": "pipe_inlet",
    "PIPE OUTLET": "pipe_outlet",
    "PIPE ENTRANCE": "pipe_entrance",
    "PIPE DIAPHRAGM": "pipe_diaphragm",
    "PIPE BEND": "pipe_bend",
    "PIPE GATE VALVE": "pipe_gate_valve",
    "LIQUID PUMP": "liquid_pump",
    "PIPE WHITE-COLEBROOK": "pipe_white_colebrook",
}
_LENGTH_NAMES = (
    "RectWidth",
    "RectHeight",
    "CircDiameter",
    "PipeDiameter",
    "PipeThickness",
    "Axis1Length",
    "Axis2Length",
    "BoxWidth",
    "BoxHeight",
    "BoxT1",
    "BoxT2",
    "BoxT3",
    "BoxT4",
    "Thickness",
    "ManningRadius",
    "ColebrookeRadius",
    "ColebrookeGrainDiameter",
)
_AREA_NAMES = (
    "TrussArea",
    "ManningArea",
    "EnlargeArea1",
    "EnlargeArea2",
    "ContractArea1",
    "ContractArea2",
    "EntrancePipeArea",
    "EntranceArea",
    "DiaphragmPipeArea",
    "DiaphragmArea",
    "BendPipeArea",
    "GateValvePipeArea",
    "ColebrookeArea",
)
_FLOAT_NAMES = (
    "Offset",
    "ManningCoefficient",
    "InletPressure",
    "OutletPressure",
    "InletFlowRate",
    "OutletFlowRate",
    "BendRadiusDiameter",
    "BendAngle",
    "BendLossCoefficient",
    "GateValveClosingCoeff",
    "ColebrookeFormFactor",
)
_BOOL_NAMES = (
    "InletPressureActive",
    "OutletPressureActive",
    "InletFlowRateActive",
    "OutletFlowRateActive",
)


def element_definition_kind(obj: Any) -> str:
    proxy_type = str(getattr(getattr(obj, "Proxy", None), "Type", "") or "")
    kind = _KINDS.get(proxy_type)
    if kind is None:
        raise NativeAnalyzeError(
            "The exact target is not a supported FEM element definition.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    return kind


def _finite(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise NativeAnalyzeError(
            "A FEM element definition contains a non-finite value."
        )
    return round(number, 12)


def _quantity(obj: Any, name: str, unit: str) -> float:
    return _finite(getattr(obj, name).getValueAs(unit).Value)


def _references(obj: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    visible = []
    exact = []
    for raw in tuple(getattr(obj, "References", ()) or ()):
        if not isinstance(raw, tuple) or len(raw) != 2:
            continue
        source, names = raw
        if isinstance(names, str):
            names = (names,)
        record = {
            "object_name": str(getattr(source, "Name", "") or ""),
            "subelements": [str(name) for name in tuple(names or ())],
        }
        visible.append(record)
        exact.append({**record, "object_id": int(getattr(source, "ID", -1))})
    return visible, exact


def _all_properties(obj: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in _LENGTH_NAMES:
        if name in tuple(getattr(obj, "PropertiesList", ()) or ()):
            result[name] = _quantity(obj, name, "mm")
    for name in _AREA_NAMES:
        if name in tuple(getattr(obj, "PropertiesList", ()) or ()):
            result[name] = _quantity(obj, name, "mm^2")
    for name in _FLOAT_NAMES:
        if name in tuple(getattr(obj, "PropertiesList", ()) or ()):
            result[name] = _finite(getattr(obj, name))
    for name in _BOOL_NAMES:
        if name in tuple(getattr(obj, "PropertiesList", ()) or ()):
            result[name] = bool(getattr(obj, name))
    if "Rotation" in tuple(getattr(obj, "PropertiesList", ()) or ()):
        result["Rotation"] = _quantity(obj, "Rotation", "deg")
    for name in (
        "SectionType",
        "LiquidSectionType",
        "GasSectionType",
        "ChannelSectionType",
    ):
        if name in tuple(getattr(obj, "PropertiesList", ()) or ()):
            result[name] = str(getattr(obj, name))
    for name in ("PumpFlowRate", "PumpHeadLoss"):
        if name in tuple(getattr(obj, "PropertiesList", ()) or ()):
            result[name] = [_finite(value) for value in tuple(getattr(obj, name) or ())]
    return result


def _beam_properties(obj: Any) -> dict[str, Any]:
    native = str(obj.SectionType)
    definition = _BEAM_SECTIONS.get(native)
    if definition is None:
        raise NativeAnalyzeError(f"Unsupported beam section type {native!r}.")
    kind, names = definition
    labels = {
        "RectWidth": "width_mm",
        "RectHeight": "height_mm",
        "CircDiameter": "diameter_mm",
        "PipeDiameter": "outer_diameter_mm",
        "PipeThickness": "wall_thickness_mm",
        "Axis1Length": "axis_1_mm",
        "Axis2Length": "axis_2_mm",
        "BoxWidth": "width_mm",
        "BoxHeight": "height_mm",
        "BoxT1": "t1_mm",
        "BoxT2": "t2_mm",
        "BoxT3": "t3_mm",
        "BoxT4": "t4_mm",
    }
    return {
        "kind": kind,
        **{labels[name]: _quantity(obj, name, "mm") for name in names},
    }


def _fluid_properties(obj: Any) -> dict[str, Any]:
    native = str(obj.LiquidSectionType)
    kind = _FLUID_SECTIONS.get(native)
    if str(obj.SectionType) != "Liquid" or kind is None:
        raise NativeAnalyzeError(f"Unsupported 1D fluid section type {native!r}.")
    if kind == "pipe_manning":
        return {
            "kind": kind,
            "area_mm2": _quantity(obj, "ManningArea", "mm^2"),
            "hydraulic_radius_mm": _quantity(obj, "ManningRadius", "mm"),
            "manning_coefficient": _finite(obj.ManningCoefficient),
        }
    if kind == "pipe_enlargement":
        return {
            "kind": kind,
            "initial_area_mm2": _quantity(obj, "EnlargeArea1", "mm^2"),
            "enlarged_area_mm2": _quantity(obj, "EnlargeArea2", "mm^2"),
        }
    if kind == "pipe_contraction":
        return {
            "kind": kind,
            "initial_area_mm2": _quantity(obj, "ContractArea1", "mm^2"),
            "contracted_area_mm2": _quantity(obj, "ContractArea2", "mm^2"),
        }
    if kind in {"pipe_inlet", "pipe_outlet"}:
        stem = "Inlet" if kind == "pipe_inlet" else "Outlet"
        return {
            "kind": kind,
            "pressure_mpa": _finite(getattr(obj, f"{stem}Pressure")),
            "mass_flow_rate_kg_s": _finite(getattr(obj, f"{stem}FlowRate")),
            "pressure_active": bool(getattr(obj, f"{stem}PressureActive")),
            "mass_flow_rate_active": bool(getattr(obj, f"{stem}FlowRateActive")),
        }
    if kind == "pipe_entrance":
        return {
            "kind": kind,
            "pipe_area_mm2": _quantity(obj, "EntrancePipeArea", "mm^2"),
            "entrance_area_mm2": _quantity(obj, "EntranceArea", "mm^2"),
        }
    if kind == "pipe_diaphragm":
        return {
            "kind": kind,
            "pipe_area_mm2": _quantity(obj, "DiaphragmPipeArea", "mm^2"),
            "aperture_area_mm2": _quantity(obj, "DiaphragmArea", "mm^2"),
        }
    if kind == "pipe_bend":
        return {
            "kind": kind,
            "pipe_area_mm2": _quantity(obj, "BendPipeArea", "mm^2"),
            "bend_radius_to_diameter": _finite(obj.BendRadiusDiameter),
            "angle_degrees": _finite(obj.BendAngle),
            "loss_coefficient": _finite(obj.BendLossCoefficient),
        }
    if kind == "pipe_gate_valve":
        return {
            "kind": kind,
            "pipe_area_mm2": _quantity(obj, "GateValvePipeArea", "mm^2"),
            "closing_coefficient": _finite(obj.GateValveClosingCoeff),
        }
    if kind == "liquid_pump":
        rates = tuple(obj.PumpFlowRate or ())
        heads = tuple(obj.PumpHeadLoss or ())
        return {
            "kind": kind,
            "curve": [
                {"flow_rate_mm3_s": _finite(rate), "head_loss_mm": _finite(head)}
                for rate, head in zip(rates, heads)
            ],
        }
    return {
        "kind": kind,
        "pipe_area_mm2": _quantity(obj, "ColebrookeArea", "mm^2"),
        "hydraulic_radius_mm": _quantity(obj, "ColebrookeRadius", "mm"),
        "grain_diameter_mm": _quantity(obj, "ColebrookeGrainDiameter", "mm"),
        "form_factor": _finite(obj.ColebrookeFormFactor),
    }


def element_definition_state(obj: Any) -> dict[str, Any]:
    document = getattr(obj, "Document", None)
    if not is_live(document, obj):
        raise NativeAnalyzeError("The FEM element definition is no longer live.")
    kind = element_definition_kind(obj)
    references, exact_references = _references(obj)
    if kind == "beam_section":
        definition = _beam_properties(obj)
    elif kind == "beam_rotation":
        definition = {"rotation_degrees": _quantity(obj, "Rotation", "deg")}
    elif kind == "shell_thickness":
        definition = {"thickness_mm": _quantity(obj, "Thickness", "mm")}
    else:
        definition = _fluid_properties(obj)
    result = {
        **concise_object(obj),
        "element_definition_kind": kind,
        "references": references,
        "definition": definition,
    }
    payload = {
        "object_name": str(obj.Name),
        "object_id": int(obj.ID),
        "label": str(obj.Label),
        "kind": kind,
        "references": exact_references,
        "properties": _all_properties(obj),
    }
    result["state_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return result


def element_definition_still_exact(obj: Any, expected_sha256: str) -> bool:
    try:
        return element_definition_state(obj)["state_sha256"] == expected_sha256
    except NativeAnalyzeError:
        return False
