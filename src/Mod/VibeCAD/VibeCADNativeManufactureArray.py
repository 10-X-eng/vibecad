# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped parametric CAM Array operation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import (
    clean_operation_label,
    exact_fields,
    finite_number,
    shape_sha256,
)
from VibeCADNativeManufactureState import (
    candidate_model_state,
    job_state,
    operation_state,
    persistent_resource_state,
    resolve_job_target,
    resolve_model_target,
    resolve_operation_target,
    tool_controller_state,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


MAX_ARRAY_BASES = 64
MAX_ARRAY_POINT_SOURCES = 32
MAX_ARRAY_POINT_SUBELEMENTS = 64
MAX_ARRAY_POINT_CANDIDATES = 256
MAX_ARRAY_COMMANDS = 500_000
_TARGET_FIELDS = frozenset({"object_name", "expected_state_sha256"})
_ARRAY_FIELDS = frozenset(
    {"label", "job", "base_operations", "pattern", "reverse_direction", "jitter"}
)
_VECTOR_FIELDS = frozenset({"x_mm", "y_mm", "z_mm"})
_POINT_SOURCE_FIELDS = frozenset({"model", "subelements"})
_SUBELEMENT_NAME = re.compile(r"^(Vertex|Edge|Face)([1-9][0-9]*)$")
_EPSILON = 1.0e-9


@dataclass(frozen=True, slots=True)
class ArrayCreateSpec:
    label: Any
    job: Mapping[str, Any]
    base_operations: Any
    pattern: Mapping[str, Any]
    reverse_direction: Any
    jitter: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ArrayPattern:
    kind: str
    copies: int
    copies_x: int
    copies_y: int
    offset_mm: tuple[float, float, float]
    total_angle_degrees: float
    centre_mm: tuple[float, float, float]
    first_direction: str
    sorting: str


@dataclass(frozen=True, slots=True)
class ArrayJitter:
    enabled: bool
    seed: int
    maximum_offset_mm: tuple[float, float, float]
    maximum_rotation_degrees: float


@dataclass(frozen=True, slots=True)
class PreparedPointSelection:
    model: Any
    state: Mapping[str, Any]
    shape_sha256: str
    subelements: tuple[str, ...]
    element_sha256: tuple[str, ...]
    candidate_count: int


@dataclass(frozen=True, slots=True)
class PreparedPointOrigin:
    kind: str
    selection: PreparedPointSelection | None


@dataclass(frozen=True, slots=True)
class ArrayTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class PreparedArrayCreate:
    label: str
    job: Any
    job_before: Mapping[str, Any]
    bases: tuple[Any, ...]
    base_reference_before: tuple[Mapping[str, Any], ...]
    base_state_before: tuple[Mapping[str, Any], ...]
    controller: Any
    controller_before: Mapping[str, Any]
    pattern: ArrayPattern
    reverse_direction: bool
    jitter: ArrayJitter
    point_sources: tuple[PreparedPointSelection, ...]
    point_origin: PreparedPointOrigin
    repeat_upper_bound: int
    base_command_count: int
    base_cutting_count: int
    job_operations_before: tuple[Any, ...]
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Any
    timeline_before: ArrayTimelineState


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _boolean(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{noun} must be true or false.")
    return value


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
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        _error(f"{noun} expected_state_sha256 must be one lowercase SHA-256 hash.")
    return {"object_name": name, "expected_state_sha256": digest}


def _normalize_pattern(value: Any) -> ArrayPattern:
    if not isinstance(value, Mapping):
        _error("CAM Array pattern must be one closed pattern request.")
    kind = str(value.get("kind") or "")
    if kind == "linear_1d":
        pattern = exact_fields(
            value,
            frozenset({"kind", "copies", "offset_mm"}),
            "Linear-1D Array pattern",
        )
        offset = _vector(pattern["offset_mm"], "Linear-1D Array offset")
        if all(abs(component) <= _EPSILON for component in offset):
            _error("Linear-1D Array offset_mm must move each copy.")
        return ArrayPattern(
            kind=kind,
            copies=_integer(pattern["copies"], "Linear-1D Array copies", 1, 99999),
            copies_x=0,
            copies_y=0,
            offset_mm=offset,
            total_angle_degrees=0.0,
            centre_mm=(0.0, 0.0, 0.0),
            first_direction="x",
            sorting="automatic",
        )
    if kind == "linear_2d":
        pattern = exact_fields(
            value,
            frozenset(
                {"kind", "copies_x", "copies_y", "offset_mm", "first_direction"}
            ),
            "Linear-2D Array pattern",
        )
        copies_x = _integer(pattern["copies_x"], "Linear-2D Array copies_x", 0, 99999)
        copies_y = _integer(pattern["copies_y"], "Linear-2D Array copies_y", 0, 99999)
        if copies_x == 0 and copies_y == 0:
            _error("Linear-2D Array requires at least one copy in X or Y.")
        offset = _vector(pattern["offset_mm"], "Linear-2D Array offset")
        first_direction = str(pattern["first_direction"] or "")
        if first_direction not in {"x", "y"}:
            _error("Linear-2D Array first_direction must be x or y.")
        x_moves = copies_x > 0 and (
            abs(offset[0]) > _EPSILON
            or (first_direction == "y" and abs(offset[2]) > _EPSILON)
        )
        y_moves = copies_y > 0 and (
            abs(offset[1]) > _EPSILON
            or (first_direction == "x" and abs(offset[2]) > _EPSILON)
        )
        if not x_moves and not y_moves:
            _error("Linear-2D Array offset_mm must move at least one requested copy.")
        return ArrayPattern(
            kind=kind,
            copies=0,
            copies_x=copies_x,
            copies_y=copies_y,
            offset_mm=offset,
            total_angle_degrees=0.0,
            centre_mm=(0.0, 0.0, 0.0),
            first_direction=first_direction,
            sorting="automatic",
        )
    if kind == "polar":
        pattern = exact_fields(
            value,
            frozenset({"kind", "copies", "total_angle_degrees", "centre_mm"}),
            "Polar Array pattern",
        )
        angle = finite_number(
            pattern["total_angle_degrees"],
            "Polar Array total angle",
            minimum=-360_000.0,
            maximum=360_000.0,
        )
        if abs(angle) <= _EPSILON:
            _error("Polar Array total_angle_degrees must not be zero.")
        return ArrayPattern(
            kind=kind,
            copies=_integer(pattern["copies"], "Polar Array copies", 1, 99999),
            copies_x=0,
            copies_y=0,
            offset_mm=(0.0, 0.0, 0.0),
            total_angle_degrees=angle,
            centre_mm=_vector(pattern["centre_mm"], "Polar Array centre"),
            first_direction="x",
            sorting="automatic",
        )
    if kind == "points":
        pattern = exact_fields(
            value,
            frozenset({"kind", "sources", "origin", "sorting"}),
            "Points Array pattern",
        )
        sources = pattern["sources"]
        if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_ARRAY_POINT_SOURCES:
            _error(
                f"Points Array sources must contain one through {MAX_ARRAY_POINT_SOURCES} entries."
            )
        sorting = str(pattern["sorting"] or "")
        if sorting not in {"automatic", "manual"}:
            _error("Points Array sorting must be automatic or manual.")
        return ArrayPattern(
            kind=kind,
            copies=0,
            copies_x=0,
            copies_y=0,
            offset_mm=(0.0, 0.0, 0.0),
            total_angle_degrees=0.0,
            centre_mm=(0.0, 0.0, 0.0),
            first_direction="x",
            sorting=sorting,
        )
    _error("CAM Array pattern kind must be linear_1d, linear_2d, polar, or points.")


def _normalize_jitter(value: Any) -> ArrayJitter:
    if not isinstance(value, Mapping):
        _error("CAM Array jitter must be one closed jitter request.")
    enabled = value.get("enabled")
    if enabled is False:
        exact_fields(value, frozenset({"enabled"}), "Disabled Array jitter")
        return ArrayJitter(False, 0, (0.0, 0.0, 0.0), 0.0)
    if enabled is True:
        jitter = exact_fields(
            value,
            frozenset(
                {"enabled", "seed", "maximum_offset_mm", "maximum_rotation_degrees"}
            ),
            "Enabled Array jitter",
        )
        offset = _vector(
            jitter["maximum_offset_mm"],
            "Array jitter maximum offset",
            nonnegative=True,
        )
        angle = finite_number(
            jitter["maximum_rotation_degrees"],
            "Array jitter maximum rotation",
            minimum=0.0,
            maximum=360.0,
        )
        if all(abs(component) <= _EPSILON for component in offset) and angle <= _EPSILON:
            _error("Enabled Array jitter requires a nonzero offset or rotation limit.")
        return ArrayJitter(
            True,
            _integer(jitter["seed"], "Array jitter seed", 0, 2_147_483_647),
            offset,
            angle,
        )
    _error("CAM Array jitter enabled must be true or false.")


def _timeline_state(document: Any) -> ArrayTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != "App::DocumentTimeline":
        _error(
            "CAM Array requires a valid document History.",
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
            "CAM Array found malformed document History state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return ArrayTimelineState(timeline, operations, visibility, suppression, position)


def _resolve_base_target(
    document: Any,
    value: Any,
    index: int,
) -> tuple[Any, Mapping[str, Any]]:
    target = _target(value, f"base_operations[{index}]")
    return resolve_operation_target(document, target)


def _point_selection(
    document: Any,
    value: Any,
    noun: str,
    *,
    allow_multiple: bool,
) -> PreparedPointSelection:
    item = exact_fields(value, _POINT_SOURCE_FIELDS, noun)
    model_target = _target(item["model"], f"{noun} model")
    model, state = resolve_model_target(document, model_target)
    raw_names = item["subelements"]
    if not isinstance(raw_names, list) or len(raw_names) > MAX_ARRAY_POINT_SUBELEMENTS:
        _error(
            f"{noun} subelements must contain zero through {MAX_ARRAY_POINT_SUBELEMENTS} names."
        )
    names = tuple(str(name or "") for name in raw_names)
    if len(set(names)) != len(names):
        _error(f"{noun} subelements must be distinct.")
    if not allow_multiple and len(names) > 1:
        _error(f"{noun} accepts at most one subelement.")
    shape = model.Shape
    element_hashes: list[str] = []
    if names:
        for name in names:
            match = _SUBELEMENT_NAME.fullmatch(name)
            if match is None:
                _error(f"{noun} accepts only exact VertexN, EdgeN, or FaceN names.")
            try:
                element = shape.getElement(name)
            except Exception as exc:
                raise NativeManufactureError(
                    f"{noun} {model.Name}.{name} no longer exists.",
                    error_code="NATIVE_MANUFACTURE_TARGET_STALE",
                ) from exc
            if str(getattr(element, "ShapeType", "")) != match.group(1):
                _error(
                    f"{noun} {model.Name}.{name} has the wrong shape type.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
            if not tuple(getattr(element, "Vertexes", ()) or ()):
                _error(
                    f"{noun} {model.Name}.{name} has no placement vertex.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
            element_hashes.append(shape_sha256(element, f"{noun} {name}"))
        count = len(names)
    else:
        vertices = tuple(getattr(shape, "Vertexes", ()) or ())
        edges = tuple(getattr(shape, "Edges", ()) or ())
        if not vertices or (edges and not tuple(getattr(edges[0], "Vertexes", ()) or ())):
            _error(
                f"{noun} whole source has no placement vertex.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        count = 1 if edges else len(vertices)
    return PreparedPointSelection(
        model=model,
        state=state,
        shape_sha256=shape_sha256(shape, f"{noun} model"),
        subelements=names,
        element_sha256=tuple(element_hashes),
        candidate_count=count,
    )


def _prepare_points(
    document: Any,
    request: Mapping[str, Any],
) -> tuple[tuple[PreparedPointSelection, ...], PreparedPointOrigin, int]:
    sources = tuple(
        _point_selection(
            document,
            value,
            f"Points Array source {index}",
            allow_multiple=True,
        )
        for index, value in enumerate(request["sources"])
    )
    names = tuple(str(source.model.Name) for source in sources)
    if len(set(names)) != len(names):
        _error("Each Points Array source object may appear only once.")
    candidate_count = sum(source.candidate_count for source in sources)
    if candidate_count > MAX_ARRAY_POINT_CANDIDATES:
        _error(
            f"Points Array resolves to more than {MAX_ARRAY_POINT_CANDIDATES} candidate placements.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
            repair={"candidate_placement_count": candidate_count},
        )

    origin_request = request["origin"]
    if not isinstance(origin_request, Mapping):
        _error("Points Array origin must be one closed origin request.")
    origin_kind = str(origin_request.get("kind") or "")
    if origin_kind == "global":
        exact_fields(origin_request, frozenset({"kind"}), "Global Points Array origin")
        origin = PreparedPointOrigin("global", None)
    elif origin_kind == "whole_source":
        origin_value = exact_fields(
            origin_request,
            frozenset({"kind", "model"}),
            "Whole-source Points Array origin",
        )
        origin = PreparedPointOrigin(
            origin_kind,
            _point_selection(
                document,
                {"model": origin_value["model"], "subelements": []},
                "Points Array origin",
                allow_multiple=False,
            ),
        )
    elif origin_kind == "subelement":
        origin_value = exact_fields(
            origin_request,
            frozenset({"kind", "model", "subelement"}),
            "Subelement Points Array origin",
        )
        origin = PreparedPointOrigin(
            origin_kind,
            _point_selection(
                document,
                {
                    "model": origin_value["model"],
                    "subelements": [origin_value["subelement"]],
                },
                "Points Array origin",
                allow_multiple=False,
            ),
        )
    else:
        _error("Points Array origin kind must be global, whole_source, or subelement.")
    return sources, origin, candidate_count


def _point_request(pattern: Mapping[str, Any]) -> Mapping[str, Any]:
    if str(pattern.get("kind") or "") != "points":
        return {"sources": (), "origin": {"kind": "global"}}
    return pattern


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


def preflight_array_create(document: Any, spec: ArrayCreateSpec) -> PreparedArrayCreate:
    """Freeze exact bases, pattern references, Job state, and workload."""

    if not isinstance(spec, ArrayCreateSpec):
        raise TypeError("spec must be an ArrayCreateSpec")
    job_target = _target(spec.job, "CAM Array job")
    job, job_before = resolve_job_target(document, job_target)
    group = tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
    if len(group) > MAX_ARRAY_BASES:
        _error(
            f"CAM Array cannot address a Job with more than {MAX_ARRAY_BASES} operations.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    raw_bases = spec.base_operations
    if not isinstance(raw_bases, list) or not 1 <= len(raw_bases) <= MAX_ARRAY_BASES:
        _error(f"base_operations must contain one through {MAX_ARRAY_BASES} exact operations.")
    resolved = tuple(
        _resolve_base_target(document, target, index)
        for index, target in enumerate(raw_bases)
    )
    bases = tuple(value[0] for value in resolved)
    base_reference = tuple(value[1] for value in resolved)
    names = tuple(str(base.Name) for base in bases)
    if len(set(names)) != len(names):
        _error("CAM Array base_operations must be distinct.")
    if any(base not in group for base in bases):
        _error(
            "Every CAM Array base must be an exact operation-group entry in the target Job.",
            "NATIVE_MANUFACTURE_TARGET_STALE",
            repair={"available_operation_names": [str(value.Name) for value in group]},
        )

    import Path.Base.Util as PathUtil
    import Path.Dressup.Utils as PathDressup
    from Path.CommandBoundary import is_timeline_input_usable

    controllers = tuple(PathDressup.toolController(base) for base in bases)
    controller = controllers[0]
    if (
        controller is None
        or any(value is not controller for value in controllers)
        or getattr(controller, "Document", None) is not document
        or not is_timeline_input_usable(controller, document)
    ):
        _error(
            "CAM Array bases must share one current tool controller.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    for base in bases:
        commands = tuple(getattr(getattr(base, "Path", None), "Commands", ()) or ())
        if (
            not base.isDerivedFrom("Path::Feature")
            or not base.isValid()
            or not PathUtil.activeForOp(base)
            or not is_timeline_input_usable(base, document)
            or not commands
        ):
            _error(
                f"CAM Array base {base.Name!r} is not one active current toolpath.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        if PathUtil.coolantModeForOp(base) != "None":
            _error(
                f"CAM Array base {base.Name!r} must use coolant mode None.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )

    pattern = _normalize_pattern(spec.pattern)
    point_sources: tuple[PreparedPointSelection, ...] = ()
    point_origin = PreparedPointOrigin("global", None)
    if pattern.kind == "points":
        point_sources, point_origin, repeat_upper_bound = _prepare_points(
            document,
            _point_request(spec.pattern),
        )
    elif pattern.kind == "linear_2d":
        repeat_upper_bound = (pattern.copies_x + 1) * (pattern.copies_y + 1) - 1
    else:
        repeat_upper_bound = pattern.copies

    base_states = tuple(persistent_resource_state(base) for base in bases)
    base_command_count = sum(
        len(tuple(getattr(getattr(base, "Path", None), "Commands", ()) or ()))
        for base in bases
    )
    base_cutting_count = sum(
        sum(
            1
            for command in tuple(base.Path.Commands or ())
            if str(getattr(command, "Name", "")) in {"G1", "G2", "G3"}
        )
        for base in bases
    )
    if base_command_count <= 0 or base_cutting_count <= 0:
        _error(
            "CAM Array bases must contain at least one cutting command.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    command_upper_bound = base_command_count * repeat_upper_bound
    if command_upper_bound > MAX_ARRAY_COMMANDS:
        _error(
            f"CAM Array could generate {command_upper_bound:,} commands, above the synchronous "
            f"limit of {MAX_ARRAY_COMMANDS:,}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
            repair={
                "base_command_count": base_command_count,
                "repeat_upper_bound": repeat_upper_bound,
                "command_upper_bound": command_upper_bound,
            },
        )
    return PreparedArrayCreate(
        label=clean_operation_label(spec.label, "CAM Array"),
        job=job,
        job_before=job_before,
        bases=bases,
        base_reference_before=base_reference,
        base_state_before=base_states,
        controller=controller,
        controller_before=tool_controller_state(controller),
        pattern=pattern,
        reverse_direction=_boolean(spec.reverse_direction, "CAM Array reverse_direction"),
        jitter=_normalize_jitter(spec.jitter),
        point_sources=point_sources,
        point_origin=point_origin,
        repeat_upper_bound=repeat_upper_bound,
        base_command_count=base_command_count,
        base_cutting_count=base_cutting_count,
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


def _assert_point_selection_current(value: PreparedPointSelection, noun: str) -> None:
    model = value.model
    document = model.Document
    if (
        document.getObject(str(model.Name)) is not model
        or candidate_model_state(model).get("state_sha256") != value.state.get("state_sha256")
        or shape_sha256(model.Shape, f"{noun} model") != value.shape_sha256
    ):
        _error(f"{noun} changed after preflight.", "NATIVE_MANUFACTURE_STATE_STALE")
    hashes = tuple(
        shape_sha256(model.Shape.getElement(name), f"{noun} {name}")
        for name in value.subelements
    )
    if hashes != value.element_sha256:
        _error(f"{noun} subelements changed after preflight.", "NATIVE_MANUFACTURE_STATE_STALE")


def _assert_preflight_current(document: Any, prepared: PreparedArrayCreate) -> None:
    if (
        tuple(document.Objects) != prepared.objects_before
        or read_current_selection(document) != prepared.selection_before
        or _timeline_state(document) != prepared.timeline_before
        or tuple(prepared.job.Operations.Group or ()) != prepared.job_operations_before
        or job_state(prepared.job).get("state_sha256")
        != prepared.job_before.get("state_sha256")
        or tool_controller_state(prepared.controller).get("state_sha256")
        != prepared.controller_before.get("state_sha256")
        or tuple(operation_state(base) for base in prepared.bases)
        != prepared.base_reference_before
        or tuple(persistent_resource_state(base) for base in prepared.bases)
        != prepared.base_state_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        _error(
            "The CAM Array Job, bases, History, selection, or visibility changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    for index, source in enumerate(prepared.point_sources):
        _assert_point_selection_current(source, f"Points Array source {index}")
    if prepared.point_origin.selection is not None:
        _assert_point_selection_current(prepared.point_origin.selection, "Points Array origin")


def _apply_settings(operation: Any, prepared: PreparedArrayCreate) -> None:
    import FreeCAD as App

    pattern = prepared.pattern
    jitter = prepared.jitter
    operation.Label = prepared.label
    operation.Active = True
    operation.ToolController = prepared.controller
    operation.Type = {
        "linear_1d": "Linear1D",
        "linear_2d": "Linear2D",
        "polar": "Polar",
        "points": "Points",
    }[pattern.kind]
    operation.Copies = pattern.copies
    operation.CopiesX = pattern.copies_x
    operation.CopiesY = pattern.copies_y
    operation.Offset = App.Vector(*pattern.offset_mm)
    operation.Angle = pattern.total_angle_degrees
    operation.Centre = App.Vector(*pattern.centre_mm)
    operation.SwapDirection = pattern.first_direction == "x"
    operation.ReverseDirection = prepared.reverse_direction
    operation.PointsSorting = "Automatic" if pattern.sorting == "automatic" else "Manual"
    operation.PointsSource = [
        (
            source.model,
            list(source.subelements) if source.subelements else [""],
        )
        for source in prepared.point_sources
    ]
    if prepared.point_origin.selection is None:
        operation.PointsOrigin = None
    else:
        origin = prepared.point_origin.selection
        operation.PointsOrigin = (
            origin.model,
            list(origin.subelements) if origin.subelements else [""],
        )
    operation.UseJitter = jitter.enabled
    operation.JitterSeed = jitter.seed
    operation.JitterMagnitude = App.Vector(*jitter.maximum_offset_mm)
    operation.JitterAngle = jitter.maximum_rotation_degrees


def create_array(
    document: Any,
    *,
    prepared: PreparedArrayCreate,
) -> NativeMutationDraft:
    """Create one exact source-preserving CAM Array in the owned transaction."""

    if not isinstance(prepared, PreparedArrayCreate):
        raise TypeError("prepared must be a PreparedArrayCreate")
    _assert_preflight_current(document, prepared)
    try:
        import Path.Op.Gui.Array as PathArrayGui

        operation = PathArrayGui.Create(
            prepared.label,
            prepared.bases,
            prepared.job,
        )
        _apply_settings(operation, prepared)
        for existing, visible in prepared.visibility_before:
            if bool(existing.ViewObject.Visibility) is not visible:
                existing.ViewObject.Visibility = visible
        if not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(operation):
            raise RuntimeError("The CAM Array was not provisionally enrolled in History")
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Array factory could not create the requested operation.",
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
    )


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _link_sub_state(value: Any) -> tuple[str, tuple[str, ...]] | None:
    if not value:
        return None
    obj, names = value
    if obj is None:
        return None
    cleaned = tuple(str(name) for name in names if str(name))
    return str(obj.Name), cleaned


def _link_sub_list_state(value: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (str(obj.Name), tuple(str(name) for name in names if str(name)))
        for obj, names in tuple(value or ())
    )


def _label_matches_requested(actual: str, requested: str) -> bool:
    if actual == requested:
        return True
    suffix = actual[len(requested) :] if actual.startswith(requested) else ""
    return len(suffix) >= 3 and suffix.isdigit()


def _assert_settings(operation: Any, prepared: PreparedArrayCreate) -> None:
    pattern = prepared.pattern
    jitter = prepared.jitter
    expected_sources = tuple(
        (str(source.model.Name), source.subelements) for source in prepared.point_sources
    )
    expected_origin = (
        None
        if prepared.point_origin.selection is None
        else (
            str(prepared.point_origin.selection.model.Name),
            prepared.point_origin.selection.subelements,
        )
    )
    actual = {
        "active": bool(operation.Active),
        "type": str(operation.Type),
        "copies": int(operation.Copies),
        "copies_x": int(operation.CopiesX),
        "copies_y": int(operation.CopiesY),
        "offset_mm": _vector_tuple(operation.Offset),
        "angle_degrees": round(float(operation.Angle.Value), 9),
        "centre_mm": _vector_tuple(operation.Centre),
        "first_direction": "x" if bool(operation.SwapDirection) else "y",
        "reverse_direction": bool(operation.ReverseDirection),
        "sorting": str(operation.PointsSorting),
        "sources": _link_sub_list_state(operation.PointsSource),
        "origin": _link_sub_state(operation.PointsOrigin),
        "jitter_enabled": bool(operation.UseJitter),
        "jitter_seed": int(operation.JitterSeed),
        "jitter_offset_mm": _vector_tuple(operation.JitterMagnitude),
        "jitter_angle_degrees": round(float(operation.JitterAngle.Value), 9),
    }
    expected = {
        "active": True,
        "type": {
            "linear_1d": "Linear1D",
            "linear_2d": "Linear2D",
            "polar": "Polar",
            "points": "Points",
        }[pattern.kind],
        "copies": pattern.copies,
        "copies_x": pattern.copies_x,
        "copies_y": pattern.copies_y,
        "offset_mm": pattern.offset_mm,
        "angle_degrees": pattern.total_angle_degrees,
        "centre_mm": pattern.centre_mm,
        "first_direction": pattern.first_direction,
        "reverse_direction": prepared.reverse_direction,
        "sorting": "Automatic" if pattern.sorting == "automatic" else "Manual",
        "sources": expected_sources,
        "origin": expected_origin,
        "jitter_enabled": jitter.enabled,
        "jitter_seed": jitter.seed,
        "jitter_offset_mm": jitter.maximum_offset_mm,
        "jitter_angle_degrees": jitter.maximum_rotation_degrees,
    }
    mismatches = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in expected.items()
        if actual.get(key) != value
    }
    actual_label = str(operation.Label)
    if not _label_matches_requested(actual_label, prepared.label):
        mismatches["label"] = {
            "expected": prepared.label,
            "actual": actual_label,
        }
    if tuple(operation.Base or ()) != prepared.bases:
        mismatches["base_operations"] = {
            "expected": [str(value.Name) for value in prepared.bases],
            "actual": [str(value.Name) for value in tuple(operation.Base or ())],
        }
    if operation.ToolController is not prepared.controller:
        mismatches["tool_controller"] = {
            "expected": str(prepared.controller.Name),
            "actual": str(getattr(getattr(operation, "ToolController", None), "Name", "")),
        }
    if mismatches:
        _error(
            "The created CAM Array did not retain its exact requested settings.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={"mismatches": mismatches},
        )


def _verify_timeline(document: Any, prepared: PreparedArrayCreate, operation: Any) -> None:
    after = _timeline_state(document)
    before = prepared.timeline_before
    expected = (
        *before.operations[: before.position],
        operation,
        *before.operations[before.position :],
    )
    if (
        after.timeline is not before.timeline
        or after.operations != expected
        or after.position != before.position + 1
    ):
        _error(
            "The CAM Array was not inserted as one exact operation at the History marker.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    for old_index in range(len(before.operations)):
        new_index = old_index if old_index < before.position else old_index + 1
        if (
            after.visibility[new_index] is not before.visibility[old_index]
            or after.suppression[new_index] is not before.suppression[old_index]
        ):
            _error(
                "CAM Array creation changed existing History visibility or suppression.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
            )
    if after.suppression[before.position]:
        _error(
            "The created CAM Array was unexpectedly suppressed.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )


def _pattern_payload(prepared: PreparedArrayCreate, repeat_count: int) -> dict[str, Any]:
    pattern = prepared.pattern
    result: dict[str, Any] = {"kind": pattern.kind}
    if pattern.kind == "linear_1d":
        result.update(copies=pattern.copies, offset_mm=list(pattern.offset_mm))
    elif pattern.kind == "linear_2d":
        result.update(
            copies_x=pattern.copies_x,
            copies_y=pattern.copies_y,
            offset_mm=list(pattern.offset_mm),
            first_direction=pattern.first_direction,
        )
    elif pattern.kind == "polar":
        result.update(
            copies=pattern.copies,
            total_angle_degrees=pattern.total_angle_degrees,
            centre_mm=list(pattern.centre_mm),
        )
    else:
        result.update(
            source_count=len(prepared.point_sources),
            candidate_placement_count=prepared.repeat_upper_bound,
            sorting=pattern.sorting,
            origin_kind=prepared.point_origin.kind,
        )
    result["repeat_count"] = repeat_count
    return result


def verify_created_array(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    """Prove exact ownership, source preservation, settings, and generated work."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedArrayCreate) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Array")
    if tuple(document.Objects) != (*prepared.objects_before, operation):
        _error(
            "CAM Array creation changed objects outside the exact output operation.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if tuple(prepared.job.Operations.Group or ()) != (
        *prepared.job_operations_before,
        operation,
    ):
        _error(
            "The CAM Array is not the exact final operation in its Job group.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if (
        document.getObject(str(operation.Name)) is not operation
        or not operation.isDerivedFrom("Path::Feature")
        or not operation.isValid()
        or str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation"
    ):
        _error(
            "The created CAM Array is not one valid exact History operation.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    import Path.Base.Util as PathUtil
    import Path.Op.Gui.Array as PathArrayGui
    import PathScripts.PathUtils as PathUtils

    if (
        not isinstance(getattr(operation, "Proxy", None), PathArrayGui.ObjectArray)
        or not isinstance(
            getattr(getattr(operation, "ViewObject", None), "Proxy", None),
            PathArrayGui.ViewProviderArray,
        )
        or PathUtils.findParentJob(operation) is not prepared.job
        or PathUtil.timelineParentJob(operation) is not prepared.job
    ):
        _error(
            "The created CAM Array lost its native proxy, view provider, or Job parent.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    _assert_settings(operation, prepared)
    _verify_timeline(document, prepared, operation)

    if (
        tuple(persistent_resource_state(base) for base in prepared.bases)
        != prepared.base_state_before
    ):
        _error(
            "CAM Array creation changed one of its source toolpaths.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    for index, source in enumerate(prepared.point_sources):
        _assert_point_selection_current(source, f"Points Array source {index}")
    if prepared.point_origin.selection is not None:
        _assert_point_selection_current(prepared.point_origin.selection, "Points Array origin")
    if (
        read_current_selection(document) != prepared.selection_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        _error(
            "CAM Array creation changed the human selection or existing visibility.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    commands = tuple(getattr(getattr(operation, "Path", None), "Commands", ()) or ())
    command_count = len(commands)
    cutting_count = sum(
        1
        for command in commands
        if str(getattr(command, "Name", "")) in {"G1", "G2", "G3"}
    )
    if (
        command_count <= 0
        or command_count % prepared.base_command_count != 0
        or cutting_count % prepared.base_cutting_count != 0
    ):
        _error(
            "The CAM Array did not produce complete repeated source toolpaths.",
            "NATIVE_MANUFACTURE_OPERATION_GENERATION_FAILED",
            repair={
                "command_count": command_count,
                "cutting_command_count": cutting_count,
                "base_command_count": prepared.base_command_count,
                "base_cutting_command_count": prepared.base_cutting_count,
            },
        )
    repeat_count = command_count // prepared.base_command_count
    if (
        cutting_count != prepared.base_cutting_count * repeat_count
        or repeat_count < 1
        or repeat_count > prepared.repeat_upper_bound
        or (prepared.pattern.kind != "points" and repeat_count != prepared.repeat_upper_bound)
    ):
        _error(
            "The CAM Array generated the wrong number of complete repeats.",
            "NATIVE_MANUFACTURE_OPERATION_GENERATION_FAILED",
            repair={
                "expected_repeat_count": (
                    f"1 through {prepared.repeat_upper_bound}"
                    if prepared.pattern.kind == "points"
                    else prepared.repeat_upper_bound
                ),
                "actual_repeat_count": repeat_count,
            },
        )

    after_job = job_state(prepared.job)
    if (
        _job_invariants(after_job) != _job_invariants(prepared.job_before)
        or int(after_job["counts"]["operations"])
        != int(prepared.job_before["counts"]["operations"]) + 1
    ):
        _error(
            "CAM Array creation changed unrelated Job resources.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    state = operation_state(operation)
    return {
        "operation": "array",
        "object_name": str(operation.Name),
        "label": str(operation.Label)[:160],
        "job_object_name": str(prepared.job.Name),
        "base_operation_names": [str(base.Name) for base in prepared.bases],
        "tool_controller_name": str(prepared.controller.Name),
        "pattern": _pattern_payload(prepared, repeat_count),
        "reverse_direction": prepared.reverse_direction,
        "jitter_enabled": prepared.jitter.enabled,
        "command_count": command_count,
        "cutting_command_count": cutting_count,
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
