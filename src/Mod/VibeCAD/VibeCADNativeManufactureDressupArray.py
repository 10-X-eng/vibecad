# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Array dress-up."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import (
    clean_operation_label,
    exact_fields,
    finite_number,
)
from VibeCADNativeManufactureState import (
    copy_configuration_state,
    job_state,
    operation_state,
    persistent_resource_state,
    resolve_job_target,
    resolve_operation_target,
    tool_controller_state,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


MAX_ARRAY_DRESSUP_PLACEMENTS = 256
MAX_ARRAY_DRESSUP_COMMANDS = 500_000
_TARGET_FIELDS = frozenset({"object_name", "expected_state_sha256"})
_VECTOR_FIELDS = frozenset({"x_mm", "y_mm", "z_mm"})
_EPSILON = 1.0e-9
_CUTTING_COMMANDS = frozenset(
    {"G1", "G2", "G3", "G73", "G74", "G81", "G82", "G83", "G84", "G85"}
)


@dataclass(frozen=True, slots=True)
class ArrayDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    pattern: Mapping[str, Any]
    jitter: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ArrayDressupPattern:
    kind: str
    copies: int
    copies_x: int
    copies_y: int
    offset_mm: tuple[float, float, float]
    total_angle_degrees: float
    centre_mm: tuple[float, float, float]
    first_direction: str
    placement_count: int


@dataclass(frozen=True, slots=True)
class ArrayDressupJitter:
    enabled: bool
    percentage: int
    seed: int
    maximum_offset_mm: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ArrayDressupTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class PreparedArrayDressup:
    label: str
    job: Any
    job_before: Mapping[str, Any]
    base: Any
    base_reference_before: Mapping[str, Any]
    base_state_before: Mapping[str, Any]
    base_configuration_before: Mapping[str, Any]
    base_was_visible: bool
    controller: Any
    controller_before: Mapping[str, Any]
    coolant: str
    pattern: ArrayDressupPattern
    jitter: ArrayDressupJitter
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str
    job_operations_before: tuple[Any, ...]
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Any
    timeline_before: ArrayDressupTimelineState


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _target(value: Any, noun: str) -> Mapping[str, Any]:
    target = exact_fields(value, _TARGET_FIELDS, noun)
    name = str(target["object_name"] or "").strip()
    digest = str(target["expected_state_sha256"] or "").strip()
    if (
        not name
        or len(name) > 128
        or not (name[0].isalpha() or name[0] == "_")
        or any(not (character.isalnum() or character == "_") for character in name)
    ):
        _error(f"{noun} object_name must be one stable document object name.")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        _error(f"{noun} expected_state_sha256 must be one lowercase SHA-256 hash.")
    return {"object_name": name, "expected_state_sha256": digest}


def _integer(value: Any, noun: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _error(f"{noun} must be an integer from {minimum} through {maximum}.")
    if not minimum <= value <= maximum:
        _error(f"{noun} must be from {minimum} through {maximum}.")
    return value


def _vector(
    value: Any,
    noun: str,
    *,
    nonnegative: bool = False,
) -> tuple[float, float, float]:
    vector = exact_fields(value, _VECTOR_FIELDS, noun)
    minimum = 0.0 if nonnegative else -1_000_000.0
    return tuple(
        finite_number(
            vector[f"{axis}_mm"],
            f"{noun} {axis}",
            minimum=minimum,
        )
        for axis in ("x", "y", "z")
    )


def _normalize_pattern(value: Any) -> ArrayDressupPattern:
    if not isinstance(value, Mapping):
        _error("CAM Array dress-up pattern must be one closed pattern request.")
    kind = str(value.get("kind") or "")
    if kind == "linear_1d":
        pattern = exact_fields(
            value,
            frozenset({"kind", "copies", "offset_mm"}),
            "Linear-1D Array dress-up pattern",
        )
        copies = _integer(pattern["copies"], "Linear-1D copies", 1, 99999)
        offset = _vector(pattern["offset_mm"], "Linear-1D offset")
        if all(abs(component) <= _EPSILON for component in offset):
            _error("Linear-1D Array dress-up offset_mm must move each copy.")
        return ArrayDressupPattern(
            kind,
            copies,
            0,
            0,
            offset,
            0.0,
            (0.0, 0.0, 0.0),
            "x",
            copies + 1,
        )
    if kind == "linear_2d":
        pattern = exact_fields(
            value,
            frozenset(
                {"kind", "copies_x", "copies_y", "offset_mm", "first_direction"}
            ),
            "Linear-2D Array dress-up pattern",
        )
        copies_x = _integer(pattern["copies_x"], "Linear-2D copies_x", 0, 99999)
        copies_y = _integer(pattern["copies_y"], "Linear-2D copies_y", 0, 99999)
        if copies_x == 0 and copies_y == 0:
            _error("Linear-2D Array dress-up requires a copy in X or Y.")
        offset = _vector(pattern["offset_mm"], "Linear-2D offset")
        first_direction = str(pattern["first_direction"] or "")
        if first_direction not in {"x", "y"}:
            _error("Linear-2D first_direction must be x or y.")
        x_moves = copies_x > 0 and (
            abs(offset[0]) > _EPSILON
            or (first_direction == "y" and abs(offset[2]) > _EPSILON)
        )
        y_moves = copies_y > 0 and (
            abs(offset[1]) > _EPSILON
            or (first_direction == "x" and abs(offset[2]) > _EPSILON)
        )
        if not x_moves and not y_moves:
            _error("Linear-2D Array dress-up offset_mm must move a requested copy.")
        placements = (copies_x + 1) * (copies_y + 1)
        return ArrayDressupPattern(
            kind,
            0,
            copies_x,
            copies_y,
            offset,
            0.0,
            (0.0, 0.0, 0.0),
            first_direction,
            placements,
        )
    if kind == "polar":
        pattern = exact_fields(
            value,
            frozenset({"kind", "copies", "total_angle_degrees", "centre_mm"}),
            "Polar Array dress-up pattern",
        )
        copies = _integer(pattern["copies"], "Polar copies", 1, 99999)
        angle = finite_number(
            pattern["total_angle_degrees"],
            "Polar total angle",
            minimum=-360_000.0,
            maximum=360_000.0,
        )
        if abs(angle) <= _EPSILON:
            _error("Polar Array dress-up total_angle_degrees must not be zero.")
        return ArrayDressupPattern(
            kind,
            copies,
            0,
            0,
            (0.0, 0.0, 0.0),
            angle,
            _vector(pattern["centre_mm"], "Polar centre"),
            "x",
            copies + 1,
        )
    _error("CAM Array dress-up pattern kind must be linear_1d, linear_2d, or polar.")


def _normalize_jitter(
    value: Any,
    pattern: ArrayDressupPattern,
) -> ArrayDressupJitter:
    if not isinstance(value, Mapping):
        _error("CAM Array dress-up jitter must be one closed jitter request.")
    enabled = value.get("enabled")
    if enabled is False:
        exact_fields(value, frozenset({"enabled"}), "Disabled Array dress-up jitter")
        return ArrayDressupJitter(False, 0, 0, (0.0, 0.0, 0.0))
    if enabled is True:
        jitter = exact_fields(
            value,
            frozenset({"enabled", "percentage", "seed", "maximum_offset_mm"}),
            "Enabled Array dress-up jitter",
        )
        if pattern.kind == "polar":
            _error("The shipped Polar Array dress-up does not apply jitter; disable it.")
        maximum = _vector(
            jitter["maximum_offset_mm"],
            "Array dress-up maximum jitter offset",
            nonnegative=True,
        )
        if all(component <= _EPSILON for component in maximum):
            _error("Enabled Array dress-up jitter requires a nonzero maximum offset.")
        return ArrayDressupJitter(
            True,
            _integer(jitter["percentage"], "Array jitter percentage", 1, 100),
            _integer(jitter["seed"], "Array jitter seed", 1, 2_147_483_647),
            maximum,
        )
    _error("CAM Array dress-up jitter enabled must be true or false.")


def _timeline_state(document: Any) -> ArrayDressupTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != "App::DocumentTimeline":
        _error(
            "CAM Array dress-up requires a valid document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(timeline.Operations or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    position = int(timeline.Position)
    if (
        len(operations) != len(visibility)
        or len(operations) != len(suppression)
        or not 0 <= position <= len(operations)
    ):
        _error(
            "CAM Array dress-up found malformed document History state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return ArrayDressupTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _path_sha256(commands: tuple[Any, ...]) -> str:
    digest = hashlib.sha256()
    for command in commands:
        try:
            encoded = str(command.toGCode()).encode("utf-8")
        except Exception as exc:
            raise NativeManufactureError(
                "The CAM Array dress-up contains an unreadable toolpath command.",
                error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            ) from exc
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _job_invariants(state: Mapping[str, Any]) -> dict[str, Any]:
    counts = dict(state.get("counts", {}))
    counts.pop("operations", None)
    counts.pop("active_operations", None)
    return {
        "object_name": state.get("object_name"),
        "type_id": state.get("type_id"),
        "settings_sha256": state.get("settings_sha256"),
        "models": state.get("models"),
        "tools": state.get("tools"),
        "machine": state.get("machine"),
        "stock": state.get("stock"),
        "postprocessor": state.get("postprocessor"),
        "counts": counts,
    }


def _expected_path(
    base: Any,
    pattern: ArrayDressupPattern,
    jitter: ArrayDressupJitter,
) -> tuple[int, int, str]:
    try:
        import FreeCAD as App
        import Path.Dressup.Array as DressupArray

        generated = DressupArray.PathArray(
            base,
            {"linear_1d": "Linear1D", "linear_2d": "Linear2D", "polar": "Polar"}[
                pattern.kind
            ],
            pattern.copies,
            App.Vector(*pattern.offset_mm),
            pattern.copies_x,
            pattern.copies_y,
            pattern.total_angle_degrees,
            App.Vector(*pattern.centre_mm),
            pattern.first_direction == "x",
            App.Vector(*jitter.maximum_offset_mm),
            jitter.percentage,
            jitter.seed if jitter.enabled else 0,
        ).getPath()
        commands = tuple(generated.Commands or ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM Array dress-up path could not be prepared.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        ) from exc
    cutting = sum(
        1 for command in commands if str(getattr(command, "Name", "")) in _CUTTING_COMMANDS
    )
    return len(commands), cutting, _path_sha256(commands)


def preflight_array_dressup(
    document: Any,
    spec: ArrayDressupSpec,
) -> PreparedArrayDressup:
    """Freeze one exact replacement target and deterministic array path."""

    if not isinstance(spec, ArrayDressupSpec):
        raise TypeError("spec must be an ArrayDressupSpec")
    label = clean_operation_label(spec.label, "CAM Array dress-up")
    pattern = _normalize_pattern(spec.pattern)
    jitter = _normalize_jitter(spec.jitter, pattern)
    if pattern.placement_count > MAX_ARRAY_DRESSUP_PLACEMENTS:
        _error(
            f"CAM Array dress-up would create {pattern.placement_count} placements; "
            f"the safety limit is {MAX_ARRAY_DRESSUP_PLACEMENTS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )

    job, job_before = resolve_job_target(document, _target(spec.job, "Array dress-up job"))
    base_target = _target(spec.base_operation, "Array dress-up base_operation")
    base, base_reference = resolve_operation_target(document, base_target)

    try:
        import Path.Base.Util as PathUtil
        import Path.Dressup.Utils as PathDressup
        import PathScripts.PathUtils as PathUtils
        from Path.CommandBoundary import is_timeline_input_usable

        group = tuple(job.Operations.Group or ())
        valid = (
            base in group
            and PathDressup.isOp(base)
            and base.isValid()
            and is_timeline_input_usable(base, document)
            and PathUtil.activeForOp(base)
            and PathUtils.findParentJob(base) is job
            and bool(tuple(getattr(getattr(base, "Path", None), "Commands", ()) or ()))
        )
        controller = PathUtil.toolControllerForOp(base)
        coolant = str(PathUtil.coolantModeForOp(base))
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM Array dress-up base could not be validated.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if not valid:
        _error(
            "CAM Array dress-up base_operation must be one active, valid, current "
            "operation-group entry in the exact Job with a nonempty path.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "available_operation_names": [str(operation.Name) for operation in group]
            },
        )
    if controller is None or getattr(controller, "Document", None) is not document:
        _error(
            "CAM Array dress-up base_operation requires one current tool controller.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )

    base_commands = tuple(base.Path.Commands or ())
    command_upper_bound = len(base_commands) * pattern.placement_count
    if command_upper_bound > MAX_ARRAY_DRESSUP_COMMANDS:
        _error(
            f"CAM Array dress-up could generate {command_upper_bound} commands; "
            f"the safety limit is {MAX_ARRAY_DRESSUP_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    expected_count, expected_cutting, expected_hash = _expected_path(base, pattern, jitter)
    if expected_count != command_upper_bound or expected_cutting <= 0:
        _error(
            "CAM Array dress-up base did not produce the expected repeated cutting path.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            repair={
                "expected_command_count": command_upper_bound,
                "prepared_command_count": expected_count,
                "prepared_cutting_command_count": expected_cutting,
            },
        )

    return PreparedArrayDressup(
        label=label,
        job=job,
        job_before=job_before,
        base=base,
        base_reference_before=base_reference,
        base_state_before=persistent_resource_state(base),
        base_configuration_before=copy_configuration_state(base, {}),
        base_was_visible=bool(base.ViewObject.Visibility),
        controller=controller,
        controller_before=tool_controller_state(controller),
        coolant=coolant,
        pattern=pattern,
        jitter=jitter,
        expected_command_count=expected_count,
        expected_cutting_count=expected_cutting,
        expected_path_sha256=expected_hash,
        job_operations_before=group,
        objects_before=tuple(document.Objects),
        visibility_before=tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in tuple(document.Objects)
            if getattr(obj, "ViewObject", None) is not None
        ),
        selection_before=read_current_selection(document),
        timeline_before=_timeline_state(document),
    )


def _assert_preflight_current(document: Any, prepared: PreparedArrayDressup) -> None:
    if (
        tuple(document.Objects) != prepared.objects_before
        or read_current_selection(document) != prepared.selection_before
        or _timeline_state(document) != prepared.timeline_before
        or tuple(prepared.job.Operations.Group or ()) != prepared.job_operations_before
        or job_state(prepared.job).get("state_sha256")
        != prepared.job_before.get("state_sha256")
        or operation_state(prepared.base) != prepared.base_reference_before
        or persistent_resource_state(prepared.base) != prepared.base_state_before
        or tool_controller_state(prepared.controller).get("state_sha256")
        != prepared.controller_before.get("state_sha256")
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        _error(
            "The CAM Array dress-up Job, base, History, selection, or visibility "
            "changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def create_array_dressup(
    document: Any,
    *,
    prepared: PreparedArrayDressup,
) -> NativeMutationDraft:
    """Create and configure one replacement dress-up inside the owned transaction."""

    if not isinstance(prepared, PreparedArrayDressup):
        raise TypeError("prepared must be a PreparedArrayDressup")
    _assert_preflight_current(document, prepared)
    try:
        import FreeCAD as App
        import Path.Dressup.Gui.Array as DressupArrayGui

        operation = DressupArrayGui.CreateInTransaction(
            prepared.base,
            hide_base=False,
        )
        pattern = prepared.pattern
        jitter = prepared.jitter
        operation.Label = prepared.label
        operation.Type = {
            "linear_1d": "Linear1D",
            "linear_2d": "Linear2D",
            "polar": "Polar",
        }[pattern.kind]
        operation.Copies = pattern.copies
        operation.CopiesX = pattern.copies_x
        operation.CopiesY = pattern.copies_y
        operation.Offset = App.Vector(*pattern.offset_mm)
        operation.Angle = pattern.total_angle_degrees
        operation.Centre = App.Vector(*pattern.centre_mm)
        operation.SwapDirection = pattern.first_direction == "x"
        operation.JitterPercent = jitter.percentage
        operation.JitterMagnitude = App.Vector(*jitter.maximum_offset_mm)
        operation.JitterSeed = jitter.seed
        if not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(operation):
            raise RuntimeError("The CAM Array dress-up was not enrolled in History")
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
        prepared.base.ViewObject.Visibility = False
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Array dress-up factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "operation": operation},
        recompute_targets=(operation, prepared.job),
        created=(object_identity(operation),),
        changed=(object_identity(prepared.job),),
        replaced=(object_identity(prepared.base),),
    )


def _label_matches(actual: str, requested: str) -> bool:
    if actual == requested:
        return True
    suffix = actual[len(requested) :] if actual.startswith(requested) else ""
    return len(suffix) >= 3 and suffix.isdigit()


def _vector_value(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _angle_degrees(value: Any) -> float:
    getter = getattr(value, "getValueAs", None)
    return round(float(getter("deg") if callable(getter) else value), 9)


def _verify_timeline(
    document: Any,
    prepared: PreparedArrayDressup,
    operation: Any,
) -> None:
    after = _timeline_state(document)
    before = prepared.timeline_before
    expected_operations = (
        *before.operations[: before.position],
        operation,
        *before.operations[before.position :],
    )
    if (
        after.timeline is not before.timeline
        or after.operations != expected_operations
        or after.position != before.position + 1
    ):
        _error(
            "The CAM Array dress-up was not inserted at the exact History marker.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    for old_index, old_operation in enumerate(before.operations):
        new_index = old_index if old_index < before.position else old_index + 1
        expected_visibility = (
            False if old_operation is prepared.base else before.visibility[old_index]
        )
        if (
            after.visibility[new_index] is not expected_visibility
            or after.suppression[new_index] is not before.suppression[old_index]
        ):
            _error(
                "CAM Array dress-up changed unrelated History visibility or suppression.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
                repair={
                    "object_name": str(old_operation.Name),
                    "old_index": old_index,
                    "new_index": new_index,
                    "expected_visibility": expected_visibility,
                    "actual_visibility": after.visibility[new_index],
                    "expected_suppression": before.suppression[old_index],
                    "actual_suppression": after.suppression[new_index],
                    "is_replaced_base": old_operation is prepared.base,
                },
            )
    if not after.visibility[before.position] or after.suppression[before.position]:
        _error(
            "The created CAM Array dress-up is not the active visible History result.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )


def verify_created_array_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact replacement ownership, parameters, path, and durable state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedArrayDressup) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Array dress-up")
    if tuple(document.Objects) != (*prepared.objects_before, operation):
        _error(
            "CAM Array dress-up creation changed objects outside its exact output.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    base_index = prepared.job_operations_before.index(prepared.base)
    expected_group = (
        *prepared.job_operations_before[:base_index],
        operation,
        *prepared.job_operations_before[base_index + 1 :],
    )
    if tuple(prepared.job.Operations.Group or ()) != expected_group:
        _error(
            "The CAM Array dress-up did not replace its exact Job operation entry.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    import Path.Base.Util as PathUtil
    import Path.Dressup.Array as DressupArray
    import Path.Dressup.Gui.Array as DressupArrayGui
    import PathScripts.PathUtils as PathUtils

    pattern = prepared.pattern
    jitter = prepared.jitter
    actual_label = str(operation.Label)
    expected_replacements = (prepared.base,) if prepared.base_was_visible else ()
    if (
        document.getObject(str(operation.Name)) is not operation
        or not operation.isDerivedFrom("Path::Feature")
        or not operation.isValid()
        or not isinstance(getattr(operation, "Proxy", None), DressupArray.DressupArray)
        or not isinstance(
            getattr(getattr(operation, "ViewObject", None), "Proxy", None),
            DressupArrayGui.DressupArrayViewProvider,
        )
        or operation.Base is not prepared.base
        or PathUtils.findParentJob(operation) is not prepared.job
        or PathUtil.timelineParentJob(operation) is not prepared.job
        or str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation"
        or tuple(getattr(operation, "VibeCADTimelineReplacedInputs", ()) or ())
        != expected_replacements
        or PathUtil.toolControllerForOp(operation) is not prepared.controller
        or str(PathUtil.coolantModeForOp(operation)) != prepared.coolant
        or "ToolController" in tuple(operation.PropertiesList)
        or "CoolantMode" in tuple(operation.PropertiesList)
        or not _label_matches(actual_label, prepared.label)
    ):
        _error(
            "The created CAM Array dress-up lost its exact base, Job, resources, or "
            "replacement identity.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if (
        str(operation.Type)
        != {"linear_1d": "Linear1D", "linear_2d": "Linear2D", "polar": "Polar"}[
            pattern.kind
        ]
        or int(operation.Copies) != pattern.copies
        or int(operation.CopiesX) != pattern.copies_x
        or int(operation.CopiesY) != pattern.copies_y
        or _vector_value(operation.Offset) != pattern.offset_mm
        or _angle_degrees(operation.Angle) != pattern.total_angle_degrees
        or _vector_value(operation.Centre) != pattern.centre_mm
        or bool(operation.SwapDirection) is not (pattern.first_direction == "x")
        or int(operation.JitterPercent) != jitter.percentage
        or int(operation.JitterSeed) != jitter.seed
        or _vector_value(operation.JitterMagnitude) != jitter.maximum_offset_mm
    ):
        _error(
            "The created CAM Array dress-up did not retain its exact pattern settings.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    state = operation_state(operation)
    commands = tuple(operation.Path.Commands or ())
    cutting_count = sum(
        1 for command in commands if str(getattr(command, "Name", "")) in _CUTTING_COMMANDS
    )
    if (
        len(commands) != prepared.expected_command_count
        or cutting_count != prepared.expected_cutting_count
        or state.get("path_sha256") != prepared.expected_path_sha256
    ):
        _error(
            "The CAM Array dress-up did not generate the exact deterministic repeated path.",
            "NATIVE_MANUFACTURE_OPERATION_GENERATION_FAILED",
            repair={
                "expected_command_count": prepared.expected_command_count,
                "actual_command_count": len(commands),
                "expected_cutting_command_count": prepared.expected_cutting_count,
                "actual_cutting_command_count": cutting_count,
                "expected_path_sha256": prepared.expected_path_sha256,
                "actual_path_sha256": state.get("path_sha256"),
            },
        )
    base_after = persistent_resource_state(prepared.base)
    base_configuration_after = copy_configuration_state(prepared.base, {})
    if (
        base_configuration_after != prepared.base_configuration_before
        or base_after.get("path_sha256")
        != prepared.base_state_before.get("path_sha256")
        or base_after.get("command_count")
        != prepared.base_state_before.get("command_count")
        or base_after.get("active") is not prepared.base_state_before.get("active")
    ):
        _error(
            "CAM Array dress-up creation changed its retained base operation.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={
                "authored_configuration_matches": (
                    base_configuration_after == prepared.base_configuration_before
                ),
                "expected_path_sha256": prepared.base_state_before.get("path_sha256"),
                "actual_path_sha256": base_after.get("path_sha256"),
                "expected_command_count": prepared.base_state_before.get("command_count"),
                "actual_command_count": base_after.get("command_count"),
                "expected_active": prepared.base_state_before.get("active"),
                "actual_active": base_after.get("active"),
            },
        )
    if tool_controller_state(prepared.controller).get("state_sha256") != (
        prepared.controller_before.get("state_sha256")
    ):
        _error(
            "CAM Array dress-up creation changed its inherited tool controller.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if (
        read_current_selection(document) != prepared.selection_before
        or bool(prepared.base.ViewObject.Visibility)
        or not bool(operation.ViewObject.Visibility)
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
            if obj is not prepared.base
        )
    ):
        _error(
            "CAM Array dress-up changed selection or unrelated existing visibility.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    _verify_timeline(document, prepared, operation)
    after_job = job_state(prepared.job)
    if (
        _job_invariants(after_job) != _job_invariants(prepared.job_before)
        or int(after_job["counts"]["operations"])
        != int(prepared.job_before["counts"]["operations"])
    ):
        _error(
            "CAM Array dress-up changed unrelated Job resources.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "array_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(prepared.job.Name),
        "base_operation_name": str(prepared.base.Name),
        "pattern": pattern.kind,
        "placement_count": pattern.placement_count,
        "jitter_enabled": jitter.enabled,
        "command_count": len(commands),
        "cutting_command_count": cutting_count,
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
