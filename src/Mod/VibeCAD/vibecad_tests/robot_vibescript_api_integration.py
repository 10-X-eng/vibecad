# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native production gate for the canonical Robot VibeScript domain."""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import sys
import tempfile

MODULE_ROOT = Path(__file__).resolve().parent.parent
while str(MODULE_ROOT) in sys.path:
    sys.path.remove(str(MODULE_ROOT))
sys.path.insert(0, str(MODULE_ROOT))

import FreeCAD as App  # noqa: E402
import Robot  # noqa: E402

del Robot

from VibeCADModelingSurface import resolve_modeling_surface  # noqa: E402
import VibeCADVibeScriptDomainPublication as publication_module  # noqa: E402
from VibeCADVibeScriptDomainPublication import (  # noqa: E402
    PROP_ROBOT_VALIDATION,
    delete_live_program,
    publish_candidate,
)
from VibeCADVibeScriptDomainRuntime import (  # noqa: E402
    RobotDomainAdapter,
    accept_candidate,
    complete_inspection,
    execute_candidate,
    finish_delete,
    prepare_candidate,
    prepare_delete,
    restore_prepared_delete,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomains import (  # noqa: E402
    PROP_PROGRAM_ID,
    complete_domain_context,
    domain_context_snapshot,
    get_vibescript_pack,
)
from vibescript_robot_api import RobotAPIError, RobotDomainAPI  # noqa: E402
from vibescript_robot_worker import (  # noqa: E402
    RobotCandidateError,
    SIMULATION_SCHEMA,
    VALIDATION_SCHEMA,
    validate_and_build_robot,
    validate_robot_definition,
)


EXPORTS = ("robot", "waypoint", "trajectory", "dressup", "simulate")
OUTPUT_TYPES = ("robot", "trajectory", "dressup", "simulation")
EXPECTED_OUTPUTS = [
    {"name": "Robot", "type": "robot"},
    {"name": "Trajectory", "type": "trajectory"},
    {"name": "DressUp", "type": "dressup"},
    {"name": "Simulation", "type": "simulation"},
]
START_ROTATION = [
    0.7071067811865476,
    -8.659560562354933e-17,
    0.7071067811865475,
    4.3297802811774664e-17,
]


class _Service:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _active_document():
        return App.ActiveDocument

    @staticmethod
    def active_workbench_name() -> str:
        return "RobotWorkbench"

    @staticmethod
    def modeling_engine() -> str:
        return "vibescript"

    @staticmethod
    def provider_document_revision() -> str:
        return "robot-native-fixture-revision"

    def project_scope_snapshot(self) -> dict[str, str]:
        return {"root": str(self.root), "project_id": "robot-native-fixture"}

    @staticmethod
    def provider_working_set() -> dict[str, object]:
        return {"target_count": 0, "targets": []}

    @staticmethod
    def selection_summary() -> dict[str, object]:
        return {"selection": []}


def _api() -> RobotDomainAPI:
    return RobotDomainAPI(EXPORTS, OUTPUT_TYPES)


def _expect_error(fragment: str, call) -> None:
    try:
        call()
    except (TypeError, ValueError, RuntimeError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"Expected Robot failure containing {fragment!r}.")


def _expect_candidate_error(stage: str, call) -> RobotCandidateError:
    try:
        call()
    except RobotCandidateError as exc:
        assert exc.details.get("stage") == stage, exc.details
        assert str(exc.details.get("correction") or "").strip(), exc.details
        return exc
    raise AssertionError(f"Expected Robot candidate failure at {stage!r}.")


def _exercise_source_api() -> None:
    api = _api()
    assert api.exported_names == EXPORTS
    for redundant in (
        "output",
        "waypoints",
        "path",
        "motion",
        "simulate_to_file",
        "load_robot",
    ):
        assert not hasattr(api, redundant)
    for name in EXPORTS:
        signature = str(inspect.signature(getattr(api, name)))
        assert "*args" not in signature and "**" not in signature
        assert inspect.getdoc(getattr(api, name))
    robot = api.robot(axis_positions=[0, -90, 90, 0, 0, 0])
    start = api.waypoint(
        {"position": [1825, 0, 2400], "rotation": START_ROTATION},
        name="Start",
        velocity=100,
        acceleration=100,
    )
    finish = api.waypoint(
        {"position": [1825, 100, 2400], "rotation": START_ROTATION},
        name="Finish",
        velocity=100,
        acceleration=100,
    )
    trajectory = api.trajectory([start, finish])
    dressup = api.dressup(trajectory, speed=80)
    simulation = api.simulate(robot, dressup, require_reachable=False)
    assert [
        validate_robot_definition(value)["operation"]
        for value in (robot, trajectory, dressup, simulation)
    ] == ["robot", "trajectory", "dressup", "simulate"]
    _expect_error(
        "never a file path",
        lambda: api.robot(kinematics="/tmp/robot.csv"),
    )
    _expect_error(
        "ASCII identifier",
        lambda: api.waypoint(
            [0, 0, 0],
            name="unsafe waypoint",
            velocity=1,
            acceleration=1,
        ),
    )
    _expect_error(
        "must have type",
        lambda: api.dressup(dressup, speed=20),
    )
    _expect_error(
        "2-512",
        lambda: api.trajectory([start]),
    )
    try:
        api.waypoint(
            [0, 0, 0],
            name="InvalidSpeed",
            velocity=0,
            acceleration=1,
        )
    except RobotAPIError as exc:
        assert exc.details["stage"] == "source_validation"
        assert exc.details["operation"] == "waypoint"
        assert exc.details["parameter"] == "velocity"
        assert "Change only the failing source expression" in exc.details["correction"]
    else:
        raise AssertionError("Expected one structured Robot source failure.")

    pack = get_vibescript_pack("RobotWorkbench")
    assert pack is not None
    description = RobotDomainAdapter(pack).describe_api()
    assert description["api_contract"] == "vibecad-vibescript-robot-api-v1"
    assert "flange_in_robot_base" in description["placement_and_frame_contract"][
        "simulation_formula"
    ]
    assert "incoming segment" in description["waypoint_contract"][
        "ordered_semantics"
    ]
    assert "no collision checking" in description["simulation_contract"][
        "not_simulated"
    ]
    assert "active workbench determines" in description["workbench_handoffs"]["rule"]
    assert len(json.dumps(description, allow_nan=False).encode("utf-8")) < 32 * 1024


def _assert_placement_close(observed: dict[str, object], expected) -> None:
    expected_state = _placement_state(expected)
    for group in ("position", "rotation"):
        left = [float(value) for value in observed[group]]
        right = expected_state[group]
        if group == "rotation":
            dot = sum(a * b for a, b in zip(left, right))
            assert abs(abs(dot) - 1.0) <= 1.0e-8, (observed, expected_state)
        else:
            assert all(abs(a - b) <= 1.0e-8 for a, b in zip(left, right)), (
                observed,
                expected_state,
            )


def _exercise_frame_contract() -> None:
    api = _api()
    robot_base = {
        "position": [120.0, -75.0, 30.0],
        "rotation": [0.0, 0.0, 0.3826834323650898, 0.9238795325112867],
    }
    trajectory_base = {
        "position": [250.0, 40.0, 15.0],
        "rotation": [0.0, 0.0, -0.25881904510252074, 0.9659258262890683],
    }
    tool = {
        "position": [25.0, 0.0, 80.0],
        "rotation": [0.0, 0.13052619222005157, 0.0, 0.9914448613738104],
    }
    robot = api.robot(
        axis_positions=[0, -90, 90, 0, 0, 0],
        base=robot_base,
        tool=tool,
    )
    start = api.waypoint(
        {"position": [1825, 0, 2400], "rotation": START_ROTATION},
        name="FrameStart",
        velocity=100,
        acceleration=100,
    )
    finish = api.waypoint(
        {"position": [1825, 100, 2400], "rotation": START_ROTATION},
        name="FrameFinish",
        velocity=100,
        acceleration=100,
    )
    path = api.trajectory([start, finish], base=trajectory_base)
    evidence = api.simulate(
        robot,
        path,
        sample_period=0.1,
        maximum_samples=32,
        require_reachable=False,
    )
    expected_outputs = [
        {"name": "Robot", "type": "robot"},
        {"name": "Path", "type": "trajectory"},
        {"name": "Evidence", "type": "simulation"},
    ]
    with tempfile.TemporaryDirectory(prefix="vibecad-robot-frame-") as directory:
        root = Path(directory)
        (root / "outputs").mkdir()
        document = App.newDocument("VibeScriptRobotFrame", "Robot frame gate", True, True)
        try:
            _expect_candidate_error(
                "result_contract",
                lambda: validate_and_build_robot(
                    document,
                    {
                        "Robot": robot,
                        "Path": path,
                        "Evidence": evidence,
                        "Unexpected": evidence,
                    },
                    expected_outputs,
                    root,
                ),
            )
            outputs, _validation = validate_and_build_robot(
                document,
                {"Robot": robot, "Path": path, "Evidence": evidence},
                expected_outputs,
                root,
            )
            data = outputs[2]["robot_data"]
            path_target = App.Placement(
                App.Vector(1825, 0, 2400), App.Rotation(*START_ROTATION)
            )
            native_robot_base = App.Placement(
                App.Vector(*robot_base["position"]),
                App.Rotation(*robot_base["rotation"]),
            )
            native_trajectory_base = App.Placement(
                App.Vector(*trajectory_base["position"]),
                App.Rotation(*trajectory_base["rotation"]),
            )
            native_tool = App.Placement(
                App.Vector(*tool["position"]), App.Rotation(*tool["rotation"])
            )
            world_tool_target = native_trajectory_base * path_target
            flange_target = (
                native_robot_base.inverse()
                * world_tool_target
                * native_tool.inverse()
            )
            _assert_placement_close(data["start_path_target"], path_target)
            _assert_placement_close(
                data["start_world_tool_target"], world_tool_target
            )
            _assert_placement_close(data["start_target"], flange_target)
            assert data["native_trace"]["base_and_tool_frames_applied"] is True
            assert data["coordinate_frames"]["robot_base"]["position"] == (
                robot_base["position"]
            )
        finally:
            App.closeDocument(document.Name)


def _input_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "axis_1": {"type": "number", "minimum": -30, "maximum": 30},
            "end_y": {"type": "number", "minimum": 10, "maximum": 500},
            "speed": {"type": "number", "minimum": 0, "maximum": 1000},
            "dressup_speed": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 1000,
            },
        },
        "required": ["axis_1", "end_y", "speed", "dressup_speed"],
        "additionalProperties": False,
    }


