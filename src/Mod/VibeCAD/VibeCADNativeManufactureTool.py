# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native CAM ToolBit and ToolController mutations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import (
    capture_other_job_states,
    job_state,
    other_job_states_are_current,
    resolve_job_target,
    resolve_tool_bit_target,
    resolve_tool_controller_target,
    tool_bit_state,
    tool_controller_state,
)
from VibeCADNativeManufactureToolState import (
    ToolCatalogRecord,
    apply_tool_property_changes,
    instantiate_catalog_tool,
    normalize_tool_property_changes,
    resolve_catalog_record,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


_SPINDLE_DIRECTIONS = frozenset({"Forward", "Reverse", "None"})
_FEED_PROPERTIES = {
    "horizontal_feed_mm_per_minute": "HorizFeed",
    "vertical_feed_mm_per_minute": "VertFeed",
    "ramp_feed_mm_per_minute": "RampFeed",
    "lead_in_feed_mm_per_minute": "LeadInFeed",
    "lead_out_feed_mm_per_minute": "LeadOutFeed",
    "horizontal_rapid_mm_per_minute": "HorizRapid",
    "vertical_rapid_mm_per_minute": "VertRapid",
}


@dataclass(frozen=True, slots=True)
class ToolControllerSettings:
    label: str
    tool_number: Mapping[str, Any]
    tool_length_offset: int
    spindle_speed_rpm: float
    spindle_direction: str
    horizontal_feed_mm_per_minute: float
    vertical_feed_mm_per_minute: float
    ramp_feed_mm_per_minute: float
    lead_in_feed_mm_per_minute: float
    lead_out_feed_mm_per_minute: float
    horizontal_rapid_mm_per_minute: float
    vertical_rapid_mm_per_minute: float


@dataclass(frozen=True, slots=True)
class ToolCatalogTarget:
    catalog_id: str
    expected_content_sha256: str


@dataclass(frozen=True, slots=True)
class ToolControllerCreateSpec:
    job_target: Mapping[str, Any]
    catalog_tool: ToolCatalogTarget
    tool_label: str | None
    tool_property_changes: tuple[Mapping[str, Any], ...]
    controller: ToolControllerSettings | None


@dataclass(frozen=True, slots=True)
class ToolControllerUpdateSpec:
    target: Mapping[str, Any]
    controller: ToolControllerSettings


@dataclass(frozen=True, slots=True)
class ToolBitUpdateSpec:
    target: Mapping[str, Any]
    label: str
    property_changes: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PreparedToolControllerCreate:
    spec: ToolControllerCreateSpec
    job: Any
    job_before: Mapping[str, Any]
    record: ToolCatalogRecord
    detached_toolbit: Any
    tool_number: int
    objects_before: tuple[Any, ...]
    other_job_states: tuple[tuple[Any, str], ...]
    selection_before: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class PreparedToolControllerUpdate:
    spec: ToolControllerUpdateSpec
    controller: Any
    controller_before: Mapping[str, Any]
    job: Any
    job_before: Mapping[str, Any]
    tool_number: int
    objects_before: tuple[Any, ...]
    other_job_states: tuple[tuple[Any, str], ...]
    selection_before: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class PreparedToolBitUpdate:
    spec: ToolBitUpdateSpec
    tool: Any
    tool_before: Mapping[str, Any]
    controllers: tuple[Any, ...]
    controller_states: tuple[Mapping[str, Any], ...]
    jobs: tuple[Any, ...]
    job_states: tuple[Mapping[str, Any], ...]
    visual_resources_before: tuple[Any, ...]
    objects_before: tuple[Any, ...]
    other_job_states: tuple[tuple[Any, str], ...]
    selection_before: tuple[Any, ...]


def _other_job_states(
    document: Any,
    owned_jobs: tuple[Any, ...],
) -> tuple[tuple[Any, str], ...]:
    return capture_other_job_states(document, owned_jobs)


def _label(value: Any, noun: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 160:
        raise NativeManufactureError(
            f"{noun} must contain 1 through 160 characters.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    return clean


def _finite_range(value: Any, noun: str, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            f"{noun} must be numeric.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        ) from exc
    if not math.isfinite(result) or not 0.0 <= result <= maximum:
        raise NativeManufactureError(
            f"{noun} must be finite and between 0 and {maximum:g}.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    return result


def _integer_range(value: Any, noun: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise NativeManufactureError(
            f"{noun} must be an integer from {minimum} through {maximum}.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    return value


def _clean_settings(settings: ToolControllerSettings) -> ToolControllerSettings:
    if not isinstance(settings, ToolControllerSettings):
        raise TypeError("controller settings must be ToolControllerSettings")
    if not isinstance(settings.tool_number, Mapping):
        raise NativeManufactureError(
            "tool_number must select next_available or one explicit number.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    direction = str(settings.spindle_direction or "")
    if direction not in _SPINDLE_DIRECTIONS:
        raise NativeManufactureError(
            "spindle_direction must be Forward, Reverse, or None.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    values = {
        name: _finite_range(getattr(settings, name), name, 1_000_000_000.0)
        for name in _FEED_PROPERTIES
    }
    return ToolControllerSettings(
        label=_label(settings.label, "Tool controller label"),
        tool_number=dict(settings.tool_number),
        tool_length_offset=_integer_range(
            settings.tool_length_offset,
            "tool_length_offset",
            0,
            10_000,
        ),
        spindle_speed_rpm=_finite_range(
            settings.spindle_speed_rpm,
            "spindle_speed_rpm",
            10_000_000.0,
        ),
        spindle_direction=direction,
        **values,
    )


def _resolve_tool_number(job: Any, request: Mapping[str, Any], current: Any = None) -> int:
    used = {
        int(controller.ToolNumber)
        for controller in tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
        if controller is not current
    }
    kind = str(request.get("kind") or "")
    if kind == "next_available" and set(request) == {"kind"}:
        candidate = 1
        while candidate in used:
            candidate += 1
        if candidate > 10_000:
            raise NativeManufactureError(
                "The CAM Job has no available tool number from 1 through 10000.",
                error_code="NATIVE_MANUFACTURE_TOOL_NUMBER_UNAVAILABLE",
            )
        return candidate
    if kind == "explicit" and set(request) == {"kind", "value"}:
        candidate = _integer_range(request.get("value"), "tool number", 1, 10_000)
        if candidate in used:
            raise NativeManufactureError(
                f"CAM tool number {candidate} is already used by this Job.",
                error_code="NATIVE_MANUFACTURE_TOOL_NUMBER_CONFLICT",
                repair={"used_tool_numbers": sorted(used)},
            )
        return candidate
    raise NativeManufactureError(
        "tool_number must be {kind:'next_available'} or {kind:'explicit',value:1..10000}.",
        error_code="NATIVE_ARGUMENTS_INVALID",
    )


def _job_for_controller(document: Any, controller: Any) -> Any:
    return next(
        job
        for job in document.Objects
        if controller in tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
    )


def _apply_controller_settings(
    controller: Any,
    settings: ToolControllerSettings,
    tool_number: int,
) -> None:
    controller.Label = settings.label
    controller.ToolNumber = tool_number
    controller.ToolLengthOffset = settings.tool_length_offset
    controller.SpindleSpeed = settings.spindle_speed_rpm
    controller.SpindleDir = settings.spindle_direction
    for input_name, property_name in _FEED_PROPERTIES.items():
        if property_name in {"RampFeed", "LeadInFeed", "LeadOutFeed"}:
            controller.setExpression(property_name, None)
        setattr(controller, property_name, f"{getattr(settings, input_name)} mm/min")


def preflight_tool_controller_create(
    document: Any,
    spec: ToolControllerCreateSpec,
) -> PreparedToolControllerCreate:
    if not isinstance(spec, ToolControllerCreateSpec):
        raise TypeError("spec must be a ToolControllerCreateSpec")
    job, before = resolve_job_target(document, spec.job_target)
    _catalog, record = resolve_catalog_record(
        spec.catalog_tool.catalog_id,
        spec.catalog_tool.expected_content_sha256,
    )
    settings = _clean_settings(spec.controller) if spec.controller is not None else None
    toolbit = instantiate_catalog_tool(record)
    toolbit.label = _label(spec.tool_label or record.label, "ToolBit label")
    changes = tuple(spec.tool_property_changes)
    if changes:
        apply_tool_property_changes(
            toolbit.obj,
            changes,
            shape=toolbit._tool_bit_shape,
        )
    tool_number = _resolve_tool_number(
        job,
        settings.tool_number if settings is not None else {"kind": "next_available"},
    )
    return PreparedToolControllerCreate(
        spec=ToolControllerCreateSpec(
            job_target=dict(spec.job_target),
            catalog_tool=spec.catalog_tool,
            tool_label=toolbit.label,
            tool_property_changes=changes,
            controller=settings,
        ),
        job=job,
        job_before=before,
        record=record,
        detached_toolbit=toolbit,
        tool_number=tool_number,
        objects_before=tuple(document.Objects),
        other_job_states=_other_job_states(document, (job,)),
        selection_before=read_current_selection(document),
    )


def preflight_tool_controller_update(
    document: Any,
    spec: ToolControllerUpdateSpec,
) -> PreparedToolControllerUpdate:
    if not isinstance(spec, ToolControllerUpdateSpec):
        raise TypeError("spec must be a ToolControllerUpdateSpec")
    settings = _clean_settings(spec.controller)
    controller, before = resolve_tool_controller_target(document, spec.target)
    job = _job_for_controller(document, controller)
    return PreparedToolControllerUpdate(
        spec=ToolControllerUpdateSpec(dict(spec.target), settings),
        controller=controller,
        controller_before=before,
        job=job,
        job_before=job_state(job),
        tool_number=_resolve_tool_number(job, settings.tool_number, controller),
        objects_before=tuple(document.Objects),
        other_job_states=_other_job_states(document, (job,)),
        selection_before=read_current_selection(document),
    )


def preflight_tool_bit_update(
    document: Any,
    spec: ToolBitUpdateSpec,
) -> PreparedToolBitUpdate:
    if not isinstance(spec, ToolBitUpdateSpec):
        raise TypeError("spec must be a ToolBitUpdateSpec")
    tool, before = resolve_tool_bit_target(document, spec.target)
    changes = normalize_tool_property_changes(tool, tuple(spec.property_changes))
    if not changes:
        raise NativeManufactureError(
            "Update at least one ToolBit property.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    controllers = tuple(
        controller
        for job in document.Objects
        for controller in tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
        if getattr(controller, "Tool", None) is tool
    )
    jobs = tuple(dict.fromkeys(_job_for_controller(document, item) for item in controllers))
    if len(jobs) != 1 or getattr(tool, "VibeCADTimelineOwner", None) is not jobs[0]:
        raise NativeManufactureError(
            "A ToolBit property edit requires one exact owning CAM Job.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    try:
        import Path.Base.Util as PathUtil

        visual_resources = tuple(PathUtil.timelineVisualResourceGraph(tool))
    except Exception as exc:
        raise NativeManufactureError(
            "The ToolBit display resource graph could not be read.",
            error_code="NATIVE_MANUFACTURE_TOOL_STATE_INVALID",
        ) from exc
    return PreparedToolBitUpdate(
        spec=ToolBitUpdateSpec(
            target=dict(spec.target),
            label=_label(spec.label, "ToolBit label"),
            property_changes=changes,
        ),
        tool=tool,
        tool_before=before,
        controllers=controllers,
        controller_states=tuple(tool_controller_state(item) for item in controllers),
        jobs=jobs,
        job_states=tuple(job_state(item) for item in jobs),
        visual_resources_before=visual_resources,
        objects_before=tuple(document.Objects),
        other_job_states=_other_job_states(document, jobs),
        selection_before=read_current_selection(document),
    )


def _assert_common_current(document: Any, prepared: Any) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeManufactureError(
            "The CAM document graph changed after preflight.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "The human selection changed after preflight.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if not other_job_states_are_current(document, prepared.other_job_states):
        raise NativeManufactureError(
            "Another CAM setup changed after tool preflight.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )


def create_tool_controller(
    document: Any,
    *,
    prepared: PreparedToolControllerCreate,
) -> NativeMutationDraft:
    _assert_common_current(document, prepared)
    if job_state(prepared.job)["state_sha256"] != prepared.job_before["state_sha256"]:
        raise NativeManufactureError(
            "The CAM Job changed before tool creation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    _catalog, record = resolve_catalog_record(
        prepared.record.catalog_id,
        prepared.record.content_sha256,
    )
    if record != prepared.record:
        raise NativeManufactureError(
            "The selected CAM tool changed before creation.",
            error_code="NATIVE_MANUFACTURE_TOOL_CATALOG_STALE",
        )
    reconciliation = None
    try:
        import Path.Base.Util as PathUtil
        import Path.Tool.Gui.Controller as PathToolControllerGui

        reconciliation = PathUtil.stageTimelineResourceGraphExtension(prepared.job)
        tool = prepared.detached_toolbit.attach_to_doc(
            document,
            label=prepared.spec.tool_label,
            timeline_owner=prepared.job,
        )
        controller = PathToolControllerGui.Create(
            (
                prepared.spec.controller.label
                if prepared.spec.controller is not None
                else f"TC: {prepared.spec.tool_label}"
            ),
            tool,
            prepared.tool_number,
            document=document,
            timelineOwner=prepared.job,
        )
        if prepared.spec.controller is not None:
            _apply_controller_settings(
                controller,
                prepared.spec.controller,
                prepared.tool_number,
            )
        prepared.job.Proxy.addToolController(controller)
        graph = tuple(PathUtil.toolControllerResourceGraph(controller))
        PathUtil.finalizeTimelineResourceGraphExtension(
            prepared.job,
            reconciliation,
            graph,
        )
        tool.Proxy.update_visual_representation_in_place()
        tool.Proxy._suppress_visual_update = True
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM ToolBit and controller graph could not be created.",
            error_code="NATIVE_MANUFACTURE_TOOL_CREATE_FAILED",
        ) from exc
    created_objects = tuple(obj for obj in document.Objects if obj not in prepared.objects_before)
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "controller": controller,
            "tool": tool,
            "graph": graph,
            "created_objects": created_objects,
        },
        recompute_targets=(*graph, prepared.job),
        created=(object_identity(controller),),
        changed=(object_identity(prepared.job),),
    )


def update_tool_controller(
    document: Any,
    *,
    prepared: PreparedToolControllerUpdate,
) -> NativeMutationDraft:
    _assert_common_current(document, prepared)
    if (
        tool_controller_state(prepared.controller)["state_sha256"]
        != prepared.controller_before["state_sha256"]
        or job_state(prepared.job)["state_sha256"] != prepared.job_before["state_sha256"]
    ):
        raise NativeManufactureError(
            "The CAM tool controller changed before update.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    _apply_controller_settings(
        prepared.controller,
        prepared.spec.controller,
        prepared.tool_number,
    )
    operations = tuple(getattr(getattr(prepared.job, "Operations", None), "Group", ()) or ())
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(prepared.controller, *operations, prepared.job),
        changed=(object_identity(prepared.controller),),
    )


def update_tool_bit(
    document: Any,
    *,
    prepared: PreparedToolBitUpdate,
) -> NativeMutationDraft:
    _assert_common_current(document, prepared)
    if tool_bit_state(prepared.tool)["state_sha256"] != prepared.tool_before["state_sha256"]:
        raise NativeManufactureError(
            "The CAM ToolBit changed before update.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    for controller, before in zip(prepared.controllers, prepared.controller_states):
        if tool_controller_state(controller)["state_sha256"] != before["state_sha256"]:
            raise NativeManufactureError(
                "A ToolBit controller changed before update.",
                error_code="NATIVE_MANUFACTURE_STATE_STALE",
            )
    try:
        import Path.Base.Util as PathUtil

        proxy = prepared.tool.Proxy
        update_visual = getattr(proxy, "update_visual_representation_in_place", None)
        if not callable(update_visual):
            raise RuntimeError("The CAM ToolBit cannot update its display body in place")
        was_suppressed = bool(getattr(proxy, "_suppress_visual_update", False))
        proxy._suppress_visual_update = True
        try:
            prepared.tool.Label = prepared.spec.label
            apply_tool_property_changes(prepared.tool, prepared.spec.property_changes)
            update_visual()
        finally:
            proxy._suppress_visual_update = was_suppressed
        visual_after = tuple(PathUtil.timelineVisualResourceGraph(prepared.tool))
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The ToolBit property and display-resource edit could not be applied.",
            error_code="NATIVE_MANUFACTURE_TOOL_UPDATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    operations = tuple(
        operation
        for job in prepared.jobs
        for operation in tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "visual_resources_after": visual_after,
        },
        recompute_targets=(prepared.tool, *prepared.controllers, *operations, *prepared.jobs),
        changed=(object_identity(prepared.tool),),
    )


def _assert_no_graph_or_selection_change(document: Any, prepared: Any) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeManufactureError(
            "The CAM settings update changed the document graph.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "The CAM settings update changed the human selection.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
        )
    if not other_job_states_are_current(document, prepared.other_job_states):
        raise NativeManufactureError(
            "The CAM tool edit changed another setup.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
        )


def _job_receipt(job: Any) -> dict[str, Any]:
    state = job_state(job)
    return {
        "object_name": state["object_name"],
        "state_sha256": state["state_sha256"],
        "tool_count": state["counts"]["tools"],
    }


def verify_created_tool_controller(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value
    prepared: PreparedToolControllerCreate = value["prepared"]
    controller = value["controller"]
    tool = value["tool"]
    graph = tuple(value["graph"])
    created_objects = tuple(value["created_objects"])
    current_created_objects = tuple(
        obj for obj in document.Objects if obj not in prepared.objects_before
    )
    if current_created_objects != created_objects:
        raise NativeManufactureError(
            "Tool creation changed objects outside its exact graph.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
            repair={
                "mutation_object_names": [
                    str(obj.Name) for obj in created_objects[:16]
                ],
                "verified_object_names": [
                    str(obj.Name) for obj in current_created_objects[:16]
                ],
            },
        )
    if (
        not graph
        or graph[0] is not controller
        or controller.Tool is not tool
        or controller not in tuple(prepared.job.Tools.Group)
        or any(
            str(getattr(resource, "VibeCADTimelineRole", "") or "") != "resource"
            or getattr(resource, "VibeCADTimelineOwner", None) is not prepared.job
            for resource in graph
        )
    ):
        raise NativeManufactureError(
            "The ToolBit/controller resource graph is incomplete.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
        )
    implicit = set()
    for resource in graph:
        origin = getattr(resource, "Origin", None)
        if origin is not None:
            implicit.add(origin)
            implicit.update(tuple(getattr(origin, "OriginFeatures", ()) or ()))
    unexpected = tuple(
        obj
        for obj in created_objects
        if obj not in graph and obj not in implicit
    )
    if unexpected:
        raise NativeManufactureError(
            "Tool creation produced an object outside its exact Job graph.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
            repair={
                "unexpected_object_names": [
                    str(obj.Name) for obj in unexpected[:16]
                ],
            },
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "Tool creation changed the human selection.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
        )
    tool.Proxy._suppress_visual_update = False
    if not other_job_states_are_current(document, prepared.other_job_states):
        raise NativeManufactureError(
            "Tool creation changed another CAM setup.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
        )
    state = tool_controller_state(controller)
    job_after = job_state(prepared.job)
    if (
        state["tool_number"] != prepared.tool_number
        or state["tool"]["object_name"] != str(tool.Name)
        or job_after["counts"]["tools"] != prepared.job_before["counts"]["tools"] + 1
    ):
        raise NativeManufactureError(
            "The created CAM controller failed its exact postcondition.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
        )
    return {
        "controller": state,
        "catalog_tool": prepared.record.summary(),
        "job": _job_receipt(prepared.job),
        "resource_count": len(graph),
    }


def verify_updated_tool_controller(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedToolControllerUpdate = draft.value["prepared"]
    _assert_no_graph_or_selection_change(document, prepared)
    state = tool_controller_state(prepared.controller)
    if state["state_sha256"] == prepared.controller_before["state_sha256"]:
        raise NativeManufactureError(
            "The CAM controller update made no durable change.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    return {"controller": state, "job": _job_receipt(prepared.job)}


def verify_updated_tool_bit(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedToolBitUpdate = draft.value["prepared"]
    _assert_no_graph_or_selection_change(document, prepared)
    visual_after = tuple(draft.value["visual_resources_after"])
    if visual_after != prepared.visual_resources_before:
        raise NativeManufactureError(
            "The CAM ToolBit update replaced part of its stable display graph.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
        )
    if any(
        str(getattr(resource, "VibeCADTimelineRole", "") or "") != "resource"
        or getattr(resource, "VibeCADTimelineOwner", None) is not prepared.jobs[0]
        for resource in visual_after
    ):
        raise NativeManufactureError(
            "The updated ToolBit display graph is not owned by its CAM Job.",
            error_code="NATIVE_MANUFACTURE_TOOL_POSTCONDITION_FAILED",
        )
    state = tool_bit_state(prepared.tool)
    if state["state_sha256"] == prepared.tool_before["state_sha256"]:
        raise NativeManufactureError(
            "The CAM ToolBit update made no durable change.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    return {
        "tool": state,
        "controllers": [tool_controller_state(item) for item in prepared.controllers],
        "jobs": [_job_receipt(job) for job in prepared.jobs],
    }
