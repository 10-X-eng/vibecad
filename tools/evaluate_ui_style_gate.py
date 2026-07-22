#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Evaluate UI/style readiness from collected baseline artifacts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import manual_smoke


PASS = "pass"
PARTIAL = "partial"
FAIL = "fail"
MISSING = "missing"

REQUIRED_VISUAL_REGRESSION_SELFTEST_SCENARIOS = {
    "identical_passes",
    "small_image_drift_passes",
    "large_image_change_fails",
    "new_layout_finding_fails",
    "missing_scene_fails",
    "extra_scene_fails",
    "harness_metadata_change_passes",
    "scene_context_change_fails",
    "lax_manifest_policy_fails",
    "missing_scene_context_fingerprint_fails",
    "missing_scene_context_identity_fails",
    "missing_approval_metadata_fails",
    "reapproved_large_change_passes",
    "reapproved_new_layout_finding_passes",
}

REQUIRED_VISUAL_BASELINE_HARNESS_SELFTEST_SCENARIOS = {
    "reset_output_dir_removes_stale_summary_screenshots_and_variants",
    "preflight_allows_unique_scene_outputs_and_variants",
    "preflight_rejects_duplicate_fixture_scene_names",
    "preflight_rejects_fixture_output_name_collisions",
    "preflight_rejects_dialog_output_name_collisions",
    "preflight_rejects_task_output_name_collisions",
    "preflight_rejects_empty_scene_output_name",
    "preflight_rejects_duplicate_workbench_names",
    "preflight_rejects_duplicate_variant_slugs",
}

REQUIRED_SCREENSHOT_INTEGRITY_SELFTEST_SCENARIOS = {
    "valid_capture_passes",
    "blank_capture_fails",
    "missing_screenshot_fails",
    "too_small_capture_fails",
    "metadata_screen_size_mismatch_fails",
    "missing_captured_widget_metadata_fails",
    "visible_widget_count_too_low_fails",
    "failed_source_result_fails",
    "scene_error_fails",
    "outside_screenshot_path_fails",
    "outside_metadata_path_fails",
    "duplicate_screenshot_and_metadata_paths_fail",
}

REQUIRED_WORKFLOW_COVERAGE_SELFTEST_SCENARIOS = {
    "default_required_workflows_pass",
    "new_required_workflow_without_detail_contract_fails_config",
    "new_required_workflow_with_detail_contract_fails_without_runtime_events",
    "missing_required_workflow_detail_fails_gate",
    "workflow_detail_outside_start_pass_window_fails_gate",
    "workflow_fail_event_fails_gate",
    "duplicate_workflow_start_event_fails_gate",
    "duplicate_workflow_pass_event_fails_gate",
    "invalid_workflow_event_json_fails_gate",
    "workflow_event_directory_path_fails_gate",
    "invalid_required_workflow_detail_config_fails_gate",
    "duplicate_required_workflow_fails_config",
    "blank_required_workflow_fails_config",
}

REQUIRED_MANUAL_SMOKE_SELFTEST_SCENARIOS = {
    "valid_current_build_passes",
    "stale_build_fails",
    "stale_run_fails",
    "missing_run_fails",
    "future_completion_fails",
    "missing_evidence_fails",
    "blocked_status_fails",
    "fail_status_fails",
    "missing_required_check_fails",
    "wrong_description_fails",
    "placeholder_evidence_fails",
    "missing_path_evidence_fails",
    "unsupported_uri_evidence_fails",
    "placeholder_notes_fails",
    "stale_evidence_hint_fails",
    "stale_file_evidence_fails",
    "too_new_file_evidence_fails",
    "extra_check_fails",
}

REQUIRED_ARTIFACT_PROVENANCE_SELFTEST_SCENARIOS = {
    "clean_provenance_passes",
    "missing_run_marker_fails",
    "stale_run_marker_fails",
    "missing_required_artifact_fails",
    "directory_run_marker_fails",
    "directory_required_artifact_fails",
    "modified_after_run_marker_fails",
    "empty_current_run_id_fails",
}

REQUIRED_JSON_ARTIFACT_INTEGRITY_SELFTEST_SCENARIOS = {
    "strict_json_files_are_accepted",
    "prefixed_json_is_rejected",
    "include_list_ignores_unowned_json_files",
    "duplicate_include_pattern_is_rejected",
    "escaping_include_pattern_is_rejected",
}

REQUIRED_DEPENDENCY_SMOKE_SELFTEST_SCENARIOS = {
    "present_python_module_available",
    "missing_python_module_recorded",
    "present_executable_available",
    "missing_executable_recorded",
    "partial_when_any_dependency_missing",
    "duplicate_dependency_name_fails_config",
    "missing_affects_fails_config",
    "unsupported_kind_fails_config",
    "placeholder_affects_fails_config",
    "duplicate_affects_fails_config",
}

REQUIRED_CTEST_INVENTORY_SELFTEST_SCENARIOS = {
    "valid_inventory_passes",
    "new_not_run_test_fails",
    "reason_change_fails",
    "removed_not_run_test_passes",
    "lax_manifest_policy_fails",
    "duplicate_current_not_run_test_fails",
    "placeholder_manifest_reason_fails",
}

REQUIRED_REGISTERED_CLASSIFICATION_SELFTEST_SCENARIOS = {
    "valid_classification_passes",
    "count_range_classification_passes",
    "unclassified_issue_fails",
    "ok_result_with_traceback_requires_classification",
    "stale_classification_fails",
    "result_mismatch_fails",
    "missing_evidence_fails",
    "duplicate_classification_fails",
    "missing_reason_fails",
    "placeholder_reason_fails",
    "missing_required_evidence_list_fails",
    "missing_expected_counts_fails",
    "expected_count_mismatch_fails",
    "expected_count_range_mismatch_fails",
    "unknown_expected_count_field_fails",
    "unknown_expected_count_range_field_fails",
    "invalid_expected_count_range_fails",
    "blank_required_evidence_fails",
    "placeholder_required_evidence_fails",
    "nonblocking_hard_failure_fails",
}


def safe_scene_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value).strip("-")


def variant_slug(variant: dict[str, Any], index: int) -> str:
    raw = variant.get("name") or f"variant-{index:02d}"
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in raw).strip("-")
    return cleaned or f"variant-{index:02d}"


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def check(status: str, message: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "evidence": evidence or {},
    }


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def referenced_path_exists(value: Any, base_dir: Path) -> bool:
    if not value:
        return False
    path = Path(str(value))
    if not path.is_absolute():
        path = base_dir / path
    return path.exists()


