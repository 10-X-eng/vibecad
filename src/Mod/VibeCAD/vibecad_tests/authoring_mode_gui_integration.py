# SPDX-License-Identifier: LGPL-2.1-or-later

"""Clean-profile GUI gate for the human-owned authoring-mode selector."""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
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


def _queue_save_dialog(path: Path | None) -> None:
    attempts = {"remaining": 1600}

    def respond() -> None:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if not isinstance(widget, QtWidgets.QFileDialog) or not widget.isVisible():
                continue
            if path is None:
                widget.reject()
            else:
                widget.setDirectory(str(path.parent))
                file_name = widget.findChild(QtWidgets.QLineEdit, "fileNameEdit")
                if file_name is None:
                    break
                file_name.setText(path.name)
                widget.accept()
            return
        attempts["remaining"] -= 1
        if attempts["remaining"] > 0:
            QtCore.QTimer.singleShot(5, respond)

    QtCore.QTimer.singleShot(0, respond)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-start-")
        save_path = Path(temporary.name) / "native-start.FCStd"
        dialog_preferences = App.ParamGet("User parameter:BaseApp/Preferences/Dialog")
        native_dialog_before = dialog_preferences.GetBool("DontUseNativeDialog", False)
        dialog_preferences.SetBool("DontUseNativeDialog", True)
        Gui.activateWorkbench("PartDesignWorkbench")
        VibeGui._show_panel()
        _process_events()

        main_window = Gui.getMainWindow()
        prompt = main_window.findChild(QtWidgets.QPlainTextEdit, "VibePrompt")
        send = main_window.findChild(QtWidgets.QPushButton, "VibeSend")
        assert prompt is not None and prompt.isEnabled() is False
        assert send is not None and send.isEnabled() is False

        document = App.newDocument("VibeCADAuthoringModeGate")
        _process_events()

        selector = main_window.findChild(QtWidgets.QComboBox, "VibeAuthoringMode")
        assert selector is not None
        assert selector.count() == 2
        assert [selector.itemData(index) for index in range(2)] == [
            "vibescript",
            "native",
        ]
        VibeGui._refresh_authoring_mode_selector()
        assert selector.currentData() == "vibescript"
        assert selector.isEnabled() is True
        assert _item_enabled(selector, "vibescript") is True
        assert _item_enabled(selector, "native") is True
        assert selector.property("VibeNativeAvailable") is False
        assert not document.FileName
        assert get_service().conversation_catalog()["conversation_count"] == 0
        new_conversation = main_window.findChild(
            QtWidgets.QToolButton,
            "VibeNewConversation",
        )
        assert send is not None and send.isEnabled() is False
        assert new_conversation is not None and new_conversation.isEnabled() is False
        assert prompt is not None and prompt.isEnabled() is False
        assert prompt.isReadOnly() is True
        assert prompt.placeholderText() == "Save this VibeCAD document to enable VibeCAD."
        assert selector.toolTip() == "Save this VibeCAD document to enable VibeCAD."

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
        _queue_save_dialog(None)
        selector.setCurrentIndex(selector.findData("native"))
        assert selector.currentData() == "vibescript"
        assert get_service().modeling_engine() == "vibescript"
        assert get_service().conversation_catalog()["conversation_count"] == 0
        assert confirmations == [False, True]

        _queue_save_dialog(save_path)
        selector.setCurrentIndex(selector.findData("native"))
        service = get_service()
        assert selector.currentData() == "native"
        assert service.modeling_engine() == "native"
        assert confirmations == [False, True, True]
        assert Path(document.FileName) == save_path
        assert save_path.is_file()
        catalog = service.conversation_catalog()
        assert catalog["conversation_count"] == 1
        assert catalog["active_conversation_id"]
        assert send.isEnabled() is True
        assert new_conversation.isEnabled() is True
        assert prompt.isEnabled() is True
        assert prompt.isReadOnly() is False
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
        if "dialog_preferences" in locals():
            dialog_preferences.SetBool("DontUseNativeDialog", native_dialog_before)
        if "temporary" in locals():
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
