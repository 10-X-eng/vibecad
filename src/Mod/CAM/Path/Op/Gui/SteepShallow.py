# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the FreeCAD project.

################################################################################
#                                                                              #
#   FreeCAD is free software: you can redistribute it and/or modify            #
#   it under the terms of the GNU Lesser General Public License as             #
#   published by the Free Software Foundation, either version 2.1              #
#   of the License, or (at your option) any later version.                     #
#                                                                              #
#   FreeCAD is distributed in the hope that it will be useful,                 #
#   but WITHOUT ANY WARRANTY; without even the implied warranty                #
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                    #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with FreeCAD. If not, see https://www.gnu.org/licenses       #
#                                                                              #
################################################################################


from PySide.QtCore import QT_TRANSLATE_NOOP
import FreeCAD
import FreeCADGui

import Path
import Path.Base.Gui.Util as PathGuiUtil
import Path.Op.Gui.Base as PathOpGui
import Path.Op.SteepShallow as PathSteepShallow

__title__ = "CAM Steep/Shallow Operation UI"
__author__ = "FreeCAD CAM developers"
__url__ = "https://www.freecad.org"
__doc__ = (
    "Steep/Shallow 3D finishing operation page controller and command implementation."
)


if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


class TaskPanelOpPage(PathOpGui.TaskPanelPage):
    """Page controller for the Steep/Shallow 3D finishing operation."""

    def initPage(self, obj):
        self.slopeThresholdSpinBox = PathGuiUtil.QuantitySpinBox(
            self.form.slopeThreshold, obj, "SlopeThreshold"
        )
        self.stepOverSpinBox = PathGuiUtil.QuantitySpinBox(
            self.form.stepOver, obj, "StepOver"
        )
        self.boundaryOverlapSpinBox = PathGuiUtil.QuantitySpinBox(
            self.form.boundaryOverlap, obj, "BoundaryOverlap"
        )
        self.sampleIntervalSpinBox = PathGuiUtil.QuantitySpinBox(
            self.form.sampleInterval, obj, "SampleInterval"
        )
        self.restReferenceToolDiameterSpinBox = PathGuiUtil.QuantitySpinBox(
            self.form.restReferenceToolDiameter, obj, "RestReferenceToolDiameter"
        )
        self.form.useRestMachining.toggled.connect(
            self.form.restReferenceToolDiameter.setEnabled
        )

    def getForm(self):
        form = FreeCADGui.PySideUic.loadUi(":/panels/PageOpSteepShallowEdit.ui")
        comboToPropertyMap = [("cutMode", "CutMode")]
        enumTups = PathSteepShallow.ObjectSteepShallow.propertyEnumerations(
            dataType="raw"
        )
        PathGuiUtil.populateCombobox(form, enumTups, comboToPropertyMap)
        return form

    def getFields(self, obj):
        self.updateToolController(obj, self.form.toolController)
        self.updateCoolant(obj, self.form.coolantController)

        if obj.CutMode != str(self.form.cutMode.currentData()):
            obj.CutMode = str(self.form.cutMode.currentData())

        if obj.UseRestMachining != self.form.useRestMachining.isChecked():
            obj.UseRestMachining = self.form.useRestMachining.isChecked()

        for sb in (
            self.slopeThresholdSpinBox,
            self.stepOverSpinBox,
            self.boundaryOverlapSpinBox,
            self.sampleIntervalSpinBox,
            self.restReferenceToolDiameterSpinBox,
        ):
            sb.updateProperty()

    def setFields(self, obj):
        self.setupToolController(obj, self.form.toolController)
        self.setupCoolant(obj, self.form.coolantController)
        self.selectInComboBox(obj.CutMode, self.form.cutMode)
        self.form.useRestMachining.setChecked(obj.UseRestMachining)
        self.form.restReferenceToolDiameter.setEnabled(obj.UseRestMachining)
        self.updateQuantitySpinBoxes()

    def updateQuantitySpinBoxes(self, index=None):
        for sb in (
            self.slopeThresholdSpinBox,
            self.stepOverSpinBox,
            self.boundaryOverlapSpinBox,
            self.sampleIntervalSpinBox,
            self.restReferenceToolDiameterSpinBox,
        ):
            sb.updateWidget()

    def getSignalsForUpdate(self, obj):
        return [
            self.form.toolController.currentIndexChanged,
            self.form.coolantController.currentIndexChanged,
            self.form.cutMode.currentIndexChanged,
            self.form.slopeThreshold.editingFinished,
            self.form.stepOver.editingFinished,
            self.form.boundaryOverlap.editingFinished,
            self.form.sampleInterval.editingFinished,
            self.form.useRestMachining.toggled,
            self.form.restReferenceToolDiameter.editingFinished,
        ]


Command = PathOpGui.SetupOperation(
    "SteepShallow",
    PathSteepShallow.Create,
    TaskPanelOpPage,
    "CAM_SteepShallow",
    QT_TRANSLATE_NOOP("CAM_SteepShallow", "Steep and Shallow"),
    QT_TRANSLATE_NOOP(
        "CAM_SteepShallow",
        "3D finishing that cuts steep regions with constant-Z contours "
        "and shallow regions with surface-following passes.",
    ),
    PathSteepShallow.SetupProperties,
)


FreeCAD.Console.PrintLog("Loading PathSteepShallowGui... done\n")
