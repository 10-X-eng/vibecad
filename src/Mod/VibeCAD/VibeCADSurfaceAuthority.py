# SPDX-License-Identifier: LGPL-2.1-or-later

"""Single GUI authority for workbench and document edit activation."""

from __future__ import annotations

from typing import Any


def activate_workbench(workbench: str) -> None:
    """Activate one exact FreeCAD workbench on the GUI thread."""

    import FreeCADGui as Gui

    Gui.activateWorkbench(str(workbench))


def enter_edit_mode(gui_document: Any, object_name: str) -> bool:
    """Enter edit mode for one exact object through its GUI document."""

    return bool(gui_document.setEdit(str(object_name)))
