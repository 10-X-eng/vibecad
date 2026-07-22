#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Self-test dynamic UI/style coverage configuration behavior."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

import evaluate_ui_style_gate


SCHEMA = "freecad-ui-style-coverage-selftest-v1"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def scenario(name: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "result": "pass" if passed else "fail",
        "details": details,
    }


def build_config(
    output_dir: Path,
    repo_tools: Path,
    fixture_config: Path | None = None,
    dialog_config: Path | None = None,
    task_config: Path | None = None,
    variant_config: Path | None = None,
) -> Path:
    config = {
        "required_workbenches": [
            "PartWorkbench",
            "PartDesignWorkbench",
            "SketcherWorkbench",
            "DraftWorkbench",
            "TechDrawWorkbench",
            "FemWorkbench",
            "CAMWorkbench",
            "SpreadsheetWorkbench",
        ],
        "required_fixture_coverage": [
            "part",
            "partdesign",
            "sketcher",
            "draft",
            "techdraw",
            "fem",
            "cam",
            "spreadsheet",
        ],
        "required_dialog_coverage": [
            "preferences",
            "file-open",
            "file-open-return",
            "file-save",
        ],
        "required_task_coverage": [
            "partdesign-task",
            "sketcher-edit",
            "draft-panel",
            "techdraw-page-view",
            "fem-solver-material",
            "cam-setup-tool",
        ],
        "fixture_scene_config": str(fixture_config or repo_tools / "gui_visual_scenes.default.json"),
        "dialog_scene_config": str(dialog_config or repo_tools / "gui_visual_dialogs.default.json"),
        "task_scene_config": str(task_config or repo_tools / "gui_visual_tasks.default.json"),
        "variant_config": str(variant_config or repo_tools / "gui_visual_variants.default.json"),
    }
    path = output_dir / "coverage.json"
    write_json(path, config)
    return path


