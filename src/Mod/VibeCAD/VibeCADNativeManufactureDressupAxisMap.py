# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Axis Map dress-up."""

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
from VibeCADNativeManufactureOperationSupport import finite_number
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


_AXIS_MAPS = {
    "x_to_a": "X->A",
    "y_to_a": "Y->A",
    "x_to_b": "X->B",
    "y_to_b": "Y->B",
    "x_to_c": "X->C",
    "y_to_c": "Y->C",
}
_EPSILON = 1.0e-9


@dataclass(frozen=True, slots=True)
class AxisMapDressupSpec:
    label: Any
    job: Mapping[str, Any]
    base_operation: Mapping[str, Any]
    axis_mapping: Any
    radius_mm: Any
    reverse: Any


@dataclass(frozen=True, slots=True)
class PreparedAxisMapDressup:
    base: PreparedDressupBase
    axis_mapping: str
    native_axis_map: str
    radius_mm: float
    reverse: bool
    mapped_command_count: int
    expected_command_count: int
    expected_cutting_count: int
    expected_path_sha256: str
    center_targets: tuple[Any, ...]
    path_centers_before: tuple[tuple[Any, tuple[float, float, float]], ...]


def _path_center(obj: Any) -> tuple[float, float, float]:
    center = obj.Path.Center
    return tuple(round(float(getattr(center, axis)), 9) for axis in ("x", "y", "z"))


def _path_center_snapshot(document: Any) -> tuple[tuple[Any, tuple[float, float, float]], ...]:
    result = []
    for obj in tuple(document.Objects):
        path = getattr(obj, "Path", None)
        if path is not None and hasattr(path, "Center"):
            result.append((obj, _path_center(obj)))
    return tuple(result)


def _job_center_targets(job: Any) -> tuple[Any, ...]:
    values = [job]
    values.extend(tuple(job.Proxy.allOperations()))
    seen = set()
    result = []
    for obj in values:
        if id(obj) not in seen:
            seen.add(id(obj))
            result.append(obj)
    return tuple(result)


