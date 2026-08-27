# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional Drawing hatch creation with human-owned file input."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree

from VibeCADNativeDrawingDimensionSupport import (
    PreparedDrawingDimensionTarget,
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    exact_drawing_mapping,
    prepare_drawing_dimension_target,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingGeometryState import drawing_projected_geometry_state
from VibeCADNativeDrawingHatchState import (
    MAX_DRAWING_HATCH_FACES,
    MAX_DRAWING_IMAGE_PATTERN_BYTES,
    MAX_DRAWING_PAT_PATTERN_BYTES,
    MAX_DRAWING_PATTERNS,
    drawing_hatch_inventory_state,
    drawing_hatch_state,
    normalize_drawing_hatch_plan,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeInput import (
    NativeInputArtifact,
    NativeInputError,
    NativeInputRequest,
    authorize_native_input_path,
)
from VibeCADNativeLabel import matches_preferred_document_label
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import object_identity


_IMAGE_OPERATIONS = frozenset({"create_image_default", "create_image_file"})
_GEOMETRIC_OPERATIONS = frozenset(
    {"create_geometric_default", "create_geometric_file"}
)
_DEFAULT_OPERATIONS = frozenset(
    {"create_image_default", "create_geometric_default"}
)


@dataclass(frozen=True, slots=True)
class DrawingHatchSpec:
    operation: str
    kind: str
    label: str
    faces: tuple[str, ...]
    pattern_name: str
    style: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedDrawingHatch:
    target: PreparedDrawingDimensionTarget
    spec: DrawingHatchSpec
    artifact: NativeInputArtifact
    source_kind: str
    host_plan: dict[str, Any]


def drawing_image_hatch_input_request() -> NativeInputRequest:
    return NativeInputRequest(
        purpose="drawing_image_hatch_pattern",
        title="Select Drawing Image Hatch Pattern",
        allowed_suffixes=(".svg", ".png", ".bmp", ".jpg", ".jpeg"),
        name_filter=(
            "Drawing hatch patterns (*.svg *.SVG *.png *.PNG *.bmp *.BMP "
            "*.jpg *.JPG *.jpeg *.JPEG)"
        ),
        maximum_bytes=MAX_DRAWING_IMAGE_PATTERN_BYTES,
    )


def drawing_geometric_hatch_input_request() -> NativeInputRequest:
    return NativeInputRequest(
        purpose="drawing_geometric_hatch_pattern",
        title="Select Drawing Geometric Hatch Pattern",
        allowed_suffixes=(".pat",),
        name_filter="Drawing PAT hatch patterns (*.pat *.PAT)",
        maximum_bytes=MAX_DRAWING_PAT_PATTERN_BYTES,
    )


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def _finite(
    value: Any,
    noun: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingError(
            f"Drawing hatch {noun} must be numeric.",
            error_code="NATIVE_DRAWING_HATCH_PARAMETERS_INVALID",
        ) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        _error(
            f"Drawing hatch {noun} must be from {minimum:g} through {maximum:g}.",
            "NATIVE_DRAWING_HATCH_PARAMETERS_INVALID",
        )
    return result


def _style(kind: str, value: Any) -> dict[str, Any]:
    fields = {"scale", "rotation_degrees", "offset_mm", "color_rgb"}
    if kind == "geometric":
        fields.add("line_width_mm")
    exact = exact_drawing_mapping(
        value,
        frozenset(fields),
        "style",
        family="hatch",
        error_code="NATIVE_DRAWING_HATCH_PARAMETERS_INVALID",
    )
    offset = exact_drawing_mapping(
        exact["offset_mm"],
        frozenset({"x_mm", "y_mm"}),
        "offset",
        family="hatch",
        error_code="NATIVE_DRAWING_HATCH_PARAMETERS_INVALID",
    )
    color = exact_drawing_mapping(
        exact["color_rgb"],
        frozenset({"red", "green", "blue"}),
        "color",
        family="hatch",
        error_code="NATIVE_DRAWING_HATCH_PARAMETERS_INVALID",
    )
    result = {
        "scale": _finite(exact["scale"], "scale", minimum=1.0e-12, maximum=1000.0),
        "rotation_degrees": _finite(
            exact["rotation_degrees"],
            "rotation_degrees",
            minimum=-360.0,
            maximum=360.0,
        ),
        "offset_mm": {
            "x_mm": _finite(
                offset["x_mm"], "offset x_mm", minimum=-1_000_000.0, maximum=1_000_000.0
            ),
            "y_mm": _finite(
                offset["y_mm"], "offset y_mm", minimum=-1_000_000.0, maximum=1_000_000.0
            ),
        },
        "color_rgb": {
            name: _finite(
                color[name], f"color {name}", minimum=0.0, maximum=1.0
            )
            for name in ("red", "green", "blue")
        },
    }
    if kind == "geometric":
        result["line_width_mm"] = _finite(
            exact["line_width_mm"],
            "line_width_mm",
            minimum=0.0,
            maximum=100.0,
        )
    return result


def _spec(operation: str, values: Mapping[str, Any]) -> DrawingHatchSpec:
    if operation in _IMAGE_OPERATIONS:
        kind = "image"
    elif operation in _GEOMETRIC_OPERATIONS:
        kind = "geometric"
    else:
        raise ValueError("operation is not a Drawing hatch operation")
    label = str(values["label"] or "")
    if label != label.strip() or not 1 <= len(label) <= 160:
        _error(
            "A Drawing hatch label must contain 1 to 160 non-padding characters.",
            "NATIVE_DRAWING_HATCH_PARAMETERS_INVALID",
        )
    raw_faces = tuple(values["faces"] or ())
    if not 1 <= len(raw_faces) <= MAX_DRAWING_HATCH_FACES:
        _error(
            "A Drawing hatch requires 1 to 64 exact projected faces.",
            "NATIVE_DRAWING_HATCH_REFERENCES_INVALID",
        )
    faces = tuple(str(value.get("subelement") or "") for value in raw_faces)
    pattern_name = str(values.get("pattern_name") or "")
    if kind == "geometric" and not 1 <= len(pattern_name) <= 128:
        _error(
            "A geometric Drawing hatch pattern name must contain 1 to 128 characters.",
            "NATIVE_DRAWING_HATCH_PARAMETERS_INVALID",
        )
    return DrawingHatchSpec(
        operation=operation,
        kind=kind,
        label=label,
        faces=faces,
        pattern_name=pattern_name,
        style=_style(kind, values["style"]),
    )


def _color(raw: Any, noun: str) -> dict[str, float]:
    if not isinstance(raw, Mapping) or set(raw) != {"red", "green", "blue"}:
        _error(
            f"TechDraw returned malformed {noun}.",
            "NATIVE_DRAWING_HATCH_RUNTIME_UNAVAILABLE",
        )
    return {
        name: _finite(raw[name], f"{noun} {name}", minimum=0.0, maximum=1.0)
        for name in ("red", "green", "blue")
    }


def _host_defaults() -> dict[str, Any]:
    try:
        import TechDrawGui

        raw = TechDrawGui.drawingHatchDefaults()
    except Exception as exc:
        _error(
            f"TechDraw hatch defaults are unavailable: {str(exc).strip()}",
            "NATIVE_DRAWING_HATCH_DEFAULTS_UNAVAILABLE",
        )
    if not isinstance(raw, Mapping) or set(raw) != {"image", "geometric"}:
        _error(
            "TechDraw returned malformed hatch defaults.",
            "NATIVE_DRAWING_HATCH_RUNTIME_UNAVAILABLE",
        )
    image = raw["image"]
    geometric = raw["geometric"]
    if (
        not isinstance(image, Mapping)
        or set(image) != {"pattern_file", "pattern_file_name", "color_rgb"}
        or not isinstance(geometric, Mapping)
        or set(geometric)
        != {
            "pattern_file",
            "pattern_file_name",
            "pattern_name",
            "pattern_names",
            "color_rgb",
            "line_width_mm",
        }
    ):
        _error(
            "TechDraw returned malformed hatch defaults.",
            "NATIVE_DRAWING_HATCH_RUNTIME_UNAVAILABLE",
        )
    names = tuple(str(value or "") for value in tuple(geometric["pattern_names"] or ()))
    preferred = str(geometric["pattern_name"] or "")
    if (
        not 1 <= len(names) <= MAX_DRAWING_PATTERNS
        or len(names) != len(set(names))
        or any(not 1 <= len(value) <= 128 for value in names)
        or preferred not in names
    ):
        _error(
            "TechDraw returned an invalid PAT pattern catalog.",
            "NATIVE_DRAWING_HATCH_RUNTIME_UNAVAILABLE",
        )
    return {
        "image": {
            "pattern_file": str(image["pattern_file"] or ""),
            "pattern_file_name": str(image["pattern_file_name"] or ""),
            "color_rgb": _color(image["color_rgb"], "default image color"),
        },
        "geometric": {
            "pattern_file": str(geometric["pattern_file"] or ""),
            "pattern_file_name": str(geometric["pattern_file_name"] or ""),
            "pattern_name": preferred,
            "pattern_names": names,
            "color_rgb": _color(
                geometric["color_rgb"], "default geometric color"
            ),
            "line_width_mm": _finite(
                geometric["line_width_mm"],
                "default geometric line width",
                minimum=0.0,
                maximum=100.0,
            ),
        },
    }


def _claim_default(kind: str) -> tuple[NativeInputArtifact, dict[str, Any]]:
    defaults = _host_defaults()
    selected = defaults[kind]
    request = (
        drawing_image_hatch_input_request()
        if kind == "image"
        else drawing_geometric_hatch_input_request()
    )
    try:
        authorization = authorize_native_input_path(
            request, selected["pattern_file"]
        )
        artifact = authorization.claim(request)
    except NativeInputError as exc:
        raise NativeDrawingError(
            "The configured Drawing hatch pattern is unavailable or invalid.",
            error_code="NATIVE_DRAWING_HATCH_DEFAULTS_UNAVAILABLE",
            repair={"preference": "TechDraw hatch pattern file"},
        ) from exc
    if artifact.file_name != selected["pattern_file_name"]:
        _error(
            "TechDraw's configured hatch file identity changed during authorization.",
            "NATIVE_DRAWING_HATCH_DEFAULTS_UNAVAILABLE",
        )
    return artifact, defaults


def _claim_authorized(
    authorization: Any,
    request: NativeInputRequest,
) -> NativeInputArtifact:
    try:
        return authorization.claim(request)
    except (AttributeError, NativeInputError) as exc:
        code = getattr(exc, "code", "NATIVE_INPUT_AUTHORIZATION_FAILED")
        raise NativeDrawingError(str(exc), error_code=code) from exc


def _validate_image_artifact(artifact: NativeInputArtifact) -> None:
    suffix = Path(artifact.file_name).suffix.casefold()
    if suffix != ".svg":
        return
    content = artifact.read_bytes(maximum_bytes=MAX_DRAWING_IMAGE_PATTERN_BYTES)
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _error(
            "The selected SVG hatch contains an unsupported document type or entity declaration.",
            "NATIVE_DRAWING_HATCH_PATTERN_INVALID",
        )
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise NativeDrawingError(
            "The selected SVG hatch is not valid XML.",
            error_code="NATIVE_DRAWING_HATCH_PATTERN_INVALID",
        ) from exc
    if str(root.tag).rsplit("}", 1)[-1].casefold() != "svg":
        _error(
            "The selected SVG hatch has no SVG root element.",
            "NATIVE_DRAWING_HATCH_PATTERN_INVALID",
        )
    if sum(1 for _element in root.iter()) > 50_000:
        _error(
            "The selected SVG hatch contains too many elements.",
            "NATIVE_DRAWING_HATCH_PATTERN_INVALID",
        )


def drawing_hatch_defaults_state() -> dict[str, Any]:
    """Return configured defaults and catalog without host paths."""

    image_artifact, defaults = _claim_default("image")
    geometric_artifact, geometric_defaults = _claim_default("geometric")
    _validate_image_artifact(image_artifact)
    exact = {
        "image": {
            "pattern": {**image_artifact.summary(), "pattern_kind": (
                "svg"
                if Path(image_artifact.file_name).suffix.casefold() == ".svg"
                else "bitmap"
            )},
            "default_style": {
                "scale": 1.0,
                "rotation_degrees": 0.0,
                "offset_mm": {"x_mm": 0.0, "y_mm": 0.0},
                "color_rgb": defaults["image"]["color_rgb"],
            },
        },
        "geometric": {
            "pattern": geometric_artifact.summary(),
            "default_pattern_name": geometric_defaults["geometric"]["pattern_name"],
            "pattern_names": list(geometric_defaults["geometric"]["pattern_names"]),
            "default_style": {
                "scale": 1.0,
                "rotation_degrees": 0.0,
                "offset_mm": {"x_mm": 0.0, "y_mm": 0.0},
                "line_width_mm": geometric_defaults["geometric"]["line_width_mm"],
                "color_rgb": geometric_defaults["geometric"]["color_rgb"],
            },
        },
    }
    encoded = json.dumps(
        exact, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {**exact, "defaults_state_sha256": hashlib.sha256(encoded).hexdigest()}


def _host_plan(
    view: Any,
    spec: DrawingHatchSpec,
    artifact: NativeInputArtifact,
    *,
    apply: bool,
) -> tuple[dict[str, Any], Any | None]:
    try:
        import TechDrawGui

        style = spec.style
        offset = style["offset_mm"]
        color = style["color_rgb"]
        pattern_file = str(artifact.host_path_after_content_verification())
        if spec.kind == "image":
            function = (
                TechDrawGui.createDrawingImageHatch
                if apply
                else TechDrawGui.validateDrawingImageHatch
            )
            raw = function(
                view,
                list(spec.faces),
                pattern_file,
                style["scale"],
                style["rotation_degrees"],
                offset["x_mm"],
                offset["y_mm"],
                color["red"],
                color["green"],
                color["blue"],
            )
        else:
            function = (
                TechDrawGui.createDrawingGeometricHatch
                if apply
                else TechDrawGui.validateDrawingGeometricHatch
            )
            raw = function(
                view,
                list(spec.faces),
                pattern_file,
                spec.pattern_name,
                style["scale"],
                style["rotation_degrees"],
                offset["x_mm"],
                offset["y_mm"],
                style["line_width_mm"],
                color["red"],
                color["green"],
                color["blue"],
            )
    except NativeDrawingError:
        raise
    except Exception as exc:
        if apply:
            raise NativeMutationError(
                "NATIVE_DRAWING_HATCH_CREATE_FAILED",
                f"TechDraw rejected the exact {spec.kind} hatch: {str(exc).strip()}",
            ) from exc
        _error(
            f"TechDraw rejected the exact {spec.kind} hatch: {str(exc).strip()}",
            "NATIVE_DRAWING_HATCH_PATTERN_INVALID",
            repair={
                "accepted_target": "1 to 64 unique projected FaceN references",
                "tool": "drawing.projected_geometry",
            },
        )
    hatch = raw.get("hatch") if apply and isinstance(raw, Mapping) else None
    if apply and isinstance(raw, Mapping):
        raw = {key: value for key, value in raw.items() if key not in {"hatch", "object_name"}}
    return normalize_drawing_hatch_plan(raw, kind=spec.kind), hatch


def prepare_drawing_hatch(
    document: Any,
    *,
    operation: str,
    values: Mapping[str, Any],
    authorization: Any | None = None,
    input_request: NativeInputRequest | None = None,
) -> PreparedDrawingHatch:
    spec = _spec(operation, values)
    target = prepare_drawing_dimension_target(
        document,
        page_target=values["page"],
        view_target=values["view"],
        element_targets=tuple(values["faces"]),
        allowed_element_types=frozenset({"face"}),
        family="hatch",
        code_prefix="NATIVE_DRAWING_HATCH",
    )
    if tuple(item["name"] for item in target.element_states_before) != spec.faces:
        _error(
            "The Drawing hatch face targets are inconsistent.",
            "NATIVE_DRAWING_HATCH_REFERENCES_INVALID",
        )
    if spec.kind == "image":
        requested_faces = set(spec.faces)
        conflicts = [
            {
                "object_name": item["object_name"],
                "faces": sorted(requested_faces.intersection(item["faces"])),
            }
            for item in drawing_hatch_inventory_state(target.view)["hatches"]
            if item["kind"] == "image"
            and requested_faces.intersection(item["faces"])
        ]
        if conflicts:
            _error(
                "One or more requested faces already have an image hatch.",
                "NATIVE_DRAWING_HATCH_FACE_CONFLICT",
                repair={
                    "conflicting_hatches": conflicts,
                    "requirement": "Choose unhatched faces or remove the existing image hatch first.",
                },
            )
    if operation in _DEFAULT_OPERATIONS:
        artifact, defaults = _claim_default(spec.kind)
        source_kind = "configured_default"
        if spec.kind == "geometric" and spec.pattern_name not in defaults["geometric"]["pattern_names"]:
            _error(
                "The requested geometric hatch pattern is not in the configured PAT catalog.",
                "NATIVE_DRAWING_HATCH_PATTERN_INVALID",
                repair={
                    "available_pattern_names": list(defaults["geometric"]["pattern_names"]),
                    "read_operation": "read_defaults",
                },
            )
    else:
        expected_request = (
            drawing_image_hatch_input_request()
            if spec.kind == "image"
            else drawing_geometric_hatch_input_request()
        )
        request = input_request
        if request is None and authorization is not None:
            request = getattr(authorization, "request", None)
        if not isinstance(request, NativeInputRequest) or request != expected_request:
            _error(
                "The authorized Drawing hatch request does not match this operation.",
                "NATIVE_DRAWING_HATCH_INPUT_INVALID",
            )
        artifact = _claim_authorized(authorization, request)
        source_kind = "human_authorized"
    _validate_image_artifact(artifact)
    artifact.verify_unchanged()
    host_plan, _hatch = _host_plan(target.view, spec, artifact, apply=False)
    if (
        host_plan["view_name"] != str(target.view.Name)
        or host_plan["page_name"] != str(target.page.Name)
        or tuple(host_plan["faces"]) != spec.faces
        or host_plan["pattern_file_name"] != artifact.file_name
        or host_plan["style"] != spec.style
        or (
            spec.kind == "geometric"
            and host_plan["pattern_name"] != spec.pattern_name
        )
    ):
        _error(
            "TechDraw's hatch plan does not match the exact request.",
            "NATIVE_DRAWING_HATCH_RUNTIME_UNAVAILABLE",
        )
    return PreparedDrawingHatch(
        target=target,
        spec=spec,
        artifact=artifact,
        source_kind=source_kind,
        host_plan=host_plan,
    )


def mutate_drawing_hatch(
    document: Any,
    *,
    prepared: PreparedDrawingHatch,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedDrawingHatch):
        raise TypeError("prepared must be a PreparedDrawingHatch")
    prepared.artifact.verify_unchanged()
    applied, hatch = _host_plan(
        prepared.target.view,
        prepared.spec,
        prepared.artifact,
        apply=True,
    )
    if applied != prepared.host_plan or hatch is None:
        _error(
            "TechDraw created a hatch inconsistent with preflight.",
            "NATIVE_DRAWING_HATCH_CREATE_FAILED",
        )
    if (
        getattr(hatch, "Document", None) is not document
        or not hatch.isDerivedFrom(
            "TechDraw::DrawHatch"
            if prepared.spec.kind == "image"
            else "TechDraw::DrawGeomHatch"
        )
    ):
        _error(
            "TechDraw did not create the requested hatch type.",
            "NATIVE_DRAWING_HATCH_CREATE_FAILED",
        )
    hatch.Label = prepared.spec.label
    try:
        document.publishProvisionalTimelineOperationBlock(hatch, (), ())
    except Exception as exc:
        raise NativeMutationError(
            "NATIVE_DRAWING_HATCH_HISTORY_FAILED",
            f"The Drawing hatch could not be enrolled in History: {str(exc).strip()}",
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "hatch": hatch},
        recompute_targets=(),
        created=(object_identity(hatch),),
    )


def _postcondition_error(message: str) -> None:
    raise NativeMutationError(
        "NATIVE_DRAWING_HATCH_POSTCONDITION_FAILED",
        message,
    )


def verify_drawing_hatch(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedDrawingHatch = draft.value["prepared"]
    hatch = draft.value["hatch"]
    target = prepared.target
    before_keys = {drawing_object_key(obj) for obj in target.objects_before}
    new_objects = tuple(
        obj for obj in document.Objects if drawing_object_key(obj) not in before_keys
    )
    if tuple(map(drawing_object_key, new_objects)) != (drawing_object_key(hatch),):
        _postcondition_error("Hatch creation changed objects outside its result.")
    if tuple(map(drawing_object_key, tuple(target.page.Views or ()))) != tuple(
        map(drawing_object_key, target.page_views_before)
    ):
        _postcondition_error("Hatch creation changed Drawing page membership.")
    if tuple(map(drawing_object_key, drawing_timeline_operations(document))) != tuple(
        map(drawing_object_key, (*target.timeline_before, hatch))
    ):
        _postcondition_error("Hatch creation was not one exact History operation.")
    if (
        drawing_view_state(target.view)["state_sha256"]
        != target.view_state_before["state_sha256"]
    ):
        _postcondition_error("Hatch creation changed its source view definition.")
    projection_after = drawing_projected_geometry_state(target.view)
    if (
        projection_after["projection_state_sha256"]
        != target.projection_state_before["projection_state_sha256"]
    ):
        before_by_name = {
            item["name"]: item
            for item in target.projection_state_before["elements"]
        }
        after_by_name = {item["name"]: item for item in projection_after["elements"]}
        changed = []
        for name in sorted(set(before_by_name) | set(after_by_name)):
            before = before_by_name.get(name, {})
            after = after_by_name.get(name, {})
            if before and not after:
                changed.append(f"{name}(removed)")
                if len(changed) >= 8:
                    break
                continue
            if after and not before:
                changed.append(f"{name}(added)")
                if len(changed) >= 8:
                    break
                continue
            fields = sorted(
                key
                for key in set(before) | set(after)
                if key != "element_state_sha256" and before.get(key) != after.get(key)
            )
            if fields:
                changed.append(f"{name}({','.join(fields[:4])})")
            if len(changed) >= 8:
                break
        detail = ", ".join(changed) or "projection metadata"
        _postcondition_error(
            f"Hatch creation changed its projected geometry: {detail}."
        )
    if drawing_page_state(target.page)["state_sha256"] != target.page_state_before[
        "state_sha256"
    ]:
        _postcondition_error("Hatch creation changed its Drawing page definition.")
    if drawing_selection_state(document) != target.selection_before:
        _postcondition_error("Hatch creation changed the human selection.")
    actual_visibility = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in target.visibility_before
    )
    if actual_visibility != target.visibility_before:
        _postcondition_error("Hatch creation changed existing visibility.")
    state = drawing_hatch_state(hatch)
    expected_pattern = prepared.artifact.summary()
    checks = (
        ("kind", state["kind"] == prepared.spec.kind),
        (
            "label",
            matches_preferred_document_label(
                state["label"], prepared.spec.label
            ),
        ),
        ("page", state["page_name"] == str(target.page.Name)),
        ("source view", state["source_view_name"] == str(target.view.Name)),
        ("faces", tuple(state["faces"]) == prepared.spec.faces),
        ("style", state["style"] == prepared.spec.style),
        (
            "embedded pattern content",
            all(
                state["pattern"][name] == expected_pattern[name]
                for name in ("file_name", "size_bytes", "sha256")
            ),
        ),
        (
            "PAT pattern name",
            prepared.spec.kind != "geometric"
            or state["pattern"]["pattern_name"] == prepared.spec.pattern_name,
        ),
        ("History availability", state["timeline_usable"]),
        ("validity", state["valid"]),
    )
    mismatch = next((name for name, matches in checks if not matches), None)
    if mismatch is not None:
        _postcondition_error(
            f"The created Drawing hatch does not match its requested {mismatch}."
        )
    return {
        "hatch": state,
        "source_kind": prepared.source_kind,
        "next": {
            "tool": "drawing.projected_geometry",
            "view_name": str(target.view.Name),
        },
    }
