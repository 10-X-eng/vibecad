# SPDX-License-Identifier: LGPL-2.1-or-later

"""Clean-profile GUI acceptance gate for the Engineering Brief window."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback

source_root = str(os.environ.get("VIBECAD_TEST_SOURCE_ROOT") or "").strip()
if source_root:
    sys.path.insert(0, source_root)

import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADEngineeringBrief import (
    new_engineering_brief,
    parse_engineering_brief_result,
)
from VibeCADEngineeringBriefGui import EngineeringBriefDialog


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    main_thread = threading.get_ident()
    dialog = None
    poll_timer = QtCore.QTimer()
    tick_timer = QtCore.QTimer()
    tick_count = 0
    persisted: list[dict] = []
    started: list[tuple[dict, str]] = []
    exit_code = 1
    turn_count = 0
    phase = 0

    def finish(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        poll_timer.stop()
        tick_timer.stop()
        if dialog is not None:
            dialog.close()
        application.exit(exit_code)

    try:
        identity = {
            "project_root": "unused-by-injected-store",
            "document_uid": "brief-gui-document",
            "conversation_id": "d" * 32,
        }
        context = {
            "workbench": "PartDesignWorkbench",
            "units": {"schema": 0, "length_example": "1.00 mm"},
            "document": {
                "name": "BriefGuiDocument",
                "uid": "brief-gui-document",
                "object_count": 1,
            },
            "selection": {
                "selection_count": 1,
                "selection": [{"object": "Body", "label": "Bracket"}],
            },
        }
        state = new_engineering_brief(
            "Design a motor bracket.",
            identity=identity,
            context=context,
        )
        payload = {
            "assistant_message": "What vertical service load must it support?",
            "next_question": "What vertical service load must it support?",
            "ready": False,
            "brief": {
                "objective": "Design a motor bracket.",
                "deliverables": ["Editable 3D model"],
                "existing_geometry": ["Use selected Bracket body"],
                "units": "mm, N, MPa",
                "dimensions": [],
                "materials": [],
                "interfaces": [],
                "loads": [],
                "manufacturing": [],
                "tolerances": [],
                "analyses": [],
                "acceptance_criteria": [],
                "requirements": ["Remain editable"],
                "preferences": [],
            },
            "assumptions": [],
            "open_questions": ["Vertical service load"],
        }
        ready_payload = json.loads(json.dumps(payload))
        ready_payload.update(
            {
                "assistant_message": "The engineering brief is ready for review.",
                "next_question": "",
                "ready": True,
                "open_questions": [],
            }
        )
        ready_payload["brief"]["loads"] = ["1.5 kN vertical service load"]

        def turn_runner(prior, *, user_response, **_kwargs):
            nonlocal turn_count
            assert threading.get_ident() != main_thread
            turn_count += 1
            return parse_engineering_brief_result(
                json.dumps(payload if turn_count == 1 else ready_payload),
                prior_state=prior,
                user_response=user_response,
            )

        def persist(snapshot):
            assert threading.get_ident() != main_thread
            time.sleep(0.05)
            persisted.append(dict(snapshot))

        def start(snapshot, readable):
            assert threading.get_ident() == main_thread
            started.append((dict(snapshot), readable))
            return True

        dialog = EngineeringBriefDialog(
            state,
            turn_runner=turn_runner,
            persist_callback=persist,
            start_callback=start,
            parent=Gui.getMainWindow(),
        )
        dialog.show()
        assert dialog.isModal() is False
        assert dialog.windowTitle() == "VibeCAD Engineering Brief"
        assert dialog.request_edit.toPlainText() == "Design a motor bracket."
        assert "Objective" in dialog.preview.toPlainText()
        assert "Design a motor bracket." in dialog.preview.toPlainText()
        assert dialog.pages.currentIndex() == 0
        assert dialog.primary_button.text() == "Build My Brief"
        assert dialog.primary_button.isEnabled()
        assert dialog.primary_button.isDefault()

        def tick() -> None:
            nonlocal tick_count
            tick_count += 1

        def poll() -> None:
            nonlocal phase
            try:
                if phase == 0:
                    if dialog.state.get("next_question") != payload["next_question"]:
                        return
                    assert tick_count >= 1
                    assert dialog.pages.currentIndex() == 1
                    assert dialog.primary_button.text() == "Submit Answer"
                    assert not dialog.primary_button.isEnabled()
                    assert not dialog.primary_button.isDefault()
                    assert dialog.best_judgment_button.isVisible()
                    assert payload["next_question"] in dialog.question_label.text()
                    dialog.answer_edit.setPlainText("Use a 1.5 kN service load.")
                    assert dialog.primary_button.isEnabled()
                    assert dialog.primary_button.isDefault()
                    dialog.primary_button.click()
                    assert not dialog.primary_button.isEnabled()
                    assert not dialog.primary_button.isDefault()
                    phase = 1
                    return
                if not dialog.state.get("ready"):
                    return
                assert dialog.pages.currentIndex() == 2
                assert dialog.primary_button.text() == "Start CAD Work"
                assert dialog.primary_button.isEnabled()
                assert dialog.primary_button.isDefault()
                assert not dialog.preview.isReadOnly()
                assert not dialog.best_judgment_button.isVisible()
                dialog.preview.appendPlainText("\nHuman review: prioritize stiffness.")
                dialog.primary_button.click()
                assert len(started) == 1
                assert "Human review: prioritize stiffness." in started[0][1]
                assert not dialog._persist_thread.is_alive()
                assert persisted
                print("VIBECAD_ENGINEERING_BRIEF_GUI_OK", flush=True)
                finish(0)
            except Exception:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        tick_timer.timeout.connect(tick)
        tick_timer.start(10)
        poll_timer.timeout.connect(poll)
        poll_timer.start(20)
        dialog.primary_button.click()
        assert not dialog.primary_button.isEnabled()
        assert not dialog.primary_button.isDefault()
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
