# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import ast
import copy
import inspect

import pytest

import VibeCADMechanismEngine as mechanism_engine
from VibeCADMechanismEngine import (
    MECHANISM_SCENARIO_SCHEMA,
    MECHANISM_SOLVE_REPORT_SCHEMA,
    MECHANISM_STATIC_CHECK_SCHEMA,
    MECHANISM_VERIFICATION_REPORT_SCHEMA,
    MechanismContractError,
    evaluate_static_mechanism_check,
    evaluate_mechanism_scenario,
    mechanism_scenario_sha256,
    mechanism_static_check_sha256,
    normalize_mechanism_scenario,
    normalize_mechanism_solve_report,
    normalize_mechanism_static_check,
    normalize_mechanism_verification_report,
)
from VibeCADMechanismGeometry import (
    MechanismGeometryError,
    measure_static_component_pairs,
)


JOINT_TYPES = (
    "fixed",
    "revolute",
    "cylindrical",
    "slider",
    "ball",
    "distance",
    "parallel",
    "perpendicular",
    "angle",
    "rack_pinion",
    "screw",
    "gears",
    "belt",
)


def _placement(x: float = 0.0) -> dict:
    return {
        "position": [x, 0.0, 0.0],
        "rotation": [0.0, 0.0, 0.0, 1.0],
    }


def _connector(component_id: str) -> dict:
    return {
        "component_id": component_id,
        "selection": {"type": "component_origin"},
        "occurrence_path": None,
        "anchor": None,
        "offset": _placement(),
    }


def _component(component_id: str, *, grounded: bool = False) -> dict:
    return {
        "id": component_id,
        "label": component_id,
        "source": {
            "kind": "document_object",
            "document_uid": "source-document",
            "object_name": f"{component_id}Source",
            "document_path": f"parts/{component_id}.FCStd",
        },
        "initial_placement": _placement(),
        "grounded": grounded,
        "flexible": False,
    }


def _scenario() -> dict:
    components = [_component("Base", grounded=True)]
    joints = []
    for index, kind in enumerate(JOINT_TYPES, start=1):
        component_id = f"Part{index}"
        components.append(_component(component_id))
        parameters = {
            "distance": {"distance_mm": 8.0},
            "angle": {"angle_degrees": 30.0},
            "rack_pinion": {"pitch_radius_mm": 10.0},
            "screw": {"thread_pitch_mm": 2.0},
            "gears": {"radius1_mm": 8.0, "radius2_mm": 16.0},
            "belt": {"radius1_mm": 8.0, "radius2_mm": 16.0},
        }.get(kind, {})
        joints.append(
            {
                "id": f"Joint{index}",
                "label": kind,
                "kind": kind,
                "connectors": [
                    _connector("Base"),
                    _connector(component_id),
                ],
                "parameters": parameters,
                "length_limits_mm": (
                    [0.0, 25.0]
                    if kind in {"slider", "cylindrical"}
                    else None
                ),
                "angle_limits_degrees": (
                    [-90.0, 90.0]
                    if kind in {"revolute", "cylindrical"}
                    else None
                ),
                "suppressed": False,
            }
        )
    return {
        "schema": MECHANISM_SCENARIO_SCHEMA,
        "assembly": {"id": "Model", "label": "Joint Matrix"},
        "components": components,
        "joints": joints,
        "solve": {
            "id": "Diagnostics",
            "label": "",
            "require_solved": True,
        },
        "motions": [],
        "simulation": None,
    }


def _solved_placement(x: float = 0.0) -> dict:
    return {
        "position_mm": [x, 0.0, 0.0],
        "rotation_axis": [0.0, 0.0, 1.0],
        "rotation_angle_degrees": 0.0,
        "matrix": [
            1.0,
            0.0,
            0.0,
            x,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ],
    }


def _solved_occurrence(path: str = "ModuleOccurrence") -> dict:
    return {
        "occurrence_path": path,
        "source_node_id": "n0001",
        "source_kind": "shape",
        "source_label": "Nested component",
        "native_name": "CandidateOccurrence",
        "native_type_id": "App::Link",
        "native_target_mode": "direct_exposed_occurrence",
        "live_occurrence": True,
        "local_placement": _solved_placement(2.0),
        "global_placement": _solved_placement(12.0),
    }


