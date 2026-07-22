#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Map the UI/style readiness checklist to collected gate evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA = "freecad-ui-style-requirement-audit-v1"
PASS = "pass"
PARTIAL = "partial"
FAIL = "fail"
MISSING = "missing"
DEFAULT_REQUIREMENT_SPEC = Path(__file__).resolve().with_name("ui_style_requirements.default.json")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return read_json(path)


def gate(gates: dict[str, Any], name: str) -> dict[str, Any]:
    return gates.get(
        name,
        {
            "status": MISSING,
            "message": f"Gate '{name}' is missing",
            "evidence": {},
        },
    )


def requirement(
    requirement_id: str,
    title: str,
    status: str,
    evidence: dict[str, Any],
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": requirement_id,
        "title": title,
        "status": status,
        "evidence": evidence,
        "blockers": blockers or [],
    }


def load_requirement_spec(path: Path | None) -> dict[str, Any]:
    path = path or DEFAULT_REQUIREMENT_SPEC
    data = read_json(path)
    requirements = data.get("requirements", [])
    return {
        "path": str(path),
        "requirements": [
            {
                "id": str(item.get("id", "")).strip(),
                "title": str(item.get("title", "")).strip(),
            }
            for item in requirements
            if isinstance(item, dict)
        ],
    }