def _program_source(*, label_prefix: str = "Native") -> str:
    return (
        "rotation = [0.7071067811865476, -8.659560562354933e-17, "
        "0.7071067811865475, 4.3297802811774664e-17]\n"
        "robot = api.robot(axis_positions=[inputs['axis_1'], -90, 90, 0, 0, 0], "
        f"label='{label_prefix} robot')\n"
        "start = api.waypoint({'position':[1825, 0, 2400], 'rotation':rotation}, "
        "name='Start', velocity=inputs['speed'], acceleration=inputs['speed'])\n"
        "finish = api.waypoint({'position':[1825, inputs['end_y'], 2400], "
        "'rotation':rotation}, name='Finish', velocity=inputs['speed'], "
        "acceleration=inputs['speed'])\n"
        "trajectory = api.trajectory([start, finish], "
        f"label='{label_prefix} trajectory')\n"
        "dressup = api.dressup(trajectory, speed=inputs['dressup_speed'], "
        f"label='{label_prefix} dress-up')\n"
        "simulation = api.simulate(robot, dressup, sample_period=0.05, "
        "maximum_samples=256, require_reachable=False, "
        f"label='{label_prefix} simulation')\n"
        "result = {'Robot':robot, 'Trajectory':trajectory, 'DressUp':dressup, "
        "'Simulation':simulation}\n"
    )


