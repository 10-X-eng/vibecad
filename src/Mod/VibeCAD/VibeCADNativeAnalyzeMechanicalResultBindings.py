# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind concise structural result reads and presentation."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeCurrentTargets import current_state
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeMechanicalResultSchema import (
    ANALYZE_MECHANICAL_RESULTS,
    ANALYZE_SHOW_MECHANICAL,
)
from VibeCADNativeAnalyzeInspectRuntime import NativeAnalyzeInspectRuntime
from VibeCADNativeAnalyzePresentation import present_legacy_result
from VibeCADNativeAnalyzePresentationRuntime import NativeAnalyzePresentationRuntime
from VibeCADNativeAnalyzeResultState import result_state
from VibeCADNativeAnalyzeSolverState import solver_kind as analyze_solver_kind
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _semantic(field: Mapping[str, Any]) -> str:
    semantic = str(field.get("semantic") or "")
    if semantic:
        return semantic
    normalized = "".join(
        character
        for character in str(field.get("name") or "").casefold()
        if character.isalnum()
    )
    if "vonmises" in normalized:
        return "von_mises_stress"
    if "displacement" in normalized:
        return "displacement_magnitude"
    return ""


def _field_unit(
    state: Mapping[str, Any],
    semantic: str,
    field: Mapping[str, Any],
    solver_kind: str,
) -> str:
    if solver_kind == "calculix":
        if semantic == "von_mises_stress":
            return "MPa"
        if semantic == "displacement_magnitude":
            return "mm"
    unit = str(field.get("unit") or "")
    if unit:
        return unit
    if str(state.get("result_kind") or "") == "result":
        if semantic == "von_mises_stress":
            return "MPa"
        if semantic == "displacement_magnitude":
            return "mm"
    return ""


def _mechanical_fields(
    state: Mapping[str, Any],
    *,
    solver_kind: str = "",
) -> list[dict[str, Any]]:
    fields = []
    for raw in list(state.get("fields") or ()):
        if not isinstance(raw, Mapping):
            continue
        semantic = _semantic(raw)
        if semantic not in {"von_mises_stress", "displacement_magnitude"}:
            continue
        item = {
            key: raw[key]
            for key in ("name", "association", "components", "value_count", "range")
            if key in raw
        }
        item["semantic"] = semantic
        unit = _field_unit(state, semantic, raw, solver_kind)
        if unit:
            item["unit"] = unit
        value_range = item.get("range")
        source_unit = str(raw.get("unit") or "")
        if isinstance(value_range, list) and len(value_range) == 2:
            if source_unit == "Pa" and unit == "MPa":
                item["range"] = [float(value) / 1.0e6 for value in value_range]
            elif source_unit == "m" and unit == "mm":
                item["range"] = [float(value) * 1000.0 for value in value_range]
        fields.append(item)
    return fields


def _result_position(
    dataset: Any,
    association: str,
    index: int,
) -> dict[str, float] | None:
    try:
        if association == "point":
            coordinates = dataset.GetPoint(index)
        elif association == "cell":
            identifiers = dataset.GetCell(index).GetPointIds()
            count = int(identifiers.GetNumberOfIds())
            if count < 1:
                return None
            points = [
                dataset.GetPoint(identifiers.GetId(item)) for item in range(count)
            ]
            coordinates = tuple(
                sum(float(point[axis]) for point in points) / count
                for axis in range(3)
            )
        else:
            return None
        values = tuple(float(coordinates[axis]) for axis in range(3))
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return dict(zip(("x", "y", "z"), values, strict=True))


def _maximum_position(
    result: Any,
    field: Mapping[str, Any],
) -> dict[str, float] | None:
    try:
        dataset = result.getDataSet()
        association = str(field.get("association") or "")
        attributes = (
            dataset.GetPointData() if association == "point" else dataset.GetCellData()
        )
        array = attributes.GetArray(str(field["name"]))
        count = int(array.GetNumberOfTuples())
        components = int(array.GetNumberOfComponents())
        if count < 1 or components < 1:
            return None

        def value(index: int) -> float:
            if components == 1:
                return float(array.GetTuple1(index))
            return math.sqrt(
                sum(float(component) ** 2 for component in array.GetTuple(index))
            )

        values = tuple(
            (number, index)
            for index in range(count)
            if math.isfinite(number := value(index))
        )
        if not values:
            return None
        maximum_index = max(values)[1]
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None
    return _result_position(dataset, association, maximum_index)


