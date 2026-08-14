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
"""
Provides the TechDraw AxoLengthDimension GuiCommand.
00.01 2023/02/01 Basic version
00.02 2023/12/07 Calculate real 3D values if parallel to coordinate axis
"""

__title__ = "TechDrawTools.CommandAxoLengthDimension"
__author__ = "edi"
__url__ = "https://www.freecad.org"
__version__ = "00.02"
__date__ = "2023/12/07"

from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui

import TechDrawTools.TDToolsUtil as Utils
from TechDrawTools.AxoLengthDimension import (
    ParallelAxonometricDirectionsError,
    create_axonometric_length,
)
from VibeCADNativeTransaction import _OwnedDocumentTransaction
    

class CommandAxoLengthDimension:
    """Creates a 3D length dimension."""

    def __init__(self):
        """Initialize variables for the command that must exist at all times."""
        pass

    def GetResources(self):
        """Return a dictionary with data that will be used by the button or menu item."""
        return {'Pixmap': 'actions/TechDraw_AxoLengthDimension.svg',
                'Accel': "",
                'MenuText': QT_TRANSLATE_NOOP("TechDraw_AxoLengthDimension", "Axonometric Length Dimension"),
                'ToolTip': QT_TRANSLATE_NOOP("TechDraw_AxoLengthDimension", "Creates a length dimension in with "
                            "axonometric view, using selected edges or vertex pairs to define direction and measurement")}

    def Activated(self):
        """Run the following code when the command is activated (button press)."""
        if not self.IsActive():
            return

        edges = Utils.getSelEdges(2)
        if not edges:
            return
        vertexes = Utils.getSelVertexes(0)

        vertNames = list()
        edgeNames = list()
        if len(vertexes)<2:
            vertexes.append(edges[0].Vertexes[0])
            vertexes.append(edges[0].Vertexes[1])
            edgeNames = Utils.getSelEdgeNames(2)
        else:
            vertNames = Utils.getSelVertexNames(2)

        view = Utils.getSelView()
        if view is None:
            return
        document = view.Document
        page = view.findParentPage()
        if page is None:
            return

        measurement_names = vertNames if vertNames else edgeNames[:1]
        direction_names = Utils.getSelEdgeNames(2)
        transaction = _OwnedDocumentTransaction(
            document,
            "Create axonometric length dimension",
        )
        try:
            result = create_axonometric_length(
                view,
                measurement_names,
                direction_names[0],
                direction_names[1],
                label_position_in_view_mm=None,
            )
            if result.analysis.value_mode != "projected":
                result.dimension.Label = result.dimension.Label.replace(
                    "Dimension",
                    "Dimension3D",
                )
        except ParallelAxonometricDirectionsError:
            transaction.abort()
            Gui.Selection.clearSelection()
            return
        except Exception:
            transaction.abort()
            raise
        transaction.commit()
        view.requestPaint()

        Gui.Selection.clearSelection()

    def IsActive(self):
        """Return True when the command should be active or False when it should be disabled (greyed)."""
        document = App.ActiveDocument
        if (
            document is None
            or Gui.Control.activeDialog()
            or document.getBookedTransactionID() != 0
            or document.HasPendingTransaction
            or not Utils.havePage()
            or not Utils.haveView()
        ):
            return False
        selection = Gui.Selection.getSelectionEx()
        if len(selection) != 1:
            return False
        selected = selection[0]
        return (
            selected.Object is not None
            and selected.Object.Document is document
            and selected.Object.isDerivedFrom("TechDraw::DrawView")
            and sum(
                1
                for name in selected.SubElementNames
                if name.startswith("Edge")
            )
            >= 2
        )

#
# The command must be "registered" with a unique name by calling its class.
Gui.addCommand('TechDraw_AxoLengthDimension', CommandAxoLengthDimension())
