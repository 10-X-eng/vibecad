#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Self-test UI/style readiness gate composition."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import evaluate_ui_style_gate
import manual_smoke


SCHEMA = "freecad-ui-style-gate-selftest-v1"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ok_selftest(name: str = "selftest.json") -> dict[str, Any]:
    return {
        "present": True,
        "result": "ok",
        "scenario_count": 1,
        "failed_scenarios": [],
        "report": name,
    }


def visual_baseline_harness_ok_selftest() -> dict[str, Any]:
    scenario_names = sorted(evaluate_ui_style_gate.REQUIRED_VISUAL_BASELINE_HARNESS_SELFTEST_SCENARIOS)
    return {
        "present": True,
        "result": "ok",
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "failed_scenarios": [],
        "report": "visual-baseline-harness-selftest.json",
    }


def screenshot_integrity_ok_selftest() -> dict[str, Any]:
    scenario_names = sorted(evaluate_ui_style_gate.REQUIRED_SCREENSHOT_INTEGRITY_SELFTEST_SCENARIOS)
    return {
        "present": True,
        "result": "ok",
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "failed_scenarios": [],
        "report": "screenshot-integrity-selftest.json",
    }


def workflow_coverage_ok_selftest() -> dict[str, Any]:
    scenario_names = sorted(evaluate_ui_style_gate.REQUIRED_WORKFLOW_COVERAGE_SELFTEST_SCENARIOS)
    return {
        "present": True,
        "result": "ok",
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "failed_scenarios": [],
        "report": "workflow-coverage-selftest.json",
    }


def manual_smoke_ok_selftest() -> dict[str, Any]:
    scenario_names = sorted(evaluate_ui_style_gate.REQUIRED_MANUAL_SMOKE_SELFTEST_SCENARIOS)
    return {
        "present": True,
        "result": "ok",
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "failed_scenarios": [],
        "report": "manual-smoke-selftest.json",
    }


def artifact_provenance_ok_selftest() -> dict[str, Any]:
    scenario_names = sorted(evaluate_ui_style_gate.REQUIRED_ARTIFACT_PROVENANCE_SELFTEST_SCENARIOS)
    return {
        "present": True,
        "result": "ok",
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "failed_scenarios": [],
        "report": "artifact-provenance-selftest.json",
    }


def json_artifact_integrity_ok_selftest() -> dict[str, Any]:
    scenario_names = sorted(evaluate_ui_style_gate.REQUIRED_JSON_ARTIFACT_INTEGRITY_SELFTEST_SCENARIOS)
    return {
        "present": True,
        "result": "ok",
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "failed_scenarios": [],
        "report": "json-artifact-integrity-selftest.json",
    }


def dependency_smoke_ok_selftest() -> dict[str, Any]:
    scenario_names = sorted(evaluate_ui_style_gate.REQUIRED_DEPENDENCY_SMOKE_SELFTEST_SCENARIOS)
    return {
        "present": True,
        "result": "ok",
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "failed_scenarios": [],
        "report": "dependency-smoke-selftest.json",
    }


def ctest_inventory_ok_selftest() -> dict[str, Any]:
    scenario_names = sorted(evaluate_ui_style_gate.REQUIRED_CTEST_INVENTORY_SELFTEST_SCENARIOS)
    return {
        "present": True,
        "result": "ok",
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "failed_scenarios": [],
        "report": "ctest-inventory-selftest.json",
    }


def registered_classification_ok_selftest() -> dict[str, Any]:
    scenario_names = sorted(evaluate_ui_style_gate.REQUIRED_REGISTERED_CLASSIFICATION_SELFTEST_SCENARIOS)
    return {
        "present": True,
        "result": "ok",
        "scenario_count": len(scenario_names),
        "scenario_names": scenario_names,
        "failed_scenarios": [],
        "report": "registered-classification-selftest.json",
    }


def visual_summary(
    required: set[str],
    discovered_workbenches: set[str] | None = None,
    include_cleanup: bool = False,
    return_checks: dict[str, str] | None = None,
) -> dict[str, Any]:
    scenes = [{"scene": scene} for scene in sorted(required)]
    if include_cleanup:
        for scene in scenes:
            scene["cleanup"] = {
                "before_count": 1,
                "after_count": 0,
                "result": "ok",
            }
    if return_checks:
        for scene in scenes:
            expected = return_checks.get(scene["scene"])
            if expected:
                scene["return_check"] = {
                    "kind": "opened_file",
                    "expected": expected,
                    "opened": expected,
                    "active_document": "SelfTestDocument",
                }
    summary = {
        "result": "ok",
        "scene_count": len(scenes),
        "failed_scene_count": 0,
        "traceback_count": 0,
        "unallowed_traceback_count": 0,
        "scenes": scenes,
        "failed_scenes": [],
    }
    if discovered_workbenches is not None:
        summary["discovered_workbenches"] = sorted(discovered_workbenches)
        summary["captured_workbenches"] = sorted(
            scene.removeprefix("workbench-")
            for scene in required
            if scene.startswith("workbench-")
        )
    return summary


def regression_summary(work_dir: Path, label: str) -> dict[str, Any]:
    diff_dir = work_dir / f"{label}-diffs"
    diff_dir.mkdir(parents=True, exist_ok=True)
    review_json = diff_dir / "review-index.json"
    review_html = diff_dir / "review-index.html"
    review_json.write_text('{"failures":[]}\n', encoding="utf-8")
    review_html.write_text("<!doctype html><title>Review index</title>\n", encoding="utf-8")
    return {
        "manifest_present": True,
        "check_report": "visual-regression-check.json",
        "check_present": True,
        "check_result": "ok",
        "check_manifest": "visual-approved.json",
        "check_capture_dir": "visual-current",
        "check_diff_dir": str(diff_dir),
        "check_policy": {
            "max_changed_ratio": 0.03,
            "max_rms": 8.0,
            "new_findings_fail": True,
            "baseline_images_are_portable": True,
        },
        "check_approval_command": (
            "gui_visual_regression.py approve --capture-dir visual-current --manifest visual-approved.json "
            "--reviewer '<name>' --approval-note '<why this visual change is intentional>'"
        ),
        "check_review_index": {
            "json": str(review_json),
            "html": str(review_html),
        },
        "failure_count": 0,
        "failure_kind_counts": {},
        "approved_scene_count": 1,
        "current_capture_scene_count": 1,
        "format": 2,
        "absolute_screenshot_count": 0,
        "portable_screenshot_count": 1,
        "missing_context_fingerprint_count": 0,
        "missing_context_identity_count": 0,
        "approval": {
            "reviewer": "ui style gate self-test",
            "note": "Synthetic visual baseline approval metadata.",
            "approved_utc": "2026-01-01T00:00:00+00:00",
            "source_capture_dir": "visual-current",
        },
        "policy": {
            "max_changed_ratio": 0.03,
            "max_rms": 8.0,
            "new_findings_fail": True,
            "baseline_images_are_portable": True,
        },
    }


