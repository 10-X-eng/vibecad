# SPDX-License-Identifier: LGPL-2.1-or-later

"""Clean-profile GUI gate for the complete document/chat/authority lifecycle."""

from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADPreferences import preferences
from VibeCADProject import AUTHORING_MODE_META_KEY, NATIVE_AUTHORITY_META_KEY


def _process_events(rounds: int = 16) -> None:
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
    temporary = None
    saved_project_root = None
    exit_code = 1
    try:
        preferences().SetString("NewDocumentAuthoringMode", "ask")
        assert App.ActiveDocument is None
        Gui.activateWorkbench("PartDesignWorkbench")
        VibeGui._show_panel()
        _process_events()

        main_window = Gui.getMainWindow()
        prompt = main_window.findChild(QtWidgets.QPlainTextEdit, "VibePrompt")
        send = main_window.findChild(QtWidgets.QPushButton, "VibeSend")
        selector = main_window.findChild(QtWidgets.QComboBox, "VibeAuthoringMode")
        new_conversation = main_window.findChild(
            QtWidgets.QToolButton,
            "VibeNewConversation",
        )
        assert prompt is not None and prompt.isEnabled() is False
        assert prompt.isReadOnly() is True
        assert prompt.placeholderText() == "Create or open a document to use VibeCAD."
        assert send is not None and send.isEnabled() is False
        assert selector is not None and selector.isEnabled() is False

        document = App.newDocument("VibeCADAuthoringModeGate")
        _process_events()
        service = get_service()

        assert not document.FileName
        assert selector.count() == 3
        assert [selector.itemData(index) for index in range(3)] == [
            "",
            "vibescript",
            "native",
        ]
        assert selector.currentData() == ""
        assert selector.isEnabled() is True
        assert _item_enabled(selector, "vibescript") is True
        assert _item_enabled(selector, "native") is True
        assert prompt.isEnabled() is True and prompt.isReadOnly() is False
        assert prompt.placeholderText() == (
            "Choose Native or VibeScript above, then message VibeCAD."
        )
        assert send.isEnabled() is False
        assert new_conversation is not None and new_conversation.isEnabled() is True
        assert service.assistant_document_state()["turn_enabled"] is False
        catalog = service.conversation_catalog()
        assert catalog["conversation_count"] == 1
        conversation_id = str(catalog["active_conversation_id"])
        temporary_root = Path(service.project_scope_snapshot()["root"])
        assert temporary_root.name.startswith("unsaved-")
        assert temporary_root.is_dir()

        document.openTransaction("Authoring mode transaction blocker")
        VibeGui._refresh_authoring_mode_selector()
        assert selector.isEnabled() is False
        assert "transaction" in selector.toolTip().lower()
        document.abortTransaction()

        blocker_sketch = document.addObject(
            "Sketcher::SketchObject",
            "AuthoringModeBlockerSketch",
        )
        Gui.activeDocument().setEdit(blocker_sketch.Name)
        _process_events()
        assert selector.isEnabled() is False
        assert "task" in selector.toolTip().lower()
        Gui.activeDocument().resetEdit()
        document.removeObject(blocker_sketch.Name)
        _process_events()

        run_id = VibeGui._assistant_run_controller.begin()
        try:
            VibeGui._refresh_authoring_mode_selector()
            assert selector.isEnabled() is False
            assert "assistant run" in selector.toolTip().lower()
        finally:
            VibeGui._assistant_run_controller.finish(run_id)
        VibeGui._render_assistant_run_state(VibeGui._find_dock())

        confirmations: list[bool] = []

        def unexpected_confirmation() -> bool:
            confirmations.append(False)
            return False

        VibeGui._confirm_take_manual_control = unexpected_confirmation
        selector.setCurrentIndex(selector.findData("native"))
        _process_events()
        assert confirmations == []
        assert service.modeling_engine() == "native"
        assert not document.FileName
        assert send.isEnabled() is True
        assert service.assistant_document_state()["turn_enabled"] is True
        assert service.conversation_catalog()["active_conversation_id"] == conversation_id

        selector.setCurrentIndex(selector.findData("vibescript"))
        _process_events()
        assert service.modeling_engine() == "vibescript"

        scripted = document.addObject("App::FeaturePython", "ScriptedContentMarker")
        scripted.addProperty("App::PropertyString", "VibeCADVibeScriptProgramId")
        scripted.VibeCADVibeScriptProgramId = "a" * 32

        VibeGui._confirm_take_manual_control = unexpected_confirmation
        selector.setCurrentIndex(selector.findData("native"))
        _process_events()
        assert confirmations == [False]
        assert service.modeling_engine() == "vibescript"

        def accept_confirmation() -> bool:
            confirmations.append(True)
            return True

        VibeGui._confirm_take_manual_control = accept_confirmation
        selector.setCurrentIndex(selector.findData("native"))
        _process_events()
        assert confirmations == [False, True]
        assert service.modeling_engine() == "native"
        assert service.conversation_catalog()["active_conversation_id"] == conversation_id

        native_feature = document.addObject("PartDesign::Feature", "ManualFeature")
        _process_events()
        assert native_feature is not None
        assert service.native_document_state()["native_authority"]["changed"] is True
        assert selector.isEnabled() is False
        assert _item_enabled(selector, "vibescript") is False

        prepared = service.prepare_conversation_turn("user", "Keep this turn.")
        history = service.persist_prepared_conversation_turn(prepared)
        service.accept_persisted_conversation_turn(history, prepared)
        artifact = temporary_root / "vibescript" / "partdesign" / "program-a" / "probe.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("portable program artifact", encoding="utf-8")

        temporary = tempfile.TemporaryDirectory(prefix="vibecad-native-start-")
        save_path = Path(temporary.name) / "native-start.FCStd"
        document.saveAs(str(save_path))
        _process_events()

        assert save_path.is_file()
        assert not temporary_root.exists()
        saved_project_root = Path(service.project_scope_snapshot()["root"])
        assert (saved_project_root / artifact.relative_to(temporary_root)).is_file()
        saved_catalog = service.conversation_catalog()
        assert saved_catalog["active_conversation_id"] == conversation_id
        assert service.conversation_history()["turn_count"] == 1
        metadata = dict(document.Meta)
        assert metadata[AUTHORING_MODE_META_KEY] == "native"
        assert metadata[NATIVE_AUTHORITY_META_KEY]

        App.closeDocument(document.Name)
        document = None
        _process_events()
        assert prompt.isEnabled() is False and prompt.isReadOnly() is True
        assert send.isEnabled() is False
        assert selector.isEnabled() is False

        disposable = App.newDocument("DiscardedUnsavedChat")
        _process_events()
        disposable_root = Path(service.project_scope_snapshot()["root"])
        assert disposable_root.is_dir()
        App.closeDocument(disposable.Name)
        _process_events()
        assert not disposable_root.exists()
        assert prompt.isEnabled() is False and selector.isEnabled() is False

        print("VIBECAD_AUTHORING_MODE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if saved_project_root is not None:
            shutil.rmtree(saved_project_root, ignore_errors=True)
        if temporary is not None:
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
