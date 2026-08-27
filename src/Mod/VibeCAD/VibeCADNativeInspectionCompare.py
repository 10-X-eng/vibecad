# SPDX-License-Identifier: LGPL-2.1-or-later

"""Background preparation and exact publication for Visual Inspection."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import tempfile
from typing import Any, Mapping

from VibeCADIsolatedMeshWorker import freecadcmd_path, run_isolated_mesh_worker
from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


JOB_SCHEMA = "vibecad-inspection-comparison-job-v1"
RESULT_SCHEMA = "vibecad-inspection-comparison-result-v1"
CACHE_SCHEMA = "vibecad-inspection-comparison-cache-v1"
MAX_SAMPLES = 2_000_000
MAX_ARTIFACT_BYTES = MAX_SAMPLES * 4
TIMEOUT_SECONDS = 86_400


class NativeInspectionCompareError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "NATIVE_INSPECTION_COMPARE_FAILED") -> None:
        self.error_code = str(error_code)
        super().__init__(str(message))

    def failure(self) -> dict[str, str]:
        return {"error_code": self.error_code, "message": str(self)}


@dataclass(frozen=True, slots=True)
class InspectionSource:
    obj: Any
    name: str
    label: str
    type_id: str
    kind: str
    geometry: Any
    placement: Any
    state_sha256: str
    visible: bool


@dataclass(frozen=True, slots=True)
class InspectionComparisonRequest:
    document_uid: str
    actual: InspectionSource
    nominals: tuple[InspectionSource, ...]
    search_radius_mm: float
    tolerance_mm: float
    thickness_mm: float
    require_complete: bool
    result_label: str
    cache_root: str
    freecadcmd: str
    child_script: str


@dataclass(frozen=True, slots=True)
class PreparedInspectionComparison:
    request: InspectionComparisonRequest
    distance_path: str
    distance_sha256: str
    distance_count: int
    summary: Mapping[str, Any]
    cache_key: str
    cache_hit: bool


def _number(value: Any, name: str, *, allow_zero: bool) -> float:
    if type(value) not in {int, float}:
        raise NativeInspectionCompareError(f"{name} must be one finite number.")
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or (not allow_zero and result == 0.0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise NativeInspectionCompareError(f"{name} must be one finite {qualifier} number.")
    if result > 1_000_000.0:
        raise NativeInspectionCompareError(f"{name} must not exceed 1000000 mm.")
    return result


def _object_name(value: Any, field: str) -> str:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeInspectionCompareError(f"{field} must contain only object_name.")
    name = str(value["object_name"])
    if not name:
        raise NativeInspectionCompareError(f"{field}.object_name is empty.")
    return name


def _kind_and_geometry(obj: Any) -> tuple[str, Any]:
    mesh = getattr(obj, "Mesh", None)
    if mesh is not None and int(getattr(mesh, "CountFacets", 0) or 0) > 0:
        return "mesh_bms", mesh
    points = getattr(obj, "Points", None)
    if points is not None and int(getattr(points, "CountPoints", 0) or 0) > 0:
        return "points_asc", points
    try:
        import Part

        shape = Part.getShape(obj, transform=True)
    except Exception as exc:
        raise NativeInspectionCompareError(
            f"Object {obj.Name!r} is not usable Part, Mesh, or Points geometry."
        ) from exc
    if shape is None or shape.isNull():
        raise NativeInspectionCompareError(
            f"Object {obj.Name!r} is not usable Part, Mesh, or Points geometry."
        )
    return "brep", shape.copy()


def _source(document: Any, document_uid: str, value: Any, field: str) -> InspectionSource:
    name = _object_name(value, field)
    obj = resolve_object(document, NativeObjectRef(document_uid, name))
    try:
        from VibeCADNativeMeshSnapshot import mesh_object_is_context_active

        active = mesh_object_is_context_active(obj)
    except Exception:
        active = True
    if not active:
        raise NativeInspectionCompareError(
            f"Object {name!r} is not active at the current History position.",
            error_code="NATIVE_INSPECTION_HISTORY_TARGET_INACTIVE",
        )
    kind, geometry = _kind_and_geometry(obj)
    state = mesh_object_state(obj)
    state_sha256 = str(state.get("state_sha256") or "")
    if len(state_sha256) != 64:
        raise NativeInspectionCompareError(
            f"Object {name!r} has no exact comparison state.",
            error_code="NATIVE_INSPECTION_STATE_UNAVAILABLE",
        )
    placement = None
    if kind != "brep":
        try:
            placement = obj.getGlobalPlacement()
        except Exception:
            placement = getattr(obj, "Placement", None)
        if placement is None:
            raise NativeInspectionCompareError(
                f"Object {name!r} has no usable placement."
            )
    return InspectionSource(
        obj=obj,
        name=name,
        label=str(getattr(obj, "Label", "") or name),
        type_id=str(getattr(obj, "TypeId", "") or ""),
        kind=kind,
        geometry=geometry,
        placement=placement,
        state_sha256=state_sha256,
        visible=bool(getattr(obj, "Visibility", False)),
    )


def capture_inspection_comparison(
    document: Any,
    document_uid: str,
    values: Mapping[str, Any],
) -> InspectionComparisonRequest:
    actual = _source(document, document_uid, values["actual"], "actual")
    raw_nominals = values["nominals"]
    if not isinstance(raw_nominals, list) or not 1 <= len(raw_nominals) <= 16:
        raise NativeInspectionCompareError("nominals must contain 1 to 16 objects.")
    nominals = tuple(
        _source(document, document_uid, value, f"nominals[{index}]")
        for index, value in enumerate(raw_nominals)
    )
    names = [source.name for source in nominals]
    if len(names) != len(set(names)):
        raise NativeInspectionCompareError("nominals must not repeat an object.")
    if actual.name in set(names):
        raise NativeInspectionCompareError("actual cannot also appear in nominals.")
    search_radius = _number(values["search_radius_mm"], "search_radius_mm", allow_zero=False)
    tolerance = _number(values["tolerance_mm"], "tolerance_mm", allow_zero=True)
    if tolerance > search_radius:
        raise NativeInspectionCompareError(
            "tolerance_mm must not exceed search_radius_mm."
        )
    require_complete = values.get("require_complete", True)
    if type(require_complete) is not bool:
        raise NativeInspectionCompareError("require_complete must be true or false.")
    thickness = _number(values.get("thickness_mm", 0.0), "thickness_mm", allow_zero=True)
    result_label = str(values.get("result_label") or f"{actual.label} Inspection").strip()
    if not 1 <= len(result_label) <= 160:
        raise NativeInspectionCompareError("result_label must contain 1 to 160 characters.")
    import FreeCAD as App

    return InspectionComparisonRequest(
        document_uid=str(document_uid),
        actual=actual,
        nominals=nominals,
        search_radius_mm=search_radius,
        tolerance_mm=tolerance,
        thickness_mm=thickness,
        require_complete=require_complete,
        result_label=result_label,
        cache_root=str(
            Path(str(App.getUserAppDataDir()))
            / "VibeCAD"
            / "cache"
            / CACHE_SCHEMA
        ),
        freecadcmd=str(freecadcmd_path()),
        child_script=str(Path(__file__).resolve().with_name("VibeCADInspectionComparisonChild.py")),
    )


def source_still_exact(document: Any, source: InspectionSource) -> bool:
    try:
        return (
            document.getObject(source.name) is source.obj
            and mesh_object_state(source.obj).get("state_sha256") == source.state_sha256
        )
    except Exception:
        return False


def comparison_still_exact(document: Any, request: InspectionComparisonRequest) -> bool:
    return all(
        source_still_exact(document, source)
        for source in (request.actual, *request.nominals)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_key(request: InspectionComparisonRequest) -> str:
    payload = {
        "schema": CACHE_SCHEMA,
        "actual": request.actual.state_sha256,
        "nominals": [source.state_sha256 for source in request.nominals],
        "search_radius_mm": request.search_radius_mm,
        "tolerance_mm": request.tolerance_mm,
        "require_complete": request.require_complete,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_paths(request: InspectionComparisonRequest, key: str) -> tuple[Path, Path]:
    directory = Path(request.cache_root) / key[:2] / key
    return directory / "distances.f32", directory / "metadata.json"


def _cached_comparison(
    request: InspectionComparisonRequest,
    key: str,
) -> PreparedInspectionComparison | None:
    distance_path, metadata_path = _cache_paths(request, key)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        size = distance_path.stat().st_size
    except (OSError, ValueError):
        return None
    count = metadata.get("distance_count") if isinstance(metadata, dict) else None
    summary = metadata.get("summary") if isinstance(metadata, dict) else None
    if (
        metadata.get("schema") != CACHE_SCHEMA
        or metadata.get("cache_key") != key
        or type(count) is not int
        or not 1 <= count <= MAX_SAMPLES
        or size != count * 4
        or size > MAX_ARTIFACT_BYTES
        or metadata.get("distance_sha256") != _sha256(distance_path)
        or not isinstance(summary, dict)
    ):
        return None
    return PreparedInspectionComparison(
        request=request,
        distance_path=str(distance_path),
        distance_sha256=str(metadata["distance_sha256"]),
        distance_count=count,
        summary=dict(summary),
        cache_key=key,
        cache_hit=True,
    )


def _publish_cache(
    request: InspectionComparisonRequest,
    key: str,
    source: Path,
    *,
    digest: str,
    count: int,
    summary: Mapping[str, Any],
) -> PreparedInspectionComparison:
    distance_path, metadata_path = _cache_paths(request, key)
    distance_path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    distance_temp = distance_path.with_name(
        f".{distance_path.name}.{os.getpid()}.{token}.tmp"
    )
    metadata_temp = metadata_path.with_name(
        f".{metadata_path.name}.{os.getpid()}.{token}.tmp"
    )
    metadata = {
        "schema": CACHE_SCHEMA,
        "cache_key": key,
        "distance_sha256": digest,
        "distance_count": count,
        "summary": dict(summary),
    }
    try:
        with source.open("rb") as source_stream, distance_temp.open("wb") as target_stream:
            shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
            target_stream.flush()
            os.fsync(target_stream.fileno())
        os.replace(distance_temp, distance_path)
        with metadata_temp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(metadata, ensure_ascii=True, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(metadata_temp, metadata_path)
    finally:
        distance_temp.unlink(missing_ok=True)
        metadata_temp.unlink(missing_ok=True)
    return PreparedInspectionComparison(
        request=request,
        distance_path=str(distance_path),
        distance_sha256=digest,
        distance_count=count,
        summary=dict(summary),
        cache_key=key,
        cache_hit=False,
    )


def _write_source(source: InspectionSource, path: Path) -> str:
    if source.kind == "brep":
        source.geometry.exportBrep(str(path))
        return "brep_sha256"
    geometry = source.geometry.copy()
    placement = source.placement
    matrix = placement.toMatrix()
    try:
        geometry.Placement = type(placement)()
    except Exception:
        pass
    if source.kind == "mesh_bms":
        geometry.transform(matrix)
    else:
        geometry.transformGeometry(matrix)
    geometry.write(str(path))
    return "mesh_sha256" if source.kind == "mesh_bms" else "artifact_sha256"


def run_inspection_comparison(
    request: InspectionComparisonRequest,
    *,
    cancelled: Any,
    progress: Any,
) -> PreparedInspectionComparison:
    if not isinstance(request, InspectionComparisonRequest):
        raise TypeError("request must be an InspectionComparisonRequest")
    if cancelled():
        raise NativeBackgroundCancelled()
    key = _cache_key(request)
    cached = _cached_comparison(request, key)
    if cached is not None:
        progress(90, "Reusing verified signed deviations")
        return cached
    with tempfile.TemporaryDirectory(prefix="vibecad-inspection-compare-") as directory:
        root = Path(directory)
        inputs = root / "inputs"
        inputs.mkdir()
        descriptors = []
        sources = (request.actual, *request.nominals)
        for index, source in enumerate(sources):
            if cancelled():
                raise NativeBackgroundCancelled()
            suffix = {"brep": ".brep", "mesh_bms": ".bms", "points_asc": ".asc"}[
                source.kind
            ]
            path = inputs / f"source-{index:03d}{suffix}"
            progress(2 + int(16 * index / len(sources)), "Snapshotting comparison geometry")
            digest_field = _write_source(source, path)
            if not path.is_file() or path.stat().st_size < 1:
                raise NativeInspectionCompareError(
                    f"Object {source.name!r} produced no comparison artifact."
                )
            digest = _sha256(path)
            descriptors.append(
                {
                    "document_uid": request.document_uid,
                    "object_name": source.name,
                    "label": source.label,
                    "type_id": source.type_id,
                    "artifact_kind": source.kind,
                    "artifact_path": str(path.relative_to(root)),
                    digest_field: digest,
                }
            )
        request_path = root / "request.json"
        result_path = root / "result.json"
        request_path.write_text(
            json.dumps(
                {
                    "schema": JOB_SCHEMA,
                    "workspace": str(root),
                    "result_path": str(result_path),
                    "document_references": descriptors,
                    "actual": {"document_uid": descriptors[0]["document_uid"], "object_name": request.actual.name},
                    "nominals": [
                        {"document_uid": item["document_uid"], "object_name": item["object_name"]}
                        for item in descriptors[1:]
                    ],
                    "search_radius_mm": request.search_radius_mm,
                    "tolerance_mm": request.tolerance_mm,
                    "require_complete": request.require_complete,
                    "result_label": request.result_label,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        progress(20, "Computing signed deviations")
        result = run_isolated_mesh_worker(
            freecadcmd=request.freecadcmd,
            child_script=request.child_script,
            request_path=request_path,
            result_path=result_path,
            expected_schema=RESULT_SCHEMA,
            cancelled=cancelled,
            timeout_seconds=TIMEOUT_SECONDS,
            failure_code="NATIVE_INSPECTION_COMPARE_FAILED",
        )
        relative = Path(str(result.get("distance_path") or ""))
        distance_path = (root / relative).resolve()
        if root.resolve() not in distance_path.parents or not distance_path.is_file():
            raise NativeInspectionCompareError("The comparison returned no distance artifact.")
        count = int(result.get("distance_count", 0) or 0)
        if not 1 <= count <= MAX_SAMPLES or distance_path.stat().st_size != count * 4:
            raise NativeInspectionCompareError("The comparison distance artifact is invalid.")
        digest = _sha256(distance_path)
        if digest != str(result.get("distance_sha256") or ""):
            raise NativeInspectionCompareError("The comparison distance artifact changed.")
        progress(90, "Authenticating signed deviations")
        return _publish_cache(
            request,
            key,
            distance_path,
            digest=digest,
            count=count,
            summary=dict(result.get("summary") or {}),
        )


def discard_prepared_comparison(prepared: Any) -> None:
    del prepared


def _property(obj: Any, type_id: str, name: str, value: Any, description: str) -> None:
    if name not in set(getattr(obj, "PropertiesList", ()) or ()):
        obj.addProperty(type_id, name, "Inspection", description)
    setattr(obj, name, value)


def _create_inspection_feature(
    document: Any,
    group: Any,
    prepared: PreparedInspectionComparison,
) -> Any:
    request = prepared.request
    path = Path(prepared.distance_path)
    if _sha256(path) != prepared.distance_sha256:
        raise NativeInspectionCompareError("The prepared comparison distances changed.")
    distances = array("f")
    with path.open("rb") as stream:
        distances.fromfile(stream, prepared.distance_count)
        if stream.read(1):
            raise NativeInspectionCompareError("The prepared comparison distances are oversized.")
    feature = document.addObject(
        "Inspection::Feature",
        document.getUniqueObjectName(f"{request.actual.name}_Inspect"),
    )
    if feature is None:
        raise NativeInspectionCompareError("The Inspection result could not be created.")
    feature.Label = request.result_label
    for name in ("Actual", "Nominals", "SearchRadius", "Thickness"):
        feature.setPropertyStatus(name, "NoRecompute")
    feature.Actual = request.actual.obj
    feature.Nominals = [source.obj for source in request.nominals]
    feature.SearchRadius = request.search_radius_mm
    feature.Thickness = request.thickness_mm
    feature.Distances = distances.tolist()
    summary = prepared.summary
    _property(feature, "App::PropertyFloat", "VibeCADTolerance", request.tolerance_mm, "Accepted signed deviation in millimetres.")
    _property(feature, "App::PropertyBool", "VibeCADRequireComplete", request.require_complete, "Whether every actual sample has a nominal result.")
    _property(feature, "App::PropertyBool", "VibeCADPassed", bool(summary.get("passed")), "Comparison verdict.")
    _property(feature, "App::PropertyInteger", "VibeCADSampleCount", prepared.distance_count, "Actual sample count.")
    _property(feature, "App::PropertyInteger", "VibeCADMeasuredCount", int(summary.get("measured_count", 0) or 0), "Measured sample count.")
    _property(feature, "App::PropertyInteger", "VibeCADUnmeasuredCount", int(summary.get("unmeasured_count", 0) or 0), "Unmeasured sample count.")
    _property(feature, "App::PropertyFloat", "VibeCADRMSDistance", float(summary.get("rms") or 0.0), "Root-mean-square signed deviation in millimetres.")
    _property(feature, "App::PropertyFloat", "VibeCADAbsoluteMaximumDistance", float(summary.get("absolute_maximum") or 0.0), "Largest absolute signed deviation in millimetres.")
    feature.purgeTouched()
    feature.freeze()
    feature.purgeTouched()
    group.addObject(feature)
    return feature


def commit_inspection_comparisons(
    document: Any,
    prepared_values: tuple[PreparedInspectionComparison, ...],
) -> NativeMutationDraft:
    if not prepared_values or not all(
        isinstance(value, PreparedInspectionComparison) for value in prepared_values
    ):
        raise TypeError("prepared_values must contain prepared Inspection comparisons")
    if not all(
        comparison_still_exact(document, value.request) for value in prepared_values
    ):
        raise NativeInspectionCompareError(
            "Comparison geometry changed while deviations were computed.",
            error_code="NATIVE_INSPECTION_STATE_STALE",
        )
    import Inspection

    del Inspection
    group = document.addObject("Inspection::Group", document.getUniqueObjectName("Inspection"))
    if group is None:
        raise NativeInspectionCompareError("The Inspection result could not be created.")
    group.Label = (
        f"{prepared_values[0].request.result_label} Results"
        if len(prepared_values) == 1
        else "Visual Inspection Results"
    )
    features = tuple(
        _create_inspection_feature(document, group, prepared)
        for prepared in prepared_values
    )
    sources = tuple(
        dict.fromkeys(
            source.obj
            for prepared in prepared_values
            for source in (prepared.request.actual, *prepared.request.nominals)
            if source.visible
        )
    )
    document.publishProvisionalTimelineOperationBlock(
        group,
        features,
        tuple(group for _feature in features),
    )
    for source in sources:
        source.Visibility = False
    return NativeMutationDraft(
        value={
            "prepared_values": prepared_values,
            "group": group,
            "features": features,
        },
        created=tuple(object_identity(obj) for obj in (group, *features)),
        replaced=tuple(object_identity(source) for source in sources),
    )


def commit_inspection_comparison(
    document: Any,
    prepared: PreparedInspectionComparison,
) -> NativeMutationDraft:
    draft = commit_inspection_comparisons(document, (prepared,))
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "group": draft.value["group"],
            "feature": draft.value["features"][0],
        },
        created=draft.created,
        changed=draft.changed,
        deleted=draft.deleted,
        replaced=draft.replaced,
        recompute_targets=draft.recompute_targets,
        after_recompute=draft.after_recompute,
    )


def _verify_feature(
    document: Any,
    group: Any,
    feature: Any,
    prepared: PreparedInspectionComparison,
) -> dict[str, Any]:
    request = prepared.request
    observed = array("f", (float(value) for value in feature.Distances))
    if (
        document.getObject(str(feature.Name)) is not feature
        or feature.Actual is not request.actual.obj
        or tuple(feature.Nominals) != tuple(source.obj for source in request.nominals)
        or float(feature.SearchRadius) != request.search_radius_mm
        or float(feature.Thickness) != request.thickness_mm
        or len(observed) != prepared.distance_count
        or hashlib.sha256(observed.tobytes()).hexdigest() != prepared.distance_sha256
        or not bool(feature.isFrozen())
        or str(getattr(feature, "VibeCADTimelineRole", "") or "") != "resource"
        or getattr(feature, "VibeCADTimelineOwner", None) is not group
        or not comparison_still_exact(document, request)
    ):
        raise NativeInspectionCompareError("The Inspection result failed exact verification.")
    return {
        "operation": "compare",
        "actual": object_reference(request.actual.obj),
        "nominals": [object_reference(source.obj) for source in request.nominals],
        "result": object_reference(feature),
        "group": object_reference(group),
        "representation": "inspection_result",
        "search_radius_mm": request.search_radius_mm,
        "tolerance_mm": request.tolerance_mm,
        "cache_reused": bool(prepared.cache_hit),
        "summary": dict(prepared.summary),
    }


def verify_inspection_comparisons(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared_values = tuple(draft.value["prepared_values"])
    group = draft.value["group"]
    features = tuple(draft.value["features"])
    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", ()) or ()) if timeline else []
    if (
        document.getObject(str(group.Name)) is not group
        or len(features) != len(prepared_values)
        or tuple(group.Group) != features
        or operations.count(group) != 1
        or str(getattr(group, "VibeCADTimelineRole", "") or "") != "operation"
    ):
        raise NativeInspectionCompareError("The Inspection result failed exact verification.")
    return {
        "operation": "compare",
        "group": object_reference(group),
        "representation": "inspection_result",
        "comparisons": [
            _verify_feature(document, group, feature, prepared)
            for feature, prepared in zip(features, prepared_values, strict=True)
        ],
    }


def verify_inspection_comparison(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared = draft.value["prepared"]
    group = draft.value["group"]
    feature = draft.value["feature"]
    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", ()) or ()) if timeline else []
    if (
        document.getObject(str(group.Name)) is not group
        or tuple(group.Group) != (feature,)
        or operations.count(group) != 1
        or str(getattr(group, "VibeCADTimelineRole", "") or "") != "operation"
    ):
        raise NativeInspectionCompareError("The Inspection result failed exact verification.")
    return _verify_feature(document, group, feature, prepared)
