# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path

import pytest

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeInput import authorize_native_input_path
from VibeCADNativeRobotSetup import (
    NativeRobotSetupError,
    _parse_kinematics,
    _parse_visual,
    robot_kinematic_input_request,
    robot_visual_input_request,
)
from VibeCADNativeRobotSetupSchema import (
    ROBOT_SETUP_CAPABILITY_NAME,
    register_robot_setup_capability_definition,
    robot_setup_capability_definition,
)
from VibeCADNativeRobotDefaults import (
    prepare_robot_motion_defaults,
    prepare_robot_orientation_defaults,
)
from VibeCADNativeRobotState import RobotSetupState, _finite


def _artifact(path: Path, request):
    return authorize_native_input_path(request, path).claim(request)


def test_robot_setup_schema_covers_each_shipped_configuration_action() -> None:
    definition = robot_setup_capability_definition()

    assert definition.name == ROBOT_SETUP_CAPABILITY_NAME
    assert tuple(variant.operation for variant in definition.variants) == (
        "create",
        "add_tool_shape",
        "set_default_orientation",
        "set_default_values",
    )
    assert tuple(variant.action_ids for variant in definition.variants) == (
        frozenset({"Robot_Create"}),
        frozenset({"Robot_AddToolShape"}),
        frozenset({"Robot_SetDefaultOrientation"}),
        frozenset({"Robot_SetDefaultValues"}),
    )
    assert all(
        variant.surface_ids == frozenset({"assemble"})
        and variant.background_required is False
        for variant in definition.variants
    )
    assert tuple(
        variant.transaction_behavior for variant in definition.variants
    ) == ("document", "document", "session", "session")
    schema = definition.provider_schema(("create",))
    properties = schema["parameters"]["oneOf"][0]["properties"]
    assert set(properties) == {"operation", "label"}
    serialized = repr(
        definition.provider_schema(
            (
                "create",
                "add_tool_shape",
                "set_default_orientation",
                "set_default_values",
            )
        )
    ).casefold()
    assert "file_path" not in serialized
    assert "directory" not in serialized
    assert "runcommand" not in serialized
    assert "workbench" not in serialized

    registry = NativeCapabilityRegistry()
    register_robot_setup_capability_definition(registry)
    assert registry.definition_names == (ROBOT_SETUP_CAPABILITY_NAME,)


def test_robot_setup_provider_contract_contains_only_user_intent() -> None:
    published = provider_visible_native_schema(
        robot_setup_capability_definition().provider_schema(
            (
                "create",
                "add_tool_shape",
                "set_default_orientation",
                "set_default_values",
            )
        )
    )
    assert published["description"] == "Create and configure a Robot."
    schema = published["parameters"]
    serialized = repr(schema)

    assert "expected_" not in serialized
    assert "sha256" not in serialized.casefold()
    assert schema["type"] == "object"
    assert schema["required"] == ["operation"]
    properties = schema["properties"]
    assert properties["operation"]["enum"] == [
        "create",
        "add_tool_shape",
        "set_default_orientation",
        "set_default_values",
    ]
    operation_map = properties["operation"]["description"]
    assert "create=label" in operation_map
    assert "add_tool_shape=robot,tool_shape" in operation_map
    assert "set_default_orientation=placement" in operation_map
    assert (
        "set_default_values=speed_mm_per_s,continuous,acceleration_mm_per_s2"
        in operation_map
    )


def test_robot_motion_defaults_are_explicit_positive_native_units() -> None:
    spec = prepare_robot_motion_defaults(
        {
            "expected_defaults_state_sha256": "a" * 64,
            "speed_mm_per_s": 2500.0,
            "continuous": True,
            "acceleration_mm_per_s2": 7000.0,
        }
    )

    assert spec.speed_mm_per_s == 2500.0
    assert spec.continuous is True
    assert spec.acceleration_mm_per_s2 == 7000.0

    with pytest.raises(NativeRobotSetupError, match="speed"):
        prepare_robot_motion_defaults(
            {
                "expected_defaults_state_sha256": "a" * 64,
                "speed_mm_per_s": 0.0,
                "continuous": False,
                "acceleration_mm_per_s2": 1.0,
            }
        )


