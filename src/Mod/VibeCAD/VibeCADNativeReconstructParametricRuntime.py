# SPDX-License-Identifier: LGPL-2.1-or-later

"""Human-authorized, background preparation for printables reverse IR."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeBackground import NativeBackgroundCancelled, NativeBackgroundError
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeInput import NativeInputError, NativeInputRequest
from VibeCADNativeMeshErrors import NativeMeshError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeOutput import (
    NativeOutputBundleItem,
    NativeOutputError,
    NativeOutputRequest,
    publish_authorized_output_bundle,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket
from VibeCADNativeTargets import object_identity

from VibeCADNativeMeshReconstructParametricSchema import (
    MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
)

MAX_PRINTABLES_IR_BYTES = 16 * 1024 * 1024
MAX_PROFILE_POINTS = 10_000
MAX_HOLES = 512
MAX_RECONSTRUCTION_OUTPUT_BYTES = 2 * 1024 * 1024 * 1024
_VARIANTS = {
    "from_printables_ir": frozenset({"ir_path", "result_label", "step_path", "stl_path"}),
}


@dataclass(frozen=True, slots=True)
class PreparedPrintablesReconstruction:
    plan: dict[str, Any]
    input_summary: dict[str, Any]
    shape: Any
    staged_outputs: tuple[tuple[str, Path], ...] = ()
    _temporary_directory: Any | None = None

    def cleanup(self) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()


def _error(message: str, code: str = "NATIVE_RECONSTRUCT_IR_INVALID") -> NativeMeshError:
    return NativeMeshError(message, error_code=code)


def _finite(value: Any, field: str, *, positive: bool = False) -> float:
    if type(value) not in {int, float}:
        raise _error(f"{field} must be one finite number.")
    result = float(value)
    if not math.isfinite(result) or abs(result) > 1_000_000_000.0:
        raise _error(f"{field} must be one bounded finite number.")
    if positive and result <= 0.0:
        raise _error(f"{field} must be positive.")
    return result


def _point(value: Any, field: str, size: int) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise _error(f"{field} must contain exactly {size} coordinates.")
    return tuple(_finite(item, f"{field}[{index}]") for index, item in enumerate(value))


def _unit(value: Any, field: str) -> tuple[float, float, float]:
    vector = _point(value, field, 3)
    length = math.sqrt(sum(item * item for item in vector))
    if length <= 1.0e-12:
        raise _error(f"{field} must be a non-zero direction.")
    return tuple(item / length for item in vector)  # type: ignore[return-value]


def _dot(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    return sum(left * right for left, right in zip(first, second, strict=True))


def _same_point(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return all(abs(left - right) <= 1.0e-9 for left, right in zip(first, second, strict=True))


def _outer_uv(sketch: Mapping[str, Any]) -> list[tuple[float, float]]:
    profiles = sketch.get("profiles")
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 256:
        raise _error("IR sketch profiles must contain 1 through 256 profiles.")
    outer_profiles = [
        profile
        for profile in profiles
        if isinstance(profile, Mapping)
        and profile.get("role") != "hole"
        and not str(profile.get("id") or "").startswith("hole")
    ]
    if len(outer_profiles) != 1:
        raise _error("IR sketch must contain exactly one supported outer profile.")
    entities = outer_profiles[0].get("entities")
    if not isinstance(entities, list) or not 1 <= len(entities) <= MAX_PROFILE_POINTS:
        raise _error("IR outer profile must contain bounded line or polyline entities.")

    points: list[tuple[float, float]] = []
    for entity_index, entity in enumerate(entities):
        if not isinstance(entity, Mapping):
            raise _error("IR outer profile entities must be objects.")
        entity_type = str(entity.get("type") or "")
        if entity_type == "line":
            segment = [
                _point(entity.get("a_mm"), f"entities[{entity_index}].a_mm", 2),
                _point(entity.get("b_mm"), f"entities[{entity_index}].b_mm", 2),
            ]
        elif entity_type == "polyline":
            raw_points = entity.get("points_mm")
            if not isinstance(raw_points, list) or not 2 <= len(raw_points) <= MAX_PROFILE_POINTS:
                raise _error("IR polyline must contain 2 through 10000 points.")
            segment = [
                _point(value, f"entities[{entity_index}].points_mm[{index}]", 2)
                for index, value in enumerate(raw_points)
            ]
        else:
            raise _error("IR supports only line and polyline outer-profile entities.")
        if points:
            if not _same_point(points[-1], segment[0]):
                raise _error("IR outer profile entities must form one ordered chain.")
            points.extend(segment[1:])
        else:
            points.extend(segment)
        if len(points) > MAX_PROFILE_POINTS:
            raise _error("IR outer profile exceeds the 10000-point bound.")

    if len(points) < 4 or not _same_point(points[0], points[-1]):
        raise _error("IR requires one closed outer profile.")
    points.pop()
    if len(points) < 3 or len(set(points)) < 3:
        raise _error("IR outer profile must contain at least three distinct points.")
    twice_area = sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:] + points[:1], strict=True)
    )
    if abs(twice_area) <= 1.0e-12:
        raise _error("IR outer profile must enclose a non-zero area.")
    return points


def printables_ir_plan(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Validate schema-version-1 IR and return one bounded reconstruction plan."""

    if not isinstance(ir, Mapping):
        raise _error("printables IR must be a JSON object")
    if ir.get("schema_version") != 1:
        raise _error("printables IR schema_version must be 1")
    if ir.get("units") != "mm":
        raise _error("printables IR units must be mm")
    classification = str(ir.get("class") or "")
    if classification not in {"parametric", "analytic", "organic", "failed"}:
        raise _error("printables IR class is not a known reconstruction class")
    if classification == "failed":
        raise _error(
            "IR class is failed; no reconstruction can be created.",
            "NATIVE_RECONSTRUCT_FAILED_CLASS",
        )
    forbidden = ir.get("forbidden")
    if not isinstance(forbidden, Mapping) or forbidden.get("triangle_wrapped_step") is not True:
        raise _error("printables IR must forbid triangle-wrapped STEP")
    if type(ir.get("expected_shells")) is not int or ir.get("expected_shells") != 1:
        raise _error("This reconstruction requires exactly one solid.")

    raw_sketches = ir.get("sketches")
    if not isinstance(raw_sketches, list) or not 1 <= len(raw_sketches) <= 64:
        raise _error("IR sketches must contain 1 through 64 sketch definitions.")
    sketches: dict[str, Mapping[str, Any]] = {}
    for sketch in raw_sketches:
        if not isinstance(sketch, Mapping):
            raise _error("Every IR sketch must be an object.")
        identifier = str(sketch.get("id") or "").strip()
        if not identifier or len(identifier) > 128 or identifier in sketches:
            raise _error("Every IR sketch needs one unique bounded id.")
        sketches[identifier] = sketch

    raw_features = ir.get("features")
    if not isinstance(raw_features, list) or not 1 <= len(raw_features) <= MAX_HOLES + 1:
        raise _error("IR features must contain one extrude and at most 512 holes.")
    unsupported = [
        str(feature.get("type") or "")
        for feature in raw_features
        if not isinstance(feature, Mapping)
        or str(feature.get("type") or "") not in {"extrude", "hole"}
    ]
    if unsupported:
        raise _error("IR contains operations outside the supported extrude and hole subset.")
    extrudes = [
        feature
        for feature in raw_features
        if feature.get("type") == "extrude" and feature.get("op") == "add"
    ]
    if len(extrudes) != 1 or any(
        feature.get("type") == "extrude" and feature.get("op") != "add" for feature in raw_features
    ):
        raise _error("IR requires exactly one additive extrude feature.")
    extrude = extrudes[0]
    sketch_id = str(extrude.get("sketch") or "")
    sketch = sketches.get(sketch_id)
    if sketch is None:
        raise _error("IR extrude sketch is missing", "NATIVE_RECONSTRUCT_NO_SKETCH")

    origin = _point(sketch.get("origin_mm"), "sketch.origin_mm", 3)
    normal = _unit(sketch.get("normal"), "sketch.normal")
    x_axis = _unit(sketch.get("x_axis"), "sketch.x_axis")
    y_axis = _unit(sketch.get("y_axis"), "sketch.y_axis")
    if abs(_dot(x_axis, y_axis)) > 1.0e-6:
        raise _error("IR sketch x_axis and y_axis must be orthogonal.")
    if abs(_dot(x_axis, normal)) > 1.0e-6 or abs(_dot(y_axis, normal)) > 1.0e-6:
        raise _error("IR sketch axes must lie on the sketch plane.")
    direction = _unit(extrude.get("direction") or normal, "extrude.direction")
    if abs(abs(_dot(direction, normal)) - 1.0) > 1.0e-6:
        raise _error("IR extrude direction must be normal to the sketch plane.")
    depth = _finite(extrude.get("depth_mm"), "extrude.depth_mm", positive=True)
    outer = _outer_uv(sketch)

    holes = []
    for index, feature in enumerate(raw_features):
        if feature.get("type") != "hole":
            continue
        diameter = _finite(
            feature.get("diameter_mm"),
            f"holes[{index}].diameter_mm",
            positive=True,
        )
        uv = feature.get("uv_mm")
        origin_mm = feature.get("origin_mm")
        if uv is None and origin_mm is None:
            raise _error("Every IR hole needs uv_mm or origin_mm.")
        holes.append(
            {
                "diameter_mm": diameter,
                "uv_mm": _point(uv, f"holes[{index}].uv_mm", 2) if uv is not None else None,
                "origin_mm": (
                    _point(origin_mm, f"holes[{index}].origin_mm", 3)
                    if origin_mm is not None
                    else None
                ),
            }
        )
    if len(holes) > MAX_HOLES:
        raise _error("IR exceeds the 512-hole reconstruction bound.")

    body = str(ir.get("body") or "Reconstructed Part").strip()
    if not body or len(body) > 160:
        raise _error("IR body label must contain 1 through 160 characters.")
    input_triangles = ir.get("input_triangles", 0)
    if type(input_triangles) is not int or not 0 <= input_triangles <= 2_147_483_647:
        raise _error("input_triangles must be a bounded non-negative integer.")
    return {
        "class": classification,
        "body": body,
        "expected_shells": 1,
        "origin_mm": origin,
        "normal": normal,
        "x_axis": x_axis,
        "y_axis": y_axis,
        "direction": direction,
        "depth_mm": depth,
        "outer_uv": outer,
        "holes": holes,
        "extrude": {
            "type": "extrude",
            "op": "add",
            "sketch": sketch_id,
            "depth_mm": depth,
            "direction": direction,
        },
        "sketches": {sketch_id: dict(sketch)},
        "input_triangles": input_triangles,
    }


