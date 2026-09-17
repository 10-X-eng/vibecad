# SPDX-License-Identifier: LGPL-2.1-or-later
"""Click-channel workflows for New document, Sketch then pad, and Export.

This is a CI/operator harness, not the Windows visible tour. Clicks go through
the same authenticated ``/v1/ui/click`` channel used by
``Invoke-VibeCAD-VisibleTour.ps1``. The harness never moves the OS cursor.
``Invoke-VibeCAD-VisibleTour.ps1`` remains the human-watchable demo.

Optional Jev classification is default-off and never overrides code pass/fail.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol

import VibeCADAgentCli as cli
import VibeCADJevJudge as jev


WORKFLOW_NAMES = ("new_document", "sketch_then_pad", "export")
INSPECT_OBJECTS_PYTHON = """
doc = App.ActiveDocument
result = []
if doc is not None:
    result = [
        {
            "name": str(getattr(obj, "Name", "") or ""),
            "label": str(getattr(obj, "Label", "") or ""),
            "type_id": str(getattr(obj, "TypeId", "") or ""),
        }
        for obj in list(getattr(doc, "Objects", []) or [])
    ]
"""


class ClickChannel(Protocol):
    def click(self, kind: str, text: str) -> dict[str, Any]:
        """Activate one semantic target through the existing click channel."""

    def documents(self) -> dict[str, Any]:
        """Return the current document listing."""

    def inspect_objects(self) -> list[dict[str, Any]]:
        """Return active-document objects for post-click assertions."""


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    kind: str
    text: str
    document_count_delta: int | None = None
    selected_after: str | None = None
    require_type_ids: tuple[str, ...] = ()
    require_active_document: bool = False
    require_command_id: str | None = None


NEW_DOCUMENT_STEP = WorkflowStep(
    name="new_document",
    kind="command",
    text="VibeCADRibbonNew",
    document_count_delta=1,
    require_active_document=True,
    require_command_id="Std_New",
)

WORKFLOWS: dict[str, tuple[WorkflowStep, ...]] = {
    "new_document": (NEW_DOCUMENT_STEP,),
    "sketch_then_pad": (
        NEW_DOCUMENT_STEP,
        WorkflowStep(
            name="select_model_ribbon",
            kind="ribbon",
            text="Model",
            selected_after="Model",
        ),
        WorkflowStep(
            name="create_sketch",
            kind="command",
            text="Sketcher_NewSketch",
            require_type_ids=("Sketcher::SketchObject",),
            require_command_id="Sketcher_NewSketch",
        ),
        WorkflowStep(
            name="create_pad",
            kind="command",
            text="PartDesign_Pad",
            require_type_ids=("Sketcher::SketchObject", "PartDesign::Pad"),
            require_command_id="PartDesign_Pad",
        ),
    ),
    "export": (
        NEW_DOCUMENT_STEP,
        WorkflowStep(
            name="export",
            kind="command",
            text="Std_Export",
            require_active_document=True,
            require_command_id="Std_Export",
        ),
    ),
}


class AgentClickChannel:
    """Live or in-process adapter over ``VibeCADAgentCli`` / ``/v1/ui/click``."""

    def __init__(self, *, timeout_seconds: float = 30.0, gui_only: bool = False) -> None:
        self.timeout_seconds = timeout_seconds
        self.gui_only = gui_only

    def _call(self, command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = argparse.Namespace(
            command=command,
            local=False,
            gui_only=self.gui_only,
            timeout=self.timeout_seconds,
            kind=None,
            text=None,
            expected_process_id=None,
            expected_index=None,
            path=None,
            document=None,
            overwrite=False,
            discard_unsaved=False,
            script=None,
            python=None,
            no_recompute=False,
        )
        for key, value in dict(arguments or {}).items():
            setattr(args, key, value)
        payload = cli.execute(args)
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "failure_code": "CHANNEL_INVALID_RESPONSE",
                "error": "The click channel did not return a JSON object.",
            }
        return payload

    def click(self, kind: str, text: str) -> dict[str, Any]:
        return self._call("ui-click", {"kind": kind, "text": text})

    def documents(self) -> dict[str, Any]:
        return self._call("documents")

    def inspect_objects(self) -> list[dict[str, Any]]:
        payload = self._call("run", {"python": INSPECT_OBJECTS_PYTHON, "no_recompute": True})
        result = payload.get("result")
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []


class FakeClickChannel:
    """Headless stand-in that uses the same click/document/object shapes."""

    def __init__(self) -> None:
        self.documents_list: list[dict[str, Any]] = []
        self.objects: list[dict[str, Any]] = []
        self.selected_ribbon = "Start"
        self.export_activated = False
        self.clicks: list[tuple[str, str]] = []

    def click(self, kind: str, text: str) -> dict[str, Any]:
        self.clicks.append((kind, text))
        if kind == "ribbon":
            self.selected_ribbon = text
            return {
                "ok": True,
                "target_kind": "ribbon",
                "target_text": text,
                "selected_after": text,
                "semantic_verified": True,
                "input_method": "qt_in_process_mouse_click",
                "physical_cursor_control": "none",
            }
        if text in {"VibeCADRibbonNew", "Std_New", "New document"}:
            name = f"Unnamed{len(self.documents_list)}"
            self.documents_list.append(
                {
                    "document": name,
                    "label": name,
                    "path": "",
                    "active": True,
                    "object_count": len(self.objects),
                    "modified": True,
                }
            )
            for item in self.documents_list[:-1]:
                item["active"] = False
            return {
                "ok": True,
                "target_kind": "command",
                "target_text": text,
                "command_id": "Std_New",
                "semantic_verified": True,
                "input_method": "qt_in_process_mouse_click",
                "physical_cursor_control": "none",
            }
        if text == "Sketcher_NewSketch":
            self.objects.append(
                {
                    "name": "Sketch",
                    "label": "Sketch",
                    "type_id": "Sketcher::SketchObject",
                }
            )
            return {
                "ok": True,
                "target_kind": "command",
                "target_text": text,
                "command_id": "Sketcher_NewSketch",
                "semantic_verified": True,
                "input_method": "qt_in_process_mouse_click",
                "physical_cursor_control": "none",
            }
        if text == "PartDesign_Pad":
            self.objects.append(
                {
                    "name": "Pad",
                    "label": "Pad",
                    "type_id": "PartDesign::Pad",
                }
            )
            return {
                "ok": True,
                "target_kind": "command",
                "target_text": text,
                "command_id": "PartDesign_Pad",
                "semantic_verified": True,
                "input_method": "qt_in_process_mouse_click",
                "physical_cursor_control": "none",
            }
        if text == "Std_Export":
            self.export_activated = True
            return {
                "ok": True,
                "target_kind": "command",
                "target_text": text,
                "command_id": "Std_Export",
                "semantic_verified": True,
                "input_method": "qt_in_process_action_trigger",
                "physical_cursor_control": "none",
            }
        return {
            "ok": False,
            "failure_code": "UI_TARGET_NOT_UNIQUE",
            "error": f"Fake channel has no target {text!r}.",
            "semantic_verified": False,
        }

    def documents(self) -> dict[str, Any]:
        return {
            "ok": True,
            "document_count": len(self.documents_list),
            "documents": list(self.documents_list),
        }

    def inspect_objects(self) -> list[dict[str, Any]]:
        return list(self.objects)


@dataclass
class StepResult:
    name: str
    passed: bool
    click: dict[str, Any]
    documents_before: dict[str, Any]
    documents_after: dict[str, Any]
    objects: list[dict[str, Any]]
    errors: list[str] = field(default_factory=list)
    judge: dict[str, Any] | None = None


def _type_ids(objects: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("type_id") or "")
        for item in objects
        if str(item.get("type_id") or "")
    }


def _active_documents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    documents = payload.get("documents")
    if not isinstance(documents, list):
        return []
    return [item for item in documents if isinstance(item, dict) and item.get("active")]


def assert_step_state(
    step: WorkflowStep,
    *,
    click: dict[str, Any],
    documents_before: dict[str, Any],
    documents_after: dict[str, Any],
    objects: list[dict[str, Any]],
) -> list[str]:
    """Reuse the tour's semantic-target + post-click checks for one step."""

    errors: list[str] = []
    if click.get("ok") is not True:
        errors.append(
            f"{step.name}: click channel failed: "
            f"{click.get('failure_code') or click.get('error') or click}"
        )
        return errors
    if click.get("semantic_verified") is not True:
        errors.append(f"{step.name}: click was not semantically verified.")
    if click.get("physical_cursor_control") not in {None, "none"}:
        errors.append(f"{step.name}: click channel reported physical cursor control.")
    if step.selected_after and click.get("selected_after") != step.selected_after:
        errors.append(
            f"{step.name}: selected_after was {click.get('selected_after')!r}, "
            f"expected {step.selected_after!r}."
        )
    if step.require_command_id:
        command_id = str(click.get("command_id") or click.get("target_text") or "")
        if command_id != step.require_command_id and click.get("target_text") != step.text:
            errors.append(
                f"{step.name}: command_id was {command_id!r}, "
                f"expected {step.require_command_id!r}."
            )
    if step.document_count_delta is not None:
        before = int(documents_before.get("document_count") or 0)
        after = int(documents_after.get("document_count") or 0)
        if after - before != step.document_count_delta:
            errors.append(
                f"{step.name}: document_count delta was {after - before}, "
                f"expected {step.document_count_delta}."
            )
    if step.require_active_document and not _active_documents(documents_after):
        errors.append(f"{step.name}: no active document after the click.")
    missing = [type_id for type_id in step.require_type_ids if type_id not in _type_ids(objects)]
    if missing:
        errors.append(f"{step.name}: missing document objects {missing!r}.")
    return errors