def test_robot_orientation_defaults_normalize_a_nonzero_axis() -> None:
    spec = prepare_robot_orientation_defaults(
        {
            "expected_defaults_state_sha256": "b" * 64,
            "placement": {
                "origin_mm": {"x": 1.0, "y": -2.0, "z": 3.0},
                "rotation": {
                    "axis": {"x": 0.0, "y": 0.0, "z": 0.5},
                    "angle_degrees": 45.0,
                },
            },
        }
    )

    assert spec.displacement_mm == (1.0, -2.0, 3.0)
    assert spec.rotation_axis == (0.0, 0.0, 1.0)
    assert spec.angle_degrees == 45.0

    with pytest.raises(NativeRobotSetupError, match="axis must be nonzero"):
        prepare_robot_orientation_defaults(
            {
                "expected_defaults_state_sha256": "b" * 64,
                "placement": {
                    "origin_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {
                        "axis": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "angle_degrees": 0.0,
                    },
                },
            }
        )


def test_robot_definition_requests_are_host_owned_and_bounded() -> None:
    visual = robot_visual_input_request()
    kinematics = robot_kinematic_input_request()

    assert visual.allowed_suffixes == (".wrl", ".vrml")
    assert kinematics.allowed_suffixes == (".csv",)
    assert visual.maximum_bytes > kinematics.maximum_bytes > 0


def test_robot_state_canonicalizes_signed_zero_without_rounding() -> None:
    assert _finite(-0.0, "Axis1") == 0.0
    assert str(_finite(-0.0, "Axis1")) == "0.0"
    assert _finite(1.0e-15, "Axis1") == 1.0e-15


def test_empty_robot_setup_summary_is_explicitly_available() -> None:
    assert RobotSetupState((), (), "0" * 64).summary() == {
        "available": True,
        "state_sha256": "0" * 64,
        "robot_count": 0,
        "robots": [],
    }


def test_robot_definition_parser_accepts_exact_six_axis_contract(
    tmp_path: Path,
) -> None:
    visual_path = tmp_path / "robot.wrl"
    visual_path.write_text("#VRML V2.0 utf8\nGroup {}\n", encoding="utf-8")
    csv_path = tmp_path / "robot.csv"
    csv_path.write_text(
        "a,alpha,d,theta,rotation,max,min,velocity\n"
        "500,-90,1045,0,-1,185,-185,156\n"
        "1300,0,0,0,1,35,-155,156\n"
        "55,90,0,-90,1,154,-130,156\n"
        "0,-90,-1025,0,1,350,-350,330\n"
        "0,90,0,0,1,130,-130,330\n"
        "0,180,-300,0,1,350,-350,615\n",
        encoding="utf-8",
    )
    visual_request = robot_visual_input_request()
    kinematic_request = robot_kinematic_input_request()

    _parse_visual(_artifact(visual_path, visual_request))
    axes = _parse_kinematics(_artifact(csv_path, kinematic_request))

    assert len(axes) == 6
    assert all(len(row) == 8 for row in axes)
    assert axes[0] == (500.0, -90.0, 1045.0, 0.0, -1.0, 185.0, -185.0, 156.0)


@pytest.mark.parametrize(
    "axis_row, message",
    (
        ("0,0,0,0,0,180,-180,100", "rotation direction"),
        ("0,0,0,0,1,-180,180,100", "minimum angle"),
        ("0,0,0,0,1,180,-180,0", "velocity"),
        ("0,0,0,0,1,180,-180,nan", "non-finite"),
    ),
)
def test_robot_kinematic_parser_rejects_unsafe_axis_contracts(
    tmp_path: Path,
    axis_row: str,
    message: str,
) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text(
        "a,alpha,d,theta,rotation,max,min,velocity\n"
        + "\n".join([axis_row] * 6)
        + "\n",
        encoding="utf-8",
    )
    request = robot_kinematic_input_request()

    with pytest.raises(NativeRobotSetupError, match=message):
        _parse_kinematics(_artifact(path, request))
