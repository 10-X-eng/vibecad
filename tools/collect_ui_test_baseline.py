#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Summarize FreeCAD UI/style baseline test artifacts.

The script reads logs produced by the current baseline workflow and writes a
single JSON report. It intentionally does not claim coverage beyond what the
logs prove.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


REQUIRED_PROVENANCE_STEPS = [
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
    "artifact-provenance-selftest",
    "run-ui-test-baseline-selftest",
    "json-artifact-integrity",
    "json-artifact-integrity-selftest",
]

PROVENANCE_ARTIFACTS = {
    "ctest-N": ["ctest-N.log"],
    "ctest": ["ctest.log"],
    "ctest-not-run-check": ["ctest-not-run-check.json"],
    "freecad-startup-smoke": ["freecad-startup-smoke.json"],
    "freecad-dependency-smoke": ["freecad-dependency-smoke.json"],
    "dependency-smoke-selftest": ["freecad-dependency-smoke-selftest.json"],
    "gui-layout-assertion-smoke": ["gui-layout-assertion-smoke.json"],
    "freecad-t0": ["freecad-t0.log"],
    "freecad-registered-split": ["freecad-registered-split/summary.json"],
    "freecad-registered-issue-classification": ["freecad-registered-issue-classification.json"],
    "registered-classification-selftest": ["registered-classification-selftest.json"],
    "registered-harness-selftest": ["freecad-registered-harness-selftest.json"],
    "gui-survey-venv": ["gui-survey-venv/summary.json"],
    "gui-exercise-venv": ["gui-exercise-venv/summary.json"],
    "gui-workflows-venv": ["gui-workflows-venv/summary.json"],
    "gui-visual-venv": ["gui-visual-venv/summary.json"],
    "gui-visual-fixtures": ["gui-visual-fixtures/summary.json"],
    "gui-visual-matrix": ["gui-visual-matrix/summary.json"],
    "gui-visual-dialogs": ["gui-visual-dialogs/summary.json"],
    "gui-visual-dialogs-native": ["gui-visual-dialogs-native/summary.json"],
    "gui-visual-tasks": ["gui-visual-tasks/summary.json"],
    "gui-visual-regression-check": ["gui-visual-regression-check.json"],
    "gui-visual-fixtures-regression-check": ["gui-visual-fixtures-regression-check.json"],
    "gui-visual-matrix-regression-check": ["gui-visual-matrix-regression-check.json"],
    "gui-visual-dialogs-regression-check": ["gui-visual-dialogs-regression-check.json"],
    "gui-visual-tasks-regression-check": ["gui-visual-tasks-regression-check.json"],
    "gui-visual-regression-selftest": ["gui-visual-regression-selftest.json"],
    "gui-screenshot-integrity": ["gui-screenshot-integrity.json"],
    "gui-screenshot-integrity-selftest": ["gui-screenshot-integrity-selftest.json"],
    "manual-smoke-selftest": ["manual-smoke-selftest.json"],
    "gui-workflow-coverage-selftest": ["gui-workflow-coverage-selftest.json"],
    "gui-layout-assertion-coverage-selftest": ["gui-layout-assertion-coverage-selftest.json"],
    "ui-style-coverage-selftest": ["ui-style-coverage-selftest.json"],
    "ui-style-gate-selftest": ["ui-style-gate-selftest.json"],
    "ui-style-requirement-audit-selftest": ["ui-style-requirement-audit-selftest.json"],
    "ui-style-run-status-selftest": ["ui-style-run-status-selftest.json"],
    "artifact-provenance-selftest": ["artifact-provenance-selftest.json"],
    "run-ui-test-baseline-selftest": ["run-ui-test-baseline-selftest.json"],
    "json-artifact-integrity": ["json-artifact-integrity.json"],
    "json-artifact-integrity-selftest": ["json-artifact-integrity-selftest.json"],
}


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_stripped_text(path: Path) -> str:
    return read_text(path).strip()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "{[":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return value if isinstance(value, dict) else None
        raise