def run_selftest(summary_path: Path, results_dir: Path, output: Path | None) -> int:
    summary = read_json(summary_path)
    repo_tools = Path(__file__).resolve().parent

    scenarios = []
    base_coverage = evaluate_ui_style_gate.load_coverage_spec(repo_tools / "ui_style_coverage.default.json")
    base_report = evaluate_ui_style_gate.evaluate(summary, repo_tools.parent, results_dir, base_coverage)
    base_visual = base_report["gates"]["visual_baselines"]
    base_matrix = base_report["gates"]["theme_dpi_font_matrix"]
    scenarios.append(
        scenario(
            "default_config_visual_and_matrix_pass",
            base_visual["status"] == "pass" and base_matrix["status"] == "pass",
            {
                "visual_status": base_visual["status"],
                "matrix_status": base_matrix["status"],
            },
        )
    )

    new_workbench_summary = json.loads(json.dumps(summary))
    new_workbench_summary.setdefault("gui_visual_venv", {}).setdefault(
        "discovered_workbenches", []
    ).append("SelfTestNewWorkbench")
    new_workbench_report = evaluate_ui_style_gate.evaluate(
        new_workbench_summary,
        repo_tools.parent,
        results_dir,
        base_coverage,
    )
    new_workbench_visual = new_workbench_report["gates"]["visual_baselines"]
    new_workbench_matrix = new_workbench_report["gates"]["theme_dpi_font_matrix"]
    scenarios.append(
        scenario(
            "new_discovered_workbench_fails_visual_and_matrix_gates",
            new_workbench_visual["status"] == "fail"
            and "SelfTestNewWorkbench"
            in new_workbench_visual["evidence"]["gui_visual_venv"].get(
                "missing_discovered_workbenches", []
            )
            and new_workbench_matrix["status"] == "fail"
            and any(
                "workbench-SelfTestNewWorkbench" in missing
                for missing in new_workbench_matrix["evidence"].get(
                    "missing_required_scenes_by_variant", {}
                ).values()
            ),
            {
                "visual_status": new_workbench_visual["status"],
                "missing_discovered_workbenches": new_workbench_visual["evidence"][
                    "gui_visual_venv"
                ].get("missing_discovered_workbenches", []),
                "matrix_status": new_workbench_matrix["status"],
                "matrix_missing_by_variant": new_workbench_matrix["evidence"].get(
                    "missing_required_scenes_by_variant", {}
                ),
            },
        )
    )

    with tempfile.TemporaryDirectory(prefix="freecad-ui-style-coverage-selftest-") as temp:
        temp_dir = Path(temp)

        fixture_config = read_json(repo_tools / "gui_visual_scenes.default.json")
        fixture_config["scenes"] = list(fixture_config["scenes"]) + [
            {
                "name": "selftest-missing-fixture",
                "workbench": "PartWorkbench",
                "file": "data/tests/Crank.fcstd",
            }
        ]
        fixture_path = temp_dir / "fixtures.json"
        write_json(fixture_path, fixture_config)
        fixture_coverage_path = build_config(temp_dir, repo_tools, fixture_config=fixture_path)
        fixture_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(fixture_coverage_path),
        )
        fixture_visual = fixture_report["gates"]["visual_baselines"]
        fixture_matrix = fixture_report["gates"]["theme_dpi_font_matrix"]
        missing_fixture = fixture_visual["evidence"]["gui_visual_fixtures"][
            "missing_required_scenes"
        ]
        matrix_missing = fixture_matrix["evidence"].get("missing_required_scenes_by_variant", {})
        scenarios.append(
            scenario(
                "new_required_fixture_fails_visual_and_matrix_gates",
                fixture_visual["status"] == "fail"
                and "fixture-selftest-missing-fixture" in missing_fixture
                and fixture_matrix["status"] == "fail"
                and any(
                    "fixture-selftest-missing-fixture" in missing
                    for missing in matrix_missing.values()
                ),
                {
                    "visual_status": fixture_visual["status"],
                    "missing_fixture_scenes": missing_fixture,
                    "matrix_status": fixture_matrix["status"],
                    "matrix_missing_by_variant": matrix_missing,
                },
            )
        )

        dialog_config = read_json(repo_tools / "gui_visual_dialogs.default.json")
        dialog_config["dialog_scenes"] = list(dialog_config["dialog_scenes"]) + [
            {
                "name": "selftest-missing-dialog",
                "coverage": ["preferences"],
                "command": "Std_DlgPreferences",
                "capture": "top_level_dialog",
                "wait_ms": 100,
                "close_after_capture": True,
                "required_widget_class_contains": ["Gui::Dialog::DlgPreferencesImp"],
                "required_visible_text_contains": ["Preferences"],
            }
        ]
        dialog_path = temp_dir / "dialogs.json"
        write_json(dialog_path, dialog_config)
        task_config = read_json(repo_tools / "gui_visual_tasks.default.json")
        task_config["task_scenes"] = list(task_config["task_scenes"]) + [
            {
                "name": "selftest-missing-task",
                "coverage": ["partdesign-task"],
                "workbench": "PartDesignWorkbench",
                "file": "data/tests/PartDesignExample.FCStd",
                "edit_object": "Pad",
                "capture": "main_window",
                "wait_ms": 100,
                "close_after_capture": True,
                "required_widget_class_contains": ["Gui::TaskView::TaskBox"],
            }
        ]
        task_path = temp_dir / "tasks.json"
        write_json(task_path, task_config)
        dialog_task_coverage_path = build_config(
            temp_dir,
            repo_tools,
            dialog_config=dialog_path,
            task_config=task_path,
        )
        dialog_task_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(dialog_task_coverage_path),
        )
        dialog_task_visual = dialog_task_report["gates"]["visual_baselines"]
        dialog_task_matrix = dialog_task_report["gates"]["theme_dpi_font_matrix"]
        dialog_task_missing = dialog_task_matrix["evidence"].get(
            "missing_required_scenes_by_variant", {}
        )
        scenarios.append(
            scenario(
                "new_required_dialog_and_task_fail_visual_and_matrix_gates",
                dialog_task_visual["status"] == "fail"
                and dialog_task_matrix["status"] == "fail"
                and any(
                    "dialog-selftest-missing-dialog" in missing
                    for missing in dialog_task_missing.values()
                )
                and any(
                    "task-selftest-missing-task" in missing
                    for missing in dialog_task_missing.values()
                ),
                {
                    "visual_status": dialog_task_visual["status"],
                    "matrix_status": dialog_task_matrix["status"],
                    "matrix_missing_by_variant": dialog_task_missing,
                    "missing_dialog_scenes": dialog_task_visual["evidence"][
                        "gui_visual_dialogs"
                    ].get("missing_required_scenes", []),
                    "missing_task_scenes": dialog_task_visual["evidence"][
                        "gui_visual_tasks"
                    ].get("missing_required_scenes", []),
                },
            )
        )

        empty_workbench_coverage = read_json(repo_tools / "ui_style_coverage.default.json")
        empty_workbench_coverage["required_workbenches"] = []
        empty_workbench_coverage_path = temp_dir / "empty-workbenches-coverage.json"
        write_json(empty_workbench_coverage_path, empty_workbench_coverage)
        empty_workbench_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(empty_workbench_coverage_path),
        )
        empty_workbench_visual = empty_workbench_report["gates"]["visual_baselines"]
        empty_workbench_matrix = empty_workbench_report["gates"]["theme_dpi_font_matrix"]
        scenarios.append(
            scenario(
                "empty_required_workbenches_fails_visual_and_matrix_gates",
                empty_workbench_visual["status"] == "fail"
                and empty_workbench_matrix["status"] == "fail"
                and any(
                    "empty_required_list:coverage_config.required_workbenches" in error
                    for error in empty_workbench_visual["evidence"].get(
                        "workbench_config_errors", []
                    )
                )
                and any(
                    "empty_required_list:coverage_config.required_workbenches" in error
                    for error in empty_workbench_matrix["evidence"].get(
                        "workbench_config_errors", []
                    )
                ),
                {
                    "visual_status": empty_workbench_visual["status"],
                    "matrix_status": empty_workbench_matrix["status"],
                    "workbench_config_errors": empty_workbench_visual["evidence"].get(
                        "workbench_config_errors", []
                    ),
                },
            )
        )

        invalid_workbench_coverage = read_json(repo_tools / "ui_style_coverage.default.json")
        invalid_workbench_coverage["required_workbenches"] = list(
            invalid_workbench_coverage["required_workbenches"]
        ) + [invalid_workbench_coverage["required_workbenches"][0], ""]
        invalid_workbench_coverage_path = temp_dir / "invalid-workbenches-coverage.json"
        write_json(invalid_workbench_coverage_path, invalid_workbench_coverage)
        invalid_workbench_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(invalid_workbench_coverage_path),
        )
        invalid_workbench_visual = invalid_workbench_report["gates"]["visual_baselines"]
        invalid_workbench_matrix = invalid_workbench_report["gates"]["theme_dpi_font_matrix"]
        invalid_workbench_errors = invalid_workbench_visual["evidence"].get(
            "workbench_config_errors", []
        )
        scenarios.append(
            scenario(
                "blank_and_duplicate_required_workbenches_fail_visual_and_matrix_gates",
                invalid_workbench_visual["status"] == "fail"
                and invalid_workbench_matrix["status"] == "fail"
                and any("duplicate_required_workbench" in error for error in invalid_workbench_errors)
                and any("blank_required_workbench" in error for error in invalid_workbench_errors),
                {
                    "visual_status": invalid_workbench_visual["status"],
                    "matrix_status": invalid_workbench_matrix["status"],
                    "workbench_config_errors": invalid_workbench_errors,
                },
            )
        )

        malformed_fixture_config = read_json(repo_tools / "gui_visual_scenes.default.json")
        malformed_fixture_config.pop("scenes", None)
        malformed_fixture_path = temp_dir / "malformed-fixtures.json"
        write_json(malformed_fixture_path, malformed_fixture_config)
        malformed_fixture_coverage_path = build_config(
            temp_dir,
            repo_tools,
            fixture_config=malformed_fixture_path,
        )
        malformed_fixture_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(malformed_fixture_coverage_path),
        )
        malformed_fixture_visual = malformed_fixture_report["gates"]["visual_baselines"]
        malformed_fixture_matrix = malformed_fixture_report["gates"]["theme_dpi_font_matrix"]
        malformed_fixture_errors = malformed_fixture_visual["evidence"].get(
            "fixture_config_errors", []
        )
        scenarios.append(
            scenario(
                "missing_fixture_scene_list_fails_visual_and_matrix_gates",
                malformed_fixture_visual["status"] == "fail"
                and malformed_fixture_matrix["status"] == "fail"
                and any("missing_required_list:fixture_scene_config.scenes" in error for error in malformed_fixture_errors)
                and any(
                    "missing_required_list:fixture_scene_config.scenes" in error
                    for error in malformed_fixture_matrix["evidence"].get(
                        "fixture_config_errors", []
                    )
                ),
                {
                    "visual_status": malformed_fixture_visual["status"],
                    "matrix_status": malformed_fixture_matrix["status"],
                    "fixture_config_errors": malformed_fixture_errors,
                },
            )
        )

        missing_category_coverage_path = build_config(temp_dir, repo_tools)
        missing_category_coverage = read_json(missing_category_coverage_path)
        missing_category_coverage["required_fixture_coverage"] = list(
            missing_category_coverage["required_fixture_coverage"]
        ) + ["selftest-fixture-domain"]
        missing_category_coverage["required_dialog_coverage"] = list(
            missing_category_coverage["required_dialog_coverage"]
        ) + ["selftest-dialog-domain"]
        missing_category_coverage["required_task_coverage"] = list(
            missing_category_coverage["required_task_coverage"]
        ) + ["selftest-task-domain"]
        missing_category_path = temp_dir / "missing-category-coverage.json"
        write_json(missing_category_path, missing_category_coverage)
        missing_category_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(missing_category_path),
        )
        missing_category_visual = missing_category_report["gates"]["visual_baselines"]
        scenarios.append(
            scenario(
                "missing_required_visual_coverage_tags_fail_visual_gate",
                missing_category_visual["status"] == "fail"
                and any(
                    "missing_required_fixture_coverage:selftest-fixture-domain" in error
                    for error in missing_category_visual["evidence"].get(
                        "fixture_config_errors", []
                    )
                )
                and any(
                    "missing_required_dialog_coverage:selftest-dialog-domain" in error
                    for error in missing_category_visual["evidence"].get(
                        "dialog_config_errors", []
                    )
                )
                and any(
                    "missing_required_task_coverage:selftest-task-domain" in error
                    for error in missing_category_visual["evidence"].get(
                        "task_config_errors", []
                    )
                ),
                {
                    "visual_status": missing_category_visual["status"],
                    "fixture_config_errors": missing_category_visual["evidence"].get(
                        "fixture_config_errors", []
                    ),
                    "dialog_config_errors": missing_category_visual["evidence"].get(
                        "dialog_config_errors", []
                    ),
                    "task_config_errors": missing_category_visual["evidence"].get(
                        "task_config_errors", []
                    ),
                },
            )
        )

        untagged_fixture_config = read_json(repo_tools / "gui_visual_scenes.default.json")
        untagged_fixture_config["scenes"][0].pop("coverage", None)
        untagged_fixture_path = temp_dir / "untagged-fixtures.json"
        write_json(untagged_fixture_path, untagged_fixture_config)
        untagged_fixture_coverage_path = build_config(
            temp_dir,
            repo_tools,
            fixture_config=untagged_fixture_path,
        )
        untagged_fixture_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(untagged_fixture_coverage_path),
        )
        untagged_fixture_visual = untagged_fixture_report["gates"]["visual_baselines"]
        untagged_fixture_errors = untagged_fixture_visual["evidence"].get(
            "fixture_config_errors", []
        )
        scenarios.append(
            scenario(
                "untagged_fixture_scene_fails_visual_gate",
                untagged_fixture_visual["status"] == "fail"
                and any("missing_fixture_scene_coverage:part-crank" in error for error in untagged_fixture_errors)
                and any("missing_required_fixture_coverage:part" in error for error in untagged_fixture_errors),
                {
                    "visual_status": untagged_fixture_visual["status"],
                    "fixture_config_errors": untagged_fixture_errors,
                },
            )
        )

        duplicate_fixture_config = read_json(repo_tools / "gui_visual_scenes.default.json")
        duplicate_fixture_config["scenes"] = list(duplicate_fixture_config["scenes"]) + [
            dict(duplicate_fixture_config["scenes"][0])
        ]
        duplicate_fixture_path = temp_dir / "duplicate-fixtures.json"
        write_json(duplicate_fixture_path, duplicate_fixture_config)
        duplicate_fixture_coverage_path = build_config(
            temp_dir,
            repo_tools,
            fixture_config=duplicate_fixture_path,
        )
        duplicate_fixture_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(duplicate_fixture_coverage_path),
        )
        duplicate_fixture_visual = duplicate_fixture_report["gates"]["visual_baselines"]
        duplicate_fixture_matrix = duplicate_fixture_report["gates"]["theme_dpi_font_matrix"]
        fixture_errors = duplicate_fixture_visual["evidence"].get("fixture_config_errors", [])
        scenarios.append(
            scenario(
                "duplicate_fixture_scene_name_fails_visual_and_matrix_gates",
                duplicate_fixture_visual["status"] == "fail"
                and duplicate_fixture_matrix["status"] == "fail"
                and any("duplicate_fixture_scene_name" in error for error in fixture_errors)
                and any(
                    "duplicate_fixture_scene_name" in error
                    for error in duplicate_fixture_matrix["evidence"].get(
                        "fixture_config_errors", []
                    )
                ),
                {
                    "visual_status": duplicate_fixture_visual["status"],
                    "matrix_status": duplicate_fixture_matrix["status"],
                    "fixture_config_errors": fixture_errors,
                },
            )
        )

        invalid_dialog_config = read_json(repo_tools / "gui_visual_dialogs.default.json")
        weak_dialog = dict(invalid_dialog_config["dialog_scenes"][0])
        weak_dialog["name"] = "selftest-dialog-weak-contract"
        weak_dialog.pop("required_widget_class_contains", None)
        weak_dialog.pop("required_visible_text_contains", None)
        weak_dialog["capture"] = "not-a-real-capture-target"
        weak_dialog["wait_ms"] = 0
        weak_dialog["close_after_capture"] = False
        invalid_dialog_config["dialog_scenes"] = list(invalid_dialog_config["dialog_scenes"]) + [
            weak_dialog,
            {"name": "", "command": "Std_DlgPreferences"},
            {"name": "selftest-dialog-without-action"},
        ]
        invalid_dialog_path = temp_dir / "invalid-dialogs.json"
        write_json(invalid_dialog_path, invalid_dialog_config)
        invalid_task_config = read_json(repo_tools / "gui_visual_tasks.default.json")
        weak_task = dict(invalid_task_config["task_scenes"][0])
        weak_task["name"] = "selftest-task-weak-contract"
        weak_task.pop("required_widget_class_contains", None)
        weak_task["capture"] = ""
        weak_task["wait_ms"] = "later"
        weak_task["close_after_capture"] = False
        invalid_task_config["task_scenes"] = list(invalid_task_config["task_scenes"]) + [
            weak_task,
            {"name": "selftest-task-missing-workbench", "edit_object": "Sketch"},
            {"name": "selftest-task-missing-file", "workbench": "PartWorkbench", "file": "missing.FCStd", "edit_object": "Box"},
        ]
        invalid_task_path = temp_dir / "invalid-tasks.json"
        write_json(invalid_task_path, invalid_task_config)
        invalid_scene_coverage_path = build_config(
            temp_dir,
            repo_tools,
            dialog_config=invalid_dialog_path,
            task_config=invalid_task_path,
        )
        invalid_scene_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(invalid_scene_coverage_path),
        )
        invalid_scene_visual = invalid_scene_report["gates"]["visual_baselines"]
        dialog_errors = invalid_scene_visual["evidence"].get("dialog_config_errors", [])
        task_errors = invalid_scene_visual["evidence"].get("task_config_errors", [])
        scenarios.append(
            scenario(
                "invalid_dialog_and_task_scene_config_fails_visual_gate",
                invalid_scene_visual["status"] == "fail"
                and any("blank_dialog_scene_name" in error for error in dialog_errors)
                and any("missing_dialog_scene_action" in error for error in dialog_errors)
                and any("invalid_dialog_scene_capture:selftest-dialog-weak-contract" in error for error in dialog_errors)
                and any("invalid_dialog_scene_wait_ms:selftest-dialog-weak-contract" in error for error in dialog_errors)
                and any("dialog_scene_must_close_after_capture:selftest-dialog-weak-contract" in error for error in dialog_errors)
                and any("missing_dialog_scene_required_widget_class:selftest-dialog-weak-contract" in error for error in dialog_errors)
                and any("missing_dialog_scene_required_visible_text:selftest-dialog-weak-contract" in error for error in dialog_errors)
                and any("missing_task_scene_workbench" in error for error in task_errors)
                and any("missing_task_scene_file" in error for error in task_errors)
                and any("missing_task_scene_capture:selftest-task-weak-contract" in error for error in task_errors)
                and any("invalid_task_scene_wait_ms:selftest-task-weak-contract" in error for error in task_errors)
                and any("task_scene_must_close_after_capture:selftest-task-weak-contract" in error for error in task_errors)
                and any("missing_task_scene_required_widget_class:selftest-task-weak-contract" in error for error in task_errors),
                {
                    "visual_status": invalid_scene_visual["status"],
                    "dialog_config_errors": dialog_errors,
                    "task_config_errors": task_errors,
                },
            )
        )

        variant_config = read_json(repo_tools / "gui_visual_variants.default.json")
        variant_config["variants"] = list(variant_config["variants"]) + [
            {"name": "selftest-missing-variant"}
        ]
        variant_path = temp_dir / "variants.json"
        write_json(variant_path, variant_config)
        variant_coverage_path = build_config(temp_dir, repo_tools, variant_config=variant_path)
        variant_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(variant_coverage_path),
        )
        variant_matrix = variant_report["gates"]["theme_dpi_font_matrix"]
        missing_variants = variant_matrix["evidence"].get("missing_variants", [])
        scenarios.append(
            scenario(
                "new_required_variant_fails_matrix_gate",
                variant_matrix["status"] == "fail"
                and "selftest-missing-variant" in missing_variants,
                {
                    "matrix_status": variant_matrix["status"],
                    "missing_variants": missing_variants,
                },
            )
        )

        duplicate_variant_config = read_json(repo_tools / "gui_visual_variants.default.json")
        duplicate_variant_config["variants"] = list(duplicate_variant_config["variants"]) + [
            dict(duplicate_variant_config["variants"][0])
        ]
        duplicate_variant_path = temp_dir / "duplicate-variants.json"
        write_json(duplicate_variant_path, duplicate_variant_config)
        duplicate_variant_coverage_path = build_config(
            temp_dir,
            repo_tools,
            variant_config=duplicate_variant_path,
        )
        duplicate_variant_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(duplicate_variant_coverage_path),
        )
        duplicate_variant_matrix = duplicate_variant_report["gates"]["theme_dpi_font_matrix"]
        scenarios.append(
            scenario(
                "duplicate_variant_name_fails_matrix_gate",
                duplicate_variant_matrix["status"] == "fail"
                and any(
                    "duplicate_variant_name" in error
                    for error in duplicate_variant_matrix["evidence"].get("variant_config_errors", [])
                ),
                {
                    "matrix_status": duplicate_variant_matrix["status"],
                    "variant_config_errors": duplicate_variant_matrix["evidence"].get(
                        "variant_config_errors", []
                    ),
                },
            )
        )

        invalid_variant_config = read_json(repo_tools / "gui_visual_variants.default.json")
        invalid_variant_config["variants"] = list(invalid_variant_config["variants"]) + [
            {"name": "", "font_scale": 0},
            {"name": "missing-pack", "preference_pack": "does/not/exist.cfg"},
        ]
        invalid_variant_path = temp_dir / "invalid-variants.json"
        write_json(invalid_variant_path, invalid_variant_config)
        invalid_variant_coverage_path = build_config(
            temp_dir,
            repo_tools,
            variant_config=invalid_variant_path,
        )
        invalid_variant_report = evaluate_ui_style_gate.evaluate(
            summary,
            repo_tools.parent,
            results_dir,
            evaluate_ui_style_gate.load_coverage_spec(invalid_variant_coverage_path),
        )
        invalid_variant_matrix = invalid_variant_report["gates"]["theme_dpi_font_matrix"]
        errors = invalid_variant_matrix["evidence"].get("variant_config_errors", [])
        scenarios.append(
            scenario(
                "invalid_variant_fields_fail_matrix_gate",
                invalid_variant_matrix["status"] == "fail"
                and any("blank_variant_name" in error for error in errors)
                and any("invalid_font_scale" in error for error in errors)
                and any("missing_preference_pack" in error for error in errors),
                {
                    "matrix_status": invalid_variant_matrix["status"],
                    "variant_config_errors": errors,
                },
            )
        )

    failed = [item for item in scenarios if item["result"] != "pass"]
    report = {
        "schema": SCHEMA,
        "result": "ok" if not failed else "fail",
        "scenario_count": len(scenarios),
        "failed_scenarios": [item["name"] for item in failed],
        "scenarios": scenarios,
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0 if report["result"] == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=Path("/tmp/freecad-test-results/baseline-summary.json"))
    parser.add_argument("--results-dir", type=Path, default=Path("/tmp/freecad-test-results"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    return run_selftest(args.summary, args.results_dir, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
