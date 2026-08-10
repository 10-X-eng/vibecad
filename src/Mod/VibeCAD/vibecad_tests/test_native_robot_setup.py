# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path

import pytest

from VibeCADNativeCapabilityRegistry import NativeCapabilityRegistry
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
from VibeCADNativeRobotState import RobotSetupState, _finite


def _artifact(path: Path, request):
    return authorize_native_input_path(request, path).claim(request)


def test_robot_setup_schema_covers_only_create_without_provider_paths() -> None:
    definition = robot_setup_capability_definition()

    assert definition.name == ROBOT_SETUP_CAPABILITY_NAME
    assert len(definition.variants) == 1
    variant = definition.variants[0]
    assert variant.operation == "create"
    assert variant.action_ids == frozenset({"Robot_Create"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    schema = definition.provider_schema(("create",))
    properties = schema["parameters"]["oneOf"][0]["properties"]
    assert set(properties) == {
        "operation",
        "label",
        "expected_state_sha256",
        "expected_robot_count",
    }

    registry = NativeCapabilityRegistry()
    register_robot_setup_capability_definition(registry)
    assert registry.definition_names == (ROBOT_SETUP_CAPABILITY_NAME,)


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
