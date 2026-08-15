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
from VibeCADNativeMutation import NativeMutationDraft


PreparedMeshCut = (
    PreparedMeshPolygon
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
    if operation in {"poly_cut", "poly_trim"}:
        return prepare_mesh_polygon(document, document_uid, operation, values)
    return prepare_mesh_plane_operation(document, document_uid, operation, values)


def create_mesh_cut(document: Any, prepared: PreparedMeshCut) -> NativeMutationDraft:
    if isinstance(prepared, PreparedMeshPolygon):
        return create_mesh_polygon(document, prepared)
    return create_mesh_plane_operation(document, prepared)


def verify_mesh_cut(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    if isinstance(draft.value.get("prepared"), PreparedMeshPolygon):
        return verify_mesh_polygon(document, draft)
    return verify_mesh_plane_operation(document, draft)
