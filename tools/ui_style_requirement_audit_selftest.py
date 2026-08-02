#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Self-test the UI/style requirement audit mapping."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import ui_style_requirement_audit as audit


SCHEMA = "freecad-ui-style-requirement-audit-selftest-v1"
EXPECTED_REQUIREMENTS = {
    "core_tests_stay_green",
    "registered_freecad_tests_actionable",
    "visual_baselines_cover_real_workflows",
    "layout_assertions_exist",
    "theme_dpi_font_matrix",
    "image_diff_workflow",
    "gui_exercise_fixture_based",
    "crash_gate",
    "manual_smoke_pass",
}
EXPECTED_SPEC = {
    "path": "synthetic-requirements.json",
    "requirements": [
        {"id": requirement_id, "title": title}
        for requirement_id, title in {
            "core_tests_stay_green": "Core tests stay green",
            "registered_freecad_tests_actionable": "Registered FreeCAD tests are made actionable",
            "visual_baselines_cover_real_workflows": "Visual baselines cover real workflows",
            "layout_assertions_exist": "Layout assertions exist",
            "theme_dpi_font_matrix": "Theme/DPI/font matrix",
            "image_diff_workflow": "Image diff workflow",
            "gui_exercise_fixture_based": "GUI exercise is fixture-based",
            "crash_gate": "Crash gate",
            "manual_smoke_pass": "Manual smoke pass",
        }.items()
    ],
}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check(status: str, message: str = "synthetic") -> dict[str, Any]:
    return {"status": status, "message": message, "evidence": {}}


