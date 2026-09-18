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
