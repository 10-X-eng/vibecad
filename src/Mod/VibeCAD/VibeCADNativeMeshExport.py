# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact detached Mesh export shared by human and Native callers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeOutput import (
    NativeOutputAuthorization,
    NativeOutputError,
    NativeOutputRequest,
    publish_authorized_output,
)


MAX_MESH_EXPORT_BYTES = 16 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MeshExportFormat:
    name: str
    code: str
    suffixes: tuple[str, ...]
    suggested_suffix: str
    round_trip: bool = True


_FORMATS = (
    MeshExportFormat("binary_stl", "STL", (".stl",), ".stl"),
    MeshExportFormat("ascii_stl", "AST", (".ast", ".stl"), ".ast"),
    MeshExportFormat("binary_mesh", "BMS", (".bms",), ".bms"),
    MeshExportFormat("obj", "OBJ", (".obj",), ".obj"),
    MeshExportFormat("smf", "SMF", (".smf",), ".smf"),
    MeshExportFormat("off", "OFF", (".off",), ".off"),
    MeshExportFormat("inventor", "IV", (".iv",), ".iv"),
    MeshExportFormat("x3d", "X3D", (".x3d",), ".x3d"),
    MeshExportFormat("compressed_x3d", "X3DZ", (".x3dz",), ".x3dz"),
    MeshExportFormat("webgl_x3d", "X3DOM", (".xhtml",), ".xhtml", False),
    MeshExportFormat("ply", "PLY", (".ply",), ".ply"),
    MeshExportFormat("vrml", "VRML", (".wrl", ".vrml"), ".wrl"),
    MeshExportFormat("compressed_vrml", "WRZ", (".wrz",), ".wrz"),
    MeshExportFormat("nastran", "NAS", (".nas", ".bdf"), ".bdf"),
    MeshExportFormat("python", "PY", (".py",), ".py", False),
    MeshExportFormat("asymptote", "ASY", (".asy",), ".asy", False),
    MeshExportFormat("3mf", "3MF", (".3mf",), ".3mf"),
)
MESH_EXPORT_FORMATS = {value.name: value for value in _FORMATS}
MESH_EXPORT_FORMAT_CODES = {value.code: value for value in _FORMATS}
MESH_EXPORT_FORMAT_SUFFIXES = {
    name: value.suggested_suffix for name, value in MESH_EXPORT_FORMATS.items()
}


@dataclass(frozen=True, slots=True)
class CapturedMeshExport:
    source: Any
    object_name: str
    expected_state_sha256: str
    label: str
    topology: Mapping[str, int]
    detached_mesh: Any
    colors: tuple[tuple[float, float, float], ...]
    color_binding: str
    format: MeshExportFormat


def _suggested_name(label: str, suffix: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip()).strip("._-")
    return f"{(stem or 'mesh')[:120]}{suffix}"


def mesh_export_request(
    label: str,
    format_value: MeshExportFormat,
    *,
    selected_suffix: str | None = None,
) -> NativeOutputRequest:
    if not isinstance(format_value, MeshExportFormat):
        raise TypeError("format_value must be a MeshExportFormat")
    suffix = str(selected_suffix or format_value.suggested_suffix).casefold()
    if suffix not in format_value.suffixes:
        raise NativeMeshError(
            f"{format_value.name} output must use one of: "
            + ", ".join(format_value.suffixes)
        )
    return NativeOutputRequest(
        purpose="export_mesh",
        title="Export Mesh",
        suggested_file_name=_suggested_name(label, suffix),
        allowed_suffixes=(suffix,),
        name_filter=f"{format_value.name.replace('_', ' ').title()} (*{suffix})",
        maximum_bytes=MAX_MESH_EXPORT_BYTES,
    )


def provider_mesh_export_format(name: Any) -> MeshExportFormat:
    value = MESH_EXPORT_FORMATS.get(str(name or ""))
    if value is None or value.name not in {
        "binary_stl",
        "ascii_stl",
        "binary_mesh",
        "obj",
        "off",
        "ply",
        "nastran",
        "3mf",
    }:
        raise NativeMeshError("The requested Mesh export format is unavailable.")
    return value


def human_mesh_export_format(code: Any, path: str) -> MeshExportFormat:
    clean_code = str(code or "").strip().upper()
    suffix = Path(str(path)).suffix.casefold()
    if clean_code:
        value = MESH_EXPORT_FORMAT_CODES.get(clean_code)
        if value is None or (suffix and suffix not in value.suffixes):
            raise NativeMeshError("The output file name does not match the selected Mesh format.")
        return value
    matches = [value for value in _FORMATS if suffix in value.suffixes]
    if not matches:
        raise NativeMeshError("Choose a supported Mesh output extension.")
    return MESH_EXPORT_FORMATS["binary_stl"] if suffix == ".stl" else matches[0]


