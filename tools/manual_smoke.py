#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Create or validate the manual UI/style smoke artifact."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCHEMA = "freecad-ui-style-manual-smoke-v2"

REQUIRED_CHECKS = {
    "open_sample_files": "Open representative sample files and confirm they render and remain usable.",
    "inspect_toolbars_task_panels": "Inspect toolbars and task panels for clipping, overlap, missing icons, and scroll access.",
    "create_edit_sketch": "Create and edit a sketch, including constraints and task-panel close/cancel behavior.",
    "create_partdesign_feature": "Create a PartDesign feature and inspect its task panel and resulting model.",
    "open_techdraw_page": "Open a TechDraw page/view and inspect navigation, page rendering, and task/UI controls.",
    "use_draft_panel": "Use a Draft panel and confirm controls, selection, and task state are usable.",
    "run_cam_fem_panel_smoke": "Run one CAM and one FEM panel smoke check, including setup/tool or solver/material UI.",
}

VALID_STATUSES = {"pass", "fail", "blocked"}
PLACEHOLDER_EVIDENCE_PREFIXES = ("synthetic://", "placeholder://", "todo://")
PLACEHOLDER_TEXT = {"todo", "tbd", "n/a", "none", "placeholder"}
AUDITABLE_EVIDENCE_SCHEMES = {"http", "https", "file"}
EVIDENCE_HINT = "Use existing local file paths, file:// URIs, or http(s) review links."


def freecad_version_string(value: Any) -> str:
    if isinstance(value, list) and len(value) >= 3:
        base = ".".join(str(part) for part in value[:3])
        revision = str(value[7]).strip() if len(value) > 7 and str(value[7]).strip() else ""
        suffix = f" ({revision})" if revision else ""
        return base + suffix
    return str(value or "").strip()


def expected_build_from_summary(summary: dict[str, Any]) -> dict[str, str]:
    startup = summary.get("freecad_startup_smoke", {})
    return {
        "freecad_version": freecad_version_string(startup.get("freecad_version")),
        "git_revision": str((startup.get("freecad_version") or [""] * 8)[7]).strip()
        if isinstance(startup.get("freecad_version"), list) and len(startup.get("freecad_version")) > 7
        else "",
        "build_dir": str(summary.get("build_dir", "")).strip(),
    }


def expected_run_from_summary(summary: dict[str, Any]) -> dict[str, str]:
    return {
        "run_id": str(summary.get("run_id", "")).strip(),
        "results_dir": str(summary.get("results_dir", "")).strip(),
    }


def template() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "completed_utc": "",
        "tester": "",
        "baseline_run": {
            "run_id": "",
            "results_dir": "",
        },
        "build": {
            "freecad_version": "",
            "git_revision": "",
            "build_dir": "build/release",
        },
        "environment": {
            "display": "",
            "theme": "",
            "dpi_scale": "",
            "font_size": "",
        },
        "checks": {
            name: {
                "description": description,
                "status": "blocked",
                "notes": "",
                "evidence": [],
                "evidence_hint": EVIDENCE_HINT,
            }
            for name, description in REQUIRED_CHECKS.items()
        },
        "overall_notes": "",
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_datetime(value: Any, field: str, errors: list[str]) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        errors.append(f"{field} is required")
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        errors.append(f"{field} must be ISO-8601")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{field} must include timezone")
        return None
    return parsed


def validate_file_evidence_timestamp(
    path: Path,
    field: str,
    errors: list[str],
    created: datetime | None,
    completed: datetime | None,
) -> None:
    if created is None or completed is None or not path.exists():
        return
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    if modified < created:
        errors.append(f"{field} file evidence is older than created_utc: {str(path)!r}")
    if modified > completed + timedelta(seconds=60):
        errors.append(f"{field} file evidence is newer than completed_utc: {str(path)!r}")


def validate_evidence_entry(
    entry: Any,
    field: str,
    errors: list[str],
    created: datetime | None = None,
    completed: datetime | None = None,
) -> None:
    text = str(entry).strip()
    lowered = text.lower()
    if lowered in PLACEHOLDER_TEXT or lowered.startswith(PLACEHOLDER_EVIDENCE_PREFIXES):
        errors.append(f"{field} contains placeholder entry: {text!r}")
        return

    parsed = urlparse(text)
    if parsed.scheme:
        if parsed.scheme not in AUDITABLE_EVIDENCE_SCHEMES:
            errors.append(
                f"{field} contains unsupported evidence URI scheme: {parsed.scheme!r}"
            )
            return
        if parsed.scheme == "file":
            path = Path(parsed.path)
            if not path.exists():
                errors.append(f"{field} file evidence does not exist: {text!r}")
            else:
                validate_file_evidence_timestamp(path, field, errors, created, completed)
        return

    path = Path(text)
    if not path.exists():
        errors.append(f"{field} path evidence does not exist: {text!r}")
    else:
        validate_file_evidence_timestamp(path, field, errors, created, completed)


