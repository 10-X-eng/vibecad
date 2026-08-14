# SPDX-License-Identifier: LGPL-2.1-or-later

"""Retained source-linked native Mesh curvature plots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMeshTargets import (
    PreparedMeshTarget,
    is_live,
    mesh_target_still_exact,
    prepare_mesh_targets,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, object_reference


@dataclass(frozen=True, slots=True)
class PreparedMeshCurvature:
    targets: tuple[PreparedMeshTarget, ...]


@dataclass(frozen=True, slots=True)
class CreatedMeshCurvature:
    prepared: PreparedMeshCurvature
    results: tuple[Any, ...]
    controller: Any | None


def prepare_mesh_curvature(
    document: Any,
    document_uid: str,
    values: Mapping[str, Any],
) -> PreparedMeshCurvature:
    targets = prepare_mesh_targets(document, document_uid, values["targets"])
    if len(targets) > 16:
        raise NativeMeshError("targets must contain 1 to 16 exact Mesh targets.")
    return PreparedMeshCurvature(targets)


def create_mesh_curvature(
    document: Any,
    prepared: PreparedMeshCurvature,
) -> NativeMutationDraft:
    if any(not mesh_target_still_exact(document, target) for target in prepared.targets):
        raise NativeMeshError(
            "An exact Mesh changed after curvature preflight.",
            error_code="NATIVE_MESH_STATE_STALE",
        )
    import MeshGui

    results = []
    for target in prepared.targets:
        name = document.getUniqueObjectName(f"{target.source.Name}_Curvature")
        result = document.addObject("Mesh::Curvature", name)
        if result is None:
            raise NativeMeshError("The retained Mesh curvature object could not be created.")
        result.Label = target.label
        result.Source = target.source
        results.append(result)
    controller = MeshGui.publishSourcePreservingOutputs(
        document.Name,
        [target.source for target in prepared.targets],
        results,
        "MeshCurvatureResults",
        "Mesh Curvature",
        "Calculate mesh curvature",
    )
    created = [*results, *([controller] if controller is not None else [])]
    return NativeMutationDraft(
        value=CreatedMeshCurvature(prepared, tuple(results), controller),
        recompute_targets=tuple(created),
        created=tuple(object_identity(value) for value in created),
    )


def verify_mesh_curvature(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    created = draft.value
    if not isinstance(created, CreatedMeshCurvature):
        raise NativeMeshError("The Mesh curvature operation lost its prepared state.")
    for target, result in zip(
        created.prepared.targets,
        created.results,
        strict=True,
    ):
        if not mesh_target_still_exact(document, target):
            raise NativeMeshError(
                "A Mesh source changed while curvature was being calculated.",
                error_code="NATIVE_MESH_STATE_STALE",
            )
        if (
            not is_live(document, result)
            or not result.isValid()
            or result.Source is not target.source
            or int(result.SampleCount) != int(target.topology.get("points", 0) or 0)
        ):
            raise NativeMeshError(
                "The retained Mesh curvature result failed postcondition verification.",
                error_code="NATIVE_MESH_CURVATURE_POSTCONDITION_FAILED",
            )
    timeline = document.getObject("VibeCADTimeline")
    operations = tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()
    if created.controller is None:
        result = created.results[0]
        history_valid = (
            operations.count(result) == 1
            and str(getattr(result, "VibeCADTimelineRole", "") or "") == "operation"
            and getattr(result, "VibeCADTimelineOwner", None) is None
            and not tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        )
    else:
        history_valid = (
            is_live(document, created.controller)
            and operations.count(created.controller) == 1
            and tuple(getattr(created.controller, "Sources", ()) or ())
            == tuple(target.source for target in created.prepared.targets)
            and tuple(getattr(created.controller, "Group", ()) or ()) == created.results
            and str(getattr(created.controller, "InputMode", "") or "")
            == "Source preserving"
            and all(
                getattr(result, "VibeCADTimelineOwner", None) is created.controller
                for result in created.results
            )
        )
    if not history_valid:
        raise NativeMeshError(
            "The retained curvature results are not in the expected source-preserving History state.",
            error_code="NATIVE_MESH_CURVATURE_POSTCONDITION_FAILED",
        )
    result = {
        "results": [
            {
                **object_reference(value),
                "source_object_name": str(value.Source.Name),
                "sample_count": int(value.SampleCount),
                "state_sha256": mesh_object_state(value)["state_sha256"],
            }
            for value in created.results
        ]
    }
    if created.controller is not None:
        result["operation_controller"] = object_reference(created.controller)
    return result
