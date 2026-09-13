# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact boundary for task-free native CAM operation creation."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field, replace
import hashlib
import math
import re
from typing import Any, Callable, Mapping

from VibeCADNativeManufactureContract import clean_path_operation_label
from VibeCADNativeManufactureErrors import NativeManufactureError
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


MAX_OPERATION_GEOMETRY_ITEMS = 32
MAX_OPERATION_SUBELEMENTS = 64
LINKING_STRATEGIES = {
    "clearance_height": "Clearance Height",
    "retract_height": "Retract Height",
    "line_of_sight": "Line of Sight",
    "tool_diameter": "Tool Diameter",
    "tool_shape": "Tool Shape",
}
_SUBELEMENT_NAME = re.compile(r"^(Face|Edge|Vertex)([1-9][0-9]*)$")
_CANONICAL_BREP_TOLERANCE_MM = 1.0e-7


@dataclass(frozen=True, slots=True)
class PreparedOperationGeometry:
    public_source: Any
    job_resource: Any
    source_state_sha256: str
    source_shape: Any = field(repr=False, compare=False)
    shape_sha256: str
    subelements: tuple[str, ...]
    element_sha256: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperationTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class PreparedOperationBoundary:
    noun: str
    job: Any
    job_before: Mapping[str, Any]
    controller: Any
    controller_before: Mapping[str, Any]
    geometry_kind: str
    geometry: tuple[PreparedOperationGeometry, ...]
    selected_types: frozenset[str]
    job_operations_before: tuple[Any, ...]
    other_job_states: tuple[tuple[Any, str], ...]
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Any
    timeline_before: OperationTimelineState


OperationFactory = Callable[..., Any]
ProviderFactory = Callable[[Any, Any], Any]
OperationConfigurator = Callable[[Any], None]
OperationVerifier = Callable[[Any, Mapping[str, Any]], None]
AdditionalVerifier = Callable[[Any, Mapping[str, Any]], Mapping[str, Any] | None]


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def clean_operation_label(value: Any, noun: str) -> str:
    return clean_path_operation_label(value, noun)