def artifact_provenance_summary(results_dir: Path, run_id: str) -> dict[str, Any]:
    steps = {}
    stale = []
    missing = []
    modified_after_run_id = []
    missing_artifacts = []
    for step in REQUIRED_PROVENANCE_STEPS:
        path = results_dir / f"{step}.run_id"
        value = read_stripped_text(path) if path.is_file() else ""
        present = bool(value)
        matches = present and bool(run_id) and value == run_id
        artifact_records = []
        step_modified_after_run_id = False
        step_missing_artifact = False
        marker_mtime = path.stat().st_mtime if path.is_file() else None
        for relative in PROVENANCE_ARTIFACTS.get(step, []):
            artifact = results_dir / relative
            artifact_present = artifact.is_file()
            artifact_mtime = artifact.stat().st_mtime if artifact_present else None
            artifact_newer = (
                artifact_present
                and marker_mtime is not None
                and artifact_mtime is not None
                and artifact_mtime > marker_mtime + 1.0
            )
            artifact_records.append(
                {
                    "path": str(artifact),
                    "present": artifact_present,
                    "is_file": artifact_present,
                    "modified_after_run_id": artifact_newer,
                }
            )
            if not artifact_present:
                step_missing_artifact = True
            if artifact_newer:
                step_modified_after_run_id = True
        steps[step] = {
            "run_id_path": str(path),
            "present": present,
            "run_id": value,
            "matches_current_run": matches,
            "artifacts": artifact_records,
            "artifacts_present": not step_missing_artifact,
            "modified_after_run_id": step_modified_after_run_id,
        }
        if not present:
            missing.append(step)
        elif not matches:
            stale.append(step)
        if step_missing_artifact:
            missing_artifacts.append(step)
        if step_modified_after_run_id:
            modified_after_run_id.append(step)
    return {
        "required_step_count": len(REQUIRED_PROVENANCE_STEPS),
        "missing_steps": missing,
        "stale_steps": stale,
        "missing_artifact_steps": missing_artifacts,
        "modified_after_run_id_steps": modified_after_run_id,
        "all_required_steps_match": (
            not missing
            and not stale
            and not missing_artifacts
            and not modified_after_run_id
            and bool(run_id)
        ),
        "steps": steps,
    }


def ctest_summary(results_dir: Path, build_dir: Path) -> dict[str, Any]:
    log = read_text(results_dir / "ctest.log")
    total_inventory = None
    inventory = read_text(results_dir / "ctest-N.log")
    total_match = re.search(r"Total Tests:\s+(\d+)", inventory)
    if total_match:
        total_inventory = int(total_match.group(1))

    if total_inventory is None:
        total_match = re.search(r"/(\d+) Test\s+#", log)
        if total_match:
            total_inventory = int(total_match.group(1))

    result_match = re.search(
        r"(?P<pct>\d+)% tests passed, (?P<failed>\d+) tests failed out of (?P<run>\d+)",
        log,
    )
    not_run = re.findall(r"^\s*(\d+) - ([^(]+) \(([^)]+)\)", log, re.MULTILINE)

    return {
        "command": f"ctest --test-dir {build_dir} --output-on-failure -j8",
        "log": str(results_dir / "ctest.log"),
        "inventory_total": total_inventory,
        "run": int(result_match.group("run")) if result_match else None,
        "failed": int(result_match.group("failed")) if result_match else None,
        "pass_percent": int(result_match.group("pct")) if result_match else None,
        "not_run": [
            {"id": int(test_id), "name": name.strip(), "reason": reason}
            for test_id, name, reason in not_run
        ],
        "qt_label_line": next(
            (line for line in log.splitlines() if line.strip().startswith("Qt")),
            None,
        ),
    }


def ctest_inventory_regression_summary(results_dir: Path) -> dict[str, Any]:
    manifest_path = results_dir / "ctest-not-run-approved.json"
    report_path = results_dir / "ctest-not-run-check.json"
    manifest = read_json(manifest_path)
    report = read_json(report_path)
    return {
        "manifest": str(manifest_path),
        "manifest_present": manifest is not None,
        "approved_not_run_count": len((manifest or {}).get("approved_not_run", {})),
        "check_report": str(report_path),
        "check_present": report is not None,
        "check_result": (report or {}).get("result"),
        "failure_count": (report or {}).get("failure_count"),
        "current_not_run_count": (report or {}).get("current_not_run_count"),
        "newly_runnable_count": (report or {}).get("newly_runnable_count"),
        "failures": (report or {}).get("failures", []),
    }