def synthetic_summary() -> dict[str, Any]:
    return {
        "ctest": {"log": "ctest.log", "run": 1, "inventory_total": 1, "failed": 0},
        "ctest_inventory_regression": {"approved_not_run_count": 0, "newly_runnable_count": 0},
        "freecad_startup_smoke": {"report": "startup.json", "freecad_version": "selftest"},
        "freecad_registered_tests": {"log": "freecad-t0.log", "completed": True, "traceback_count": 0},
        "freecad_registered_split": {"summary": "split.json", "discovered_suite_count": 1, "selected_suite_count": 1},
        "freecad_registered_issue_classification": {"report": "classification.json", "unclassified_issue_count": 0, "hard_blocker_count": 0},
        "gui_visual_venv": {"scene_count": 1, "summary": "workbenches.json"},
        "gui_visual_fixtures": {"scene_count": 1, "summary": "fixtures.json"},
        "gui_visual_dialogs": {"scene_count": 1, "summary": "dialogs.json"},
        "gui_visual_dialogs_native": {"scene_count": 1, "summary": "dialogs-native.json"},
        "gui_visual_tasks": {"scene_count": 1, "summary": "tasks.json"},
        "layout_assertion_smoke": {"report": "layout.json", "observed": {"zero_size": True}},
        "gui_visual_matrix": {"summary": "matrix.json", "scene_count": 1, "variant_count": 1},
        "gui_visual_regression": {
            "manifest": "workbench-approved.json",
            "manifest_present": True,
            "approved_scene_count": 1,
            "format": 2,
            "approval": {"reviewer": "selftest"},
            "missing_context_fingerprint_count": 0,
            "missing_context_identity_count": 0,
            "absolute_screenshot_count": 0,
            "check_report": "workbench-check.json",
            "check_result": "ok",
            "failure_count": 0,
            "check_diff_dir": "workbench-diffs",
            "check_review_index": {
                "json": "workbench-diffs/review-index.json",
                "html": "workbench-diffs/review-index.html",
            },
            "check_approval_command": "approve workbench",
        },
        "gui_visual_fixtures_regression": {
            "manifest": "fixture-approved.json",
            "manifest_present": True,
            "approved_scene_count": 1,
            "format": 2,
            "approval": {"reviewer": "selftest"},
            "missing_context_fingerprint_count": 0,
            "missing_context_identity_count": 0,
            "absolute_screenshot_count": 0,
            "check_report": "fixture-check.json",
            "check_result": "ok",
            "failure_count": 0,
            "check_diff_dir": "fixture-diffs",
            "check_review_index": {
                "json": "fixture-diffs/review-index.json",
                "html": "fixture-diffs/review-index.html",
            },
            "check_approval_command": "approve fixture",
        },
        "gui_visual_dialogs_regression": {
            "manifest": "dialog-approved.json",
            "manifest_present": True,
            "approved_scene_count": 1,
            "format": 2,
            "approval": {"reviewer": "selftest"},
            "missing_context_fingerprint_count": 0,
            "missing_context_identity_count": 0,
            "absolute_screenshot_count": 0,
            "check_report": "dialog-check.json",
            "check_result": "ok",
            "failure_count": 0,
            "check_diff_dir": "dialog-diffs",
            "check_review_index": {
                "json": "dialog-diffs/review-index.json",
                "html": "dialog-diffs/review-index.html",
            },
            "check_approval_command": "approve dialog",
        },
        "gui_visual_tasks_regression": {
            "manifest": "task-approved.json",
            "manifest_present": True,
            "approved_scene_count": 1,
            "format": 2,
            "approval": {"reviewer": "selftest"},
            "missing_context_fingerprint_count": 0,
            "missing_context_identity_count": 0,
            "absolute_screenshot_count": 0,
            "check_report": "task-check.json",
            "check_result": "ok",
            "failure_count": 0,
            "check_diff_dir": "task-diffs",
            "check_review_index": {
                "json": "task-diffs/review-index.json",
                "html": "task-diffs/review-index.html",
            },
            "check_approval_command": "approve task",
        },
        "gui_visual_matrix_regression": {
            "manifest": "matrix-approved.json",
            "manifest_present": True,
            "approved_scene_count": 1,
            "format": 2,
            "approval": {"reviewer": "selftest"},
            "missing_context_fingerprint_count": 0,
            "missing_context_identity_count": 0,
            "absolute_screenshot_count": 0,
            "check_report": "matrix-check.json",
            "check_result": "ok",
            "failure_count": 0,
            "check_diff_dir": "matrix-diffs",
            "check_review_index": {
                "json": "matrix-diffs/review-index.json",
                "html": "matrix-diffs/review-index.html",
            },
            "check_approval_command": "approve matrix",
        },
        "gui_visual_regression_selftest": {
            "report": "visual-selftest.json",
            "result": "ok",
            "scenario_count": 1,
            "scenario_names": ["identical_passes"],
            "failed_scenarios": [],
        },
        "gui_exercise_venv": {"summary": "exercise.json"},
        "gui_workflows_venv": {"summary": "workflows.json"},
        "run_ui_test_baseline_selftest": {
            "report": "runner-selftest.json",
            "result": "ok",
            "scenario_count": 1,
            "failed_scenarios": [],
        },
        "artifact_provenance_selftest": {
            "report": "artifact-provenance-selftest.json",
            "result": "ok",
            "scenario_count": 1,
            "failed_scenarios": [],
        },
        "json_artifact_integrity": {
            "report": "json-artifact-integrity.json",
            "result": "ok",
            "checked_count": 40,
            "failure_count": 0,
            "failures": [],
        },
        "json_artifact_integrity_selftest": {
            "report": "json-artifact-integrity-selftest.json",
            "result": "ok",
            "scenario_count": 3,
            "failed_scenarios": [],
        },
    }


def all_pass_gate() -> dict[str, Any]:
    gates = {
        "core_tests": check(audit.PASS),
        "registered_tests": check(audit.PASS),
        "visual_baselines": check(audit.PASS),
        "layout_assertions": check(audit.PASS),
        "theme_dpi_font_matrix": check(audit.PASS),
        "image_diff_workflow": check(audit.PASS),
        "gui_exercise": check(
            audit.PASS,
            "synthetic",
        ),
        "crash_gate": check(audit.PASS),
        "dependency_coverage": check(audit.PARTIAL),
        "manual_smoke": check(audit.PASS),
    }
    gates["gui_exercise"]["evidence"] = {
        "passed_workflows": ["switch_workbench"],
        "required_workflows": ["switch_workbench"],
        "task_scene_count": 1,
    }
    return {
        "overall_status": audit.PASS,
        "ready_for_sweeping_style_change": True,
        "status_counts": {"pass": 9, "partial": 1},
        "gates": gates,
    }