def finite_number(
    value: Any,
    noun: str,
    *,
    minimum: float = -1_000_000.0,
    maximum: float = 1_000_000.0,
) -> float:
    if isinstance(value, bool):
        _error(f"{noun} must be one finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError):
        _error(f"{noun} must be one finite number.")
    if not math.isfinite(result):
        _error(f"{noun} must be one finite number.")
    if not minimum <= result <= maximum:
        _error(f"{noun} must be between {minimum:g} and {maximum:g}.")
    return round(result, 9)


def exact_fields(value: Any, fields: frozenset[str], noun: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _error(f"{noun} must contain exactly: {', '.join(sorted(fields))}.")
    return value


def merge_subelement_geometry_items(
    raw_items: Any,
    *,
    noun: str,
    max_items: int,
    max_subelements: int,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Return the canonical per-model union of exact subelement selections."""
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= max_items:
        _error(f"{noun} subelements geometry requires 1 through {max_items} model items.")
    order: list[str] = []
    grouped: dict[str, tuple[str, list[str], set[str]]] = {}
    total = 0
    for item in raw_items:
        if not isinstance(item, Mapping) or set(item) != {"model", "subelements"}:
            _error(f"Each {noun} geometry item requires model and subelements.")
        target = item["model"]
        if not isinstance(target, Mapping) or set(target) != {
            "object_name",
            "expected_state_sha256",
        }:
            _error(f"Each {noun} model requires one exact state target.")
        name = str(target.get("object_name") or "")
        expected = str(target.get("expected_state_sha256") or "")
        raw_names = item["subelements"]
        if not isinstance(raw_names, list) or not raw_names:
            _error(f"Each {noun} model requires at least one subelement.")
        if name not in grouped:
            order.append(name)
            grouped[name] = (expected, [], set())
        current_expected, names, seen_names = grouped[name]
        if expected != current_expected:
            _error(f"{noun} model {name!r} has conflicting exact states.")
        for value in raw_names:
            subelement = str(value)
            if subelement in seen_names:
                continue
            total += 1
            if total > max_subelements:
                _error(
                    f"A {noun} request accepts at most {max_subelements} total subelements."
                )
            names.append(subelement)
            seen_names.add(subelement)
    return tuple(
        (name, grouped[name][0], tuple(grouped[name][1])) for name in order
    )


def clear_operation_expressions(
    operation: Any, property_names: tuple[str, ...]
) -> None:
    for property_name in property_names:
        try:
            operation.setExpression(property_name, None)
        except Exception as exc:
            raise NativeManufactureError(
                f"The CAM operation could not take manual control of {property_name}.",
                error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            ) from exc


def quantity_mm(operation: Any, property_name: str) -> float:
    return round(float(getattr(operation, property_name).getValueAs("mm")), 9)


def validate_operation_tool(
    prepared: PreparedOperationBoundary,
    *,
    require_shape: bool = False,
) -> float:
    """Return the positive cutter diameter after validating required resources."""

    if not isinstance(prepared, PreparedOperationBoundary):
        raise TypeError("prepared must be a PreparedOperationBoundary")
    if not isinstance(require_shape, bool):
        raise TypeError("require_shape must be a bool")
    tool = getattr(prepared.controller, "Tool", None)
    diameter = getattr(getattr(tool, "Diameter", None), "Value", None)
    try:
        result = float(diameter)
    except (TypeError, ValueError):
        result = 0.0
    if not math.isfinite(result) or result <= 0.0:
        _error(
            f"{prepared.noun} requires a tool controller with a positive tool diameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if require_shape:
        body = getattr(tool, "BitBody", None)
        shape = getattr(body, "Shape", None)
        if shape is None or bool(getattr(shape, "isNull", lambda: True)()):
            _error(
                f"{prepared.noun} tool_shape linking requires a controller whose "
                "ToolBit has a valid solid BitBody shape.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
    return round(result, 9)


def validate_operation_tool_linking(
    prepared: PreparedOperationBoundary,
    strategy: str,
) -> float:
    """Validate the cutter resources consumed by one linking strategy."""

    if strategy not in LINKING_STRATEGIES:
        raise ValueError("strategy is not a supported CAM linking strategy")
    return validate_operation_tool(
        prepared,
        require_shape=strategy == "tool_shape",
    )


def has_prior_cutting_operation(prepared: PreparedOperationBoundary) -> bool:
    """Return whether the frozen Job prefix contains an active cutting path."""

    if not isinstance(prepared, PreparedOperationBoundary):
        raise TypeError("prepared must be a PreparedOperationBoundary")
    return any(
        bool(getattr(operation, "Active", True))
        and any(
            str(getattr(command, "Name", "")) in {"G1", "G2", "G3"}
            for command in tuple(
                getattr(getattr(operation, "Path", None), "Commands", ()) or ()
            )
        )
        for operation in prepared.job_operations_before
    )


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _timeline_state(document: Any) -> OperationTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if (
        timeline is None
        or str(getattr(timeline, "TypeId", "")) != "App::DocumentTimeline"
    ):
        _error(
            "A CAM operation requires a valid document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    if len(operations) != len(visibility):
        _error(
            "The document History is malformed.", "NATIVE_MANUFACTURE_HISTORY_INVALID"
        )
    return OperationTimelineState(timeline, operations, visibility)


def shape_sha256(shape: Any, noun: str) -> str:
    export = getattr(shape, "exportBrepToString", None)
    if shape is None or not callable(export):
        _error(
            f"The exact {noun} has no serializable Part geometry.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    try:
        content = export()
        encoded = (
            content if isinstance(content, bytes) else str(content).encode("utf-8")
        )
    except Exception as exc:
        raise NativeManufactureError(
            f"The exact {noun} geometry could not be fingerprinted.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _canonical_geometry_sha256(shape: Any, noun: str) -> str:
    """Hash geometry while excluding non-geometric OCCT tolerance drift."""

    copy_shape = getattr(shape, "copy", None)
    if not callable(copy_shape):
        return shape_sha256(shape, noun)
    try:
        canonical = copy_shape()
        fix_tolerance = getattr(canonical, "fixTolerance", None)
        if callable(fix_tolerance):
            fix_tolerance(_CANONICAL_BREP_TOLERANCE_MM)
    except Exception as exc:
        raise NativeManufactureError(
            f"The exact {noun} geometry could not be canonicalized.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        ) from exc
    return shape_sha256(canonical, noun)


def _public_shape_is_unchanged(
    actual_shape: Any,
    frozen_shape: Any,
    frozen_shape_sha256: str,
    noun: str,
) -> tuple[bool, str]:
    """Compare exact geometry when OCC returns a fresh equivalent shape identity."""

    same_shape = getattr(actual_shape, "isSame", None)
    if callable(same_shape) and same_shape(frozen_shape):
        return True, frozen_shape_sha256
    actual_shape_sha256 = shape_sha256(actual_shape, noun)
    if actual_shape_sha256 == frozen_shape_sha256:
        return True, actual_shape_sha256
    unchanged = _canonical_geometry_sha256(
        actual_shape,
        noun,
    ) == _canonical_geometry_sha256(frozen_shape, noun)
    return unchanged, actual_shape_sha256


def _job_resources_are_unchanged(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    """Compare durable Job model/tool fingerprints, not recompute bookkeeping."""

    def fingerprints(state: Mapping[str, Any], name: str, key: str) -> tuple[str, ...] | None:
        values = state.get(name)
        if not isinstance(values, list):
            return None
        result = tuple(
            str(value.get(key) or "") if isinstance(value, Mapping) else ""
            for value in values
        )
        return result if all(result) else None

    return all(
        frozen is not None and frozen == current
        for frozen, current in (
            (
                fingerprints(before, "models", "resource_state_sha256"),
                fingerprints(after, "models", "resource_state_sha256"),
            ),
            (
                fingerprints(before, "tools", "state_sha256"),
                fingerprints(after, "tools", "state_sha256"),
            ),
        )
    )


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


def _validate_subelement(
    source: Any,
    name: str,
    allowed_types: frozenset[str],
    noun: str,
) -> tuple[str, str]:
    match = _SUBELEMENT_NAME.fullmatch(str(name or ""))
    if not match or match.group(1) not in allowed_types:
        accepted = ", ".join(value + "N" for value in sorted(allowed_types))
        _error(f"{noun} geometry accepts only exact {accepted} names.")
    try:
        element = source.Shape.getElement(name)
    except Exception as exc:
        raise NativeManufactureError(
            f"{noun} geometry {source.Name}.{name} no longer exists.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        ) from exc
    expected_type = match.group(1)
    if str(getattr(element, "ShapeType", "")) != expected_type:
        _error(
            f"{noun} geometry {source.Name}.{name} is not a {expected_type.lower()}.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return expected_type, shape_sha256(element, f"{noun} subelement {name}")


def _prepare_geometry(
    document: Any,
    job: Any,
    job_before: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    noun: str,
    allowed_types: frozenset[str],
    allow_entire_job: bool,
) -> tuple[
    str,
    tuple[PreparedOperationGeometry, ...],
    frozenset[str],
]:
    models = _job_model_map(job)
    model_states = {
        str(item.get("object_name") or ""): str(item.get("state_sha256") or "")
        for item in job_before.get("models", ())
    }
    if not isinstance(request, Mapping):
        _error(f"{noun} geometry must be one closed geometry request.")
    kind = str(request.get("kind") or "")
    if kind == "entire_job":
        if not allow_entire_job or set(request) != {"kind"}:
            _error(f"{noun} does not accept this entire_job geometry request.")
        prepared = tuple(
            PreparedOperationGeometry(
                public_source=public,
                job_resource=resource,
                source_state_sha256=model_states.get(name, ""),
                source_shape=public.Shape,
                shape_sha256=shape_sha256(public.Shape, f"CAM model {name}"),
                subelements=(),
                element_sha256=(),
            )
            for name, (public, resource) in models.items()
        )
        if not prepared:
            _error(
                f"The exact CAM Job has no model for {noun}.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        return kind, prepared, frozenset()
    if kind == "whole_models":
        if set(request) != {"kind", "models"}:
            _error(f"{noun} whole_models geometry must contain exactly kind and models.")
        raw_targets = request.get("models")
        if (
            not isinstance(raw_targets, list)
            or not 1 <= len(raw_targets) <= MAX_OPERATION_GEOMETRY_ITEMS
        ):
            _error(f"{noun} whole_models geometry requires 1 through 32 exact models.")
        prepared_items = []
        seen_models: set[str] = set()
        for target in raw_targets:
            if not isinstance(target, Mapping) or set(target) != {
                "object_name",
                "expected_state_sha256",
            }:
                _error(f"Each {noun} whole model requires one exact state target.")
            name = str(target.get("object_name") or "")
            expected = str(target.get("expected_state_sha256") or "")
            if name in seen_models or name not in models:
                _error(
                    f"{noun} whole models must be distinct public sources owned by the exact Job.",
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
            prepared_items.append(
                PreparedOperationGeometry(
                    public_source=public,
                    job_resource=resource,
                    source_state_sha256=expected,
                    source_shape=public.Shape,
                    shape_sha256=shape_sha256(public.Shape, f"CAM model {name}"),
                    subelements=(),
                    element_sha256=(),
                )
            )
            seen_models.add(name)
        return kind, tuple(prepared_items), frozenset()
    if kind != "subelements" or set(request) != {"kind", "items"}:
        _error(
            f"{noun} geometry must be entire_job, whole_models, or a closed "
            "subelements request."
        )
    grouped_items = merge_subelement_geometry_items(
        request.get("items"),
        noun=noun,
        max_items=MAX_OPERATION_GEOMETRY_ITEMS,
        max_subelements=MAX_OPERATION_SUBELEMENTS,
    )
    prepared_items = []
    selected_types: set[str] = set()
    for name, expected, names in grouped_items:
        if name not in models:
            _error(
                f"{noun} model {name!r} is not a public source owned by the exact Job.",
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
            element_type, element_hash = _validate_subelement(
                public,
                subelement,
                allowed_types,
                noun,
            )
            selected_types.add(element_type)
            element_hashes.append(element_hash)
        prepared_items.append(
            PreparedOperationGeometry(
                public_source=public,
                job_resource=resource,
                source_state_sha256=expected,
                source_shape=public.Shape,
                shape_sha256=shape_sha256(public.Shape, f"CAM model {name}"),
                subelements=names,
                element_sha256=tuple(element_hashes),
            )
        )
    return kind, tuple(prepared_items), frozenset(selected_types)


def preflight_operation_boundary(
    document: Any,
    *,
    noun: str,
    job_target: Mapping[str, Any],
    tool_controller_target: Mapping[str, Any],
    geometry: Mapping[str, Any],
    allowed_subelement_types: frozenset[str],
    allow_entire_job: bool = True,
) -> PreparedOperationBoundary:
    clean_noun = str(noun or "").strip()
    if not clean_noun:
        raise ValueError("noun must not be empty")
    allowed = frozenset(str(value) for value in allowed_subelement_types)
    if not allowed or not allowed.issubset({"Face", "Edge", "Vertex"}):
        raise ValueError("allowed_subelement_types is invalid")
    if _transaction_open(document):
        _error(
            f"Finish or cancel the open task before creating {clean_noun}.",
            "NATIVE_TRANSACTION_ACTIVE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            f"Wait for document recompute to finish before creating {clean_noun}.",
            "NATIVE_MANUFACTURE_RECOMPUTE_ACTIVE",
        )
    job, before = resolve_job_target(document, job_target)
    controller, controller_before = resolve_tool_controller_target(
        document,
        tool_controller_target,
    )
    if controller not in tuple(
        getattr(getattr(job, "Tools", None), "Group", ()) or ()
    ) or controller_before.get("job_name") != str(job.Name):
        _error(
            "The exact tool controller is not owned by the exact CAM Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    geometry_kind, prepared_geometry, selected_types = _prepare_geometry(
        document,
        job,
        before,
        geometry,
        noun=clean_noun,
        allowed_types=allowed,
        allow_entire_job=allow_entire_job,
    )
    return PreparedOperationBoundary(
        noun=clean_noun,
        job=job,
        job_before=before,
        controller=controller,
        controller_before=controller_before,
        geometry_kind=geometry_kind,
        geometry=prepared_geometry,
        selected_types=selected_types,
        job_operations_before=tuple(
            getattr(getattr(job, "Operations", None), "Group", ()) or ()
        ),
        other_job_states=capture_other_job_states(document, (job,)),
        objects_before=tuple(document.Objects),
        visibility_before=tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in tuple(document.Objects)
            if getattr(obj, "ViewObject", None) is not None
        ),
        selection_before=read_current_selection(document),
        timeline_before=_timeline_state(document),
    )


def preflight_operation_without_geometry(
    document: Any,
    *,
    noun: str,
    job_target: Mapping[str, Any],
    tool_controller_target: Mapping[str, Any],
) -> PreparedOperationBoundary:
    """Freeze a Job operation whose human command accepts explicit coordinates."""

    prepared = preflight_operation_boundary(
        document,
        noun=noun,
        job_target=job_target,
        tool_controller_target=tool_controller_target,
        geometry={"kind": "entire_job"},
        allowed_subelement_types=frozenset({"Face", "Edge", "Vertex"}),
        allow_entire_job=True,
    )
    return replace(prepared, geometry_kind="custom_points")


def assert_operation_boundary_current(
    document: Any,
    prepared: PreparedOperationBoundary,
) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        _error(
            f"The CAM document graph changed before {prepared.noun} creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if any(
        bool(obj.ViewObject.Visibility) is not visible
        for obj, visible in prepared.visibility_before
    ):
        _error(
            f"Document visibility changed before {prepared.noun} creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if read_current_selection(document) != prepared.selection_before:
        _error(
            f"The human selection changed before {prepared.noun} creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if _timeline_state(document) != prepared.timeline_before:
        _error(
            f"Document History changed before {prepared.noun} creation.",
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
            f"The CAM Job or controller changed before {prepared.noun} creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    if not other_job_states_are_current(document, prepared.other_job_states):
        _error(
            f"Another CAM setup changed before {prepared.noun} creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    for item in prepared.geometry:
        if (
            document.getObject(str(item.public_source.Name)) is not item.public_source
            or shape_sha256(
                item.public_source.Shape,
                f"CAM model {item.public_source.Name}",
            )
            != item.shape_sha256
        ):
            _error(
                f"CAM model {item.public_source.Name!r} changed before {prepared.noun} creation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        hashes = tuple(
            _validate_subelement(
                item.public_source,
                name,
                frozenset({_SUBELEMENT_NAME.fullmatch(name).group(1)}),
                prepared.noun,
            )[1]
            for name in item.subelements
        )
        if hashes != item.element_sha256:
            _error(
                f"Selected geometry on {item.public_source.Name!r} changed before {prepared.noun} creation.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )


def _add_base_geometry(operation: Any, prepared: PreparedOperationBoundary) -> None:
    if prepared.geometry_kind != "subelements":
        return
    for item in prepared.geometry:
        for subelement in item.subelements:
            operation.Proxy.addBase(operation, item.public_source, subelement)


def native_operation_presentation(
    module_name: str,
) -> tuple[ProviderFactory | None, Any | None]:
    """Resolve GUI presentation only when this process owns a GUI."""

    import FreeCAD as App

    clean_name = str(module_name or "").strip()
    if not clean_name.startswith("Path.Op.Gui."):
        raise ValueError("A CAM presentation module must be in Path.Op.Gui.")
    if not App.GuiUp:
        return None, None
    module = importlib.import_module(clean_name)
    return module.PathOpGui.ViewProvider, module.Command.res


def create_native_operation(
    document: Any,
    *,
    prepared: PreparedOperationBoundary,
    internal_name: str,
    operation_factory: OperationFactory,
    provider_factory: ProviderFactory | None,
    provider_resource: Any | None,
    configure: OperationConfigurator,
    payload: Mapping[str, Any],
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedOperationBoundary):
        raise TypeError("prepared must be a PreparedOperationBoundary")
    assert_operation_boundary_current(document, prepared)
    try:
        operation = operation_factory(
            internal_name,
            parentJob=prepared.job,
            toolController=prepared.controller,
        )
        _add_base_geometry(operation, prepared)
        configure(operation)
        view = getattr(operation, "ViewObject", None)
        if view is None:
            if provider_factory is not None or provider_resource is not None:
                raise RuntimeError(
                    "A headless CAM operation cannot receive GUI presentation."
                )
            provider = None
        else:
            if provider_factory is None or provider_resource is None:
                raise RuntimeError(
                    "A GUI CAM operation requires its native view provider."
                )
            provider = provider_factory(view, provider_resource)
            view.Proxy = provider
            provider.deleteOnReject = False
            view.Visibility = True
        for existing, visible in prepared.visibility_before:
            if bool(existing.ViewObject.Visibility) is not visible:
                existing.ViewObject.Visibility = visible
        if not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(
            operation
        ):
            raise RuntimeError(
                f"The {prepared.noun} was not provisionally enrolled in document History"
            )
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            f"The native CAM factory could not create {prepared.noun}.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
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
            "payload": dict(payload),
        },
        recompute_targets=(operation, prepared.job),
        created=(object_identity(operation),),
        changed=(object_identity(prepared.job),),
    )


def extend_native_operation_draft(
    draft: NativeMutationDraft,
    **private_values: Any,
) -> NativeMutationDraft:
    """Attach verifier-only state without changing a mutation draft's receipt."""

    if not isinstance(draft, NativeMutationDraft):
        raise TypeError("draft must be a NativeMutationDraft")
    value = dict(draft.value)
    overlap = set(value).intersection(private_values)
    if overlap:
        raise ValueError(
            "Native operation draft values already exist: " + ", ".join(sorted(overlap))
        )
    value.update(private_values)
    return NativeMutationDraft(
        value=value,
        recompute_targets=draft.recompute_targets,
        created=draft.created,
        changed=draft.changed,
        deleted=draft.deleted,
        replaced=draft.replaced,
    )


def _base_state(operation: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (
            str(getattr(base, "Name", "") or ""),
            tuple(str(name) for name in subelements),
        )
        for base, subelements in tuple(getattr(operation, "Base", ()) or ())
    )


def _expected_base_state(
    prepared: PreparedOperationBoundary,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if prepared.geometry_kind in {"entire_job", "whole_models", "custom_points"}:
        return ()
    return tuple(
        (str(item.job_resource.Name), item.subelements) for item in prepared.geometry
    )


def operation_geometry_summary(prepared: PreparedOperationBoundary) -> dict[str, Any]:
    if prepared.geometry_kind == "custom_points":
        return {"kind": "custom_points"}
    if prepared.geometry_kind == "entire_job":
        return {
            "kind": "entire_job",
            "model_names": [str(item.public_source.Name) for item in prepared.geometry],
        }
    if prepared.geometry_kind == "whole_models":
        return {
            "kind": "whole_models",
            "model_names": [
                str(item.public_source.Name) for item in prepared.geometry
            ],
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


def verify_native_operation(
    document: Any,
    draft: NativeMutationDraft,
    *,
    result_key: str,
    assert_settings: OperationVerifier,
    additional_verify: AdditionalVerifier | None = None,
    minimum_cutting_commands: int = 1,
    cutting_command_names: frozenset[str] = frozenset({"G1", "G2", "G3"}),
) -> dict[str, Any]:
    if not isinstance(minimum_cutting_commands, int) or minimum_cutting_commands < 0:
        raise ValueError("minimum_cutting_commands must be a nonnegative integer")
    if (
        not isinstance(cutting_command_names, frozenset)
        or not cutting_command_names
        or any(not isinstance(name, str) or not name for name in cutting_command_names)
    ):
        raise ValueError("cutting_command_names must be nonempty command names")
    import Path.Base.Util as PathUtil

    value = draft.value
    prepared: PreparedOperationBoundary = value["prepared"]
    operation = value["operation"]
    provider = value["provider"]
    payload: Mapping[str, Any] = value["payload"]
    if tuple(document.Objects) != (*prepared.objects_before, operation):
        _error(
            f"{prepared.noun} creation changed objects outside the exact operation.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if tuple(prepared.job.Operations.Group) != (
        *prepared.job_operations_before,
        operation,
    ):
        _error(
            f"The {prepared.noun} is not the exact final operation in its CAM Job.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
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
            f"The {prepared.noun} was not published as one exact History operation.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    if document.getObject(str(operation.Name)) is not operation:
        _error(
            f"The created {prepared.noun} is no longer the exact document operation.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation":
        _error(
            f"The created {prepared.noun} is not marked as a History operation.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if PathUtil.timelineParentJob(operation) is not prepared.job:
        _error(
            f"The created {prepared.noun} lost its exact CAM Job parent.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if operation.ToolController is not prepared.controller:
        _error(
            f"The created {prepared.noun} lost its exact tool controller.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    view = getattr(operation, "ViewObject", None)
    if view is None:
        if provider is not None:
            _error(
                f"The headless {prepared.noun} retained unexpected GUI presentation.",
                "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            )
    elif provider is None or view.Proxy is not provider or bool(provider.deleteOnReject):
        _error(
            f"The created {prepared.noun} did not retain its accepted native view provider.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if _base_state(operation) != _expected_base_state(prepared):
        _error(
            f"The created {prepared.noun} did not retain the exact requested Job geometry.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    assert_settings(operation, payload)
    for item in prepared.geometry:
        actual_shape = item.public_source.Shape
        unchanged, actual_shape_sha256 = _public_shape_is_unchanged(
            actual_shape,
            item.source_shape,
            item.shape_sha256,
            f"CAM model {item.public_source.Name}",
        )
        if not unchanged:
            raise NativeManufactureError(
                f"{prepared.noun} creation changed public model source "
                f"{item.public_source.Name!r}.",
                error_code="NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
                repair={
                    "object_name": str(item.public_source.Name),
                    "expected_shape_sha256": item.shape_sha256,
                    "actual_shape_sha256": actual_shape_sha256,
                },
            )
    diagnostics = operation.Proxy.getGenerationDiagnostics(operation)
    commands = tuple(getattr(operation.Path, "Commands", ()) or ())
    cutting_count = sum(
        1
        for command in commands
        if str(getattr(command, "Name", "")) in cutting_command_names
    )
    if (
        diagnostics.get("status") != "succeeded"
        or diagnostics.get("stage") != "complete"
        or diagnostics.get("error") is not None
        or int(diagnostics.get("command_count", 0)) != len(commands)
        or cutting_count < minimum_cutting_commands
    ):
        raise NativeManufactureError(
            f"The created {prepared.noun} did not produce a valid cutting toolpath.",
            error_code="NATIVE_MANUFACTURE_OPERATION_GENERATION_FAILED",
            repair={
                "generation_status": diagnostics.get("status"),
                "generation_stage": diagnostics.get("stage"),
                "generation_error": diagnostics.get("error"),
                "command_count": len(commands),
                "cutting_command_count": cutting_count,
            },
        )
    if read_current_selection(document) != prepared.selection_before:
        _error(
            f"{prepared.noun} creation changed the human selection.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    visibility_after = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in prepared.visibility_before
    )
    if visibility_after != prepared.visibility_before:
        _error(
            f"{prepared.noun} creation changed existing document visibility.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    state = operation_state(operation)
    job_after = job_state(prepared.job)
    if (
        job_after["counts"]["operations"]
        != prepared.job_before["counts"]["operations"] + 1
        or not _job_resources_are_unchanged(prepared.job_before, job_after)
    ):
        _error(
            f"{prepared.noun} creation changed unrelated CAM Job resources.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if not other_job_states_are_current(document, prepared.other_job_states):
        _error(
            f"{prepared.noun} creation changed another CAM setup.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    extra = additional_verify(operation, payload) if additional_verify else None
    result = {
        **state,
        "geometry": operation_geometry_summary(prepared),
        "parameters": dict(payload.get("parameters", {})),
        "cutting_command_count": cutting_count,
    }
    if extra:
        result.update(dict(extra))
    return {
        result_key: result,
        "job": {
            "object_name": job_after["object_name"],
            "state_sha256": job_after["state_sha256"],
            "operation_count": job_after["counts"]["operations"],
        },
    }
