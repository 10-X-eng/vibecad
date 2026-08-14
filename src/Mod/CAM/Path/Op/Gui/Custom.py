# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2017 sliptonic <shopinthewoods@gmail.com>               *
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

import FreeCAD
import FreeCADGui
import Path.Base.Util as PathUtil
import Path.Main.Job as PathJob
import Path.Op.Custom as PathCustom
import Path.Op.Gui.Base as PathOpGui
import PathScripts.PathUtils as PathUtils
from Path.Main.Gui.Editor import CodeEditor

from PySide import QtGui
from PySide.QtCore import QT_TRANSLATE_NOOP

import os

__title__ = "CAM Custom Operation UI"
__author__ = "sliptonic (Brad Collette)"
__url__ = "https://www.freecad.org"
__doc__ = "Custom operation page controller and command implementation."

translate = FreeCAD.Qt.translate


class TaskPanelOpPage(PathOpGui.TaskPanelPage):
    """Page controller class for the Custom operation."""

    def getForm(self):
        """getForm() ... returns UI"""
        form = FreeCADGui.PySideUic.loadUi(":/panels/PageOpCustomEdit.ui")

        comboToPropertyMap = [("source", "Source")]
        enumTups = PathCustom.ObjectCustom.propertyEnumerations(dataType="raw")

        self.populateCombobox(form, enumTups, comboToPropertyMap)

        # add editor with lines enumeration
        self.editor = CodeEditor()
        form.txtGCodeBox.layout().removeWidget(form.txtGCode)
        form.txtGCode.deleteLater()
        form.txtGCodeBox.layout().addWidget(self.editor)

        return form

    def getFields(self, obj):
        """getFields(obj) ... transfers values from UI to obj's properties"""
        self.updateToolController(obj, self.form.toolController)
        self.updateCoolant(obj, self.form.coolantController)
        if obj.Source != str(self.form.source.currentData()):
            obj.Source = str(self.form.source.currentData())
        if obj.GcodeFile != str(self.form.fileName.text()):
            obj.GcodeFile = str(self.form.fileName.text())
        if obj.Gcode != str(self.editor.toPlainText().split("\n")):
            obj.Gcode = self.editor.toPlainText().split("\n")

    def setFields(self, obj):
        """setFields(obj) ... transfers obj's property values to UI"""
        self.setupToolController(obj, self.form.toolController)
        self.setupCoolant(obj, self.form.coolantController)
        self.selectInComboBox(obj.Source, self.form.source)
        self.form.fileName.setText(obj.GcodeFile)
        self.editor.setText("\n".join(obj.Gcode))

        self.updateVisibility()

    def getSignalsForUpdate(self, obj):
        """getSignalsForUpdate(obj) ... return list of signals for updating obj"""
        signals = []
        signals.append(self.form.toolController.currentIndexChanged)
        signals.append(self.form.coolantController.currentIndexChanged)
        signals.append(self.form.source.currentIndexChanged)
        signals.append(self.form.fileName.editingFinished)
        signals.append(self.editor.textChanged)

        return signals

    def updateVisibility(self):
        source = self.obj.getEnumerationsOfProperty("Source")[self.form.source.currentIndex()]
        if source == "File":
            self.form.fileNameBox.show()
            self.form.verticalSpacerBox.show()
            self.form.txtGCodeBox.hide()
        else:
            self.form.txtGCodeBox.show()
            self.form.fileNameBox.hide()
            self.form.verticalSpacerBox.hide()

    def registerSignalHandlers(self, obj):
        self.connectSignal(
            self.form.source.currentIndexChanged,
            self.updateVisibility,
        )
        self.connectSignal(
            self.form.setFileName.clicked,
            self.setFileName,
        )

    def setFileName(self):
        dirname = os.path.dirname(self.obj.GcodeFile)
        if not dirname:
            dirname = os.path.dirname(self.document.FileName)
        filter1 = "All Files (*)"
        filter2 = "Text files (*.cnc *.g *.gc *.gco *.gcode *.nc *.ncc *.ngc *.tap *.txt)"
        filters = translate("CAM_Custom", ";;".join((filter1, filter2)))
        selected_filter = translate("CAM_Custom", filter2)
        filename = QtGui.QFileDialog.getOpenFileName(
            self.form,
            translate("CAM_Custom", "Select file containing the gcode"),
            dirname,
            filters,
            selected_filter,
        )
        if filename and filename[0]:
            self.obj.GcodeFile = str(filename[0])
            self.setFields(self.obj)