def spec_coverage(requirements: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    actual = {item["id"]: item.get("title") for item in requirements}
    expected = {item["id"]: item.get("title") for item in spec.get("requirements", [])}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    title_mismatches = [
        {
            "id": requirement_id,
            "expected": expected[requirement_id],
            "actual": actual[requirement_id],
        }
        for requirement_id in sorted(set(expected) & set(actual))
        if expected[requirement_id] != actual[requirement_id]
    ]
    duplicate_ids = sorted(
        {
            item["id"]
            for item in requirements
            if [row["id"] for row in requirements].count(item["id"]) > 1
        }
    )
    errors = []
    if missing:
        errors.append("missing_spec_requirements")
    if extra:
        errors.append("extra_report_requirements")
    if title_mismatches:
        errors.append("title_mismatches")
    if duplicate_ids:
        errors.append("duplicate_requirement_ids")
    return {
        "status": PASS if not errors else FAIL,
        "spec": spec.get("path"),
        "expected_count": len(expected),
        "actual_count": len(requirements),
        "missing": missing,
        "extra": extra,
        "title_mismatches": title_mismatches,
        "duplicate_ids": duplicate_ids,
        "errors": errors,
    }


def gate_blockers(*items: dict[str, Any]) -> list[str]:
    blockers = []
    for item in items:
        if item.get("status") != PASS:
            blockers.append(str(item.get("message") or "Gate is not passing"))
    return blockers


def selftest_status(report: dict[str, Any] | None) -> str:
    if not report:
        return MISSING
    return PASS if report.get("result") == "ok" else FAIL


def issue_suites(item: dict[str, Any]) -> list[str]:
    evidence = item.get("evidence") or {}
    suites = evidence.get("issue_suites")
    if isinstance(suites, list):
        return suites
    failures = evidence.get("failures")
    if isinstance(failures, list):
        return sorted(
            {
                str(failure.get("suite"))
                for failure in failures
                if isinstance(failure, dict) and failure.get("suite")
            }
        )
    return []


VISUAL_REGRESSION_KEYS = {
    "workbench": "gui_visual_regression",
    "fixture": "gui_visual_fixtures_regression",
    "dialog": "gui_visual_dialogs_regression",
    "task": "gui_visual_tasks_regression",
    "matrix": "gui_visual_matrix_regression",
}


def visual_regression_audit_evidence(
    summary: dict[str, Any],
    regression_gate: dict[str, Any],
) -> dict[str, Any]:
    gate_evidence = regression_gate.get("evidence") or {}
    manifests = {}
    for label, key in VISUAL_REGRESSION_KEYS.items():
        item = summary.get(key, {})
        manifests[label] = {
            "manifest": item.get("manifest"),
            "manifest_present": item.get("manifest_present"),
            "approved_scene_count": item.get("approved_scene_count"),
            "format": item.get("format"),
            "approval": item.get("approval"),
            "missing_context_fingerprint_count": item.get(
                "missing_context_fingerprint_count"
            ),
            "missing_context_identity_count": item.get("missing_context_identity_count"),
            "absolute_screenshot_count": item.get("absolute_screenshot_count"),
            "check_report": item.get("check_report"),
            "check_result": item.get("check_result"),
            "failure_count": item.get("failure_count"),
            "check_diff_dir": item.get("check_diff_dir"),
            "check_review_index": item.get("check_review_index"),
            "check_approval_command": item.get("check_approval_command"),
        }
    return {
        "gate_failures": gate_evidence.get("failures", []),
        "manifests": manifests,
        "selftest": {
            "report": summary.get("gui_visual_regression_selftest", {}).get("report"),
            "result": summary.get("gui_visual_regression_selftest", {}).get("result"),
            "scenario_count": summary.get("gui_visual_regression_selftest", {}).get(
                "scenario_count"
            ),
            "scenario_names": summary.get("gui_visual_regression_selftest", {}).get(
                "scenario_names", []
            ),
            "failed_scenarios": summary.get("gui_visual_regression_selftest", {}).get(
                "failed_scenarios", []
            ),
        },
    }


def build_report(
    summary: dict[str, Any],
    gate_report: dict[str, Any],
    coverage_selftest: dict[str, Any] | None = None,
    gate_selftest: dict[str, Any] | None = None,
    requirement_audit_selftest: dict[str, Any] | None = None,
    run_status: dict[str, Any] | None = None,
    run_status_selftest: dict[str, Any] | None = None,
    requirement_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gates = gate_report.get("gates") or {}
    core = gate(gates, "core_tests")
    registered = gate(gates, "registered_tests")
    visual = gate(gates, "visual_baselines")
    layout = gate(gates, "layout_assertions")
    matrix = gate(gates, "theme_dpi_font_matrix")
    regression = gate(gates, "image_diff_workflow")
    exercise = gate(gates, "gui_exercise")
    crash = gate(gates, "crash_gate")
    manual = gate(gates, "manual_smoke")
    dependency = gate(gates, "dependency_coverage")

    requirements = [
        requirement(
            "core_tests_stay_green",
            "Core tests stay green",
            core.get("status", MISSING),
            {
                "gate": "core_tests",
                "message": core.get("message"),
                "ctest_log": summary.get("ctest", {}).get("log"),
                "ctest_run": summary.get("ctest", {}).get("run"),
                "ctest_inventory_total": summary.get("ctest", {}).get("inventory_total"),
                "ctest_failed": summary.get("ctest", {}).get("failed"),
                "approved_not_run_count": summary.get("ctest_inventory_regression", {}).get(
                    "approved_not_run_count"
                ),
                "newly_runnable_count": summary.get("ctest_inventory_regression", {}).get(
                    "newly_runnable_count"
                ),
                "startup_report": summary.get("freecad_startup_smoke", {}).get("report"),
                "freecad_version": summary.get("freecad_startup_smoke", {}).get("freecad_version"),
            },
            gate_blockers(core),
        ),
        requirement(
            "registered_freecad_tests_actionable",
            "Registered FreeCAD tests are made actionable",
            registered.get("status", MISSING),
            {
                "gate": "registered_tests",
                "message": registered.get("message"),
                "freecad_t0_log": summary.get("freecad_registered_tests", {}).get("log"),
                "freecad_t0_completed": summary.get("freecad_registered_tests", {}).get("completed"),
                "freecad_t0_traceback_count": summary.get("freecad_registered_tests", {}).get(
                    "traceback_count"
                ),
                "split_summary": summary.get("freecad_registered_split", {}).get("summary"),
                "split_discovered_suite_count": summary.get("freecad_registered_split", {}).get(
                    "discovered_suite_count"
                ),
                "split_selected_suite_count": summary.get("freecad_registered_split", {}).get(
                    "selected_suite_count"
                ),
                "issue_suites": issue_suites(registered),
                "classification_report": summary.get(
                    "freecad_registered_issue_classification", {}
                ).get("report"),
                "unclassified_issue_count": summary.get(
                    "freecad_registered_issue_classification", {}
                ).get("unclassified_issue_count"),
                "hard_blocker_count": summary.get(
                    "freecad_registered_issue_classification", {}
                ).get("hard_blocker_count"),
            },
            gate_blockers(registered),
        ),
        requirement(
            "visual_baselines_cover_real_workflows",
            "Visual baselines cover real workflows",
            visual.get("status", MISSING),
            {
                "gate": "visual_baselines",
                "message": visual.get("message"),
                "workbench_scene_count": summary.get("gui_visual_venv", {}).get("scene_count"),
                "fixture_scene_count": summary.get("gui_visual_fixtures", {}).get("scene_count"),
                "dialog_scene_count": summary.get("gui_visual_dialogs", {}).get("scene_count"),
                "native_dialog_scene_count": summary.get("gui_visual_dialogs_native", {}).get("scene_count"),
                "task_scene_count": summary.get("gui_visual_tasks", {}).get("scene_count"),
                "workbench_summary": summary.get("gui_visual_venv", {}).get("summary"),
                "fixture_summary": summary.get("gui_visual_fixtures", {}).get("summary"),
                "dialog_summary": summary.get("gui_visual_dialogs", {}).get("summary"),
                "native_dialog_summary": summary.get("gui_visual_dialogs_native", {}).get("summary"),
                "task_summary": summary.get("gui_visual_tasks", {}).get("summary"),
            },
            gate_blockers(visual),
        ),
        requirement(
            "layout_assertions_exist",
            "Layout assertions exist",
            layout.get("status", MISSING),
            {
                "gate": "layout_assertions",
                "message": layout.get("message"),
                "layout_smoke_report": summary.get("layout_assertion_smoke", {}).get("report"),
                "observed_assertion_kinds": sorted(
                    name
                    for name, ok in (summary.get("layout_assertion_smoke", {}).get("observed") or {}).items()
                    if ok
                ),
            },
            gate_blockers(layout),
        ),
        requirement(
            "theme_dpi_font_matrix",
            "Theme/DPI/font matrix",
            matrix.get("status", MISSING),
            {
                "gate": "theme_dpi_font_matrix",
                "message": matrix.get("message"),
                "matrix_summary": summary.get("gui_visual_matrix", {}).get("summary"),
                "matrix_scene_count": summary.get("gui_visual_matrix", {}).get("scene_count"),
                "matrix_variant_count": summary.get("gui_visual_matrix", {}).get("variant_count"),
                "variants": (matrix.get("evidence") or {}).get("variants"),
                "required_scene_suffix_count": (matrix.get("evidence") or {}).get(
                    "required_scene_suffix_count"
                ),
            },
            gate_blockers(matrix),
        ),
        requirement(
            "image_diff_workflow",
            "Image diff workflow",
            regression.get("status", MISSING),
            {
                "gate": "image_diff_workflow",
                "message": regression.get("message"),
                **visual_regression_audit_evidence(summary, regression),
            },
            gate_blockers(regression),
        ),
        requirement(
            "gui_exercise_fixture_based",
            "GUI exercise is fixture-based",
            exercise.get("status", MISSING),
            {
                "gate": "gui_exercise",
                "message": exercise.get("message"),
                "exercise_summary": summary.get("gui_exercise_venv", {}).get("summary"),
                "workflow_summary": summary.get("gui_workflows_venv", {}).get("summary"),
                "passed_workflows": (exercise.get("evidence") or {}).get("passed_workflows"),
                "required_workflows": (exercise.get("evidence") or {}).get("required_workflows"),
                "gui_workflow_coverage_selftest": (exercise.get("evidence") or {}).get(
                    "gui_workflow_coverage_selftest", {}
                ),
                "task_scene_count": (exercise.get("evidence") or {}).get("task_scene_count"),
            },
            gate_blockers(exercise),
        ),
        requirement(
            "crash_gate",
            "Crash gate",
            crash.get("status", MISSING),
            {
                "gate": "crash_gate",
                "message": crash.get("message"),
                "failure_count": len((crash.get("evidence") or {}).get("failures", [])),
                "failure_suites": issue_suites(crash),
                "failures": (crash.get("evidence") or {}).get("failures", []),
                "classification": (crash.get("evidence") or {}).get("classification"),
            },
            gate_blockers(crash),
        ),
        requirement(
            "manual_smoke_pass",
            "Manual smoke pass",
            manual.get("status", MISSING),
            {
                "gate": "manual_smoke",
                "message": manual.get("message"),
                "expected_artifact": (manual.get("evidence") or {}).get(
                    "expected", "/tmp/freecad-test-results/manual-smoke.json"
                ),
                "template_artifact": "/tmp/freecad-test-results/manual-smoke.template.json",
                "manual_smoke_selftest": (manual.get("evidence") or {}).get(
                    "manual_smoke_selftest", {}
                ),
            },
            gate_blockers(manual),
        ),
    ]

    counts: dict[str, int] = {}
    for item in requirements:
        counts[item["status"]] = counts.get(item["status"], 0) + 1

    spec = requirement_spec or load_requirement_spec(DEFAULT_REQUIREMENT_SPEC)
    spec_report = spec_coverage(requirements, spec)
    coverage_selftest_support = {
        "status": selftest_status(coverage_selftest),
        "message": (
            "Coverage config self-test proves visual requirements are config-driven"
            if (coverage_selftest or {}).get("result") == "ok"
            else "Coverage config self-test artifact is missing or failed"
        ),
        "evidence": {
            "result": (coverage_selftest or {}).get("result"),
            "scenario_count": (coverage_selftest or {}).get("scenario_count"),
            "failed_scenarios": (coverage_selftest or {}).get("failed_scenarios", []),
        },
    }
    gate_selftest_support = {
        "status": selftest_status(gate_selftest),
        "message": (
            "Gate evaluator self-test proves gate composition blocks known unsafe states"
            if (gate_selftest or {}).get("result") == "ok"
            else "Gate evaluator self-test artifact is missing or failed"
        ),
        "evidence": {
            "result": (gate_selftest or {}).get("result"),
            "scenario_count": (gate_selftest or {}).get("scenario_count"),
            "failed_scenarios": (gate_selftest or {}).get("failed_scenarios", []),
        },
    }
    requirement_audit_selftest_support = {
        "status": selftest_status(requirement_audit_selftest),
        "message": (
            "Requirement audit self-test proves checklist mapping and spec coverage"
            if (requirement_audit_selftest or {}).get("result") == "ok"
            else "Requirement audit self-test artifact is missing or failed"
        ),
        "evidence": {
            "result": (requirement_audit_selftest or {}).get("result"),
            "scenario_count": (requirement_audit_selftest or {}).get("scenario_count"),
            "failed_scenarios": (requirement_audit_selftest or {}).get("failed_scenarios", []),
            "expected_requirement_count": (requirement_audit_selftest or {}).get(
                "expected_requirement_count"
            ),
        },
    }
    runner_selftest = summary.get("run_ui_test_baseline_selftest", {})
    provenance_selftest = summary.get("artifact_provenance_selftest", {})
    json_integrity = summary.get("json_artifact_integrity", {})
    json_integrity_selftest = summary.get("json_artifact_integrity_selftest", {})
    runner_selftest_support = {
        "status": selftest_status(runner_selftest),
        "message": (
            "Runner self-test proves wrapper logging does not pollute redirected artifacts"
            if runner_selftest.get("result") == "ok"
            else "Runner self-test artifact is missing or failed"
        ),
        "evidence": {
            "result": runner_selftest.get("result"),
            "scenario_count": runner_selftest.get("scenario_count"),
            "failed_scenarios": runner_selftest.get("failed_scenarios", []),
            "report": runner_selftest.get("report"),
        },
    }
    provenance_selftest_support = {
        "status": selftest_status(provenance_selftest),
        "message": (
            "Artifact provenance self-test proves stale, missing, and modified artifacts are rejected"
            if provenance_selftest.get("result") == "ok"
            else "Artifact provenance self-test artifact is missing or failed"
        ),
        "evidence": {
            "result": provenance_selftest.get("result"),
            "scenario_count": provenance_selftest.get("scenario_count"),
            "failed_scenarios": provenance_selftest.get("failed_scenarios", []),
            "report": provenance_selftest.get("report"),
        },
    }
    json_integrity_support = {
        "status": PASS if json_integrity.get("result") == "ok" else selftest_status(json_integrity),
        "message": (
            "JSON artifact integrity report proves baseline-owned JSON artifacts are strict JSON"
            if json_integrity.get("result") == "ok"
            else "JSON artifact integrity report is missing or failed"
        ),
        "evidence": {
            "result": json_integrity.get("result"),
            "checked_count": json_integrity.get("checked_count"),
            "checked": json_integrity.get("checked", []),
            "failure_count": json_integrity.get("failure_count"),
            "failures": json_integrity.get("failures", []),
            "report": json_integrity.get("report"),
        },
    }
    json_integrity_selftest_support = {
        "status": selftest_status(json_integrity_selftest),
        "message": (
            "JSON artifact integrity self-test proves polluted JSON artifacts are rejected"
            if json_integrity_selftest.get("result") == "ok"
            else "JSON artifact integrity self-test artifact is missing or failed"
        ),
        "evidence": {
            "result": json_integrity_selftest.get("result"),
            "scenario_count": json_integrity_selftest.get("scenario_count"),
            "failed_scenarios": json_integrity_selftest.get("failed_scenarios", []),
            "report": json_integrity_selftest.get("report"),
        },
    }
    supporting_gates = {
        "dependency_coverage": {
            "status": dependency.get("status", MISSING),
            "message": dependency.get("message"),
            "evidence": dependency.get("evidence") or {},
        },
        "coverage_config_selftest": coverage_selftest_support,
        "gate_evaluator_selftest": gate_selftest_support,
        "requirement_audit_selftest": requirement_audit_selftest_support,
        "runner_selftest": runner_selftest_support,
        "artifact_provenance_selftest": provenance_selftest_support,
        "json_artifact_integrity": json_integrity_support,
        "json_artifact_integrity_selftest": json_integrity_selftest_support,
    }
    if run_status is not None:
        run_status_selftest_support = {
            "status": selftest_status(run_status_selftest),
            "message": (
                "Run-status self-test proves full-run status validation catches incomplete runs"
                if (run_status_selftest or {}).get("result") == "ok"
                else "Run-status self-test artifact is missing or failed"
            ),
            "evidence": {
                "result": (run_status_selftest or {}).get("result"),
                "scenario_count": (run_status_selftest or {}).get("scenario_count"),
                "failed_scenarios": (run_status_selftest or {}).get("failed_scenarios", []),
            },
        }
        supporting_gates["run_status_selftest"] = run_status_selftest_support
        supporting_gates["full_runner_status"] = {
            "status": PASS if run_status.get("result") == "ok" else FAIL,
            "message": (
                "Full baseline runner step status is complete"
                if run_status.get("result") == "ok"
                else "Full baseline runner has missing or nonzero step statuses"
            ),
            "evidence": {
                "result": run_status.get("result"),
                "failure_count": run_status.get("failure_count"),
                "discovered_step_count": run_status.get("discovered_step_count"),
                "failures": run_status.get("failures", []),
            },
        }
    required_supporting_statuses = [
        supporting_gates["coverage_config_selftest"]["status"],
        supporting_gates["gate_evaluator_selftest"]["status"],
        supporting_gates["requirement_audit_selftest"]["status"],
        supporting_gates["runner_selftest"]["status"],
        supporting_gates["artifact_provenance_selftest"]["status"],
        supporting_gates["json_artifact_integrity"]["status"],
        supporting_gates["json_artifact_integrity_selftest"]["status"],
    ]
    if "full_runner_status" in supporting_gates:
        required_supporting_statuses.append(supporting_gates["run_status_selftest"]["status"])
        required_supporting_statuses.append(supporting_gates["full_runner_status"]["status"])
    ready = (
        set(counts) == {PASS}
        and spec_report["status"] == PASS
        and all(status == PASS for status in required_supporting_statuses)
    )
    return {
        "schema": SCHEMA,
        "overall_status": PASS if ready else FAIL,
        "ready_for_sweeping_style_change": ready,
        "requirement_counts": counts,
        "requirements": requirements,
        "requirement_spec_coverage": spec_report,
        "supporting_gates": supporting_gates,
        "source": {
            "summary_overall_status": gate_report.get("overall_status"),
            "summary_ready_for_sweeping_style_change": gate_report.get(
                "ready_for_sweeping_style_change"
            ),
            "gate_status_counts": gate_report.get("status_counts"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=Path("/tmp/freecad-test-results/baseline-summary.json"))
    parser.add_argument("--gate", type=Path, default=Path("/tmp/freecad-test-results/ui-style-gate.json"))
    parser.add_argument(
        "--coverage-selftest",
        type=Path,
        default=Path("/tmp/freecad-test-results/ui-style-coverage-selftest.json"),
    )
    parser.add_argument(
        "--gate-selftest",
        type=Path,
        default=Path("/tmp/freecad-test-results/ui-style-gate-selftest.json"),
    )
    parser.add_argument(
        "--requirement-audit-selftest",
        type=Path,
        default=Path("/tmp/freecad-test-results/ui-style-requirement-audit-selftest.json"),
    )
    parser.add_argument("--run-status", type=Path)
    parser.add_argument("--run-status-selftest", type=Path)
    parser.add_argument("--requirement-spec", type=Path, default=DEFAULT_REQUIREMENT_SPEC)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = build_report(
        read_json(args.summary),
        read_json(args.gate),
        read_optional_json(args.coverage_selftest),
        read_optional_json(args.gate_selftest),
        read_optional_json(args.requirement_audit_selftest),
        read_optional_json(args.run_status) if args.run_status else None,
        read_optional_json(args.run_status_selftest) if args.run_status_selftest else None,
        load_requirement_spec(args.requirement_spec),
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report["overall_status"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
