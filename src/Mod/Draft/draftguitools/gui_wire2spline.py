# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   (c) 2009, 2010 Yorik van Havre <yorik@uncreated.net>                  *
# *   (c) 2009, 2010 Ken Cline <cline@frii.com>                             *
# *   (c) 2020 Eliud Cabrera Castillo <e.cabrera-castillo@tum.de>           *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful,            *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with FreeCAD; if not, write to the Free Software        *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************
"""Provides GUI tools to convert polylines to B-splines and back.

These tools work on polylines and B-splines which have multiple points.

Essentially, the points of the original object are extracted
and passed to the `make_wire` or `make_bspline` functions,
depending on the desired result.
"""

## @package gui_wire2spline
# \ingroup draftguitools
# \brief Provides GUI tools to convert polylines to B-splines and back.

## \addtogroup draftguitools
# @{
from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCADGui as Gui
from draftguitools import gui_base_original
from draftutils import utils
from draftutils.translate import translate


class WireToBSpline(gui_base_original.Modifier):
    """Gui Command for the Wire to BSpline tool."""

    def __init__(self):
        super().__init__()
        self.running = False

    def GetResources(self):
        """Set icon, menu and tooltip."""

        return {
            "Pixmap": "Draft_WireToBSpline",
            "MenuText": QT_TRANSLATE_NOOP("Draft_WireToBSpline", "Convert Wire/B-Spline"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Draft_WireToBSpline",
                "Converts the selected polyline to a B-spline, or the selected B-spline to a polyline",
            ),
        }

    def Activated(self):
        """Execute when the command is called."""
        if self.running:
            self.finish()

        selection = Gui.Selection.getSelection()
        if not selection or utils.getType(selection[0]) not in ["Wire", "BSpline"]:
            return

        super(WireToBSpline, self).Activated(name="Convert polyline/B-spline")
        if not self.doc:
            self.finish()
            return

        obj = selection[0]
        Gui.addModule("draftutils.timeline")
        commands = [
            "obj = FreeCAD.ActiveDocument." + obj.Name,
            "converted = draftutils.timeline.convert_wire_replacement(obj)",
            "FreeCAD.ActiveDocument.recompute()",
        ]
        self.commit(
            translate("draft", "Convert polyline/B-spline"),
            commands,
            inputs=(obj,),
        )
        self.finish()


Gui.addCommand("Draft_WireToBSpline", WireToBSpline())

## @}
