# SPDX-License-Identifier: LGPL-2.1-or-later
"""Headless contracts for the click-channel workflow harness."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import VibeCADAgentCli as cli
import VibeCADAgentControl as control
import VibeCADJevJudge as jev
import VibeCADWorkflowHarness as harness


def test_cli_accepts_command_kind_on_the_existing_click_route() -> None:
    parsed = cli.build_parser().parse_args(
        ["ui-click", "--kind", "command", "--text", "VibeCADRibbonNew"]
    )
    assert cli._http_route(parsed.command) == ("POST", "/v1/ui/click")
    assert cli._command_arguments(parsed) == {
        "kind": "command",
        "text": "VibeCADRibbonNew",
        "expected_process_id": None,
        "expected_index": None,
    }


def test_command_click_uses_the_same_in_process_mouse_injection(monkeypatch) -> None:
    class Point:
        def __init__(self, x: int, y: int) -> None:
            self._x = x
            self._y = y

        def x(self) -> int:
            return self._x

        def y(self) -> int:
            return self._y

    class Rect:
        def center(self) -> Point:
            return Point(18, 10)

    class Button:
        def __init__(self) -> None:
            self.clicked = False

        def objectName(self) -> str:  # noqa: N802
            return "VibeCADRibbonNew"

        def toolTip(self) -> str:  # noqa: N802
            return "New document"

        def accessibleName(self) -> str:  # noqa: N802
            return "New document"

        def property(self, name: str):
            return "Std_New" if name == "VibeCADCommandId" else None

        def isEnabled(self) -> bool:  # noqa: N802
            return True

        def isVisible(self) -> bool:  # noqa: N802
            return True

        def rect(self) -> Rect:
            return Rect()

        def defaultAction(self):  # noqa: N802
            return None

    button = Button()
    window = SimpleNamespace(
        findChild=lambda *_args: None,
        findChildren=lambda _kind: [button],
        menuBar=lambda: SimpleNamespace(actions=lambda: []),
    )
    application = SimpleNamespace(focus=object(), active_window=window, popup=None)
    qt_core = SimpleNamespace(
        Qt=SimpleNamespace(
            LeftButton="left",
            NoModifier="none",
            OtherFocusReason="other",
        )
    )
    qt_gui = SimpleNamespace(QCursor=SimpleNamespace(pos=lambda: Point(4, 8)))
    qt_widgets = SimpleNamespace(
        QTabBar=object,
        QToolButton=Button,
        QApplication=SimpleNamespace(
            processEvents=lambda: None,
            focusWidget=lambda: application.focus,
            activeWindow=lambda: application.active_window,
            activePopupWidget=lambda: application.popup,
        ),
    )
    clicks: list[tuple[object, object, object, object]] = []

    class QTest:
        @staticmethod
        def mouseClick(widget, button_name, modifiers, point) -> None:  # noqa: N802
            clicks.append((widget, button_name, modifiers, point))
            widget.clicked = True

    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtCore=qt_core, QtGui=qt_gui, QtWidgets=qt_widgets),
    )
    monkeypatch.setitem(
        sys.modules,
        "PySide6",
        SimpleNamespace(QtTest=SimpleNamespace(QTest=QTest)),
    )
    monkeypatch.setattr(
        control,
        "_gui",
        lambda: SimpleNamespace(GuiUp=True, getMainWindow=lambda: window),
    )
    monkeypatch.setattr(control, "_document_thread_dispatch", lambda operation: operation())
    freecad = sys.modules["FreeCAD"]
    monkeypatch.setattr(freecad, "isRestoring", lambda: False, raising=False)

    payload = control.dispatch("ui_click", {"kind": "command", "text": "VibeCADRibbonNew"})
    assert payload["ok"] is True
    assert payload["semantic_verified"] is True
    assert payload["command_id"] == "Std_New"
    assert payload["input_method"] == "qt_in_process_mouse_click"
    assert payload["physical_cursor_control"] == "none"
    assert len(clicks) == 1
    assert button.clicked is True


def test_three_workflows_pass_on_the_fake_click_channel_without_a_key(
    monkeypatch,
) -> None:
    monkeypatch.delenv(jev.API_KEY_ENV, raising=False)
    monkeypatch.delenv(jev.JUDGE_FLAG_ENV, raising=False)
    report = harness.run_all_workflows(harness.FakeClickChannel())
    assert report["ok"] is True
    names = [item["workflow"] for item in report["workflows"]]
    assert names == ["new_document", "sketch_then_pad", "export"]
    sketch = next(item for item in report["workflows"] if item["workflow"] == "sketch_then_pad")
    type_ids = {
        obj["type_id"]
        for step in sketch["steps"]
        for obj in step["objects"]
    }
    assert "Sketcher::SketchObject" in type_ids
    assert "PartDesign::Pad" in type_ids
    for workflow in report["workflows"]:
        for step in workflow["steps"]:
            assert step["judge"]["skipped"] is True
            assert step["judge"]["used_for_pass_fail"] is False


def test_new_document_fails_when_the_channel_does_not_add_a_document() -> None:
    channel = harness.FakeClickChannel()

    def silent_new(kind: str, text: str) -> dict[str, Any]:
        if text in {"VibeCADRibbonNew", "Std_New", "New document"}:
            return {
                "ok": True,
                "target_kind": "command",
                "target_text": text,
                "command_id": "Std_New",
                "semantic_verified": True,
                "physical_cursor_control": "none",
            }
        return harness.FakeClickChannel.click(channel, kind, text)

    channel.click = silent_new  # type: ignore[method-assign]
    report = harness.run_workflow("new_document", channel)
    assert report["ok"] is False
    assert any("document_count" in error for error in report["steps"][0]["errors"])


def test_jev_is_optional_and_cannot_flip_a_green(monkeypatch) -> None:
    monkeypatch.setenv(jev.JUDGE_FLAG_ENV, "1")
    monkeypatch.setenv(jev.API_KEY_ENV, "test-key")

    def asker(_state):
        return {
            "answers": {
                "landed": {"type": "noul", "noul": 0.1},
                "failure_class": {
                    "type": "choice",
                    "choice": "model_lied",
                    "confidence": 0.2,
                },
                "progress": {"type": "score", "score": 0.0, "confidence": 0.1},
            }
        }

    report = harness.run_workflow(
        "new_document",
        harness.FakeClickChannel(),
        judge_asker=asker,
        judge_environ={
            jev.JUDGE_FLAG_ENV: "1",
            jev.API_KEY_ENV: "test-key",
        },
    )
    assert report["ok"] is True
    judge = report["steps"][0]["judge"]
    assert judge["skipped"] is False
    assert judge["low_confidence"] is True
    assert judge["used_for_pass_fail"] is False
    assert jev.cannot_flip_green(True, judge) is True


def test_jev_stays_skipped_in_ci_without_a_key(monkeypatch) -> None:
    monkeypatch.setenv(jev.JUDGE_FLAG_ENV, "1")
    monkeypatch.delenv(jev.API_KEY_ENV, raising=False)
    assert jev.judge_enabled() is False
    classification = jev.classify_step({"step": "new_document"})
    assert classification is not None
    assert classification["skipped"] is True
    assert classification["used_for_pass_fail"] is False


def test_harness_cli_fake_channel_exits_zero_without_typesafe(monkeypatch) -> None:
    monkeypatch.delenv(jev.API_KEY_ENV, raising=False)
    monkeypatch.delenv(jev.JUDGE_FLAG_ENV, raising=False)
    assert harness.main(["--fake-channel"]) == 0


def test_visible_tour_stays_the_demo() -> None:
    source = harness.__doc__ or ""
    assert "Invoke-VibeCAD-VisibleTour.ps1" in source
    assert "never moves the OS cursor" in source