def _summary(
    fields: list[Mapping[str, Any]],
    *,
    result: Any | None = None,
) -> dict[str, Any]:
    summary = {}
    output_names = {
        "von_mises_stress": "maximum_von_mises_stress",
        "displacement_magnitude": "maximum_displacement",
    }
    for field in fields:
        value_range = field.get("range")
        if not isinstance(value_range, list) or len(value_range) != 2:
            continue
        semantic = str(field.get("semantic") or "")
        name = output_names.get(semantic)
        if name:
            item = {
                "value": float(value_range[1]),
                "unit": str(field.get("unit") or ""),
            }
            position = _maximum_position(result, field) if result is not None else None
            if position is not None:
                item["result_position_mm"] = position
            summary[name] = item
    return summary


def _call_values(call: Any, runtime_type: type, operation: str) -> tuple[Any, dict[str, Any]]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, runtime_type):
        raise TypeError("A focused mechanical-result call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused mechanical-result call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != operation:
        raise ValueError(f"A focused mechanical-result call requires {operation}.")
    return runtime, values


def _read(call: Any) -> Mapping[str, Any]:
    runtime, values = _call_values(call, NativeAnalyzeInspectRuntime, "read")
    result, state = current_state(runtime, values.pop("result_name"), result_state)
    owner = getattr(result, "VibeCADTimelineOwner", None)
    try:
        backend = analyze_solver_kind(owner)
    except NativeAnalyzeError:
        backend = ""
    fields = _mechanical_fields(state, solver_kind=backend)
    if not fields:
        raise NativeAnalyzeError(
            "The named result contains no structural stress or displacement fields.",
            error_code="NATIVE_ANALYZE_RESULT_DATA_MISSING",
        )
    return {
        "result_name": str(state["object_name"]),
        "point_count": int(state.get("point_count", state.get("node_count", 0)) or 0),
        "cell_count": int(state.get("cell_count", 0) or 0),
        "fields": fields,
        **_summary(fields, result=result),
    }


def _show_post_result(result: Any, state: Mapping[str, Any], semantic: str) -> dict[str, Any]:
    fields = [field for field in _mechanical_fields(state) if field["semantic"] == semantic]
    if not fields:
        raise NativeAnalyzeError(
            f"The named result has no {semantic.replace('_', ' ')} field.",
            error_code="NATIVE_ANALYZE_RESULT_DATA_MISSING",
        )
    field_name = str(fields[0]["name"])
    view = getattr(result, "ViewObject", None)
    if view is None:
        raise NativeAnalyzeError("The structural result has no presentation object.")
    available = tuple(view.getEnumerationsOfProperty("Field") or ())
    if field_name not in available:
        raise NativeAnalyzeError(
            f"The structural result cannot display {field_name}.",
            error_code="NATIVE_ANALYZE_PRESENTATION_INVALID",
        )
    previous = {
        "field": str(getattr(view, "Field", "")),
        "component": str(getattr(view, "Component", "")),
        "visible": bool(getattr(view, "Visibility", False)),
    }
    view.Field = field_name
    components = tuple(view.getEnumerationsOfProperty("Component") or ())
    if "Magnitude" in components:
        view.Component = "Magnitude"
    view.Visibility = True
    return {
        "changed": previous
        != {
            "field": str(view.Field),
            "component": str(getattr(view, "Component", "")),
            "visible": bool(view.Visibility),
        },
        "result_name": str(result.Name),
        "field": semantic,
        "visible": True,
    }


def _show(call: Any) -> Mapping[str, Any]:
    runtime, values = _call_values(call, NativeAnalyzePresentationRuntime, "show")
    result, state = current_state(runtime, values.pop("result_name"), result_state)
    field = str(values.pop("field"))
    if state["result_kind"] == "result":
        response = present_legacy_result(
            runtime._context,
            result={
                "object_name": state["object_name"],
                "expected_state_sha256": state["state_sha256"],
            },
            field=field,
            deformation_scale=1.0,
            visible=True,
        )
        return {"result_name": state["object_name"], "field": field, **response}
    return _show_post_result(result, state, field)


def register_analyze_mechanical_result_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_MECHANICAL_RESULTS, _read)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_SHOW_MECHANICAL, _show)
    )


def analyze_mechanical_result_runtime_bindings(
    runtime: NativeAnalyzeInspectRuntime,
) -> dict[str, Any]:
    return {ANALYZE_MECHANICAL_RESULTS: runtime}


def analyze_mechanical_presentation_runtime_bindings(
    runtime: NativeAnalyzePresentationRuntime,
) -> dict[str, Any]:
    return {ANALYZE_SHOW_MECHANICAL: runtime}