def _solve_report(scenario: dict) -> dict:
    clean = normalize_mechanism_scenario(scenario)
    component_ids = [item["id"] for item in clean["components"]]
    return {
        "schema": MECHANISM_SOLVE_REPORT_SCHEMA,
        "scenario_sha256": mechanism_scenario_sha256(clean),
        "status": "solved",
        "solver_code": 0,
        "solver_verdict": "solved",
        "require_solved": True,
        "component_count": len(component_ids),
        "joint_count": len(clean["joints"]),
        "grounded_components": ["Base"],
        "native_diagnostics": {
            "available": True,
            "has_conflicts": False,
        },
        "component_placements": {
            component_id: _solved_placement(float(index))
            for index, component_id in enumerate(component_ids)
        },
        "component_occurrences": {
            component_id: [] for component_id in component_ids
        },
        "joint_dependency_issues": [],
    }


def _static_check(
    scenario: dict,
    *,
    requirements: list[dict] | None = None,
    contacts: list[dict] | None = None,
) -> dict:
    return {
        "schema": MECHANISM_STATIC_CHECK_SCHEMA,
        "id": "Verification",
        "label": "Static verification",
        "scenario_sha256": mechanism_scenario_sha256(scenario),
        "requirements": requirements or [],
        "contacts": contacts or [],
    }


def _geometry_evidence(
    scenario: dict,
    declarations: list[dict],
) -> dict:
    return {
        "schema": "vibecad-mechanism-static-evidence-v1",
        "geometry_engine": {"name": "OpenCASCADE", "version": "test"},
        "component_count": len(scenario["components"]),
        "declaration_count": len(declarations),
        "complete_count": len(declarations),
        "indeterminate_count": 0,
        "declarations": declarations,
    }


def _pair_evidence(
    declaration_id: str,
    *,
    distance: float,
    volume: float = 0.0,
    first_interface: str | None = None,
    second_interface: str | None = None,
    contact_locus: bool | None = None,
) -> dict:
    return {
        "declaration_id": declaration_id,
        "first_component": "Base",
        "second_component": "Part1",
        "tolerance_mm": 0.01,
        "first_interface": first_interface,
        "second_interface": second_interface,
        "status": "complete",
        "error": "",
        "body": {
            "first_component": "Base",
            "second_component": "Part1",
            "minimum_distance_mm": distance,
            "common_volume_mm3": volume,
        },
        "interfaces": (
            {
                "first_component": "Base",
                "second_component": "Part1",
                "first_interface": first_interface,
                "second_interface": second_interface,
                "minimum_distance_mm": distance,
                "contact_locus_on_interfaces": contact_locus,
            }
            if first_interface is not None
            else None
        ),
    }


def test_shared_mechanism_contract_has_no_freecad_or_domain_worker_dependency() -> None:
    tree = ast.parse(inspect.getsource(mechanism_engine))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.partition(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])
    assert "FreeCAD" not in imported_roots
    assert not {
        "vibescript_assembly_worker",
        "vibescript_partdesign_worker",
    } & imported_roots


def test_scenario_contract_covers_every_native_joint_kind_deterministically() -> None:
    scenario = _scenario()
    clean = normalize_mechanism_scenario(scenario)
    assert clean["schema"] == MECHANISM_SCENARIO_SCHEMA
    assert [item["kind"] for item in clean["joints"]] == list(JOINT_TYPES)
    assert mechanism_scenario_sha256(scenario) == mechanism_scenario_sha256(
        copy.deepcopy(scenario)
    )


