# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Robot trajectories."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeRobotTrajectoryRuntime import NativeRobotTrajectoryRuntime
from VibeCADNativeRobotTrajectorySchema import ROBOT_TRAJECTORY_CAPABILITY_NAME
from VibeCADNativeRobotTrajectorySchema import (
    ROBOT_EDGE_PATH_CAPABILITY_NAME,
    ROBOT_PATH_MOTION_CAPABILITY_NAME,
    ROBOT_PATH_SEQUENCE_CAPABILITY_NAME,
)


_EDGE_PATH_DEFAULTS = {
    "segmentation_mm": 0.5,
    "use_rotation": False,
}
def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeRobotTrajectoryRuntime):
        raise TypeError("A Robot trajectory call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Robot trajectory call requires argument data.")
    return runtime.mutate_trajectory(arguments, ticket=ticket)


def _mutate_focused(
    call: Any,
    *,
    feature_operation: str,
    create_operation: str,
    edit_operation: str,
    defaults: Mapping[str, Any],
) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeRobotTrajectoryRuntime):
        raise TypeError("A Robot path call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Robot path call requires argument data.")
    values = dict(arguments)
    requested_operation = str(values.pop("operation", "") or "")
    if requested_operation == create_operation:
        mode = "create"
    elif requested_operation == edit_operation:
        mode = "edit"
    else:
        raise ValueError("The Robot path operation is unavailable.")
    target = values.pop("target", None)
    expanded = {
        "operation": feature_operation,
        "mode": mode,
        "target": target,
        **defaults,
        **values,
    }
    return runtime.mutate_trajectory(expanded, ticket=ticket)


def _mutate_edge_path(call: Any) -> Mapping[str, Any]:
    return _mutate_focused(
        call,
        feature_operation="edge2_trac",
        create_operation="create_path",
        edit_operation="edit_path",
        defaults=_EDGE_PATH_DEFAULTS,
    )


def _mutate_path_motion(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeRobotTrajectoryRuntime):
        raise TypeError("A Robot path call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Robot path call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "set_motion":
        raise ValueError("The Robot path motion operation is unavailable.")
    return runtime.set_path_motion(values, ticket=ticket)


def _mutate_path_sequence(call: Any) -> Mapping[str, Any]:
    return _mutate_focused(
        call,
        feature_operation="trajectory_compound",
        create_operation="create_sequence",
        edit_operation="edit_sequence",
        defaults={},
    )


def register_robot_trajectory_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ROBOT_TRAJECTORY_CAPABILITY_NAME, _mutate)
    )


def register_robot_path_feature_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name, handler in (
        (ROBOT_EDGE_PATH_CAPABILITY_NAME, _mutate_edge_path),
        (ROBOT_PATH_MOTION_CAPABILITY_NAME, _mutate_path_motion),
        (ROBOT_PATH_SEQUENCE_CAPABILITY_NAME, _mutate_path_sequence),
    ):
        registry.register_implementation(NativeCapabilityImplementation(name, handler))


def robot_trajectory_runtime_bindings(
    runtime: NativeRobotTrajectoryRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeRobotTrajectoryRuntime):
        raise TypeError("runtime must be a NativeRobotTrajectoryRuntime")
    return {
        ROBOT_TRAJECTORY_CAPABILITY_NAME: runtime,
        ROBOT_EDGE_PATH_CAPABILITY_NAME: runtime,
        ROBOT_PATH_MOTION_CAPABILITY_NAME: runtime,
        ROBOT_PATH_SEQUENCE_CAPABILITY_NAME: runtime,
    }
