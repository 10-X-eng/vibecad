# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic, task-free creation of one exact CAM Profile operation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import (
    PreparedOperationBoundary,
    create_native_operation,
    extend_native_operation_draft,
    merge_subelement_geometry_items,
    native_operation_presentation,
    preflight_operation_boundary,
    quantity_mm as shared_quantity_mm,
    verify_native_operation,
)
from VibeCADNativeManufactureState import (
    capture_other_job_states,
    job_state,
    operation_state,
    other_job_states_are_current,
    resolve_job_target,
    resolve_tool_controller_target,
    tool_controller_state,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


MAX_PROFILE_GEOMETRY_ITEMS = 32
MAX_PROFILE_SUBELEMENTS = 64
_SUBELEMENT_NAME = re.compile(r"^(Face|Edge)([1-9][0-9]*)$")
_PROFILE_FIELDS = frozenset(
    {
        "direction",
        "cut_side",
        "cutter_compensation",
        "extra_offset_mm",
        "pass_count",
        "stepover_mm",
        "multiple_features",
        "sorting",
        "start_on_longest_edge",
        "profile_outer_perimeter",
        "profile_noncircular_holes",
        "profile_circular_holes",
    }
)
_DEPTH_FIELDS = frozenset(
    {"start_depth_mm", "final_depth_mm", "step_down_mm"}
)
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})


@dataclass(frozen=True, slots=True)
class ProfileCreateSpec:
    label: str
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    profile: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    coolant: str


@dataclass(frozen=True, slots=True)
class PreparedProfileGeometry:
    public_source: Any
    job_resource: Any
    source_state_sha256: str
    shape_sha256: str
    subelements: tuple[str, ...]
    element_sha256: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class PreparedProfileCreate:
    spec: ProfileCreateSpec
    settings: Mapping[str, Any]
    job: Any
    job_before: Mapping[str, Any]
    controller: Any
    controller_before: Mapping[str, Any]
    geometry_kind: str
    geometry: tuple[PreparedProfileGeometry, ...]
    job_operations_before: tuple[Any, ...]
    other_job_states: tuple[tuple[Any, str], ...]
    objects_before: tuple[Any, ...]
    selection_before: Any
    timeline_before: _TimelineState


@dataclass(frozen=True, slots=True)
class ProfileDefaultsSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: tuple[Mapping[str, Any], ...]
    cut_side: str
    coolant: str


@dataclass(frozen=True, slots=True)
class PreparedProfileDefaults:
    label: str
    boundary: PreparedOperationBoundary
    cut_side: str
    coolant: str


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _clean_label(value: Any) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 160:
        _error("A Profile label must contain 1 through 160 characters.")
    return result


def _finite(value: Any, noun: str) -> float:
    if isinstance(value, bool):
        _error(f"{noun} must be one finite number in millimetres.")
    try:
        result = float(value)
    except (TypeError, ValueError):
        _error(f"{noun} must be one finite number in millimetres.")
    if not math.isfinite(result) or abs(result) > 1_000_000.0:
        _error(f"{noun} must be between -1000000 and 1000000 mm.")
    return round(result, 9)