def test_scenario_contract_carries_exact_fastener_and_motion_identity() -> None:
    scenario = _scenario()
    scenario["components"][1]["source"] = {
        "kind": "standard_fastener",
        "standard": "ISO4762",
        "nominal_thread": "M6",
        "length_mm": 20.0,
        "model_thread": True,
        "left_handed": False,
        "options": {"head_type": "socket"},
    }
    scenario["motions"] = [
        {
            "id": "Drive",
            "label": "Hinge drive",
            "joint_id": "Joint2",
            "motion_type": "angular",
            "formula": "initialValue + pi/2*time",
        }
    ]
    scenario["simulation"] = {
        "id": "Simulation",
        "label": "Travel",
        "motion_ids": ["Drive"],
        "start_time_s": 0.0,
        "end_time_s": 1.0,
        "time_step_s": 0.1,
        "error_tolerance": 1.0e-6,
        "frames_per_second": 30,
    }
    clean = normalize_mechanism_scenario(scenario)
    assert clean["components"][1]["source"]["model_thread"] is True
    assert clean["motions"][0]["joint_id"] == "Joint2"
    assert clean["simulation"]["motion_ids"] == ["Drive"]


def test_scenario_contract_rejects_relational_mismatches() -> None:
    scenario = _scenario()
    scenario["joints"][0]["connectors"][1]["component_id"] = "Base"
    with pytest.raises(MechanismContractError, match="cannot connect"):
        normalize_mechanism_scenario(scenario)

    scenario = _scenario()
    scenario["motions"] = [
        {
            "id": "Drive",
            "label": "",
            "joint_id": "Missing",
            "motion_type": "angular",
            "formula": "time",
        }
    ]
    scenario["simulation"] = {
        "id": "Simulation",
        "label": "",
        "motion_ids": ["Drive"],
        "start_time_s": 0.0,
        "end_time_s": 1.0,
        "time_step_s": 0.1,
        "error_tolerance": 1.0e-6,
        "frames_per_second": 30,
    }
    with pytest.raises(MechanismContractError, match="scenario joint"):
        normalize_mechanism_scenario(scenario)

    scenario = _scenario()
    scenario["joints"][0]["parameters"] = {"distance_mm": 1.0}
    with pytest.raises(MechanismContractError, match="must contain exactly"):
        normalize_mechanism_scenario(scenario)

    scenario = _scenario()
    scenario["motions"] = [
        {
            "id": "Drive",
            "label": "",
            "joint_id": "Joint2",
            "motion_type": "linear",
            "formula": "time",
        }
    ]
    scenario["simulation"] = {
        "id": "Simulation",
        "label": "",
        "motion_ids": ["Drive"],
        "start_time_s": 0.0,
        "end_time_s": 1.0,
        "time_step_s": 0.1,
        "error_tolerance": 1.0e-6,
        "frames_per_second": 30,
    }
    with pytest.raises(MechanismContractError, match="revolute joint"):
        normalize_mechanism_scenario(scenario)


def test_scenario_contract_rejects_ambiguous_identity_values() -> None:
    for document_path in (
        "/parts/Part.FCStd",
        "C:/parts/Part.FCStd",
        r"parts\Part.FCStd",
        "parts/../Part.FCStd",
        " parts/Part.FCStd",
        "parts/Part.step",
    ):
        scenario = _scenario()
        scenario["components"][0]["source"]["document_path"] = document_path
        with pytest.raises(
            MechanismContractError,
            match=r"portable relative \.FCStd path",
        ):
            normalize_mechanism_scenario(scenario)

    scenario = _scenario()
    scenario["components"][0]["source"] = {
        "kind": "standard_fastener",
        "standard": "ISO4762",
        "nominal_thread": "M6",
        "length_mm": 20.0,
        "model_thread": True,
        "left_handed": False,
        "options": {"Head Type": "socket"},
    }
    with pytest.raises(MechanismContractError, match="at most 16 scalar"):
        normalize_mechanism_scenario(scenario)

    scenario = _scenario()
    scenario["assembly"][1] = "non-string key"
    with pytest.raises(MechanismContractError, match="field names must be strings"):
        normalize_mechanism_scenario(scenario)


def test_static_geometry_rejects_nonstable_ids_before_native_evaluation() -> None:
    with pytest.raises(MechanismGeometryError, match="stable identifiers"):
        measure_static_component_pairs(
            {"not/a/stable/id": {"shape": object(), "placement": {}}},
            [["not/a/stable/id", "not/a/stable/id"]],
        )


