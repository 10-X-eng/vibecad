# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Drilling/Tapping operation."""

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
    preflight_operation_without_geometry,
    quantity_mm,
    validate_operation_tool_linking,
    verify_native_operation,
)
from VibeCADNativeMutation import NativeMutationDraft


_TARGET_FIELDS = frozenset({"feature_groups", "locations_mm", "sorting"})
_FEATURE_GROUP_FIELDS = frozenset({"model", "features"})
_FEATURE_FIELDS = frozenset({"subelement", "enabled"})
_LOCATION_FIELDS = frozenset({"x_mm", "y_mm"})
_PROCESS_FIELDS = frozenset(
    {"kind", "cycle", "depth_extension", "keep_tool_down"}
)
_DEPTH_FIELDS = frozenset({"start_depth_mm", "final_depth_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_LINKING_FIELDS = frozenset({"strategy", "collision_clearance_mm"})
_SORTING_MODES = {"automatic": "Automatic", "manual": "Manual"}
_STRATEGIES = {"drilling": "Drilling", "tapping": "Tapping"}
_DEPTH_EXTENSIONS = {
    "none": "None",
    "drill_tip": "Drill Tip",
    "two_drill_tips": "2x Drill Tip",
}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_CENTER_TOLERANCE_MM = 1.0e-7
_MAX_FEATURES = 64
_MAX_LOCATIONS = 64
_CYCLE_COMMANDS = frozenset({"G73", "G74", "G81", "G82", "G83", "G84", "G85"})


@dataclass(frozen=True, slots=True)
class DrillingCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    targets: Mapping[str, Any]
    process: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    linking: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class DrillingParameters:
    sorting: str
    strategy: str
    cycle_kind: str
    peck_depth_mm: float | None
    chip_break: bool
    dwell_time_seconds: float | None
    feed_retract: bool
    depth_extension: str
    keep_tool_down: bool
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
class PreparedDrillingFeature:
    object_name: str
    job_resource: Any
    subelement: str
    enabled: bool
    center_x_mm: float
    center_y_mm: float
    diameter_mm: float


@dataclass(frozen=True, slots=True)
class PreparedDrillingCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: DrillingParameters
    features: tuple[PreparedDrillingFeature, ...]
    locations_mm: tuple[tuple[float, float], ...]
    tool_diameter_mm: float
    tip_length_mm: float
    tapping_pitch_mm: float | None
    spindle_speed_rpm: float | None
    spindle_direction: str | None


@dataclass(frozen=True, slots=True)
class DrillingDefaultsSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: tuple[Mapping[str, Any], ...]
    coolant: Any


@dataclass(frozen=True, slots=True)
class PreparedDrillingDefaults:
    label: str
    boundary: PreparedOperationBoundary
    coolant: str
    features: tuple[PreparedDrillingFeature, ...]
    tool_diameter_mm: float


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _boolean(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{noun} must be true or false.")
    return value


def _positive(value: Any, noun: str, *, maximum: float = 1_000_000.0) -> float:
    result = finite_number(value, noun, minimum=0.0, maximum=maximum)
    if result <= 0.0:
        _error(f"{noun} must be greater than zero.")
    return result


def _normalize_targets(
    raw: Any,
) -> tuple[
    tuple[RequestedFeatureGroup, ...],
    tuple[tuple[float, float], ...],
    str,
]:
    targets = exact_fields(raw, _TARGET_FIELDS, "Drilling targets")
    raw_groups = targets["feature_groups"]
    raw_locations = targets["locations_mm"]
    if not isinstance(raw_groups, list) or len(raw_groups) > 32:
        _error("Drilling feature_groups must contain zero through 32 model groups.")
    if not isinstance(raw_locations, list) or len(raw_locations) > _MAX_LOCATIONS:
        _error("Drilling locations_mm must contain zero through 64 XY locations.")

    groups = []
    seen_models: set[str] = set()
    feature_count = 0
    for index, raw_group in enumerate(raw_groups):
        group = exact_fields(
            raw_group,
            _FEATURE_GROUP_FIELDS,
            f"Drilling feature group {index}",
        )
        model = group["model"]
        if not isinstance(model, Mapping) or set(model) != {
            "object_name",
            "expected_state_sha256",
        }:
            _error(f"Drilling feature group {index} requires one exact model target.")
        object_name = str(model.get("object_name") or "")
        if not object_name or object_name in seen_models:
            _error("Drilling feature groups must target distinct Job models.")
        seen_models.add(object_name)
        raw_features = group["features"]
        if not isinstance(raw_features, list) or not 1 <= len(raw_features) <= 64:
            _error(
                f"Drilling feature group {index} must contain 1 through 64 features."
            )
        features = []
        seen_subelements: set[str] = set()
        for feature_index, raw_feature in enumerate(raw_features):
            feature = exact_fields(
                raw_feature,
                _FEATURE_FIELDS,
                f"Drilling feature {index}:{feature_index}",
            )
            subelement = str(feature["subelement"] or "")
            if subelement in seen_subelements:
                _error(
                    f"Drilling feature {object_name}.{subelement} is duplicated."
                )
            seen_subelements.add(subelement)
            features.append(
                (
                    subelement,
                    _boolean(feature["enabled"], "Drilling feature enabled"),
                )
            )
        feature_count += len(features)
        groups.append(RequestedFeatureGroup(dict(model), tuple(features)))
    if feature_count > _MAX_FEATURES:
        _error("Drilling accepts at most 64 exact features in total.")

    locations = []
    for index, raw_location in enumerate(raw_locations):
        location = exact_fields(
            raw_location,
            _LOCATION_FIELDS,
            f"Drilling location {index}",
        )
        locations.append(
            (
                finite_number(location["x_mm"], f"Drilling location {index} x"),
                finite_number(location["y_mm"], f"Drilling location {index} y"),
            )
        )
    sorting = str(targets["sorting"] or "")
    if sorting not in _SORTING_MODES:
        _error("Drilling sorting must be automatic or manual.")
    return tuple(groups), tuple(locations), sorting


def _normalize_cycle(strategy: str, raw: Any) -> tuple[str, float | None, bool, float | None, bool]:
    if not isinstance(raw, Mapping):
        _error("Drilling cycle must be one closed cycle request.")
    kind = str(raw.get("kind") or "")
    if kind == "standard":
        exact_fields(raw, frozenset({"kind"}), "Standard drilling cycle")
        return kind, None, False, None, False
    if kind == "dwell":
        cycle = exact_fields(
            raw,
            frozenset({"kind", "time_seconds"}),
            "Dwell drilling cycle",
        )
        return (
            kind,
            None,
            False,
            _positive(
                cycle["time_seconds"],
                "Drilling dwell time",
                maximum=86_400.0,
            ),
            False,
        )
    if strategy == "tapping":
        _error("Tapping cycle kind must be standard or dwell.")
    if kind == "peck":
        cycle = exact_fields(
            raw,
            frozenset({"kind", "depth_mm", "chip_break"}),
            "Peck drilling cycle",
        )
        return (
            kind,
            _positive(cycle["depth_mm"], "Drilling peck depth"),
            _boolean(cycle["chip_break"], "Drilling chip_break"),
            None,
            False,
        )
    if kind == "feed_retract":
        exact_fields(raw, frozenset({"kind"}), "Feed-retract drilling cycle")
        return kind, None, False, None, True
    _error("Drilling cycle kind must be standard, peck, dwell, or feed_retract.")


def _normalize_parameters(spec: DrillingCreateSpec, sorting: str) -> DrillingParameters:
    process = exact_fields(spec.process, _PROCESS_FIELDS, "Drilling process")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Drilling depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Drilling heights")
    linking = exact_fields(spec.linking, _LINKING_FIELDS, "Drilling linking")
    strategy = str(process["kind"] or "")
    if strategy not in _STRATEGIES:
        _error("Drilling process kind must be drilling or tapping.")
    cycle_kind, peck_depth, chip_break, dwell_time, feed_retract = _normalize_cycle(
        strategy,
        process["cycle"],
    )
    extension = str(process["depth_extension"] or "")
    if extension not in _DEPTH_EXTENSIONS:
        _error(
            "Drilling depth_extension must be none, drill_tip, or two_drill_tips."
        )
    start = finite_number(depths["start_depth_mm"], "Drilling start depth")
    final = finite_number(depths["final_depth_mm"], "Drilling final depth")
    if final >= start:
        _error("Drilling final_depth_mm must be below start_depth_mm.")
    safe = finite_number(heights["safe_height_mm"], "Drilling safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Drilling clearance height",
    )
    if safe < start:
        _error("Drilling safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Drilling clearance_height_mm must be at or above safe_height_mm.")
    linking_strategy = str(linking["strategy"] or "")
    if linking_strategy not in LINKING_STRATEGIES:
        _error(
            "Drilling linking strategy must be clearance_height, retract_height, "
            "line_of_sight, tool_diameter, or tool_shape."
        )
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Drilling coolant must be none, flood, or mist.")
    return DrillingParameters(
        sorting=sorting,
        strategy=strategy,
        cycle_kind=cycle_kind,
        peck_depth_mm=peck_depth,
        chip_break=chip_break,
        dwell_time_seconds=dwell_time,
        feed_retract=feed_retract,
        depth_extension=extension,
        keep_tool_down=_boolean(
            process["keep_tool_down"],
            "Drilling keep_tool_down",
        ),
        start_depth_mm=start,
        final_depth_mm=final,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        linking_strategy=linking_strategy,
        collision_clearance_mm=finite_number(
            linking["collision_clearance_mm"],
            "Drilling collision clearance",
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
            f"Drilling feature {source.Name}.{subelement} changed after turn start.",
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
            f"Drilling feature {source.Name}.{subelement} is not a circular Face or "
            "Edge accepted by the shipped Drilling selection gate.",
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
            f"Drilling feature {source.Name}.{subelement} has no usable circular "
            "center and diameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    result = (float(center.x), float(center.y), diameter)
    if not all(math.isfinite(value) for value in result):
        _error(
            f"Drilling feature {source.Name}.{subelement} has non-finite geometry.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return tuple(round(value, 9) for value in result)


def _prepare_features(
    boundary: PreparedOperationBoundary,
    groups: tuple[RequestedFeatureGroup, ...],
) -> tuple[PreparedDrillingFeature, ...]:
    if len(boundary.geometry) != len(groups):
        raise RuntimeError("Drilling target preflight lost a feature group")
    prepared = []
    for item, requested in zip(boundary.geometry, groups):
        if item.subelements != tuple(name for name, _enabled in requested.features):
            raise RuntimeError("Drilling target preflight changed feature order")
        for subelement, enabled in requested.features:
            x_mm, y_mm, diameter_mm = _feature_facts(item.public_source, subelement)
            prepared.append(
                PreparedDrillingFeature(
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


def _validate_distinct_targets(
    features: tuple[PreparedDrillingFeature, ...],
    locations: tuple[tuple[float, float], ...],
) -> int:
    targets = [
        (feature.center_x_mm, feature.center_y_mm, f"{feature.object_name}.{feature.subelement}")
        for feature in features
        if feature.enabled
    ]
    targets.extend((x_mm, y_mm, f"location {index}") for index, (x_mm, y_mm) in enumerate(locations))
    if not targets:
        _error("Drilling requires at least one enabled feature or explicit location.")
    for index, target in enumerate(targets):
        for prior in targets[:index]:
            if math.hypot(target[0] - prior[0], target[1] - prior[1]) <= _CENTER_TOLERANCE_MM:
                _error(
                    f"Drilling targets {prior[2]} and {target[2]} resolve to the same "
                    "XY center. Keep only one enabled target."
                )
    return len(targets)


def _quantity_value(value: Any, unit: str) -> float:
    getter = getattr(value, "getValueAs", None)
    return float(getter(unit)) if callable(getter) else float(value)


def _prepare_tool_mode(
    boundary: PreparedOperationBoundary,
    parameters: DrillingParameters,
) -> tuple[float, float, float | None, float | None, str | None]:
    diameter = validate_operation_tool_linking(
        boundary,
        parameters.linking_strategy,
    )
    tool = boundary.controller.Tool
    tip_length = 0.0
    if parameters.depth_extension != "none":
        angle = getattr(tool, "TipAngle", None)
        try:
            angle_degrees = _quantity_value(angle, "deg")
        except (TypeError, ValueError, RuntimeError):
            angle_degrees = 0.0
        if not math.isfinite(angle_degrees) or not 0.0 < angle_degrees < 180.0:
            _error(
                "Drilling drill-tip depth extension requires a tool with TipAngle "
                "strictly between 0 and 180 degrees.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        tip_length = (diameter / 2.0) / math.tan(math.radians(angle_degrees) / 2.0)
        if not math.isfinite(tip_length) or tip_length <= 0.0:
            _error(
                "The selected tool does not produce a positive drill-tip length.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        tip_length = round(tip_length, 9)

    if parameters.strategy != "tapping":
        return diameter, tip_length, None, None, None
    pitch = getattr(tool, "Pitch", None)
    try:
        pitch_mm = _quantity_value(pitch, "mm")
    except (TypeError, ValueError, RuntimeError):
        pitch_mm = 0.0
    speed = getattr(boundary.controller, "SpindleSpeed", None)
    try:
        spindle_speed = float(getattr(speed, "Value", speed))
    except (TypeError, ValueError):
        spindle_speed = 0.0
    direction = str(getattr(tool, "SpindleDirection", "") or "")
    if not math.isfinite(pitch_mm) or pitch_mm <= 0.0:
        _error(
            "Tapping requires a selected Tap tool with positive Pitch.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if not math.isfinite(spindle_speed) or spindle_speed <= 0.0:
        _error(
            "Tapping requires a tool controller with positive spindle speed.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if direction not in {"Forward", "Reverse"}:
        _error(
            "Tapping requires tool SpindleDirection Forward or Reverse.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return (
        diameter,
        tip_length,
        round(pitch_mm, 9),
        round(spindle_speed, 9),
        direction,
    )


def preflight_drilling_create(
    document: Any,
    spec: DrillingCreateSpec,
) -> PreparedDrillingCreate:
    """Freeze the Job, tool, ordered hole targets, and complete cycle settings."""

    if not isinstance(spec, DrillingCreateSpec):
        raise TypeError("spec must be a DrillingCreateSpec")
    groups, locations, sorting = _normalize_targets(spec.targets)
    parameters = _normalize_parameters(spec, sorting)
    if groups:
        boundary = preflight_operation_boundary(
            document,
            noun="Drilling",
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
    else:
        boundary = preflight_operation_without_geometry(
            document,
            noun="Drilling",
            job_target=spec.job,
            tool_controller_target=spec.tool_controller,
        )
        features = ()
    _validate_distinct_targets(features, locations)
    diameter, tip_length, pitch, speed, direction = _prepare_tool_mode(
        boundary,
        parameters,
    )
    return PreparedDrillingCreate(
        label=clean_operation_label(spec.label, "Drilling"),
        boundary=boundary,
        parameters=parameters,
        features=features,
        locations_mm=locations,
        tool_diameter_mm=diameter,
        tip_length_mm=tip_length,
        tapping_pitch_mm=pitch,
        spindle_speed_rpm=speed,
        spindle_direction=direction,
    )


def preflight_drilling_defaults(
    document: Any,
    spec: DrillingDefaultsSpec,
) -> PreparedDrillingDefaults:
    """Freeze exact drillable geometry while retaining setup-owned defaults."""

    if not isinstance(spec, DrillingDefaultsSpec):
        raise TypeError("spec must be a DrillingDefaultsSpec")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Drilling coolant must be none, flood, or mist.")
    groups = tuple(
        RequestedFeatureGroup(
            model=dict(item["model"]),
            features=tuple((str(name), True) for name in item["subelements"]),
        )
        for item in spec.geometry
    )
    boundary = preflight_operation_boundary(
        document,
        noun="Drilling",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry={"kind": "subelements", "items": list(spec.geometry)},
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=False,
    )
    features = _prepare_features(boundary, groups)
    _validate_distinct_targets(features, ())
    try:
        diameter = _quantity_value(boundary.controller.Tool.Diameter, "mm")
    except (AttributeError, TypeError, ValueError, RuntimeError):
        diameter = 0.0
    if not math.isfinite(diameter) or diameter <= 0.0:
        _error(
            "Drilling requires a tool controller with positive tool diameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return PreparedDrillingDefaults(
        label=clean_operation_label(spec.label, "Drilling"),
        boundary=boundary,
        coolant=coolant,
        features=features,
        tool_diameter_mm=round(diameter, 9),
    )


def _cycle_payload(parameters: DrillingParameters) -> dict[str, Any]:
    cycle: dict[str, Any] = {"kind": parameters.cycle_kind}
    if parameters.cycle_kind == "peck":
        cycle.update(
            depth_mm=parameters.peck_depth_mm,
            chip_break=parameters.chip_break,
        )
    elif parameters.cycle_kind == "dwell":
        cycle["time_seconds"] = parameters.dwell_time_seconds
    return cycle


def _parameter_payload(prepared: PreparedDrillingCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    return {
        "targets": {"sorting": parameters.sorting},
        "process": {
            "kind": parameters.strategy,
            "cycle": _cycle_payload(parameters),
            "depth_extension": parameters.depth_extension,
            "keep_tool_down": parameters.keep_tool_down,
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


def _disabled_feature_names(prepared: PreparedDrillingCreate) -> tuple[str, ...]:
    return tuple(
        f"{feature.job_resource.Name}.{feature.subelement}"
        for feature in prepared.features
        if not feature.enabled
    )


def _apply_settings(operation: Any, prepared: PreparedDrillingCreate) -> None:
    import FreeCAD as App

    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        (
            "PeckDepth",
            "DwellTime",
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
    operation.Strategy = _STRATEGIES[parameters.strategy]
    operation.SortingMode = _SORTING_MODES[parameters.sorting]
    operation.Locations = [
        App.Vector(x_mm, y_mm, 0.0) for x_mm, y_mm in prepared.locations_mm
    ]
    operation.Disabled = list(_disabled_feature_names(prepared))
    operation.StartPoint = App.Vector(0.0, 0.0, 0.0)
    operation.UseEndPoint = False
    operation.EndPoint = App.Vector(0.0, 0.0, 0.0)

    operation.PeckEnabled = parameters.cycle_kind == "peck"
    operation.PeckDepth = f"{parameters.peck_depth_mm or prepared.tool_diameter_mm * 0.75} mm"
    operation.ChipBreakEnabled = parameters.chip_break
    operation.DwellEnabled = parameters.cycle_kind == "dwell"
    operation.DwellTime = parameters.dwell_time_seconds or 1.0
    operation.FeedRetractEnabled = parameters.feed_retract
    operation.AddTipLength = False
    operation.ExtraOffset = _DEPTH_EXTENSIONS[parameters.depth_extension]
    operation.KeepToolDown = parameters.keep_tool_down

    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CollisionAvoidanceStrategy = LINKING_STRATEGIES[
        parameters.linking_strategy
    ]
    operation.CollisionClearance = f"{parameters.collision_clearance_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]


def create_drilling(
    document: Any,
    *,
    prepared: PreparedDrillingCreate,
) -> NativeMutationDraft:
    """Create one native Drilling/Tapping operation inside the owned transaction."""

    if not isinstance(prepared, PreparedDrillingCreate):
        raise TypeError("prepared must be a PreparedDrillingCreate")
    import Path.Op.Drilling as PathDrilling

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Drilling"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Drilling",
        operation_factory=PathDrilling.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, drilling_prepared=prepared)


def _apply_drilling_defaults(
    operation: Any,
    *,
    prepared: PreparedDrillingDefaults,
) -> None:
    operation.Label = prepared.label
    operation.CoolantMode = _COOLANT_MODES[prepared.coolant]


def create_drilling_defaults(
    document: Any,
    *,
    prepared: PreparedDrillingDefaults,
) -> NativeMutationDraft:
    """Create Drilling with the same setup-owned defaults as the human command."""

    if not isinstance(prepared, PreparedDrillingDefaults):
        raise TypeError("prepared must be a PreparedDrillingDefaults")
    import Path.Op.Drilling as PathDrilling

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Drilling"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Drilling",
        operation_factory=PathDrilling.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_drilling_defaults, prepared=prepared),
        payload={"parameters": {"source": "setup_defaults"}},
    )
    return extend_native_operation_draft(draft, drilling_defaults=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _assert_drilling_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedDrillingCreate,
) -> None:
    parameters = prepared.parameters
    actual = {
        "strategy": str(operation.Strategy),
        "sorting": str(operation.SortingMode),
        "locations_mm": tuple(_vector_tuple(value) for value in operation.Locations),
        "disabled": tuple(operation.Disabled),
        "peck_enabled": bool(operation.PeckEnabled),
        "peck_depth_mm": quantity_mm(operation, "PeckDepth"),
        "chip_break": bool(operation.ChipBreakEnabled),
        "dwell_enabled": bool(operation.DwellEnabled),
        "dwell_time_seconds": round(float(operation.DwellTime), 9),
        "feed_retract": bool(operation.FeedRetractEnabled),
        "add_tip_length": bool(operation.AddTipLength),
        "depth_extension": str(operation.ExtraOffset),
        "keep_tool_down": bool(operation.KeepToolDown),
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
    }
    expected = {
        "strategy": _STRATEGIES[parameters.strategy],
        "sorting": _SORTING_MODES[parameters.sorting],
        "locations_mm": tuple((x, y, 0.0) for x, y in prepared.locations_mm),
        "disabled": _disabled_feature_names(prepared),
        "peck_enabled": parameters.cycle_kind == "peck",
        "peck_depth_mm": round(
            parameters.peck_depth_mm or prepared.tool_diameter_mm * 0.75,
            9,
        ),
        "chip_break": parameters.chip_break,
        "dwell_enabled": parameters.cycle_kind == "dwell",
        "dwell_time_seconds": parameters.dwell_time_seconds or 1.0,
        "feed_retract": parameters.feed_retract,
        "add_tip_length": False,
        "depth_extension": _DEPTH_EXTENSIONS[parameters.depth_extension],
        "keep_tool_down": parameters.keep_tool_down,
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
    }
    mismatches = {
        name: {"expected": expected_value, "actual": actual.get(name)}
        for name, expected_value in expected.items()
        if actual.get(name) != expected_value
    }
    for property_name in (
        "PeckDepth",
        "DwellTime",
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
            "The created Drilling operation did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_DRILLING_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _target_summary(prepared: PreparedDrillingCreate) -> dict[str, Any]:
    return {
        "kind": "hole_targets",
        "features": [
            {
                "object_name": feature.object_name,
                "subelement": feature.subelement,
                "enabled": feature.enabled,
            }
            for feature in prepared.features
        ],
        "locations_mm": [
            {"x_mm": x_mm, "y_mm": y_mm}
            for x_mm, y_mm in prepared.locations_mm
        ],
    }


def _drilling_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedDrillingCreate,
) -> Mapping[str, Any]:
    cycle_commands = tuple(
        command
        for command in tuple(operation.Path.Commands)
        if str(command.Name) in _CYCLE_COMMANDS
    )
    expected_count = sum(feature.enabled for feature in prepared.features) + len(
        prepared.locations_mm
    )
    if len(cycle_commands) != expected_count:
        raise NativeManufactureError(
            "The created Drilling operation did not retain one canned cycle per "
            "enabled target.",
            error_code="NATIVE_MANUFACTURE_DRILLING_POSTCONDITION_FAILED",
            repair={
                "expected_cycle_count": expected_count,
                "actual_cycle_count": len(cycle_commands),
            },
        )
    parameters = prepared.parameters
    if parameters.strategy == "tapping":
        expected_command = "G84" if prepared.spindle_direction == "Forward" else "G74"
    else:
        expected_command = {
            "standard": "G81",
            "peck": "G73" if parameters.chip_break else "G83",
            "dwell": "G82",
            "feed_retract": "G85",
        }[parameters.cycle_kind]
    if any(str(command.Name) != expected_command for command in cycle_commands):
        raise NativeManufactureError(
            "The created Drilling operation generated the wrong canned cycle type.",
            error_code="NATIVE_MANUFACTURE_DRILLING_POSTCONDITION_FAILED",
            repair={
                "expected_command": expected_command,
                "actual_commands": [str(command.Name) for command in cycle_commands],
            },
        )
    return {
        "geometry": _target_summary(prepared),
        "strategy": parameters.strategy,
        "enabled_target_count": expected_count,
        "feature_count": len(prepared.features),
        "location_count": len(prepared.locations_mm),
        "cycle_command": expected_command,
        "cutting_command_count": len(cycle_commands),
        "tool_diameter_mm": prepared.tool_diameter_mm,
        "tip_length_mm": prepared.tip_length_mm,
    }


def verify_created_drilling(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrillingCreate = draft.value["drilling_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="drilling",
        assert_settings=partial(_assert_drilling_settings, prepared=prepared),
        additional_verify=partial(_drilling_result, prepared=prepared),
        minimum_cutting_commands=0,
    )


def _default_drilling_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedDrillingDefaults,
) -> Mapping[str, Any]:
    commands = tuple(getattr(operation.Path, "Commands", ()) or ())
    cycles = tuple(
        command
        for command in commands
        if str(getattr(command, "Name", "")) in _CYCLE_COMMANDS
    )
    if len(cycles) != len(prepared.features) or any(
        str(command.Name) != "G81" for command in cycles
    ):
        raise NativeManufactureError(
            "The default Drilling operation did not produce one standard cycle per target.",
            error_code="NATIVE_MANUFACTURE_DRILLING_POSTCONDITION_FAILED",
            repair={
                "expected_cycle_count": len(prepared.features),
                "actual_cycle_count": len(cycles),
                "actual_commands": [str(command.Name) for command in cycles],
            },
        )
    return {
        "parameters": {
            "source": "setup_defaults",
            "strategy": str(operation.Strategy),
            "sorting": str(operation.SortingMode),
            "start_depth_mm": quantity_mm(operation, "StartDepth"),
            "final_depth_mm": quantity_mm(operation, "FinalDepth"),
            "safe_height_mm": quantity_mm(operation, "SafeHeight"),
            "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
            "coolant": str(operation.CoolantMode),
        },
        "enabled_target_count": len(prepared.features),
        "cycle_command": "G81",
        "tool_diameter_mm": prepared.tool_diameter_mm,
    }


def verify_created_drilling_defaults(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDrillingDefaults = draft.value["drilling_defaults"]
    return verify_native_operation(
        document,
        draft,
        result_key="drilling",
        assert_settings=lambda _operation, _payload: None,
        additional_verify=partial(_default_drilling_result, prepared=prepared),
        minimum_cutting_commands=0,
    )
