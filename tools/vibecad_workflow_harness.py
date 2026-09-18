#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run named VibeCAD button workflows through the existing loopback click path.

The visible tour remains a demo. This harness posts the same
``/v1/ui/click`` body the tour posts, then checks documents and the model
tree after every step. Closed profiles and the export file go through the
existing ``/v1/run`` route (``SketchObject.addGeometry(Part.Circle)`` and
``Import.export``), not a second clicker. Code owns the click, the
timeout, and pass/fail.

Use ``--fake`` when no display is available. That still speaks HTTP on
127.0.0.1. Jev is optional and off unless ``--judge`` is set and
``TYPESAFE_API_KEY`` is present.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import vibecad_workflow_channel as channel
import vibecad_workflow_judge as judge


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
DEFAULT_WORKFLOWS = TOOLS_DIR / "vibecad_workflows.json"
TOUR_SCRIPT = REPO_ROOT / "Invoke-VibeCAD-VisibleTour.ps1"
CLICK_NEVER_REACHED_CODES = frozenset(
    {
        "GUI_REQUIRED",
        "HTTP_ERROR",
        "INVALID_RESPONSE",
        "MAIN_WINDOW_UNAVAILABLE",
        "MENU_BAR_UNAVAILABLE",
        "NOT_FOUND",
        "RIBBON_TABS_UNAVAILABLE",
        "UNAUTHORIZED",
        "UI_PROCESS_ID_INVALID",
        "UI_PROCESS_MISMATCH",
        "UI_TARGET_DISABLED",
        "UI_TARGET_HAS_NO_MENU",
        "UI_TARGET_INDEX_INVALID",
        "UI_TARGET_INDEX_MISMATCH",
        "UI_TARGET_KIND_INVALID",
        "UI_TARGET_NOT_TRIGGERABLE",
        "UI_TARGET_NOT_UNIQUE",
        "UI_TARGET_TEXT_REQUIRED",
    }
)
ALLOWED_CLICK_INPUT_METHODS = frozenset(
    {
        "qt_in_process_mouse_click",
        "qt_in_process_menu_popup",
        "qt_in_process_action_trigger",
        "qt_in_process_dialog_button",
    }
)


def load_workflows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    workflows = payload.get("workflows")
    if not isinstance(workflows, list) or not workflows:
        raise ValueError("Workflow file has no workflows.")
    return [item for item in workflows if isinstance(item, dict)]


def load_live_endpoint(agent_home: Path) -> tuple[str, str]:
    endpoint_path = agent_home / "endpoint.json"
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    token_path = Path(str(endpoint.get("token_path") or agent_home / "token"))
    token = token_path.read_text(encoding="utf-8").strip()
    return str(endpoint.get("base_url") or ""), token


def click_reached_target(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("ok")
        or payload.get("click_queued")
        or payload.get("object_name")
        or payload.get("input_method")
        or payload.get("semantic_verified")
        or str(payload.get("failure_code") or "") == "UI_CLICK_NOT_APPLIED"
    )


def click_accepted(payload: dict[str, Any]) -> tuple[bool, str]:
    """Reject clicks that never reached a target. Checks own pass/fail.

    Creating a document moves Qt focus. The live agent may then return
    ``UI_CLICK_NOT_APPLIED`` with ``focus_restored`` false even though
    ``GET /v1/documents`` shows an active document. Restoration fields
    are evidence, not a harness hard-fail.
    """

    if str(payload.get("physical_cursor_control") or "none") != "none":
        return False, "click used physical cursor control"
    input_method = str(payload.get("input_method") or "")
    if input_method and input_method not in ALLOWED_CLICK_INPUT_METHODS:
        return False, f"unsupported click input_method {input_method!r}"
    failure_code = str(payload.get("failure_code") or "")
    if failure_code in CLICK_NEVER_REACHED_CODES:
        return False, str(payload.get("error") or failure_code or "click failed")
    if payload.get("ok") or click_reached_target(payload):
        return True, ""
    return False, str(payload.get("error") or failure_code or "click failed")