def test_solve_report_is_bound_to_scenario_identity_and_native_evidence() -> None:
    scenario = normalize_mechanism_scenario(_scenario())
    component_ids = [item["id"] for item in scenario["components"]]
    report = {
        "schema": MECHANISM_SOLVE_REPORT_SCHEMA,
        "scenario_sha256": mechanism_scenario_sha256(scenario),
        "status": "solved",
        "solver_code": 0,
        "solver_verdict": "solved",
        "require_solved": True,
        "component_count": len(component_ids),
        "joint_count": len(scenario["joints"]),
        "grounded_components": ["Base"],
        "native_diagnostics": {
            "available": True,
            "has_conflicts": False,
        },
        "component_placements": {
            component_id: _solved_placement(float(index))
            for index, component_id in enumerate(component_ids)
        },
        "component_occurrences": {
            component_id: (
                [_solved_occurrence()] if component_id == "Part1" else []
            )
            for component_id in component_ids
        },
        "joint_dependency_issues": [],
    }
    clean = normalize_mechanism_solve_report(scenario, report)
    assert clean["schema"] == MECHANISM_SOLVE_REPORT_SCHEMA
    assert clean["component_count"] == len(component_ids)
    assert clean["component_occurrences"]["Part1"][0][
        "occurrence_path"
    ] == "ModuleOccurrence"

    bad_hash = {**report, "scenario_sha256": "0" * 64}
    with pytest.raises(MechanismContractError, match="evaluated scenario"):
        normalize_mechanism_solve_report(scenario, bad_hash)

    bad_count = {**report, "component_count": len(component_ids) - 1}
    with pytest.raises(MechanismContractError, match="differs from the scenario"):
        normalize_mechanism_solve_report(scenario, bad_count)

    missing_component = copy.deepcopy(report)
    del missing_component["component_occurrences"]["Part2"]
    with pytest.raises(
        MechanismContractError,
        match="every scenario component exactly once",
    ):
        normalize_mechanism_solve_report(scenario, missing_component)

    duplicate_path = copy.deepcopy(report)
    duplicate_path["component_occurrences"]["Part1"].append(
        _solved_occurrence()
    )
    with pytest.raises(MechanismContractError, match="unique stable occurrence"):
        normalize_mechanism_solve_report(scenario, duplicate_path)

    misplaced_non_live = copy.deepcopy(report)
    occurrence = misplaced_non_live["component_occurrences"]["Part1"][0]
    occurrence["live_occurrence"] = False
    with pytest.raises(
        MechanismContractError,
        match="cannot report native placements",
    ):
        normalize_mechanism_solve_report(scenario, misplaced_non_live)

    def backend(detached_scenario):
        detached_scenario["assembly"]["label"] = "backend mutation"
        return report

    with pytest.raises(MechanismContractError, match="mutated"):
        evaluate_mechanism_scenario(scenario, backend)
    assert scenario["assembly"]["label"] == "Joint Matrix"


def test_solver_scope_never_equates_constraint_consistency_with_operation() -> None:
    from VibeCADMechanismEngine import solver_validation_scope

    solved = solver_validation_scope(constraints_consistent=True)
    assert solved["scope"] == "joint_constraint_consistency"
    assert solved["constraints_consistent"] is True
    assert solved["mechanical_operation_verified"] is False
    assert "collision clearance" in solved["advisory"]
    assert "motion_over_operating_range" in solved["required_evidence"]


def test_static_check_requires_explicit_pairs_tolerances_and_unique_authority() -> None:
    scenario = _scenario()
    check = _static_check(
        scenario,
        requirements=[
            {
                "id": "Clearance",
                "type": "minimum_clearance",
                "first_component": "Base",
                "second_component": "Part1",
                "minimum_mm": 0.25,
                "tolerance_mm": 0.01,
            }
        ],
    )
    clean = normalize_mechanism_static_check(scenario, check)
    assert clean["requirements"][0]["minimum_mm"] == 0.25
    assert len(mechanism_static_check_sha256(scenario, check)) == 64

    missing_tolerance = copy.deepcopy(check)
    del missing_tolerance["requirements"][0]["tolerance_mm"]
    with pytest.raises(MechanismContractError, match="missing fields"):
        normalize_mechanism_static_check(scenario, missing_tolerance)

    duplicate = copy.deepcopy(check)
    duplicate["contacts"] = [
        {
            "id": "Duplicate",
            "policy": "prohibited",
            "first_component": "Part1",
            "second_component": "Base",
            "tolerance_mm": 0.01,
        }
    ]
    with pytest.raises(MechanismContractError, match="duplicates the unordered pair"):
        normalize_mechanism_static_check(scenario, duplicate)


