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
from SectionViewPosition import (
    SectionViewPositionError,
    alignment_base,
    apply_section_view_position,
    calculate_axis_alignment,
    calculate_edge_vertex_alignment,
    same_drawing,
    triangle_point,
)

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
            apply_section_view_position(
                sectionView,
                sectionView.X.Value - moveVector.x,
                sectionView.Y.Value - moveVector.y,
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
            try:
                prepared = calculate_axis_alignment(sectionView, "nearest")
            except SectionViewPositionError:
                return None
            return sectionView, prepared["move_vector"]

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
        try:
            prepared = calculate_edge_vertex_alignment(
                sectionView,
                sectionSelection.SubElementNames[0],
                selectedBaseView,
                baseSelection.SubElementNames[0],
            )
        except (AttributeError, SectionViewPositionError):
            return None
        return sectionView, prepared["move_vector"]

    def _alignmentBase(self, baseView):
        return alignment_base(baseView)

    def _sameDrawing(self, sectionView, baseView):
        return same_drawing(sectionView, baseView)

    def getTrianglePoint(self,p1,dir,p2):
        '''
        Calculate the third vertex of a right triangle.

        Parameters:
        p1, p2 : vertices of the hypotenuse
        dir    : direction vector of one leg (kathete)

        Returns:
        p3 : the third vertex completing the right triangle
        '''
        try:
            return triangle_point(p1, dir, p2)
        except SectionViewPositionError:
            return None

# The command must be "registered" with a unique name by calling its class.
Gui.addCommand('TechDraw_ExtensionPositionSectionView', CommandPositionSectionView())
