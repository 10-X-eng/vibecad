# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM holding-tag dress-up."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureDressupSupport import (
    MAX_DRESSUP_COMMANDS,
    PreparedDressupBase,
    assert_dressup_preflight_current,
    command_path_sha256,
    cutting_command_count,
    dressup_error,
    preflight_dressup_base,
    publish_dressup_replacement,
    verify_dressup_envelope,
)
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import exact_fields, finite_number
from VibeCADNativeManufactureState import (
    operation_state,
    persistent_resource_state,
    resolve_operation_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


MAX_NATIVE_HOLDING_TAGS = 256
MAX_NATIVE_HOLDING_TAG_SCAN_UNITS = 5_000_000
_PLACEMENT_FIELDS = {
    "explicit_locations": frozenset({"kind", "shape", "tags"}),
    "automatic_distribution": frozenset(
        {
            "kind",
            "shape",
            "minimum_per_wire",
            "maximum_for_longest_wire",
        }
    ),
    "copy_enabled_from_dressup": frozenset(
        {"kind", "source_tag_dressup"}
    ),
}
_SHAPE_FIELDS = frozenset(
    {
        "material_width_mm",
        "material_height_mm",
        "side_angle_from_horizontal_degrees",
        "top_fillet_radius_mm",
    }
)
_LOCATION_FIELDS = frozenset({"x_mm", "y_mm", "enabled"})


@dataclass(frozen=True, slots=True)
class TagDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    placement: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedTagDressup:
    base: PreparedDressupBase
    placement_kind: str
    shape: Any
    locations: tuple[Any, ...]
    minimum_per_wire: int
    maximum_for_longest_wire: int
    source: Any | None
    source_reference_before: Mapping[str, Any] | None
    source_state_before: Mapping[str, Any] | None
    positions_xyz_mm: tuple[tuple[float, float, float], ...]
    disabled_indices: tuple[int, ...]
    effective_heights_mm: tuple[float, ...]
    effective_fillet_radii_mm: tuple[float, ...]
    mapped_segment_count: int
    bottom_wire_count: int
    bottom_edge_count: int
    scan_units: int
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str


def _strict_bool(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        dressup_error(f"{noun} must be a boolean.")
    return value


def _bounded_dimension(value: Any, noun: str, *, allow_zero: bool = False) -> float:
    result = finite_number(value, noun)
    valid = result >= 0.0 if allow_zero else result > 0.0
    if not valid or result > 1_000_000.0:
        relation = "between 0 and" if allow_zero else "greater than 0 and no more than"
        dressup_error(f"{noun} must be {relation} 1,000,000 mm.")
    return result


def _shape(value: Any):
    from Path.Dressup.TagGeneration import HoldingTagShape

    item = exact_fields(value, _SHAPE_FIELDS, "CAM holding-tag shape")
    angle = finite_number(
        item["side_angle_from_horizontal_degrees"],
        "CAM holding-tag side angle",
    )
    if not 0.1 <= angle <= 90.0:
        dressup_error(
            "CAM holding-tag side angle must be between 0.1 and 90 degrees from horizontal."
        )
    return HoldingTagShape(
        width_mm=_bounded_dimension(
            item["material_width_mm"],
            "CAM holding-tag material width",
        ),
        height_mm=_bounded_dimension(
            item["material_height_mm"],
            "CAM holding-tag material height",
        ),
        angle_degrees=angle,
        fillet_radius_mm=_bounded_dimension(
            item["top_fillet_radius_mm"],
            "CAM holding-tag top fillet radius",
            allow_zero=True,
        ),
    )


def _integer(value: Any, noun: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        dressup_error(f"{noun} must be an integer between 1 and 64.")
    if not 1 <= value <= 64:
        dressup_error(f"{noun} must be between 1 and 64.")
    return value


def _explicit_locations(value: Any) -> tuple[Any, ...]:
    from Path.Dressup.TagGeneration import HoldingTagLocation

    if not isinstance(value, list) or not 1 <= len(value) <= MAX_NATIVE_HOLDING_TAGS:
        dressup_error(
            f"CAM holding tags require between 1 and {MAX_NATIVE_HOLDING_TAGS} explicit locations."
        )
    locations = []
    for index, raw in enumerate(value):
        item = exact_fields(raw, _LOCATION_FIELDS, f"CAM holding tag {index}")
        x = finite_number(item["x_mm"], f"CAM holding tag {index} X")
        y = finite_number(item["y_mm"], f"CAM holding tag {index} Y")
        if abs(x) > 1_000_000.0 or abs(y) > 1_000_000.0:
            dressup_error(
                f"CAM holding tag {index} coordinates must be within ±1,000,000 mm."
            )
        locations.append(
            HoldingTagLocation(
                x_mm=x,
                y_mm=y,
                enabled=_strict_bool(
                    item["enabled"],
                    f"CAM holding tag {index} enabled",
                ),
            )
        )
    if not any(location.enabled for location in locations):
        dressup_error("CAM holding tags require at least one enabled location.")
    return tuple(locations)


def _resolve_source(document: Any, value: Any, base: PreparedDressupBase):
    from VibeCADNativeManufactureDressupSupport import normalize_exact_target
    import Path.Dressup.Tags as Tags

    target = normalize_exact_target(value, "CAM source holding-tag dress-up")
    try:
        source, reference = resolve_operation_target(document, target)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The source holding-tag dress-up could not be inspected.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if reference.get("state_sha256") != target["expected_state_sha256"]:
        dressup_error(
            "The source holding-tag dress-up changed after turn start.",
            "NATIVE_MANUFACTURE_STATE_STALE",
            repair={
                "object_name": str(source.Name),
                "current_state_sha256": reference.get("state_sha256"),
            },
        )
    if (
        source is base.base
        or not isinstance(getattr(source, "Proxy", None), Tags.ObjectTagDressup)
        or not source.isValid()
        or not getattr(source, "Base", None)
        or len(tuple(source.Positions or ())) > MAX_NATIVE_HOLDING_TAGS
        or len(tuple(getattr(getattr(source, "Path", None), "Commands", ()) or ()))
        > MAX_DRESSUP_COMMANDS
    ):
        dressup_error(
            "source_tag_dressup must be one distinct, valid holding-tag dress-up with "
            f"no more than {MAX_NATIVE_HOLDING_TAGS} stored positions.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    positions = tuple(source.Positions or ())
    disabled_values = tuple(source.Disabled or ())
    disabled = tuple(int(index) for index in disabled_values)
    if (
        len(disabled) != len(set(disabled))
        or any(index < 0 or index >= len(positions) for index in disabled)
    ):
        dressup_error(
            "The source holding-tag dress-up has malformed disabled-tag indices.",
            "NATIVE_MANUFACTURE_STATE_INVALID",
        )
    enabled_count = len(positions) - len(disabled)
    if enabled_count <= 0:
        dressup_error("The source holding-tag dress-up has no enabled tag to copy.")
    return source, reference, persistent_resource_state(source)


def _positive_controller_rates(base: PreparedDressupBase) -> None:
    controller = base.controller
    missing = []
    for property_name, public_name in (
        ("HorizFeed", "horizontal_feed"),
        ("VertFeed", "vertical_feed"),
        ("HorizRapid", "horizontal_rapid"),
        ("VertRapid", "vertical_rapid"),
    ):
        if float(getattr(controller, property_name).Value) <= 0.0:
            missing.append(public_name)
    diameter = float(controller.Tool.Diameter)
    if diameter <= 0.0:
        missing.append("tool_diameter")
    if missing:
        dressup_error(
            "CAM holding tags require positive feed, rapid, and tool-diameter values "
            "on the inherited tool controller.",
            "NATIVE_MANUFACTURE_MACHINE_PARAMETERS_UNAVAILABLE",
            repair={
                "tool_controller_name": str(controller.Name),
                "required_positive_properties": sorted(missing),
            },
        )


def preflight_tag_dressup(
    document: Any,
    spec: TagDressupSpec,
) -> PreparedTagDressup:
    """Freeze exact inputs and prepare the complete detached holding-tag path."""

    if not isinstance(spec, TagDressupSpec):
        raise TypeError("spec must be a TagDressupSpec")
    base = preflight_dressup_base(
        document,
        label=spec.label,
        job_target=spec.job,
        base_target=spec.base_operation,
        noun="CAM holding-tag dress-up",
    )
    if not isinstance(spec.placement, Mapping):
        dressup_error("CAM holding-tag placement must be one closed request.")
    kind = str(spec.placement.get("kind") or "")
    fields = _PLACEMENT_FIELDS.get(kind)
    if fields is None:
        dressup_error(
            "CAM holding-tag placement kind must be explicit_locations, "
            "automatic_distribution, or copy_enabled_from_dressup."
        )
    placement = exact_fields(spec.placement, fields, f"CAM holding-tag {kind}")
    _positive_controller_rates(base)
    source = None
    source_reference = None
    source_state = None
    minimum = 2
    maximum = 4

    try:
        from Path.Dressup.TagGeneration import (
            HoldingTagShape,
            automatic_locations,
            copied_locations,
            path_data_for_base,
            prepare_holding_tag_path,
        )

        path_data = path_data_for_base(base.base)
        if kind == "copy_enabled_from_dressup":
            source, source_reference, source_state = _resolve_source(
                document,
                placement["source_tag_dressup"],
                base,
            )
            shape = HoldingTagShape(
                width_mm=float(source.Width.Value),
                height_mm=float(source.Height.Value),
                angle_degrees=float(source.Angle.Value),
                fillet_radius_mm=float(source.Radius.Value),
            )
            # Validate inherited durable data through the same strict Native rules.
            shape = _shape(
                {
                    "material_width_mm": shape.width_mm,
                    "material_height_mm": shape.height_mm,
                    "side_angle_from_horizontal_degrees": shape.angle_degrees,
                    "top_fillet_radius_mm": shape.fillet_radius_mm,
                }
            )
            locations = copied_locations(path_data, source, shape)
        else:
            shape = _shape(placement["shape"])
            if kind == "explicit_locations":
                locations = _explicit_locations(placement["tags"])
            else:
                minimum = _integer(
                    placement["minimum_per_wire"],
                    "CAM holding-tag minimum_per_wire",
                )
                maximum = _integer(
                    placement["maximum_for_longest_wire"],
                    "CAM holding-tag maximum_for_longest_wire",
                )
                if minimum > maximum:
                    dressup_error(
                        "CAM holding-tag minimum_per_wire must not exceed "
                        "maximum_for_longest_wire."
                    )
                if len(path_data.baseWires) * maximum > MAX_NATIVE_HOLDING_TAGS:
                    dressup_error(
                        "The automatic holding-tag distribution could exceed the "
                        f"{MAX_NATIVE_HOLDING_TAGS}-tag safety limit for this path.",
                        "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
                        repair={
                            "bottom_wire_count": len(path_data.baseWires),
                            "maximum_for_longest_wire": maximum,
                        },
                    )
                locations = automatic_locations(
                    path_data,
                    shape,
                    minimum_per_wire=minimum,
                    maximum_for_longest_wire=maximum,
                )
        if not locations:
            dressup_error("CAM holding-tag placement produced no usable location.")
        prepared_path = prepare_holding_tag_path(
            base.base,
            shape,
            locations,
            path_data=path_data,
            max_tags=MAX_NATIVE_HOLDING_TAGS,
            max_scan_units=MAX_NATIVE_HOLDING_TAG_SCAN_UNITS,
            max_output_commands=MAX_DRESSUP_COMMANDS,
        )
    except NativeManufactureError:
        raise
    except Exception as exc:
        message = str(exc)
        code = (
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE"
            if "limit" in message.lower() or "exceed" in message.lower()
            else "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
        )
        raise NativeManufactureError(
            "The exact CAM holding-tag toolpath could not be prepared.",
            error_code=code,
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": message[:320],
            },
        ) from exc

    reasons = dict(prepared_path.disabled_reasons)
    rejected = [
        {
            "index": index,
            "reason": reasons.get(index, "unknown"),
            "x_mm": round(float(locations[index].x_mm), 9),
            "y_mm": round(float(locations[index].y_mm), 9),
        }
        for index in prepared_path.disabled
        if locations[index].enabled
    ]
    if rejected:
        dressup_error(
            "One or more requested enabled holding tags cannot be applied safely at "
            "their resolved bottom-path locations.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            repair={"rejected_tags": rejected},
        )
    enabled_count = len(prepared_path.tags) - len(prepared_path.disabled)
    if enabled_count <= 0:
        dressup_error("CAM holding-tag placement retained no enabled tag.")
    commands = tuple(prepared_path.path.Commands or ())
    cutting = cutting_command_count(commands)
    if cutting <= 0:
        dressup_error(
            "CAM holding tags did not retain a usable cutting path.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    expected_hash = command_path_sha256(commands, "CAM holding-tag dress-up")
    import PathScripts.PathUtils as PathUtils

    source_commands = tuple(PathUtils.getPathWithPlacement(base.base).Commands or ())
    if expected_hash == command_path_sha256(source_commands, "CAM holding-tag base"):
        dressup_error(
            "The requested enabled holding tags do not change the exact base toolpath.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    return PreparedTagDressup(
        base=base,
        placement_kind=kind,
        shape=shape,
        locations=tuple(locations),
        minimum_per_wire=minimum,
        maximum_for_longest_wire=maximum,
        source=source,
        source_reference_before=source_reference,
        source_state_before=source_state,
        positions_xyz_mm=tuple(
            (
                round(float(position.x), 9),
                round(float(position.y), 9),
                round(float(position.z), 9),
            )
            for position in prepared_path.positions
        ),
        disabled_indices=prepared_path.disabled,
        effective_heights_mm=tuple(
            round(float(tag.actualHeight), 9) for tag in prepared_path.tags
        ),
        effective_fillet_radii_mm=tuple(
            round(float(tag.realRadius), 9) for tag in prepared_path.tags
        ),
        mapped_segment_count=len(prepared_path.mappers),
        bottom_wire_count=len(prepared_path.path_data.baseWires),
        bottom_edge_count=prepared_path.edge_count,
        scan_units=prepared_path.scan_units,
        expected_command_count=len(commands),
        expected_cutting_count=cutting,
        expected_path_sha256=expected_hash,
    )


def _assert_source_current(prepared: PreparedTagDressup) -> None:
    if prepared.source is None:
        return
    if (
        operation_state(prepared.source)
        != prepared.source_reference_before
        or persistent_resource_state(prepared.source) != prepared.source_state_before
    ):
        dressup_error(
            "The source holding-tag dress-up changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def create_tag_dressup(
    document: Any,
    *,
    prepared: PreparedTagDressup,
) -> NativeMutationDraft:
    """Create and configure one exact holding-tag replacement."""

    if not isinstance(prepared, PreparedTagDressup):
        raise TypeError("prepared must be a PreparedTagDressup")
    assert_dressup_preflight_current(document, prepared.base)
    _assert_source_current(prepared)
    base = prepared.base
    try:
        import FreeCAD
        import Path.Dressup.Gui.Tags as TagsGui

        operation = TagsGui.CreateInTransaction(base.base, hide_base=False)
        operation.Label = base.label
        operation.Width = prepared.shape.width_mm
        operation.Height = prepared.shape.height_mm
        operation.Angle = prepared.shape.angle_degrees
        operation.Radius = prepared.shape.fillet_radius_mm
        operation.Positions = [
            FreeCAD.Vector(x, y, z) for x, y, z in prepared.positions_xyz_mm
        ]
        operation.Disabled = list(prepared.disabled_indices)
        operation.Approximation = False
        operation.Proxy.minCount = prepared.minimum_per_wire
        operation.Proxy.maxCount = prepared.maximum_for_longest_wire
        publish_dressup_replacement(document, base, operation)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM holding-tag factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "operation": operation},
        recompute_targets=(operation,),
        created=(object_identity(operation),),
        changed=(object_identity(base.job),),
        replaced=(object_identity(base.base),),
    )


def verify_created_tag_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact tag properties, path, source preservation, and lifecycle state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedTagDressup) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM holding-tag dress-up")
    base = prepared.base

    import Path.Dressup.Gui.Tags as TagsGui

    actual_label, state, commands, after_job = verify_dressup_envelope(
        document,
        prepared=base,
        operation=operation,
        proxy_type=TagsGui.PathDressupTag.ObjectTagDressup,
        view_proxy_type=TagsGui.PathDressupTagViewProvider,
        expected_command_count=prepared.expected_command_count,
        expected_cutting_count=prepared.expected_cutting_count,
        expected_path_sha256=prepared.expected_path_sha256,
    )
    positions = tuple(
        (
            round(float(position.x), 9),
            round(float(position.y), 9),
            round(float(position.z), 9),
        )
        for position in tuple(operation.Positions or ())
    )
    tags = tuple(operation.Proxy.tags or ())
    actual_heights = tuple(round(float(tag.actualHeight), 9) for tag in tags)
    actual_radii = tuple(round(float(tag.realRadius), 9) for tag in tags)
    actual_center = tuple(round(float(value), 9) for value in operation.Path.Center)
    expected_center = tuple(round(float(value), 9) for value in base.job.Path.Center)
    if (
        round(float(operation.Width.Value), 9)
        != round(prepared.shape.width_mm, 9)
        or round(float(operation.Height.Value), 9)
        != round(prepared.shape.height_mm, 9)
        or round(float(operation.Angle.Value), 9)
        != round(prepared.shape.angle_degrees, 9)
        or round(float(operation.Radius.Value), 9)
        != round(prepared.shape.fillet_radius_mm, 9)
        or bool(operation.Approximation)
        or positions != prepared.positions_xyz_mm
        or tuple(int(index) for index in operation.Disabled)
        != prepared.disabled_indices
        or int(operation.Proxy.minCount) != prepared.minimum_per_wire
        or int(operation.Proxy.maxCount) != prepared.maximum_for_longest_wire
        or len(tags) != len(prepared.locations)
        or actual_heights != prepared.effective_heights_mm
        or actual_radii != prepared.effective_fillet_radii_mm
        or len(tuple(operation.Proxy.mappers or ()))
        != prepared.mapped_segment_count
        or actual_center != expected_center
    ):
        dressup_error(
            "The created CAM holding-tag dress-up did not retain its exact shape, "
            "locations, enablement, mapped path, or rotary center.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    _assert_source_current(prepared)
    clipped_radius_count = sum(
        1
        for radius in prepared.effective_fillet_radii_mm
        if radius < round(prepared.shape.fillet_radius_mm, 9)
    )
    limited_height_count = sum(
        1
        for height in prepared.effective_heights_mm
        if height < round(prepared.shape.height_mm, 9)
    )
    placement: dict[str, Any] = {"kind": prepared.placement_kind}
    if prepared.placement_kind == "automatic_distribution":
        placement.update(
            minimum_per_wire=prepared.minimum_per_wire,
            maximum_for_longest_wire=prepared.maximum_for_longest_wire,
        )
    elif prepared.source is not None:
        placement["source_object_name"] = str(prepared.source.Name)
    return {
        "operation": "tag_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(base.job.Name),
        "base_operation_name": str(base.base.Name),
        "placement": placement,
        "shape": {
            "material_width_mm": prepared.shape.width_mm,
            "material_height_mm": prepared.shape.height_mm,
            "side_angle_from_horizontal_degrees": prepared.shape.angle_degrees,
            "top_fillet_radius_mm": prepared.shape.fillet_radius_mm,
        },
        "tag_count": len(tags),
        "enabled_tag_count": len(tags) - len(prepared.disabled_indices),
        "disabled_tag_count": len(prepared.disabled_indices),
        "disabled_indices": list(prepared.disabled_indices),
        "limited_height_count": limited_height_count,
        "clipped_fillet_count": clipped_radius_count,
        "bottom_wire_count": prepared.bottom_wire_count,
        "mapped_segment_count": prepared.mapped_segment_count,
        "command_count": len(commands),
        "cutting_command_count": prepared.expected_cutting_count,
        "path_center_mm": list(actual_center),
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