def run_case(
    name: str,
    gate_report: dict[str, Any],
    expected_status: str,
    expected_requirement: str | None = None,
    requirement_spec: dict[str, Any] | None = None,
    coverage_selftest: dict[str, Any] | None = None,
    gate_selftest: dict[str, Any] | None = None,
    requirement_audit_selftest: dict[str, Any] | None = None,
    run_status: dict[str, Any] | None = None,
    run_status_selftest: dict[str, Any] | None = None,
    expected_supporting_gate: str | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = audit.build_report(
        summary or synthetic_summary(),
        gate_report,
        coverage_selftest
        if coverage_selftest is not None
        else {"result": "ok", "scenario_count": 1, "failed_scenarios": []},
        gate_selftest
        if gate_selftest is not None
        else {"result": "ok", "scenario_count": 1, "failed_scenarios": []},
        requirement_audit_selftest
        if requirement_audit_selftest is not None
        else {
            "result": "ok",
            "scenario_count": 1,
            "failed_scenarios": [],
            "expected_requirement_count": len(EXPECTED_REQUIREMENTS),
        },
        run_status,
        run_status_selftest
        if run_status_selftest is not None
        else ({"result": "ok", "scenario_count": 1, "failed_scenarios": []} if run_status is not None else None),
        requirement_spec or EXPECTED_SPEC,
    )
    requirement_ids = {item["id"] for item in report["requirements"]}
    nonpass = {item["id"]: item["status"] for item in report["requirements"] if item["status"] != audit.PASS}
    ok = (
        report["overall_status"] == expected_status
        and requirement_ids == EXPECTED_REQUIREMENTS
        and len(report["requirements"]) == len(EXPECTED_REQUIREMENTS)
    )
    if expected_requirement:
        ok = ok and expected_requirement in nonpass
    if expected_supporting_gate:
        ok = (
            ok
            and report["supporting_gates"][expected_supporting_gate]["status"] != audit.PASS
        )
    return {
        "ok": ok,
        "overall_status": report["overall_status"],
        "ready_for_sweeping_style_change": report["ready_for_sweeping_style_change"],
        "requirement_count": len(report["requirements"]),
        "requirement_ids": sorted(requirement_ids),
        "nonpass_requirements": nonpass,
        "spec_coverage": report.get("requirement_spec_coverage"),
        "supporting_gates": {
            name: item["status"] for name, item in report["supporting_gates"].items()
        },
    }


def image_diff_evidence_case() -> dict[str, Any]:
    gate_report = all_pass_gate()
    gate_report["overall_status"] = audit.FAIL
    gate_report["ready_for_sweeping_style_change"] = False
    gate_report["gates"]["image_diff_workflow"] = {
        "status": audit.FAIL,
        "message": "Visual regression checks are failing or missing",
        "evidence": {
            "failures": [
                "gui_visual_regression",
                "gui_visual_regression:missing_approval_metadata:reviewer",
                "gui_visual_regression:missing_scene_context_identities",
            ]
        },
    }
    summary = synthetic_summary()
    summary["gui_visual_regression"].update(
        {
            "check_result": "failed",
            "failure_count": 3,
            "approval": None,
            "missing_context_identity_count": 1,
        }
    )
    report = audit.build_report(
        summary,
        gate_report,
        {"result": "ok", "scenario_count": 1, "failed_scenarios": []},
        {"result": "ok", "scenario_count": 1, "failed_scenarios": []},
        {
            "result": "ok",
            "scenario_count": 1,
            "failed_scenarios": [],
            "expected_requirement_count": len(EXPECTED_REQUIREMENTS),
        },
        requirement_spec=EXPECTED_SPEC,
    )
    image_requirement = next(
        item for item in report["requirements"] if item["id"] == "image_diff_workflow"
    )
    evidence = image_requirement["evidence"]
    workbench = evidence.get("manifests", {}).get("workbench", {})
    ok = (
        image_requirement["status"] == audit.FAIL
        and "gui_visual_regression:missing_approval_metadata:reviewer"
        in evidence.get("gate_failures", [])
        and workbench.get("failure_count") == 3
        and workbench.get("approval") is None
        and workbench.get("missing_context_identity_count") == 1
        and evidence.get("selftest", {}).get("scenario_count") == 1
    )
    return {
        "ok": ok,
        "overall_status": report["overall_status"],
        "image_diff_status": image_requirement["status"],
        "gate_failures": evidence.get("gate_failures", []),
        "workbench_manifest_evidence": workbench,
        "selftest": evidence.get("selftest", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/freecad-test-results/ui-style-requirement-audit-selftest.json"))
    args = parser.parse_args()

    all_pass = all_pass_gate()
    missing_manual = copy.deepcopy(all_pass)
    missing_manual["gates"]["manual_smoke"] = check(audit.MISSING, "Manual smoke artifact is missing")

    failing_crash = copy.deepcopy(all_pass)
    failing_crash["gates"]["crash_gate"] = check(audit.FAIL, "Crash/process/traceback gate has hard failures")

    runner_selftest_failure_summary = synthetic_summary()
    runner_selftest_failure_summary["run_ui_test_baseline_selftest"]["result"] = "failed"
    runner_selftest_failure_summary["run_ui_test_baseline_selftest"]["failed_scenarios"] = [
        "run_step_keeps_redirected_json_clean"
    ]
    artifact_provenance_selftest_failure_summary = synthetic_summary()
    artifact_provenance_selftest_failure_summary["artifact_provenance_selftest"][
        "result"
    ] = "failed"
    artifact_provenance_selftest_failure_summary["artifact_provenance_selftest"][
        "failed_scenarios"
    ] = ["modified_after_run_marker_fails"]

    json_integrity_failure_summary = synthetic_summary()
    json_integrity_failure_summary["json_artifact_integrity"]["result"] = "failed"
    json_integrity_failure_summary["json_artifact_integrity"]["failure_count"] = 1
    json_integrity_failure_summary["json_artifact_integrity"]["failures"] = [
        {
            "path": "gui-visual-regression-check.json",
            "error": "Expecting value: line 1 column 1 (char 0)",
        }
    ]
    cases = {
        "all_requirements_pass_when_all_mapped_gates_pass": run_case(
            "all_requirements_pass_when_all_mapped_gates_pass",
            all_pass,
            audit.PASS,
        ),
        "missing_manual_smoke_fails_manual_requirement": run_case(
            "missing_manual_smoke_fails_manual_requirement",
            missing_manual,
            audit.FAIL,
            "manual_smoke_pass",
        ),
        "failing_crash_gate_fails_crash_requirement": run_case(
            "failing_crash_gate_fails_crash_requirement",
            failing_crash,
            audit.FAIL,
            "crash_gate",
        ),
        "image_diff_audit_evidence_includes_manifest_quality": image_diff_evidence_case(),
        "missing_gate_selftest_blocks_audit_readiness": run_case(
            "missing_gate_selftest_blocks_audit_readiness",
            all_pass,
            audit.FAIL,
            gate_selftest={},
            expected_supporting_gate="gate_evaluator_selftest",
        ),
        "failing_gate_selftest_blocks_audit_readiness": run_case(
            "failing_gate_selftest_blocks_audit_readiness",
            all_pass,
            audit.FAIL,
            gate_selftest={"result": "failed", "scenario_count": 1, "failed_scenarios": ["synthetic"]},
            expected_supporting_gate="gate_evaluator_selftest",
        ),
        "missing_requirement_audit_selftest_blocks_audit_readiness": run_case(
            "missing_requirement_audit_selftest_blocks_audit_readiness",
            all_pass,
            audit.FAIL,
            requirement_audit_selftest={},
            expected_supporting_gate="requirement_audit_selftest",
        ),
        "failing_requirement_audit_selftest_blocks_audit_readiness": run_case(
            "failing_requirement_audit_selftest_blocks_audit_readiness",
            all_pass,
            audit.FAIL,
            requirement_audit_selftest={
                "result": "failed",
                "scenario_count": 1,
                "failed_scenarios": ["synthetic"],
                "expected_requirement_count": len(EXPECTED_REQUIREMENTS),
            },
            expected_supporting_gate="requirement_audit_selftest",
        ),
        "failing_full_runner_status_blocks_audit_readiness": run_case(
            "failing_full_runner_status_blocks_audit_readiness",
            all_pass,
            audit.FAIL,
            run_status={
                "result": "failed",
                "failure_count": 1,
                "discovered_step_count": 3,
                "failures": [{"step": "synthetic-step", "status": "missing"}],
            },
            expected_supporting_gate="full_runner_status",
        ),
        "failing_run_status_selftest_blocks_audit_readiness": run_case(
            "failing_run_status_selftest_blocks_audit_readiness",
            all_pass,
            audit.FAIL,
            run_status={"result": "ok", "failure_count": 0, "discovered_step_count": 3, "failures": []},
            run_status_selftest={
                "result": "failed",
                "scenario_count": 1,
                "failed_scenarios": ["synthetic"],
            },
            expected_supporting_gate="run_status_selftest",
        ),
        "failing_runner_selftest_blocks_audit_readiness": run_case(
            "failing_runner_selftest_blocks_audit_readiness",
            all_pass,
            audit.FAIL,
            expected_supporting_gate="runner_selftest",
            summary=runner_selftest_failure_summary,
        ),
        "failing_artifact_provenance_selftest_blocks_audit_readiness": run_case(
            "failing_artifact_provenance_selftest_blocks_audit_readiness",
            all_pass,
            audit.FAIL,
            expected_supporting_gate="artifact_provenance_selftest",
            summary=artifact_provenance_selftest_failure_summary,
        ),
        "failing_json_integrity_blocks_audit_readiness": run_case(
            "failing_json_integrity_blocks_audit_readiness",
            all_pass,
            audit.FAIL,
            expected_supporting_gate="json_artifact_integrity",
            summary=json_integrity_failure_summary,
        ),
    }
    expanded_spec = copy.deepcopy(EXPECTED_SPEC)
    expanded_spec["requirements"].append(
        {"id": "selftest_new_requirement", "title": "Self-test new requirement"}
    )
    expanded_report = audit.build_report(
        synthetic_summary(),
        all_pass,
        {"result": "ok", "scenario_count": 1, "failed_scenarios": []},
        {"result": "ok", "scenario_count": 1, "failed_scenarios": []},
        {
            "result": "ok",
            "scenario_count": 1,
            "failed_scenarios": [],
            "expected_requirement_count": len(EXPECTED_REQUIREMENTS),
        },
        None,
        None,
        expanded_spec,
    )
    cases["new_requirement_in_spec_fails_until_mapped"] = {
        "ok": expanded_report["overall_status"] == audit.FAIL
        and expanded_report["requirement_spec_coverage"]["status"] == audit.FAIL
        and "selftest_new_requirement" in expanded_report["requirement_spec_coverage"]["missing"],
        "overall_status": expanded_report["overall_status"],
        "spec_coverage": expanded_report["requirement_spec_coverage"],
    }
    retitled_spec = copy.deepcopy(EXPECTED_SPEC)
    retitled_spec["requirements"][0]["title"] = "Retitled core requirement"
    retitled_report = audit.build_report(
        synthetic_summary(),
        all_pass,
        {"result": "ok", "scenario_count": 1, "failed_scenarios": []},
        {"result": "ok", "scenario_count": 1, "failed_scenarios": []},
        {
            "result": "ok",
            "scenario_count": 1,
            "failed_scenarios": [],
            "expected_requirement_count": len(EXPECTED_REQUIREMENTS),
        },
        None,
        None,
        retitled_spec,
    )
    cases["requirement_title_mismatch_fails_spec_coverage"] = {
        "ok": retitled_report["overall_status"] == audit.FAIL
        and retitled_report["requirement_spec_coverage"]["status"] == audit.FAIL
        and bool(retitled_report["requirement_spec_coverage"]["title_mismatches"]),
        "overall_status": retitled_report["overall_status"],
        "spec_coverage": retitled_report["requirement_spec_coverage"],
    }
    failed = [name for name, result in cases.items() if not result["ok"]]
    report = {
        "schema": SCHEMA,
        "result": "ok" if not failed else "failed",
        "scenario_count": len(cases),
        "failed_scenarios": failed,
        "expected_requirement_count": len(EXPECTED_REQUIREMENTS),
        "expected_requirements": sorted(EXPECTED_REQUIREMENTS),
        "scenarios": cases,
    }
    write_json(args.output, report)
    return 0 if report["result"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
