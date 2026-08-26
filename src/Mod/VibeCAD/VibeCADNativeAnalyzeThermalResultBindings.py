# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind focused temperature reads and presentation to exact Analyze state."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeCurrentTargets import current_state
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeInspectRuntime import NativeAnalyzeInspectRuntime
from VibeCADNativeAnalyzePresentation import present_legacy_result
from VibeCADNativeAnalyzePresentationRuntime import NativeAnalyzePresentationRuntime
from VibeCADNativeAnalyzeResultState import result_state
from VibeCADNativeAnalyzeThermalResultSchema import (
    ANALYZE_SHOW_TEMPERATURE,
    ANALYZE_TEMPERATURE_RESULTS,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _temperature_field(state: Mapping[str, Any]) -> dict[str, Any] | None:
    for raw in list(state.get("fields") or ()):
        if not isinstance(raw, Mapping):
            continue
        semantic = str(raw.get("semantic") or "").casefold()
        name = str(raw.get("name") or "")
        normalized = "".join(character for character in name.casefold() if character.isalnum())
        if semantic != "temperature" and not normalized.startswith("temperature"):
            continue
        if "flux" in normalized:
            continue
        return {
            key: raw[key]
            for key in (
                "name",
                "association",
                "components",
                "value_count",
                "range",
                "unit",
            )
            if key in raw
        }
    return None


def _position(dataset: Any, association: str, index: int) -> dict[str, float] | None:
    try:
        if association == "point":
            coordinates = dataset.GetPoint(index)
        elif association == "cell":
            identifiers = dataset.GetCell(index).GetPointIds()
            count = int(identifiers.GetNumberOfIds())
            if count < 1:
                return None
            points = [dataset.GetPoint(identifiers.GetId(item)) for item in range(count)]
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


def _extreme_positions(result: Any, field: Mapping[str, Any]) -> dict[str, Any]:
    try:
        dataset = result.getDataSet()
        association = str(field.get("association") or "")
        attributes = (
            dataset.GetPointData() if association == "point" else dataset.GetCellData()
        )
        array = attributes.GetArray(str(field["name"]))
        count = int(array.GetNumberOfTuples())
        values = [float(array.GetTuple1(index)) for index in range(count)]
        finite = [(value, index) for index, value in enumerate(values) if math.isfinite(value)]
        if not finite:
            return {}
        minimum = min(finite)
        maximum = max(finite)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return {}
    result_positions = {}
    for name, item in (("minimum_position_mm", minimum), ("maximum_position_mm", maximum)):
        position = _position(dataset, association, item[1])
        if position is not None:
            result_positions[name] = position
    return result_positions


def _arguments(call: Any, runtime_type: type, operation: str) -> tuple[Any, dict[str, Any]]:
    runtime = getattr(call, "runtime", None)
    values = getattr(call, "arguments", None)
    if not isinstance(runtime, runtime_type):
        raise TypeError("A temperature-result call requires its exact runtime.")
    if not isinstance(values, Mapping):
        raise TypeError("A temperature-result call requires argument data.")
    result = dict(values)
    if result.pop("operation", None) != operation:
        raise ValueError(f"A temperature-result call requires {operation}.")
    return runtime, result


def _read(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzeInspectRuntime, "read")
    result, state = current_state(runtime, values.pop("result_name"), result_state)
    field = _temperature_field(state)
    value_range = field.get("range") if field is not None else None
    if not isinstance(value_range, list) or len(value_range) != 2:
        raise NativeAnalyzeError(
            "The named result contains no temperature range.",
            error_code="NATIVE_ANALYZE_RESULT_DATA_MISSING",
        )
    return {
        "result_name": str(state["object_name"]),
        "temperature_range_k": [float(value_range[0]), float(value_range[1])],
        **_extreme_positions(result, field),
    }


def _show_pipeline(result: Any, field: Mapping[str, Any]) -> dict[str, Any]:
    view = getattr(result, "ViewObject", None)
    if view is None:
        raise NativeAnalyzeError("The temperature result has no presentation object.")
    field_name = str(field["name"])
    available = tuple(view.getEnumerationsOfProperty("Field") or ())
    if field_name not in available:
        raise NativeAnalyzeError(
            "The result cannot display its temperature field.",
            error_code="NATIVE_ANALYZE_PRESENTATION_INVALID",
        )
    previous = {
        "field": str(getattr(view, "Field", "")),
        "visible": bool(getattr(view, "Visibility", False)),
    }
    view.Field = field_name
    view.Visibility = True
    current = {"field": str(view.Field), "visible": bool(view.Visibility)}
    return {
        "changed": previous != current,
        "result_name": str(result.Name),
        "field": "temperature",
        "visible": True,
    }


def _show(call: Any) -> Mapping[str, Any]:
    runtime, values = _arguments(call, NativeAnalyzePresentationRuntime, "show")
    result, state = current_state(runtime, values.pop("result_name"), result_state)
    field = _temperature_field(state)
    if field is None:
        raise NativeAnalyzeError(
            "The named result contains no temperature field.",
            error_code="NATIVE_ANALYZE_RESULT_DATA_MISSING",
        )
    if state["result_kind"] == "result":
        response = present_legacy_result(
            runtime._context,
            result={
                "object_name": state["object_name"],
                "expected_state_sha256": state["state_sha256"],
            },
            field="temperature",
            deformation_scale=1.0,
            visible=True,
        )
        return {"result_name": state["object_name"], "field": "temperature", **response}
    return _show_pipeline(result, field)


def register_analyze_thermal_result_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_TEMPERATURE_RESULTS, _read)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_SHOW_TEMPERATURE, _show)
    )


def analyze_thermal_result_runtime_bindings(
    runtime: NativeAnalyzeInspectRuntime,
) -> dict[str, Any]:
    return {ANALYZE_TEMPERATURE_RESULTS: runtime}


def analyze_thermal_presentation_runtime_bindings(
    runtime: NativeAnalyzePresentationRuntime,
) -> dict[str, Any]:
    return {ANALYZE_SHOW_TEMPERATURE: runtime}
