# SPDX-License-Identifier: LGPL-2.1-or-later

"""Routing facade for the focused Mesh cut capability."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeMeshPlane import (
    PreparedMeshCrossSections,
    PreparedMeshPlaneSection,
    PreparedMeshPlaneTrim,
    create_mesh_plane_operation,
    prepare_mesh_plane_operation,
    verify_mesh_plane_operation,
)
from VibeCADNativeMeshPolygon import (
    PreparedMeshPolygon,
    create_mesh_polygon,
    prepare_mesh_polygon,
    verify_mesh_polygon,
)
from VibeCADNativeMeshViewportPolygon import (
    PreparedMeshViewportPolygon,
    create_mesh_viewport_polygon,
    prepare_mesh_viewport_polygon,
    verify_mesh_viewport_polygon,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeMeshTargets import mesh_target_still_exact, plane_target_still_exact


PreparedMeshCut = (
    PreparedMeshPolygon
    | PreparedMeshViewportPolygon
    | PreparedMeshPlaneTrim
    | PreparedMeshPlaneSection
    | PreparedMeshCrossSections
)


def prepare_mesh_cut(
    document: Any,
    document_uid: str,
    operation: str,
    values: Mapping[str, Any],
) -> PreparedMeshCut:
    if operation in {"viewport_cut", "viewport_trim"}:
        return prepare_mesh_viewport_polygon(document, document_uid, operation, values)
    if operation in {"poly_cut", "poly_trim"}:
        return prepare_mesh_polygon(document, document_uid, operation, values)
    return prepare_mesh_plane_operation(document, document_uid, operation, values)


def create_mesh_cut(document: Any, prepared: PreparedMeshCut) -> NativeMutationDraft:
    if isinstance(prepared, PreparedMeshViewportPolygon):
        return create_mesh_viewport_polygon(document, prepared)
    if isinstance(prepared, PreparedMeshPolygon):
        return create_mesh_polygon(document, prepared)
    return create_mesh_plane_operation(document, prepared)


def verify_mesh_cut(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    if isinstance(draft.value.get("prepared"), PreparedMeshViewportPolygon):
        return verify_mesh_viewport_polygon(document, draft)
    if isinstance(draft.value.get("prepared"), PreparedMeshPolygon):
        return verify_mesh_polygon(document, draft)
    return verify_mesh_plane_operation(document, draft)


def mesh_cut_still_exact(document: Any, prepared: PreparedMeshCut) -> bool:
    if isinstance(prepared, PreparedMeshViewportPolygon):
        return all(mesh_target_still_exact(document, target) for target in prepared.targets)
    if isinstance(prepared, PreparedMeshPolygon):
        return mesh_target_still_exact(document, prepared.target)
    if isinstance(prepared, (PreparedMeshPlaneTrim, PreparedMeshPlaneSection)):
        return mesh_target_still_exact(
            document, prepared.target
        ) and plane_target_still_exact(document, prepared.plane)
    if isinstance(prepared, PreparedMeshCrossSections):
        return all(mesh_target_still_exact(document, target) for target in prepared.targets)
    return False
