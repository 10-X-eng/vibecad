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
"""Provides several TechDraw GuiCommands to create vertexes."""

__title__ = "TechDrawTools.CommandVertexCreations"
__author__ = "edi"
__url__ = "https://www.freecad.org"
__version__ = "00.01"
__date__ = "2023/12/05"


from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui

import TechDrawTools
import TechDrawTools.TDToolsUtil as Utils


import TechDraw

class CommandVertexCreationGroup:
    '''Create a drop down toolbar/menubar for vertex creating tools'''
    def Activated(self, index):
        if index == 0:
            Gui.runCommand("TechDraw_ExtensionVertexAtIntersection")
        elif index == 1:
            Gui.runCommand("TechDraw_CommandAddOffsetVertex")

    def GetCommands(self):
        return("TechDraw_ExtensionVertexAtIntersection",
               "TechDraw_CommandAddOffsetVertex")

    def GetDefaultCommand(self):
        return 0

    def GetResources(self):
        """Return a dictionary with data that will be used by the button or menu item."""
        return {'Pixmap': 'TechDraw_ExtensionVertexAtIntersection.svg',
                'Accel': "",
                'MenuText': QT_TRANSLATE_NOOP("TechDraw_ExtensionVertexAtIntersection","Cosmetic Intersection Vertices"),
                'ToolTip': QT_TRANSLATE_NOOP("TechDraw_ExtensionVertexAtIntersection", "Adds cosmetic vertices at the intersectionss of selected edges")}

    def IsActive(self):
        """Return True when the command should be active or False when it should be disabled (greyed)."""
        if App.ActiveDocument:
            return Utils.havePage() and Utils.haveView()
        else:
            return False

class CommandAddOffsetVertex:
    """Creates a vertex offset to a selected vertex."""

    def __init__(self):
        """Initialize variables for the command that must exist at all times."""
        pass

    def GetResources(self):
        """Return a dictionary with data that will be used by the button or menu item."""
        return {'Pixmap': 'actions/TechDraw_AddOffsetVertex.svg',
                'Accel': "",
                'MenuText': QT_TRANSLATE_NOOP("TechDraw_AddOffsetVertex", "Offset Vertex"),
                'ToolTip': QT_TRANSLATE_NOOP("TechDraw_AddOffsetVertex", "Creates an offset from one selected vertex")}

    def Activated(self):
        """Run the following code when the command is activated (button pressed)."""
        if not self.IsActive():
            return
        selected = Gui.Selection.getSelectionEx()[0]
        view = selected.Object
        vertex = view.getVertexBySelection(
            selected.SubElementNames[0]
        )
        if vertex is None:
            return
        self.ui = TechDrawTools.TaskAddOffsetVertex(
            view,
            vertex,
            selected.SubElementNames[0],
        )
        dialog = Gui.Control.showDialog(
            self.ui,
            self.ui.gui_document,
        )
        if dialog is not None:
            dialog.setAutoCloseOnDeletedDocument(True)
            dialog.setDocumentName(self.ui.document.Name)

    def IsActive(self):
        """Return True when the command should be active or False when it should be disabled (greyed)."""
        document = App.ActiveDocument
        if (
            document is None
            or Gui.Control.activeDialog()
            or document.getBookedTransactionID() != 0
            or document.HasPendingTransaction
        ):
            return False
        selection = Gui.Selection.getSelectionEx()
        return (
            len(selection) == 1
            and selection[0].Object is not None
            and selection[0].Object.Document is document
            and selection[0].Object.isDerivedFrom(
                "TechDraw::DrawViewPart"
            )
            and len(selection[0].SubElementNames) == 1
            and selection[0].SubElementNames[0].startswith(
                "Vertex"
            )
            and selection[0].Object.findParentPage() is not None
        )

Gui.addCommand('TechDraw_CommandVertexCreationGroup',CommandVertexCreationGroup())
Gui.addCommand('TechDraw_CommandAddOffsetVertex',CommandAddOffsetVertex())