def _captured(
    root: Path,
    document,
    *,
    operation: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    pack = get_vibescript_pack("RobotWorkbench")
    assert pack is not None
    return {
        "tool_name": f"vibescript.robot.{operation}",
        "operation": operation,
        "arguments": arguments,
        "pack": pack,
        "project_root": str(root),
        "project_id": "robot-native-fixture",
        "document_name": str(document.Name),
        "document_uid": str(document.Uid),
        "document_revision": "robot-native-fixture-revision",
        "document_objects": [
            {"name": obj.Name, "label": obj.Label, "type_id": obj.TypeId}
            for obj in document.Objects
        ],
        "live_programs": [],
        "surface": resolve_modeling_surface(
            "RobotWorkbench", "vibescript"
        ).summary(),
        "freecad_home": str(App.getHomePath()),
        "timeout_seconds": 90.0,
        "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
    }


def _prepare_execute_validate(captured: dict[str, object]):
    prepared = prepare_candidate(captured)
    staged_names = {path.name for path in Path(prepared["staging"]).iterdir()}
    assert staged_names == {
        "request.json",
        "worker.py",
        "vibescript_domain_api.py",
        "vibescript_robot_api.py",
        "vibescript_robot_worker.py",
    }, sorted(staged_names)
    assert prepared["reference_requirements"] == []
    execution = execute_candidate(prepared, cancellation_check=None)
    validated = validate_candidate(prepared, execution) if execution.get("ok") else None
    return prepared, execution, validated


def _run_candidate(captured: dict[str, object], service: _Service):
    prepared, execution, validated = _prepare_execute_validate(captured)
    assert execution.get("ok") is True, execution
    assert validated is not None
    retain_candidate(prepared, status="validated")
    publication = publish_candidate(service, prepared, validated)
    accepted = accept_candidate(prepared, publication)
    return prepared, execution, validated, publication, accepted


def _outputs(document, accepted: dict[str, object]) -> dict[str, object]:
    result = {}
    for name, details in accepted["live_outputs"].items():
        obj = document.getObject(details["object_name"])
        assert obj is not None, (name, details)
        result[name] = obj
    return result


def _managed_names(document, program_id: str) -> set[str]:
    return {
        str(obj.Name)
        for obj in document.Objects
        if str(getattr(obj, PROP_PROGRAM_ID, "") or "") == program_id
    }


def _placement_state(value) -> dict[str, list[float]]:
    return {
        "position": [float(item) for item in value.Base],
        "rotation": [float(item) for item in value.Rotation.Q],
    }


def _snapshot(obj) -> dict[str, object]:
    output_type = str(getattr(obj, "VibeCADVibeScriptOutputType", "") or "")
    result: dict[str, object] = {
        "name": str(obj.Name),
        "type_id": str(obj.TypeId),
        "output_type": output_type,
        "label": str(obj.Label),
        "revision": str(obj.VibeCADVibeScriptRevision),
        "definition": str(obj.VibeCADVibeScriptDefinition),
        "validation": str(getattr(obj, PROP_ROBOT_VALIDATION)),
        "derived_state": str(obj.VibeCADDerivedState),
        "human_note": str(getattr(obj, "HumanRobotNote", "") or ""),
        "human_length": float(getattr(obj, "HumanRobotLength", 0.0) or 0.0),
        "expressions": [
            [str(path), str(expression)]
            for path, expression in list(obj.ExpressionEngine or [])
        ],
        "managed_properties": sorted(
            name
            for name in list(obj.PropertiesList or [])
            if str(obj.getGroupOfProperty(name) or "") == "VibeCAD"
        ),
    }
    if output_type == "robot":
        result["native"] = {
            "axes": [float(getattr(obj, f"Axis{axis}")) for axis in range(1, 7)],
            "home": [float(value) for value in obj.Home],
            "base": _placement_state(obj.Base),
            "tool": _placement_state(obj.Tool),
            "tcp": _placement_state(obj.Tcp),
            "kinematic_file": str(obj.RobotKinematicFile or ""),
        }
    elif output_type in {"trajectory", "dressup"}:
        trajectory = obj.Trajectory
        native = {
            "waypoint_count": len(trajectory.Waypoints),
            "length": float(trajectory.Length),
            "duration": float(trajectory.Duration),
            "base": _placement_state(obj.Base),
            "waypoints": [
                {
                    "name": str(value.Name),
                    "type": str(value.Type),
                    "position": _placement_state(value.Pos),
                    "velocity": float(value.Velocity),
                    "acceleration": float(value.Acceleration),
                    "continuous": bool(value.Cont),
                    "tool": int(value.Tool),
                    "base": int(value.Base),
                }
                for value in trajectory.Waypoints
            ],
        }
        if output_type == "dressup":
            native.update(
                {
                    "source": str(obj.Source.Name),
                    "use_speed": bool(obj.UseSpeed),
                    "speed": float(obj.Speed),
                    "use_acceleration": bool(obj.UseAcceleration),
                    "acceleration": float(obj.Acceleration),
                    "continuous": str(obj.ContType),
                    "add_type": str(obj.AddType),
                    "offset": _placement_state(obj.PosAdd),
                    "frozen": bool(obj.isFrozen()),
                }
            )
        result["native"] = native
    elif output_type == "simulation":
        result["native"] = {
            "robot": str(obj.VibeCADRobot.Name),
            "trajectory": str(obj.VibeCADTrajectory.Name),
            "duration": float(obj.VibeCADDuration),
            "length": float(obj.VibeCADLength),
            "sample_count": int(obj.VibeCADSampleCount),
            "reachable_count": int(obj.VibeCADReachableCount),
            "unreachable_count": int(obj.VibeCADUnreachableCount),
            "samples_limited": bool(obj.VibeCADSamplesLimited),
            "artifact_sha256": str(obj.VibeCADArtifactSHA256),
        }
    else:
        raise AssertionError(f"Unexpected Robot output type {output_type!r}.")
    return result


def _assert_snapshot(obj, expected: dict[str, object]) -> None:
    observed = _snapshot(obj)
    if observed != expected:
        differences = {
            key: {"observed": observed.get(key), "expected": expected.get(key)}
            for key in sorted(set(observed) | set(expected))
            if observed.get(key) != expected.get(key)
        }
        raise AssertionError(json.dumps(differences, sort_keys=True, default=str))


def _add_human_state(outputs: dict[str, object]) -> None:
    for index, (name, obj) in enumerate(outputs.items(), start=1):
        obj.addProperty(
            "App::PropertyString",
            "HumanRobotNote",
            "Human",
            "Human-authored state that Robot regeneration and rollback must preserve.",
        )
        obj.HumanRobotNote = f"preserve {name}"
        obj.addProperty(
            "App::PropertyLength",
            "HumanRobotLength",
            "Human",
            "Human-authored expression-backed property.",
        )
        obj.HumanRobotLength = float(index)
        obj.setExpression("HumanRobotLength", f"{index} mm + 2 mm")


def _exercise_native_kinematic_persistence() -> None:
    with tempfile.TemporaryDirectory(prefix="vibecad-robot-kinematic-") as directory:
        path = Path(directory) / "custom-kinematic.FCStd"
        document = App.newDocument("RobotKinematicPersistence")
        try:
            obj = document.addObject("Robot::RobotObject", "CustomRobot")
            rows = [
                [500, -90, 1045, 0, 1, 170, -170, 111],
                [1300, 0, 0, 0, -1, 30, -140, 112],
                [55, 90, 0, -90, 1, 140, -120, 113],
                [0, -90, -1025, 0, -1, 300, -300, 114],
                [0, 90, 0, 0, 1, 120, -120, 115],
                [0, 180, -300, 0, -1, 300, -300, 116],
            ]
            obj.setKinematic(rows)
            expected_axes = [20.0, -80.0, 70.0, 10.0, -15.0, 25.0]
            for axis, value in enumerate(expected_axes, start=1):
                setattr(obj, f"Axis{axis}", value)
            expected_tcp = _placement_state(obj.Tcp)
            probe = obj.getRobot()
            probe.Tcp = obj.Tcp
            document.saveAs(str(path))
        finally:
            App.closeDocument(document.Name)
        reopened = App.openDocument(str(path))
        try:
            obj = reopened.getObject("CustomRobot")
            assert obj is not None and type(obj).__name__ == "RobotObject"
            assert [float(getattr(obj, f"Axis{axis}")) for axis in range(1, 7)] == (
                expected_axes
            )
            observed_tcp = _placement_state(obj.Tcp)
            for group in ("position", "rotation"):
                assert all(
                    abs(left - right) <= 1.0e-8
                    for left, right in zip(observed_tcp[group], expected_tcp[group])
                )
            probe = obj.getRobot()
            probe.Tcp = obj.Tcp
        finally:
            App.closeDocument(reopened.Name)


def _exercise_lifecycle() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="vibecad-robot-native-") as directory:
        root = Path(directory)
        document = App.newDocument("VibeScriptRobotNative")
        service = _Service(root)
        try:
            App.setActiveDocument(document.Name)
            pack = get_vibescript_pack("RobotWorkbench")
            assert pack is not None and pack.production_ready
            surface = resolve_modeling_surface("RobotWorkbench", "vibescript")
            assert surface.available is True, surface.unavailable_reason
            assert surface.cad_tool_names == tuple(
                f"vibescript.robot.{name}"
                for name in (
                    "create_program",
                    "edit_source",
                    "set_inputs",
                    "reconfigure_program",
                    "delete_program",
                )
            )
            initial_inputs = {
                "axis_1": 0.0,
                "end_y": 100.0,
                "speed": 100.0,
                "dressup_speed": 80.0,
            }
            create_capture = _captured(
                root,
                document,
                operation="create_program",
                arguments={
                    "program_name": "Native Robot Lifecycle",
                    "source": _program_source(),
                    "input_schema": _input_schema(),
                    "inputs": initial_inputs,
                    "expected_outputs": EXPECTED_OUTPUTS,
                },
            )
            prepared, execution, validated = _prepare_execute_validate(create_capture)
            assert execution.get("ok") is True, execution
            assert validated is not None
            assert execution["robot_validation"]["schema"] == VALIDATION_SCHEMA
            simulation = execution["outputs"][3]
            assert simulation["artifact_schema"] == SIMULATION_SCHEMA
            assert simulation["sample_count"] >= 2
            malformed = copy.deepcopy(execution)
            malformed["robot_validation"]["output_count"] = 3
            _expect_error(
                "output count",
                lambda: validate_candidate(prepared, malformed),
            )
            malformed = copy.deepcopy(execution)
            malformed["outputs"][1]["robot_data"]["duration"] += 1.0
            _expect_error(
                "differs",
                lambda: validate_candidate(prepared, malformed),
            )
            malformed = copy.deepcopy(execution)
            malformed["outputs"][3]["artifact_sha256"] = "0" * 64
            _expect_error(
                "unauthenticated",
                lambda: validate_candidate(prepared, malformed),
            )

            retain_candidate(prepared, status="validated")
            publication = publish_candidate(service, prepared, validated)
            accepted = accept_candidate(prepared, publication)
            outputs = _outputs(document, accepted)
            stable_names = {name: str(obj.Name) for name, obj in outputs.items()}
            assert {name: str(obj.TypeId) for name, obj in outputs.items()} == {
                "Robot": "Robot::RobotObject",
                "Trajectory": "Robot::TrajectoryObject",
                "DressUp": "Robot::TrajectoryDressUpObject",
                "Simulation": "App::FeaturePython",
            }
            assert _managed_names(document, prepared["program_id"]) == set(
                stable_names.values()
            )
            assert outputs["DressUp"].Source is outputs["Trajectory"]
            assert outputs["DressUp"].isFrozen() is True
            assert "Touched" not in set(outputs["DressUp"].State)
            assert outputs["Simulation"].VibeCADRobot is outputs["Robot"]
            assert outputs["Simulation"].VibeCADTrajectory is outputs["DressUp"]
            assert (
                outputs["Simulation"].VibeCADReachableCount
                + outputs["Simulation"].VibeCADUnreachableCount
                == outputs["Simulation"].VibeCADSampleCount
            )
            accepted_duration = float(outputs["DressUp"].Trajectory.Duration)
            document.recompute()
            assert float(outputs["DressUp"].Trajectory.Duration) == accepted_duration
            assert outputs["DressUp"].isFrozen() is True
            assert "Touched" not in set(outputs["DressUp"].State)
            _add_human_state(outputs)

            consumer = document.addObject("App::FeaturePython", "HumanRobotConsumer")
            consumer.addProperty("App::PropertyLinkList", "Sources")
            consumer.Sources = list(outputs.values())
            inspected = complete_inspection(
                {
                    **create_capture,
                    "program_id": prepared["program_id"],
                    "live_programs": [],
                }
            )
            assert inspected["program"]["live_outputs"]["Simulation"]["robot_data"]

            failed_capture = _captured(
                root,
                document,
                operation="set_inputs",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": accepted["working_revision"],
                    "patch": {"speed": 0.0},
                },
            )
            failed_prepared, failed_execution, failed_validated = (
                _prepare_execute_validate(failed_capture)
            )
            assert failed_validated is None
            assert failed_execution["ok"] is False
            assert failed_execution["failure_code"] == "DOMAIN_CANDIDATE_FAILED"
            assert failed_execution["domain_failure_stage"] == "source_validation"
            failure_details = failed_execution["observed"]["details"]
            assert failure_details["operation"] == "waypoint"
            assert failure_details["parameter"] == "velocity"
            assert failed_execution["retry"]["required_changes"] == [
                failure_details["correction"]
            ]
            assert "greater than 0" in failure_details["correction"]
            retain_candidate(
                failed_prepared,
                status="failed",
                failure=failed_execution,
            )
            assert all(
                str(obj.VibeCADVibeScriptRevision) == accepted["accepted_revision"]
                for obj in outputs.values()
            )

            recovery_capture = _captured(
                root,
                document,
                operation="set_inputs",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": failed_prepared["revision"],
                    "patch": {"speed": 125.0, "end_y": 150.0},
                },
            )
            recovery_prepared, _, _, recovery_publication, accepted = _run_candidate(
                recovery_capture, service
            )
            assert recovery_publication["created_objects"] == []
            outputs = _outputs(document, accepted)
            assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
            assert consumer.Sources == list(outputs.values())
            for name, obj in outputs.items():
                assert obj.HumanRobotNote == f"preserve {name}"

            reconfigure_capture = _captured(
                root,
                document,
                operation="reconfigure_program",
                arguments={
                    "program_id": prepared["program_id"],
                    "expected_revision": accepted["working_revision"],
                    "source": _program_source(label_prefix="Reconfigured"),
                    "input_schema": _input_schema(),
                    "inputs": dict(recovery_prepared["inputs"]),
                    "expected_outputs": EXPECTED_OUTPUTS,
                },
            )
            reconfigured, reconfigured_execution, reconfigured_validated = (
                _prepare_execute_validate(reconfigure_capture)
            )
            assert reconfigured_execution.get("ok") is True
            assert reconfigured_validated is not None
            retain_candidate(reconfigured, status="validated")
            before_fault = {name: _snapshot(obj) for name, obj in outputs.items()}
            original_configure = publication_module._configure_robot

            def fail_after_simulation(obj, item, live_outputs, trajectory_swaps):
                original_configure(obj, item, live_outputs, trajectory_swaps)
                if item["name"] == "Simulation":
                    raise RuntimeError("injected Robot publication failure")

            publication_module._configure_robot = fail_after_simulation
            try:
                _expect_error(
                    "injected Robot publication failure",
                    lambda: publish_candidate(
                        service, reconfigured, reconfigured_validated
                    ),
                )
            finally:
                publication_module._configure_robot = original_configure
            outputs = {
                name: document.getObject(object_name)
                for name, object_name in stable_names.items()
            }
            assert all(obj is not None for obj in outputs.values())
            for name, obj in outputs.items():
                _assert_snapshot(obj, before_fault[name])
            assert consumer.Sources == list(outputs.values())

            reconfigured_publication = publish_candidate(
                service, reconfigured, reconfigured_validated
            )
            accepted = accept_candidate(reconfigured, reconfigured_publication)
            outputs = _outputs(document, accepted)
            assert {name: str(obj.Name) for name, obj in outputs.items()} == stable_names
            assert all(str(obj.Label).startswith("Reconfigured") for obj in outputs.values())
            assert outputs["DressUp"].isFrozen() is True
            assert consumer.Sources == list(outputs.values())

            context = complete_domain_context(domain_context_snapshot(service, "robot"))
            assert context["domain"] == "robot"
            assert context["document_robots"]["object_count"] == 4
            assert "inspection_document" not in context
            assert "part_document_shapes" not in context

            save_path = root / "robot-production.FCStd"
            document.saveAs(str(save_path))
            App.closeDocument(document.Name)
            reopened = App.openDocument(str(save_path))
            assert reopened is not None
            App.setActiveDocument(reopened.Name)
            outputs = {
                name: reopened.getObject(object_name)
                for name, object_name in stable_names.items()
            }
            assert all(obj is not None for obj in outputs.values())
            assert type(outputs["Robot"]).__name__ == "RobotObject"
            assert outputs["DressUp"].isFrozen() is True
            reopened_duration = float(outputs["DressUp"].Trajectory.Duration)
            reopened.recompute()
            assert float(outputs["DressUp"].Trajectory.Duration) == reopened_duration
            assert outputs["DressUp"].isFrozen() is True
            assert "Touched" not in set(outputs["DressUp"].State)
            for name, obj in outputs.items():
                assert obj.HumanRobotNote == f"preserve {name}"
                assert json.loads(str(getattr(obj, PROP_ROBOT_VALIDATION)))[
                    "schema"
                ] == VALIDATION_SCHEMA

            consumer = reopened.getObject("HumanRobotConsumer")
            assert consumer is not None and consumer.Sources == list(outputs.values())
            delete_capture = _captured(
                root,
                reopened,
                operation="delete_program",
                arguments={
                    "program_id": reconfigured["program_id"],
                    "expected_revision": reconfigured["revision"],
                    "reason": "verify Robot external-reference guard",
                },
            )
            prepared_delete = prepare_delete(delete_capture)
            _expect_error(
                "reference",
                lambda: delete_live_program(service, prepared_delete),
            )
            restore_prepared_delete(prepared_delete)
            consumer.Sources = []
            reopened.removeObject(consumer.Name)

            before_delete_fault = {
                name: _snapshot(obj) for name, obj in outputs.items()
            }
            delete_capture = _captured(
                root,
                reopened,
                operation="delete_program",
                arguments={
                    "program_id": reconfigured["program_id"],
                    "expected_revision": reconfigured["revision"],
                    "reason": "exercise explicit Robot deletion rollback",
                },
            )
            prepared_delete = prepare_delete(delete_capture)
            original_remove = publication_module._remove_owned_objects

            def fail_after_committed_removal(active_document, managed_objects):
                original_remove(active_document, managed_objects)
                active_document.commitTransaction()
                raise RuntimeError("injected Robot deletion failure")

            publication_module._remove_owned_objects = fail_after_committed_removal
            try:
                try:
                    delete_live_program(service, prepared_delete)
                except RuntimeError as exc:
                    assert "injected Robot deletion failure" in str(exc)
                    assert "rollback failure" not in str(exc).lower(), str(exc)
                else:
                    raise AssertionError("Expected injected Robot deletion failure.")
                restore_prepared_delete(prepared_delete)
            finally:
                publication_module._remove_owned_objects = original_remove
            outputs = {
                name: reopened.getObject(object_name)
                for name, object_name in stable_names.items()
            }
            assert all(obj is not None for obj in outputs.values())
            for name, obj in outputs.items():
                _assert_snapshot(obj, before_delete_fault[name])

            delete_capture = _captured(
                root,
                reopened,
                operation="delete_program",
                arguments={
                    "program_id": reconfigured["program_id"],
                    "expected_revision": reconfigured["revision"],
                    "reason": "Robot production integration complete",
                },
            )
            prepared_delete = prepare_delete(delete_capture)
            finished = finish_delete(
                prepared_delete,
                delete_live_program(service, prepared_delete),
            )
            assert finished["ok"] is True
            assert not _managed_names(reopened, reconfigured["program_id"])
            App.closeDocument(reopened.Name)
            return {
                "canonical_nonredundant_api": True,
                "stable_native_outputs": True,
                "failed_candidate_retention": True,
                "model_correctable_failure_recovery": True,
                "exact_worker_host_validation": True,
                "exact_result_contract": True,
                "model_first_api_description": True,
                "explicit_coordinate_frames": True,
                "precomputed_trajectory_swap": True,
                "frozen_dressup": True,
                "explicit_publication_rollback": True,
                "explicit_deletion_rollback": True,
                "save_reopen": True,
                "external_reference_guard": True,
                "isolated_domain_context": True,
            }
        finally:
            if App.ActiveDocument is not None:
                App.closeDocument(App.ActiveDocument.Name)


def main() -> int:
    _exercise_source_api()
    _exercise_frame_contract()
    _exercise_native_kinematic_persistence()
    result = _exercise_lifecycle()
    print(json.dumps({"integration": "robot_vibescript_api", "ok": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