def write_manual_smoke(results_dir: Path, summary: dict[str, Any]) -> None:
    artifact = manual_smoke.template()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    artifact["created_utc"] = now
    artifact["completed_utc"] = now
    artifact["tester"] = "ui style gate self-test"
    artifact["build"].update(manual_smoke.expected_build_from_summary(summary))
    artifact["baseline_run"].update(manual_smoke.expected_run_from_summary(summary))
    artifact["environment"] = {
        "display": "xvfb",
        "theme": "default",
        "dpi_scale": "1.0",
        "font_size": "default",
    }
    evidence_dir = results_dir / "manual-smoke-evidence"
    for name, check in artifact["checks"].items():
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / f"{name}.txt"
        evidence_path.write_text(f"Synthetic manual smoke evidence for {name}\n", encoding="utf-8")
        check["status"] = "pass"
        check["notes"] = f"Synthetic pass for {name}"
        check["evidence"] = [str(evidence_path)]
    write_json(results_dir / "manual-smoke.json", artifact)


def synthetic_summary(work_dir: Path, coverage: dict[str, Any]) -> dict[str, Any]:
    events_path = work_dir / "events.jsonl"
    workflow_events = []
    for workflow in sorted(coverage["required_workflows"]):
        workflow_events.append({"status": "workflow_started", "workflow": workflow})
        for detail in coverage.get("required_workflow_details", {}).get(workflow, []):
            event = {"status": detail.get("status"), "workflow": workflow}
            event.update(detail.get("fields_equal") or {})
            for field in detail.get("fields_present", []):
                event.setdefault(field, f"selftest-{field}")
            if workflow == "switch_workbench" and event.get("status") == "workbench_active":
                event.pop("workflow", None)
            workflow_events.append(event)
        workflow_events.append({"status": "workflow_pass", "workflow": workflow})
    events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in workflow_events),
        encoding="utf-8",
    )
    discovered_workbenches = {
        scene.removeprefix("workbench-")
        for scene in coverage["required_workbench_scenes"]
    }
    discovered_workbench_scenes = {
        f"workbench-{name}" for name in discovered_workbenches
    }
    matrix_scenes = []
    for variant_name, slug in coverage["required_variant_slugs"].items():
        for suffix in sorted(
            coverage["required_fixture_scenes"]
            | coverage["required_workbench_scenes"]
            | coverage["required_dialog_scenes"]
            | coverage["required_task_scenes"]
            | discovered_workbench_scenes
        ):
            matrix_scenes.append({"scene": f"variant-{slug}-{suffix}"})
    freecad_version = [
        "26",
        "3",
        "0",
        "self-test",
        "self-test",
        "2026/01/01 00:00:00",
        "main",
        "selftestrevision",
    ]
    provenance_steps = {
        step: {
            "run_id_path": f"{step}.run_id",
            "present": True,
            "run_id": "selftest-run",
            "matches_current_run": True,
            "artifacts": [
                {
                    "path": f"{step}.json",
                    "present": True,
                    "modified_after_run_id": False,
                }
            ],
            "artifacts_present": True,
            "modified_after_run_id": False,
        }
        for step in [
            "ctest-N",
            "ctest",
            "ctest-not-run-check",
            "freecad-startup-smoke",
            "freecad-dependency-smoke",
            "dependency-smoke-selftest",
            "gui-layout-assertion-smoke",
            "freecad-t0",
            "freecad-registered-split",
            "freecad-registered-issue-classification",
            "registered-classification-selftest",
            "registered-harness-selftest",
            "gui-survey-venv",
            "gui-exercise-venv",
            "gui-workflows-venv",
            "gui-visual-venv",
            "gui-visual-fixtures",
            "gui-visual-matrix",
            "gui-visual-dialogs",
            "gui-visual-dialogs-native",
            "gui-visual-tasks",
            "gui-visual-regression-check",
            "gui-visual-fixtures-regression-check",
            "gui-visual-matrix-regression-check",
            "gui-visual-dialogs-regression-check",
            "gui-visual-tasks-regression-check",
            "gui-visual-regression-selftest",
            "gui-screenshot-integrity",
            "gui-screenshot-integrity-selftest",
            "manual-smoke-selftest",
            "gui-workflow-coverage-selftest",
            "gui-layout-assertion-coverage-selftest",
            "ui-style-coverage-selftest",
            "ui-style-gate-selftest",
            "ui-style-requirement-audit-selftest",
            "ui-style-run-status-selftest",
            "run-ui-test-baseline-selftest",
            "artifact-provenance-selftest",
            "json-artifact-integrity",
            "json-artifact-integrity-selftest",
        ]
    }
    return {
        "run_id": "selftest-run",
        "results_dir": str(work_dir),
        "artifact_provenance": {
            "required_step_count": len(provenance_steps),
            "missing_steps": [],
            "stale_steps": [],
            "missing_artifact_steps": [],
            "modified_after_run_id_steps": [],
            "all_required_steps_match": True,
            "steps": provenance_steps,
        },
        "build_dir": "build/release",
        "ctest": {"failed": 0, "pass_percent": 100, "run": 1, "inventory_total": 1, "not_run": []},
        "ctest_inventory_regression": {
            "check_present": True,
            "check_result": "ok",
            "approved_not_run_count": 0,
            "newly_runnable_count": 0,
            "failure_count": 0,
            "failures": [],
        },
        "ctest_inventory_selftest": ctest_inventory_ok_selftest(),
        "freecad_startup_smoke": {
            "result": "ok",
            "returncode": 0,
            "python_path_contains_venv": True,
            "freecad_version": freecad_version,
            "report": "startup.json",
        },
        "freecad_registered_tests": {"completed": True, "traceback_count": 0, "log": "freecad-t0.log"},
        "freecad_registered_split": {"present": True, "issues": [], "result_counts": {"ok": 1}},
        "freecad_registered_issue_classification": {
            "present": True,
            "result": "ok",
            "classified_issue_count": 0,
            "unclassified_issue_count": 0,
            "hard_blocker_count": 0,
        },
        "registered_classification_selftest": registered_classification_ok_selftest(),
        "registered_harness_selftest": ok_selftest(),
        "visual_baseline_harness_selftest": visual_baseline_harness_ok_selftest(),
        "gui_visual_venv": visual_summary(coverage["required_workbench_scenes"], discovered_workbenches),
        "gui_visual_fixtures": visual_summary(coverage["required_fixture_scenes"]),
        "gui_visual_dialogs": visual_summary(
            coverage["required_dialog_scenes"],
            include_cleanup=True,
            return_checks=coverage.get("required_dialog_return_checks", {}),
        ),
        "gui_visual_dialogs_native": visual_summary(
            coverage["required_dialog_scenes"],
            include_cleanup=True,
            return_checks=coverage.get("required_dialog_return_checks", {}),
        ),
        "gui_visual_tasks": visual_summary(coverage["required_task_scenes"], include_cleanup=True),
        "layout_assertion_smoke": {
            "present": True,
            "result": "ok",
            "observed": {
                "zero_size": True,
                "possible_text_clipping": True,
                "obvious_sibling_overlap": True,
                "outside_parent_bounds": True,
                "missing_button_text_or_icon": True,
                "task_panel_no_scroll_path": True,
                "low_text_contrast": True,
            },
            "missing": [],
            "examples": {
                name: [{"kind": name, "widget": {"class": "QWidget"}}]
                for name in coverage["required_layout_assertions"]
            },
            "report": "layout.json",
        },
        "layout_assertion_coverage_selftest": ok_selftest(),
        "gui_visual_matrix": {
            "result": "ok",
            "scene_count": len(matrix_scenes),
            "variant_count": len(coverage["required_variants"]),
            "failed_scene_count": 0,
            "variants": [
                {
                    "name": name,
                    "slug": coverage["required_variant_slugs"][name],
                    "config": coverage["required_variant_configs"].get(name, {"name": name}),
                }
                for name in sorted(coverage["required_variants"])
            ],
            "scenes": matrix_scenes,
        },
        "gui_visual_regression": regression_summary(work_dir, "visual"),
        "gui_visual_fixtures_regression": regression_summary(work_dir, "fixtures"),
        "gui_visual_dialogs_regression": regression_summary(work_dir, "dialogs"),
        "gui_visual_tasks_regression": regression_summary(work_dir, "tasks"),
        "gui_visual_matrix_regression": regression_summary(work_dir, "matrix"),
        "gui_visual_regression_selftest": {
            **ok_selftest(),
            "scenario_count": len(evaluate_ui_style_gate.REQUIRED_VISUAL_REGRESSION_SELFTEST_SCENARIOS),
            "scenario_names": sorted(evaluate_ui_style_gate.REQUIRED_VISUAL_REGRESSION_SELFTEST_SCENARIOS),
            "manifest_format": 2,
            "manifest_absolute_screenshot_count": 0,
            "manifest_missing_context_fingerprint_count": 0,
            "manifest_missing_context_identity_count": 0,
            "manifest_has_approval_metadata": True,
        },
        "gui_screenshot_integrity": {
            "present": True,
            "result": "ok",
            "capture_count": 5,
            "scene_count": (
                len(coverage["required_workbench_scenes"])
                + len(coverage["required_fixture_scenes"])
                + len(coverage["required_dialog_scenes"])
                + len(coverage["required_task_scenes"])
                + len(matrix_scenes)
            ),
            "failure_count": 0,
            "thresholds": {
                "min_width": 64,
                "min_height": 64,
                "min_stddev": 1.0,
                "min_unique_colors": 8,
                "min_visible_widgets": 1,
            },
            "failures": [],
            "report": "screenshot-integrity.json",
        },
        "gui_screenshot_integrity_selftest": screenshot_integrity_ok_selftest(),
        "gui_exercise_venv": {"result": "ok"},
        "gui_workflows_venv": {"result": "ok", "events_path": str(events_path)},
        "gui_workflow_coverage_selftest": workflow_coverage_ok_selftest(),
        "dependency_smoke": {
            "present": True,
            "result": "ok",
            "missing": {},
            "missing_count": 0,
            "report": "dependencies.json",
            "config": "optional_dependencies.default.json",
        },
        "dependency_smoke_selftest": dependency_smoke_ok_selftest(),
        "manual_smoke_selftest": manual_smoke_ok_selftest(),
        "run_ui_test_baseline_selftest": ok_selftest("run-ui-test-baseline-selftest.json"),
        "artifact_provenance_selftest": artifact_provenance_ok_selftest(),
        "json_artifact_integrity": {
            "present": True,
            "result": "ok",
            "checked_count": 40,
            "failure_count": 0,
            "failures": [],
            "report": "json-artifact-integrity.json",
        },
        "json_artifact_integrity_selftest": json_artifact_integrity_ok_selftest(),
    }