@pytest.mark.parametrize(
    ("policy", "distance", "volume", "contact_locus", "expected"),
    [
        ("prohibited", 1.0, 0.0, None, "pass"),
        ("prohibited", 0.0, 0.0, None, "fail"),
        ("clearance", 0.24, 0.0, None, "pass"),
        ("allowed", 0.0, 0.0, True, "pass"),
        ("allowed", 0.0, 0.0, False, "fail"),
        ("allowed", 0.0, 0.1, True, "fail"),
        ("required", 0.0, 0.0, True, "pass"),
        ("required", 1.0, 0.0, True, "fail"),
    ],
)
def test_static_contact_policies_produce_honest_verdicts(
    policy: str,
    distance: float,
    volume: float,
    contact_locus: bool | None,
    expected: str,
) -> None:
    scenario = normalize_mechanism_scenario(_scenario())
    contact = {
        "id": "Contact",
        "policy": policy,
        "first_component": "Base",
        "second_component": "Part1",
        "tolerance_mm": 0.01,
    }
    if policy == "clearance":
        contact["minimum_clearance_mm"] = 0.25
    if policy in {"allowed", "required"}:
        contact["first_interface"] = "MatingFace"
        contact["second_interface"] = "SeatFace"
    check = _static_check(scenario, contacts=[contact])
    pair = _pair_evidence(
        "Contact",
        distance=distance,
        volume=volume,
        first_interface=contact.get("first_interface"),
        second_interface=contact.get("second_interface"),
        contact_locus=contact_locus,
    )
    report = evaluate_static_mechanism_check(
        scenario,
        _solve_report(scenario),
        check,
        _geometry_evidence(scenario, [pair]),
    )
    assert report["schema"] == MECHANISM_VERIFICATION_REPORT_SCHEMA
    assert report["verdict"] == expected
    assert report["scope"]["motion_certified"] is False


def test_static_verification_report_rejects_tampered_verdict_and_evidence() -> None:
    scenario = normalize_mechanism_scenario(_scenario())
    check = _static_check(
        scenario,
        requirements=[
            {
                "id": "Collision",
                "type": "collision_free",
                "first_component": "Base",
                "second_component": "Part1",
                "tolerance_mm": 0.01,
            }
        ],
    )
    solve = _solve_report(scenario)
    report = evaluate_static_mechanism_check(
        scenario,
        solve,
        check,
        _geometry_evidence(
            scenario,
            [_pair_evidence("Collision", distance=2.0)],
        ),
    )
    assert normalize_mechanism_verification_report(
        scenario,
        solve,
        check,
        report,
    ) == report

    tampered = copy.deepcopy(report)
    tampered["verdict"] = "fail"
    with pytest.raises(MechanismContractError, match="deterministic report"):
        normalize_mechanism_verification_report(
            scenario,
            solve,
            check,
            tampered,
        )

    tampered = copy.deepcopy(report)
    tampered["geometry_evidence"]["declarations"][0]["body"][
        "minimum_distance_mm"
    ] = 0.0
    with pytest.raises(MechanismContractError, match="deterministic report"):
        normalize_mechanism_verification_report(
            scenario,
            solve,
            check,
            tampered,
        )