def preflight_axis_map_dressup(
    document: Any,
    spec: AxisMapDressupSpec,
) -> PreparedAxisMapDressup:
    """Freeze one exact base and prepare its complete rotary remapping."""

    if not isinstance(spec, AxisMapDressupSpec):
        raise TypeError("spec must be an AxisMapDressupSpec")
    axis_mapping = str(spec.axis_mapping or "")
    if axis_mapping not in _AXIS_MAPS:
        dressup_error(
            "CAM Axis Map axis_mapping must be x_to_a, y_to_a, x_to_b, y_to_b, "
            "x_to_c, or y_to_c."
        )
    radius = finite_number(
        spec.radius_mm,
        "CAM Axis Map radius_mm",
        minimum=0.0,
        maximum=1_000_000.0,
    )
    if radius <= _EPSILON:
        dressup_error("CAM Axis Map radius_mm must be greater than zero.")
    if not isinstance(spec.reverse, bool):
        dressup_error("CAM Axis Map reverse must be true or false.")

    base = preflight_dressup_base(
        document,
        label=spec.label,
        job_target=spec.job,
        base_target=spec.base_operation,
        noun="CAM Axis Map dress-up",
    )
    centers_before = _path_center_snapshot(document)
    center_targets = _job_center_targets(base.job)
    try:
        import Path.Dressup.Gui.AxisMap as AxisMapGui

        existing_radii = sorted(
            {
                round(float(operation.Radius.Value), 9)
                for operation in center_targets
                if isinstance(
                    getattr(operation, "Proxy", None),
                    AxisMapGui.ObjectDressup,
                )
            }
        )
        if existing_radii and existing_radii != [radius]:
            dressup_error(
                "CAM Axis Map operations in one Job must use one shared wrap radius.",
                "NATIVE_MANUFACTURE_AXIS_MAP_RADIUS_CONFLICT",
                repair={
                    "existing_radius_mm": (
                        existing_radii[0] if len(existing_radii) == 1 else existing_radii
                    ),
                    "requested_radius_mm": radius,
                },
            )
        expected_path, mapped_count = AxisMapGui.remapPathWithMetadata(
            base.base,
            _AXIS_MAPS[axis_mapping],
            radius,
            spec.reverse,
        )
        commands = tuple(expected_path.Commands or ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The exact CAM Axis Map toolpath could not be prepared.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        ) from exc
    cutting = cutting_command_count(commands)
    if mapped_count <= 0:
        dressup_error(
            f"The selected operation has no {_AXIS_MAPS[axis_mapping][0]} coordinates "
            "for the requested Axis Map.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if len(commands) > MAX_DRESSUP_COMMANDS:
        dressup_error(
            f"CAM Axis Map would generate {len(commands)} commands; the safety limit is "
            f"{MAX_DRESSUP_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    if cutting <= 0:
        dressup_error(
            "CAM Axis Map requires a base that produces at least one cutting command.",
            "NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        )
    return PreparedAxisMapDressup(
        base=base,
        axis_mapping=axis_mapping,
        native_axis_map=_AXIS_MAPS[axis_mapping],
        radius_mm=radius,
        reverse=spec.reverse,
        mapped_command_count=mapped_count,
        expected_command_count=len(commands),
        expected_cutting_count=cutting,
        expected_path_sha256=command_path_sha256(
            commands,
            "CAM Axis Map dress-up",
        ),
        center_targets=center_targets,
        path_centers_before=centers_before,
    )


def _assert_preflight_current(
    document: Any,
    prepared: PreparedAxisMapDressup,
) -> None:
    assert_dressup_preflight_current(document, prepared.base)
    if (
        _path_center_snapshot(document) != prepared.path_centers_before
        or _job_center_targets(prepared.base.job) != prepared.center_targets
    ):
        dressup_error(
            "The CAM Axis Map Job center state changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def create_axis_map_dressup(
    document: Any,
    *,
    prepared: PreparedAxisMapDressup,
) -> NativeMutationDraft:
    """Create and configure one Axis Map replacement in the owned transaction."""

    if not isinstance(prepared, PreparedAxisMapDressup):
        raise TypeError("prepared must be a PreparedAxisMapDressup")
    _assert_preflight_current(document, prepared)
    base = prepared.base
    try:
        import Path.Dressup.Gui.AxisMap as AxisMapGui

        operation = AxisMapGui.CreateInTransaction(
            base.base,
            hide_base=False,
        )
        operation.Label = base.label
        operation.AxisMap = prepared.native_axis_map
        operation.Radius = prepared.radius_mm
        operation.Reverse = prepared.reverse
        base.job.Proxy.setCenterOfRotation(operation.Proxy.center(operation))
        publish_dressup_replacement(document, base, operation)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Axis Map factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc

    expected_center = (0.0, 0.0, round(-prepared.radius_mm, 9))
    changed = [base.job]
    for obj in prepared.center_targets:
        if obj is base.job or obj is base.base:
            continue
        before = next(
            center
            for candidate, center in prepared.path_centers_before
            if candidate is obj
        )
        if before != expected_center:
            changed.append(obj)
    return NativeMutationDraft(
        value={"prepared": prepared, "operation": operation},
        recompute_targets=(*prepared.center_targets, operation),
        created=(object_identity(operation),),
        changed=tuple(object_identity(obj) for obj in changed),
        replaced=(object_identity(base.base),),
    )


def verify_created_axis_map_dressup(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact remapping, rotary center propagation, and replacement state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedAxisMapDressup) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Axis Map dress-up")
    base = prepared.base

    import Path.Dressup.Gui.AxisMap as AxisMapGui

    actual_label, state, commands, after_job = verify_dressup_envelope(
        document,
        prepared=base,
        operation=operation,
        proxy_type=AxisMapGui.ObjectDressup,
        view_proxy_type=AxisMapGui.ViewProviderDressup,
        expected_command_count=prepared.expected_command_count,
        expected_cutting_count=prepared.expected_cutting_count,
        expected_path_sha256=prepared.expected_path_sha256,
    )
    if (
        str(operation.AxisMap) != prepared.native_axis_map
        or round(float(operation.Radius.Value), 9) != prepared.radius_mm
        or bool(operation.Reverse) is not prepared.reverse
    ):
        dressup_error(
            "The created CAM Axis Map dress-up did not retain its exact mapping settings.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    expected_center = (0.0, 0.0, round(-prepared.radius_mm, 9))
    expected_targets = (*prepared.center_targets, operation)
    if any(_path_center(obj) != expected_center for obj in expected_targets):
        dressup_error(
            "The CAM Axis Map dress-up did not publish its exact Job rotary center.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={
                "expected_center_mm": expected_center,
                "actual_centers_mm": {
                    str(obj.Name): _path_center(obj) for obj in expected_targets
                },
            },
        )
    target_ids = {id(obj) for obj in prepared.center_targets}
    if any(
        _path_center(obj) != center
        for obj, center in prepared.path_centers_before
        if id(obj) not in target_ids
    ):
        dressup_error(
            "CAM Axis Map changed a path center outside its exact Job.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    output_axis = prepared.native_axis_map[3]
    return {
        "operation": "axis_map_dressup",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(base.job.Name),
        "base_operation_name": str(base.base.Name),
        "axis_mapping": prepared.axis_mapping,
        "input_axis": prepared.native_axis_map[0],
        "output_axis": output_axis,
        "radius_mm": prepared.radius_mm,
        "reverse": prepared.reverse,
        "mapped_command_count": prepared.mapped_command_count,
        "command_count": len(commands),
        "cutting_command_count": prepared.expected_cutting_count,
        "rotary_center_mm": list(expected_center),
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
