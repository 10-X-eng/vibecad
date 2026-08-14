# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-authorized background point-cloud input and output."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping

from VibeCADNativeInput import NativeInputArtifact, NativeInputRequest
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeOutput import NativeOutputRequest, publish_authorized_output
from VibeCADNativePointTargets import PreparedPointTarget
from VibeCADNativeMeshTargets import is_live
from VibeCADNativeTargets import object_identity


MAX_POINT_INPUT_BYTES = 8 * 1024 * 1024 * 1024
MAX_POINT_OUTPUT_BYTES = 16 * 1024 * 1024 * 1024
POINT_INPUT_SUFFIXES = (".asc", ".pcd", ".ply", ".e57")
POINT_OUTPUT_SUFFIXES = {"asc": ".asc", "pcd": ".pcd", "ply": ".ply"}


@dataclass(frozen=True, slots=True)
class PreparedPointImport:
    artifact: NativeInputArtifact
    data: Mapping[str, Any]


def point_input_request() -> NativeInputRequest:
    return NativeInputRequest(
        purpose="import_point_cloud",
        title="Import Point Cloud",
        allowed_suffixes=POINT_INPUT_SUFFIXES,
        name_filter="Point clouds (*.asc *.pcd *.ply *.e57)",
        maximum_bytes=MAX_POINT_INPUT_BYTES,
    )


def prepare_point_import(
    authorization: Any,
    request: NativeInputRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedPointImport:
    if cancelled():
        from VibeCADNativeBackground import NativeBackgroundCancelled

        raise NativeBackgroundCancelled()
    progress(5, "Verifying selected point-cloud file")
    artifact = authorization.claim(request)
    path = artifact.host_path_after_content_verification()
    progress(15, "Reading detached point-cloud data")
    try:
        import Points

        data = Points.readNativePointCloud(str(path))
    except Exception as exc:
        raise NativeMeshError(
            "The selected file could not be read as a supported point cloud.",
            error_code="NATIVE_POINT_CLOUD_IMPORT_INVALID",
        ) from exc
    artifact.host_path_after_content_verification()
    if cancelled():
        from VibeCADNativeBackground import NativeBackgroundCancelled

        raise NativeBackgroundCancelled()
    if not isinstance(data, Mapping) or int(data.get("point_count", 0) or 0) < 1:
        raise NativeMeshError(
            "The selected file contains no usable point data.",
            error_code="NATIVE_POINT_CLOUD_IMPORT_EMPTY",
        )
    progress(85, "Point-cloud data verified")
    return PreparedPointImport(artifact, dict(data))


def _set_attribute(obj: Any, property_type: str, name: str, values: Any) -> None:
    if not values:
        return
    obj.addProperty(property_type, name, "Point Cloud", locked=True)
    setattr(obj, name, list(values))


def commit_point_import(document: Any, prepared: PreparedPointImport) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedPointImport):
        raise TypeError("prepared must be a PreparedPointImport")
    data = prepared.data
    point_count = int(data["point_count"])
    structured = bool(data.get("structured", False))
    width = int(data.get("width", 0) or 0)
    height = int(data.get("height", 0) or 0)
    if structured and (width < 2 or height < 2 or width * height != point_count):
        raise NativeMeshError("The imported structured point grid is invalid.")
    type_id = "Points::Structured" if structured else "Points::Feature"
    obj = document.addObject(type_id, document.getUniqueObjectName("ImportedPoints"))
    if obj is None:
        raise NativeMeshError("The imported point-cloud result could not be created.")
    obj.Label = Path(prepared.artifact.file_name).stem[:160] or "Imported Points"
    obj.Points = data["points"]
    if structured:
        obj.Width = width
        obj.Height = height
    _set_attribute(obj, "Points::PropertyGreyValueList", "Intensity", data.get("intensities"))
    _set_attribute(obj, "App::PropertyColorList", "Color", data.get("colors"))
    _set_attribute(obj, "Points::PropertyNormalList", "Normal", data.get("normals"))
    import MeshGui

    MeshGui.publishStandaloneOutputs(
        str(document.Name),
        [obj],
        [prepared.artifact.file_name],
        "ImportedPoints",
        "Imported Points",
        "Import point cloud",
    )
    return NativeMutationDraft(
        value={"object": obj, "prepared": prepared},
        recompute_targets=(obj,),
        created=(object_identity(obj),),
    )


