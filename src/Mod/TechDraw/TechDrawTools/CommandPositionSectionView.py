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
Provides the TechDraw PositionSectionView GuiCommand.
00.01 2021/03/17 C++ Basic version
00.02 2023/12/21 Option to select an edge and its corresponding vertex
"""

__title__ = "TechDrawTools.CommandPositionSectionView"
__author__ = "edi"
__url__ = "https://www.freecad.org"
__version__ = "00.02"
__date__ = "2023/12/21"

from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui

from VibeCADNativeTransaction import _OwnedDocumentTransaction

class CommandPositionSectionView:
    """Orthogonally align a section view with its source view."""

    def __init__(self):
        """Initialize variables for the command that must exist at all times."""
        pass

    def GetResources(self):
        """Return a dictionary with data that will be used by the button or menu item."""
        return {'Pixmap': 'TechDraw_ExtensionPositionSectionView.svg',
                'Accel': "",
                'MenuText': QT_TRANSLATE_NOOP("TechDraw_PositionSectionView", "Position Section View"),
                'ToolTip': QT_TRANSLATE_NOOP("TechDraw_PositionSectionView",
                  "Aligns the selected section view with its source view orthogonally or the selected edge in the section view to the selected vertex in the base view")}

    def Activated(self):
        """Run the following code when the command is activated (button pressed)."""
        prepared = self._prepareAlignment()
        if prepared is None:
            return
        sectionView, moveVector = prepared
        if moveVector.Length <= 1e-9:
            return
        document = sectionView.Document
        transaction = _OwnedDocumentTransaction(
            document,
            "Position section view",
        )
        try:
            sectionView.X = (
                sectionView.X.Value - moveVector.x
            )
            sectionView.Y = (
                sectionView.Y.Value - moveVector.y
            )
            document.recompute()
            if {"Invalid", "Error"} & set(sectionView.State):
                raise RuntimeError(
                    "The aligned section view is invalid"
                )
        except Exception:
            transaction.abort()
            raise
        transaction.commit()
        sectionView.requestPaint()

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
        return self._prepareAlignment() is not None

    def _prepareAlignment(self):
        selection = Gui.Selection.getSelectionEx()
        if len(selection) not in (1, 2):
            return None

        if len(selection) == 1:
            selected = selection[0]
            sectionView = selected.Object
            if (
                sectionView is None
                or sectionView.TypeId
                != "TechDraw::DrawViewSection"
            ):
                return None
            baseView = self._alignmentBase(
                sectionView.BaseView
            )
            if not self._sameDrawing(
                sectionView,
                baseView,
            ):
                return None
            basePoint = App.Vector(
                baseView.X.Value,
                baseView.Y.Value,
                0.0,
            )
            sectionPoint = App.Vector(
                sectionView.X.Value,
                sectionView.Y.Value,
                0.0,
            )
            moveVector = sectionPoint.sub(basePoint)
            if abs(moveVector.x) > abs(moveVector.y):
                moveVector.x = 0.0
            else:
                moveVector.y = 0.0
            return sectionView, moveVector

        sectionSelection = None
        baseSelection = None
        for selected in selection:
            obj = selected.Object
            names = list(selected.SubElementNames)
            if (
                obj is not None
                and obj.TypeId == "TechDraw::DrawViewSection"
                and len(names) == 1
                and names[0].startswith("Edge")
            ):
                if sectionSelection is not None:
                    return None
                sectionSelection = selected
            elif (
                obj is not None
                and obj.isDerivedFrom("TechDraw::DrawView")
                and len(names) == 1
                and names[0].startswith("Vertex")
            ):
                if baseSelection is not None:
                    return None
                baseSelection = selected
            else:
                return None
        if sectionSelection is None or baseSelection is None:
            return None

        sectionView = sectionSelection.Object
        selectedBaseView = baseSelection.Object
        baseView = self._alignmentBase(selectedBaseView)
        if not self._sameDrawing(sectionView, baseView):
            return None

        sectionEdge = sectionView.getEdgeBySelection(
            sectionSelection.SubElementNames[0]
        )
        baseVertex = selectedBaseView.getVertexBySelection(
            baseSelection.SubElementNames[0]
        )
        if (
            sectionEdge is None
            or baseVertex is None
            or len(sectionEdge.Vertexes) < 1
            or not hasattr(sectionEdge.Curve, "Direction")
        ):
            return None
        sectionDirection = sectionEdge.Curve.Direction
        if sectionDirection.Length <= 1e-12:
            return None

        basePoint = baseVertex.Point
        sectionPoint = sectionEdge.Vertexes[0].Point
        baseScale = float(baseView.getScale())
        sectionScale = float(sectionView.getScale())
        if baseScale <= 0.0 or sectionScale <= 0.0:
            return None
        basePoint = (
            App.Vector(
                baseView.X.Value,
                baseView.Y.Value,
                0.0,
            )
            + basePoint * baseScale
        )
        sectionPoint = (
            App.Vector(
                sectionView.X.Value,
                sectionView.Y.Value,
                0.0,
            )
            + sectionPoint * sectionScale
        )
        trianglePoint = self.getTrianglePoint(
            sectionPoint,
            sectionDirection,
            basePoint,
        )
        if trianglePoint is None:
            return None
        return sectionView, trianglePoint.sub(basePoint)

    def _alignmentBase(self, baseView):
        if (
            baseView is not None
            and baseView.TypeId
            == "TechDraw::DrawProjGroupItem"
        ):
            parents = [
                obj
                for obj in baseView.InList
                if obj.TypeId == "TechDraw::DrawProjGroup"
            ]
            if len(parents) != 1:
                return None
            return parents[0]
        return baseView

    def _sameDrawing(self, sectionView, baseView):
        if sectionView is None or baseView is None:
            return False
        document = sectionView.Document
        try:
            document_is_live = (
                document is not None
                and App.getDocument(document.Name) is document
            )
        except (NameError, ReferenceError, RuntimeError):
            document_is_live = False
        if (
            not document_is_live
            or baseView.Document is not document
        ):
            return False
        sectionPage = sectionView.findParentPage()
        basePage = baseView.findParentPage()
        return (
            sectionPage is not None
            and basePage is sectionPage
        )

    def getTrianglePoint(self,p1,dir,p2):
        '''
        Calculate the third vertex of a right triangle.

        Parameters:
        p1, p2 : vertices of the hypotenuse
        dir    : direction vector of one leg (kathete)

        Returns:
        p3 : the third vertex completing the right triangle
        '''
        a = -dir.y
        b = dir.x
        c1 = p1.x * a + p1.y * b
        c2 = -p2.x * b + p2.y * a
        ab = a * a + b * b
        if ab <= 1e-24:
            return None
        x = (c1 * a - c2 * b) / ab
        y = (c2 * a + c1 * b) / ab
        return App.Vector(x,y,0.0)

# The command must be "registered" with a unique name by calling its class.
Gui.addCommand('TechDraw_ExtensionPositionSectionView', CommandPositionSectionView())
