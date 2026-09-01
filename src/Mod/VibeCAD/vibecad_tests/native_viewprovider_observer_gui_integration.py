# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression gate for observing view providers during Body construction."""

from __future__ import annotations

import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
        VibeGui._connect_document_observer()
        document = App.newDocument("NativeViewProviderObserverGate")

        body = document.addObject("PartDesign::Body", "ObserverBody")
        assert body.Origin is not None
        assert body.ViewObject.Object is body

        component = document.addObject("PartDesign::Component", "ObserverComponent")
        assert component.Origin is not None
        assert component.ViewObject.Object is component

        document.recompute()
        print(
            "VIBECAD_NATIVE_VIEWPROVIDER_OBSERVER_GUI_OK "
            "body=true component=true origin=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