def _decode_printables_ir(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_PRINTABLES_IR_BYTES:
        raise _error("printables IR exceeds its bounded input size.")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("printables IR is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise _error("printables IR must be a JSON object")
    return value


def load_printables_ir(path: str | Path) -> dict[str, Any]:
    """Compatibility loader for trusted host callers; provider calls use authorization."""

    source = Path(path)
    try:
        if not source.is_file() or source.stat().st_size > MAX_PRINTABLES_IR_BYTES:
            raise _error("printables IR is unavailable or exceeds its bounded input size.")
        return _decode_printables_ir(source.read_bytes())
    except NativeMeshError:
        raise
    except OSError as exc:
        raise _error("printables IR could not be read.") from exc


def printables_ir_input_request(path_hint: str = "") -> NativeInputRequest:
    file_name = str(path_hint or "").replace("\\", "/").rsplit("/", 1)[-1]
    visible_name = "".join(
        character if 32 <= ord(character) < 127 else "_" for character in file_name[:120]
    )
    suffix = f" ({visible_name})" if visible_name else ""
    return NativeInputRequest(
        purpose="reconstruct_parametric_from_printables_ir",
        title=f"Select Printables Reverse IR{suffix}",
        allowed_suffixes=(".json",),
        name_filter="Printables reverse IR (*.json)",
        maximum_bytes=MAX_PRINTABLES_IR_BYTES,
    )


def prepare_printables_reconstruction(
    authorization: Any,
    *,
    cancelled: Any,
    progress: Any,
    request: NativeInputRequest | None = None,
    output_kinds: tuple[str, ...] = (),
) -> PreparedPrintablesReconstruction:
    selected_request = request or printables_ir_input_request()
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(10, "Verifying selected printables reverse IR")
    artifact = authorization.claim(selected_request)
    payload = artifact.read_bytes(maximum_bytes=MAX_PRINTABLES_IR_BYTES)
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(50, "Validating bounded reconstruction features")
    plan = printables_ir_plan(_decode_printables_ir(payload))
    if any(kind not in {"step", "stl"} for kind in output_kinds):
        raise TypeError("output_kinds may contain only step and stl")
    if len(output_kinds) != len(set(output_kinds)):
        raise TypeError("output_kinds must not repeat an output format")
    progress(65, "Building detached reconstructed solid")
    shape = _build_solid(plan)
    temporary_directory = None
    staged_outputs: list[tuple[str, Path]] = []
    try:
        if output_kinds:
            temporary_directory = TemporaryDirectory(prefix="vibecad-reconstruct-")
            root = Path(temporary_directory.name)
            progress(75, "Preparing detached reconstructed output")
            for kind in output_kinds:
                path = root / ("reconstructed.step" if kind == "step" else "reconstructed.stl")
                if kind == "step":
                    shape.exportStep(str(path))
                else:
                    import Mesh

                    mesh = Mesh.Mesh(shape.tessellate(0.1))
                    mesh.write(str(path))
                if not path.is_file() or path.stat().st_size <= 0:
                    raise _error(
                        f"The detached {kind.upper()} output was not generated.",
                        "NATIVE_RECONSTRUCT_OUTPUT_FAILED",
                    )
                staged_outputs.append((kind, path))
    except Exception:
        if temporary_directory is not None:
            temporary_directory.cleanup()
        raise
    progress(90, "Reconstruction plan ready")
    return PreparedPrintablesReconstruction(
        plan,
        dict(artifact.summary()),
        shape,
        tuple(staged_outputs),
        temporary_directory,
    )


def _safe_suggested_name(raw: Any, fallback: str, suffixes: tuple[str, ...]) -> str:
    value = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not value or Path(value).suffix.casefold() not in suffixes:
        value = fallback
    value = "".join(
        "_" if character in '<>:"/\\|?*' or ord(character) < 32 else character
        for character in value
    ).strip(" .")
    return value[:240] or fallback


def reconstruction_output_requests(
    values: Mapping[str, Any],
    *,
    label: str,
) -> tuple[tuple[str, NativeOutputRequest], ...]:
    base_with_suffix = _safe_suggested_name(
        f"{label}.unused",
        "Reconstructed-Part.unused",
        (".unused",),
    )
    base = Path(base_with_suffix).stem
    result = []
    if values.get("step_path"):
        result.append(
            (
                "step",
                NativeOutputRequest(
                    purpose="export_reconstructed_step",
                    title="Export Reconstructed STEP",
                    suggested_file_name=_safe_suggested_name(
                        values.get("step_path"), f"{base}.step", (".step", ".stp")
                    ),
                    allowed_suffixes=(".step", ".stp"),
                    name_filter="STEP files (*.step *.stp)",
                    maximum_bytes=MAX_RECONSTRUCTION_OUTPUT_BYTES,
                ),
            )
        )
    if values.get("stl_path"):
        result.append(
            (
                "stl",
                NativeOutputRequest(
                    purpose="export_reconstructed_stl",
                    title="Export Reconstructed STL",
                    suggested_file_name=_safe_suggested_name(
                        values.get("stl_path"), f"{base}.stl", (".stl",)
                    ),
                    allowed_suffixes=(".stl",),
                    name_filter="STL files (*.stl)",
                    maximum_bytes=MAX_RECONSTRUCTION_OUTPUT_BYTES,
                ),
            )
        )
    return tuple(result)


def _build_solid(plan: Mapping[str, Any]) -> Any:
    import FreeCAD as App
    import Part

    origin = App.Vector(*plan["origin_mm"])
    x_axis = App.Vector(*plan["x_axis"])
    y_axis = App.Vector(*plan["y_axis"])
    direction = App.Vector(*plan["direction"])
    points = [origin + x_axis * float(u) + y_axis * float(v) for u, v in plan["outer_uv"]]
    points.append(points[0])
    solid = Part.Face(Part.makePolygon(points)).extrude(direction * float(plan["depth_mm"]))
    for hole in plan["holes"]:
        uv = hole["uv_mm"]
        if uv is None:
            relative = App.Vector(*hole["origin_mm"]) - origin
            uv = (relative.dot(x_axis), relative.dot(y_axis))
        hole_origin = origin + x_axis * float(uv[0]) + y_axis * float(uv[1]) - direction
        cutter = Part.makeCylinder(
            float(hole["diameter_mm"]) / 2.0,
            float(plan["depth_mm"]) + 2.0,
            hole_origin,
            direction,
        )
        previous_volume = float(getattr(solid, "Volume", 0.0) or 0.0)
        solid = solid.cut(cutter)
        next_volume = float(getattr(solid, "Volume", 0.0) or 0.0)
        if not next_volume < previous_volume - 1.0e-9:
            raise _error(
                "An IR hole does not remove material from the reconstructed solid.",
                "NATIVE_RECONSTRUCT_HOLE_OUTSIDE",
            )
    solids = tuple(getattr(solid, "Solids", ()) or ())
    if len(solids) != 1:
        raise _error(
            f"Reconstruction produced {len(solids)} solids instead of exactly one.",
            "NATIVE_RECONSTRUCT_SHELL_COUNT",
        )
    result = solids[0]
    if not bool(result.isValid()):
        raise _error(
            "The detached reconstruction produced invalid B-rep geometry.",
            "NATIVE_RECONSTRUCT_INVALID_SHAPE",
        )
    return result


def _valid_reconstruction_object(document: Any, feature: Any) -> bool:
    name = str(getattr(feature, "Name", "") or "")
    shape = getattr(feature, "Shape", None)
    return bool(
        name
        and getattr(feature, "Document", None) is document
        and document.getObject(name) is feature
        and len(tuple(getattr(shape, "Solids", ()) or ())) == 1
        and not bool(shape.isNull())
    )


def _reconstruction_draft(
    document: Any,
    prepared: PreparedPrintablesReconstruction,
    *,
    label: str,
    outputs: tuple[tuple[str, NativeOutputRequest, Any], ...],
    guard: Any,
) -> NativeMutationDraft:
    plan = prepared.plan
    shape = prepared.shape
    if shape is None:
        raise TypeError("prepared reconstruction has no detached shape")
    feature = document.addObject(
        "Part::Feature",
        document.getUniqueObjectName("ReconstructedPart"),
    )
    if feature is None:
        raise _error(
            "The reconstructed B-rep feature could not be created.",
            "NATIVE_RECONSTRUCT_CREATE_FAILED",
        )
    feature.Label = label
    feature.Shape = shape
    for property_name, property_label, value in (
        ("VibeCADReconstructionClass", "Reconstruction class", plan["class"]),
        ("VibeCADReconstructionSource", "Source file", prepared.input_summary["file_name"]),
    ):
        if property_name not in tuple(getattr(feature, "PropertiesList", ()) or ()):
            feature.addProperty("App::PropertyString", property_name, "VibeCAD", property_label)
        setattr(feature, property_name, str(value))

    state: dict[str, Any] = {
        "feature": feature,
        "prepared": prepared,
        "artifacts": (),
    }

    def after_recompute(_document: Any) -> None:
        if not _valid_reconstruction_object(document, feature):
            raise _error(
                "The reconstructed solid failed its exact postcondition.",
                "NATIVE_RECONSTRUCT_POSTCONDITION",
            )
        if not outputs:
            return
        staged = dict(prepared.staged_outputs)
        if set(staged) != {kind for kind, _request, _authorization in outputs}:
            raise _error(
                "Detached reconstruction outputs do not match the authorized set.",
                "NATIVE_RECONSTRUCT_OUTPUT_FAILED",
            )
        items = []
        for kind, request, authorization in outputs:
            source = staged[kind]
            writer = lambda path, current=source: shutil.copyfile(current, path)
            items.append(
                NativeOutputBundleItem(
                    request=request,
                    authorization=authorization,
                    writer=writer,
                    temporary_suffix=".step" if kind == "step" else ".stl",
                )
            )
        state["artifacts"] = publish_authorized_output_bundle(
            tuple(items),
            guard=guard,
        )

    return NativeMutationDraft(
        value=state,
        recompute_targets=(feature,),
        created=(object_identity(feature),),
        after_recompute=after_recompute,
    )


def _verify_reconstruction(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    value = draft.value
    feature = value.get("feature") if isinstance(value, Mapping) else None
    prepared = value.get("prepared") if isinstance(value, Mapping) else None
    if (
        feature is None
        or not isinstance(prepared, PreparedPrintablesReconstruction)
        or not _valid_reconstruction_object(document, feature)
    ):
        raise _error(
            "The reconstructed solid identity changed before commit.",
            "NATIVE_RECONSTRUCT_POSTCONDITION",
        )
    artifacts = tuple(value.get("artifacts") or ())
    return {
        "capability": MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME,
        "class": prepared.plan["class"],
        "object_name": str(feature.Name),
        "solid_count": 1,
        "used_mesh_to_shape": False,
        "input": dict(prepared.input_summary),
        "outputs": [artifact.summary() for artifact in artifacts],
    }


def _job_summary(snapshot: Any) -> dict[str, Any]:
    return {
        "job_id": str(snapshot.job_id),
        "capability": str(snapshot.capability_name),
        "phase": str(snapshot.phase),
        "progress_percent": int(snapshot.progress_percent),
        "progress_message": str(snapshot.progress_message),
        "terminal": bool(snapshot.terminal),
    }


def _label(value: Any) -> str:
    result = " ".join(str(value or "").split())
    if not result or len(result) > 160 or any(ord(character) < 32 for character in result):
        raise NativeMeshError("result_label must contain 1 through 160 visible characters.")
    return result


class NativeReconstructParametricRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    @property
    def capability_name(self) -> str:
        return MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        if operation != "from_printables_ir":
            raise NativeMeshError(
                "unsupported reconstruct operation",
                error_code="NATIVE_RECONSTRUCT_OPERATION",
            )
        if not isinstance(ticket, NativeCallTicket):
            raise TypeError("ticket must be a NativeCallTicket")
        context = self._context
        context.guard()
        manager = context.background_manager
        dispatcher = context.document_thread_dispatch
        input_authorizer = context.authorize_input
        if manager is None or dispatcher is None or input_authorizer is None:
            raise NativeMeshError(
                "Background human-authorized reconstruction is unavailable in this session.",
                error_code="NATIVE_RECONSTRUCT_BACKGROUND_UNAVAILABLE",
            )
        request = printables_ir_input_request(str(values.get("ir_path") or ""))
        try:
            input_authorization = input_authorizer(request)
        except NativeInputError as exc:
            raise NativeMeshError(str(exc), error_code=exc.code) from exc
        if input_authorization is None:
            raise NativeMeshError(
                "The human cancelled printables IR selection.",
                error_code="NATIVE_RECONSTRUCT_INPUT_CANCELLED",
            )

        output_requests = reconstruction_output_requests(
            values,
            label=_label(values["result_label"]),
        )
        output_grants = []
        if output_requests:
            output_authorizer = context.authorize_output
            if output_authorizer is None:
                raise NativeMeshError(
                    "Human output authorization is unavailable in this session.",
                    error_code="NATIVE_RECONSTRUCT_OUTPUT_UNAVAILABLE",
                )
            try:
                for kind, output_request in output_requests:
                    authorization = output_authorizer(output_request)
                    if authorization is None:
                        raise NativeMeshError(
                            "The human cancelled reconstructed file output.",
                            error_code="NATIVE_RECONSTRUCT_OUTPUT_CANCELLED",
                        )
                    output_grants.append((kind, output_request, authorization))
            except NativeOutputError as exc:
                raise NativeMeshError(str(exc), error_code=exc.code) from exc

        def prepare(cancelled: Any, progress: Any) -> PreparedPrintablesReconstruction:
            return prepare_printables_reconstruction(
                input_authorization,
                request=request,
                cancelled=cancelled,
                progress=progress,
                output_kinds=tuple(kind for kind, _request, _authorization in output_grants),
            )

        def commit(prepared: PreparedPrintablesReconstruction) -> Mapping[str, Any]:
            return run_immediate_mutation(
                context,
                ticket=ticket,
                transaction_name="Reconstruct Parametric",
                mutate=lambda document: _reconstruction_draft(
                    document,
                    prepared,
                    label=_label(values["result_label"]),
                    outputs=tuple(output_grants),
                    guard=context.guard,
                ),
                verify=_verify_reconstruction,
            )

        try:
            snapshot = manager.submit(
                document_uid=context.document_uid,
                capability_name=(
                    f"{MESH_RECONSTRUCT_PARAMETRIC_CAPABILITY_NAME}.from_printables_ir"
                ),
                prepare=prepare,
                validate_before_commit=context.guard,
                commit=commit,
                dispatch_to_document_thread=dispatcher,
                finalize_message="Publishing reconstructed solid",
                cleanup=(
                    lambda prepared: (
                        prepared.cleanup()
                        if isinstance(prepared, PreparedPrintablesReconstruction)
                        else None
                    )
                ),
                changes_document=True,
            )
        except NativeBackgroundError as exc:
            raise NativeMeshError(
                str(exc),
                error_code="NATIVE_RECONSTRUCT_QUEUE_FAILED",
            ) from exc
        return {
            "job": _job_summary(snapshot),
            "next": {
                "tool": "native.job",
                "operation": "status",
                "job_id": snapshot.job_id,
            },
        }
