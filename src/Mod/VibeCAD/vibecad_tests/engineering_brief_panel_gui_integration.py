# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exercise the built composer, document ownership, and durable brief recovery."""

import json
import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtWidgets
import VibeCADGui as gui
import VibeCADSession as session
from VibeCADPreferences import preferences


class TestBriefPanel(unittest.TestCase):
    def wait(self, condition):
        end = time.monotonic() + 5
        while not condition() and time.monotonic() < end:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.001)
        self.assertTrue(condition())

    def test_composer_owned_handoff_and_reopen(self):
        preferences().SetString("NewDocumentAuthoringMode", "native")
        Gui.activateWorkbench("PartDesignWorkbench")
        doc = App.newDocument("BriefPanelOwner")
        names = [doc.Name]
        self.addCleanup(
            lambda: [
                App.closeDocument(name) for name in names if name in App.listDocuments()
            ]
        )
        box = doc.addObject("Part::Box", "Box")
        doc.recompute()
        volume = box.Shape.Volume
        gui._show_panel()
        dock = gui._find_dock()
        prompt = dock.findChild(QtWidgets.QPlainTextEdit, "VibePrompt")
        prompt.setPlainText("Design a motor bracket for a 1.5 kN service load.")
        gui._open_engineering_brief_from_panel()
        dialog = gui._engineering_brief_dialog
        self.assertIsNotNone(dialog)
        self.addCleanup(
            lambda: dialog.close() if gui._engineering_brief_dialog is dialog else None
        )
        self.assertFalse(dialog.isModal())
        QtWidgets.QApplication.processEvents()
        artifact = Path(os.environ["VIBECAD_TEST_OUTPUT"])
        dialog.grab().save(str(artifact / "brief-request.png"))
        calls = []

        class Provider:
            def run(self, prompt, context, **kwargs):
                calls.append(context)
                return SimpleNamespace(
                    final_output=json.dumps(
                        {
                            "assistant_message": "The brief is ready for review.",
                            "next_question": "",
                            "ready": True,
                            "brief": {
                                "objective": "Design a motor bracket",
                                "units": "mm, N",
                                "loads": ["1.5 kN service load"],
                                "requirements": ["Remain editable"],
                            },
                            "assumptions": ["Indoor use"],
                            "open_questions": [],
                        }
                    )
                )

        with patch.object(session, "choose_provider", return_value=Provider()):
            dialog.primary_button.click()
            self.wait(lambda: dialog.state["ready"] or (not dialog._turn_active))
        self.assertTrue(dialog.state["ready"], dialog.status.text())
        self.assertEqual(calls[0]["provider_tool_schemas"], [])
        dialog.preview.appendPlainText("Human requirement: reuse M6 bolts.")
        QtWidgets.QApplication.processEvents()
        dialog.grab().save(str(artifact / "brief-review.png"))
        other = App.newDocument("BriefPanelOther")
        names.append(other.Name)
        with patch.object(gui, "_execute_assistant_run") as execute:
            dialog.primary_button.click()
            self.assertIn(
                "active document or conversation changed", dialog.status.text()
            )
            execute.assert_not_called()
            App.setActiveDocument(doc.Name)
            dialog.primary_button.click()
            self.assertEqual(execute.call_count, 1, dialog.status.text())
            self.assertIn("reuse M6 bolts", execute.call_args.kwargs["prompt"])
            self.wait(lambda: not dialog._persist_thread.is_alive())
            self.wait(lambda: gui._engineering_brief_dialog is None)
        self.assertEqual(box.Shape.Volume, volume)
        gui._open_engineering_brief_from_panel()
        reopened = gui._engineering_brief_dialog
        self.assertIsNotNone(reopened)
        self.assertTrue(reopened.state["ready"])
        self.assertIn("reuse M6 bolts", reopened.preview.toPlainText())
        reopened.close()
        self.wait(lambda: not reopened._persist_thread.is_alive())
        self.wait(lambda: gui._engineering_brief_dialog is None)
