#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Self-test the workflow harness against the fake loopback click channel."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any

import vibecad_workflow_channel as channel
import vibecad_workflow_harness as harness
import vibecad_workflow_judge as judge


TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent
TOUR_SCRIPT = REPO_ROOT / "Invoke-VibeCAD-VisibleTour.ps1"


def scenario(name: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "result": "pass" if passed else "fail", "details": details}


def _run_fake(
    workflows: list[dict[str, Any]], **kwargs: Any
) -> tuple[dict[str, Any], channel.FakeAgentState, str]:
    with tempfile.TemporaryDirectory(prefix="vibecad-workflow-harness-") as temp:
        token = secrets.token_hex(24)
        server, base_url, state = channel.start_fake_channel(temp, token)
        try:
            client = channel.AgentClickChannel(base_url, token, timeout_seconds=5)
            report = harness.run_harness(
                workflows=workflows,
                client=client,
                timeout_seconds=5,
                judge_enabled=kwargs.get("judge_enabled", False),
                judge_transport=kwargs.get("judge_transport"),
                export_path=str(Path(temp) / "vibecad-workflow-harness.step"),
            )
            return report, state, base_url
        finally:
            server.shutdown()
            server.server_close()


def main() -> int:
    os.environ.pop("TYPESAFE_API_KEY", None)
    workflows = harness.load_workflows(TOOLS_DIR / "vibecad_workflows.json")
    scenarios = []

    report, _state, _base_url = _run_fake(workflows)
    workflow_ids = [item["id"] for item in report["workflows"]]
    new_step = report["workflows"][0]["steps"][0]
    new_click = new_step.get("click_response") or {}
    sketch_clicks = [
        str((step.get("click") or {}).get("text") or "")
        for item in report["workflows"]
        if item["id"] == "sketch_then_pad"
        for step in item["steps"]
    ]
    workflow_source = (TOOLS_DIR / "vibecad_workflows.json").read_text(encoding="utf-8")
    scenarios.append(
        scenario(
            "three_workflows_pass_without_typesafe_key",
            report["passed"] is True
            and workflow_ids == ["new_document", "sketch_then_pad", "export"]
            and all(item["passed"] for item in report["workflows"])
            and all(
                step.get("judge", {}).get("called") is False
                for item in report["workflows"]
                for step in item["steps"]
            ),
            {
                "passed": report["passed"],
                "workflow_ids": workflow_ids,
            },
        )
    )
    scenarios.append(
        scenario(
            "workflows_use_visible_ribbon_and_part_design_commands",
            '"kind": "menu"' not in workflow_source
            and "PartDesign_NewBody" in sketch_clicks
            and "Sketcher_NewSketch" in sketch_clicks
            and "OK" in sketch_clicks
            and "leave_active_sketch" in workflow_source
            and "PartDesign_DesignExtrude" in sketch_clicks
            and "PartDesign_Pad" not in sketch_clicks,
            {"sketch_clicks": sketch_clicks},
        )
    )
    scenarios.append(
        scenario(
            "new_document_lands_when_focus_moves",
            new_step.get("passed") is True
            and new_click.get("ok") is False
            and new_click.get("failure_code") == "UI_CLICK_NOT_APPLIED"
            and new_click.get("focus_restored") is False
            and any(
                isinstance(item, dict) and item.get("active")
                for item in (new_step.get("documents") or {}).get("documents") or []
            ),
            {
                "errors": new_step.get("errors"),
                "failure_code": new_click.get("failure_code"),
                "focus_restored": new_click.get("focus_restored"),
            },
        )
    )

    tour_source = TOUR_SCRIPT.read_text(encoding="utf-8")
    harness_source = (TOOLS_DIR / "vibecad_workflow_harness.py").read_text(encoding="utf-8")
    channel_source = (TOOLS_DIR / "vibecad_workflow_channel.py").read_text(encoding="utf-8")
    scenarios.append(
        scenario(
            "reuses_tour_click_route_and_leaves_tour_as_demo",
            "/v1/ui/click" in tour_source
            and "Invoke-VibeCADAgentPost" in tour_source
            and report["click_route"] == "/v1/ui/click"
            and "tour_remains_demo" in harness_source
            and "pyautogui" not in channel_source
            and "SetCursorPos" not in channel_source
            and "SendInput" not in channel_source,
            {"click_route": report["click_route"]},
        )
    )

    failing = json.loads(json.dumps(workflows))
    failing[0]["steps"][-1]["check"]["document_count_min"] = 9
    failed_report, _state, _base_url = _run_fake(failing[:1])
    scenarios.append(
        scenario(
            "failed_tree_or_document_check_fails_the_workflow",
            failed_report["passed"] is False
            and failed_report["workflows"][0]["passed"] is False
            and any(
                "document_count_min" in error
                for error in failed_report["workflows"][0]["steps"][-1]["errors"]
            ),
            {"errors": failed_report["workflows"][0]["steps"][-1]["errors"]},
        )
    )

    refused = False
    try:
        channel.require_loopback_url("https://example.invalid/v1")
    except ValueError:
        refused = True
    scenarios.append(
        scenario(
            "refuses_non_loopback_endpoints",
            refused,
            {},
        )
    )

    skipped = judge.judge_step({"code_passed": True}, enabled=True)
    disabled = judge.judge_step({"code_passed": True}, enabled=False)
    low_confidence = judge.interpret_judge(
        {
            "model": "jev-latest",
            "answers": {
                "landed": {"type": "noul", "noul": 0.91},
                "failure_class": {
                    "type": "choice",
                    "choice": "none",
                    "confidence": 0.12,
                },
            },
        }
    )
    high_confidence = judge.interpret_judge(
        {
            "model": "jev-latest",
            "answers": {
                "landed": {"type": "noul", "noul": 0.91},
                "failure_class": {
                    "type": "choice",
                    "choice": "none",
                    "confidence": 0.88,
                },
            },
        }
    )
    scenarios.append(
        scenario(
            "judge_skips_without_key_and_low_confidence_is_not_pass",
            skipped.get("skipped") == "typesafe_key_absent"
            and disabled.get("skipped") == "judge_disabled"
            and low_confidence["counts_as_pass"] is False
            and high_confidence["counts_as_pass"] is True,
            {
                "skipped": skipped,
                "low_confidence": low_confidence,
                "high_confidence": high_confidence,
            },
        )
    )

    judged_report, _state, _base_url = _run_fake(
        workflows[:1],
        judge_enabled=True,
        judge_transport=lambda _state, _key: {
            "model": "mock-jev",
            "answers": {
                "landed": {"type": "noul", "noul": 0.8},
                "failure_class": {
                    "type": "choice",
                    "choice": "none",
                    "confidence": 0.9,
                },
            },
        },
    )
    first_judge = judged_report["workflows"][0]["steps"][0]["judge"]
    scenarios.append(
        scenario(
            "optional_judge_can_use_a_mock_transport",
            judged_report["passed"] is True
            and first_judge.get("called") is True
            and first_judge.get("counts_as_pass") is True,
            {"judge": first_judge},
        )
    )

    sketch_workflow = next(
        item for item in report["workflows"] if item["id"] == "sketch_then_pad"
    )
    export_workflow = next(
        item for item in report["workflows"] if item["id"] == "export"
    )
    sketch_step_ids = [str(step.get("id") or "") for step in sketch_workflow["steps"]]
    extrude_step = next(
        step for step in sketch_workflow["steps"] if step.get("id") == "click_extrude"
    )
    accept_step = next(
        step
        for step in sketch_workflow["steps"]
        if step.get("id") == "accept_design_task"
    )
    place_step = next(
        step
        for step in sketch_workflow["steps"]
        if step.get("id") == "place_closed_profile"
    )
    export_step = next(
        step for step in export_workflow["steps"] if step.get("id") == "export_step"
    )
    extrude_type_ids = {
        str(item.get("type_id") or "")
        for item in (extrude_step.get("tree") or {}).get("objects") or []
        if isinstance(item, dict)
    }
    accept_type_ids = {
        str(item.get("type_id") or "")
        for item in (accept_step.get("tree") or {}).get("objects") or []
        if isinstance(item, dict)
    }
    export_result = (export_step.get("click_response") or {}).get("result") or {}
    exported_path = str(export_result.get("exported_path") or "")
    export_bytes = int(export_result.get("bytes") or 0)
    pad_while_editing = None
    model_while_editing = None
    rectangle_while_editing = None
    with tempfile.TemporaryDirectory(prefix="vibecad-workflow-harness-") as temp:
        token = secrets.token_hex(24)
        server, base_url, _state = channel.start_fake_channel(temp, token)
        try:
            client = channel.AgentClickChannel(base_url, token, timeout_seconds=5)
            client.click("action", "Std_New")
            client.click("ribbon", "Model")
            client.click("action", "PartDesign_NewBody")
            client.click("action", "Sketcher_NewSketch")
            client.click("dialog", "OK")
            pad_while_editing = client.click("action", "PartDesign_Pad")
            model_while_editing = client.click("ribbon", "Model")
            rectangle_while_editing = client.click("action", "Sketcher_CreateRectangle")
            after_rectangle = client.inspect_tree()
            leave = client.click("action", "Sketcher_LeaveSketch")
            empty_extrude = client.click("action", "PartDesign_DesignExtrude")
            empty_tree = client.inspect_tree()
            placed = client.run(channel.workflow_run_python("place_closed_circle"))
            disabled_after_place = client.click("action", "PartDesign_DesignExtrude")
            selected = client.run(channel.workflow_run_python("select_sketch"))
            filled_extrude = client.click("action", "PartDesign_DesignExtrude")
            filled_tree = client.inspect_tree()
            export_before_accept = client.run(
                channel.workflow_run_python(
                    "export_step",
                    export_path=str(Path(temp) / "before-accept.step"),
                )
            )
            accepted_task = client.run(
                channel.workflow_run_python("accept_design_task")
            )
            after_accept = client.inspect_tree()
            export_after_accept = client.run(
                channel.workflow_run_python(
                    "export_step",
                    export_path=str(Path(temp) / "after-accept.step"),
                )
            )
        finally:
            server.shutdown()
            server.server_close()
    after_rectangle_objects = (
        (after_rectangle.get("result") or {}).get("objects") or []
    )
    empty_type_ids = {
        str(item.get("type_id") or "")
        for item in ((empty_tree.get("result") or {}).get("objects") or [])
        if isinstance(item, dict)
    }
    filled_type_ids = {
        str(item.get("type_id") or "")
        for item in ((filled_tree.get("result") or {}).get("objects") or [])
        if isinstance(item, dict)
    }
    after_accept_type_ids = {
        str(item.get("type_id") or "")
        for item in ((after_accept.get("result") or {}).get("objects") or [])
        if isinstance(item, dict)
    }
    rectangle_added_profile = any(
        isinstance(item, dict) and item.get("closed_profile")
        for item in after_rectangle_objects
    )
    scenarios.append(
        scenario(
            "closed_profile_then_extrude_creates_solid_and_export_writes_file",
            sketch_step_ids
            == [
                "select_model_ribbon",
                "click_body",
                "click_sketch",
                "accept_sketch_orientation",
                "leave_sketch",
                "place_closed_profile",
                "select_sketch",
                "click_extrude",
                "accept_design_task",
            ]
            and place_step.get("passed") is True
            and extrude_step.get("passed") is True
            and accept_step.get("passed") is True
            and "PartDesign::DesignExtrude" in extrude_type_ids
            and "PartDesign::DesignBodyPublication" not in extrude_type_ids
            and "PartDesign::DesignBodyPublication" in accept_type_ids
            and export_step.get("passed") is True
            and exported_path.endswith(".step")
            and export_bytes >= 1
            and export_result.get("type_id") == "PartDesign::DesignBodyPublication"
            and int(export_result.get("face_count") or 0) >= 1
            and "place_closed_circle" in channel_source
            and "leave_active_sketch" in channel_source
            and "leaveActiveSketch" in channel_source
            and "accept_design_task" in channel_source
            and "QDialogButtonBox" in channel_source
            and "Part.Circle" in channel_source
            and "addGeometry" in channel_source
            and "Import.export" in channel_source
            and "exportStep" in channel_source,
            {
                "sketch_step_ids": sketch_step_ids,
                "extrude_type_ids": sorted(extrude_type_ids),
                "accept_type_ids": sorted(accept_type_ids),
                "exported_path": exported_path,
                "export_bytes": export_bytes,
                "export_type_id": export_result.get("type_id"),
            },
        )
    )
    scenarios.append(
        scenario(
            "empty_sketch_and_rectangle_handler_do_not_create_a_solid",
            pad_while_editing.get("failure_code") == "UI_TARGET_NOT_UNIQUE"
            and "found 0" in str(pad_while_editing.get("error") or "")
            and model_while_editing.get("failure_code") == "UI_TARGET_DISABLED"
            and rectangle_while_editing.get("ok") is True
            and rectangle_while_editing.get("object_name") == "Sketcher_CreateRectangle"
            and rectangle_added_profile is False
            and leave.get("ok") is True
            and leave.get("object_name") == "Sketcher_LeaveSketch"
            and empty_extrude.get("ok") is True
            and empty_extrude.get("error") == "Linked shape object is empty"
            and "PartDesign::DesignExtrude" not in empty_type_ids
            and "Sketcher::SketchObject" in empty_type_ids
            and placed.get("ok") is True
            and int((placed.get("result") or {}).get("geometry_count") or 0) >= 1
            and disabled_after_place.get("failure_code") == "UI_TARGET_DISABLED"
            and selected.get("ok") is True
            and (selected.get("result") or {}).get("sub") == "InternalFace1"
            and (selected.get("result") or {}).get("command_active") is True
            and filled_extrude.get("ok") is True
            and not filled_extrude.get("error")
            and "PartDesign::DesignExtrude" in filled_type_ids
            and "PartDesign::DesignBodyPublication" not in filled_type_ids
            and export_before_accept.get("ok") is False
            and "Faces" in str(export_before_accept.get("error") or "")
            and accepted_task.get("ok") is True
            and (accepted_task.get("result") or {}).get("type_id")
            == "PartDesign::DesignBodyPublication"
            and "PartDesign::DesignBodyPublication" in after_accept_type_ids
            and export_after_accept.get("ok") is True
            and (export_after_accept.get("result") or {}).get("type_id")
            == "PartDesign::DesignBodyPublication"
            and int((export_after_accept.get("result") or {}).get("face_count") or 0)
            >= 1,
            {
                "pad_while_editing": pad_while_editing.get("failure_code"),
                "model_while_editing": model_while_editing.get("failure_code"),
                "rectangle_added_profile": rectangle_added_profile,
                "empty_extrude_error": empty_extrude.get("error"),
                "disabled_after_place": disabled_after_place.get("failure_code"),
                "selected_sub": (selected.get("result") or {}).get("sub"),
                "empty_type_ids": sorted(empty_type_ids),
                "filled_type_ids": sorted(filled_type_ids),
                "export_before_accept": export_before_accept.get("error"),
                "after_accept_type_ids": sorted(after_accept_type_ids),
                "export_after_type": (export_after_accept.get("result") or {}).get(
                    "type_id"
                ),
            },
        )
    )

    recovery_ok = None
    dismissed = None
    with tempfile.TemporaryDirectory(prefix="vibecad-workflow-harness-") as temp:
        token = secrets.token_hex(24)
        server, base_url, _state = channel.start_fake_channel(
            temp, token, recovery_dialog=True
        )
        try:
            client = channel.AgentClickChannel(base_url, token, timeout_seconds=5)
            client.click("action", "Std_New")
            client.click("ribbon", "Model")
            client.click("action", "PartDesign_NewBody")
            client.click("action", "Sketcher_NewSketch")
            recovery_ok = client.click("dialog", "OK")
            dismissed = client.run(
                channel.workflow_run_python("dismiss_document_recovery")
            )
            after_cancel = client.click("dialog", "OK")
        finally:
            server.shutdown()
            server.server_close()
    scenarios.append(
        scenario(
            "document_recovery_is_dismissed_with_cancel_not_start_recovery",
            recovery_ok.get("failure_code") == "UI_TARGET_NOT_UNIQUE"
            and "found 2" in str(recovery_ok.get("error") or "")
            and dismissed.get("ok") is True
            and (dismissed.get("result") or {}).get("button") == "Cancel"
            and (dismissed.get("result") or {}).get("dismissed") == ["Cancel"]
            and after_cancel.get("ok") is True
            and after_cancel.get("object_name") == "Choose Orientation",
            {
                "recovery_ok": recovery_ok.get("failure_code"),
                "dismissed": dismissed.get("result"),
                "after_cancel": after_cancel.get("object_name"),
            },
        )
    )

    failed = [item for item in scenarios if item["result"] != "pass"]
    payload = {
        "schema": "vibecad-workflow-harness-selftest-v1",
        "ok": not failed,
        "scenario_count": len(scenarios),
        "failed_scenarios": [item["name"] for item in failed],
        "scenarios": scenarios,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