def CreateInTransaction(
    document,
    job,
    *,
    name="Custom",
    label="Custom",
    tool_controller,
    coolant_mode="None",
    gcode=(),
):
    """Create one fully configured Custom operation in the caller transaction."""

    if document is None or getattr(job, "Document", None) is not document:
        raise ValueError("A CAM Custom operation requires one Job in the target document")
    if not isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob):
        raise ValueError("A CAM Custom operation requires a native CAM Job")
    if (
        getattr(tool_controller, "Document", None) is not document
        or tool_controller
        not in tuple(getattr(getattr(job, "Tools", None), "Group", ()) or ())
    ):
        raise ValueError(
            "A CAM Custom operation requires one controller owned by its Job"
        )
    internal_name = str(name or "").strip()
    tree_label = str(label or "").strip()
    if not internal_name or not tree_label:
        raise ValueError("A CAM Custom operation requires a nonempty name and label")
    coolant = str(coolant_mode or "").strip().capitalize()
    if coolant not in {"None", "Flood", "Mist"}:
        raise ValueError("A CAM Custom coolant mode must be None, Flood, or Mist")
    lines = tuple(gcode)
    if not lines or any(not isinstance(line, str) or not line for line in lines):
        raise ValueError("A CAM Custom operation requires nonempty G-code lines")

    result = document.addObject("Path::FeaturePython", internal_name)
    result = PathCustom.Create(
        internal_name,
        obj=result,
        parentJob=job,
        toolController=tool_controller,
    )
    result.Label = tree_label
    result.Source = "Text"
    result.GcodeFile = ""
    result.Gcode = list(lines)
    result.CoolantMode = coolant
    if FreeCAD.GuiUp and getattr(result, "ViewObject", None) is not None:
        result.ViewObject.Proxy = PathOpGui.ViewProvider(result.ViewObject, Command.res)
    return result


def _validate_custom_result(
    document,
    job,
    result,
    tool_controller,
    coolant_mode,
    *,
    require_path=True,
):
    """Reject a Custom result that lost its exact durable CAM identity."""

    result_name = str(getattr(result, "Name", "") or "")
    result_id = int(getattr(result, "ID", 0) or 0)
    view = getattr(result, "ViewObject", None)
    if (
        not result_name
        or not result_id
        or document.getObject(result_name) is not result
        or document.getObject(result_id) is not result
        or getattr(result, "Document", None) is not document
        or not result.isDerivedFrom("Path::Feature")
        or not isinstance(getattr(result, "Proxy", None), PathCustom.ObjectCustom)
        or isinstance(getattr(result, "Proxy", None), PathCustom.ObjectEmbeddedPath)
        or view is None
        or not isinstance(getattr(view, "Proxy", None), PathOpGui.ViewProvider)
        or result not in tuple(getattr(job.Operations, "Group", ()) or ())
        or PathUtils.findParentJob(result) is not job
        or PathUtil.timelineParentJob(result) is not job
        or PathUtil.toolControllerForOp(result) is not tool_controller
        or PathUtil.coolantModeForOp(result) != coolant_mode
        or str(result.Source) != "Text"
        or str(result.GcodeFile)
        or not tuple(result.Gcode)
        or not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(result)
        or tuple(getattr(result, "VibeCADTimelineReplacedInputs", ()) or ())
        or not result.isValid()
        or (require_path and not tuple(getattr(result.Path, "Commands", ()) or ()))
    ):
        raise RuntimeError("The CAM Custom operation was not created correctly")
    return result


Command = PathOpGui.SetupOperation(
    "Custom",
    PathCustom.Create,
    TaskPanelOpPage,
    "CAM_Custom",
    QT_TRANSLATE_NOOP("CAM_Custom", "Custom"),
    QT_TRANSLATE_NOOP("CAM_Custom", "Create custom G-code snippet"),
    PathCustom.SetupProperties,
)

FreeCAD.Console.PrintLog("Loading PathCustomGui... done\n")
