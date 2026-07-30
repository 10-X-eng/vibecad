# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2019 Yorik van Havre <yorik@uncreated.net>              *
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
"""Provides GUI tools to create WorkingPlaneProxy objects."""

## @package gui_planeproxy
# \ingroup draftguitools
# \brief Provides GUI tools to create WorkingPlaneProxy objects.

## \addtogroup draftguitools
# @{
from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui
from draftutils import gui_utils
from draftutils import timeline
from draftutils.transaction import run_document_mutation

__title__ = "FreeCAD Draft Workbench GUI Tools - Working plane-related tools"
__author__ = "Yorik van Havre, Werner Mayer, Martin Burbaum, Ken Cline, " "Dmitry Chigrin"
__url__ = "https://www.freecad.org"


class Draft_WorkingPlaneProxy:
    """The Draft_WorkingPlaneProxy command definition."""

    def GetResources(self):
        """Set icon, menu and tooltip."""
        return {
            "Pixmap": "Draft_PlaneProxy",
            "MenuText": QT_TRANSLATE_NOOP("Draft_WorkingPlaneProxy", "Working Plane Proxy"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Draft_WorkingPlaneProxy",
                "Creates a proxy object from the current working plane that allows to restore the camera position and visibility of objects",
            ),
        }

    def IsActive(self):
        """Return True when this command should be available."""
        return bool(gui_utils.get_3d_view())

    def Activated(self):
        """Execute when the command is called."""
        import Draft
        import WorkingPlane

        document = App.ActiveDocument
        if document is None:
            return
        placement = WorkingPlane.get_working_plane().get_placement()

        def create_proxy():
            proxy = Draft.make_workingplaneproxy(placement)
            if proxy is None:
                raise RuntimeError(
                    "Draft could not create the working-plane proxy"
                )
            timeline.accept_outputs((proxy,))

        run_document_mutation(
            document,
            QT_TRANSLATE_NOOP(
                "Draft",
                "Create Working Plane Proxy",
            ),
            create_proxy,
        )


Gui.addCommand("Draft_WorkingPlaneProxy", Draft_WorkingPlaneProxy())

## @}
