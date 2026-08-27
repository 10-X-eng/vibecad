# SPDX-License-Identifier: LGPL-2.1-or-later

"""Single low-level owner for authorized FreeCAD GUI surface transitions."""

from __future__ import annotations

from typing import Any


def activate_authorized_workbench(workbench: str) -> None:
    """Activate one already-authorized exact workbench and drain GUI events."""

    clean_workbench = str(workbench or "").strip()
    if not clean_workbench:
        raise ValueError("workbench must be non-empty")
    import FreeCADGui as Gui
    from PySide import QtCore, QtWidgets

    Gui.activateWorkbench(clean_workbench)
    _drain_gui_events(Gui, QtCore, QtWidgets)


def enter_authorized_edit_mode(gui_document: Any, object_name: str) -> bool:
    """Enter edit mode for one already-authorized exact document object."""

    clean_name = str(object_name or "").strip()
    if gui_document is None:
        raise ValueError("gui_document is required")
    if not clean_name:
        raise ValueError("object_name must be non-empty")
    return bool(gui_document.setEdit(clean_name))


def drain_authorized_gui_events() -> None:
    """Drain the bounded event window after an authorized GUI transition."""

    import FreeCADGui as Gui
    from PySide import QtCore, QtWidgets

    _drain_gui_events(Gui, QtCore, QtWidgets)


def _drain_gui_events(Gui: Any, QtCore: Any, QtWidgets: Any) -> None:
    for _index in range(8):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)