def list_from_config(config: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(config, dict):
        value = config.get(key, [])
    else:
        value = config
    return value if isinstance(value, list) else []


def required_list_from_config(
    config: Any,
    key: str,
    label: str,
    errors: list[str],
) -> list[Any]:
    if not isinstance(config, dict):
        errors.append(f"config_must_be_object:{label}")
        return []
    if key not in config:
        errors.append(f"missing_required_list:{label}.{key}")
        return []
    value = config.get(key)
    if not isinstance(value, list):
        errors.append(f"required_list_must_be_array:{label}.{key}")
        return []
    if not value:
        errors.append(f"empty_required_list:{label}.{key}")
    return value


def load_referenced_json(config_path: Path, value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    path = Path(value)
    if not path.is_absolute():
        path = config_path.parent / path
    data = read_json(path)
    return data or {}


def load_referenced_json_with_errors(
    config_path: Path,
    value: str | None,
    label: str,
    errors: list[str],
) -> dict[str, Any]:
    if not value:
        errors.append(f"missing_config_reference:{label}")
        return {}
    path = Path(value)
    if not path.is_absolute():
        path = config_path.parent / path
    if not path.exists():
        errors.append(f"missing_config_file:{label}:{path}")
        return {}
    data = read_json(path)
    if not isinstance(data, dict):
        errors.append(f"config_must_be_object:{label}:{path}")
        return {}
    return data


def validate_scene_config(
    kind: str,
    scenes: list[Any],
    repo_root: Path,
    required_fields: set[str],
    either_fields: set[str] | None = None,
    validate_file_paths: bool = False,
) -> list[str]:
    errors = []
    seen_names = set()
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            errors.append(f"{kind}_scene_must_be_object:{index}")
            continue
        name = str(scene.get("name") or "").strip()
        label = name or str(index)
        if not name:
            errors.append(f"blank_{kind}_scene_name:{index}")
        elif name in seen_names:
            errors.append(f"duplicate_{kind}_scene_name:{name}")
        seen_names.add(name)
        for field in sorted(required_fields):
            if not str(scene.get(field) or "").strip():
                errors.append(f"missing_{kind}_scene_{field}:{label}")
        if either_fields and not any(str(scene.get(field) or "").strip() for field in either_fields):
            errors.append(f"missing_{kind}_scene_action:{label}")
        if validate_file_paths and scene.get("file"):
            file_path = Path(str(scene["file"]))
            if not file_path.is_absolute():
                file_path = repo_root / file_path
            if not file_path.exists():
                errors.append(f"missing_{kind}_scene_file:{label}:{scene['file']}")
    return errors


def scene_coverage_tags(scene: dict[str, Any]) -> set[str]:
    value = scene.get("coverage", [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def validate_required_scene_coverage(
    kind: str,
    scenes: list[Any],
    required_tags: set[str],
) -> list[str]:
    errors = []
    observed = set()
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        name = str(scene.get("name") or index)
        tags = scene_coverage_tags(scene)
        if not tags:
            errors.append(f"missing_{kind}_scene_coverage:{name}")
        observed.update(tags)
    missing = sorted(required_tags - observed)
    for tag in missing:
        errors.append(f"missing_required_{kind}_coverage:{tag}")
    return errors


def nonempty_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def validate_capture_contract(kind: str, scenes: list[Any], require_visible_text: bool) -> list[str]:
    errors = []
    valid_capture_targets = {"main_window", "active_modal", "active_window", "top_level_dialog"}
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        label = str(scene.get("name") or index)
        capture = str(scene.get("capture") or "").strip()
        if not capture:
            errors.append(f"missing_{kind}_scene_capture:{label}")
        elif capture not in valid_capture_targets:
            errors.append(f"invalid_{kind}_scene_capture:{label}:{capture}")
        if scene.get("close_after_capture") is not True:
            errors.append(f"{kind}_scene_must_close_after_capture:{label}")
        wait_ms = scene.get("wait_ms")
        try:
            wait = int(wait_ms)
        except (TypeError, ValueError):
            errors.append(f"invalid_{kind}_scene_wait_ms:{label}")
        else:
            if wait <= 0:
                errors.append(f"invalid_{kind}_scene_wait_ms:{label}")
        if not nonempty_string_list(scene.get("required_widget_class_contains")) and not nonempty_string_list(
            scene.get("required_widget_class_contains_any")
        ):
            errors.append(f"missing_{kind}_scene_required_widget_class:{label}")
        if (
            require_visible_text
            and not nonempty_string_list(scene.get("required_visible_text_contains"))
            and not nonempty_string_list(scene.get("required_visible_text_contains_any"))
        ):
            errors.append(f"missing_{kind}_scene_required_visible_text:{label}")
    return errors


def normalize_required_tags(kind: str, values: list[Any], errors: list[str]) -> set[str]:
    tags = set()
    for index, value in enumerate(values):
        tag = str(value or "").strip()
        if not tag:
            errors.append(f"blank_required_{kind}_coverage:{index}")
            continue
        if tag in tags:
            errors.append(f"duplicate_required_{kind}_coverage:{tag}")
        tags.add(tag)
    return tags


def load_coverage_spec(config_path: Path | None) -> dict[str, Any]:
    if config_path is None:
        config_path = Path(__file__).resolve().with_name("ui_style_coverage.default.json")
    repo_root = Path(__file__).resolve().parent.parent
    coverage_config_errors: list[str] = []
    if not config_path.exists():
        coverage_config_errors.append(f"missing_coverage_config:{config_path}")
    config = read_json(config_path) or {}
    if not isinstance(config, dict):
        coverage_config_errors.append(f"coverage_config_must_be_object:{config_path}")
        config = {}
    fixture_config_errors: list[str] = []
    dialog_config_errors: list[str] = []
    task_config_errors: list[str] = []
    variant_config_errors: list[str] = []
    fixture_config = load_referenced_json_with_errors(
        config_path,
        config.get("fixture_scene_config"),
        "fixture_scene_config",
        fixture_config_errors,
    )
    fixtures = required_list_from_config(
        fixture_config,
        "scenes",
        "fixture_scene_config",
        fixture_config_errors,
    )
    dialog_config = load_referenced_json_with_errors(
        config_path,
        config.get("dialog_scene_config"),
        "dialog_scene_config",
        dialog_config_errors,
    )
    dialogs = required_list_from_config(
        dialog_config,
        "dialog_scenes",
        "dialog_scene_config",
        dialog_config_errors,
    )
    task_config = load_referenced_json_with_errors(
        config_path,
        config.get("task_scene_config"),
        "task_scene_config",
        task_config_errors,
    )
    tasks = required_list_from_config(
        task_config,
        "task_scenes",
        "task_scene_config",
        task_config_errors,
    )
    variant_config = load_referenced_json_with_errors(
        config_path,
        config.get("variant_config"),
        "variant_config",
        variant_config_errors,
    )
    variants = required_list_from_config(
        variant_config,
        "variants",
        "variant_config",
        variant_config_errors,
    )
    fixture_config_errors.extend(
        validate_scene_config(
            "fixture",
            fixtures,
            repo_root,
            {"name", "workbench", "file"},
            validate_file_paths=True,
        )
    )
    raw_required_fixture_coverage = required_list_from_config(
        config,
        "required_fixture_coverage",
        "coverage_config",
        fixture_config_errors,
    )
    required_fixture_coverage = normalize_required_tags(
        "fixture", raw_required_fixture_coverage, fixture_config_errors
    )
    fixture_config_errors.extend(
        validate_required_scene_coverage("fixture", fixtures, required_fixture_coverage)
    )
    dialog_config_errors.extend(
        validate_scene_config(
            "dialog",
            dialogs,
            repo_root,
            {"name"},
            {"command", "python"},
        )
    )
    raw_required_dialog_coverage = required_list_from_config(
        config,
        "required_dialog_coverage",
        "coverage_config",
        dialog_config_errors,
    )
    required_dialog_coverage = normalize_required_tags(
        "dialog", raw_required_dialog_coverage, dialog_config_errors
    )
    dialog_config_errors.extend(
        validate_required_scene_coverage("dialog", dialogs, required_dialog_coverage)
    )
    dialog_config_errors.extend(
        validate_capture_contract("dialog", dialogs, require_visible_text=True)
    )
    required_dialog_return_checks = {}
    for index, scene in enumerate(dialogs, start=1):
        if not isinstance(scene, dict):
            continue
        expected_opened_file = str(scene.get("expect_opened_file") or "").strip()
        if not expected_opened_file:
            continue
        label = safe_scene_name(str(scene.get("name") or f"dialog-{index:03d}"))
        if not str(scene.get("accept_file") or "").strip():
            dialog_config_errors.append(f"dialog_return_check_missing_accept_file:{label}")
        expected_path = Path(expected_opened_file)
        if not expected_path.is_absolute():
            expected_path = repo_root / expected_path
        if not expected_path.exists():
            dialog_config_errors.append(f"dialog_return_check_missing_file:{label}:{expected_path}")
        required_dialog_return_checks[f"dialog-{label}"] = str(expected_path.resolve())
    task_config_errors.extend(
        validate_scene_config(
            "task",
            tasks,
            repo_root,
            {"name", "workbench"},
            {"command", "edit_object", "python", "select_object"},
            validate_file_paths=True,
        )
    )
    raw_required_task_coverage = required_list_from_config(
        config,
        "required_task_coverage",
        "coverage_config",
        task_config_errors,
    )
    required_task_coverage = normalize_required_tags(
        "task", raw_required_task_coverage, task_config_errors
    )
    task_config_errors.extend(
        validate_required_scene_coverage("task", tasks, required_task_coverage)
    )
    task_config_errors.extend(
        validate_capture_contract("task", tasks, require_visible_text=False)
    )
    seen_variants = set()
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict):
            variant_config_errors.append(f"variant_must_be_object:{index}")
            continue
        name = str(variant.get("name") or "").strip()
        if not name:
            variant_config_errors.append(f"blank_variant_name:{index}")
        elif name in seen_variants:
            variant_config_errors.append(f"duplicate_variant_name:{name}")
        seen_variants.add(name)
        if "font_scale" in variant:
            try:
                font_scale = float(variant["font_scale"])
            except (TypeError, ValueError):
                variant_config_errors.append(f"invalid_font_scale:{name or index}")
            else:
                if font_scale <= 0:
                    variant_config_errors.append(f"invalid_font_scale:{name or index}")
        if variant.get("preference_pack"):
            pack = Path(str(variant["preference_pack"]))
            if not pack.is_absolute():
                pack = repo_root / pack
            if not pack.exists():
                variant_config_errors.append(f"missing_preference_pack:{name or index}")
    workflow_config = load_referenced_json(config_path, config.get("workflow_config"))
    raw_required_workflows = workflow_config.get("required_workflows", [])
    required_workflows = []
    workflow_config_errors = []
    if not isinstance(raw_required_workflows, list):
        workflow_config_errors.append("required_workflows_must_be_list")
        raw_required_workflows = []
    seen_workflows = set()
    for index, name in enumerate(raw_required_workflows):
        workflow = str(name or "").strip()
        if not workflow:
            workflow_config_errors.append(f"blank_required_workflow:{index}")
            continue
        if workflow in seen_workflows:
            workflow_config_errors.append(f"duplicate_required_workflow:{workflow}")
        seen_workflows.add(workflow)
        required_workflows.append(workflow)
    raw_required_workflow_details = workflow_config.get("required_workflow_details", {})
    required_workflow_details: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(raw_required_workflow_details, dict):
        workflow_config_errors.append("required_workflow_details_must_be_object")
        raw_required_workflow_details = {}
    for workflow in required_workflows:
        detail_specs = raw_required_workflow_details.get(workflow)
        if not isinstance(detail_specs, list) or not detail_specs:
            workflow_config_errors.append(f"missing_required_workflow_details:{workflow}")
            required_workflow_details[workflow] = []
            continue
        required_workflow_details[workflow] = []
        for index, detail in enumerate(detail_specs):
            if not isinstance(detail, dict):
                workflow_config_errors.append(f"workflow_detail_must_be_object:{workflow}:{index}")
                continue
            status = str(detail.get("status") or "").strip()
            if not status:
                workflow_config_errors.append(f"missing_workflow_detail_status:{workflow}:{index}")
            fields_present = detail.get("fields_present", [])
            if isinstance(fields_present, str):
                fields_present = [fields_present]
            if not isinstance(fields_present, list):
                workflow_config_errors.append(f"workflow_detail_fields_present_must_be_list:{workflow}:{index}")
                fields_present = []
            fields_present = [str(field).strip() for field in fields_present if str(field).strip()]
            fields_equal = detail.get("fields_equal", {})
            if not isinstance(fields_equal, dict):
                workflow_config_errors.append(f"workflow_detail_fields_equal_must_be_object:{workflow}:{index}")
                fields_equal = {}
            required_workflow_details[workflow].append(
                {
                    "status": status,
                    "fields_present": fields_present,
                    "fields_equal": fields_equal,
                }
            )
    layout_config = load_referenced_json(config_path, config.get("layout_assertion_config"))
    raw_required_layout_assertions = layout_config.get("required_assertions", [])
    required_layout_assertions = set()
    layout_config_errors = []
    if not isinstance(raw_required_layout_assertions, list):
        layout_config_errors.append("required_assertions_must_be_list")
        raw_required_layout_assertions = []
    for index, name in enumerate(raw_required_layout_assertions):
        assertion = str(name or "").strip()
        if not assertion:
            layout_config_errors.append(f"blank_required_assertion:{index}")
            continue
        if assertion in required_layout_assertions:
            layout_config_errors.append(f"duplicate_required_assertion:{assertion}")
        required_layout_assertions.add(assertion)
    workbench_config_errors = []
    raw_required_workbenches = required_list_from_config(
        config,
        "required_workbenches",
        "coverage_config",
        workbench_config_errors,
    )
    required_workbenches = []
    seen_workbenches = set()
    for index, name in enumerate(raw_required_workbenches):
        workbench = str(name or "").strip()
        if not workbench:
            workbench_config_errors.append(f"blank_required_workbench:{index}")
            continue
        if workbench in seen_workbenches:
            workbench_config_errors.append(f"duplicate_required_workbench:{workbench}")
        seen_workbenches.add(workbench)
        required_workbenches.append(workbench)
    return {
        "config": str(config_path),
        "coverage_config_errors": coverage_config_errors,
        "workbench_config_errors": workbench_config_errors,
        "required_workbenches": required_workbenches,
        "required_workbench_scenes": {f"workbench-{name}" for name in required_workbenches},
        "required_fixture_scenes": {
            f"fixture-{scene.get('name') or f'scene-{index:03d}'}"
            for index, scene in enumerate(fixtures, start=1)
            if isinstance(scene, dict)
        },
        "required_fixture_coverage": sorted(required_fixture_coverage),
        "required_dialog_scenes": {
            f"dialog-{safe_scene_name(str(scene.get('name') or f'dialog-{index:03d}'))}"
            for index, scene in enumerate(dialogs, start=1)
            if isinstance(scene, dict)
        },
        "required_dialog_return_checks": required_dialog_return_checks,
        "required_dialog_coverage": sorted(required_dialog_coverage),
        "required_task_scenes": {
            f"task-{safe_scene_name(str(scene.get('name') or f'task-{index:03d}'))}"
            for index, scene in enumerate(tasks, start=1)
            if isinstance(scene, dict)
        },
        "required_task_coverage": sorted(required_task_coverage),
        "required_variants": {
            str(variant.get("name") or variant_slug(variant, index))
            for index, variant in enumerate(variants, start=1)
            if isinstance(variant, dict)
        },
        "required_variant_configs": {
            str(variant.get("name") or variant_slug(variant, index)): variant
            for index, variant in enumerate(variants, start=1)
            if isinstance(variant, dict)
        },
        "required_variant_slugs": {
            str(variant.get("name") or variant_slug(variant, index)): variant_slug(variant, index)
            for index, variant in enumerate(variants, start=1)
            if isinstance(variant, dict)
        },
        "variant_config_errors": variant_config_errors,
        "fixture_config_errors": fixture_config_errors,
        "dialog_config_errors": dialog_config_errors,
        "task_config_errors": task_config_errors,
        "required_workflows": set(required_workflows),
        "required_workflow_details": required_workflow_details,
        "workflow_config_errors": workflow_config_errors,
        "required_layout_assertions": required_layout_assertions,
        "layout_config_errors": layout_config_errors,
    }


def ctest_gate(summary: dict[str, Any]) -> dict[str, Any]:
    ctest = summary.get("ctest", {})
    inventory = summary.get("ctest_inventory_regression", {})
    inventory_selftest = summary.get("ctest_inventory_selftest", {})
    startup = summary.get("freecad_startup_smoke", {})
    if not ctest:
        return check(MISSING, "CTest summary is missing")
    inventory_selftest_evidence = {
        "present": inventory_selftest.get("present", False),
        "result": inventory_selftest.get("result"),
        "scenario_count": inventory_selftest.get("scenario_count"),
        "scenario_names": inventory_selftest.get("scenario_names", []),
        "missing_required_scenarios": sorted(
            REQUIRED_CTEST_INVENTORY_SELFTEST_SCENARIOS
            - set(inventory_selftest.get("scenario_names", []))
        ),
        "failed_scenarios": inventory_selftest.get("failed_scenarios", []),
        "report": inventory_selftest.get("report"),
    }
    if inventory_selftest.get("result") != "ok":
        return check(
            FAIL,
            "CTest disabled/skipped inventory checker self-test failed or is missing",
            {"ctest_inventory_selftest": inventory_selftest_evidence},
        )
    if inventory_selftest_evidence["missing_required_scenarios"]:
        return check(
            FAIL,
            "CTest disabled/skipped inventory checker self-test is missing required scenarios",
            {"ctest_inventory_selftest": inventory_selftest_evidence},
        )
    failed = ctest.get("failed")
    pass_percent = ctest.get("pass_percent")
    startup_ok = (
        startup.get("result") == "ok"
        and startup.get("returncode") == 0
        and bool(startup.get("freecad_version"))
        and startup.get("python_path_contains_venv") is True
    )
    if failed == 0 and pass_percent == 100 and inventory.get("check_result") == "ok" and startup_ok:
        return check(
            PASS,
            "CTest is green, disabled/skipped inventory has no new entries, and venv startup works",
            {
                "run": ctest.get("run"),
                "inventory_total": ctest.get("inventory_total"),
                "not_run_count": len(ctest.get("not_run", [])),
                "approved_not_run_count": inventory.get("approved_not_run_count"),
                "newly_runnable_count": inventory.get("newly_runnable_count"),
                "freecad_version": startup.get("freecad_version"),
                "startup_report": startup.get("report"),
                "ctest_inventory_selftest": inventory_selftest_evidence,
            },
        )
    if failed == 0 and pass_percent == 100 and inventory.get("check_result") == "ok" and not startup_ok:
        return check(
            FAIL,
            "CTest is green but venv startup smoke is missing or failed",
            {
                "startup_present": startup.get("present", False),
                "startup_result": startup.get("result"),
                "startup_returncode": startup.get("returncode"),
                "freecad_version": startup.get("freecad_version"),
                "python_path_contains_venv": startup.get("python_path_contains_venv"),
                "startup_report": startup.get("report"),
                "ctest_inventory_selftest": inventory_selftest_evidence,
            },
        )
    if failed == 0 and pass_percent == 100 and not inventory.get("check_present"):
        return check(
            FAIL,
            "CTest is green but disabled/skipped inventory comparison is missing",
            {
                "run": ctest.get("run"),
                "inventory_total": ctest.get("inventory_total"),
                "not_run_count": len(ctest.get("not_run", [])),
                "expected_check_report": inventory.get("check_report"),
                "ctest_inventory_selftest": inventory_selftest_evidence,
            },
        )
    return check(
        FAIL,
        "CTest is not green or disabled/skipped inventory regressed",
        {
            "failed": failed,
            "pass_percent": pass_percent,
            "inventory_check_result": inventory.get("check_result"),
            "inventory_failure_count": inventory.get("failure_count"),
            "inventory_failures": inventory.get("failures", []),
            "startup_result": startup.get("result"),
            "ctest_inventory_selftest": inventory_selftest_evidence,
        },
    )


def registered_gate(summary: dict[str, Any]) -> dict[str, Any]:
    split = summary.get("freecad_registered_split", {})
    t0 = summary.get("freecad_registered_tests", {})
    classification = summary.get("freecad_registered_issue_classification", {})
    selftest = summary.get("registered_classification_selftest", {})
    harness_selftest = summary.get("registered_harness_selftest", {})
    selftest_evidence = {
        "present": selftest.get("present", False),
        "result": selftest.get("result"),
        "scenario_count": selftest.get("scenario_count"),
        "scenario_names": selftest.get("scenario_names", []),
        "missing_required_scenarios": sorted(
            REQUIRED_REGISTERED_CLASSIFICATION_SELFTEST_SCENARIOS
            - set(selftest.get("scenario_names", []))
        ),
        "failed_scenarios": selftest.get("failed_scenarios", []),
        "report": selftest.get("report"),
    }
    harness_selftest_evidence = {
        "present": harness_selftest.get("present", False),
        "result": harness_selftest.get("result"),
        "scenario_count": harness_selftest.get("scenario_count"),
        "failed_scenarios": harness_selftest.get("failed_scenarios", []),
        "report": harness_selftest.get("report"),
    }
    if harness_selftest.get("result") != "ok":
        return check(
            FAIL,
            "Registered split harness classification self-test failed or is missing",
            {"registered_harness_selftest": harness_selftest_evidence},
        )
    if selftest.get("result") != "ok":
        return check(
            FAIL,
            "Registered issue classification validator self-test failed or is missing",
            {
                "classification_selftest": selftest_evidence,
                "registered_harness_selftest": harness_selftest_evidence,
            },
        )
    if selftest_evidence["missing_required_scenarios"]:
        return check(
            FAIL,
            "Registered issue classification validator self-test is missing required scenarios",
            {
                "classification_selftest": selftest_evidence,
                "registered_harness_selftest": harness_selftest_evidence,
            },
        )
    if t0.get("completed") is True and not t0.get("traceback_count"):
        return check(
            PASS,
            "FreeCAD -t 0 completed without tracebacks",
            {
                "log": t0.get("log"),
                "classification_selftest": selftest_evidence,
                "registered_harness_selftest": harness_selftest_evidence,
            },
        )
    if not split.get("present"):
        return check(FAIL, "FreeCAD -t 0 did not complete and no suite split summary is present")
    if split.get("discovered_suite_count") != split.get("selected_suite_count"):
        return check(
            FAIL,
            "Registered suite split did not run every discovered suite",
            {
                "discovered_suite_count": split.get("discovered_suite_count"),
                "selected_suite_count": split.get("selected_suite_count"),
            },
        )
    issues = split.get("issues", [])
    if issues:
        evidence = {
            "result_counts": split.get("result_counts", {}),
            "issue_count": len(issues),
            "issue_suites": [issue.get("suite") for issue in issues],
            "classification": {
                "present": classification.get("present", False),
                "result": classification.get("result"),
                "classified_issue_count": classification.get("classified_issue_count"),
                "unclassified_issue_count": classification.get("unclassified_issue_count"),
                "hard_blocker_count": classification.get("hard_blocker_count"),
                "errors": classification.get("errors", []),
            },
            "classification_selftest": selftest_evidence,
            "registered_harness_selftest": harness_selftest_evidence,
        }
        if classification.get("result") == "ok" and classification.get("unclassified_issue_count") == 0:
            message = "Registered suites are actionable and classified, but not green"
        elif classification.get("present"):
            message = "Registered suites have unclassified or stale issue classifications"
        else:
            message = "Registered suites are actionable but not green and no classification report is present"
        return check(
            FAIL,
            message,
            evidence,
        )
    return check(
        PASS,
        "Every registered suite passed independently",
        {
            "result_counts": split.get("result_counts", {}),
            "classification_selftest": selftest_evidence,
            "registered_harness_selftest": harness_selftest_evidence,
        },
    )


def visual_gate(summary: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    integrity = summary.get("gui_screenshot_integrity", {})
    integrity_selftest = summary.get("gui_screenshot_integrity_selftest", {})
    harness_selftest = summary.get("visual_baseline_harness_selftest", {})
    integrity_evidence = {
        "present": integrity.get("present", False),
        "result": integrity.get("result"),
        "capture_count": integrity.get("capture_count"),
        "scene_count": integrity.get("scene_count"),
        "failure_count": integrity.get("failure_count"),
        "thresholds": integrity.get("thresholds", {}),
        "failures": integrity.get("failures", []),
        "report": integrity.get("report"),
    }
    integrity_selftest_evidence = {
        "present": integrity_selftest.get("present", False),
        "result": integrity_selftest.get("result"),
        "scenario_count": integrity_selftest.get("scenario_count"),
        "scenario_names": integrity_selftest.get("scenario_names", []),
        "missing_required_scenarios": sorted(
            REQUIRED_SCREENSHOT_INTEGRITY_SELFTEST_SCENARIOS
            - set(integrity_selftest.get("scenario_names", []))
        ),
        "failed_scenarios": integrity_selftest.get("failed_scenarios", []),
        "report": integrity_selftest.get("report"),
    }
    harness_selftest_evidence = {
        "present": harness_selftest.get("present", False),
        "result": harness_selftest.get("result"),
        "scenario_count": harness_selftest.get("scenario_count"),
        "scenario_names": harness_selftest.get("scenario_names", []),
        "missing_required_scenarios": sorted(
            REQUIRED_VISUAL_BASELINE_HARNESS_SELFTEST_SCENARIOS
            - set(harness_selftest.get("scenario_names", []))
        ),
        "failed_scenarios": harness_selftest.get("failed_scenarios", []),
        "report": harness_selftest.get("report"),
    }
    workbench_capture = summary.get("gui_visual_venv", {})
    discovered_workbenches = {
        str(name)
        for name in workbench_capture.get("discovered_workbenches", [])
        if name
    }
    captured_workbenches = {
        str(name)
        for name in workbench_capture.get("captured_workbenches", [])
        if name
    }
    discovered_workbench_scenes = {f"workbench-{name}" for name in discovered_workbenches}
    required_scenes = {
        "gui_visual_venv": coverage["required_workbench_scenes"] | discovered_workbench_scenes,
        "gui_visual_fixtures": coverage["required_fixture_scenes"],
        "gui_visual_dialogs": coverage["required_dialog_scenes"],
        "gui_visual_dialogs_native": coverage["required_dialog_scenes"],
        "gui_visual_tasks": coverage["required_task_scenes"],
    }
    failures = []
    evidence: dict[str, Any] = {
        "screenshot_integrity": integrity_evidence,
        "screenshot_integrity_selftest": integrity_selftest_evidence,
        "visual_baseline_harness_selftest": harness_selftest_evidence,
        "coverage_config_errors": coverage.get("coverage_config_errors", []),
        "workbench_config_errors": coverage.get("workbench_config_errors", []),
        "fixture_config_errors": coverage.get("fixture_config_errors", []),
        "dialog_config_errors": coverage.get("dialog_config_errors", []),
        "task_config_errors": coverage.get("task_config_errors", []),
        "required_fixture_coverage": coverage.get("required_fixture_coverage", []),
        "required_dialog_coverage": coverage.get("required_dialog_coverage", []),
        "required_task_coverage": coverage.get("required_task_coverage", []),
    }
    for error_key in (
        "coverage_config_errors",
        "workbench_config_errors",
        "fixture_config_errors",
        "dialog_config_errors",
        "task_config_errors",
    ):
        if coverage.get(error_key):
            failures.append(error_key)
    if integrity_selftest.get("result") != "ok":
        failures.append("screenshot_integrity_selftest")
    if integrity_selftest_evidence["missing_required_scenarios"]:
        failures.append("screenshot_integrity_selftest_missing_required_scenarios")
    if harness_selftest.get("result") != "ok":
        failures.append("visual_baseline_harness_selftest")
    if harness_selftest_evidence["missing_required_scenarios"]:
        failures.append("visual_baseline_harness_selftest_missing_required_scenarios")
    if integrity.get("result") != "ok":
        failures.append("screenshot_integrity")
    for key, required in required_scenes.items():
        item = summary.get(key, {})
        scene_names = {
            scene.get("scene")
            for scene in item.get("scenes", [])
            if isinstance(scene, dict) and scene.get("scene")
        }
        missing_scenes = sorted(required - scene_names)
        evidence[key] = {
            "result": item.get("result"),
            "scene_count": item.get("scene_count"),
            "failed_scene_count": item.get("failed_scene_count"),
            "traceback_count": item.get("traceback_count"),
            "required_scene_count": len(required),
            "missing_required_scenes": missing_scenes,
            "failed_scenes": item.get("failed_scenes", []),
        }
        if key in {"gui_visual_dialogs", "gui_visual_dialogs_native", "gui_visual_tasks"}:
            cleanup_missing = sorted(
                scene.get("scene")
                for scene in item.get("scenes", [])
                if isinstance(scene, dict) and scene.get("scene") and not scene.get("cleanup")
            )
            cleanup_failed = sorted(
                scene.get("scene")
                for scene in item.get("scenes", [])
                if isinstance(scene, dict)
                and scene.get("scene")
                and isinstance(scene.get("cleanup"), dict)
                and scene["cleanup"].get("result") != "ok"
            )
            evidence[key]["cleanup_missing"] = cleanup_missing
            evidence[key]["cleanup_failed"] = cleanup_failed
            if cleanup_missing:
                failures.append(f"{key}:missing_cleanup_evidence")
            if cleanup_failed:
                failures.append(f"{key}:cleanup_failed")
        if key in {"gui_visual_dialogs", "gui_visual_dialogs_native"}:
            required_return_checks = coverage.get("required_dialog_return_checks", {})
            scenes_by_name = {
                str(scene.get("scene")): scene
                for scene in item.get("scenes", [])
                if isinstance(scene, dict) and scene.get("scene")
            }
            return_check_failures = []
            return_check_evidence = {}
            for scene_name, expected_opened in sorted(required_return_checks.items()):
                scene = scenes_by_name.get(scene_name)
                return_check = scene.get("return_check") if isinstance(scene, dict) else None
                return_check_evidence[scene_name] = return_check
                if not isinstance(return_check, dict):
                    return_check_failures.append(f"missing_return_check:{scene_name}")
                    continue
                if return_check.get("kind") != "opened_file":
                    return_check_failures.append(f"wrong_return_check_kind:{scene_name}")
                if return_check.get("opened") != expected_opened:
                    return_check_failures.append(
                        f"wrong_opened_file:{scene_name}:{return_check.get('opened')}:{expected_opened}"
                    )
            evidence[key]["required_return_checks"] = required_return_checks
            evidence[key]["return_check_evidence"] = return_check_evidence
            evidence[key]["return_check_failures"] = return_check_failures
            if return_check_failures:
                failures.append(f"{key}:return_check_failed")
        if key == "gui_visual_venv":
            missing_discovered_workbenches = sorted(discovered_workbenches - captured_workbenches)
            evidence[key].update(
                {
                    "discovered_workbench_count": len(discovered_workbenches),
                    "captured_workbench_count": len(captured_workbenches),
                    "missing_discovered_workbenches": missing_discovered_workbenches,
                }
            )
            if item.get("result") == "ok" and not discovered_workbenches:
                failures.append(f"{key}:missing_discovered_workbench_inventory")
            if missing_discovered_workbenches:
                failures.append(f"{key}:missing_discovered_workbenches")
        if item.get("result") != "ok" or (item.get("scene_count") or 0) < len(required):
            failures.append(key)
        if item.get("failed_scene_count"):
            failures.append(f"{key}:failed_scenes")
        if missing_scenes:
            failures.append(f"{key}:missing_required_scenes")
    if failures:
        return check(
            FAIL,
            "Required visual capture sets are missing, failed, or lack required workflow scenes",
            {"coverage_config": coverage["config"], "failures": failures, **evidence},
        )
    return check(
        PASS,
        "Required visual workbench, fixture, dialog, and task workflow captures are present",
        {"coverage_config": coverage["config"], **evidence},
    )


def layout_assertion_gate(summary: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    smoke = summary.get("layout_assertion_smoke", {})
    selftest = summary.get("layout_assertion_coverage_selftest", {})
    selftest_evidence = {
        "present": selftest.get("present", False),
        "result": selftest.get("result"),
        "scenario_count": selftest.get("scenario_count"),
        "scenario_names": selftest.get("scenario_names", []),
        "missing_required_scenarios": sorted(
            REQUIRED_MANUAL_SMOKE_SELFTEST_SCENARIOS
            - set(selftest.get("scenario_names", []))
        ),
        "failed_scenarios": selftest.get("failed_scenarios", []),
        "report": selftest.get("report"),
        "expected_build": selftest.get("expected_build", {}),
        "expected_run": selftest.get("expected_run", {}),
    }
    if selftest.get("result") != "ok":
        return check(
            FAIL,
            "Layout assertion coverage self-test failed or is missing",
            {"layout_assertion_coverage_selftest": selftest_evidence},
        )
    if coverage.get("layout_config_errors"):
        return check(
            FAIL,
            "Layout assertion coverage config is invalid",
            {
                "report": smoke.get("report"),
                "coverage_config": coverage["config"],
                "layout_assertion_coverage_selftest": selftest_evidence,
                "layout_config_errors": coverage.get("layout_config_errors", []),
            },
        )
    required = coverage["required_layout_assertions"]
    if not smoke.get("present"):
        return check(MISSING, "Layout assertion runtime smoke report is missing", {"expected": smoke.get("report")})
    observed = {
        name
        for name, ok in (smoke.get("observed") or {}).items()
        if ok
    }
    examples = smoke.get("examples") or {}
    missing_examples = []
    for name in sorted(required & observed):
        findings = examples.get(name)
        if not isinstance(findings, list) or not any(
            isinstance(finding, dict) and finding.get("kind") == name
            for finding in findings
        ):
            missing_examples.append(name)
    missing = sorted(required - observed)
    smoke_failed = smoke.get("result") != "ok"
    if smoke_failed:
        missing = sorted(set(missing) | set(smoke.get("missing", [])))
    if smoke_failed or missing or missing_examples:
        return check(
            FAIL,
            "Required layout assertion kinds failed runtime smoke coverage",
            {
                "report": smoke.get("report"),
                "smoke_result": smoke.get("result"),
                "coverage_config": coverage["config"],
                "layout_assertion_coverage_selftest": selftest_evidence,
                "required": sorted(required),
                "missing": missing,
                "missing_examples": missing_examples,
                "observed": sorted(observed),
                "process_returncode": smoke.get("process_returncode"),
            },
        )
    return check(
        PASS,
        "Required layout assertion kinds are exercised by runtime smoke coverage",
        {
            "report": smoke.get("report"),
            "coverage_config": coverage["config"],
            "layout_assertion_coverage_selftest": selftest_evidence,
            "required": sorted(required),
            "observed": sorted(observed & required),
            "example_count": {
                name: len((examples.get(name) or []))
                for name in sorted(required)
            },
        },
    )


def matrix_gate(summary: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    matrix = summary.get("gui_visual_matrix", {})
    workbench_capture = summary.get("gui_visual_venv", {})
    discovered_workbench_scenes = {
        f"workbench-{name}"
        for name in workbench_capture.get("discovered_workbenches", [])
        if name
    }
    variants = {
        variant.get("name"): variant
        for variant in matrix.get("variants", [])
        if variant.get("name")
    }
    required_variants = coverage["required_variants"]
    required_scene_suffixes = (
        coverage["required_fixture_scenes"]
        | coverage["required_workbench_scenes"]
        | coverage["required_dialog_scenes"]
        | coverage["required_task_scenes"]
        | discovered_workbench_scenes
    )
    scene_names = {
        scene.get("scene")
        for scene in matrix.get("scenes", [])
        if isinstance(scene, dict) and scene.get("scene")
    }
    missing_variants = sorted(required_variants - set(variants))
    missing_by_variant: dict[str, list[str]] = {}
    for variant_name in sorted(required_variants & set(variants)):
        slug = variants[variant_name].get("slug") or coverage["required_variant_slugs"].get(
            variant_name, variant_name
        )
        prefix = f"variant-{slug}-"
        missing_scenes = sorted(
            suffix for suffix in required_scene_suffixes if f"{prefix}{suffix}" not in scene_names
        )
        if missing_scenes:
            missing_by_variant[variant_name] = missing_scenes
    variant_identity_mismatches = []
    for variant_name in sorted(required_variants & set(variants)):
        expected = coverage.get("required_variant_configs", {}).get(variant_name, {})
        actual = variants[variant_name].get("config")
        expected_identity = {
            key: expected.get(key)
            for key in ("preference_pack", "font_scale", "env")
            if key in expected
        }
        if not expected_identity:
            continue
        if not isinstance(actual, dict):
            variant_identity_mismatches.append(
                {
                    "variant": variant_name,
                    "field": "config",
                    "expected": expected_identity,
                    "actual": None,
                }
            )
            continue
        for field, expected_value in expected_identity.items():
            if actual.get(field) != expected_value:
                variant_identity_mismatches.append(
                    {
                        "variant": variant_name,
                        "field": field,
                        "expected": expected_value,
                        "actual": actual.get(field),
                    }
                )

    failures = []
    if coverage.get("coverage_config_errors"):
        failures.append("coverage_config_errors")
    if coverage.get("workbench_config_errors"):
        failures.append("workbench_config_errors")
    if coverage.get("variant_config_errors"):
        failures.append("variant_config_errors")
    if coverage.get("fixture_config_errors"):
        failures.append("fixture_config_errors")
    if matrix.get("result") != "ok":
        failures.append("matrix_result")
    if workbench_capture.get("result") == "ok" and not discovered_workbench_scenes:
        failures.append("missing_discovered_workbench_inventory")
    if missing_variants:
        failures.append("missing_variants")
    if missing_by_variant:
        failures.append("missing_required_variant_scenes")
    if variant_identity_mismatches:
        failures.append("variant_identity_mismatch")
    if matrix.get("failed_scene_count"):
        failures.append("failed_scenes")

    if failures:
        return check(
            FAIL,
            "Theme/DPI/font matrix is incomplete, failed, or lacks required scenes per variant",
            {
                "result": matrix.get("result"),
                "coverage_config": coverage["config"],
                "variant_count": matrix.get("variant_count"),
                "coverage_config_errors": coverage.get("coverage_config_errors", []),
                "workbench_config_errors": coverage.get("workbench_config_errors", []),
                "variant_config_errors": coverage.get("variant_config_errors", []),
                "fixture_config_errors": coverage.get("fixture_config_errors", []),
                "missing_variants": missing_variants,
                "missing_required_scenes_by_variant": missing_by_variant,
                "variant_identity_mismatches": variant_identity_mismatches,
                "failed_scene_count": matrix.get("failed_scene_count"),
                "failures": failures,
            },
        )
    return check(
        PASS,
        "Theme/DPI/font matrix covers required variants and required scenes per variant",
        {
            "coverage_config": coverage["config"],
            "scene_count": matrix.get("scene_count"),
            "variants": sorted(variants),
            "variant_identities": {
                name: variants[name].get("config")
                for name in sorted(variants)
            },
            "required_scene_suffix_count": len(required_scene_suffixes),
        },
    )


def regression_gate(summary: dict[str, Any], results_dir: Path) -> dict[str, Any]:
    keys = [
        "gui_visual_regression",
        "gui_visual_fixtures_regression",
        "gui_visual_dialogs_regression",
        "gui_visual_tasks_regression",
        "gui_visual_matrix_regression",
    ]
    failures = []
    evidence = {}
    for key in keys:
        item = summary.get(key, {})
        policy = item.get("policy") or {}
        approval = item.get("approval") or {}
        max_changed_ratio = policy.get("max_changed_ratio")
        max_rms = policy.get("max_rms")
        max_changed_ratio_value = safe_float(max_changed_ratio)
        max_rms_value = safe_float(max_rms)
        evidence[key] = {
            "check_result": item.get("check_result"),
            "failure_count": item.get("failure_count"),
            "failure_kind_counts": item.get("failure_kind_counts", {}),
            "check_report": item.get("check_report"),
            "check_manifest": item.get("check_manifest"),
            "check_capture_dir": item.get("check_capture_dir"),
            "current_capture_scene_count": item.get("current_capture_scene_count"),
            "check_diff_dir": item.get("check_diff_dir"),
            "check_policy": item.get("check_policy"),
            "check_approval_command": item.get("check_approval_command"),
            "check_review_index": item.get("check_review_index"),
            "approval": approval,
            "approved_scene_count": item.get("approved_scene_count"),
            "format": item.get("format"),
            "absolute_screenshot_count": item.get("absolute_screenshot_count"),
            "portable_screenshot_count": item.get("portable_screenshot_count"),
            "missing_context_fingerprint_count": item.get("missing_context_fingerprint_count"),
            "missing_context_identity_count": item.get("missing_context_identity_count"),
            "max_changed_ratio": max_changed_ratio,
            "max_rms": max_rms,
            "new_findings_fail": policy.get("new_findings_fail"),
            "baseline_images_are_portable": policy.get("baseline_images_are_portable"),
        }
        review_index = item.get("check_review_index") or {}
        if not isinstance(review_index, dict):
            failures.append(f"{key}:invalid_review_index")
            review_index = {}
        for index_kind in ("json", "html"):
            review_path = review_index.get(index_kind)
            if not referenced_path_exists(review_path, results_dir):
                failures.append(f"{key}:missing_review_index_file:{index_kind}")
        if item.get("check_result") != "ok" or item.get("failure_count") not in (0, None):
            failures.append(key)
        for field in (
            "check_manifest",
            "check_capture_dir",
            "check_diff_dir",
            "check_policy",
            "check_approval_command",
            "check_review_index",
            "current_capture_scene_count",
        ):
            if not item.get(field):
                failures.append(f"{key}:missing_review_metadata:{field}")
        if item.get("absolute_screenshot_count"):
            failures.append(f"{key}:nonportable_manifest")
        approved_scene_count = item.get("approved_scene_count")
        current_capture_scene_count = item.get("current_capture_scene_count")
        if (
            approved_scene_count is not None
            and current_capture_scene_count is not None
            and approved_scene_count != current_capture_scene_count
        ):
            failures.append(f"{key}:approved_scene_count_mismatch")
        if item.get("missing_context_fingerprint_count"):
            failures.append(f"{key}:missing_scene_context_fingerprints")
        if item.get("missing_context_identity_count"):
            failures.append(f"{key}:missing_scene_context_identities")
        if item.get("manifest_present") and item.get("format") != 2:
            failures.append(f"{key}:old_manifest_format")
        if item.get("manifest_present"):
            for field in ("reviewer", "note", "approved_utc", "source_capture_dir"):
                value = str(approval.get(field, "")).strip()
                if not value or value.lower() in {"todo", "tbd", "n/a", "none", "placeholder"}:
                    failures.append(f"{key}:missing_approval_metadata:{field}")
            if max_changed_ratio_value is None or max_changed_ratio_value > 0.03:
                failures.append(f"{key}:changed_ratio_threshold_too_lax")
            if max_rms_value is None or max_rms_value > 8.0:
                failures.append(f"{key}:rms_threshold_too_lax")
            if policy.get("new_findings_fail") is not True:
                failures.append(f"{key}:new_findings_not_failing")
            if policy.get("baseline_images_are_portable") is not True:
                failures.append(f"{key}:baseline_images_not_portable")
    selftest = summary.get("gui_visual_regression_selftest", {})
    evidence["gui_visual_regression_selftest"] = {
        "present": selftest.get("present", False),
        "result": selftest.get("result"),
        "scenario_count": selftest.get("scenario_count"),
        "scenario_names": selftest.get("scenario_names", []),
        "failed_scenarios": selftest.get("failed_scenarios", []),
        "manifest_format": selftest.get("manifest_format"),
        "manifest_absolute_screenshot_count": selftest.get("manifest_absolute_screenshot_count"),
        "manifest_missing_context_fingerprint_count": selftest.get(
            "manifest_missing_context_fingerprint_count"
        ),
        "manifest_missing_context_identity_count": selftest.get(
            "manifest_missing_context_identity_count"
        ),
        "manifest_has_approval_metadata": selftest.get("manifest_has_approval_metadata"),
    }
    if selftest.get("result") != "ok":
        failures.append("gui_visual_regression_selftest")
    scenario_names = {
        str(name)
        for name in selftest.get("scenario_names", [])
        if str(name).strip()
    }
    missing_selftest_scenarios = sorted(
        REQUIRED_VISUAL_REGRESSION_SELFTEST_SCENARIOS - scenario_names
    )
    evidence["gui_visual_regression_selftest"]["missing_required_scenarios"] = (
        missing_selftest_scenarios
    )
    if missing_selftest_scenarios:
        failures.append("gui_visual_regression_selftest:missing_required_scenarios")
    if selftest.get("manifest_format") != 2 or selftest.get("manifest_absolute_screenshot_count") not in (0, None):
        failures.append("gui_visual_regression_selftest:portable_manifest")
    if selftest.get("manifest_missing_context_fingerprint_count") not in (0, None):
        failures.append("gui_visual_regression_selftest:scene_context_fingerprints")
    if selftest.get("manifest_missing_context_identity_count") not in (0, None):
        failures.append("gui_visual_regression_selftest:scene_context_identities")
    if selftest.get("manifest_has_approval_metadata") is not True:
        failures.append("gui_visual_regression_selftest:approval_metadata")
    if failures:
        return check(FAIL, "Visual regression checks are failing or missing", {"failures": failures, **evidence})
    return check(PASS, "Visual regression manifests and checks are present", evidence)


def read_workflow_events(events_path: Any) -> tuple[list[dict[str, Any]], list[str]]:
    errors = []
    events = []
    if not events_path:
        return events, ["missing_workflow_events_path"]
    path = Path(str(events_path))
    if not path.exists():
        return events, [f"missing_workflow_events_file:{path}"]
    if not path.is_file():
        return events, [f"workflow_events_path_not_file:{path}"]
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return events, [f"workflow_events_unreadable:{path}:{exc}"]
    for line_number, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"invalid_workflow_event_json:{line_number}")
            continue
        if not isinstance(event, dict):
            errors.append(f"workflow_event_must_be_object:{line_number}")
            continue
        events.append(event)
    return events, errors


def workflow_detail_matches(event: dict[str, Any], spec: dict[str, Any]) -> bool:
    status = spec.get("status")
    if status and event.get("status") != status:
        return False
    for field in spec.get("fields_present", []):
        if field not in event or event.get(field) in (None, ""):
            return False
    for field, expected in (spec.get("fields_equal") or {}).items():
        if event.get(field) != expected:
            return False
    return True


def workflow_event_windows(events: list[dict[str, Any]], workflows: set[str]) -> dict[str, dict[str, Any]]:
    windows: dict[str, dict[str, Any]] = {
        workflow: {"start": None, "pass": None, "starts": [], "passes": []}
        for workflow in workflows
    }
    for index, event in enumerate(events):
        workflow = event.get("workflow")
        if workflow not in windows:
            continue
        if event.get("status") == "workflow_started":
            windows[workflow]["starts"].append(index)
            if windows[workflow]["start"] is None:
                windows[workflow]["start"] = index
        if event.get("status") == "workflow_pass":
            windows[workflow]["passes"].append(index)
            if windows[workflow]["pass"] is None:
                windows[workflow]["pass"] = index
    return windows


def event_in_workflow_window(index: int, window: dict[str, Any]) -> bool:
    start = window.get("start")
    passed = window.get("pass")
    if start is None or passed is None:
        return False
    return start < index < passed


def gui_exercise_gate(summary: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    exercise = summary.get("gui_exercise_venv", {})
    workflows = summary.get("gui_workflows_venv", {})
    tasks = summary.get("gui_visual_tasks", {})
    workflow_selftest = summary.get("gui_workflow_coverage_selftest", {})
    workflow_selftest_evidence = {
        "present": workflow_selftest.get("present", False),
        "result": workflow_selftest.get("result"),
        "scenario_count": workflow_selftest.get("scenario_count"),
        "scenario_names": workflow_selftest.get("scenario_names", []),
        "missing_required_scenarios": sorted(
            REQUIRED_WORKFLOW_COVERAGE_SELFTEST_SCENARIOS
            - set(workflow_selftest.get("scenario_names", []))
        ),
        "failed_scenarios": workflow_selftest.get("failed_scenarios", []),
        "report": workflow_selftest.get("report"),
    }
    if workflow_selftest.get("result") != "ok":
        return check(
            FAIL,
            "GUI workflow coverage self-test failed or is missing",
            {"gui_workflow_coverage_selftest": workflow_selftest_evidence},
        )
    if workflow_selftest_evidence["missing_required_scenarios"]:
        return check(
            FAIL,
            "GUI workflow coverage self-test is missing required scenarios",
            {"gui_workflow_coverage_selftest": workflow_selftest_evidence},
        )
    if coverage.get("workflow_config_errors"):
        return check(
            FAIL,
            "GUI workflow coverage config is invalid",
            {
                "coverage_config": coverage["config"],
                "workflow_config_errors": coverage.get("workflow_config_errors", []),
                "gui_workflow_coverage_selftest": workflow_selftest_evidence,
            },
        )
    if exercise.get("result") != "ok":
        return check(FAIL, "Conservative GUI exercise failed or is missing", {"result": exercise.get("result")})
    required_workflows = coverage["required_workflows"]
    required_workflow_details = coverage.get("required_workflow_details", {})
    passed_workflows = set()
    started_workflows = set()
    failed_workflows = set()
    events, event_errors = read_workflow_events(workflows.get("events_path"))
    for event in events:
        if event.get("status") == "workflow_started":
            started_workflows.add(event.get("workflow"))
        if event.get("status") == "workflow_pass":
            passed_workflows.add(event.get("workflow"))
        if event.get("status") == "workflow_fail":
            failed_workflows.add(event.get("workflow"))
    missing_workflows = sorted(required_workflows - passed_workflows)
    missing_started_workflows = sorted(required_workflows - started_workflows)
    workflow_windows = workflow_event_windows(events, required_workflows)
    order_failures = [
        {
            "workflow": workflow,
            "start_index": window.get("start"),
            "pass_index": window.get("pass"),
        }
        for workflow, window in sorted(workflow_windows.items())
        if window.get("start") is None
        or window.get("pass") is None
        or int(window.get("start") or 0) >= int(window.get("pass") or -1)
    ]
    duplicate_event_failures = [
        {
            "workflow": workflow,
            "start_indices": window.get("starts", []),
            "pass_indices": window.get("passes", []),
        }
        for workflow, window in sorted(workflow_windows.items())
        if len(window.get("starts", [])) > 1 or len(window.get("passes", [])) > 1
    ]
    detail_failures = []
    for workflow in sorted(required_workflows):
        window = workflow_windows.get(workflow, {})
        workflow_events = [
            event
            for index, event in enumerate(events)
            if event_in_workflow_window(index, window)
            and (
                event.get("workflow") == workflow
                or (workflow == "switch_workbench" and event.get("status") == "workbench_active")
            )
        ]
        for index, detail_spec in enumerate(required_workflow_details.get(workflow, [])):
            if not any(workflow_detail_matches(event, detail_spec) for event in workflow_events):
                detail_failures.append(
                    {
                        "workflow": workflow,
                        "detail_index": index,
                        "expected": detail_spec,
                    }
                )
    if (
        workflows.get("result") != "ok"
        or missing_workflows
        or missing_started_workflows
        or failed_workflows
        or event_errors
        or order_failures
        or duplicate_event_failures
        or detail_failures
    ):
        return check(
            FAIL,
            "Stateful GUI workflows failed or are missing",
            {
                "exercise_result": exercise.get("result"),
                "coverage_config": coverage["config"],
                "workflow_result": workflows.get("result"),
                "events_path": workflows.get("events_path"),
                "event_errors": event_errors,
                "missing_workflows": missing_workflows,
                "missing_started_workflows": missing_started_workflows,
                "failed_workflows": sorted(failed_workflows),
                "passed_workflows": sorted(passed_workflows),
                "started_workflows": sorted(started_workflows),
                "order_failures": order_failures,
                "duplicate_event_failures": duplicate_event_failures,
                "detail_failures": detail_failures,
                "gui_workflow_coverage_selftest": workflow_selftest_evidence,
            },
        )
    return check(
        PASS,
        "Conservative GUI exercise and required stateful workflows passed",
        {
            "exercise_result": exercise.get("result"),
            "coverage_config": coverage["config"],
            "workflow_result": workflows.get("result"),
            "events_path": workflows.get("events_path"),
            "passed_workflows": sorted(passed_workflows),
            "started_workflows": sorted(started_workflows),
            "workflow_windows": workflow_windows,
            "required_workflows": sorted(required_workflows),
            "required_workflow_details": required_workflow_details,
            "task_scene_count": tasks.get("scene_count"),
            "gui_workflow_coverage_selftest": workflow_selftest_evidence,
        },
    )


def crash_gate(summary: dict[str, Any]) -> dict[str, Any]:
    def failure_kind(result: str | None, returncode: Any, traceback_count: Any) -> str:
        if result == "crash":
            return "crash"
        if result == "timeout":
            return "timeout"
        if result == "traceback" or (traceback_count or 0) > 0:
            return "python_traceback"
        if result == "ok_with_process_errors":
            return "process_errors"
        if result == "process_failed" or returncode not in (0, None):
            return "nonzero_process_exit"
        return "process_failure"

    def signal_excerpt(log_path: str | None) -> list[str]:
        if not log_path:
            return []
        text = read_text(Path(log_path))
        if not text:
            return []
        interesting = []
        patterns = (
            "Program received signal",
            "SIGSEGV",
            "Segmentation fault",
            "Traceback (most recent call last):",
            "Process returned",
            "Exception",
            "Error",
        )
        for line in text.splitlines():
            if any(pattern in line for pattern in patterns):
                interesting.append(line.strip())
            if len(interesting) >= 5:
                break
        return interesting

    hard_failures = []
    split = summary.get("freecad_registered_split", {})
    classification = summary.get("freecad_registered_issue_classification", {})
    classified_by_suite = {
        issue.get("suite"): issue
        for issue in classification.get("classified_issues", [])
        if isinstance(issue, dict) and issue.get("suite")
    }
    for issue in split.get("issues", []):
        if issue.get("result") in {"crash", "process_failed", "timeout", "traceback", "ok_with_process_errors"}:
            result = issue.get("result")
            returncode = issue.get("returncode")
            traceback_count = issue.get("traceback_count")
            log = issue.get("log")
            classified_issue = classified_by_suite.get(issue.get("suite"), {})
            hard_failures.append(
                {
                    "suite": issue.get("suite"),
                    "result": result,
                    "failure_kind": failure_kind(result, returncode, traceback_count),
                    "returncode": returncode,
                    "traceback_count": traceback_count,
                    "log": log,
                    "signal_excerpt": signal_excerpt(log),
                    "classification": {
                        "present": bool(classified_issue),
                        "result": classified_issue.get("result"),
                        "reason": classified_issue.get("reason"),
                        "hard_blocker": classified_issue.get("hard_blocker"),
                        "hard_blocker_required_by_result": classified_issue.get(
                            "hard_blocker_required_by_result"
                        ),
                    },
                }
            )
    for key in ["gui_visual_venv", "gui_visual_fixtures", "gui_visual_dialogs", "gui_visual_tasks", "gui_visual_matrix"]:
        item = summary.get(key, {})
        if item.get("result") != "ok" or (item.get("unallowed_traceback_count") or 0) > 0:
            result = item.get("result")
            returncode = item.get("process_returncode")
            traceback_count = item.get("traceback_count")
            log = item.get("process_log")
            hard_failures.append(
                {
                    "suite": key,
                    "result": result,
                    "failure_kind": failure_kind(result, returncode, traceback_count),
                    "returncode": returncode,
                    "traceback_count": traceback_count,
                    "unallowed_traceback_count": item.get("unallowed_traceback_count"),
                    "log": log,
                    "signal_excerpt": signal_excerpt(log),
                }
            )
    if hard_failures:
        return check(
            FAIL,
            "Crash/process/traceback gate has hard failures",
            {
                "failures": hard_failures,
                "classification": {
                    "present": classification.get("present", False),
                    "result": classification.get("result"),
                    "classified_issue_count": classification.get("classified_issue_count"),
                    "hard_blocker_count": classification.get("hard_blocker_count"),
                    "unclassified_issue_count": classification.get("unclassified_issue_count"),
                },
            },
        )
    return check(PASS, "No crash/process/traceback hard failures are present in collected GUI artifacts")


def manual_smoke_gate(summary: dict[str, Any], results_dir: Path) -> dict[str, Any]:
    selftest = summary.get("manual_smoke_selftest", {})
    selftest_evidence = {
        "present": selftest.get("present", False),
        "result": selftest.get("result"),
        "scenario_count": selftest.get("scenario_count"),
        "scenario_names": selftest.get("scenario_names", []),
        "missing_required_scenarios": sorted(
            REQUIRED_MANUAL_SMOKE_SELFTEST_SCENARIOS
            - set(selftest.get("scenario_names", []))
        ),
        "failed_scenarios": selftest.get("failed_scenarios", []),
        "report": selftest.get("report"),
        "expected_build": selftest.get("expected_build", {}),
        "expected_run": selftest.get("expected_run", {}),
    }
    if selftest.get("result") != "ok":
        return check(
            FAIL,
            "Manual smoke validator self-test failed or is missing",
            {"manual_smoke_selftest": selftest_evidence},
        )
    if selftest_evidence["missing_required_scenarios"]:
        return check(
            FAIL,
            "Manual smoke validator self-test is missing required scenarios",
            {"manual_smoke_selftest": selftest_evidence},
        )
    path = results_dir / "manual-smoke.json"
    smoke = read_json(path)
    if smoke is None:
        return check(
            MISSING,
            "Manual smoke artifact is missing",
            {"expected": str(path), "manual_smoke_selftest": selftest_evidence},
        )
    expected_build = manual_smoke.expected_build_from_summary(summary)
    expected_run = manual_smoke.expected_run_from_summary(summary)
    ok, errors = manual_smoke.validate(
        smoke,
        expected_build=expected_build,
        expected_run=expected_run,
    )
    if not ok:
        return check(
            FAIL,
            "Manual smoke artifact is incomplete, stale, or invalid",
            {
                "errors": errors,
                "path": str(path),
                "expected_build": expected_build,
                "expected_run": expected_run,
                "manual_smoke_selftest": selftest_evidence,
            },
        )
    return check(
        PASS,
        "Manual smoke pass is recorded for the current baseline build",
        {
            "path": str(path),
            "tester": smoke.get("tester"),
            "expected_build": expected_build,
            "expected_run": expected_run,
            "manual_smoke_selftest": selftest_evidence,
        },
    )


def dependency_gate(summary: dict[str, Any]) -> dict[str, Any]:
    smoke = summary.get("dependency_smoke", {})
    selftest = summary.get("dependency_smoke_selftest", {})
    selftest_evidence = {
        "present": selftest.get("present", False),
        "result": selftest.get("result"),
        "scenario_count": selftest.get("scenario_count"),
        "scenario_names": selftest.get("scenario_names", []),
        "missing_required_scenarios": sorted(
            REQUIRED_DEPENDENCY_SMOKE_SELFTEST_SCENARIOS
            - set(selftest.get("scenario_names", []))
        ),
        "failed_scenarios": selftest.get("failed_scenarios", []),
        "report": selftest.get("report"),
    }
    if selftest.get("result") != "ok":
        return check(
            FAIL,
            "Optional dependency smoke self-test failed or is missing",
            {"dependency_smoke_selftest": selftest_evidence},
        )
    if selftest_evidence["missing_required_scenarios"]:
        return check(
            FAIL,
            "Optional dependency smoke self-test is missing required scenarios",
            {"dependency_smoke_selftest": selftest_evidence},
        )
    if not smoke.get("present"):
        return check(MISSING, "Optional dependency smoke report is missing", {"expected": smoke.get("report")})
    if smoke.get("config_errors"):
        return check(
            FAIL,
            "Optional dependency smoke config is invalid",
            {
                "report": smoke.get("report"),
                "config": smoke.get("config"),
                "dependency_smoke_selftest": selftest_evidence,
                "config_errors": smoke.get("config_errors", []),
            },
        )
    missing = smoke.get("missing", {})
    if missing:
        return check(
            PARTIAL,
            "Optional dependency gaps are recorded but still reduce coverage",
            {
                "report": smoke.get("report"),
                "config": smoke.get("config"),
                "dependency_smoke_selftest": selftest_evidence,
                "missing_count": smoke.get("missing_count"),
                "missing": {
                    name: {
                        "reason": item.get("reason"),
                        "affects": item.get("affects", []),
                    }
                    for name, item in missing.items()
                },
            },
        )
    return check(
        PASS,
        "No optional dependency gaps are recorded",
        {
            "report": smoke.get("report"),
            "config": smoke.get("config"),
            "dependency_smoke_selftest": selftest_evidence,
        },
    )


def test_infrastructure_gate(summary: dict[str, Any]) -> dict[str, Any]:
    runner_selftest = summary.get("run_ui_test_baseline_selftest", {})
    provenance_selftest = summary.get("artifact_provenance_selftest", {})
    json_integrity = summary.get("json_artifact_integrity", {})
    json_integrity_selftest = summary.get("json_artifact_integrity_selftest", {})
    provenance = summary.get("artifact_provenance", {})
    evidence = {
        "run_id": summary.get("run_id"),
        "artifact_provenance": {
            "required_step_count": provenance.get("required_step_count"),
            "all_required_steps_match": provenance.get("all_required_steps_match"),
            "missing_steps": provenance.get("missing_steps", []),
            "stale_steps": provenance.get("stale_steps", []),
            "missing_artifact_steps": provenance.get("missing_artifact_steps", []),
            "modified_after_run_id_steps": provenance.get("modified_after_run_id_steps", []),
        },
        "run_ui_test_baseline_selftest": {
            "present": runner_selftest.get("present", False),
            "result": runner_selftest.get("result"),
            "scenario_count": runner_selftest.get("scenario_count"),
            "failed_scenarios": runner_selftest.get("failed_scenarios", []),
            "report": runner_selftest.get("report"),
        },
        "artifact_provenance_selftest": {
            "present": provenance_selftest.get("present", False),
            "result": provenance_selftest.get("result"),
            "scenario_count": provenance_selftest.get("scenario_count"),
            "scenario_names": provenance_selftest.get("scenario_names", []),
            "missing_required_scenarios": sorted(
                REQUIRED_ARTIFACT_PROVENANCE_SELFTEST_SCENARIOS
                - set(provenance_selftest.get("scenario_names", []))
            ),
            "failed_scenarios": provenance_selftest.get("failed_scenarios", []),
            "report": provenance_selftest.get("report"),
        },
        "json_artifact_integrity": {
            "present": json_integrity.get("present", False),
            "result": json_integrity.get("result"),
            "checked_count": json_integrity.get("checked_count"),
            "checked": json_integrity.get("checked", []),
            "failure_count": json_integrity.get("failure_count"),
            "failures": json_integrity.get("failures", []),
            "report": json_integrity.get("report"),
        },
        "json_artifact_integrity_selftest": {
            "present": json_integrity_selftest.get("present", False),
            "result": json_integrity_selftest.get("result"),
            "scenario_count": json_integrity_selftest.get("scenario_count"),
            "scenario_names": json_integrity_selftest.get("scenario_names", []),
            "missing_required_scenarios": sorted(
                REQUIRED_JSON_ARTIFACT_INTEGRITY_SELFTEST_SCENARIOS
                - set(json_integrity_selftest.get("scenario_names", []))
            ),
            "failed_scenarios": json_integrity_selftest.get("failed_scenarios", []),
            "report": json_integrity_selftest.get("report"),
        },
    }
    failures = [
        name
        for name, item in (
            ("run_ui_test_baseline_selftest", runner_selftest),
            ("artifact_provenance_selftest", provenance_selftest),
            ("json_artifact_integrity", json_integrity),
            ("json_artifact_integrity_selftest", json_integrity_selftest),
        )
        if item.get("result") != "ok"
    ]
    if not summary.get("run_id"):
        failures.append("missing_run_id")
    if provenance.get("all_required_steps_match") is not True:
        failures.append("artifact_provenance")
    if evidence["artifact_provenance_selftest"]["missing_required_scenarios"]:
        failures.append("artifact_provenance_selftest_missing_required_scenarios")
    if evidence["json_artifact_integrity_selftest"]["missing_required_scenarios"]:
        failures.append("json_artifact_integrity_selftest_missing_required_scenarios")
    if failures:
        return check(
            FAIL,
            "Test infrastructure self-checks or artifact provenance are missing/failing",
            {"failures": failures, **evidence},
        )
    return check(
        PASS,
        "Runner, JSON artifact integrity, and artifact provenance self-checks are passing",
        evidence,
    )


def evaluate(
    summary: dict[str, Any],
    repo_root: Path,
    results_dir: Path,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    gates = {
        "core_tests": ctest_gate(summary),
        "registered_tests": registered_gate(summary),
        "visual_baselines": visual_gate(summary, coverage),
        "layout_assertions": layout_assertion_gate(summary, coverage),
        "theme_dpi_font_matrix": matrix_gate(summary, coverage),
        "image_diff_workflow": regression_gate(summary, results_dir),
        "gui_exercise": gui_exercise_gate(summary, coverage),
        "crash_gate": crash_gate(summary),
        "dependency_coverage": dependency_gate(summary),
        "manual_smoke": manual_smoke_gate(summary, results_dir),
        "test_infrastructure": test_infrastructure_gate(summary),
    }
    status_counts: dict[str, int] = {}
    for gate in gates.values():
        status_counts[gate["status"]] = status_counts.get(gate["status"], 0) + 1
    overall = PASS if set(status_counts) == {PASS} else FAIL
    return {
        "overall_status": overall,
        "ready_for_sweeping_style_change": overall == PASS,
        "status_counts": status_counts,
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=Path("/tmp/freecad-test-results/baseline-summary.json"))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--results-dir", type=Path, default=Path("/tmp/freecad-test-results"))
    parser.add_argument(
        "--coverage-config",
        type=Path,
        help="JSON coverage manifest. Defaults to tools/ui_style_coverage.default.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    summary = read_json(args.summary)
    if summary is None:
        report = {
            "overall_status": FAIL,
            "ready_for_sweeping_style_change": False,
            "status_counts": {MISSING: 1},
            "gates": {"summary": check(MISSING, "Baseline summary is missing", {"expected": str(args.summary)})},
        }
    else:
        report = evaluate(summary, args.repo_root, args.results_dir, load_coverage_spec(args.coverage_config))

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report["overall_status"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
