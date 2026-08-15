# ***************************************************************************
# *   Copyright (c) 2023 edi <edi271@a1.net>                                *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************
"""Provides the TechDraw AddOffsetVertex Task Dialog."""

__title__ = "TechDrawTools.TasAddOffsetVertex"
__author__ = "edi"
__url__ = "https://www.freecad.org"
__version__ = "00.01"
__date__ = "2023/12/04"

import FreeCAD as App
import FreeCADGui as Gui
import TechDraw
import TechDrawGui

import os

translate = App.Qt.translate


class TaskAddOffsetVertex:
    """Provides the TechDraw AddOffsetVertex Task Dialog."""

    def __init__(self, view, vertex, source_name=None):
        if (
            view is None
            or vertex is None
            or not view.isDerivedFrom("TechDraw::DrawViewPart")
            or view.Document is None
        ):
            raise RuntimeError(
                "Select one projected drawing vertex"
            )
        page = view.findParentPage()
        if page is None or page.Document is not view.Document:
            raise RuntimeError(
                "The selected vertex must belong to a drawing page"
            )
        self.document = view.Document
        self.gui_document = Gui.getDocument(self.document.Name)
        if self.gui_document is None:
            raise RuntimeError(
                "The selected drawing has no GUI document"
            )
        self.view_name = view.Name
        self.page_name = page.Name
        self.source_name = str(source_name or "")
        canonical = TechDraw.makeCanonicalPoint(
            view,
            vertex.Point,
            False,
        )
        self.source_point = App.Vector(
            canonical.x,
            canonical.y,
            canonical.z,
        )

        self._uiPath = App.getHomePath()
        self._uiPath = os.path.join(
            self._uiPath, "Mod/TechDraw/TechDrawTools/Gui/TaskAddOffsetVertex.ui"
        )
        self.form = Gui.PySideUic.loadUi(self._uiPath)
        self.form.setWindowTitle(translate("TechDraw_AddOffsetVertex", "Offset Vertex"))
        self._previewTag = None
        self.form.dSpinBoxX.valueChanged.connect(self.onOffsetChanged)
        self.form.dSpinBoxY.valueChanged.connect(self.onOffsetChanged)

        sel = Gui.Selection.getSelectionEx()
        if sel and sel[0].SubElementNames:
            sub = sel[0].SubElementNames[0]
            self.form.le_SourceVertex.setText(f"{view.Label}.{sub}")

        self.transaction_id = int(
            self.gui_document.openCommand("Add offset vertex")
        )
        if (
            self.transaction_id == 0
            or self.document.getBookedTransactionID()
            != self.transaction_id
        ):
            raise RuntimeError(
                "Could not open the offset-vertex task"
            )

    def _resolve_view(self):
        try:
            if App.getDocument(self.document.Name) is not self.document:
                return None
        except (NameError, ReferenceError, RuntimeError):
            return None
        view = self.document.getObject(self.view_name)
        page = self.document.getObject(self.page_name)
        if (
            view is None
            or page is None
            or not view.isDerivedFrom("TechDraw::DrawViewPart")
            or view.findParentPage() is not page
        ):
            return None
        return view

    def onOffsetChanged(self):
        view = self._resolve_view()
        if view is None:
            return
        offset = App.Vector(self.form.dSpinBoxX.value(), self.form.dSpinBoxY.value(), 0)
        if self._previewTag:
            view.removeCosmeticVertex(self._previewTag)
        self._previewTag = self._create_preview(view, offset)

    def _create_preview(self, view, offset):
        if self.source_name:
            created = TechDrawGui.createDrawingOffsetVertex(
                view,
                self.source_name,
                float(offset.x),
                float(offset.y),
            )
            return created["vertex"]["tag"]
        tag = view.makeCosmeticVertex(self.source_point + offset)
        view.requestPaint()
        return tag

    def accept(self):
        view = self._resolve_view()
        if view is None:
            return False
        if not self._previewTag:
            offset = App.Vector(self.form.dSpinBoxX.value(), self.form.dSpinBoxY.value(), 0)
            self._previewTag = self._create_preview(view, offset)
        if not self._previewTag:
            return False
        view.requestPaint()
        return True

    def reject(self):
        # The TaskView closes this exact transaction after the panel has been
        # removed, so preview geometry is rolled back without touching another
        # document or invalidating live widgets during teardown.
        return True

    def autoClosedOnDeletedDocument(self):
        self.transaction_id = 0
