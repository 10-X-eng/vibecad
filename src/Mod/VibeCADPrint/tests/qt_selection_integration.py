# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live regression for whole-body plus subelement print selection."""

from __future__ import annotations

import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    exit_code = 1
    document = None
    try:
        import PrintCommandLoader

        document = App.newDocument("VibeCADPrintSelectionTest")
        frame = document.addObject("PartDesign::Body", "Frame")
        frame_result = frame.newObject("PartDesign::Feature", "FrameResult")
        frame_result.Shape = Part.makeBox(20, 20, 5)
        rotor = document.addObject("PartDesign::Body", "Rotor")
        rotor_result = rotor.newObject("PartDesign::Feature", "RotorResult")
        rotor_result.Shape = Part.makeCylinder(5, 8)
        document.recompute()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(document.Name, frame.Name)
        Gui.Selection.addSelection(document.Name, rotor.Name)
        Gui.Selection.addSelection(document.Name, frame_result.Name, "Edge1")

        entries = tuple(Gui.Selection.getSelectionEx() or ())
        assert any(tuple(entry.SubElementNames or ()) for entry in entries)
        commands = PrintCommandLoader.command_module()
        actual_document, objects = commands._active_selection()
        assert actual_document is document
        assert [obj.Name for obj in objects] == [frame.Name, rotor.Name]

        import PrintPanel

        panel = PrintPanel.PrintPanelWidget()
        panel._update_selection_summary()
        assert [choice.text() for choice in panel.object_checkboxes] == [
            "Frame",
            "Rotor",
        ]
        assert all(choice.isChecked() for choice in panel.object_checkboxes)
        assert all(
            "FrameResult" not in choice.text()
            for choice in panel.object_checkboxes
        )
        panel.close()
        print("VIBECAD_PRINT_SELECTION_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        Gui.Selection.clearSelection()
        if document is not None:
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