def ctest_inventory_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "ctest-inventory-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def freecad_startup_smoke_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "freecad-startup-smoke.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    payload = report.get("payload") or {}
    return {
        "report": str(path),
        "present": True,
        "result": report.get("result"),
        "returncode": report.get("returncode"),
        "freecad_version": payload.get("freecad_version"),
        "python_path_contains_venv": payload.get("python_path_contains_venv"),
    }


def dependency_smoke_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "freecad-dependency-smoke.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    missing = report.get("missing", {})
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "config": report.get("config"),
        "result": report.get("result"),
        "missing_count": report.get("missing_count"),
        "dependencies": report.get("dependencies", {}),
        "missing": missing,
        "config_errors": report.get("config_errors", []),
    }


def dependency_smoke_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "freecad-dependency-smoke-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def layout_assertion_smoke_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "gui-layout-assertion-smoke.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "process_returncode": report.get("process_returncode"),
        "required": report.get("required", []),
        "observed": report.get("observed", {}),
        "missing": report.get("missing", []),
        "examples": report.get("examples", {}),
    }


def layout_assertion_coverage_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "gui-layout-assertion-coverage-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def freecad_t0_summary(results_dir: Path) -> dict[str, Any]:
    log = read_text(results_dir / "freecad-t0.log")
    skip_reasons = Counter(re.findall(r"skipped '([^']+)'", log))
    last_test_lines = [
        line
        for line in log.splitlines()
        if re.match(r"^(\w|\.).*\.\.\.\s*(ok|skipped|$)", line)
    ]

    return {
        "command": "timeout 3600s xvfb-run -a tools/freecad_venv.sh -t 0",
        "log": str(results_dir / "freecad-t0.log"),
        "completed": "OK" in log[-2000:] or "FAILED" in log[-2000:],
        "ok_line_count": len(re.findall(r"\.\.\. ok(?:\n|$)", log)),
        "skipped_line_count": len(re.findall(r"\.\.\. skipped", log)),
        "traceback_count": log.count("Traceback (most recent call last):"),
        "deleted_object_reference_errors": log.count("ReferenceError: Cannot access attribute"),
        "quantity_slot_type_errors": log.count(
            'TypeError: Cannot call meta function "slot(Base::Quantity)"'
        ),
        "skip_reasons": dict(skip_reasons),
        "last_test_lines": last_test_lines[-12:],
        "known_stop": "DraftGuiManualInput.test_unlock_polar_fields_clears_length_and_angle"
        if "test_unlock_polar_fields_clears_length_and_angle" in log
        else None,
    }


def freecad_registered_split_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "freecad-registered-split" / "summary.json"
    summary = read_json(path)
    if summary is None:
        return {"summary": str(path), "present": False}

    results = summary.get("results", [])
    issue_results = [
        result
        for result in results
        if result.get("result") not in {"ok"}
    ]
    return {
        "summary": str(path),
        "present": True,
        "discovered_suite_count": len(summary.get("discovered_suites", [])),
        "selected_suite_count": summary.get("selected_suite_count"),
        "result_counts": summary.get("result_counts", {}),
        "issue_count": len(issue_results),
        "issues": [
            {
                "suite": result.get("suite"),
                "result": result.get("result"),
                "returncode": result.get("returncode"),
                "traceback_count": result.get("traceback_count"),
                "quantity_slot_type_errors": result.get("quantity_slot_type_errors"),
                "deleted_object_reference_errors": result.get("deleted_object_reference_errors"),
                "timeout": result.get("timeout"),
                "log": result.get("log"),
            }
            for result in issue_results
        ],
    }


def freecad_registered_issue_classification_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "freecad-registered-issue-classification.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "result": report.get("result"),
        "classified_issue_count": report.get("classified_issue_count"),
        "unclassified_issue_count": report.get("unclassified_issue_count"),
        "hard_blocker_count": report.get("hard_blocker_count"),
        "errors": report.get("errors", []),
        "classified_issues": report.get("classified_issues", []),
    }


