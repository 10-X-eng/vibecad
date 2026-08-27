# SPDX-License-Identifier: LGPL-2.1-or-later

"""Durable History result creation and proof for native CAM simulation."""

from __future__ import annotations

import json
import math
from typing import Any

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureSimulationResultInput import (
    FrozenNativeSimulation,
    SimulationTimelineState,
)
from VibeCADNativeManufactureSimulationResultWorker import (
    PreparedNativeSimulation,
)
from VibeCADNativeManufactureState import job_state, operation_state
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, object_reference, read_current_selection


_RESULT_COLOR = (0.5, 0.25, 0.25)
_PROPERTY_GROUP = "CAM Simulation"


def _error(message: str, code: str) -> None:
    raise NativeManufactureError(message, error_code=code)


def _timeline_state(document: Any) -> SimulationTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != (
        "App::DocumentTimeline"
    ):
        _error(
            "The CAM simulation result lost document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    position = int(timeline.Position)
    if (
        len(operations) != len(visibility)
        or len(operations) != len(suppression)
        or not 0 <= position <= len(operations)
    ):
        _error(
            "The CAM simulation result found malformed document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return SimulationTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _add_read_only_property(
    result: Any,
    type_id: str,
    name: str,
    description: str,
) -> None:
    if name not in tuple(getattr(result, "PropertiesList", ()) or ()):
        result.addProperty(
            type_id,
            name,
            _PROPERTY_GROUP,
            description,
            attr=16,  # App::Prop_NoRecompute
        )
    result.setEditorMode(name, 1)


def _set_provenance(result: Any, prepared: PreparedNativeSimulation) -> None:
    frozen = prepared.frozen
    _add_read_only_property(
        result,
        "App::PropertyLink",
        "SimulationJob",
        "Exact CAM Job used to create this retained material result",
    )
    _add_read_only_property(
        result,
        "App::PropertyString",
        "SimulationJobName",
        "Exact CAM Job used to create this retained material result",
    )
    _add_read_only_property(
        result,
        "App::PropertyString",
        "SimulationJobStateSHA256",
        "Exact CAM Job state used to create this retained material result",
    )
    _add_read_only_property(
        result,
        "App::PropertyStringList",
        "SimulationOperationNames",
        "Ordered active CAM operations included in the simulation",
    )
    _add_read_only_property(
        result,
        "App::PropertyInteger",
        "SimulationQuality",
        "Human-equivalent simulation quality from 1 through 10",
    )
    _add_read_only_property(
        result,
        "App::PropertyLength",
        "SimulationResolution",
        "Derived height-field resolution",
    )
    _add_read_only_property(
        result,
        "App::PropertyString",
        "SimulationProgramSHA256",
        "Digest of the exact placed CAM program and simulation settings",
    )
    _add_read_only_property(
        result,
        "Part::PropertyPartShape",
        "RetainedStockShape",
        "Solid remaining stock represented by this simulation result",
    )
    _add_read_only_property(
        result,
        "App::PropertyString",
        "RetainedStockShapeSHA256",
        "Digest of the solid remaining stock",
    )
    _add_read_only_property(
        result,
        "App::PropertyInteger",
        "RetainedStockSolidCount",
        "Solid regions in the remaining stock",
    )
    _add_read_only_property(
        result,
        "App::PropertyBool",
        "SimulationProtectedModelChecked",
        "Whether cutter sweeps were checked against the protected model",
    )
    _add_read_only_property(
        result,
        "App::PropertyBool",
        "SimulationProtectedModelCollision",
        "Whether a cutting or rapid tool sweep entered protected model volume",
    )
    _add_read_only_property(
        result,
        "App::PropertyInteger",
        "SimulationCollisionCommandCount",
        "Motion commands whose tool sweep entered protected model volume",
    )
    _add_read_only_property(
        result,
        "App::PropertyString",
        "SimulationVerificationJSON",
        "Structured verification coverage and findings",
    )
    result.SimulationJob = frozen.job
    result.SimulationJobName = str(frozen.job.Name)
    result.SimulationJobStateSHA256 = frozen.expected_job_state_sha256
    result.SimulationOperationNames = [run.operation_name for run in frozen.runs]
    result.SimulationQuality = frozen.quality
    result.SimulationResolution = frozen.stock_resolution_mm
    result.SimulationProgramSHA256 = prepared.program_sha256
    result.RetainedStockShape = prepared.stock_shape
    result.RetainedStockShapeSHA256 = prepared.stock_shape_sha256
    result.RetainedStockSolidCount = prepared.stock_solid_count
    protected = prepared.verification["protected_model"]
    result.SimulationProtectedModelChecked = bool(protected["checked"])
    result.SimulationProtectedModelCollision = bool(protected["collision"])
    result.SimulationCollisionCommandCount = int(
        protected["collision_command_count"]
    )
    result.SimulationVerificationJSON = json.dumps(
        prepared.verification,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def create_native_simulation_result(
    document: Any,
    prepared: PreparedNativeSimulation,
) -> NativeMutationDraft:
    """Create the sole durable result inside the Native-owned transaction."""

    if not isinstance(prepared, PreparedNativeSimulation):
        raise TypeError("prepared must be a PreparedNativeSimulation")
    frozen = prepared.frozen
    if tuple(document.Objects) != frozen.objects_before or _timeline_state(document) != (
        frozen.timeline_before
    ):
        _error(
            "The exact document graph or History changed before CAM simulation commit.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    visibility_before = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj in tuple(document.Objects)
        if getattr(obj, "ViewObject", None) is not None
    )
    selection_before = read_current_selection(document)
    name = document.getUniqueObjectName("CutMaterial")
    result = document.addObject("Mesh::FeaturePython", name)
    if result is None or str(getattr(result, "TypeId", "")) != "Mesh::FeaturePython":
        _error(
            "The retained CAM material-result object could not be created.",
            "NATIVE_MANUFACTURE_SIMULATION_COMMIT_FAILED",
        )
    result.ViewObject.Proxy = 0
    result.ViewObject.ShapeColor = (*_RESULT_COLOR, 0.0)
    result.Mesh = prepared.mesh
    _set_provenance(result, prepared)
    result.ViewObject.show()

    import Path.Base.Util as PathUtil

    PathUtil.markTimelineOperation(result)
    document.publishProvisionalTimelineOperationBlock(result, [])
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "result": result,
            "visibility_before": visibility_before,
            "selection_before": selection_before,
        },
        created=(object_identity(result),),
    )


