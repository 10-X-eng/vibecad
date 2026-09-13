# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Thread Milling operation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import (
    LINKING_STRATEGIES,
    PreparedOperationBoundary,
    clean_operation_label,
    clear_operation_expressions,
    create_native_operation,
    exact_fields,
    extend_native_operation_draft,
    finite_number,
    native_operation_presentation,
    preflight_operation_boundary,
    quantity_mm,
    validate_operation_tool_linking,
    verify_native_operation,
)
from VibeCADNativeManufactureThreadCatalog import (
    ResolvedThreadDefinition,
    resolve_thread_definition,
)
from VibeCADNativeMutation import NativeMutationDraft


_TARGET_FIELDS = frozenset({"feature_groups", "sorting"})
_FEATURE_GROUP_FIELDS = frozenset({"model", "features"})
_FEATURE_FIELDS = frozenset({"subelement", "enabled"})
_THREAD_FIELDS = frozenset(
    {"definition", "orientation", "direction", "passes", "lead_in_out"}
)
_DEPTH_FIELDS = frozenset({"start_depth_mm", "final_depth_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_LINKING_FIELDS = frozenset({"strategy", "collision_clearance_mm"})
_SORTING_MODES = {"automatic": "Automatic", "manual": "Manual"}
_ORIENTATIONS = {"left_hand": "LeftHand", "right_hand": "RightHand"}
_DIRECTIONS = {"climb": "Climb", "conventional": "Conventional"}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_CENTER_TOLERANCE_MM = 1.0e-7
_MAX_FEATURES = 64


@dataclass(frozen=True, slots=True)
class ThreadMillingCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    targets: Mapping[str, Any]
    thread: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    linking: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class ThreadMillingParameters:
    sorting: str
    definition: ResolvedThreadDefinition
    orientation: str
    direction: str
    passes: int
    lead_in_out: bool
    start_depth_mm: float
    final_depth_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    linking_strategy: str
    collision_clearance_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class RequestedFeatureGroup:
    model: Mapping[str, Any]
    features: tuple[tuple[str, bool], ...]


@dataclass(frozen=True, slots=True)
class PreparedThreadFeature:
    object_name: str
    job_resource: Any
    subelement: str
    enabled: bool
    center_x_mm: float
    center_y_mm: float
    diameter_mm: float


@dataclass(frozen=True, slots=True)
class PreparedThreadMillingCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: ThreadMillingParameters
    features: tuple[PreparedThreadFeature, ...]
    tool_diameter_mm: float
    tool_crest_mm: float
    cutting_angle_degrees: float
    pass_radii_mm: tuple[float, ...]


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _boolean(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{noun} must be true or false.")
    return value


def _normalize_targets(
    raw: Any,
) -> tuple[tuple[RequestedFeatureGroup, ...], str]:
    targets = exact_fields(raw, _TARGET_FIELDS, "Thread Milling targets")
    raw_groups = targets["feature_groups"]
    if not isinstance(raw_groups, list) or not 1 <= len(raw_groups) <= 32:
        _error("Thread Milling feature_groups must contain 1 through 32 model groups.")
    groups = []
    seen_models: set[str] = set()
    feature_count = 0
    for group_index, raw_group in enumerate(raw_groups):
        group = exact_fields(
            raw_group,
            _FEATURE_GROUP_FIELDS,
            f"Thread Milling feature group {group_index}",
        )
        model = group["model"]
        if not isinstance(model, Mapping) or set(model) != {
            "object_name",
            "expected_state_sha256",
        }:
            _error(
                f"Thread Milling feature group {group_index} requires one exact model target."
            )
        object_name = str(model.get("object_name") or "")
        if not object_name or object_name in seen_models:
            _error("Thread Milling feature groups must target distinct Job models.")
        seen_models.add(object_name)
        raw_features = group["features"]
        if not isinstance(raw_features, list) or not 1 <= len(raw_features) <= 64:
            _error(
                f"Thread Milling feature group {group_index} must contain 1 through 64 features."
            )
        features = []
        seen_subelements: set[str] = set()
        for feature_index, raw_feature in enumerate(raw_features):
            feature = exact_fields(
                raw_feature,
                _FEATURE_FIELDS,
                f"Thread Milling feature {group_index}:{feature_index}",
            )
            subelement = str(feature["subelement"] or "")
            if subelement in seen_subelements:
                _error(
                    f"Thread Milling feature {object_name}.{subelement} is duplicated."
                )
            seen_subelements.add(subelement)
            features.append(
                (
                    subelement,
                    _boolean(
                        feature["enabled"],
                        "Thread Milling feature enabled",
                    ),
                )
            )
        feature_count += len(features)
        groups.append(RequestedFeatureGroup(dict(model), tuple(features)))
    if feature_count > _MAX_FEATURES:
        _error("Thread Milling accepts at most 64 exact features in total.")
    sorting = str(targets["sorting"] or "")
    if sorting not in _SORTING_MODES:
        _error("Thread Milling sorting must be automatic or manual.")
    return tuple(groups), sorting


def _normalize_parameters(
    spec: ThreadMillingCreateSpec,
    sorting: str,
) -> ThreadMillingParameters:
    thread = exact_fields(spec.thread, _THREAD_FIELDS, "Thread Milling settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Thread Milling depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Thread Milling heights")
    linking = exact_fields(spec.linking, _LINKING_FIELDS, "Thread Milling linking")
    orientation = str(thread["orientation"] or "")
    if orientation not in _ORIENTATIONS:
        _error("Thread Milling orientation must be left_hand or right_hand.")
    direction = str(thread["direction"] or "")
    if direction not in _DIRECTIONS:
        _error("Thread Milling direction must be climb or conventional.")
    passes = thread["passes"]
    if isinstance(passes, bool) or not isinstance(passes, int) or not 1 <= passes <= 99:
        _error("Thread Milling passes must be an integer from 1 through 99.")
    start = finite_number(depths["start_depth_mm"], "Thread Milling start depth")
    final = finite_number(depths["final_depth_mm"], "Thread Milling final depth")
    if final >= start:
        _error("Thread Milling final_depth_mm must be below start_depth_mm.")
    safe = finite_number(heights["safe_height_mm"], "Thread Milling safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Thread Milling clearance height",
    )
    if safe < start:
        _error("Thread Milling safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error(
            "Thread Milling clearance_height_mm must be at or above safe_height_mm."
        )
    linking_strategy = str(linking["strategy"] or "")
    if linking_strategy not in LINKING_STRATEGIES:
        _error(
            "Thread Milling linking strategy must be clearance_height, "
            "retract_height, line_of_sight, tool_diameter, or tool_shape."
        )
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Thread Milling coolant must be none, flood, or mist.")
    return ThreadMillingParameters(
        sorting=sorting,
        definition=resolve_thread_definition(thread["definition"]),
        orientation=orientation,
        direction=direction,
        passes=passes,
        lead_in_out=_boolean(thread["lead_in_out"], "Thread Milling lead_in_out"),
        start_depth_mm=start,
        final_depth_mm=final,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        linking_strategy=linking_strategy,
        collision_clearance_mm=finite_number(
            linking["collision_clearance_mm"],
            "Thread Milling collision clearance",
            minimum=0.0,
        ),
        coolant=coolant,
    )


def _feature_facts(source: Any, subelement: str) -> tuple[float, float, float]:
    import Part
    import Path.Base.Drillable as Drillable

    try:
        shape = source.Shape
        feature = shape.getElement(subelement)
    except Exception as exc:
        raise NativeManufactureError(
            f"Thread Milling feature {source.Name}.{subelement} changed after turn start.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        ) from exc
    try:
        accepted = bool(
            Drillable.isDrillable(
                shape,
                feature,
                vector=None,
                allowPartial=True,
            )
        )
    except Exception:
        accepted = False
    if not accepted:
        _error(
            f"Thread Milling feature {source.Name}.{subelement} is not a circular "
            "Face or Edge accepted by the shipped Thread Milling selection gate.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    center = None
    diameter = None
    if isinstance(feature, Part.Edge) and isinstance(feature.Curve, Part.Circle):
        center = feature.Curve.Center
        diameter = 2.0 * float(feature.Curve.Radius)
    elif isinstance(feature, Part.Face):
        if isinstance(feature.Surface, Part.Cylinder):
            center = feature.Surface.Center
        circular_edges = [
            edge for edge in feature.Edges if isinstance(edge.Curve, Part.Circle)
        ]
        if circular_edges:
            if center is None:
                center = circular_edges[0].Curve.Center
            diameter = 2.0 * float(circular_edges[0].Curve.Radius)
    if center is None or diameter is None or not math.isfinite(diameter) or diameter <= 0:
        _error(
            f"Thread Milling feature {source.Name}.{subelement} has no usable "
            "circular center and diameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    result = (float(center.x), float(center.y), diameter)
    if not all(math.isfinite(value) for value in result):
        _error(
            f"Thread Milling feature {source.Name}.{subelement} has non-finite geometry.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return tuple(round(value, 9) for value in result)


def _prepare_features(
    boundary: PreparedOperationBoundary,
    groups: tuple[RequestedFeatureGroup, ...],
) -> tuple[PreparedThreadFeature, ...]:
    if len(boundary.geometry) != len(groups):
        raise RuntimeError("Thread Milling target preflight lost a feature group")
    prepared = []
    for item, requested in zip(boundary.geometry, groups):
        if item.subelements != tuple(name for name, _enabled in requested.features):
            raise RuntimeError("Thread Milling target preflight changed feature order")
        for subelement, enabled in requested.features:
            x_mm, y_mm, diameter_mm = _feature_facts(
                item.public_source,
                subelement,
            )
            prepared.append(
                PreparedThreadFeature(
                    object_name=str(item.public_source.Name),
                    job_resource=item.job_resource,
                    subelement=subelement,
                    enabled=enabled,
                    center_x_mm=x_mm,
                    center_y_mm=y_mm,
                    diameter_mm=diameter_mm,
                )
            )
    return tuple(prepared)


def _validate_distinct_enabled_features(
    features: tuple[PreparedThreadFeature, ...],
) -> int:
    enabled = [feature for feature in features if feature.enabled]
    if not enabled:
        _error("Thread Milling requires at least one enabled circular feature.")
    for index, feature in enumerate(enabled):
        for prior in enabled[:index]:
            if (
                math.hypot(
                    feature.center_x_mm - prior.center_x_mm,
                    feature.center_y_mm - prior.center_y_mm,
                )
                <= _CENTER_TOLERANCE_MM
            ):
                _error(
                    f"Thread Milling features {prior.object_name}.{prior.subelement} "
                    f"and {feature.object_name}.{feature.subelement} resolve to the "
                    "same XY center. Keep only one enabled target."
                )
    return len(enabled)


def _validate_feature_stock(
    features: tuple[PreparedThreadFeature, ...],
    definition: ResolvedThreadDefinition,
) -> None:
    for feature in (item for item in features if item.enabled):
        identity = f"{feature.object_name}.{feature.subelement}"
        if (
            definition.side == "internal"
            and feature.diameter_mm
            > definition.minor_diameter_mm + _CENTER_TOLERANCE_MM
        ):
            _error(
                f"Internal Thread Milling target {identity} is {feature.diameter_mm:g} mm "
                f"across, larger than the requested {definition.minor_diameter_mm:g} mm "
                "minor diameter; the modeled hole has no material for that thread.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        if (
            definition.side == "external"
            and feature.diameter_mm
            < definition.major_diameter_mm - _CENTER_TOLERANCE_MM
        ):
            _error(
                f"External Thread Milling target {identity} is {feature.diameter_mm:g} mm "
                f"across, smaller than the requested {definition.major_diameter_mm:g} mm "
                "major diameter; the modeled boss has no material for that thread.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )


def _quantity_value(value: Any, unit: str) -> float:
    getter = getattr(value, "getValueAs", None)
    return float(getter(unit)) if callable(getter) else float(value)


def _prepare_tool(
    boundary: PreparedOperationBoundary,
    parameters: ThreadMillingParameters,
) -> tuple[float, float, float, tuple[float, ...]]:
    import Path.Op.ThreadMilling as PathThreadMilling

    diameter = validate_operation_tool_linking(
        boundary,
        parameters.linking_strategy,
    )
    tool = boundary.controller.Tool
    try:
        crest = _quantity_value(tool.Crest, "mm")
    except (AttributeError, TypeError, ValueError, RuntimeError):
        crest = -1.0
    if not math.isfinite(crest) or crest < 0.0:
        _error(
            "Thread Milling requires a thread-milling tool with nonnegative Crest.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    angle_value = getattr(tool, "cuttingAngle", None)
    if angle_value is None:
        cutting_angle = 60.0
    else:
        try:
            cutting_angle = _quantity_value(angle_value, "deg")
        except (TypeError, ValueError, RuntimeError):
            cutting_angle = 0.0
    if not math.isfinite(cutting_angle) or not 0.0 < cutting_angle < 180.0:
        _error(
            "Thread Milling requires a tool cuttingAngle strictly between 0 and 180 degrees.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    definition = parameters.definition
    if definition.side == "internal" and diameter >= definition.minor_diameter_mm:
        _error(
            "Internal Thread Milling requires the cutter diameter to be smaller "
            "than the thread minor diameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    try:
        radii = PathThreadMilling.threadRadii(
            definition.side == "internal",
            definition.major_diameter_mm,
            definition.minor_diameter_mm,
            diameter,
            crest,
            cutting_angle,
        )
        pass_radii = tuple(
            PathThreadMilling.threadPasses(
                parameters.passes,
                PathThreadMilling.threadRadii,
                definition.side == "internal",
                definition.major_diameter_mm,
                definition.minor_diameter_mm,
                diameter,
                crest,
                cutting_angle,
            )
        )
    except Exception as exc:
        raise NativeManufactureError(
            "The selected thread and tool could not produce valid cutting radii.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    values = (*radii, *pass_radii)
    if (
        len(pass_radii) != parameters.passes
        or not all(math.isfinite(value) and value > 0.0 for value in values)
        or math.isclose(radii[0], radii[1], abs_tol=1.0e-9)
    ):
        _error(
            "The selected thread dimensions, tool diameter, crest, and cutting "
            "angle do not produce positive nondegenerate cutting radii.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return (
        diameter,
        round(crest, 9),
        round(cutting_angle, 9),
        tuple(round(value, 9) for value in pass_radii),
    )


def preflight_thread_milling_create(
    document: Any,
    spec: ThreadMillingCreateSpec,
) -> PreparedThreadMillingCreate:
    """Freeze exact hole targets, thread definition, tool, and path settings."""

    if not isinstance(spec, ThreadMillingCreateSpec):
        raise TypeError("spec must be a ThreadMillingCreateSpec")
    groups, sorting = _normalize_targets(spec.targets)
    parameters = _normalize_parameters(spec, sorting)
    boundary = preflight_operation_boundary(
        document,
        noun="Thread Milling",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry={
            "kind": "subelements",
            "items": [
                {
                    "model": dict(group.model),
                    "subelements": [name for name, _enabled in group.features],
                }
                for group in groups
            ],
        },
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=False,
    )
    features = _prepare_features(boundary, groups)
    _validate_distinct_enabled_features(features)
    _validate_feature_stock(features, parameters.definition)
    diameter, crest, cutting_angle, pass_radii = _prepare_tool(
        boundary,
        parameters,
    )
    return PreparedThreadMillingCreate(
        label=clean_operation_label(spec.label, "Thread Milling"),
        boundary=boundary,
        parameters=parameters,
        features=features,
        tool_diameter_mm=diameter,
        tool_crest_mm=crest,
        cutting_angle_degrees=cutting_angle,
        pass_radii_mm=pass_radii,
    )


def _definition_payload(definition: ResolvedThreadDefinition) -> dict[str, Any]:
    result = {
        "kind": definition.kind,
        "side": definition.side,
        "major_diameter_mm": definition.major_diameter_mm,
        "minor_diameter_mm": definition.minor_diameter_mm,
    }
    if definition.kind == "standard":
        result.update(
            series=definition.series,
            designation=definition.designation,
            fit_percent=definition.fit_percent,
        )
    if definition.threads_per_inch:
        result["threads_per_inch"] = definition.threads_per_inch
    else:
        result["pitch_mm"] = definition.pitch_mm
    return result


def _parameter_payload(prepared: PreparedThreadMillingCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    return {
        "targets": {"sorting": parameters.sorting},
        "thread": {
            "definition": _definition_payload(parameters.definition),
            "orientation": parameters.orientation,
            "direction": parameters.direction,
            "passes": parameters.passes,
            "lead_in_out": parameters.lead_in_out,
        },
        "depths": {
            "start_depth_mm": parameters.start_depth_mm,
            "final_depth_mm": parameters.final_depth_mm,
        },
        "heights": {
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
        "linking": {
            "strategy": parameters.linking_strategy,
            "collision_clearance_mm": parameters.collision_clearance_mm,
        },
        "coolant": parameters.coolant,
    }


def _disabled_feature_names(
    prepared: PreparedThreadMillingCreate,
) -> tuple[str, ...]:
    return tuple(
        f"{feature.job_resource.Name}.{feature.subelement}"
        for feature in prepared.features
        if not feature.enabled
    )


def _apply_settings(
    operation: Any,
    prepared: PreparedThreadMillingCreate,
) -> None:
    import FreeCAD as App

    parameters = prepared.parameters
    definition = parameters.definition
    clear_operation_expressions(
        operation,
        (
            "MajorDiameter",
            "MinorDiameter",
            "Pitch",
            "StartDepth",
            "FinalDepth",
            "SafeHeight",
            "ClearanceHeight",
            "CollisionClearance",
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.SortingMode = _SORTING_MODES[parameters.sorting]
    operation.Disabled = list(_disabled_feature_names(prepared))
    operation.StartPoint = App.Vector(0.0, 0.0, 0.0)
    operation.UseEndPoint = False
    operation.EndPoint = App.Vector(0.0, 0.0, 0.0)

    operation.ThreadOrientation = _ORIENTATIONS[parameters.orientation]
    operation.ThreadType = definition.native_type
    operation.ThreadName = definition.designation
    operation.ThreadFit = definition.fit_percent
    operation.MajorDiameter = f"{definition.major_diameter_mm} mm"
    operation.MinorDiameter = f"{definition.minor_diameter_mm} mm"
    operation.Pitch = f"{definition.pitch_mm} mm"
    operation.TPI = definition.threads_per_inch
    operation.Passes = parameters.passes
    operation.Direction = _DIRECTIONS[parameters.direction]
    operation.LeadInOut = parameters.lead_in_out
    operation.ClearanceOp = None

    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CollisionAvoidanceStrategy = LINKING_STRATEGIES[
        parameters.linking_strategy
    ]
    operation.CollisionClearance = f"{parameters.collision_clearance_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]


def create_thread_milling(
    document: Any,
    *,
    prepared: PreparedThreadMillingCreate,
) -> NativeMutationDraft:
    """Create one native Thread Milling operation inside the owned transaction."""

    if not isinstance(prepared, PreparedThreadMillingCreate):
        raise TypeError("prepared must be a PreparedThreadMillingCreate")
    import Path.Op.ThreadMilling as PathThreadMilling

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.ThreadMilling"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="ThreadMilling",
        operation_factory=PathThreadMilling.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(
        draft,
        thread_milling_prepared=prepared,
    )


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _assert_thread_milling_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedThreadMillingCreate,
) -> None:
    parameters = prepared.parameters
    definition = parameters.definition
    actual = {
        "sorting": str(operation.SortingMode),
        "disabled": tuple(operation.Disabled),
        "orientation": str(operation.ThreadOrientation),
        "thread_type": str(operation.ThreadType),
        "designation": str(operation.ThreadName),
        "fit_percent": int(operation.ThreadFit),
        "major_diameter_mm": quantity_mm(operation, "MajorDiameter"),
        "minor_diameter_mm": quantity_mm(operation, "MinorDiameter"),
        "pitch_mm": quantity_mm(operation, "Pitch"),
        "threads_per_inch": int(operation.TPI),
        "passes": int(operation.Passes),
        "direction": str(operation.Direction),
        "lead_in_out": bool(operation.LeadInOut),
        "clearance_operation": operation.ClearanceOp,
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "linking_strategy": str(operation.CollisionAvoidanceStrategy),
        "collision_clearance_mm": quantity_mm(operation, "CollisionClearance"),
        "coolant": str(operation.CoolantMode),
        "start_point_mm": _vector_tuple(operation.StartPoint),
        "use_end_point": bool(operation.UseEndPoint),
        "end_point_mm": _vector_tuple(operation.EndPoint),
        "workplane": _vector_tuple(operation.Workplane),
    }
    expected = {
        "sorting": _SORTING_MODES[parameters.sorting],
        "disabled": _disabled_feature_names(prepared),
        "orientation": _ORIENTATIONS[parameters.orientation],
        "thread_type": definition.native_type,
        "designation": definition.designation,
        "fit_percent": definition.fit_percent,
        "major_diameter_mm": definition.major_diameter_mm,
        "minor_diameter_mm": definition.minor_diameter_mm,
        "pitch_mm": definition.pitch_mm,
        "threads_per_inch": definition.threads_per_inch,
        "passes": parameters.passes,
        "direction": _DIRECTIONS[parameters.direction],
        "lead_in_out": parameters.lead_in_out,
        "clearance_operation": None,
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "linking_strategy": LINKING_STRATEGIES[parameters.linking_strategy],
        "collision_clearance_mm": parameters.collision_clearance_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
        "start_point_mm": (0.0, 0.0, 0.0),
        "use_end_point": False,
        "end_point_mm": (0.0, 0.0, 0.0),
        "workplane": (0.0, 0.0, 1.0),
    }
    mismatches = {
        name: {"expected": expected_value, "actual": actual.get(name)}
        for name, expected_value in expected.items()
        if actual.get(name) != expected_value
    }
    for property_name in (
        "MajorDiameter",
        "MinorDiameter",
        "Pitch",
        "StartDepth",
        "FinalDepth",
        "SafeHeight",
        "ClearanceHeight",
        "CollisionClearance",
    ):
        expression = _expression(operation, property_name)
        if expression:
            mismatches[f"{property_name}_expression"] = {
                "expected": None,
                "actual": str(expression),
            }
    if mismatches:
        raise NativeManufactureError(
            "The created Thread Milling operation did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_THREAD_MILLING_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _thread_milling_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedThreadMillingCreate,
) -> Mapping[str, Any]:
    import Path.Op.ThreadMilling as PathThreadMilling

    enabled = tuple(feature for feature in prepared.features if feature.enabled)
    expected_entries = len(enabled) * prepared.parameters.passes
    definition = prepared.parameters.definition
    elevator = (
        max(
            (definition.minor_diameter_mm - prepared.tool_diameter_mm) / 2.0 - 1.0,
            0.0,
        )
        if definition.side == "internal"
        else (definition.major_diameter_mm + prepared.tool_diameter_mm) / 2.0 + 1.0
    )
    expected_positions = sorted(
        (round(feature.center_x_mm, 7), round(feature.center_y_mm + elevator, 7))
        for feature in enabled
        for _pass in range(prepared.parameters.passes)
    )
    commands = tuple(operation.Path.Commands)
    actual_positions = sorted(
        (
            round(float(command.Parameters["X"]), 7),
            round(float(command.Parameters["Y"]), 7),
        )
        for command in commands
        if str(command.Name) == "G0"
        and "X" in command.Parameters
        and "Y" in command.Parameters
    )
    expected_helix_code = PathThreadMilling.threadSetup(operation)[0]
    helix_commands = tuple(
        command
        for command in commands
        if str(command.Name) == expected_helix_code and "Z" in command.Parameters
    )
    if actual_positions != expected_positions or len(helix_commands) < expected_entries:
        raise NativeManufactureError(
            "The created Thread Milling operation did not generate one complete "
            "thread path for every enabled target and radial pass.",
            error_code="NATIVE_MANUFACTURE_THREAD_MILLING_POSTCONDITION_FAILED",
            repair={
                "expected_entry_count": expected_entries,
                "actual_entry_count": len(actual_positions),
                "expected_helix_code": expected_helix_code,
                "actual_helix_command_count": len(helix_commands),
            },
        )
    return {
        "targets": {
            "feature_count": len(prepared.features),
            "enabled_count": len(enabled),
            "sorting": prepared.parameters.sorting,
        },
        "thread": _definition_payload(definition),
        "orientation": prepared.parameters.orientation,
        "direction": prepared.parameters.direction,
        "pass_count": prepared.parameters.passes,
        "pass_radii_mm": list(prepared.pass_radii_mm),
        "helix_command": expected_helix_code,
        "helix_command_count": len(helix_commands),
        "tool": {
            "diameter_mm": prepared.tool_diameter_mm,
            "crest_mm": prepared.tool_crest_mm,
            "cutting_angle_degrees": prepared.cutting_angle_degrees,
        },
    }


def verify_created_thread_milling(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedThreadMillingCreate = draft.value[
        "thread_milling_prepared"
    ]
    return verify_native_operation(
        document,
        draft,
        result_key="thread_milling",
        assert_settings=partial(
            _assert_thread_milling_settings,
            prepared=prepared,
        ),
        additional_verify=partial(
            _thread_milling_result,
            prepared=prepared,
        ),
    )