def validate(
    data: dict[str, Any],
    expected_build: dict[str, str] | None = None,
    expected_run: dict[str, str] | None = None,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if data.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if not str(data.get("tester", "")).strip():
        errors.append("tester is required")
    created = parse_datetime(data.get("created_utc"), "created_utc", errors)
    completed = parse_datetime(data.get("completed_utc"), "completed_utc", errors)
    if created and completed and completed < created:
        errors.append("completed_utc must not be earlier than created_utc")
    if completed and completed > datetime.now(timezone.utc):
        errors.append("completed_utc must not be in the future")

    baseline_run = data.get("baseline_run")
    if not isinstance(baseline_run, dict):
        errors.append("baseline_run object is required")
    else:
        for key in ("run_id", "results_dir"):
            if not str(baseline_run.get(key, "")).strip():
                errors.append(f"baseline_run.{key} is required")
        if expected_run:
            for key, expected in expected_run.items():
                if expected and str(baseline_run.get(key, "")).strip() != expected:
                    errors.append(
                        f"baseline_run.{key} must match current baseline: expected {expected!r}, got {str(baseline_run.get(key, '')).strip()!r}"
                    )

    build = data.get("build")
    if not isinstance(build, dict):
        errors.append("build object is required")
    else:
        for key in ("freecad_version", "git_revision", "build_dir"):
            if not str(build.get(key, "")).strip():
                errors.append(f"build.{key} is required")
        if expected_build:
            for key, expected in expected_build.items():
                if expected and str(build.get(key, "")).strip() != expected:
                    errors.append(
                        f"build.{key} must match current baseline: expected {expected!r}, got {str(build.get(key, '')).strip()!r}"
                    )

    environment = data.get("environment")
    if not isinstance(environment, dict):
        errors.append("environment object is required")
    else:
        for key in ("display", "theme", "dpi_scale", "font_size"):
            if not str(environment.get(key, "")).strip():
                errors.append(f"environment.{key} is required")

    checks = data.get("checks")
    if not isinstance(checks, dict):
        errors.append("checks object is required")
    else:
        extra_checks = sorted(set(checks) - set(REQUIRED_CHECKS))
        if extra_checks:
            errors.append(f"checks contains unrecognized entries: {', '.join(extra_checks)}")
        for name in REQUIRED_CHECKS:
            item = checks.get(name)
            if not isinstance(item, dict):
                errors.append(f"checks.{name} is required")
                continue
            if item.get("description") != REQUIRED_CHECKS[name]:
                errors.append(f"checks.{name}.description must match required smoke description")
            if item.get("evidence_hint") != EVIDENCE_HINT:
                errors.append(f"checks.{name}.evidence_hint must describe auditable evidence requirements")
            status = item.get("status")
            if status not in VALID_STATUSES:
                errors.append(f"checks.{name}.status must be one of {sorted(VALID_STATUSES)}")
            elif status != "pass":
                errors.append(f"checks.{name}.status is {status}, expected pass")
            if not str(item.get("notes", "")).strip():
                errors.append(f"checks.{name}.notes is required")
            elif str(item.get("notes", "")).strip().lower() in PLACEHOLDER_TEXT:
                errors.append(f"checks.{name}.notes must not be placeholder text")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"checks.{name}.evidence must contain at least one entry")
            elif not all(str(entry).strip() for entry in evidence):
                errors.append(f"checks.{name}.evidence entries must be non-empty strings")
            else:
                for entry in evidence:
                    validate_evidence_entry(
                        entry,
                        f"checks.{name}.evidence",
                        errors,
                        created=created,
                        completed=completed,
                    )

    return not errors, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    write_parser = subparsers.add_parser("write-template", help="Write a manual smoke JSON template")
    write_parser.add_argument("path", type=Path)
    write_parser.add_argument(
        "--summary",
        type=Path,
        help="Baseline summary used to prefill build metadata",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate a manual smoke JSON artifact")
    validate_parser.add_argument("path", type=Path)
    validate_parser.add_argument(
        "--summary",
        type=Path,
        help="Baseline summary whose build metadata the manual smoke artifact must match",
    )

    args = parser.parse_args()

    if args.command == "write-template":
        data = template()
        if args.summary:
            summary = load_json(args.summary)
            data["build"].update(expected_build_from_summary(summary))
            data["baseline_run"].update(expected_run_from_summary(summary))
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(args.path)
        return 0

    data = load_json(args.path)
    summary = load_json(args.summary) if args.summary else None
    expected_build = expected_build_from_summary(summary) if summary else None
    expected_run = expected_run_from_summary(summary) if summary else None
    ok, errors = validate(data, expected_build=expected_build, expected_run=expected_run)
    report = {
        "path": str(args.path),
        "result": "ok" if ok else "failed",
        "errors": errors,
        "expected_build": expected_build or {},
        "expected_run": expected_run or {},
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