def test_static_verification_ignores_only_the_explicitly_excluded_pair() -> None:
    scenario = normalize_mechanism_scenario(_scenario())
    check = _static_check(
        scenario,
        requirements=[
            {
                "id": "Collision",
                "type": "collision_free",
                "first_component": "Base",
                "second_component": "Part1",
                "tolerance_mm": 0.01,
            }
        ],
        contacts=[
            {
                "id": "PresentationEnvelope",
                "policy": "ignored",
                "first_component": "Base",
                "second_component": "Part2",
                "reason": "Nonphysical presentation geometry",
            }
        ],
    )
    report = evaluate_static_mechanism_check(
        scenario,
        _solve_report(scenario),
        check,
        _geometry_evidence(
            scenario,
            [_pair_evidence("Collision", distance=2.0)],
        ),
    )
    assert report["verdict"] == "pass"
    assert report["summary"] == {
        "declaration_count": 2,
        "pass_count": 1,
        "fail_count": 0,
        "indeterminate_count": 0,
        "ignored_count": 1,
    }
    ignored = report["results"][1]
    assert ignored["verdict"] == "ignored"
    assert ignored["evidence"] is None
    assert ignored["message"] == "Nonphysical presentation geometry"


@pytest.mark.parametrize(
    ("distance", "expected"),
    [
        (0.239, "fail"),
        (0.24, "pass"),
        (0.25, "pass"),
    ],
)
def test_minimum_clearance_uses_only_the_declared_tolerance(
    distance: float,
    expected: str,
) -> None:
    scenario = normalize_mechanism_scenario(_scenario())
    check = _static_check(
        scenario,
        requirements=[
            {
                "id": "Clearance",
                "type": "minimum_clearance",
                "first_component": "Base",
                "second_component": "Part1",
                "minimum_mm": 0.25,
                "tolerance_mm": 0.01,
            }
        ],
    )
    report = evaluate_static_mechanism_check(
        scenario,
        _solve_report(scenario),
        check,
        _geometry_evidence(
            scenario,
            [_pair_evidence("Clearance", distance=distance)],
        ),
    )
    assert report["verdict"] == expected


def test_static_verification_reports_indeterminate_instead_of_false_pass() -> None:
    scenario = normalize_mechanism_scenario(_scenario())
    check = _static_check(
        scenario,
        requirements=[
            {
                "id": "Collision",
                "type": "collision_free",
                "first_component": "Base",
                "second_component": "Part1",
                "tolerance_mm": 0.01,
            }
        ],
    )
    failed_solve = _solve_report(scenario)
    failed_solve["status"] = "failed"
    failed_solve["solver_code"] = -3
    failed_solve["solver_verdict"] = "conflicting_constraints"
    report = evaluate_static_mechanism_check(
        scenario,
        failed_solve,
        check,
        _geometry_evidence(
            scenario,
            [_pair_evidence("Collision", distance=2.0)],
        ),
    )
    assert report["verdict"] == "indeterminate"
    assert report["first_failure"]["reason_code"] == "assembly_not_solved"

    evidence = _geometry_evidence(
        scenario,
        [_pair_evidence("Collision", distance=2.0)],
    )
    declaration = evidence["declarations"][0]
    declaration["status"] = "indeterminate"
    declaration["error"] = "OCCT exact distance failed"
    declaration["body"] = None
    declaration["interfaces"] = None
    evidence["complete_count"] = 0
    evidence["indeterminate_count"] = 1
    report = evaluate_static_mechanism_check(
        scenario,
        _solve_report(scenario),
        check,
        evidence,
    )
    assert report["verdict"] == "indeterminate"
    assert report["first_failure"]["reason_code"] == (
        "geometry_evaluation_failed"
    )


def test_required_interface_contact_is_indeterminate_without_exact_confinement() -> None:
    scenario = normalize_mechanism_scenario(_scenario())
    check = _static_check(
        scenario,
        contacts=[
            {
                "id": "RequiredContact",
                "policy": "required",
                "first_component": "Base",
                "second_component": "Part1",
                "first_interface": "MatingFace",
                "second_interface": "SeatFace",
                "tolerance_mm": 0.01,
            }
        ],
    )
    report = evaluate_static_mechanism_check(
        scenario,
        _solve_report(scenario),
        check,
        _geometry_evidence(
            scenario,
            [
                _pair_evidence(
                    "RequiredContact",
                    distance=0.0,
                    first_interface="MatingFace",
                    second_interface="SeatFace",
                    contact_locus=None,
                )
            ],
        ),
    )
    assert report["verdict"] == "indeterminate"
    assert report["first_failure"]["reason_code"] == (
        "interface_contact_not_proven"
    )
