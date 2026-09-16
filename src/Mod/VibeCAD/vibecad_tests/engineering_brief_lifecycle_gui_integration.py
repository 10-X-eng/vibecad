# SPDX-License-Identifier: LGPL-2.1-or-later

"""Engineering Brief lifecycle tests run against real Qt widgets."""

import copy
import threading
import time
import unittest

from PySide import QtCore, QtWidgets

from VibeCADEngineeringBrief import new_engineering_brief
from VibeCADEngineeringBriefGui import EngineeringBriefDialog


class EngineeringBriefLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance()
        self.dialogs = []
        self.releases = []

    def wait_until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.001)
        self.assertTrue(predicate(), "Qt operation did not complete")

    def tearDown(self):
        for event in self.releases:
            event.set()
        for dialog in self.dialogs:
            try:
                dialog.close()
            except RuntimeError:
                pass
            if dialog._persist_thread.is_alive():
                dialog._persist_queue.put(None)
            dialog._persist_thread.join(3)
            if dialog._turn_thread is not None:
                dialog._turn_thread.join(3)
        self.app.processEvents()

    def dialog(self, persist, turn_runner=None):
        state = new_engineering_brief(
            "Make a bracket",
            {
                "project_root": "synthetic",
                "document_uid": "brief-test",
                "conversation_id": "a" * 32,
            },
            {},
        )
        dialog = EngineeringBriefDialog(
            state,
            persist_callback=persist,
            turn_runner=turn_runner or (lambda state, **kwargs: state),
            start_callback=lambda state, readable: True,
        )
        self.dialogs.append(dialog)
        dialog.show()
        return dialog

    def test_close_keeps_event_loop_responsive_until_final_save(self):
        saving = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        persisted = []

        def persist(state):
            saving.set()
            # Bound only the synthetic I/O stall so a regression can fail normally.
            release.wait(0.4)
            persisted.append(copy.deepcopy(state))

        dialog = self.dialog(persist)
        self.assertTrue(saving.wait(1))
        dialog.request_edit.setPlainText("Final human edit")
        started = time.monotonic()
        dialog.close()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.1, "Closing blocked Qt on disk I/O")
        self.assertTrue(dialog.isVisible(), "Keep the draft alive until saved")
        self.assertTrue(dialog._cancel_event.is_set())

        # A queued GUI event releases I/O: it cannot run if close blocks Qt.
        class ReleaseIO(QtCore.QObject):
            requested = QtCore.Signal()

        bridge = ReleaseIO()
        bridge.requested.connect(release.set, QtCore.Qt.QueuedConnection)
        bridge.requested.emit()
        self.wait_until(lambda: release.is_set())
        self.wait_until(lambda: not dialog._persist_thread.is_alive())
        self.assertEqual(persisted[-1]["original_request"], "Final human edit")

    def test_escape_saves_edits_and_cancels_provider(self):
        persisted = []
        cancelled = threading.Event()

        def turn(state, *, cancellation_check, **kwargs):
            deadline = time.monotonic() + 1
            while not cancellation_check() and time.monotonic() < deadline:
                time.sleep(0.001)
            if cancellation_check():
                cancelled.set()
            return state

        dialog = self.dialog(lambda state: persisted.append(copy.deepcopy(state)), turn)
        dialog.request_edit.setPlainText("Saved by Escape")
        dialog.primary_button.click()
        # QDialog's Escape path invokes reject(), not closeEvent().
        dialog.reject()
        self.assertTrue(dialog._cancel_event.is_set())
        self.wait_until(lambda: cancelled.is_set())
        self.wait_until(lambda: not dialog._persist_thread.is_alive())
        self.assertEqual(persisted[-1]["original_request"], "Saved by Escape")

    def test_failed_final_save_keeps_draft_open_for_retry(self):
        fail = threading.Event()
        fail.set()
        persisted = []

        def persist(state):
            if fail.is_set():
                raise OSError("test disk unavailable")
            persisted.append(copy.deepcopy(state))

        dialog = self.dialog(persist)
        dialog.request_edit.setPlainText("Do not lose this edit")
        dialog.close()
        self.wait_until(lambda: "test disk unavailable" in dialog.status.text())
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog._closing)
        fail.clear()
        dialog.close()
        self.wait_until(lambda: not dialog._persist_thread.is_alive())
        self.assertEqual(persisted[-1]["original_request"], "Do not lose this edit")

    def test_evolving_brief_is_visible_while_answering_questions(self):
        dialog = self.dialog(lambda state: None)
        state = dialog.state
        state["next_question"] = "What load must it carry?"
        state["brief"]["loads"] = ["Initial service load: 1 kN"]
        state["editable_text"] = "Objective: make a bracket\nInitial service load: 1 kN"
        dialog._complete_turn(state)
        self.assertEqual(dialog.pages.currentIndex(), 1)
        self.assertTrue(dialog.preview.isVisible())
        self.assertIn("1 kN", dialog.preview.toPlainText())
        self.assertTrue(dialog.answer_edit.isVisible())

    def test_parent_destruction_stops_idle_writer_and_cancels_provider(self):
        parent = QtWidgets.QWidget()
        dialog = self.dialog(lambda state: None)
        dialog.setParent(parent)
        parent.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.wait_until(lambda: not dialog._persist_thread.is_alive())
        self.assertTrue(dialog._cancel_event.is_set())

    def test_guided_brief_roundtrip_leaves_native_document_unchanged(self):
        import json
        import tempfile
        from types import SimpleNamespace

        try:
            import FreeCAD as App
            import FreeCADGui as Gui
        except ImportError:
            self.skipTest("Requires the built VibeCAD GUI")
        from VibeCADEngineeringBrief import (
            EngineeringBriefStore,
            engineering_brief_handoff,
            run_engineering_brief_turn,
        )

        main_thread = threading.get_ident()
        document = App.newDocument("EngineeringBriefLifecycleTest")
        self.addCleanup(lambda: App.closeDocument(document.Name))
        box = document.addObject("Part::Box", "Box")
        document.recompute()
        Gui.Selection.addSelection(box)
        before = (
            box.Shape.Volume,
            str(box.Placement),
            tuple(o.Name for o in document.Objects),
        )
        handoffs = []
        calls = []
        document_name = document.Name

        class Provider:
            def run(self, prompt, context, **kwargs):
                assert threading.get_ident() != main_thread
                assert kwargs["tool_runner"] is None
                assert context["provider_tool_schemas"] == []
                assert context["document"]["name"] == document_name
                calls.append(prompt)
                ready = len(calls) == 2
                return SimpleNamespace(
                    final_output=json.dumps(
                        {
                            "assistant_message": (
                                "Ready" if ready else "What service load?"
                            ),
                            "next_question": "" if ready else "What service load?",
                            "ready": ready,
                            "brief": {
                                "objective": "Make a bracket",
                                "units": "mm",
                                "loads": ["1.5 kN"] if ready else [],
                            },
                            "assumptions": [],
                            "open_questions": [] if ready else ["Load"],
                        }
                    )
                )

        with tempfile.TemporaryDirectory(prefix="vibecad-brief-store-") as directory:
            store = EngineeringBriefStore(directory)
            state = new_engineering_brief(
                "Make a bracket",
                {
                    "project_root": directory,
                    "document_uid": str(document.Uid),
                    "conversation_id": "b" * 32,
                },
                {
                    "document": {"name": document.Name},
                    "selection": {"object": box.Name},
                },
            )
            dialog = EngineeringBriefDialog(
                state,
                turn_runner=lambda state, **kwargs: run_engineering_brief_turn(
                    state, provider=Provider(), **kwargs
                ),
                persist_callback=store.write,
                start_callback=lambda state, text: (
                    handoffs.append(
                        engineering_brief_handoff(state, approved_text=text)
                    )
                    or True
                ),
            )
            self.dialogs.append(dialog)
            dialog.show()
            self.assertFalse(dialog.isModal())
            dialog.primary_button.click()
            self.wait_until(
                lambda: dialog.state["next_question"] == "What service load?"
            )
            dialog.answer_edit.setPlainText("1.5 kN")
            dialog.primary_button.click()
            self.wait_until(lambda: dialog.state["ready"])
            self.assertEqual(dialog.primary_button.text(), "Start CAD Work")
            dialog.preview.appendPlainText(
                "Human requirement: reuse the mounting bolts."
            )
            dialog.primary_button.click()
            self.wait_until(lambda: not dialog._persist_thread.is_alive())
            loaded = store.load(
                document_uid=str(document.Uid), conversation_id="b" * 32
            )
            self.assertTrue(loaded["recoverable"])
            self.assertIn("reuse the mounting bolts", loaded["state"]["editable_text"])
            self.assertIn("reuse the mounting bolts", handoffs[0])
            self.assertIn("1.5 kN", handoffs[0])
            self.assertEqual(len(calls), 2)
            self.assertEqual(
                before,
                (
                    box.Shape.Volume,
                    str(box.Placement),
                    tuple(o.Name for o in document.Objects),
                ),
            )
            self.assertEqual(
                [item.Name for item in Gui.Selection.getSelection()], [box.Name]
            )