def _exact_fields(value: Any, fields: frozenset[str], noun: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _error(f"{noun} must contain exactly: {', '.join(sorted(fields))}.")
    return value


def _normalize_settings(spec: ProfileCreateSpec) -> dict[str, Any]:
    profile = _exact_fields(spec.profile, _PROFILE_FIELDS, "profile")
    depths = _exact_fields(spec.depths, _DEPTH_FIELDS, "depths")
    heights = _exact_fields(spec.heights, _HEIGHT_FIELDS, "heights")
    direction = str(profile["direction"] or "")
    side = str(profile["cut_side"] or "")
    multiple = str(profile["multiple_features"] or "")
    sorting = str(profile["sorting"] or "")
    coolant = str(spec.coolant or "")
    if direction not in {"clockwise", "counterclockwise"}:
        _error("direction must be clockwise or counterclockwise.")
    if side not in {"outside", "inside"}:
        _error("cut_side must be outside or inside.")
    if multiple not in {"collectively", "individually"}:
        _error("multiple_features must be collectively or individually.")
    if sorting not in {"automatic", "manual"}:
        _error("sorting must be automatic or manual.")
    if coolant not in {"none", "flood", "mist"}:
        _error("coolant must be none, flood, or mist.")
    boolean_names = (
        "cutter_compensation",
        "start_on_longest_edge",
        "profile_outer_perimeter",
        "profile_noncircular_holes",
        "profile_circular_holes",
    )
    if any(not isinstance(profile[name], bool) for name in boolean_names):
        _error("Every Profile process switch must be a boolean.")
    passes = profile["pass_count"]
    if isinstance(passes, bool) or not isinstance(passes, int) or not 1 <= passes <= 99999:
        _error("pass_count must be an integer from 1 through 99999.")
    stepover = _finite(profile["stepover_mm"], "stepover_mm")
    if stepover < 0.0:
        _error("stepover_mm cannot be negative.")
    if (passes == 1 and stepover != 0.0) or (passes > 1 and stepover <= 0.0):
        _error(
            "stepover_mm must be zero for one pass and greater than zero for multiple passes."
        )
    if multiple == "collectively" and (
        sorting != "automatic" or bool(profile["start_on_longest_edge"])
    ):
        _error(
            "Collective feature handling requires automatic sorting and cannot start on a longest edge."
        )
    start_depth = _finite(depths["start_depth_mm"], "start_depth_mm")
    final_depth = _finite(depths["final_depth_mm"], "final_depth_mm")
    step_down = _finite(depths["step_down_mm"], "step_down_mm")
    safe_height = _finite(heights["safe_height_mm"], "safe_height_mm")
    clearance_height = _finite(
        heights["clearance_height_mm"], "clearance_height_mm"
    )
    if final_depth >= start_depth:
        _error("final_depth_mm must be lower than start_depth_mm.")
    if step_down <= 0.0:
        _error("step_down_mm must be greater than zero.")
    if safe_height < start_depth:
        _error("safe_height_mm must be at or above start_depth_mm.")
    if clearance_height < safe_height:
        _error("clearance_height_mm must be at or above safe_height_mm.")
    return {
        "direction": direction,
        "cut_side": side,
        "cutter_compensation": profile["cutter_compensation"],
        "extra_offset_mm": _finite(profile["extra_offset_mm"], "extra_offset_mm"),
        "pass_count": passes,
        "stepover_mm": stepover,
        "multiple_features": multiple,
        "sorting": sorting,
        "start_on_longest_edge": profile["start_on_longest_edge"],
        "profile_outer_perimeter": profile["profile_outer_perimeter"],
        "profile_noncircular_holes": profile["profile_noncircular_holes"],
        "profile_circular_holes": profile["profile_circular_holes"],
        "start_depth_mm": start_depth,
        "final_depth_mm": final_depth,
        "step_down_mm": step_down,
        "safe_height_mm": safe_height,
        "clearance_height_mm": clearance_height,
        "coolant": coolant,
    }


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _timeline_state(document: Any) -> _TimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != "App::DocumentTimeline":
        _error(
            "The CAM Job requires a valid document History before adding operations.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    if len(operations) != len(visibility):
        _error("The document History is malformed.", "NATIVE_MANUFACTURE_HISTORY_INVALID")
    return _TimelineState(timeline, operations, visibility)


def _shape_sha256(shape: Any, noun: str) -> str:
    export = getattr(shape, "exportBrepToString", None)
    if shape is None or not callable(export):
        _error(
            f"The exact {noun} has no serializable Part geometry.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    try:
        content = export()
        encoded = content if isinstance(content, bytes) else str(content).encode("utf-8")
    except Exception as exc:
        raise NativeManufactureError(
            f"The exact {noun} geometry could not be fingerprinted.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _job_model_map(job: Any) -> dict[str, tuple[Any, Any]]:
    result: dict[str, tuple[Any, Any]] = {}
    for resource in tuple(getattr(getattr(job, "Model", None), "Group", ()) or ()):
        public = job.Proxy.baseObject(job, resource)
        name = str(getattr(public, "Name", "") or "")
        if (
            not name
            or getattr(public, "Document", None) is not job.Document
            or name in result
        ):
            _error(
                "The CAM Job model graph has no unique public-source mapping.",
                "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
            )
        result[name] = (public, resource)
    return result


def _validate_subelement(source: Any, name: str) -> tuple[str, str]:
    match = _SUBELEMENT_NAME.fullmatch(str(name or ""))
    if not match:
        _error("Profile geometry accepts only exact FaceN and EdgeN names.")
    try:
        element = source.Shape.getElement(name)
    except Exception as exc:
        raise NativeManufactureError(
            f"Profile geometry {source.Name}.{name} no longer exists.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        ) from exc
    expected_type = match.group(1)
    if str(getattr(element, "ShapeType", "")) != expected_type:
        _error(
            f"Profile geometry {source.Name}.{name} is not a {expected_type.lower()}.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return expected_type, _shape_sha256(element, f"Profile subelement {name}")


def _prepare_geometry(
    document: Any,
    job: Any,
    job_before: Mapping[str, Any],
    request: Mapping[str, Any],
) -> tuple[str, tuple[PreparedProfileGeometry, ...], frozenset[str]]:
    models = _job_model_map(job)
    model_states = {
        str(item.get("object_name") or ""): str(item.get("state_sha256") or "")
        for item in job_before.get("models", ())
    }
    kind = str(request.get("kind") or "") if isinstance(request, Mapping) else ""
    if kind == "entire_job":
        if set(request) != {"kind"}:
            _error("entire_job geometry contains only kind.")
        prepared = tuple(
            PreparedProfileGeometry(
                public_source=public,
                job_resource=resource,
                source_state_sha256=model_states.get(name, ""),
                shape_sha256=_shape_sha256(public.Shape, f"CAM model {name}"),
                subelements=(),
                element_sha256=(),
            )
            for name, (public, resource) in models.items()
        )
        if not prepared:
            _error(
                "The exact CAM Job has no model to profile.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        return kind, prepared, frozenset()
    if kind != "subelements" or set(request) != {"kind", "items"}:
        _error("geometry must be entire_job or a closed subelements request.")
    grouped_items = merge_subelement_geometry_items(
        request.get("items"),
        noun="Profile",
        max_items=MAX_PROFILE_GEOMETRY_ITEMS,
        max_subelements=MAX_PROFILE_SUBELEMENTS,
    )
    prepared_items = []
    selected_types: set[str] = set()
    for name, expected, names in grouped_items:
        if name not in models:
            _error(
                f"Profile model {name!r} is not a public source owned by the exact Job.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        if model_states.get(name) != expected:
            _error(
                f"CAM model {name!r} changed after turn start.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        public, resource = models[name]
        if document.getObject(name) is not public:
            _error(
                f"CAM model {name!r} no longer exists.",
                "NATIVE_MANUFACTURE_TARGET_STALE",
            )
        element_hashes = []
        for subelement in names:
            element_type, element_hash = _validate_subelement(public, subelement)
            selected_types.add(element_type)
            element_hashes.append(element_hash)
        prepared_items.append(
            PreparedProfileGeometry(
                public_source=public,
                job_resource=resource,
                source_state_sha256=expected,
                shape_sha256=_shape_sha256(public.Shape, f"CAM model {name}"),
                subelements=names,
                element_sha256=tuple(element_hashes),
            )
        )
    return kind, tuple(prepared_items), frozenset(selected_types)


def _validate_feature_processing(
    geometry_kind: str,
    selected_types: frozenset[str],
    settings: Mapping[str, Any],
) -> None:
    outer = bool(settings["profile_outer_perimeter"])
    holes = bool(settings["profile_noncircular_holes"])
    circles = bool(settings["profile_circular_holes"])
    if geometry_kind == "entire_job" or "Face" not in selected_types:
        if not outer or holes or circles:
            _error(
                "Entire-model and edge-only Profiles require the outer perimeter and cannot request face-hole processing."
            )
    elif not any((outer, holes, circles)):
        _error("A face Profile must enable at least one perimeter or hole process.")


def preflight_profile_create(
    document: Any,
    spec: ProfileCreateSpec,
) -> PreparedProfileCreate:
    """Freeze the exact Job graph, controller, geometry, and parameters."""

    if not isinstance(spec, ProfileCreateSpec):
        raise TypeError("spec must be a ProfileCreateSpec")
    if _transaction_open(document):
        _error(
            "Finish or cancel the open task before creating a CAM Profile.",
            "NATIVE_TRANSACTION_ACTIVE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for document recompute to finish before creating a CAM Profile.",
            "NATIVE_MANUFACTURE_RECOMPUTE_ACTIVE",
        )
    clean_spec = ProfileCreateSpec(
        label=_clean_label(spec.label),
        job=spec.job,
        tool_controller=spec.tool_controller,
        geometry=spec.geometry,
        profile=spec.profile,
        depths=spec.depths,
        heights=spec.heights,
        coolant=str(spec.coolant or ""),
    )
    settings = _normalize_settings(clean_spec)
    job, before = resolve_job_target(document, clean_spec.job)
    controller, controller_before = resolve_tool_controller_target(
        document, clean_spec.tool_controller
    )
    if (
        controller not in tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
        or controller_before.get("job_name") != str(job.Name)
    ):
        _error(
            "The exact tool controller is not owned by the exact CAM Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    geometry_kind, geometry, selected_types = _prepare_geometry(
        document, job, before, clean_spec.geometry
    )
    _validate_feature_processing(geometry_kind, selected_types, settings)
    operations = tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
    return PreparedProfileCreate(
        spec=clean_spec,
        settings=settings,
        job=job,
        job_before=before,
        controller=controller,
        controller_before=controller_before,
        geometry_kind=geometry_kind,
        geometry=geometry,
        job_operations_before=operations,
        other_job_states=capture_other_job_states(document, (job,)),
        objects_before=tuple(document.Objects),
        selection_before=read_current_selection(document),
        timeline_before=_timeline_state(document),
    )


def preflight_profile_defaults(
    document: Any,
    spec: ProfileDefaultsSpec,
) -> PreparedProfileDefaults:
    """Freeze exact profile geometry while retaining setup-owned defaults."""

    if not isinstance(spec, ProfileDefaultsSpec):
        raise TypeError("spec must be a ProfileDefaultsSpec")
    cut_side = str(spec.cut_side or "")
    if cut_side not in {"outside", "inside"}:
        _error("Profile cut_side must be outside or inside.")
    coolant = str(spec.coolant or "")
    if coolant not in {"none", "flood", "mist"}:
        _error("Profile coolant must be none, flood, or mist.")
    boundary = preflight_operation_boundary(
        document,
        noun="Profile",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry={"kind": "subelements", "items": list(spec.geometry)},
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=False,
    )
    return PreparedProfileDefaults(
        label=_clean_label(spec.label),
        boundary=boundary,
        cut_side=cut_side,
        coolant=coolant,
    )


def _assert_preflight_current(document: Any, prepared: PreparedProfileCreate) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        _error(
            "The CAM document graph changed before Profile creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if read_current_selection(document) != prepared.selection_before:
        _error(
            "The human selection changed before Profile creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if _timeline_state(document) != prepared.timeline_before:
        _error(
            "Document History changed before Profile creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if (
        job_state(prepared.job).get("state_sha256")
        != prepared.job_before.get("state_sha256")
        or tool_controller_state(prepared.controller).get("state_sha256")
        != prepared.controller_before.get("state_sha256")
        or tuple(prepared.job.Operations.Group) != prepared.job_operations_before
    ):
        _error(
            "The CAM Job or controller changed before Profile creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if not other_job_states_are_current(document, prepared.other_job_states):
        _error(
            "Another CAM setup changed before Profile creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    for item in prepared.geometry:
        if (
            document.getObject(str(item.public_source.Name)) is not item.public_source
            or _shape_sha256(
                item.public_source.Shape,
                f"CAM model {item.public_source.Name}",
            )
            != item.shape_sha256
        ):
            _error(
                f"CAM model {item.public_source.Name!r} changed before Profile creation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        hashes = tuple(
            _validate_subelement(item.public_source, name)[1]
            for name in item.subelements
        )
        if hashes != item.element_sha256:
            _error(
                f"Selected geometry on {item.public_source.Name!r} changed before Profile creation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )


def _clear_expression(obj: Any, property_name: str) -> None:
    try:
        obj.setExpression(property_name, None)
    except Exception as exc:
        raise NativeManufactureError(
            f"The CAM Profile could not take manual control of {property_name}.",
            error_code="NATIVE_MANUFACTURE_PROFILE_CREATE_FAILED",
        ) from exc


def _apply_settings(operation: Any, prepared: PreparedProfileCreate) -> None:
    settings = prepared.settings
    for property_name in (
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "SafeHeight",
        "ClearanceHeight",
    ):
        _clear_expression(operation, property_name)
    operation.Label = prepared.spec.label
    operation.ToolController = prepared.controller
    operation.OpToolDiameter = prepared.controller.Tool.Diameter
    operation.Direction = (
        "CW" if settings["direction"] == "clockwise" else "CCW"
    )
    operation.Side = settings["cut_side"].capitalize()
    operation.UseComp = settings["cutter_compensation"]
    operation.OffsetExtra = f"{settings['extra_offset_mm']} mm"
    operation.NumPasses = settings["pass_count"]
    operation.Stepover = f"{settings['stepover_mm']} mm"
    operation.HandleMultipleFeatures = settings["multiple_features"].capitalize()
    operation.SortingMode = settings["sorting"].capitalize()
    operation.UseLongestEdge = settings["start_on_longest_edge"]
    operation.processPerimeter = settings["profile_outer_perimeter"]
    operation.processHoles = settings["profile_noncircular_holes"]
    operation.processCircles = settings["profile_circular_holes"]
    operation.StartDepth = f"{settings['start_depth_mm']} mm"
    operation.FinalDepth = f"{settings['final_depth_mm']} mm"
    operation.StepDown = f"{settings['step_down_mm']} mm"
    operation.SafeHeight = f"{settings['safe_height_mm']} mm"
    operation.ClearanceHeight = f"{settings['clearance_height_mm']} mm"
    operation.CoolantMode = settings["coolant"].capitalize()
    operation.UseStartPoint = False


def create_profile(
    document: Any,
    *,
    prepared: PreparedProfileCreate,
) -> NativeMutationDraft:
    """Create a Profile with the native CAM factories inside one transaction."""

    if not isinstance(prepared, PreparedProfileCreate):
        raise TypeError("prepared must be a PreparedProfileCreate")
    _assert_preflight_current(document, prepared)
    try:
        import Path.Op.Profile as PathProfile
        import Path.Op.Gui.Profile as PathProfileGui

        operation = PathProfile.Create(
            "Profile",
            parentJob=prepared.job,
            toolController=prepared.controller,
        )
        if prepared.geometry_kind == "subelements":
            for item in prepared.geometry:
                for subelement in item.subelements:
                    operation.Proxy.addBase(
                        operation,
                        item.public_source,
                        subelement,
                    )
        _apply_settings(operation, prepared)
        provider = PathProfileGui.PathOpGui.ViewProvider(
            operation.ViewObject,
            PathProfileGui.Command.res,
        )
        operation.ViewObject.Proxy = provider
        provider.deleteOnReject = False
        operation.ViewObject.Visibility = True
        if not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(
            operation
        ):
            raise RuntimeError(
                "The Profile was not provisionally enrolled in document History"
            )
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Profile factory could not create the exact operation.",
            error_code="NATIVE_MANUFACTURE_PROFILE_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "operation": operation,
            "provider": provider,
        },
        recompute_targets=(operation, prepared.job),
        created=(object_identity(operation),),
        changed=(object_identity(prepared.job),),
    )


def _apply_profile_default_intent(
    operation: Any,
    *,
    prepared: PreparedProfileDefaults,
) -> None:
    operation.Label = prepared.label
    operation.Proxy.init = False
    operation.Side = prepared.cut_side.capitalize()
    operation.CoolantMode = prepared.coolant.capitalize()
    if str(operation.Side).lower() != prepared.cut_side:
        _error(
            "The native Profile rejected cut_side "
            f"{prepared.cut_side!r} during configuration.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )


def create_profile_defaults(
    document: Any,
    *,
    prepared: PreparedProfileDefaults,
) -> NativeMutationDraft:
    """Create Profile with setup defaults plus the required cut-side intent."""

    if not isinstance(prepared, PreparedProfileDefaults):
        raise TypeError("prepared must be a PreparedProfileDefaults")
    from functools import partial

    import Path.Op.Profile as PathProfile

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Profile"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Profile",
        operation_factory=PathProfile.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_profile_default_intent, prepared=prepared),
        payload={
            "parameters": {
                "source": "setup_defaults",
                "cut_side": prepared.cut_side,
            }
        },
    )
    return extend_native_operation_draft(draft, profile_defaults=prepared)


def _base_state(operation: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (
            str(getattr(base, "Name", "") or ""),
            tuple(str(name) for name in subelements),
        )
        for base, subelements in tuple(getattr(operation, "Base", ()) or ())
    )


def _expected_base_state(
    prepared: PreparedProfileCreate,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if prepared.geometry_kind == "entire_job":
        return ()
    return tuple(
        (str(item.job_resource.Name), item.subelements) for item in prepared.geometry
    )


def _quantity_mm(obj: Any, name: str) -> float:
    return round(float(getattr(obj, name).getValueAs("mm")), 9)


def _assert_settings(operation: Any, prepared: PreparedProfileCreate) -> None:
    settings = prepared.settings
    actual = {
        "direction": "clockwise" if str(operation.Direction) == "CW" else "counterclockwise",
        "cut_side": str(operation.Side).lower(),
        "cutter_compensation": bool(operation.UseComp),
        "extra_offset_mm": _quantity_mm(operation, "OffsetExtra"),
        "pass_count": int(operation.NumPasses),
        "stepover_mm": _quantity_mm(operation, "Stepover"),
        "multiple_features": str(operation.HandleMultipleFeatures).lower(),
        "sorting": str(operation.SortingMode).lower(),
        "start_on_longest_edge": bool(operation.UseLongestEdge),
        "profile_outer_perimeter": bool(operation.processPerimeter),
        "profile_noncircular_holes": bool(operation.processHoles),
        "profile_circular_holes": bool(operation.processCircles),
        "start_depth_mm": _quantity_mm(operation, "StartDepth"),
        "final_depth_mm": _quantity_mm(operation, "FinalDepth"),
        "step_down_mm": _quantity_mm(operation, "StepDown"),
        "safe_height_mm": _quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": _quantity_mm(operation, "ClearanceHeight"),
        "coolant": str(operation.CoolantMode).lower(),
    }
    mismatches = {
        name: {"expected": expected, "actual": actual.get(name)}
        for name, expected in settings.items()
        if actual.get(name) != expected
    }
    if bool(operation.UseStartPoint):
        mismatches["use_start_point"] = {"expected": False, "actual": True}
    if mismatches:
        raise NativeManufactureError(
            "The created Profile did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _geometry_summary(prepared: PreparedProfileCreate) -> dict[str, Any]:
    if prepared.geometry_kind == "entire_job":
        return {
            "kind": "entire_job",
            "model_names": [str(item.public_source.Name) for item in prepared.geometry],
        }
    return {
        "kind": "subelements",
        "items": [
            {
                "object_name": str(item.public_source.Name),
                "subelements": list(item.subelements),
            }
            for item in prepared.geometry
        ],
    }


def verify_created_profile(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    import Path.Base.Util as PathUtil

    value = draft.value
    prepared: PreparedProfileCreate = value["prepared"]
    operation = value["operation"]
    provider = value["provider"]
    if tuple(document.Objects) != (*prepared.objects_before, operation):
        _error(
            "Profile creation changed objects outside the exact operation.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    operations_after = tuple(prepared.job.Operations.Group)
    if operations_after != (*prepared.job_operations_before, operation):
        _error(
            "The Profile is not the exact final operation in its CAM Job.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    timeline_after = _timeline_state(document)
    if (
        timeline_after.timeline is not prepared.timeline_before.timeline
        or timeline_after.operations
        != (*prepared.timeline_before.operations, operation)
        or timeline_after.visibility[: len(prepared.timeline_before.visibility)]
        != prepared.timeline_before.visibility
    ):
        _error(
            "The Profile was not published as one exact History operation.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    if document.getObject(str(operation.Name)) is not operation:
        _error(
            "The created Profile is no longer the exact document operation.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    if str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation":
        _error(
            "The created Profile is not marked as a document History operation.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    if PathUtil.timelineParentJob(operation) is not prepared.job:
        _error(
            "The created Profile lost its exact CAM Job parent.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    if operation.ToolController is not prepared.controller:
        _error(
            "The created Profile lost its exact tool controller.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    if operation.ViewObject.Proxy is not provider or bool(provider.deleteOnReject):
        _error(
            "The created Profile did not retain its accepted native view provider.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    if _base_state(operation) != _expected_base_state(prepared):
        _error(
            "The created Profile did not retain the exact requested Job model geometry.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    _assert_settings(operation, prepared)
    for item in prepared.geometry:
        if _shape_sha256(
            item.public_source.Shape,
            f"CAM model {item.public_source.Name}",
        ) != item.shape_sha256:
            _error(
                "Profile creation changed a public model source.",
                "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
            )
    diagnostics = operation.Proxy.getGenerationDiagnostics(operation)
    path = operation.Path
    commands = tuple(getattr(path, "Commands", ()) or ())
    cutting_count = sum(
        1 for command in commands if str(getattr(command, "Name", "")) in {"G1", "G2", "G3"}
    )
    if (
        diagnostics.get("status") != "succeeded"
        or diagnostics.get("stage") != "complete"
        or diagnostics.get("error") is not None
        or int(diagnostics.get("command_count", 0)) != len(commands)
        or cutting_count < 1
    ):
        _error(
            "The created Profile did not produce a valid cutting toolpath.",
            "NATIVE_MANUFACTURE_PROFILE_GENERATION_FAILED",
        )
    if read_current_selection(document) != prepared.selection_before:
        _error(
            "Profile creation changed the human selection.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    state = operation_state(operation)
    job_after = job_state(prepared.job)
    if (
        job_after["counts"]["operations"]
        != prepared.job_before["counts"]["operations"] + 1
        or job_after["models"] != prepared.job_before["models"]
        or job_after["tools"] != prepared.job_before["tools"]
    ):
        _error(
            "Profile creation changed unrelated CAM Job resources.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    if not other_job_states_are_current(document, prepared.other_job_states):
        _error(
            "Profile creation changed another CAM setup.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    return {
        "profile": {
            **state,
            "geometry": _geometry_summary(prepared),
            "parameters": dict(prepared.settings),
            "cutting_command_count": cutting_count,
        },
        "job": {
            "object_name": job_after["object_name"],
            "state_sha256": job_after["state_sha256"],
            "operation_count": job_after["counts"]["operations"],
        },
    }


def _default_profile_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedProfileDefaults,
) -> Mapping[str, Any]:
    actual_cut_side = str(operation.Side).lower()
    if actual_cut_side != prepared.cut_side:
        _error(
            "The created Profile normalized cut_side from "
            f"{prepared.cut_side!r} to {actual_cut_side!r}.",
            "NATIVE_MANUFACTURE_PROFILE_POSTCONDITION_FAILED",
        )
    return {
        "parameters": {
            "source": "setup_defaults",
            "cut_side": prepared.cut_side,
            "direction": str(operation.Direction),
            "start_depth_mm": shared_quantity_mm(operation, "StartDepth"),
            "final_depth_mm": shared_quantity_mm(operation, "FinalDepth"),
            "step_down_mm": shared_quantity_mm(operation, "StepDown"),
            "safe_height_mm": shared_quantity_mm(operation, "SafeHeight"),
            "clearance_height_mm": shared_quantity_mm(operation, "ClearanceHeight"),
            "coolant": str(operation.CoolantMode),
        }
    }


def verify_created_profile_defaults(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    from functools import partial

    prepared: PreparedProfileDefaults = draft.value["profile_defaults"]
    return verify_native_operation(
        document,
        draft,
        result_key="profile",
        assert_settings=lambda _operation, _payload: None,
        additional_verify=partial(_default_profile_result, prepared=prepared),
    )