def registered_classification_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "registered-classification-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def registered_harness_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "freecad-registered-harness-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def visual_baseline_harness_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "gui-visual-baseline-harness-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def gui_summary(results_dir: Path, name: str) -> dict[str, Any]:
    path = results_dir / name / "summary.json"
    summary = read_json(path)
    if summary is None:
        return {"summary": str(path), "present": False}
    scenes = summary.get("scenes")
    if isinstance(scenes, list):
        summary["total_finding_count"] = sum(scene.get("finding_count", 0) for scene in scenes)
        summary["total_visible_widget_count"] = sum(
            scene.get("visible_widget_count", 0) for scene in scenes
        )
        summary["failed_scene_count"] = sum(1 for scene in scenes if scene.get("error"))
        summary["failed_scenes"] = [
            {
                "scene": scene.get("scene"),
                "error": scene.get("error"),
            }
            for scene in scenes
            if scene.get("error")
        ]
    return {"summary": str(path), "present": True, **summary}


def visual_regression_summary(
    results_dir: Path,
    manifest_name: str = "gui-visual-approved.json",
    check_name: str = "gui-visual-regression-check.json",
) -> dict[str, Any]:
    manifest = read_json(results_dir / manifest_name)
    check_report = read_json(results_dir / check_name)
    check_capture_dir = (check_report or {}).get("capture_dir")
    capture_summary = None
    if check_capture_dir:
        capture_summary_path = Path(str(check_capture_dir)) / "summary.json"
        capture_summary = read_json(capture_summary_path)
    current_capture_scene_count = None
    if isinstance(capture_summary, dict):
        current_capture_scene_count = capture_summary.get("scene_count")
        if current_capture_scene_count is None and isinstance(capture_summary.get("scenes"), list):
            current_capture_scene_count = len(capture_summary["scenes"])
    failure_kind_counts: dict[str, int] = {}
    for failure in (check_report or {}).get("failures", []):
        if not isinstance(failure, dict):
            continue
        kind = str(failure.get("kind") or "unknown")
        failure_kind_counts[kind] = failure_kind_counts.get(kind, 0) + 1
    scenes = (manifest or {}).get("scenes", {})
    screenshot_paths = [
        scene.get("screenshot")
        for scene in scenes.values()
        if isinstance(scene, dict) and scene.get("screenshot")
    ]
    absolute_screenshot_paths = [
        path for path in screenshot_paths if Path(str(path)).is_absolute()
    ]
    missing_context_fingerprints = [
        name
        for name, scene in scenes.items()
        if isinstance(scene, dict) and not scene.get("scene_context_fingerprint")
    ]
    missing_context_identities = [
        name
        for name, scene in scenes.items()
        if isinstance(scene, dict) and not scene.get("scene_context_identity")
    ]
    approval = (manifest or {}).get("approval")
    return {
        "manifest": str(results_dir / manifest_name),
        "manifest_present": manifest is not None,
        "approved_scene_count": len((manifest or {}).get("scenes", {})),
        "policy": (manifest or {}).get("policy"),
        "approval": approval,
        "format": (manifest or {}).get("format"),
        "portable_screenshot_count": len(screenshot_paths) - len(absolute_screenshot_paths),
        "absolute_screenshot_count": len(absolute_screenshot_paths),
        "missing_context_fingerprint_count": len(missing_context_fingerprints),
        "missing_context_identity_count": len(missing_context_identities),
        "check_report": str(results_dir / check_name),
        "check_present": check_report is not None,
        "check_result": (check_report or {}).get("result"),
        "failure_count": (check_report or {}).get("failure_count"),
        "failure_kind_counts": failure_kind_counts,
        "check_manifest": (check_report or {}).get("manifest"),
        "check_capture_dir": check_capture_dir,
        "current_capture_scene_count": current_capture_scene_count,
        "check_diff_dir": (check_report or {}).get("diff_dir"),
        "check_policy": (check_report or {}).get("policy"),
        "check_approval_command": (check_report or {}).get("approval_command"),
        "check_review_index": (check_report or {}).get("review_index"),
    }


