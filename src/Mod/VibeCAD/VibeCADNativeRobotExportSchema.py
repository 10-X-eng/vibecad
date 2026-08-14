# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider contract for human-authorized Robot program output."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import object_reference_schema, parameters_schema


ROBOT_EXPORT_CAPABILITY_NAME = "robot.export"
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}


def _exact_source_parameters() -> dict[str, object]:
    return parameters_schema(
        {
            "robot": object_reference_schema(),
            "trajectory": object_reference_schema(),
            "expected_robot_setup_state_sha256": _STATE_SHA256,
            "expected_robot_state_sha256": _STATE_SHA256,
            "expected_trajectory_setup_state_sha256": _STATE_SHA256,
            "expected_trajectory_state_sha256": _STATE_SHA256,
        },
        (
            "robot",
            "trajectory",
            "expected_robot_setup_state_sha256",
            "expected_robot_state_sha256",
            "expected_trajectory_setup_state_sha256",
            "expected_trajectory_state_sha256",
        ),
    )


def robot_export_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ROBOT_EXPORT_CAPABILITY_NAME,
        description=(
            "Render an exact Robot trajectory as bounded KUKA KRL and publish "
            "it only to human-authorized output destinations."
        ),
        primary_classification="export",
        variants=(
            NativeCapabilityVariant(
                operation="export_kuka_compact",
                description=(
                    "Export one exact non-empty trajectory as one compact KUKA "
                    "KRL source file."
                ),
                action_ids=frozenset({"Robot_ExportKukaCompact"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactRobotAndNonEmptyTrajectoryWithHumanAuthorizedOutput"
                ),
                transaction_behavior="output",
                background_required=False,
                parameters=_exact_source_parameters(),
            ),
            NativeCapabilityVariant(
                operation="export_kuka_full",
                description=(
                    "Export one exact non-empty trajectory as an atomic full "
                    "KUKA KRL source/data pair."
                ),
                action_ids=frozenset({"Robot_ExportKukaFull"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactRobotAndNonEmptyTrajectoryWithHumanAuthorizedOutputs"
                ),
                transaction_behavior="output",
                background_required=False,
                parameters=_exact_source_parameters(),
            ),
        ),
    )


def register_robot_export_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(robot_export_capability_definition())
