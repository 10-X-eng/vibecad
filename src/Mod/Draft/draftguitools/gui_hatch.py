# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2021 Yorik van Havre <yorik@uncreated.net>              *
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


"""This module contains FreeCAD commands for the Draft workbench"""

import os
import FreeCAD
from draftguitools import gui_base
from draftutils import params
from draftutils.todo import todo
from draftutils import timeline
from draftutils.transaction import close_task_dialog
from draftutils.transaction import ObjectReference
from draftutils.transaction import reset_document_edit
from draftutils.transaction import run_document_mutation
from draftutils.translate import QT_TRANSLATE_NOOP, translate


class Draft_Hatch(gui_base.GuiCommandNeedsSelection):

    def GetResources(self):

        return {
            "Pixmap": "Draft_Hatch",
            "MenuText": QT_TRANSLATE_NOOP("Draft_Hatch", "Hatch"),
            "Accel": "H, A",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Draft_Hatch", "Creates hatches on the faces of a selected object"
            ),
        }

    def Activated(self):

        import FreeCADGui

        super().Activated()
        selection = FreeCADGui.Selection.getSelection(self.doc.Name)
        if selection:
            baseobj = selection[0]
            gui_document = FreeCADGui.getDocument(self.doc.Name)
            task = FreeCADGui.Control.showDialog(
                Draft_Hatch_TaskPanel(baseobj),
                gui_document,
            )
            task.setDocumentName(self.doc.Name)
            task.setAutoCloseOnDeletedDocument(True)
        else:
            FreeCAD.Console.PrintError(
                translate("Draft", "Choose a base object before using this command") + "\n"
            )


class Draft_Hatch_TaskPanel:

    def __init__(self, baseobj):

        import FreeCADGui
        from PySide import QtCore, QtGui
        import Draft_rc

        self.baseobj = baseobj
        self.base_reference = ObjectReference.capture(baseobj)
        self.form = FreeCADGui.PySideUic.loadUi(":/ui/dialogHatch.ui")
        self.form.setWindowIcon(QtGui.QIcon(":/icons/Draft_Hatch.svg"))
        self.form.File.fileNameChanged.connect(self.onFileChanged)
        self.form.File.setFileName(params.get_param("HatchPatternFile"))
        pat = params.get_param("HatchPatternName")
        if pat in [self.form.Pattern.itemText(i) for i in range(self.form.Pattern.count())]:
            self.form.Pattern.setCurrentText(pat)
        self.form.Scale.setValue(params.get_param("HatchPatternScale"))
        self.form.Rotation.setValue(params.get_param("HatchPatternRotation"))
        self.form.Translate.setChecked(params.get_param("HatchPatternTranslate"))
        todo.delay(self.form.setFocus, None)  # Make sure using Esc works.

    def accept(self):

        import FreeCADGui
        import Draft

        params.set_param("HatchPatternFile", self.form.File.property("fileName"))
        params.set_param("HatchPatternName", self.form.Pattern.currentText())
        params.set_param("HatchPatternScale", self.form.Scale.value())
        params.set_param("HatchPatternRotation", self.form.Rotation.value())
        params.set_param("HatchPatternTranslate", self.form.Translate.isChecked())
        baseobj = self.base_reference.resolve()
        if baseobj is None:
            raise RuntimeError(
                "The Hatch source is no longer usable at the current "
                "History position"
            )
        document = baseobj.Document
        filename = self.form.File.property("fileName")
        pattern = self.form.Pattern.currentText()
        scale = self.form.Scale.value()
        rotation = self.form.Rotation.value()
        translate_pattern = self.form.Translate.isChecked()

        if hasattr(self.baseobj, "File") and hasattr(self.baseobj, "Pattern"):
            # modify existing hatch object
            def edit_hatch():
                baseobj.File = filename
                baseobj.Pattern = pattern
                baseobj.Scale = scale
                baseobj.Rotation = rotation
                baseobj.Translate = translate_pattern

            run_document_mutation(
                document,
                translate("draft", "Edit Hatch"),
                edit_hatch,
                objects=(baseobj,),
            )
        else:
            # create new hatch object
            def create_hatch():
                hatch = Draft.make_hatch(
                    baseobject=baseobj,
                    filename=filename,
                    pattern=pattern,
                    scale=scale,
                    rotation=rotation,
                    translate=translate_pattern,
                )
                if hatch is None:
                    raise RuntimeError("Draft could not create the hatch")
                timeline.accept_derived_output(hatch, (baseobj,))

            run_document_mutation(
                document,
                translate("draft", "Create Hatch"),
                create_hatch,
                objects=(baseobj,),
            )
        self.reject()

    def reject(self):

        import FreeCADGui

        close_task_dialog(self.base_reference.document)
        reset_document_edit(self.base_reference.document)

    def onFileChanged(self, filename):

        if filename[0] == "." and self.baseobj:
            # File path relative to the FreeCAD file directory.
            filename = os.path.join(os.path.dirname(self.baseobj.Document.FileName), filename)
            filename = os.path.abspath(filename)

        pat = self.form.Pattern.currentText()
        self.form.Pattern.clear()
        patterns = self.getPatterns(filename)
        self.form.Pattern.addItems(patterns)
        if pat in patterns:
            self.form.Pattern.setCurrentText(pat)

    def getPatterns(self, filename):
        """returns a list of pattern names found in a PAT file"""
        patterns = []
        if os.path.exists(filename):
            with open(filename) as patfile:
                for line in patfile:
                    if line.startswith("*"):
                        patterns.append(line.split(",")[0][1:])
        return patterns


if FreeCAD.GuiUp:
    import FreeCADGui

    FreeCADGui.addCommand("Draft_Hatch", Draft_Hatch())
