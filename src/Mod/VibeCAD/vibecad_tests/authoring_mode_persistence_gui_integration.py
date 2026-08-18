# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI gate for saved, reopened, and multi-document authoring authority."""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
import VibeCADModelingSurface as modeling_surface_module
from VibeCADCore import get_service


def _process_events(rounds: int = 16) -> None:
    for _ in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    documents: list[str] = []
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        temporary = tempfile.TemporaryDirectory(prefix="vibecad-authority-")
        cad_path = Path(temporary.name) / "native-authority.FCStd"

        original_resolver = modeling_surface_module.resolve_modeling_surface
        modeling_surface_module.resolve_modeling_surface = (
            lambda workbench, engine: (
                SimpleNamespace(available=True, unavailable_reason="")
                if engine == "native"
                else original_resolver(workbench, engine)
            )
        )
        VibeGui._confirm_take_manual_control = lambda: True
        VibeGui._save_before_native_mode = lambda _dock=None: True
        VibeGui._ensure_first_conversation = lambda _service: None

        document = App.newDocument("NativeAuthoritySaved")
        documents.append(document.Name)
        VibeGui._show_panel()
        _process_events()
        selector = Gui.getMainWindow().findChild(
            QtWidgets.QComboBox,
            "VibeAuthoringMode",
        )
        assert selector is not None
        selector.setCurrentIndex(selector.findData("native"))
        service = get_service()
        assert service.modeling_engine() == "native"

        document.addObject("PartDesign::Feature", "ManualFeature")
        _process_events()
        before_save = service.native_document_state()
        assert before_save["native_authority"]["changed"] is True
        document_uid = str(document.Uid)

        document.saveAs(str(cad_path))
        _process_events()
        project_root = Path(service.project_context()["root"])
        assert (project_root / "project.vibecad.json").is_file()
        assert (project_root / "native-state.json").is_file()

        saved_document_name = document.Name
        App.closeDocument(saved_document_name)
        documents.remove(saved_document_name)
        _process_events()

        reopened = App.openDocument(str(cad_path))
        documents.append(reopened.Name)
        Gui.activeDocument().activeView()
        _process_events()
        assert str(reopened.Uid) == document_uid
        assert service.modeling_engine() == "native"
        restored = service.native_document_state()
        assert restored["structural_revision"] == before_save["structural_revision"]
        assert restored["native_authority"]["changed"] is True
        VibeGui._refresh_authoring_mode_selector()
        assert selector.currentData() == "native"
        assert selector.isEnabled() is False

        second = App.newDocument("IndependentUnsavedAuthority")
        documents.append(second.Name)
        _process_events()
        assert service.modeling_engine() == "vibescript"
        App.setActiveDocument(reopened.Name)
        _process_events()
        assert service.modeling_engine() == "native"

        print("VIBECAD_AUTHORING_MODE_PERSISTENCE_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        for name in reversed(documents):
            if name in App.listDocuments():
                App.closeDocument(name)
        if "temporary" in locals():
            temporary.cleanup()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