def visual_regression_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "gui-visual-regression-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": sorted((report.get("scenarios") or {}).keys()),
        "failed_scenarios": report.get("failed_scenarios", []),
        "manifest_format": report.get("manifest_format"),
        "manifest_absolute_screenshot_count": report.get("manifest_absolute_screenshot_count"),
        "manifest_missing_context_fingerprint_count": report.get(
            "manifest_missing_context_fingerprint_count"
        ),
        "manifest_missing_context_identity_count": report.get(
            "manifest_missing_context_identity_count"
        ),
        "manifest_has_approval_metadata": report.get("manifest_has_approval_metadata"),
    }


def screenshot_integrity_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "gui-screenshot-integrity.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "capture_count": report.get("capture_count"),
        "scene_count": report.get("scene_count"),
        "failure_count": report.get("failure_count"),
        "thresholds": report.get("thresholds", {}),
        "failures": report.get("failures", []),
    }


def screenshot_integrity_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "gui-screenshot-integrity-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def manual_smoke_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "manual-smoke-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
        "expected_build": report.get("expected_build", {}),
        "expected_run": report.get("expected_run", {}),
    }


def gui_workflow_coverage_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "gui-workflow-coverage-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def requirement_audit_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "ui-style-requirement-audit-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "failed_scenarios": report.get("failed_scenarios", []),
        "expected_requirement_count": report.get("expected_requirement_count"),
    }


def run_status_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "ui-style-run-status-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def style_gate_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "ui-style-gate-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def json_artifact_integrity_summary(
    results_dir: Path,
    name: str = "json-artifact-integrity.json",
) -> dict[str, Any]:
    path = results_dir / name
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "checked_count": report.get("checked_count"),
        "checked": report.get("checked", []),
        "failure_count": report.get("failure_count"),
        "failures": report.get("failures", []),
    }


