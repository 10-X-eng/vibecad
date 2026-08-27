# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded Mesh Analyze reads and detached full evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import (
    PreparedMeshTarget,
    is_active_mesh_input,
    is_live,
    mesh_target_still_exact,
    prepare_mesh_target,
)
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_SAMPLE_LIMIT = 16


def _evaluation_issue_report(issues: Mapping[str, Any]) -> dict[str, Any]:
    """Separate repair defects from exact geometric observations."""

    issue_counts: dict[str, int] = {}
    issue_samples: dict[str, Any] = {}
    observations: dict[str, Any] = {}
    for name, value in issues.items():
        if not isinstance(name, str) or not isinstance(value, Mapping):
            continue
        count = int(value.get("count", 0) or 0)
        samples = value.get("sample_indices", value.get("sample_pairs"))
        if name == "surface_fold_overs":
            if count > 0:
                observation = {
                    "count": count,
                    "threshold_degrees": 120,
                }
                if isinstance(samples, (list, tuple)) and samples:
                    observation["sample_facet_indices"] = list(samples)
                observations["steep_normal_transitions"] = observation
            continue
        issue_counts[name] = count
        if count > 0 and isinstance(samples, (list, tuple)) and samples:
            issue_samples[name] = list(samples)
    return {
        "repair_defects_found": any(value > 0 for value in issue_counts.values()),
        "issue_counts": issue_counts,
        "issue_samples": issue_samples,
        "geometric_observations": observations,
    }


@dataclass(frozen=True, slots=True)
class PreparedMeshEvaluation:
    target: PreparedMeshTarget
    degeneration_mode: str
    detached_mesh: Any


def _indices(values: Any, field: str, maximum: int) -> tuple[int, ...]:
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        raise NativeMeshError(f"{field} must contain 1 to {maximum} unique indices.")
    if any(type(value) is not int or value < 0 for value in values):
        raise NativeMeshError(f"Every {field} value must be a non-negative integer.")
    result = tuple(values)
    if len(result) != len(set(result)):
        raise NativeMeshError(f"{field} must not contain duplicates.")
    return result


def _target_summary(target: PreparedMeshTarget) -> dict[str, Any]:
    return {
        "object_name": str(target.source.Name),
        "label": str(target.source.Label),
        "state_sha256": target.expected_state_sha256,
    }


def prepare_mesh_evaluation(
    document: Any,
    document_uid: str,
    values: Mapping[str, Any],
) -> PreparedMeshEvaluation:
    target = prepare_mesh_target(
        document,
        document_uid,
        values["target"],
        require_label=False,
    )
    mode = str(values.get("degeneration_mode", "strict"))
    if mode not in {"strict", "mesh_tolerance"}:
        raise NativeMeshError(
            "degeneration_mode must be strict or mesh_tolerance."
        )
    return PreparedMeshEvaluation(
        target=target,
        degeneration_mode=mode,
        detached_mesh=target.source_mesh,
    )


def run_mesh_evaluation(
    request: PreparedMeshEvaluation,
    *,
    cancelled: Any,
    progress: Any,
) -> dict[str, Any]:
    from VibeCADNativeBackground import NativeBackgroundCancelled

    if cancelled():
        raise NativeBackgroundCancelled()
    progress(1, "Capturing exact Mesh snapshot")
    try:
        import Mesh

        detached, _digest = Mesh.snapshotWithSha256(request.detached_mesh)
        progress(5, "Evaluating detached Mesh quality")
        raw = Mesh.evaluateNative(
            detached,
            request.degeneration_mode,
            _SAMPLE_LIMIT,
        )
    except Exception as exc:
        raise NativeMeshError(
            "The native Mesh quality evaluation failed on its detached input.",
            error_code="NATIVE_MESH_EVALUATION_FAILED",
        ) from exc
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(85, "Preparing bounded Mesh quality report")
    issues = raw.get("issues") if isinstance(raw, Mapping) else None
    if not isinstance(issues, Mapping):
        raise NativeMeshError(
            "The native Mesh evaluator returned an invalid report.",
            error_code="NATIVE_MESH_EVALUATION_FAILED",
        )
    findings = _evaluation_issue_report(issues)
    return {
        "degeneration_mode": request.degeneration_mode,
        "topology": dict(raw.get("topology") or {}),
        "metrics": dict(raw.get("metrics") or {}),
        "solid": bool(raw.get("solid", False)),
        "watertight": bool(raw.get("watertight", False)),
        "open_edge_count": int(raw.get("open_edge_count", 0) or 0),
        **findings,
    }