def run_workflow(
    name: str,
    channel: ClickChannel,
    *,
    judge_asker: Any = None,
    judge_environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    if name not in WORKFLOWS:
        raise ValueError(f"Unknown workflow {name!r}; expected one of {list(WORKFLOWS)}.")

    steps: list[StepResult] = []
    for step in WORKFLOWS[name]:
        documents_before = channel.documents()
        click = channel.click(step.kind, step.text)
        documents_after = channel.documents()
        objects = channel.inspect_objects()
        errors = assert_step_state(
            step,
            click=click,
            documents_before=documents_before,
            documents_after=documents_after,
            objects=objects,
        )
        code_passed = not errors
        judge = jev.classify_step(
            {
                "workflow": name,
                "step": step.name,
                "kind": step.kind,
                "text": step.text,
                "click": click,
                "documents_before": documents_before,
                "documents_after": documents_after,
                "objects": objects,
                "code_passed": code_passed,
                "errors": errors,
            },
            asker=judge_asker,
            environ=judge_environ,
        )
        passed = jev.cannot_flip_green(code_passed, judge)
        steps.append(
            StepResult(
                name=step.name,
                passed=passed,
                click=click,
                documents_before=documents_before,
                documents_after=documents_after,
                objects=objects,
                errors=errors,
                judge=judge,
            )
        )
        if not passed:
            break

    return {
        "ok": all(step.passed for step in steps) and len(steps) == len(WORKFLOWS[name]),
        "workflow": name,
        "channel": "vibecad-agent-control",
        "demo": "Invoke-VibeCAD-VisibleTour.ps1 remains the visible demo tour.",
        "completed_steps": sum(1 for step in steps if step.passed),
        "step_count": len(WORKFLOWS[name]),
        "steps": [
            {
                "name": step.name,
                "ok": step.passed,
                "errors": step.errors,
                "click": step.click,
                "document_count_before": step.documents_before.get("document_count"),
                "document_count_after": step.documents_after.get("document_count"),
                "objects": step.objects,
                "judge": step.judge,
            }
            for step in steps
        ],
    }


def run_all_workflows(
    channel: ClickChannel,
    *,
    judge_asker: Any = None,
    judge_environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    reports = [
        run_workflow(
            name,
            channel,
            judge_asker=judge_asker,
            judge_environ=judge_environ,
        )
        for name in WORKFLOW_NAMES
    ]
    return {
        "ok": all(report["ok"] for report in reports),
        "workflows": reports,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow",
        choices=WORKFLOW_NAMES,
        help="Run one workflow. Default: all three.",
    )
    parser.add_argument(
        "--fake-channel",
        action="store_true",
        help="Use the in-process fake click channel (CI / no display).",
    )
    parser.add_argument(
        "--gui-only",
        action="store_true",
        help="Fail if the live GUI loopback click channel is not listening.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.fake_channel:
        channel: ClickChannel = FakeClickChannel()
    else:
        channel = AgentClickChannel(timeout_seconds=args.timeout, gui_only=args.gui_only)
    if args.workflow:
        report = run_workflow(args.workflow, channel)
    else:
        report = run_all_workflows(channel)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
