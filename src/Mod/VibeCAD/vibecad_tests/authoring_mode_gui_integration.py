# SPDX-License-Identifier: LGPL-2.1-or-later

"""Clean-profile GUI gate for the human-owned authoring-mode selector."""

from __future__ import annotations

import sys
import traceback
from types import SimpleNamespace

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADModelingSurface as modeling_surface_module
from VibeCADCore import get_service


def _process_events(rounds: int = 12) -> None:
    for _ in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _item_enabled(selector, mode: str) -> bool:
    index = selector.findData(mode)
    assert index >= 0
    item = selector.model().item(index)
    assert item is not None
    return bool(item.isEnabled())


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("VibeCADAuthoringModeGate")
        VibeGui._show_panel()
        _process_events()

        main_window = Gui.getMainWindow()
        selector = main_window.findChild(QtWidgets.QComboBox, "VibeAuthoringMode")
        assert selector is not None
        assert selector.count() == 2
        assert [selector.itemData(index) for index in range(2)] == [
            "vibescript",
            "native",
        ]
        VibeGui._refresh_authoring_mode_selector()
        assert selector.currentData() == "vibescript"
        assert selector.isEnabled() is False
        assert _item_enabled(selector, "vibescript") is True
        assert _item_enabled(selector, "native") is False
        assert "not yet complete" in selector.toolTip()

        original_resolver = modeling_surface_module.resolve_modeling_surface
        modeling_surface_module.resolve_modeling_surface = (
            lambda workbench, engine: (
                SimpleNamespace(available=True, unavailable_reason="")
                if engine == "native"
                else original_resolver(workbench, engine)
            )
        )

        document.openTransaction("Authoring mode transaction blocker")
        VibeGui._refresh_authoring_mode_selector()
        assert selector.isEnabled() is False
        assert _item_enabled(selector, "native") is False
        assert "transaction" in selector.toolTip().lower()
        document.abortTransaction()

        blocker_sketch = document.addObject(
            "Sketcher::SketchObject",
            "AuthoringModeBlockerSketch",
        )
        Gui.activeDocument().setEdit(blocker_sketch.Name)
        _process_events()
        VibeGui._refresh_authoring_mode_selector()
        assert selector.isEnabled() is False
        assert _item_enabled(selector, "native") is False
        assert "task" in selector.toolTip().lower()
        Gui.activeDocument().resetEdit()
        _process_events()
        document.removeObject(blocker_sketch.Name)

        run_id = VibeGui._assistant_run_controller.begin()
        try:
            VibeGui._refresh_authoring_mode_selector()
            assert selector.isEnabled() is False
            assert _item_enabled(selector, "native") is False
            assert "assistant run" in selector.toolTip().lower()
        finally:
            VibeGui._assistant_run_controller.finish(run_id)

        confirmations = []

        def reject_manual_control():
            confirmations.append(False)
            return False

        def accept_manual_control():
            confirmations.append(True)
            return True

        VibeGui._confirm_take_manual_control = reject_manual_control
        VibeGui._refresh_authoring_mode_selector()
        assert selector.isEnabled() is True
        selector.setCurrentIndex(selector.findData("native"))
        assert selector.currentData() == "vibescript"
        assert get_service().modeling_engine() == "vibescript"
        assert confirmations == [False]

        VibeGui._confirm_take_manual_control = accept_manual_control
        selector.setCurrentIndex(selector.findData("native"))
        service = get_service()
        assert selector.currentData() == "native"
        assert service.modeling_engine() == "native"
        assert confirmations == [False, True]
        assert selector.isEnabled() is True
        assert _item_enabled(selector, "vibescript") is True

        before = service.native_document_state()["structural_revision"]
        created = document.addObject("PartDesign::Feature", "ManualFeature")
        _process_events()
        if service.native_document_state()["structural_revision"] == before:
            service.note_native_object_created(created)
        VibeGui._refresh_authoring_mode_selector()
        assert selector.currentData() == "native"
        assert selector.isEnabled() is False
        assert _item_enabled(selector, "vibescript") is False
        assert "not represented by VibeScript source" in selector.toolTip()

        print("VIBECAD_AUTHORING_MODE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
