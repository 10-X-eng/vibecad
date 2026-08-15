# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-authorized, off-thread Mesh file preparation and exact commit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from VibeCADNativeInput import NativeInputArtifact, NativeInputRequest
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_MESH_IMPORT_BYTES = 8 * 1024 * 1024 * 1024
MESH_IMPORT_SUFFIXES = (
    ".stl",
    ".ast",
    ".bms",
    ".obj",
    ".off",
    ".iv",
    ".ply",
    ".nas",
    ".bdf",
)


@dataclass(frozen=True, slots=True)
class PreparedMeshImport:
    artifact: NativeInputArtifact
    mesh: Any
    point_count: int
    facet_count: int


def mesh_import_input_request() -> NativeInputRequest:
    return NativeInputRequest(
        purpose="import_mesh",
        title="Import Mesh",
        allowed_suffixes=MESH_IMPORT_SUFFIXES,
        name_filter=(
            "Mesh files (*.stl *.ast *.bms *.obj *.off *.iv *.ply *.nas *.bdf)"
        ),
        maximum_bytes=MAX_MESH_IMPORT_BYTES,
    )


def prepare_mesh_import(
    authorization: Any,
    request: NativeInputRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedMeshImport:
    if cancelled():
        from VibeCADNativeBackground import NativeBackgroundCancelled

        raise NativeBackgroundCancelled()
    progress(5, "Verifying selected mesh file")
    artifact = authorization.claim(request)
    if cancelled():
        from VibeCADNativeBackground import NativeBackgroundCancelled

        raise NativeBackgroundCancelled()
    progress(20, "Reading detached mesh data")
    try:
        import Mesh

        mesh = Mesh.read(str(artifact.host_path_after_content_verification()))
    except Exception as exc:
        raise NativeMeshError(
            "The selected file could not be read as a supported mesh.",
            error_code="NATIVE_MESH_IMPORT_INVALID",
        ) from exc
    artifact.host_path_after_content_verification()
    points = int(getattr(mesh, "CountPoints", 0) or 0)
    facets = int(getattr(mesh, "CountFacets", 0) or 0)
    if points < 3 or facets < 1:
        raise NativeMeshError(
            "The selected file contains no usable mesh facets.",
            error_code="NATIVE_MESH_IMPORT_EMPTY",
        )
    progress(85, "Mesh data verified")
    return PreparedMeshImport(artifact, mesh, points, facets)


def commit_mesh_import(document: Any, prepared: PreparedMeshImport) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshImport):
        raise TypeError("prepared must be a PreparedMeshImport")
    obj = document.addObject(
        "Mesh::Feature",
        document.getUniqueObjectName("ImportedMesh"),
    )
    if obj is None:
        raise NativeMeshError("The imported Mesh result could not be created.")
    obj.Label = Path(prepared.artifact.file_name).stem[:160] or "Imported Mesh"
    obj.Mesh = prepared.mesh
    import MeshGui

    MeshGui.publishStandaloneOutputs(
        str(document.Name),
        [obj],
        [prepared.artifact.file_name],
        "ImportedMeshes",
        "Imported Meshes",
        "Import meshes",
    )
    return NativeMutationDraft(
        value={"object": obj, "prepared": prepared},
        recompute_targets=(obj,),
        created=(object_identity(obj),),
    )


def verify_mesh_import(_document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    value = draft.value
    obj = value.get("object") if isinstance(value, dict) else None
    prepared = value.get("prepared") if isinstance(value, dict) else None
    if obj is None or not isinstance(prepared, PreparedMeshImport):
        raise NativeMeshError("The imported Mesh result identity was not retained.")
    mesh = getattr(obj, "Mesh", None)
    if (
        int(getattr(mesh, "CountPoints", 0) or 0) != prepared.point_count
        or int(getattr(mesh, "CountFacets", 0) or 0) != prepared.facet_count
        or str(getattr(obj, "VibeCADTimelineRole", "") or "") != "operation"
        or list(getattr(obj, "VibeCADExternalInputs", ()) or ())
        != [prepared.artifact.file_name]
    ):
        raise NativeMeshError("The imported Mesh failed its exact History postcondition.")
    return {
        "imported": mesh_object_state(obj),
        "input": prepared.artifact.summary(),
    }