def _verify_timeline(
    document: Any,
    before: SimulationTimelineState,
    result: Any,
) -> None:
    after = _timeline_state(document)
    expected = (
        *before.operations[: before.position],
        result,
        *before.operations[before.position :],
    )
    if (
        after.timeline is not before.timeline
        or after.operations != expected
        or after.position != before.position + 1
        or after.suppression[before.position]
    ):
        _error(
            "The CAM simulation result was not inserted at the exact History marker.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    for old_index in range(len(before.operations)):
        new_index = old_index if old_index < before.position else old_index + 1
        if (
            after.visibility[new_index] is not before.visibility[old_index]
            or after.suppression[new_index] is not before.suppression[old_index]
        ):
            _error(
                "Creating the CAM simulation result changed existing History state.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
            )


def _same_bounds(
    actual: Any,
    expected: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> bool:
    box = getattr(actual, "BoundBox", None)
    if box is None or not bool(getattr(box, "isValid", lambda: False)()):
        return False
    values = tuple(
        float(getattr(box, name))
        for name in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")
    )
    expected_values = (*expected[0], *expected[1])
    return all(
        math.isclose(value, expected_value, rel_tol=1.0e-7, abs_tol=1.0e-6)
        for value, expected_value in zip(values, expected_values)
    )


def verify_native_simulation_result(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove the durable Mesh, provenance, History, and unaffected UI state."""

    prepared = draft.value["prepared"]
    result = draft.value["result"]
    frozen: FrozenNativeSimulation = prepared.frozen
    expected_operations = [run.operation_name for run in frozen.runs]
    mesh = getattr(result, "Mesh", None)
    color = tuple(float(value) for value in result.ViewObject.ShapeColor[:3])
    if (
        tuple(document.Objects) != (*frozen.objects_before, result)
        or document.getObject(str(result.Name)) is not result
        or str(getattr(result, "TypeId", "")) != "Mesh::FeaturePython"
        or not bool(result.isValid())
        or int(getattr(mesh, "CountPoints", 0) or 0) != prepared.mesh_points
        or int(getattr(mesh, "CountFacets", 0) or 0) != prepared.mesh_facets
        or not _same_bounds(mesh, prepared.mesh_bounds_mm)
        or not bool(result.ViewObject.Visibility)
        or any(
            not math.isclose(actual, expected, abs_tol=1.0e-6)
            for actual, expected in zip(color, _RESULT_COLOR)
        )
        or str(getattr(result, "SimulationJobName", "")) != str(frozen.job.Name)
        or getattr(result, "SimulationJob", None) is not frozen.job
        or str(getattr(result, "SimulationJobStateSHA256", ""))
        != frozen.expected_job_state_sha256
        or list(getattr(result, "SimulationOperationNames", ()) or ())
        != expected_operations
        or int(getattr(result, "SimulationQuality", 0) or 0) != frozen.quality
        or not math.isclose(
            float(result.SimulationResolution.getValueAs("mm")),
            frozen.stock_resolution_mm,
            rel_tol=1.0e-9,
            abs_tol=1.0e-9,
        )
        or str(getattr(result, "SimulationProgramSHA256", ""))
        != prepared.program_sha256
        or getattr(result, "RetainedStockShape", None) is None
        or result.RetainedStockShape.isNull()
        or str(result.RetainedStockShape.ShapeType) != prepared.stock_shape_type
        or len(tuple(result.RetainedStockShape.Solids))
        != prepared.stock_solid_count
        or str(getattr(result, "RetainedStockShapeSHA256", ""))
        != prepared.stock_shape_sha256
        or int(getattr(result, "RetainedStockSolidCount", 0) or 0)
        != prepared.stock_solid_count
        or bool(getattr(result, "SimulationProtectedModelChecked", False))
        is not bool(prepared.verification["protected_model"]["checked"])
        or bool(getattr(result, "SimulationProtectedModelCollision", False))
        is not bool(prepared.verification["protected_model"]["collision"])
        or int(getattr(result, "SimulationCollisionCommandCount", -1))
        != int(prepared.verification["protected_model"]["collision_command_count"])
        or str(getattr(result, "SimulationVerificationJSON", ""))
        != json.dumps(
            prepared.verification,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        or str(getattr(result, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(result, "VibeCADTimelineOwner", None) is not None
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
    ):
        _error(
            "The retained CAM material result failed its exact postcondition.",
            "NATIVE_MANUFACTURE_SIMULATION_POSTCONDITION_FAILED",
        )
    _verify_timeline(document, frozen.timeline_before, result)
    if (
        read_current_selection(document) != draft.value["selection_before"]
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in draft.value["visibility_before"]
        )
    ):
        _error(
            "Creating the CAM simulation result changed human selection or existing visibility.",
            "NATIVE_MANUFACTURE_SIMULATION_POSTCONDITION_FAILED",
        )
    if (
        job_state(frozen.job).get("state_sha256")
        != frozen.expected_job_state_sha256
        or any(
            operation_state(run.operation).get("state_sha256")
            != run.expected_state_sha256
            for run in frozen.runs
        )
    ):
        _error(
            "Creating the CAM simulation result changed an exact simulation source.",
            "NATIVE_MANUFACTURE_SIMULATION_POSTCONDITION_FAILED",
        )

    state = mesh_object_state(result)
    statistics = prepared.statistics
    return {
        "simulation_result": {
            "result": state,
            "job": object_reference(frozen.job),
            "operations": expected_operations,
            "quality": frozen.quality,
            "resolution_mm": round(frozen.stock_resolution_mm, 9),
            "source_command_count": frozen.source_command_count,
            "executed_command_count": prepared.executed_command_count,
            "tool_run_count": len(frozen.runs),
            "program_sha256": prepared.program_sha256,
            "stock_removal": {
                "removed_volume_mm3": round(
                    float(statistics["removed_volume_mm3"]), 6
                ),
                "remaining_volume_mm3": round(
                    float(statistics["remaining_volume_mm3"]), 6
                ),
                "modified_cells": int(statistics["modified_cells"]),
            },
            "verification": dict(prepared.verification),
        }
    }
