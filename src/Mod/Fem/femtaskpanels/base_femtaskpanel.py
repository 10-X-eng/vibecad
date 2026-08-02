# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2024 Mario Passaglia <mpassaglia[at]cbc.uba.ar>         *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# *                                                                         *
# ***************************************************************************

__title__ = "FreeCAD FEM base task panel object"
__author__ = "Mario Passaglia"
__url__ = "https://www.freecad.org"

## @package base_femtaskpanel
#  \ingroup FEM
#  \brief base object for FEM task panels


import FreeCAD

if FreeCAD.GuiUp:
    import FreeCADGui


class _TaskTargetIdentity:
    """Exact, non-rebindable identity for one FEM editor target."""

    def __init__(self, obj):
        document = getattr(obj, "Document", None)
        if document is None:
            raise RuntimeError("The FEM editor target is not in a document")

        self.document = document
        self.document_address = id(document)
        self.document_name = str(document.Name)
        self.document_uid = str(getattr(document, "Uid", "") or "")
        self.obj = obj
        self.object_address = id(obj)
        self.object_name = str(obj.Name)
        self.object_id = int(obj.ID)
        self.resolve()

    def resolve_document(self):
        """Return the same live document wrapper captured at construction."""

        document = self.document
        if id(document) != self.document_address:
            raise RuntimeError("The FEM editor document identity changed")
        try:
            live_document = FreeCAD.getDocument(self.document_name)
            live_uid = str(getattr(document, "Uid", "") or "")
        except (
            AttributeError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            live_document = None
            live_uid = ""
        if (
            live_document is not document
            or live_uid != self.document_uid
        ):
            raise RuntimeError(
                "The FEM editor document is no longer available"
            )
        return document

    def resolve(self):
        """Return the exact live object; never rebind by a reused name."""

        document = self.resolve_document()
        obj = self.obj
        try:
            candidate_by_name = document.getObject(self.object_name)
            candidate_by_id = document.getObject(self.object_id)
            object_document = obj.Document
            object_name = str(obj.Name)
            object_id = int(obj.ID)
        except (
            AttributeError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            candidate_by_name = None
            candidate_by_id = None
            object_document = None
            object_name = ""
            object_id = -1
        if (
            id(obj) != self.object_address
            or object_document is not document
            or object_name != self.object_name
            or object_id != self.object_id
            or candidate_by_name is not obj
            or candidate_by_id is not obj
        ):
            raise RuntimeError(
                "The FEM editor target is no longer the captured object"
            )
        return document, obj

    def resolve_gui_document(self, require_object=True):
        """Return the GUI wrapper for the captured document."""

        document = (
            self.resolve()[0]
            if require_object
            else self.resolve_document()
        )
        if not FreeCAD.GuiUp:
            raise RuntimeError("The FEM editor requires the GUI")
        try:
            gui_document = FreeCADGui.getDocument(self.document_name)
        except (
            AttributeError,
            NameError,
            ReferenceError,
            RuntimeError,
            TypeError,
        ):
            gui_document = None
        if (
            gui_document is None
            or getattr(gui_document, "Document", None) is not document
        ):
            raise RuntimeError(
                "The FEM editor GUI document is no longer available"
            )
        return gui_document


class _BaseTaskPanel:
    """
    Base task panel
    """

    def __init__(self, obj):
        self.obj = obj
        self._target_identity = _TaskTargetIdentity(obj)

    def _resolve_editor(self):
        document, obj = self._target_identity.resolve()
        gui_document = self._target_identity.resolve_gui_document()
        return document, gui_document, obj

    @staticmethod
    def _editing_object(gui_document):
        view_provider = gui_document.getInEdit()
        return (
            getattr(view_provider, "Object", None)
            if view_provider is not None
            else None
        )

    def _finish_exact_edit(self, gui_document, obj):
        editing_object = self._editing_object(gui_document)
        if editing_object is None:
            # Standalone task dialogs are closed by the common TaskView
            # command owner after this callback returns.
            return
        if editing_object is not obj:
            raise RuntimeError(
                "A different object now owns the FEM editor document"
            )
        gui_document.resetEdit()

    def accept(self):
        document, gui_document, obj = self._resolve_editor()
        document.recompute()
        self._finish_exact_edit(gui_document, obj)

        return True

    def reject(self):
        document, gui_document, obj = self._resolve_editor()
        # TaskView marks the exact adopted edit transaction for rollback
        # before invoking this callback. resetEdit() tears down the
        # ViewProvider first and then consumes only that exact transaction.
        self._finish_exact_edit(gui_document, obj)
        document.recompute()

        return True

    def activate(self):
        if self._selectionWidget:
            self._selectionWidget.attachSelection()

    def deactivate(self):
        if self._selectionWidget:
            self._selectionWidget.detachSelection()
