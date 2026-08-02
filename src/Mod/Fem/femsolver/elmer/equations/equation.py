# ***************************************************************************
# *   Copyright (c) 2017 Markus Hovorka <m.hovorka@live.de>                 *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
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

__title__ = "FreeCAD FEM solver Elmer equation base object"
__author__ = "Markus Hovorka"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import FreeCAD as App
from ... import equationbase
from femtaskpanels import base_femtaskpanel
from femtools import membertools

if App.GuiUp:
    import FreeCADGui as Gui
    from femguiutils import selection_widgets


class Proxy(equationbase.BaseProxy):

    def __init__(self, obj):
        super().__init__(obj)
        obj.addProperty(
            "App::PropertyInteger",
            "Priority",
            "Base",
            (
                "Number of your choice\n"
                "The equation with highest number\n"
                "will be solved first."
            ),
            locked=True,
        )


class ViewProxy(equationbase.BaseViewProxy):

    def supportsDocumentTimelineEdit(self):
        return True

    def setEdit(self, vobj, mode=0):
        identity = base_femtaskpanel._TaskTargetIdentity(vobj.Object)
        gui_document = identity.resolve_gui_document()
        task = _TaskPanel(vobj.Object)
        Gui.Control.showDialog(task, gui_document)
        self._fem_edit_identity = identity

    def unsetEdit(self, vobj, mode=0):
        identity = getattr(self, "_fem_edit_identity", None)
        if identity is None:
            identity = base_femtaskpanel._TaskTargetIdentity(
                vobj.Object
            )
        gui_document = identity.resolve_gui_document(
            require_object=False
        )
        Gui.Control.closeDialog(gui_document)
        self._fem_edit_identity = None

    def doubleClicked(self, vobj):
        identity = base_femtaskpanel._TaskTargetIdentity(vobj.Object)
        gui_document = identity.resolve_gui_document()
        gui_document.setEdit(vobj.Object.Name)
        return True

    def getTaskWidget(self, vobj):
        return None


class _TaskPanel(base_femtaskpanel._BaseTaskPanel):

    def __init__(self, obj):
        super().__init__(obj)
        self._obj = obj
        self._selectionWidget = selection_widgets.GeometryElementsSelection(
            obj.References, ["Solid", "Face"], False, True
        )
        # start in solid selection mode
        self._selectionWidget.rb_solid.setChecked(True)
        propWidget = obj.ViewObject.Proxy.getTaskWidget(obj.ViewObject)
        if propWidget is None:
            self.form = self._selectionWidget
        else:
            self.form = [self._selectionWidget, propWidget]
        analysis = obj.getParentGroup()
        self._mesh = membertools.get_single_member(analysis, "Fem::FemMeshObject")
        self._part = self._mesh.Shape if self._mesh is not None else None
        self._partVisible = None
        self._meshVisible = None

    def open(self):
        if self._mesh is not None and self._part is not None:
            self._meshVisible = self._mesh.ViewObject.isVisible()
            self._partVisible = self._part.ViewObject.isVisible()
            self._mesh.ViewObject.hide()
            self._part.ViewObject.show()

    def reject(self):
        self._selectionWidget.finish_selection()
        self._restoreVisibility()
        return super().reject()

    def accept(self):
        if self._obj.References != self._selectionWidget.references:
            self._obj.References = self._selectionWidget.references
        self._selectionWidget.finish_selection()
        self._restoreVisibility()
        return super().accept()

    def activate(self):
        self._selectionWidget.attachSelection()

    def deactivate(self):
        self._selectionWidget.detachSelection()

    def _restoreVisibility(self):
        if self._mesh is not None and self._part is not None:
            if self._meshVisible:
                self._mesh.ViewObject.show()
            else:
                self._mesh.ViewObject.hide()
            if self._partVisible:
                self._part.ViewObject.show()
            else:
                self._part.ViewObject.hide()

    def _recomputeAndRestore(self):
        self._restoreVisibility()
        document, gui_document, obj = self._resolve_editor()
        document.recompute()
        self._finish_exact_edit(gui_document, obj)


##  @}