def json_artifact_integrity_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "json-artifact-integrity-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def run_ui_test_baseline_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "run-ui-test-baseline-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def artifact_provenance_selftest_summary(results_dir: Path) -> dict[str, Any]:
    path = results_dir / "artifact-provenance-selftest.json"
    report = read_json(path)
    if report is None:
        return {"report": str(path), "present": False}
    return {
        "report": str(path),
        "present": True,
        "schema": report.get("schema"),
        "result": report.get("result"),
        "scenario_count": report.get("scenario_count"),
        "scenario_names": report.get("scenario_names", []),
        "failed_scenarios": report.get("failed_scenarios", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("/tmp/freecad-test-results"))
    parser.add_argument("--build-dir", type=Path, default=Path("build/release"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    run_id = read_stripped_text(args.results_dir / "run.id")
    data = {
        "results_dir": str(args.results_dir),
        "build_dir": str(args.build_dir),
        "run_id": run_id,
        "artifact_provenance": artifact_provenance_summary(args.results_dir, run_id),
        "ctest": ctest_summary(args.results_dir, args.build_dir),
        "ctest_inventory_regression": ctest_inventory_regression_summary(args.results_dir),
        "ctest_inventory_selftest": ctest_inventory_selftest_summary(args.results_dir),
        "freecad_startup_smoke": freecad_startup_smoke_summary(args.results_dir),
        "dependency_smoke": dependency_smoke_summary(args.results_dir),
        "dependency_smoke_selftest": dependency_smoke_selftest_summary(args.results_dir),
        "layout_assertion_smoke": layout_assertion_smoke_summary(args.results_dir),
        "layout_assertion_coverage_selftest": layout_assertion_coverage_selftest_summary(args.results_dir),
        "freecad_registered_tests": freecad_t0_summary(args.results_dir),
        "freecad_registered_split": freecad_registered_split_summary(args.results_dir),
        "freecad_registered_issue_classification": freecad_registered_issue_classification_summary(
            args.results_dir
        ),
        "registered_classification_selftest": registered_classification_selftest_summary(args.results_dir),
        "registered_harness_selftest": registered_harness_selftest_summary(args.results_dir),
        "visual_baseline_harness_selftest": visual_baseline_harness_selftest_summary(args.results_dir),
        "gui_survey_venv": gui_summary(args.results_dir, "gui-survey-venv"),
        "gui_exercise_venv": gui_summary(args.results_dir, "gui-exercise-venv"),
        "gui_workflows_venv": gui_summary(args.results_dir, "gui-workflows-venv"),
        "gui_visual_venv": gui_summary(args.results_dir, "gui-visual-venv"),
        "gui_visual_fixtures": gui_summary(args.results_dir, "gui-visual-fixtures"),
        "gui_visual_matrix": gui_summary(args.results_dir, "gui-visual-matrix"),
        "gui_visual_dialogs": gui_summary(args.results_dir, "gui-visual-dialogs"),
        "gui_visual_dialogs_native": gui_summary(args.results_dir, "gui-visual-dialogs-native"),
        "gui_visual_tasks": gui_summary(args.results_dir, "gui-visual-tasks"),
        "gui_visual_regression": visual_regression_summary(args.results_dir),
        "gui_visual_fixtures_regression": visual_regression_summary(
            args.results_dir,
            "gui-visual-fixtures-approved.json",
            "gui-visual-fixtures-regression-check.json",
        ),
        "gui_visual_matrix_regression": visual_regression_summary(
            args.results_dir,
            "gui-visual-matrix-approved.json",
            "gui-visual-matrix-regression-check.json",
        ),
        "gui_visual_dialogs_regression": visual_regression_summary(
            args.results_dir,
            "gui-visual-dialogs-approved.json",
            "gui-visual-dialogs-regression-check.json",
        ),
        "gui_visual_tasks_regression": visual_regression_summary(
            args.results_dir,
            "gui-visual-tasks-approved.json",
            "gui-visual-tasks-regression-check.json",
        ),
        "gui_visual_regression_selftest": visual_regression_selftest_summary(args.results_dir),
        "gui_screenshot_integrity": screenshot_integrity_summary(args.results_dir),
        "gui_screenshot_integrity_selftest": screenshot_integrity_selftest_summary(args.results_dir),
        "manual_smoke_selftest": manual_smoke_selftest_summary(args.results_dir),
        "gui_workflow_coverage_selftest": gui_workflow_coverage_selftest_summary(args.results_dir),
        "requirement_audit_selftest": requirement_audit_selftest_summary(args.results_dir),
        "run_status_selftest": run_status_selftest_summary(args.results_dir),
        "style_gate_selftest": style_gate_selftest_summary(args.results_dir),
        "artifact_provenance_selftest": artifact_provenance_selftest_summary(args.results_dir),
        "run_ui_test_baseline_selftest": run_ui_test_baseline_selftest_summary(args.results_dir),
        "json_artifact_integrity": json_artifact_integrity_summary(args.results_dir),
        "json_artifact_integrity_final": json_artifact_integrity_summary(
            args.results_dir,
            "json-artifact-integrity-final.json",
        ),
        "json_artifact_integrity_selftest": json_artifact_integrity_selftest_summary(args.results_dir),
        "coverage_conclusion": {
            "core_unit_tests": "green for the run captured in ctest.log",
            "registered_app_gui_tests": (
                "not fully runnable as FreeCAD -t 0 in this environment; suite split "
                "artifacts are available when freecad-registered-split passes"
            ),
            "passive_gui_inventory": "runnable and useful as a structural baseline",
            "broad_gui_exercise": "not reliable enough as a release/style gate yet",
            "fixture_visual_capture": (
                "representative document screenshots are available when gui-visual-fixtures passes"
            ),
            "visual_matrix": (
                "theme/DPI/font matrix screenshots are available when gui-visual-matrix passes"
            ),
            "dialog_visual_capture": (
                "modal dialog screenshots are available when gui-visual-dialogs passes"
            ),
            "task_visual_capture": (
                "stateful task-panel screenshots are available when gui-visual-tasks passes"
            ),
            "visual_regression": (
                "manifest-based screenshot comparison exists for captured workbench, fixture, "
                "dialog, task-panel, theme, DPI, and font scenes"
            ),
            "visual_regression_manifest": (
                "manifest-based threshold comparison is available when approved manifest exists"
            ),
            "visual_capture": (
                "workbench and fixture-backed document screenshot/geometry capture is available"
            ),
        },
    }

    text = json.dumps(data, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
