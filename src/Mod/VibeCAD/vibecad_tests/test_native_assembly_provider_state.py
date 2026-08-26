# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

import VibeCADProvider as provider
from VibeCADNativeAssemblyProviderState import provider_assembly_state


def _full_state() -> dict:
    return {
        "kind": "assembly",
        "assembly_count": 1,
        "solve_on_joint_creation": True,
        "active_assembly": {
            "document_uid": "document-uid",
            "object_name": "RobotAssembly",
            "type_id": "Assembly::AssemblyObject",
            "object_id": 41,
            "label": "Robot Assembly",
        },
        "assemblies": [
            {
                "document_uid": "document-uid",
                "object_name": "RobotAssembly",
                "type_id": "Assembly::AssemblyObject",
                "object_id": 41,
                "label": "Robot Assembly",
                "active": True,
                "state": ["Up-to-date"],
                "counts": {"components": 2, "joints": 1, "grounded": 1},
                "components": [
                    {
                        "document_uid": "document-uid",
                        "object_name": "Base",
                        "type_id": "App::Link",
                        "object_id": 42,
                        "label": "Fixed Base",
                        "state": ["Up-to-date"],
                        "grounded": True,
                        "grounded_joint": {"object_name": "GroundBase"},
                        "placement": {"origin_mm": {"x": 1, "y": 2, "z": 3}},
                        "shape": {"faces": 900, "edges": 2700},
                    },
                    {
                        "document_uid": "document-uid",
                        "object_name": "Arm",
                        "type_id": "App::Link",
                        "object_id": 43,
                        "label": "Moving Arm",
                        "state": ["Up-to-date"],
                        "grounded": False,
                        "placement": {"origin_mm": {"x": 4, "y": 5, "z": 6}},
                        "shape": {"faces": 120, "edges": 360},
                        "standard_fastener": {
                            "source": {"object_name": "ArmFastenerDefinition"},
                            "state_sha256": "9" * 64,
                            "canonical_key": "ISO4762:M6:25",
                            "part_number": "ISO4762-M6x25",
                            "standard": "ISO4762",
                            "nominal_thread": "M6",
                            "length_mm": 25.0,
                            "model_thread": False,
                            "left_handed": False,
                            "catalog_option_overrides": {},
                        },
                    },
                ],
                "joints": [
                    {
                        "document_uid": "document-uid",
                        "object_name": "Pivot",
                        "type_id": "App::FeaturePython",
                        "object_id": 44,
                        "label": "Main Pivot",
                        "joint_type": "Revolute",
                        "suppressed": False,
                        "first": {
                            "component": {"object_name": "Base"},
                            "element_path": "Face2",
                            "anchor_path": "Body.Face2",
                            "offset": {"origin_mm": {"x": 0, "y": 0, "z": 10}},
                        },
                        "second": {
                            "component": {"object_name": "Arm"},
                            "element_path": "Face5",
                            "anchor_path": "Body.Face5",
                            "offset": {"origin_mm": {"x": 0, "y": 0, "z": 10}},
                        },
                        "angular_limits": {
                            "minimum": {"enabled": True, "degrees": -45},
                            "maximum": {"enabled": True, "degrees": 45},
                        },
                    }
                ],
                "solver_health": {
                    "status": 0,
                    "remaining_degrees_of_freedom": 1,
                    "maximum_absolute_residual": 0.0,
                    "residual_tolerance": 1e-6,
                    "conflict_counts": {
                        "conflicting": 0,
                        "redundant": 0,
                        "partially_redundant": 0,
                        "malformed": 0,
                    },
                },
                "view_state": {"available": True, "state_sha256": "a" * 64, "view_count": 2, "views": [{"huge": "omit"}]},
                "simulation_state": {
                    "available": True,
                    "state_sha256": "b" * 64,
                    "simulation_count": 1,
                    "simulations": [
                        {
                            "document_uid": "document-uid",
                            "object_name": "Simulation",
                            "type_id": "App::FeaturePython",
                            "object_id": 91,
                            "label": "Turret Sweep",
                            "motion_count": 1,
                            "time_start_seconds": 0.0,
                            "time_end_seconds": 2.0,
                            "output_time_step_seconds": 0.05,
                        }
                    ],
                },
                "simulation_playback": {
                    "active": True,
                    "verified": True,
                    "playback_id": "1" * 32,
                    "simulation": {
                        "document_uid": "document-uid",
                        "object_name": "Simulation",
                        "type_id": "App::FeaturePython",
                        "object_id": 91,
                        "label": "Turret Sweep",
                    },
                    "frame": 6,
                    "frame_count": 12,
                    "time_seconds": 0.5,
                    "playing": False,
                    "direction": "paused",
                },
                "bom_state": {"available": True, "state_sha256": "c" * 64, "bom_count": 0, "boms": []},
                "diagnosis_state": {"available": True, "state_sha256": "d" * 64},
                "solver_state": {"state_sha256": "e" * 64},
                "component_joint_state": {"state_sha256": "f" * 64},
            }
        ],
        "available_component_sources": [
            {
                "document_name": "Parts",
                "object_name": "UnusedSource",
                "label": "Unused Source",
                "subassembly": False,
                "placement": {"huge": "omit"},
            }
        ],
        "robot_setup": {
            "available": True,
            "robot_count": 1,
            "robots": [
                {
                    "object": {
                        "document_uid": "omit",
                        "object_name": "CellRobot",
                        "type_id": "Robot::RobotObject",
                    },
                    "label": "Cell Robot",
                    "axes_degrees": [0, -90, 90, 0, 0, 0],
                    "home_degrees": [0, -90, 90, 0, 0, 0],
                    "tool_shape": None,
                    "suppressed": False,
                    "valid": True,
                    "state_sha256": "0" * 64,
                }
            ],
        },
        "robot_tool_shapes": {
            "available": True,
            "candidate_count": 38,
            "candidates": [
                {
                    "object": {
                        "object_name": "Gripper",
                        "type_id": "Part::Feature",
                    },
                    "label": "Parallel Gripper",
                    "geometry": {"kind": "part", "huge": "omit"},
                }
            ],
        },
        "robot_waypoint_defaults": {
            "available": True,
            "motion": {
                "speed_mm_per_s": 1000.0,
                "continuous": True,
                "acceleration_mm_per_s2": 2000.0,
            },
            "orientation": {
                "displacement_mm": [0.0, 0.0, 0.0],
                "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            },
            "state_sha256": "1" * 64,
        },
        "robot_trajectories": {
            "available": True,
            "trajectory_count": 3,
            "waypoint_count": 12,
            "trajectories": [
                {
                    "object": {
                        "object_name": "PickPath",
                        "type_id": "Robot::TrajectoryObject",
                    },
                    "label": "Pick Path",
                    "feature": {"kind": "trajectory", "huge": "omit"},
                    "waypoint_count": 4,
                    "length_mm": 450.0,
                    "duration_seconds": 2.5,
                    "suppressed": False,
                    "valid": True,
                    "usable_at_history": True,
                    "state_sha256": "2" * 64,
                }
            ],
        },
    }


def test_provider_assembly_state_is_a_small_actionable_index() -> None:
    compact = provider_assembly_state(_full_state())

    assert compact == {
        "kind": "assembly",
        "assembly_count": 1,
        "active_assembly": {"object_name": "RobotAssembly"},
        "assemblies": [
            {
                "object_name": "RobotAssembly",
                "label": "Robot Assembly",
                "active": True,
                "state": ["Up-to-date"],
                "counts": {"components": 2, "joints": 1, "grounded": 1},
                "components": [
                    {
                        "object_name": "Base",
                        "label": "Fixed Base",
                        "type_id": "App::Link",
                        "grounded": True,
                    },
                    {
                        "object_name": "Arm",
                        "label": "Moving Arm",
                        "type_id": "App::Link",
                        "grounded": False,
                        "standard_fastener": {
                            "part_number": "ISO4762-M6x25",
                            "standard": "ISO4762",
                            "nominal_thread": "M6",
                            "length_mm": 25.0,
                            "model_thread": False,
                            "left_handed": False,
                            "catalog_option_overrides": {},
                        },
                    },
                ],
                "joints": [
                    {
                        "object_name": "Pivot",
                        "label": "Main Pivot",
                        "joint_type": "Revolute",
                        "suppressed": False,
                        "first": {"component": "Base", "element": "Face2"},
                        "second": {"component": "Arm", "element": "Face5"},
                        "limits": {
                            "minimum": {"enabled": True, "degrees": -45},
                            "maximum": {"enabled": True, "degrees": 45},
                        },
                    }
                ],
                "solver": {
                    "remaining_degrees_of_freedom": 1,
                    "conflicts": {
                        "conflicting": 0,
                        "redundant": 0,
                        "partially_redundant": 0,
                        "malformed": 0,
                    },
                },
                "artifacts": {"boms": 0, "views": 2, "simulations": 1},
                "motion_studies": [
                    {
                        "object_name": "Simulation",
                        "label": "Turret Sweep",
                        "motion_count": 1,
                        "time_start_seconds": 0.0,
                        "time_end_seconds": 2.0,
                        "output_time_step_seconds": 0.05,
                    }
                ],
                "playback": {
                    "active": True,
                    "playback_id": "1" * 32,
                    "simulation": {
                        "object_name": "Simulation",
                        "label": "Turret Sweep",
                    },
                    "time_seconds": 0.5,
                    "playing": False,
                    "direction": "paused",
                },
            }
        ],
        "available_component_source_count": 1,
        "available_component_sources": [
            {
                "document_name": "Parts",
                "object_name": "UnusedSource",
                "label": "Unused Source",
                "subassembly": False,
            }
        ],
        "robot": {
            "robot_count": 1,
            "robots": [
                {
                    "object_name": "CellRobot",
                    "type_id": "Robot::RobotObject",
                    "label": "Cell Robot",
                    "axes_degrees": [0, -90, 90, 0, 0, 0],
                    "home_degrees": [0, -90, 90, 0, 0, 0],
                    "suppressed": False,
                    "valid": True,
                }
            ],
            "tool_shape_candidate_count": 38,
            "tool_shape_candidates": [
                {
                    "object_name": "Gripper",
                    "type_id": "Part::Feature",
                    "label": "Parallel Gripper",
                    "kind": "part",
                }
            ],
            "trajectory_count": 3,
            "waypoint_count": 12,
            "trajectories": [
                {
                    "object_name": "PickPath",
                    "type_id": "Robot::TrajectoryObject",
                    "label": "Pick Path",
                    "kind": "trajectory",
                    "waypoint_count": 4,
                    "length_mm": 450.0,
                    "duration_seconds": 2.5,
                    "suppressed": False,
                    "valid": True,
                    "usable_at_history": True,
                }
            ],
            "waypoint_defaults": {
                "speed_mm_per_s": 1000.0,
                "continuous": True,
                "acceleration_mm_per_s2": 2000.0,
                "orientation": {
                    "displacement_mm": [0.0, 0.0, 0.0],
                    "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
                },
            },
        },
    }
    encoded = json.dumps(compact, separators=(",", ":"))
    assert len(encoded.encode()) < 4_096
    for forbidden in (
        '"document_uid":',
        '"object_id":',
        '"placement":',
            '"shape":',
            '"state_sha256":',
            '"candidates":',
        ):
        assert forbidden not in encoded


def test_provider_assembly_state_reports_inactive_motion_playback() -> None:
    state = _full_state()
    state["assemblies"][0]["simulation_playback"] = {"active": False}

    compact = provider_assembly_state(state)

    assert compact["assemblies"][0]["playback"] == {"active": False}


def test_native_provider_context_uses_the_compact_assembly_state() -> None:
    visible = provider._model_visible_native_context(
        {
            "native_state": {
                "surface_id": "assemble",
                "document": {"document_name": "Robot"},
                "structural_revision": 7,
                "domain": _full_state(),
                "working_set": [],
            }
        }
    )

    assert visible["work"] == "assembly"
    assert visible["document"] == {"name": "Robot"}
    assert visible["state"]["domain"] == provider_assembly_state(_full_state())
    assert "robot_tool_shapes" not in visible["state"]["domain"]
