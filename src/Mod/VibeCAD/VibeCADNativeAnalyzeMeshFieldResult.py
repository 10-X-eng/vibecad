# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact scalar post-result targets for adaptive Gmsh size fields."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzePostSampling import select_post_point_field
from VibeCADNativeAnalyzeResultState import (
    PreparedResultTarget,
    prepare_result_target,
    result_reference_state,
    result_state,
)
from VibeCADNativeAnalyzeState import is_live


@dataclass(frozen=True, slots=True)
class PreparedMeshFieldResult:
    target: PreparedResultTarget
    field: str
    unit: str
    value_count: int
    value_range: tuple[float, float]

    @property
    def result(self) -> Any:
        return self.target.result

    def response(self) -> dict[str, Any]:
        result = {
            "object_name": str(self.result.Name),
            "field": self.field,
            "value_count": self.value_count,
            "value_range": list(self.value_range),
        }
        if self.unit:
            result["unit"] = self.unit
        return result


def _field_range(result: Any, field: str) -> tuple[int, tuple[float, float]]:
    try:
        array = result.getDataSet().GetPointData().GetArray(field)
        components = int(array.GetNumberOfComponents())
        count = int(array.GetNumberOfTuples())
    except Exception as exc:
        raise NativeAnalyzeError(
            "The adaptive-mesh result field has no readable scalar data."
        ) from exc
    if components != 1 or count < 1:
        raise NativeAnalyzeError(
            "An adaptive Gmsh result field must contain at least one scalar point value."
        )
    try:
        from vtk.util.numpy_support import vtk_to_numpy
        import numpy as np

        values = vtk_to_numpy(array)
        valid = bool(np.all(np.isfinite(values))) and bool(np.all(values > 0.0))
        lower = float(np.min(values))
        upper = float(np.max(values))
    except ImportError:
        lower = math.inf
        upper = -math.inf
        valid = True
        for index in range(count):
            value = float(array.GetComponent(index, 0))
            if not math.isfinite(value) or value <= 0.0:
                valid = False
                break
            lower = min(lower, value)
            upper = max(upper, value)
    except Exception as exc:
        raise NativeAnalyzeError(
            "The adaptive-mesh result field values could not be validated."
        ) from exc
    if not valid or not math.isfinite(lower) or not math.isfinite(upper):
        repair = {
            "field": field,
            "required_values": "all finite and strictly positive",
        }
        if math.isfinite(lower) and math.isfinite(upper):
            repair["observed_range"] = [lower, upper]
        else:
            repair["contains_nonfinite_values"] = True
        raise NativeAnalyzeError(
            "Every adaptive Gmsh result-field value must be finite and strictly positive.",
            error_code="NATIVE_ANALYZE_FIELD_RANGE_INVALID",
            repair=repair,
        )
    return count, (
        float(format(lower, ".15g")),
        float(format(upper, ".15g")),
    )


def prepare_mesh_field_result(
    document: Any,
    document_uid: str,
    target: Any,
    field: Any,
) -> PreparedMeshFieldResult:
    prepared = prepare_result_target(
        document,
        document_uid,
        target,
        expected_kinds=frozenset({"pipeline", "branch_filter", "filter"}),
    )
    selected = select_post_point_field(prepared.result, field)
    if int(selected["components"]) != 1:
        raise NativeAnalyzeError(
            f"Adaptive Gmsh field {selected['name']!r} has "
            f"{selected['components']} components; exactly one is required.",
            error_code="NATIVE_ANALYZE_FIELD_COMPONENT_INVALID",
            repair={
                "field": selected["name"],
                "field_components": int(selected["components"]),
                "required_components": 1,
            },
        )
    count, value_range = _field_range(prepared.result, selected["name"])
    return PreparedMeshFieldResult(
        prepared,
        selected["name"],
        str(selected.get("unit", "") or ""),
        count,
        value_range,
    )


def mesh_field_result_still_exact(prepared: PreparedMeshFieldResult) -> bool:
    if not isinstance(prepared, PreparedMeshFieldResult):
        return False
    result = prepared.result
    if not is_live(getattr(result, "Document", None), result):
        return False
    try:
        state = result_state(result, include_ranges=False)
        selected = select_post_point_field(result, prepared.field)
        count, value_range = _field_range(result, prepared.field)
    except Exception:
        return False
    return (
        state["state_sha256"] == prepared.target.expected_state_sha256
        and int(selected["components"]) == 1
        and count == prepared.value_count
        and value_range == prepared.value_range
    )


def current_mesh_field_result_target(result: Any) -> dict[str, Any]:
    state = result_reference_state(result)
    return {
        "object_name": state["object_name"],
        "expected_state_sha256": state["state_sha256"],
    }