def verify_point_import(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    value = draft.value
    obj = value.get("object") if isinstance(value, dict) else None
    prepared = value.get("prepared") if isinstance(value, dict) else None
    if obj is None or not isinstance(prepared, PreparedPointImport):
        raise NativeMeshError("The imported point-cloud identity was not retained.")
    expected = int(prepared.data["point_count"])
    structured = bool(prepared.data.get("structured", False))
    expected_type = "Points::Structured" if structured else "Points::Feature"
    expected_attributes = {
        "Intensity": len(prepared.data.get("intensities") or ()),
        "Color": len(prepared.data.get("colors") or ()),
        "Normal": len(prepared.data.get("normals") or ()),
    }
    if (
        not is_live(document, obj)
        or str(obj.TypeId) != expected_type
        or int(obj.Points.CountPoints) != expected
        or not obj.isValid()
        or str(getattr(obj, "VibeCADTimelineRole", "") or "") != "operation"
        or list(getattr(obj, "VibeCADExternalInputs", ()) or ())
        != [prepared.artifact.file_name]
        or any(
            (len(getattr(obj, name)) if name in obj.PropertiesList else 0) != count
            for name, count in expected_attributes.items()
        )
        or (
            structured
            and (int(obj.Width), int(obj.Height))
            != (int(prepared.data["width"]), int(prepared.data["height"]))
        )
    ):
        raise NativeMeshError("The imported point cloud failed its History postcondition.")
    return {
        "imported": mesh_object_state(obj),
        "attributes": [
            name for name in ("Intensity", "Color", "Normal") if name in obj.PropertiesList
        ],
        "input": prepared.artifact.summary(),
    }


def point_output_request(label: str, format_name: str) -> NativeOutputRequest:
    suffix = POINT_OUTPUT_SUFFIXES[format_name]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip()).strip("._-")
    return NativeOutputRequest(
        purpose="export_point_cloud",
        title="Export Point Cloud",
        suggested_file_name=f"{(stem or 'points')[:120]}{suffix}",
        allowed_suffixes=(suffix,),
        name_filter=f"{format_name.upper()} point cloud (*{suffix})",
        maximum_bytes=MAX_POINT_OUTPUT_BYTES,
    )


def publish_point_export(
    target: PreparedPointTarget,
    format_name: str,
    request: NativeOutputRequest,
    authorization: Any,
    *,
    cancelled: Any,
    guard: Any,
    progress: Any,
) -> dict[str, Any]:
    if cancelled():
        from VibeCADNativeBackground import NativeBackgroundCancelled

        raise NativeBackgroundCancelled()
    progress(10, "Writing detached point-cloud data")

    def writer(path: str) -> None:
        import Points

        Points.writeNativePointCloud(
            target.points,
            path,
            target.placement,
            target.width,
            target.height,
            list(target.intensities),
            list(target.colors),
            list(target.normals),
        )

    def validator(path: Path) -> None:
        if cancelled():
            from VibeCADNativeBackground import NativeBackgroundCancelled

            raise NativeBackgroundCancelled()
        import Points

        check = Points.readNativePointCloud(str(path))
        if int(check.get("point_count", 0) or 0) != target.point_count:
            raise NativeMeshError("The generated point-cloud output failed validation.")

    artifact = publish_authorized_output(
        request,
        authorization,
        writer=writer,
        guard=guard,
        validator=validator,
        temporary_suffix=POINT_OUTPUT_SUFFIXES[format_name],
    )
    progress(90, "Point-cloud output verified and published")
    return {
        "output": artifact.summary(),
        "format": format_name,
        "source": {
            "object_name": target.object_name,
            "state_sha256": target.expected_state_sha256,
            "points": target.point_count,
        },
    }