def run_case(
    name: str,
    summary: dict[str, Any],
    results_dir: Path,
    coverage: dict[str, Any],
    expected_overall: str,
    expected_gate: tuple[str, str] | None = None,
) -> dict[str, Any]:
    report = evaluate_ui_style_gate.evaluate(summary, Path.cwd(), results_dir, coverage)
    ok = report["overall_status"] == expected_overall
    if expected_gate:
        gate_name, gate_status = expected_gate
        ok = ok and report["gates"][gate_name]["status"] == gate_status
    return {
        "ok": ok,
        "overall_status": report["overall_status"],
        "ready_for_sweeping_style_change": report["ready_for_sweeping_style_change"],
        "status_counts": report["status_counts"],
        "gate_statuses": {gate: value["status"] for gate, value in report["gates"].items()},
    }


def crash_classification_evidence_case(
    summary: dict[str, Any],
    results_dir: Path,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    report = evaluate_ui_style_gate.evaluate(summary, Path.cwd(), results_dir, coverage)
    failures = report["gates"]["crash_gate"].get("evidence", {}).get("failures", [])
    first = failures[0] if failures else {}
    classification = first.get("classification") or {}
    ok = (
        report["gates"]["crash_gate"]["status"] == evaluate_ui_style_gate.FAIL
        and classification.get("present") is True
        and classification.get("hard_blocker") is True
        and classification.get("hard_blocker_required_by_result") is True
        and bool(classification.get("reason"))
    )
    return {
        "ok": ok,
        "crash_gate_status": report["gates"]["crash_gate"]["status"],
        "first_failure_classification": classification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-config", type=Path, default=Path("tools/ui_style_coverage.default.json"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/freecad-test-results/ui-style-gate-selftest.json"))
    args = parser.parse_args()

    coverage = evaluate_ui_style_gate.load_coverage_spec(args.coverage_config)
    with tempfile.TemporaryDirectory(prefix="freecad-ui-style-gate-selftest-") as temp:
        work_dir = Path(temp)
        clean_results = work_dir / "clean-results"
        clean_results.mkdir()
        clean_summary = synthetic_summary(work_dir, coverage)
        write_manual_smoke(clean_results, clean_summary)

        incomplete_ctest_inventory_selftest = json.loads(json.dumps(clean_summary))
        incomplete_ctest_inventory_selftest["ctest_inventory_selftest"]["scenario_names"] = [
            name
            for name in incomplete_ctest_inventory_selftest["ctest_inventory_selftest"][
                "scenario_names"
            ]
            if name != "new_not_run_test_fails"
        ]
        incomplete_ctest_inventory_selftest["ctest_inventory_selftest"]["scenario_count"] = len(
            incomplete_ctest_inventory_selftest["ctest_inventory_selftest"]["scenario_names"]
        )

        dependency_partial = json.loads(json.dumps(clean_summary))
        dependency_partial["dependency_smoke"]["result"] = "partial"
        dependency_partial["dependency_smoke"]["missing_count"] = 1
        dependency_partial["dependency_smoke"]["missing"] = {
            "SyntheticMissing": {"reason": "synthetic", "affects": ["self-test"]}
        }

        dependency_config_error = json.loads(json.dumps(clean_summary))
        dependency_config_error["dependency_smoke"]["result"] = "failed"
        dependency_config_error["dependency_smoke"]["config_errors"] = [
            {"kind": "duplicate_name", "name": "SyntheticDependency"}
        ]
        incomplete_dependency_selftest = json.loads(json.dumps(clean_summary))
        incomplete_dependency_selftest["dependency_smoke_selftest"]["scenario_names"] = [
            name
            for name in incomplete_dependency_selftest["dependency_smoke_selftest"][
                "scenario_names"
            ]
            if name != "duplicate_affects_fails_config"
        ]
        incomplete_dependency_selftest["dependency_smoke_selftest"]["scenario_count"] = len(
            incomplete_dependency_selftest["dependency_smoke_selftest"]["scenario_names"]
        )

        registered_failure = json.loads(json.dumps(clean_summary))
        registered_failure["freecad_registered_tests"] = {
            "completed": False,
            "traceback_count": 1,
            "log": "freecad-t0.log",
        }
        registered_failure["freecad_registered_split"] = {
            "present": True,
            "discovered_suite_count": 1,
            "selected_suite_count": 1,
            "result_counts": {"traceback": 1},
            "issues": [
                {
                    "suite": "SyntheticRegisteredSuite",
                    "result": "traceback",
                    "returncode": 0,
                    "traceback_count": 1,
                    "log": "synthetic-registered.log",
                }
            ],
        }
        registered_failure["freecad_registered_issue_classification"] = {
            "present": True,
            "result": "ok",
            "classified_issue_count": 1,
            "unclassified_issue_count": 0,
            "hard_blocker_count": 1,
            "errors": [],
            "classified_issues": [
                {
                    "suite": "SyntheticRegisteredSuite",
                    "result": "traceback",
                    "reason": "Synthetic classified traceback remains a hard blocker.",
                    "hard_blocker": True,
                    "hard_blocker_required_by_result": True,
                }
            ],
        }

        incomplete_registered_classification_selftest = json.loads(json.dumps(clean_summary))
        incomplete_registered_classification_selftest["registered_classification_selftest"][
            "scenario_names"
        ] = [
            name
            for name in incomplete_registered_classification_selftest["registered_classification_selftest"][
                "scenario_names"
            ]
            if name != "nonblocking_hard_failure_fails"
        ]
        incomplete_registered_classification_selftest["registered_classification_selftest"][
            "scenario_count"
        ] = len(
            incomplete_registered_classification_selftest["registered_classification_selftest"][
                "scenario_names"
            ]
        )

        registered_crash = json.loads(json.dumps(clean_summary))
        registered_crash["freecad_registered_tests"] = {
            "completed": False,
            "traceback_count": 0,
            "log": "freecad-t0.log",
        }
        registered_crash["freecad_registered_split"] = {
            "present": True,
            "discovered_suite_count": 1,
            "selected_suite_count": 1,
            "result_counts": {"crash": 1},
            "issues": [
                {
                    "suite": "SyntheticCrashSuite",
                    "result": "crash",
                    "returncode": 1,
                    "traceback_count": 0,
                    "log": "synthetic-crash.log",
                }
            ],
        }
        registered_crash["freecad_registered_issue_classification"] = {
            "present": True,
            "result": "ok",
            "classified_issue_count": 1,
            "unclassified_issue_count": 0,
            "hard_blocker_count": 1,
            "errors": [],
            "classified_issues": [
                {
                    "suite": "SyntheticCrashSuite",
                    "result": "crash",
                    "reason": "Synthetic classified crash remains a hard blocker.",
                    "hard_blocker": True,
                    "hard_blocker_required_by_result": True,
                }
            ],
        }

        registered_timeout = json.loads(json.dumps(registered_crash))
        registered_timeout["freecad_registered_split"]["result_counts"] = {"timeout": 1}
        registered_timeout["freecad_registered_split"]["issues"][0].update(
            {
                "suite": "SyntheticTimeoutSuite",
                "result": "timeout",
                "returncode": 124,
                "log": "synthetic-timeout.log",
            }
        )
        registered_timeout["freecad_registered_issue_classification"]["classified_issues"][0].update(
            {
                "suite": "SyntheticTimeoutSuite",
                "result": "timeout",
                "reason": "Synthetic classified timeout remains a hard blocker.",
            }
        )

        registered_process_errors = json.loads(json.dumps(registered_crash))
        registered_process_errors["freecad_registered_split"]["result_counts"] = {
            "ok_with_process_errors": 1
        }
        registered_process_errors["freecad_registered_split"]["issues"][0].update(
            {
                "suite": "SyntheticProcessErrorSuite",
                "result": "ok_with_process_errors",
                "returncode": 0,
                "log": "synthetic-process-errors.log",
            }
        )
        registered_process_errors["freecad_registered_issue_classification"]["classified_issues"][0].update(
            {
                "suite": "SyntheticProcessErrorSuite",
                "result": "ok_with_process_errors",
                "reason": "Synthetic classified process error remains a hard blocker.",
            }
        )

        visual_crash = json.loads(json.dumps(clean_summary))
        visual_crash["gui_visual_tasks"]["result"] = "process_failed"
        visual_crash["gui_visual_tasks"]["process_returncode"] = 1
        visual_crash["gui_visual_tasks"]["traceback_count"] = 1
        visual_crash["gui_visual_tasks"]["unallowed_traceback_count"] = 1
        visual_crash["gui_visual_tasks"]["process_log"] = "synthetic-visual-tasks.log"

        visual_traceback_zero_returncode = json.loads(json.dumps(clean_summary))
        visual_traceback_zero_returncode["gui_visual_dialogs"]["result"] = "ok"
        visual_traceback_zero_returncode["gui_visual_dialogs"]["process_returncode"] = 0
        visual_traceback_zero_returncode["gui_visual_dialogs"]["traceback_count"] = 1
        visual_traceback_zero_returncode["gui_visual_dialogs"]["unallowed_traceback_count"] = 1
        visual_traceback_zero_returncode["gui_visual_dialogs"]["process_log"] = (
            "synthetic-visual-dialogs.log"
        )

        missing_workbench_inventory = json.loads(json.dumps(clean_summary))
        missing_workbench_inventory["gui_visual_venv"].pop("discovered_workbenches", None)
        missing_workbench_inventory["gui_visual_venv"].pop("captured_workbenches", None)

        screenshot_integrity_failure = json.loads(json.dumps(clean_summary))
        screenshot_integrity_failure["gui_screenshot_integrity"]["result"] = "fail"
        screenshot_integrity_failure["gui_screenshot_integrity"]["failure_count"] = 1
        screenshot_integrity_failure["gui_screenshot_integrity"]["failures"] = [
            {
                "capture_dir": "synthetic-capture",
                "failure_count": 1,
                "failures": [
                    {
                        "scene": "synthetic-scene",
                        "failures": ["screenshot_low_variance"],
                    }
                ],
            }
        ]
        incomplete_screenshot_integrity_selftest = json.loads(json.dumps(clean_summary))
        incomplete_screenshot_integrity_selftest["gui_screenshot_integrity_selftest"][
            "scenario_names"
        ] = [
            name
            for name in incomplete_screenshot_integrity_selftest["gui_screenshot_integrity_selftest"][
                "scenario_names"
            ]
            if name != "outside_screenshot_path_fails"
        ]
        incomplete_screenshot_integrity_selftest["gui_screenshot_integrity_selftest"][
            "scenario_count"
        ] = len(
            incomplete_screenshot_integrity_selftest["gui_screenshot_integrity_selftest"][
                "scenario_names"
            ]
        )

        layout_examples_missing = json.loads(json.dumps(clean_summary))
        layout_examples_missing["layout_assertion_smoke"]["examples"].pop("zero_size", None)

        visual_harness_selftest_failure = json.loads(json.dumps(clean_summary))
        visual_harness_selftest_failure["visual_baseline_harness_selftest"]["result"] = "failed"
        visual_harness_selftest_failure["visual_baseline_harness_selftest"]["failed_scenarios"] = [
            "reset_output_dir_removes_stale_summary_screenshots_and_variants"
        ]

        incomplete_visual_harness_selftest = json.loads(json.dumps(clean_summary))
        incomplete_visual_harness_selftest["visual_baseline_harness_selftest"][
            "scenario_names"
        ] = [
            name
            for name in incomplete_visual_harness_selftest["visual_baseline_harness_selftest"][
                "scenario_names"
            ]
            if name != "preflight_rejects_duplicate_variant_slugs"
        ]
        incomplete_visual_harness_selftest["visual_baseline_harness_selftest"][
            "scenario_count"
        ] = len(
            incomplete_visual_harness_selftest["visual_baseline_harness_selftest"][
                "scenario_names"
            ]
        )

        cleanup_failure = json.loads(json.dumps(clean_summary))
        cleanup_failure["gui_visual_dialogs"]["scenes"][0]["cleanup"] = {
            "before_count": 1,
            "after_count": 1,
            "result": "left_open",
        }
        missing_dialog_return_check = json.loads(json.dumps(clean_summary))
        for scene in missing_dialog_return_check["gui_visual_dialogs"]["scenes"]:
            if scene.get("return_check"):
                scene.pop("return_check", None)
                break

        workflow_config_error_coverage = json.loads(json.dumps(clean_summary))
        incomplete_workflow_selftest = json.loads(json.dumps(clean_summary))
        incomplete_workflow_selftest["gui_workflow_coverage_selftest"][
            "scenario_names"
        ] = [
            name
            for name in incomplete_workflow_selftest["gui_workflow_coverage_selftest"][
                "scenario_names"
            ]
            if name != "duplicate_workflow_pass_event_fails_gate"
        ]
        incomplete_workflow_selftest["gui_workflow_coverage_selftest"][
            "scenario_count"
        ] = len(
            incomplete_workflow_selftest["gui_workflow_coverage_selftest"][
                "scenario_names"
            ]
        )
        incomplete_manual_smoke_selftest = json.loads(json.dumps(clean_summary))
        incomplete_manual_smoke_selftest["manual_smoke_selftest"]["scenario_names"] = [
            name
            for name in incomplete_manual_smoke_selftest["manual_smoke_selftest"][
                "scenario_names"
            ]
            if name != "too_new_file_evidence_fails"
        ]
        incomplete_manual_smoke_selftest["manual_smoke_selftest"]["scenario_count"] = len(
            incomplete_manual_smoke_selftest["manual_smoke_selftest"]["scenario_names"]
        )
        runner_selftest_failure = json.loads(json.dumps(clean_summary))
        runner_selftest_failure["run_ui_test_baseline_selftest"]["result"] = "failed"
        runner_selftest_failure["run_ui_test_baseline_selftest"]["failed_scenarios"] = [
            "run_step_keeps_redirected_json_clean"
        ]

        artifact_provenance_selftest_failure = json.loads(json.dumps(clean_summary))
        artifact_provenance_selftest_failure["artifact_provenance_selftest"][
            "result"
        ] = "failed"
        artifact_provenance_selftest_failure["artifact_provenance_selftest"][
            "failed_scenarios"
        ] = ["modified_after_run_marker_fails"]
        incomplete_artifact_provenance_selftest = json.loads(json.dumps(clean_summary))
        incomplete_artifact_provenance_selftest["artifact_provenance_selftest"][
            "scenario_names"
        ] = [
            name
            for name in incomplete_artifact_provenance_selftest["artifact_provenance_selftest"][
                "scenario_names"
            ]
            if name != "modified_after_run_marker_fails"
        ]
        incomplete_artifact_provenance_selftest["artifact_provenance_selftest"][
            "scenario_count"
        ] = len(
            incomplete_artifact_provenance_selftest["artifact_provenance_selftest"][
                "scenario_names"
            ]
        )

        json_integrity_failure = json.loads(json.dumps(clean_summary))
        json_integrity_failure["json_artifact_integrity"]["result"] = "failed"
        json_integrity_failure["json_artifact_integrity"]["failure_count"] = 1
        json_integrity_failure["json_artifact_integrity"]["failures"] = [
            {
                "path": "gui-visual-regression-check.json",
                "error": "Expecting value: line 1 column 1 (char 0)",
                "prefix": "== gui-visual-regression-check ==",
            }
        ]
        incomplete_json_integrity_selftest = json.loads(json.dumps(clean_summary))
        incomplete_json_integrity_selftest["json_artifact_integrity_selftest"][
            "scenario_names"
        ] = [
            name
            for name in incomplete_json_integrity_selftest["json_artifact_integrity_selftest"][
                "scenario_names"
            ]
            if name != "prefixed_json_is_rejected"
        ]
        incomplete_json_integrity_selftest["json_artifact_integrity_selftest"][
            "scenario_count"
        ] = len(
            incomplete_json_integrity_selftest["json_artifact_integrity_selftest"][
                "scenario_names"
            ]
        )

        old_visual_regression_report = json.loads(json.dumps(clean_summary))
        for key in [
            "gui_visual_regression",
            "gui_visual_fixtures_regression",
            "gui_visual_dialogs_regression",
            "gui_visual_tasks_regression",
            "gui_visual_matrix_regression",
        ]:
            for field in [
                "check_manifest",
                "check_capture_dir",
                "current_capture_scene_count",
                "check_diff_dir",
                "check_policy",
                "check_approval_command",
            ]:
                old_visual_regression_report[key].pop(field, None)

        visual_regression_scene_count_mismatch = json.loads(json.dumps(clean_summary))
        visual_regression_scene_count_mismatch["gui_visual_dialogs_regression"][
            "approved_scene_count"
        ] = 3
        visual_regression_scene_count_mismatch["gui_visual_dialogs_regression"][
            "current_capture_scene_count"
        ] = 4

        incomplete_visual_regression_selftest = json.loads(json.dumps(clean_summary))
        incomplete_visual_regression_selftest["gui_visual_regression_selftest"][
            "scenario_names"
        ] = [
            name
            for name in incomplete_visual_regression_selftest["gui_visual_regression_selftest"][
                "scenario_names"
            ]
            if name != "reapproved_large_change_passes"
        ]
        incomplete_visual_regression_selftest["gui_visual_regression_selftest"][
            "scenario_count"
        ] = len(
            incomplete_visual_regression_selftest["gui_visual_regression_selftest"][
                "scenario_names"
            ]
        )

        missing_visual_review_index_file = json.loads(json.dumps(clean_summary))
        missing_visual_review_index_file["gui_visual_regression"]["check_review_index"][
            "html"
        ] = str(work_dir / "missing-review-index.html")

        stale_artifact_provenance = json.loads(json.dumps(clean_summary))
        stale_artifact_provenance["artifact_provenance"]["all_required_steps_match"] = False
        stale_artifact_provenance["artifact_provenance"]["stale_steps"] = [
            "gui-visual-venv"
        ]
        stale_artifact_provenance["artifact_provenance"]["steps"]["gui-visual-venv"][
            "run_id"
        ] = "older-run"
        stale_artifact_provenance["artifact_provenance"]["steps"]["gui-visual-venv"][
            "matches_current_run"
        ] = False

        modified_after_run_provenance = json.loads(json.dumps(clean_summary))
        modified_after_run_provenance["artifact_provenance"]["all_required_steps_match"] = False
        modified_after_run_provenance["artifact_provenance"]["modified_after_run_id_steps"] = [
            "gui-visual-regression-check"
        ]
        modified_after_run_provenance["artifact_provenance"]["steps"]["gui-visual-regression-check"][
            "modified_after_run_id"
        ] = True
        modified_after_run_provenance["artifact_provenance"]["steps"]["gui-visual-regression-check"][
            "artifacts"
        ][0]["modified_after_run_id"] = True

        missing_variant_identity = json.loads(json.dumps(clean_summary))
        for variant in missing_variant_identity["gui_visual_matrix"]["variants"]:
            if variant.get("name") != "default":
                variant.pop("config", None)
                break

        missing_manual_results = work_dir / "missing-manual"
        missing_manual_results.mkdir()

        cases = {
            "all_clean_gates_pass": run_case(
                "all_clean_gates_pass",
                clean_summary,
                clean_results,
                coverage,
                evaluate_ui_style_gate.PASS,
            ),
            "incomplete_ctest_inventory_selftest_blocks_core_gate": run_case(
                "incomplete_ctest_inventory_selftest_blocks_core_gate",
                incomplete_ctest_inventory_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("core_tests", evaluate_ui_style_gate.FAIL),
            ),
            "partial_dependency_blocks_overall_readiness": run_case(
                "partial_dependency_blocks_overall_readiness",
                dependency_partial,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("dependency_coverage", evaluate_ui_style_gate.PARTIAL),
            ),
            "dependency_config_error_blocks_overall_readiness": run_case(
                "dependency_config_error_blocks_overall_readiness",
                dependency_config_error,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("dependency_coverage", evaluate_ui_style_gate.FAIL),
            ),
            "incomplete_dependency_selftest_blocks_dependency_gate": run_case(
                "incomplete_dependency_selftest_blocks_dependency_gate",
                incomplete_dependency_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("dependency_coverage", evaluate_ui_style_gate.FAIL),
            ),
            "registered_suite_issue_blocks_overall_readiness": run_case(
                "registered_suite_issue_blocks_overall_readiness",
                registered_failure,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("registered_tests", evaluate_ui_style_gate.FAIL),
            ),
            "incomplete_registered_classification_selftest_blocks_registered_gate": run_case(
                "incomplete_registered_classification_selftest_blocks_registered_gate",
                incomplete_registered_classification_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("registered_tests", evaluate_ui_style_gate.FAIL),
            ),
            "registered_traceback_blocks_crash_gate": run_case(
                "registered_traceback_blocks_crash_gate",
                registered_failure,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("crash_gate", evaluate_ui_style_gate.FAIL),
            ),
            "registered_crash_gate_reports_classification_linkage": crash_classification_evidence_case(
                registered_failure,
                clean_results,
                coverage,
            ),
            "registered_crash_blocks_crash_gate": run_case(
                "registered_crash_blocks_crash_gate",
                registered_crash,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("crash_gate", evaluate_ui_style_gate.FAIL),
            ),
            "registered_timeout_blocks_crash_gate": run_case(
                "registered_timeout_blocks_crash_gate",
                registered_timeout,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("crash_gate", evaluate_ui_style_gate.FAIL),
            ),
            "registered_process_errors_block_crash_gate": run_case(
                "registered_process_errors_block_crash_gate",
                registered_process_errors,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("crash_gate", evaluate_ui_style_gate.FAIL),
            ),
            "visual_process_failure_blocks_crash_gate": run_case(
                "visual_process_failure_blocks_crash_gate",
                visual_crash,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("crash_gate", evaluate_ui_style_gate.FAIL),
            ),
            "visual_unallowed_traceback_blocks_crash_gate": run_case(
                "visual_unallowed_traceback_blocks_crash_gate",
                visual_traceback_zero_returncode,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("crash_gate", evaluate_ui_style_gate.FAIL),
            ),
            "missing_discovered_workbench_inventory_blocks_visual_gate": run_case(
                "missing_discovered_workbench_inventory_blocks_visual_gate",
                missing_workbench_inventory,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("visual_baselines", evaluate_ui_style_gate.FAIL),
            ),
            "screenshot_integrity_failure_blocks_visual_gate": run_case(
                "screenshot_integrity_failure_blocks_visual_gate",
                screenshot_integrity_failure,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("visual_baselines", evaluate_ui_style_gate.FAIL),
            ),
            "incomplete_screenshot_integrity_selftest_blocks_visual_gate": run_case(
                "incomplete_screenshot_integrity_selftest_blocks_visual_gate",
                incomplete_screenshot_integrity_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("visual_baselines", evaluate_ui_style_gate.FAIL),
            ),
            "visual_harness_selftest_failure_blocks_visual_gate": run_case(
                "visual_harness_selftest_failure_blocks_visual_gate",
                visual_harness_selftest_failure,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("visual_baselines", evaluate_ui_style_gate.FAIL),
            ),
            "incomplete_visual_harness_selftest_blocks_visual_gate": run_case(
                "incomplete_visual_harness_selftest_blocks_visual_gate",
                incomplete_visual_harness_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("visual_baselines", evaluate_ui_style_gate.FAIL),
            ),
            "dialog_cleanup_failure_blocks_visual_gate": run_case(
                "dialog_cleanup_failure_blocks_visual_gate",
                cleanup_failure,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("visual_baselines", evaluate_ui_style_gate.FAIL),
            ),
            "dialog_return_check_failure_blocks_visual_gate": run_case(
                "dialog_return_check_failure_blocks_visual_gate",
                missing_dialog_return_check,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("visual_baselines", evaluate_ui_style_gate.FAIL),
            ),
            "scene_config_error_blocks_visual_gate": run_case(
                "scene_config_error_blocks_visual_gate",
                clean_summary,
                clean_results,
                {**coverage, "fixture_config_errors": ["duplicate_fixture_scene_name:part-crank"]},
                evaluate_ui_style_gate.FAIL,
                ("visual_baselines", evaluate_ui_style_gate.FAIL),
            ),
            "workbench_config_error_blocks_visual_gate": run_case(
                "workbench_config_error_blocks_visual_gate",
                clean_summary,
                clean_results,
                {
                    **coverage,
                    "workbench_config_errors": [
                        "empty_required_list:coverage_config.required_workbenches"
                    ],
                },
                evaluate_ui_style_gate.FAIL,
                ("visual_baselines", evaluate_ui_style_gate.FAIL),
            ),
            "workflow_config_error_blocks_gui_exercise_gate": run_case(
                "workflow_config_error_blocks_gui_exercise_gate",
                workflow_config_error_coverage,
                clean_results,
                {**coverage, "workflow_config_errors": ["duplicate_required_workflow:switch_workbench"]},
                evaluate_ui_style_gate.FAIL,
                ("gui_exercise", evaluate_ui_style_gate.FAIL),
            ),
            "incomplete_workflow_selftest_blocks_gui_exercise_gate": run_case(
                "incomplete_workflow_selftest_blocks_gui_exercise_gate",
                incomplete_workflow_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("gui_exercise", evaluate_ui_style_gate.FAIL),
            ),
            "runner_selftest_failure_blocks_infrastructure_gate": run_case(
                "runner_selftest_failure_blocks_infrastructure_gate",
                runner_selftest_failure,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("test_infrastructure", evaluate_ui_style_gate.FAIL),
            ),
            "artifact_provenance_selftest_failure_blocks_infrastructure_gate": run_case(
                "artifact_provenance_selftest_failure_blocks_infrastructure_gate",
                artifact_provenance_selftest_failure,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("test_infrastructure", evaluate_ui_style_gate.FAIL),
            ),
            "incomplete_artifact_provenance_selftest_blocks_infrastructure_gate": run_case(
                "incomplete_artifact_provenance_selftest_blocks_infrastructure_gate",
                incomplete_artifact_provenance_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("test_infrastructure", evaluate_ui_style_gate.FAIL),
            ),
            "json_integrity_failure_blocks_infrastructure_gate": run_case(
                "json_integrity_failure_blocks_infrastructure_gate",
                json_integrity_failure,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("test_infrastructure", evaluate_ui_style_gate.FAIL),
            ),
            "incomplete_json_integrity_selftest_blocks_infrastructure_gate": run_case(
                "incomplete_json_integrity_selftest_blocks_infrastructure_gate",
                incomplete_json_integrity_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("test_infrastructure", evaluate_ui_style_gate.FAIL),
            ),
            "old_visual_regression_report_blocks_image_diff_gate": run_case(
                "old_visual_regression_report_blocks_image_diff_gate",
                old_visual_regression_report,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("image_diff_workflow", evaluate_ui_style_gate.FAIL),
            ),
            "visual_regression_scene_count_mismatch_blocks_image_diff_gate": run_case(
                "visual_regression_scene_count_mismatch_blocks_image_diff_gate",
                visual_regression_scene_count_mismatch,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("image_diff_workflow", evaluate_ui_style_gate.FAIL),
            ),
            "incomplete_visual_regression_selftest_blocks_image_diff_gate": run_case(
                "incomplete_visual_regression_selftest_blocks_image_diff_gate",
                incomplete_visual_regression_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("image_diff_workflow", evaluate_ui_style_gate.FAIL),
            ),
            "missing_visual_review_index_file_blocks_image_diff_gate": run_case(
                "missing_visual_review_index_file_blocks_image_diff_gate",
                missing_visual_review_index_file,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("image_diff_workflow", evaluate_ui_style_gate.FAIL),
            ),
            "stale_artifact_provenance_blocks_infrastructure_gate": run_case(
                "stale_artifact_provenance_blocks_infrastructure_gate",
                stale_artifact_provenance,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("test_infrastructure", evaluate_ui_style_gate.FAIL),
            ),
            "modified_after_run_artifact_blocks_infrastructure_gate": run_case(
                "modified_after_run_artifact_blocks_infrastructure_gate",
                modified_after_run_provenance,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("test_infrastructure", evaluate_ui_style_gate.FAIL),
            ),
            "layout_config_error_blocks_layout_gate": run_case(
                "layout_config_error_blocks_layout_gate",
                clean_summary,
                clean_results,
                {**coverage, "layout_config_errors": ["duplicate_required_assertion:zero_size"]},
                evaluate_ui_style_gate.FAIL,
                ("layout_assertions", evaluate_ui_style_gate.FAIL),
            ),
            "layout_missing_examples_block_layout_gate": run_case(
                "layout_missing_examples_block_layout_gate",
                layout_examples_missing,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("layout_assertions", evaluate_ui_style_gate.FAIL),
            ),
            "variant_config_error_blocks_matrix_gate": run_case(
                "variant_config_error_blocks_matrix_gate",
                clean_summary,
                clean_results,
                {**coverage, "variant_config_errors": ["duplicate_variant_name:default"]},
                evaluate_ui_style_gate.FAIL,
                ("theme_dpi_font_matrix", evaluate_ui_style_gate.FAIL),
            ),
            "missing_variant_identity_blocks_matrix_gate": run_case(
                "missing_variant_identity_blocks_matrix_gate",
                missing_variant_identity,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("theme_dpi_font_matrix", evaluate_ui_style_gate.FAIL),
            ),
            "missing_manual_smoke_blocks_overall_readiness": run_case(
                "missing_manual_smoke_blocks_overall_readiness",
                clean_summary,
                missing_manual_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("manual_smoke", evaluate_ui_style_gate.MISSING),
            ),
            "incomplete_manual_smoke_selftest_blocks_manual_gate": run_case(
                "incomplete_manual_smoke_selftest_blocks_manual_gate",
                incomplete_manual_smoke_selftest,
                clean_results,
                coverage,
                evaluate_ui_style_gate.FAIL,
                ("manual_smoke", evaluate_ui_style_gate.FAIL),
            ),
        }

    failed = [name for name, result in cases.items() if not result["ok"]]
    report = {
        "schema": SCHEMA,
        "result": "ok" if not failed else "failed",
        "scenario_count": len(cases),
        "failed_scenarios": failed,
        "scenarios": cases,
    }
    write_json(args.output, report)
    return 0 if report["result"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
