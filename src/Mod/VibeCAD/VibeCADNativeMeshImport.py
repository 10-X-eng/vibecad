# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-authorized, off-thread Mesh file preparation and exact commit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

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
    ".3mf",
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
            "Mesh files (*.stl *.ast *.bms *.obj *.off *.iv *.ply *.nas *.bdf *.3mf)"
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


def _create_mesh_imports(
    document: Any,
    prepared_values: Sequence[PreparedMeshImport],
) -> tuple[tuple[Any, ...], Any | None]:
    prepared = tuple(prepared_values)
    if not 1 <= len(prepared) <= 64 or any(
        not isinstance(value, PreparedMeshImport) for value in prepared
    ):
        raise TypeError("prepared_values must contain 1 to 64 PreparedMeshImport values")
    outputs = []
    for value in prepared:
        obj = document.addObject(
            "Mesh::Feature",
            document.getUniqueObjectName("ImportedMesh"),
        )
        if obj is None:
            raise NativeMeshError("An imported Mesh result could not be created.")
        obj.Label = Path(value.artifact.file_name).stem[:160] or "Imported Mesh"
        obj.Mesh = value.mesh
        outputs.append(obj)
    import MeshGui

    controller = MeshGui.publishStandaloneOutputs(
        str(document.Name),
        outputs,
        [value.artifact.file_name for value in prepared],
        "ImportedMeshes",
        "Imported Meshes",
        "Import meshes",
    )
    return tuple(outputs), controller


def commit_mesh_import(document: Any, prepared: PreparedMeshImport) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedMeshImport):
        raise TypeError("prepared must be a PreparedMeshImport")
    outputs, _controller = _create_mesh_imports(document, (prepared,))
    obj = outputs[0]
    return NativeMutationDraft(
        value={"object": obj, "prepared": prepared},
        recompute_targets=(obj,),
        created=(object_identity(obj),),
    )


def commit_mesh_imports(
    document: Any,
    prepared_values: Sequence[PreparedMeshImport],
) -> NativeMutationDraft:
    prepared = tuple(prepared_values)
    outputs, controller = _create_mesh_imports(document, prepared)
    created = tuple(object_identity(obj) for obj in outputs)
    if controller is not None:
        created += (object_identity(controller),)
    return NativeMutationDraft(
        value={
            "objects": outputs,
            "controller": controller,
            "prepared": prepared,
        },
        recompute_targets=outputs + ((controller,) if controller is not None else ()),
        created=created,
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


def verify_mesh_imports(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    value = draft.value
    outputs = tuple(value.get("objects") or ()) if isinstance(value, dict) else ()
    prepared = tuple(value.get("prepared") or ()) if isinstance(value, dict) else ()
    controller = value.get("controller") if isinstance(value, dict) else None
    if not outputs or len(outputs) != len(prepared):
        raise NativeMeshError("The imported Mesh result identities were not retained.")
    for obj, expected in zip(outputs, prepared):
        if (
            not isinstance(expected, PreparedMeshImport)
            or getattr(obj, "Document", None) is not document
            or document.getObject(str(getattr(obj, "Name", ""))) is not obj
            or int(getattr(getattr(obj, "Mesh", None), "CountPoints", 0) or 0)
            != expected.point_count
            or int(getattr(getattr(obj, "Mesh", None), "CountFacets", 0) or 0)
            != expected.facet_count
        ):
            raise NativeMeshError("An imported Mesh failed its exact postcondition.")
    input_names = [expected.artifact.file_name for expected in prepared]
    if len(outputs) == 1:
        if (
            controller is not None
            or str(getattr(outputs[0], "VibeCADTimelineRole", "") or "")
            != "operation"
            or list(getattr(outputs[0], "VibeCADExternalInputs", ()) or ())
            != input_names
        ):
            raise NativeMeshError("The imported Mesh failed its exact History postcondition.")
    else:
        if controller is None or getattr(controller, "Document", None) is not document:
            raise NativeMeshError("The imported Mesh group identity was not retained.")
        if str(getattr(controller, "VibeCADTimelineRole", "") or "") != "operation":
            raise NativeMeshError("The imported Mesh group was not published as one operation.")
        if list(getattr(controller, "ExternalInputs", ()) or ()) != input_names:
            raise NativeMeshError("The imported Mesh group did not retain exact input identities.")
        if set(getattr(controller, "Group", ()) or ()) != set(outputs):
            raise NativeMeshError("The imported Mesh group does not own every exact output.")
        if any(
            str(getattr(obj, "VibeCADTimelineRole", "") or "") != "resource"
            or getattr(obj, "VibeCADTimelineOwner", None) is not controller
            for obj in outputs
        ):
            raise NativeMeshError("An imported Mesh was not owned as a History resource.")
    return {
        "imported_count": len(outputs),
        "output_names": [str(obj.Name) for obj in outputs],
        "inputs": [expected.artifact.summary() for expected in prepared],
    }