def evaluate_check(
    check: dict[str, Any],
    *,
    click_payload: dict[str, Any],
    documents_payload: dict[str, Any],
    tree_payload: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    documents = documents_payload.get("documents")
    if not isinstance(documents, list):
        documents = []
    tree = tree_payload.get("result") if isinstance(tree_payload.get("result"), dict) else {}
    objects = tree.get("objects") if isinstance(tree.get("objects"), list) else []
    type_ids = {str(item.get("type_id") or "") for item in objects if isinstance(item, dict)}

    if check.get("click_ok") and not click_payload.get("ok"):
        errors.append("click_ok expected a successful click")
    minimum_docs = check.get("document_count_min")
    if minimum_docs is not None and len(documents) < int(minimum_docs):
        errors.append(
            f"document_count_min {minimum_docs} failed; found {len(documents)}"
        )
    if check.get("active_document"):
        if not any(item.get("active") for item in documents if isinstance(item, dict)):
            errors.append("no active document after click")
        if tree.get("document") in {None, ""}:
            errors.append("tree inspect reported no active document")
    required_types = check.get("tree_type_ids")
    if isinstance(required_types, list):
        missing = [item for item in required_types if item not in type_ids]
        if missing:
            errors.append(f"tree missing type ids {missing}; have {sorted(type_ids)}")
    minimum_geometry = check.get("sketch_geometry_min")
    if minimum_geometry is not None:
        geometry_counts = [
            int(item.get("geometry_count") or 0)
            for item in objects
            if isinstance(item, dict)
            and str(item.get("type_id") or "") == "Sketcher::SketchObject"
        ]
        have = max(geometry_counts) if geometry_counts else 0
        if have < int(minimum_geometry):
            errors.append(
                f"sketch_geometry_min {minimum_geometry} failed; found {have}"
            )
    run_result = (
        click_payload.get("result")
        if isinstance(click_payload.get("result"), dict)
        else {}
    )
    if check.get("run_ok") and not click_payload.get("ok"):
        errors.append("run_ok expected POST /v1/run to succeed")
    required_command = check.get("command_active")
    if required_command:
        if not bool(run_result.get("command_active")):
            errors.append(
                f"command_active {required_command} failed; "
                "PartDesign_DesignExtrude is not enabled"
            )
    if check.get("exported"):
        exported = str(
            run_result.get("exported_path")
            or click_payload.get("exported_path")
            or tree_payload.get("exported_path")
            or ""
        )
        path = Path(exported) if exported else None
        if path is not None and path.is_file():
            size = path.stat().st_size
            minimum = int(check.get("export_bytes_min") or 1)
            if size < minimum:
                errors.append(f"export file is {size} bytes; expected at least {minimum}")
        else:
            triggered = str(
                click_payload.get("object_name") or click_payload.get("target_text") or ""
            )
            if triggered not in {"Std_Export", "Export"}:
                errors.append("export did not produce a file")
    return errors


def run_step(
    step: dict[str, Any],
    client: channel.AgentClickChannel,
    *,
    timeout_seconds: float,
    judge_enabled: bool,
    judge_transport: Any = None,
    export_path: str = "",
) -> dict[str, Any]:
    click_spec = dict(step.get("click") or {})
    run_spec = dict(step.get("run") or {})
    started = time.monotonic()
    if run_spec:
        recipe = str(run_spec.get("id") or "")
        click_payload = client.run(
            channel.workflow_run_python(recipe, export_path=export_path)
        )
        accepted = bool(click_payload.get("ok"))
        click_error = str(
            click_payload.get("error")
            or click_payload.get("failure_code")
            or "POST /v1/run failed"
        )
    else:
        click_payload = client.click(
            str(click_spec.get("kind") or ""),
            str(click_spec.get("text") or ""),
            expected_process_id=click_spec.get("expected_process_id"),
            expected_index=click_spec.get("expected_index"),
        )
        accepted, click_error = click_accepted(click_payload)
    documents_payload = client.documents()
    tree_payload = client.inspect_tree()
    elapsed = time.monotonic() - started
    errors = [] if accepted else [click_error]
    if elapsed > timeout_seconds:
        errors.append(f"step exceeded timeout of {timeout_seconds:g}s")
    errors.extend(
        evaluate_check(
            dict(step.get("check") or {}),
            click_payload=click_payload,
            documents_payload=documents_payload,
            tree_payload=tree_payload,
        )
    )
    code_passed = not errors
    judge_state = {
        "workflow_step": step.get("id"),
        "click": click_spec or run_spec,
        "click_response": {
            key: click_payload.get(key)
            for key in (
                "ok",
                "failure_code",
                "target_kind",
                "target_text",
                "object_name",
                "semantic_verified",
                "input_method",
                "result",
            )
        },
        "documents": documents_payload.get("documents"),
        "tree": tree_payload.get("result"),
        "code_errors": list(errors),
        "code_passed": code_passed,
    }
    judged = judge.judge_step(
        judge_state,
        enabled=judge_enabled,
        transport=judge_transport,
    )
    # Code owns pass/fail. A judge can never turn a failed step into a pass.
    # Low confidence also never counts as a pass.
    passed = code_passed
    return {
        "id": step.get("id"),
        "click": click_spec or None,
        "run": run_spec or None,
        "elapsed_s": round(elapsed, 3),
        "click_response": click_payload,
        "documents": documents_payload,
        "tree": tree_payload.get("result"),
        "errors": errors,
        "judge": judged,
        "passed": passed,
    }


def run_workflow(
    workflow: dict[str, Any],
    client: channel.AgentClickChannel,
    *,
    timeout_seconds: float,
    judge_enabled: bool,
    judge_transport: Any = None,
    export_path: str = "",
) -> dict[str, Any]:
    steps = []
    passed = True
    for step in workflow.get("steps") or []:
        if not isinstance(step, dict):
            continue
        result = run_step(
            step,
            client,
            timeout_seconds=timeout_seconds,
            judge_enabled=judge_enabled,
            judge_transport=judge_transport,
            export_path=export_path,
        )
        steps.append(result)
        if not result["passed"]:
            passed = False
            break
    return {
        "id": workflow.get("id"),
        "title": workflow.get("title"),
        "passed": passed,
        "steps": steps,
    }


def run_harness(
    *,
    workflows: list[dict[str, Any]],
    client: channel.AgentClickChannel,
    timeout_seconds: float,
    judge_enabled: bool,
    judge_transport: Any = None,
    export_path: str = "",
) -> dict[str, Any]:
    preflight_steps = [
        {
            "id": "dismiss_document_recovery",
            "run": {"id": "dismiss_document_recovery"},
            "check": {"run_ok": True},
        },
        {
            "id": "leave_leftover_sketch",
            "run": {"id": "leave_active_sketch"},
            "check": {"run_ok": True},
        },
    ]
    preflight = []
    preflight_passed = True
    for step in preflight_steps:
        result = run_step(
            step,
            client,
            timeout_seconds=timeout_seconds,
            judge_enabled=judge_enabled,
            judge_transport=judge_transport,
            export_path=export_path,
        )
        preflight.append(result)
        if not result["passed"]:
            preflight_passed = False
            break
    results = []
    if preflight_passed:
        results = [
            run_workflow(
                workflow,
                client,
                timeout_seconds=timeout_seconds,
                judge_enabled=judge_enabled,
                judge_transport=judge_transport,
                export_path=export_path,
            )
            for workflow in workflows
        ]
    return {
        "schema": "vibecad.workflow-harness-report.v1",
        "click_route": "/v1/ui/click",
        "tour_remains_demo": TOUR_SCRIPT.is_file(),
        "judge_requested": bool(judge_enabled),
        "passed": bool(preflight_passed and results and all(item["passed"] for item in results)),
        "preflight": preflight,
        "workflows": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflows",
        type=Path,
        default=DEFAULT_WORKFLOWS,
        help="Workflow definition JSON (default: tools/vibecad_workflows.json).",
    )
    parser.add_argument(
        "--workflow",
        action="append",
        dest="workflow_ids",
        help="Run only this workflow id. Repeatable.",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use the fake loopback click channel (no display required).",
    )
    parser.add_argument(
        "--agent-home",
        type=Path,
        default=REPO_ROOT / ".vibecad-dev" / "agent",
        help="Checkout-scoped agent home with endpoint.json and token.",
    )
    parser.add_argument("--base-url", help="Override the loopback agent URL.")
    parser.add_argument("--token", help="Override the bearer token.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Ask Jev after each step. Still skipped without TYPESAFE_API_KEY.",
    )
    parser.add_argument("--output", type=Path, help="Write the JSON report here.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workflows = load_workflows(args.workflows)
    if args.workflow_ids:
        wanted = set(args.workflow_ids)
        workflows = [item for item in workflows if item.get("id") in wanted]
        if not workflows:
            raise SystemExit(f"No workflows matched {sorted(wanted)}.")

    server = None
    export_home = None
    export_path = str(Path(tempfile.gettempdir()) / "vibecad-workflow-harness.step")
    if args.fake:
        if args.output is not None:
            export_dir = args.output.parent
            export_dir.mkdir(parents=True, exist_ok=True)
        else:
            export_home = tempfile.TemporaryDirectory(prefix="vibecad-workflow-export-")
            export_dir = Path(export_home.name)
        export_path = str(export_dir / "vibecad-workflow-harness.step")
        token = args.token or secrets.token_hex(24)
        server, base_url, _state = channel.start_fake_channel(str(export_dir), token)
        client = channel.AgentClickChannel(base_url, token, timeout_seconds=args.timeout)
    else:
        base_url = args.base_url
        token = args.token
        if not base_url or not token:
            live_url, live_token = load_live_endpoint(args.agent_home)
            base_url = base_url or live_url
            token = token or live_token
        client = channel.AgentClickChannel(
            str(base_url),
            str(token),
            timeout_seconds=args.timeout,
        )

    try:
        report = run_harness(
            workflows=workflows,
            client=client,
            timeout_seconds=args.timeout,
            judge_enabled=args.judge,
            export_path=export_path,
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if export_home is not None:
            export_home.cleanup()

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
