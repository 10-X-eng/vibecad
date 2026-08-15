# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeActionManifest import _operation_variant
from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeRobotMotion import (
    MAX_SIMULATION_SAMPLES,
    NativeRobotMotionError,
    prepare_robot_home_spec,
    prepare_robot_simulation_spec,
)
from VibeCADNativeRobotMotionSchema import (
    ROBOT_MOTION_CAPABILITY_NAME,
    register_robot_motion_capability_definition,
    robot_motion_capability_definition,
)


def _simulation_values(times: list[float]) -> dict[str, object]:
    return {
        "robot": {"object_name": "Robot"},
        "trajectory": {"object_name": "Trajectory"},
        "sample_times_s": times,
        "expected_setup_state_sha256": "a" * 64,
        "expected_robot_state_sha256": "b" * 64,
        "expected_trajectory_setup_state_sha256": "c" * 64,
        "expected_trajectory_state_sha256": "d" * 64,
    }


def test_robot_motion_schema_covers_every_final_robot_action() -> None:
    definition = robot_motion_capability_definition()

    assert definition.name == ROBOT_MOTION_CAPABILITY_NAME
    assert tuple(variant.operation for variant in definition.variants) == (
        "set_home_pos",
        "restore_home_pos",
        "simulate",
    )
    assert tuple(variant.action_ids for variant in definition.variants) == (
        frozenset({"Robot_SetHomePos"}),
        frozenset({"Robot_RestoreHomePos"}),
        frozenset({"Robot_Simulate"}),
    )
    assert tuple(variant.surface_ids for variant in definition.variants) == (
        frozenset({"assemble"}),
        frozenset({"assemble"}),
        frozenset({"assemble", "manufacture"}),
    )
    assert tuple(variant.transaction_behavior for variant in definition.variants) == (
        "document",
        "document",
        "session",
    )
    assert all(variant.background_required is False for variant in definition.variants)
    assert _operation_variant("Robot_SetHomePos") == "set_home_pos"
    assert _operation_variant("Robot_RestoreHomePos") == "restore_home_pos"
    assert _operation_variant("Robot_Simulate") == "simulate"
    serialized = repr(
        definition.provider_schema(("set_home_pos", "restore_home_pos", "simulate"))
    ).casefold()
    for forbidden in (
        "file_path",
        "directory",
        "runcommand",
        "workbench",
        "selection",
        "dialog",
        "command_id",
    ):
        assert forbidden not in serialized

    registry = NativeCapabilityRegistry()
    register_robot_motion_capability_definition(registry)
    assert registry.definition_names == (ROBOT_MOTION_CAPABILITY_NAME,)


@pytest.mark.parametrize("operation", ("set_home_pos", "restore_home_pos"))
def test_robot_home_specs_require_only_exact_target_state(operation: str) -> None:
    spec = prepare_robot_home_spec(
        "document-uid",
        operation,
        {
            "robot": {"object_name": "Robot"},
            "expected_setup_state_sha256": "a" * 64,
            "expected_robot_state_sha256": "b" * 64,
        },
    )

    assert spec.operation == operation
    assert spec.robot_ref.document_uid == "document-uid"
    assert spec.robot_ref.object_name == "Robot"

    with pytest.raises(NativeRobotMotionError, match="fields are incorrect"):
        prepare_robot_home_spec(
            "document-uid",
            operation,
            {
                "robot": {"object_name": "Robot"},
                "expected_setup_state_sha256": "a" * 64,
                "expected_robot_state_sha256": "b" * 64,
                "axis_1": 45.0,
            },
        )


def test_robot_simulation_times_are_bounded_ordered_and_float32_exact() -> None:
    spec = prepare_robot_simulation_spec(
        "document-uid",
        _simulation_values([0.0, 0.25, 1.0]),
    )

    assert spec.robot_ref.object_name == "Robot"
    assert spec.trajectory_ref.object_name == "Trajectory"
    assert spec.sample_times_s == (0.0, 0.25, 1.0)

    with pytest.raises(NativeRobotMotionError, match="strictly increasing"):
        prepare_robot_simulation_spec(
            "document-uid",
            _simulation_values([0.5, 0.5]),
        )
    with pytest.raises(NativeRobotMotionError, match="float32 conversion"):
        prepare_robot_simulation_spec(
            "document-uid",
            _simulation_values([1.0, 1.0 + 1.0e-9]),
        )
    with pytest.raises(NativeRobotMotionError, match="1 to"):
        prepare_robot_simulation_spec(
            "document-uid",
            _simulation_values(
                [float(index) for index in range(MAX_SIMULATION_SAMPLES + 1)]
            ),
        )
    with pytest.raises(NativeRobotMotionError, match="lowercase SHA-256"):
        prepare_robot_simulation_spec(
            "document-uid",
            {
                **_simulation_values([0.0]),
                "expected_robot_state_sha256": "BAD",
            },
        )
