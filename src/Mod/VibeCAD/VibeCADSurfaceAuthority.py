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


def deactivate_assembly(gui_document: Any) -> bool:
    """Leave normal Assembly activation before an agent changes workspaces."""

    import FreeCADGui as Gui
    from VibeCADEditState import active_edit_state

    edit = active_edit_state(gui_document)
    assembly = edit.document_object
    if not edit.active or getattr(assembly, "TypeId", "") != "Assembly::AssemblyObject":
        return False
    document = gui_document.Document
    if assembly.Document is not document:
        raise RuntimeError("The active assembly belongs to another document.")
    if Gui.Control.activeDialog():
        raise RuntimeError("Finish the active assembly task before switching ribbons.")
    if document.HasPendingTransaction:
        raise RuntimeError("Finish the active assembly transaction before switching ribbons.")
    # Use Assembly's explicit deactivate action. A bare resetEdit() retains
    # its request to restore the assembly after a later sketch/task closes,
    # even if that assembly has since been removed from the document.
    if not assembly.ViewObject.doubleClicked():
        raise RuntimeError("VibeCAD could not deactivate the active assembly.")
    if (active_edit_state(gui_document).active
            or gui_document.activeView().getActiveObject("assembly") is not None):
        raise RuntimeError("VibeCAD could not deactivate the active assembly.")
    return True