def _colors(source: Any, topology: Mapping[str, int]) -> tuple[
    tuple[tuple[float, float, float], ...], str
]:
    properties = set(getattr(source, "PropertiesList", ()) or ())
    for property_name, count_name, binding in (
        ("VertexColors", "points", "per_vertex"),
        ("FaceColors", "facets", "per_face"),
    ):
        if property_name not in properties:
            continue
        values = []
        try:
            raw_values = tuple(getattr(source, property_name))
            for raw in raw_values:
                color = tuple(float(component) for component in tuple(raw)[:3])
                if len(color) != 3:
                    raise ValueError
                values.append(color)
        except Exception:
            continue
        if len(values) == int(topology.get(count_name, 0) or 0):
            return tuple(values), binding
    return (), "overall"


def capture_mesh_export(
    document: Any,
    source: Any,
    *,
    expected_state_sha256: str | None,
    format_value: MeshExportFormat,
) -> CapturedMeshExport:
    object_name = str(getattr(source, "Name", "") or "")
    if (
        not object_name
        or getattr(source, "Document", None) is not document
        or document.getObject(object_name) is not source
        or not bool(source.isDerivedFrom("Mesh::Feature"))
    ):
        raise NativeMeshError("The exact Mesh export target is unavailable.")
    import MeshGui

    if not bool(MeshGui.isNativeMeshInputActive(source)):
        raise NativeMeshError(
            "The exact Mesh is not active at the current History position.",
            error_code="NATIVE_MESH_HISTORY_TARGET_INACTIVE",
        )
    state = mesh_object_state(source)
    current_state = str(state.get("state_sha256") or "")
    if expected_state_sha256 is not None and current_state != expected_state_sha256:
        raise NativeMeshError(
            "The exact Mesh changed after its state was read.",
            error_code="NATIVE_MESH_STATE_STALE",
            repair={
                "target": {
                    "object_name": object_name,
                    "expected_state_sha256": current_state,
                }
            },
        )
    topology = dict(state.get("topology") or {})
    if int(topology.get("facets", 0) or 0) < 1:
        raise NativeMeshError("The exact Mesh has no facets to export.")
    detached = source.Mesh.copy()
    placement = (
        source.getGlobalPlacement()
        if callable(getattr(source, "getGlobalPlacement", None))
        else source.Placement
    )
    detached.Placement = placement
    colors, binding = _colors(source, topology)
    return CapturedMeshExport(
        source=source,
        object_name=object_name,
        expected_state_sha256=current_state,
        label=str(getattr(source, "Label", "") or object_name),
        topology=topology,
        detached_mesh=detached,
        colors=colors,
        color_binding=binding,
        format=format_value,
    )


def mesh_export_source_still_exact(document: Any, captured: CapturedMeshExport) -> bool:
    if not isinstance(captured, CapturedMeshExport):
        return False
    source = captured.source
    if (
        getattr(source, "Document", None) is not document
        or document.getObject(captured.object_name) is not source
    ):
        return False
    try:
        import MeshGui

        return bool(MeshGui.isNativeMeshInputActive(source)) and str(
            mesh_object_state(source).get("state_sha256") or ""
        ) == captured.expected_state_sha256
    except Exception:
        return False


def prepare_mesh_export(
    captured: CapturedMeshExport,
    request: NativeOutputRequest,
    authorization: NativeOutputAuthorization,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
    guard: Callable[[], None],
) -> dict[str, Any]:
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(10, "Writing detached Mesh data")

    def writer(path: str) -> None:
        keywords: dict[str, Any] = {
            "Filename": path,
            "Format": captured.format.code,
            "Name": captured.label,
        }
        if captured.colors:
            keywords["Material"] = captured.colors
        captured.detached_mesh.write(**keywords)

    def validator(path: Path) -> None:
        if cancelled():
            raise NativeBackgroundCancelled()
        if not captured.format.round_trip:
            return
        import Mesh

        check = Mesh.read(str(path))
        if int(getattr(check, "CountFacets", 0) or 0) < 1:
            raise NativeMeshError("The generated Mesh output has no facets.")

    try:
        artifact = publish_authorized_output(
            request,
            authorization,
            writer=writer,
            guard=guard,
            validator=validator,
            temporary_suffix=captured.format.suggested_suffix,
        )
    except NativeOutputError:
        if cancelled():
            raise NativeBackgroundCancelled() from None
        raise
    progress(90, "Mesh output verified and published")
    return {
        "output": artifact.summary(),
        "format": captured.format.name,
        "source": {
            "object_name": captured.object_name,
            "state_sha256": captured.expected_state_sha256,
            "points": int(captured.topology.get("points", 0) or 0),
            "facets": int(captured.topology.get("facets", 0) or 0),
            "placement_applied": True,
            "color_binding": captured.color_binding,
        },
    }