def finalize_mesh_evaluation(
    document: Any,
    request: PreparedMeshEvaluation,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    if not mesh_target_still_exact(document, request.target):
        raise NativeMeshError(
            "The exact Mesh changed while its detached quality evaluation was running.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    return {"target": _target_summary(request.target), **dict(report)}


def inspect_mesh_facets(
    document: Any,
    document_uid: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    target = prepare_mesh_target(
        document,
        document_uid,
        values["target"],
        require_label=False,
    )
    indices = _indices(values["facet_indices"], "facet_indices", 32)
    facet_count = int(target.topology.get("facets", 0) or 0)
    if any(index >= facet_count for index in indices):
        raise NativeMeshError(
            f"facet_indices must be between 0 and {max(0, facet_count - 1)} for this Mesh."
        )
    try:
        import Mesh

        facets = Mesh.inspectNativeFacets(target.source.Mesh, list(indices))
    except Exception as exc:
        raise NativeMeshError(
            "The exact Mesh facets could not be inspected.",
            error_code="NATIVE_MESH_FACET_INSPECTION_FAILED",
        ) from exc
    if not mesh_target_still_exact(document, target):
        raise NativeMeshError(
            "The exact Mesh changed during facet inspection.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    return {"target": _target_summary(target), "facets": list(facets)}


def inspect_mesh_solid(
    document: Any,
    document_uid: str,
    value: Any,
) -> dict[str, Any]:
    target = prepare_mesh_target(document, document_uid, value, require_label=False)
    try:
        solid = bool(target.source.Mesh.isSolid())
        open_edges = int(target.source.Mesh.countOpenEdges())
    except Exception as exc:
        raise NativeMeshError(
            "The exact Mesh solid state could not be evaluated.",
            error_code="NATIVE_MESH_SOLID_EVALUATION_FAILED",
        ) from exc
    if not mesh_target_still_exact(document, target):
        raise NativeMeshError(
            "The exact Mesh changed during solid evaluation.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    return {
        "target": _target_summary(target),
        "solid": solid,
        "watertight": open_edges == 0,
        "open_edge_count": open_edges,
    }


def inspect_mesh_bounds(
    document: Any,
    document_uid: str,
    value: Any,
) -> dict[str, Any]:
    target = prepare_mesh_target(document, document_uid, value, require_label=False)
    state = mesh_object_state(target.source)
    bounds = state.get("bounds")
    if not isinstance(bounds, Mapping):
        raise NativeMeshError("The exact Mesh has no valid bounding box.")
    if not mesh_target_still_exact(document, target):
        raise NativeMeshError(
            "The exact Mesh changed during bounding-box inspection.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    return {"target": _target_summary(target), "bounds": dict(bounds)}


def inspect_mesh_curvature(
    document: Any,
    document_uid: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    target_value = values["curvature"]
    required = {"object_name", "expected_state_sha256"}
    if not isinstance(target_value, Mapping) or set(target_value) != required:
        raise NativeMeshError(
            "curvature must contain only object_name and expected_state_sha256."
        )
    reference = NativeObjectRef(document_uid, str(target_value["object_name"]))
    curvature = resolve_object(
        document,
        reference,
        expected_types=("Mesh::Curvature",),
    )
    if not is_active_mesh_input(curvature) or not curvature.isValid():
        raise NativeMeshError(
            "The retained curvature result is not valid at current Mesh History."
        )
    state = mesh_object_state(curvature)
    expected = str(target_value["expected_state_sha256"])
    if state.get("state_sha256") != expected:
        raise NativeMeshError(
            "The retained curvature result changed after the provider read its state.",
            error_code="NATIVE_MESH_STATE_STALE",
            repair={
                "target": {"object_name": reference.object_name},
                "current_state_sha256": state.get("state_sha256"),
            },
        )
    indices = _indices(values["vertex_indices"], "vertex_indices", 32)
    sample_count = int((state.get("topology") or {}).get("curvature_samples", 0) or 0)
    if sample_count < 1 or any(index >= sample_count for index in indices):
        raise NativeMeshError(
            f"vertex_indices must be between 0 and {max(0, sample_count - 1)} for this result."
        )
    try:
        import MeshGui

        samples = MeshGui.inspectNativeCurvature(curvature, list(indices))
    except Exception as exc:
        raise NativeMeshError(
            "The retained curvature samples could not be inspected.",
            error_code="NATIVE_MESH_CURVATURE_INSPECTION_FAILED",
        ) from exc
    current = mesh_object_state(curvature)
    if (
        not is_live(document, curvature)
        or not is_active_mesh_input(curvature)
        or current.get("state_sha256") != expected
    ):
        raise NativeMeshError(
            "The retained curvature result changed during inspection.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    return {
        "curvature": {
            "object_name": str(curvature.Name),
            "label": str(curvature.Label),
            "state_sha256": expected,
            "sample_count": sample_count,
        },
        "samples": list(samples),
    }
