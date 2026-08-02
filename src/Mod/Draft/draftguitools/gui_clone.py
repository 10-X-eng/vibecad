# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2009, 2010 Yorik van Havre <yorik@uncreated.net>        *
# *   Copyright (c) 2009, 2010 Ken Cline <cline@frii.com>                   *
# *   Copyright (c) 2020 Eliud Cabrera Castillo <e.cabrera-castillo@tum.de> *
# *   Copyright (c) 2023 FreeCAD Project Association                        *
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
"""Provides GUI tools to create Clone objects.

The clone is basically a simple copy of the `Shape` of another object,
whether that is a Draft object or any other 3D object.

The Clone's `Shape` can be scaled in size in any direction.

This implementation was developed before the `App::Link` object was created.
In many cases using `App::Link` makes more sense, as this object is
more memory efficient as it reuses the same internal `Shape`
instead of creating a copy of it.
"""

## @package gui_clone
# \ingroup draftguitools
# \brief Provides GUI tools to create Clone objects.

## \addtogroup draftguitools
# @{
from PySide.QtCore import QT_TRANSLATE_NOOP

import FreeCAD as App
import FreeCADGui as Gui
import Draft
import Draft_rc
import draftguitools.gui_base_original as gui_base_original
import draftguitools.gui_tool_utils as gui_tool_utils
import draftutils.todo as todo
from draftutils import timeline
from draftutils.messages import _msg, _wrn
from draftutils.transaction import run_document_mutation
from draftutils.translate import translate

# The module is used to prevent complaints from code checkers (flake8)
True if Draft_rc.__name__ else False


class Clone(gui_base_original.Modifier):
    """Gui Command for the Clone tool."""

    def __init__(self):
        super().__init__()
        self.moveAfterCloning = False

    def GetResources(self):
        """Set icon, menu and tooltip."""

        return {
            "Pixmap": "Draft_Clone",
            "Accel": "C, L",
            "MenuText": QT_TRANSLATE_NOOP("Draft_Clone", "Clone"),
            "ToolTip": QT_TRANSLATE_NOOP("Draft_Clone", "Creates a clone of the selected objects"),
        }

    def Activated(self):
        """Execute when the command is called."""
        super().Activated(name="Clone")
        if not self.ui:
            return
        if not Gui.Selection.getSelection():
            self.ui.selectUi(on_close_call=self.finish)
            _msg(translate("draft", "Select an object to clone"))
            self.call = self.view.addEventCallback("SoEvent", gui_tool_utils.selectObject)
        else:
            self.proceed()

    def proceed(self):
        """Proceed with the command if objects were selected."""
        objs = Gui.Selection.getSelection()
        if not objs:
            self.moveAfterCloning = False
            self.finish()
            return
        objs_shape = [obj for obj in objs if hasattr(obj, "Shape")]
        if not objs_shape:
            _wrn(translate("draft", "Cannot clone objects without a shape, aborting"))
            self.moveAfterCloning = False
            self.finish()
            return
        elif len(objs_shape) < len(objs):
            _wrn(translate("draft", "Cannot clone objects without a shape, skipping them"))

        def create_clones():
            outputs = []
            for obj in objs_shape:
                clone = Draft.make_clone(obj)
                if clone is None:
                    raise RuntimeError("Draft could not create every clone")
                outputs.append(clone)
            timeline.accept_outputs(outputs)
            return outputs

        outputs = run_document_mutation(
            self.doc,
            translate("Draft", "Create Clone"),
            create_clones,
            objects=objs_shape,
        )

        Gui.Selection.clearSelection()
        for clone in reversed(outputs):
            if (
                clone.Document is self.doc
                and self.doc.getObject(clone.Name) is clone
            ):
                Gui.Selection.addSelection(clone)
        self.finish()

    def finish(self):
        """Terminate the operation of the tool."""
        if self.call is not None:
            self.end_callbacks(self.call)
        super().finish()
        if self.moveAfterCloning:
            todo.ToDo.delay(Gui.runCommand, "Draft_Move")


Draft_Clone = Clone
Gui.addCommand("Draft_Clone", Clone())

## @}
