# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2017 LTS <SammelLothar@gmx.de>
# SPDX-FileCopyrightText: 2020 Schildkroet
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

import FreeCAD as App
import FreeCADGui
import Path

from Path.Base.Gui import Util as PathGuiUtil
import Path.Base.Util as PathUtil
from Path.Base.Util import (
    activeForOp,
    markTimelineReplacedInputs,
    shouldRestoreTimelineReplacedInput,
)
from Path.CommandBoundary import (
    TaskDocumentTransaction,
    begin_task_launch,
    can_start_document_command,
    is_document_object,
    is_timeline_input_usable,
    open_timeline_mode_zero_editor,
)
from Path.Dressup import LeadInOut as LeadInOutCore
from Path.Dressup import Utils as PathDressup
from PathPythonGui.simple_edit_panel import SimpleEditPanel
from PathScripts import PathUtils as PathUtils

__doc__ = """LeadInOut Dressup USE ROLL-ON ROLL-OFF to profile"""

from PySide.QtCore import QT_TRANSLATE_NOOP

translate = App.Qt.translate

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())

lead_styles = LeadInOutCore.lead_styles


class ObjectDressup(LeadInOutCore.ObjectDressup):
    """GUI-restorable proxy backed by the shared headless generator."""

    pass


class TaskDressupLeadInOut(SimpleEditPanel):
    _transaction_name = "Edit LeadInOut Dress-up"
    _ui_file = ":/panels/DressUpLeadInOutEdit.ui"

    def setupUi(self):
        self.setupSpinBoxes()
        self.setupGroupBoxes()
        self.setupDynamicVisibility()
        self.setFields()
        self.pageRegisterSignalHandlers()

    def setupSpinBoxes(self):
        self.connectWidget("InvertIn", self.form.chkInvertDirectionIn)
        self.connectWidget("InvertOut", self.form.chkInvertDirectionOut)
        self.connectWidget("StyleIn", self.form.cboStyleIn)
        self.connectWidget("StyleOut", self.form.cboStyleOut)
        self.radiusIn = PathGuiUtil.QuantitySpinBox(
            self.form.dspRadiusIn, self.obj, "RadiusIn"
        )
        self.radiusOut = PathGuiUtil.QuantitySpinBox(
            self.form.dspRadiusOut, self.obj, "RadiusOut"
        )
        self.angleIn = PathGuiUtil.QuantitySpinBox(
            self.form.dspAngleIn, self.obj, "AngleIn"
        )
        self.angleOut = PathGuiUtil.QuantitySpinBox(
            self.form.dspAngleOut, self.obj, "AngleOut"
        )
        self.offsetIn = PathGuiUtil.QuantitySpinBox(
            self.form.dspOffsetIn, self.obj, "OffsetIn"
        )
        self.offsetOut = PathGuiUtil.QuantitySpinBox(
            self.form.dspOffsetOut, self.obj, "OffsetOut"
        )
        self.connectWidget("RapidPlunge", self.form.chkRapidPlunge)
        self.retractThreshold = PathGuiUtil.QuantitySpinBox(
            self.form.dspRetractThreshold, self.obj, "RetractThreshold"
        )

        self.radiusIn.updateWidget()
        self.radiusOut.updateWidget()
        self.angleIn.updateWidget()
        self.angleOut.updateWidget()
        self.offsetIn.updateWidget()
        self.offsetOut.updateWidget()
        self.retractThreshold.updateWidget()

    def setupGroupBoxes(self):
        self.form.groupBoxIn.setChecked(self.obj.LeadIn)
        self.form.groupBoxOut.setChecked(self.obj.LeadOut)
        self.form.groupBoxIn.clicked.connect(self.handleGroupBoxCheck)
        self.form.groupBoxOut.clicked.connect(self.handleGroupBoxCheck)

    def handleGroupBoxCheck(self):
        self.obj.LeadIn = self.form.groupBoxIn.isChecked()
        self.obj.LeadOut = self.form.groupBoxOut.isChecked()

    def setupDynamicVisibility(self):
        self.form.cboStyleIn.currentIndexChanged.connect(self.updateLeadInVisibility)
        self.form.cboStyleOut.currentIndexChanged.connect(self.updateLeadOutVisibility)
        self.updateLeadInVisibility()
        self.updateLeadOutVisibility()

    def getSignalsForUpdate(self):
        signals = []
        signals.append(self.form.dspRadiusIn.editingFinished)
        signals.append(self.form.dspRadiusOut.editingFinished)
        signals.append(self.form.dspAngleIn.editingFinished)
        signals.append(self.form.dspAngleOut.editingFinished)
        signals.append(self.form.dspOffsetIn.editingFinished)
        signals.append(self.form.dspOffsetOut.editingFinished)
        signals.append(self.form.dspRetractThreshold.editingFinished)
        return signals

    def pageGetFields(self):
        PathGuiUtil.updateInputField(self.obj, "RadiusIn", self.form.dspRadiusIn)
        PathGuiUtil.updateInputField(self.obj, "RadiusOut", self.form.dspRadiusOut)
        PathGuiUtil.updateInputField(self.obj, "AngleIn", self.form.dspAngleIn)
        PathGuiUtil.updateInputField(self.obj, "AngleOut", self.form.dspAngleOut)
        PathGuiUtil.updateInputField(self.obj, "OffsetIn", self.form.dspOffsetIn)
        PathGuiUtil.updateInputField(self.obj, "OffsetOut", self.form.dspOffsetOut)
        PathGuiUtil.updateInputField(
            self.obj, "RetractThreshold", self.form.dspRetractThreshold
        )

    def pageRegisterSignalHandlers(self):
        for signal in self.getSignalsForUpdate():
            signal.connect(self.pageGetFields)

    # Shared hideModes for both LeadIn and LeadOut
    hideModes = LeadInOutCore.HIDE_MODES

    def updateLeadVisibility(
        self, style, angleWidget, invertWidget, angleLabel, radiusLabel=None
    ):
        # Dynamic label for Radius/Length
        arc_styles = ("Arc", "Arc3d", "ArcZ", "ArcZFollow", "Helix")
        if radiusLabel and hasattr(self.form, radiusLabel):
            if style in arc_styles:
                getattr(self.form, radiusLabel).setText("Radius")
                # Will do translation later
                # getattr(self.form, radiusLabel).setText(translate("CAM_DressupLeadInOut", "Radius"))
            else:
                getattr(self.form, radiusLabel).setText("Length")
                # Will do translation later
                # getattr(self.form, radiusLabel).setText(translate("CAM_DressupLeadInOut", "Length"))

        # Angle
        if style in self.hideModes["Angle"]:
            angleWidget.hide()
            if hasattr(self.form, angleLabel):
                getattr(self.form, angleLabel).hide()
        else:
            angleWidget.show()
            if hasattr(self.form, angleLabel):
                getattr(self.form, angleLabel).show()
        # Invert Direction
        if style in self.hideModes["Invert"]:
            invertWidget.hide()
        else:
            invertWidget.show()

    def updateLeadInVisibility(self):
        style = self.form.cboStyleIn.currentText()
        self.updateLeadVisibility(
            style,
            self.form.dspAngleIn,
            self.form.chkInvertDirectionIn,
            "label_1",
            "label_5",
        )

    def updateLeadOutVisibility(self):
        style = self.form.cboStyleOut.currentText()
        self.updateLeadVisibility(
            style,
            self.form.dspAngleOut,
            self.form.chkInvertDirectionOut,
            "label_11",
            "label_15",
        )


class ViewProviderDressup:
    def __init__(self, vobj):
        self.obj = vobj.Object
        self.panel = None
        self._taskTransaction = None
        vobj.Proxy = self

    def attach(self, vobj):
        self.obj = vobj.Object
        self.panel = None
        self._taskTransaction = None

        if self.obj and self.obj.Base:
            for i in self.obj.Base.InList:
                if hasattr(i, "Group") and self.obj.Base.Name in [
                    o.Name for o in i.Group
                ]:
                    i.Group = [o for o in i.Group if o.Name != self.obj.Base.Name]

    def claimChildren(self):
        return [self.obj.Base]

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, vobj=None):
        return open_timeline_mode_zero_editor(self.obj)

    def setEdit(self, vobj, mode=0):
        if mode == 1:
            FreeCADGui.runCommand("Std_TransformManip")
        elif mode == 0:
            transaction = self._taskTransaction
            self._taskTransaction = None
            if transaction is None:
                transaction = TaskDocumentTransaction(
                    vobj.Object,
                    "Edit LeadInOut Dress-up",
                )
            try:
                panel = TaskDressupLeadInOut(
                    vobj.Object,
                    self,
                    transaction=transaction,
                )
                self.panel = panel
                transaction.close_dialog()
                transaction.show_dialog(panel)
            except Exception:
                self.panel = None
                transaction.close_dialog()
                if transaction.owns_transaction():
                    transaction.abort()
                raise
        return True

    def unsetEdit(self, vobj, mode=0):
        if mode == 0 and self.panel:
            self.panel.abort()

    def onDelete(self, arg1=None, arg2=None):
        """this makes sure that the base operation is added back to the project and visible"""
        Path.Log.debug("Deleting Dressup")
        if arg1.Object and arg1.Object.Base:
            gui_document = FreeCADGui.getDocument(arg1.Object.Document.Name)
            if shouldRestoreTimelineReplacedInput(
                arg1.Object,
                arg1.Object.Base,
            ):
                gui_document.getObject(arg1.Object.Base.Name).Visibility = True
            job = PathUtils.findParentJob(self.obj)
            if job:
                job.Proxy.addOperation(arg1.Object.Base, arg1.Object)
            arg1.Object.Base = None
        return True

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def clearTaskPanel(self):
        self.panel = None

    def getIcon(self):
        if activeForOp(self.obj):
            return ":/icons/CAM_Dressup.svg"
        else:
            return ":/icons/CAM_OpActive.svg"


def _validate_result(
    document,
    result,
    result_name,
    result_id,
    base,
    base_name,
    base_id,
    job,
    job_name,
    job_id,
    base_was_visible,
):
    if (
        not is_document_object(result, document)
        or result.TypeId != "Path::FeaturePython"
        or document.getObject(result_name) is not result
        or document.getObject(result_id) is not result
        or str(result.Name) != result_name
        or int(result.ID) != result_id
        or not isinstance(result.Proxy, ObjectDressup)
        or not isinstance(result.ViewObject.Proxy, ViewProviderDressup)
        or result.Base is not base
        or not is_document_object(base, document)
        or document.getObject(base_name) is not base
        or document.getObject(base_id) is not base
        or str(base.Name) != base_name
        or int(base.ID) != base_id
        or not is_document_object(job, document)
        or document.getObject(job_name) is not job
        or document.getObject(job_id) is not job
        or str(job.Name) != job_name
        or int(job.ID) != job_id
        or PathUtils.findParentJob(result) is not job
        or PathUtil.timelineParentJob(result) is not job
        or result not in tuple(job.Operations.Group or ())
        or base in tuple(job.Operations.Group or ())
        or not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(result)
        or tuple(result.VibeCADTimelineReplacedInputs)
        != ((base,) if base_was_visible else ())
        or bool(base.ViewObject.Visibility)
        or not bool(result.ViewObject.Visibility)
    ):
        raise RuntimeError(
            "The Lead In/Out CAM dress-up was not created as one exact replacement operation"
        )


class CommandPathDressup:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Dressup",
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupLeadInOut", "Lead In/Out"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_DressupLeadInOut",
                "Creates entry and exit motions for a selected path",
            ),
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        document = App.ActiveDocument
        return _validated_base(PathDressup.selection(), document) is not None

    def Activated(self):
        if not self.IsActive():
            return

        # check that the selection contains exactly what we want
        op = PathDressup.selection(verbose=True)
        if not op:
            return
        document = op.Document
        job = _validated_base(op, document)
        if job is None:
            return

        base_name = str(op.Name)
        base_id = int(op.ID)
        job_name = str(job.Name)
        job_id = int(job.ID)
        base_was_visible = bool(op.ViewObject.Visibility)
        launch = begin_task_launch(
            "Create Lead In/Out Dress-up",
            document,
        )
        try:
            FreeCADGui.addModule("Path.Dressup.Gui.LeadInOut")
            FreeCADGui.doCommand("document = FreeCAD.getDocument(%r)" % document.Name)
            FreeCADGui.doCommand("base = document.getObject(%r)" % base_name)
            result = FreeCADGui.runDocumentObjectCommand(
                document,
                "Path.Dressup.Gui.LeadInOut.CreateInTransaction(base)",
                "Path::FeaturePython",
            )
            result_name = str(result.Name)
            result_id = int(result.ID)
            result_expression = "document.getObject(%r)" % result_name
            FreeCADGui.doCommand(
                f"{result_expression}.ViewObject.Document.setEdit("
                f"{result_expression}.ViewObject, 0)"
            )
            document.recompute()
            _validate_result(
                document,
                result,
                result_name,
                result_id,
                op,
                base_name,
                base_id,
                job,
                job_name,
                job_id,
                base_was_visible,
            )
            launch.require_claimed()
        except Exception:
            launch.abort()
            raise


def _validated_base(base, document):
    if (
        not is_document_object(base, document)
        or not base.isDerivedFrom("Path::Feature")
        or base.isDerivedFrom("Path::FeatureCompoundPython")
        or not PathDressup.isOp(base)
        or not base.isValid()
        or not getattr(base, "Path", None)
        or not tuple(base.Path.Commands or ())
        or not is_timeline_input_usable(base, document)
    ):
        return None
    job = PathUtils.findParentJob(base)
    controller = PathUtil.toolControllerForOp(base)
    if (
        not is_document_object(job, document)
        or getattr(job, "Operations", None) is None
        or base not in tuple(job.Operations.Group or ())
        or not hasattr(getattr(job, "Proxy", None), "addOperation")
        or not is_timeline_input_usable(job, document)
        or not is_document_object(controller, document)
        or not LeadInOutCore.hasUsableMachineParameters(base)
    ):
        return None
    return job


def createDressupFeature(document, base, name="DressupLeadInOut"):
    """Create and initialize one exact Lead In/Out feature."""

    if document is None or getattr(base, "Document", None) is not document:
        raise RuntimeError("A Lead In/Out dress-up requires one live base operation")
    result = document.addObject("Path::FeaturePython", name)
    proxy = ObjectDressup(result, base)
    proxy.setup(result)
    return result


def CreateInTransaction(base, name="DressupLeadInOut", hide_base=True):
    """Create one Lead In/Out replacement inside its caller-owned transaction."""

    document = getattr(base, "Document", None)
    job = _validated_base(base, document)
    if job is None:
        raise RuntimeError(
            "The selected CAM operation cannot receive Lead In/Out motion"
        )
    base_was_visible = bool(base.ViewObject and base.ViewObject.Visibility)
    result = createDressupFeature(document, base, name)
    job.Proxy.addOperation(result, base)
    result.ViewObject.Proxy = ViewProviderDressup(result.ViewObject)
    markTimelineReplacedInputs(
        result,
        [base] if base_was_visible else [],
    )
    if hide_base:
        base.ViewObject.Visibility = False
    return result


def Create(baseObject, name="DressupLeadInOut", mode=0):
    """
    Create(baseObject, name='DressupLeadInOut', mode=0) … create LeadInOut dressup object for the given base path.

    import Path.Dressup.Gui.LeadInOut as lead
    lead.Create(basePath)  # to show Task panel
    lead.Create(basePath, 2)  # to skip Task panel
    """
    if _validated_base(baseObject, getattr(baseObject, "Document", None)) is None:
        Path.Log.error(
            translate(
                "CAM_DressupLeadInOut",
                "Select one valid current CAM operation with a tool controller",
            )
            + "\n"
        )
        return None

    document = baseObject.Document
    previous_transaction = int(document.getBookedTransactionID())
    transaction = TaskDocumentTransaction(
        baseObject,
        "Create a DressupLeadInOut",
        allow_caller_transaction=True,
    )
    owns_transaction = previous_transaction == 0
    try:
        obj = CreateInTransaction(baseObject, name, hide_base=True)
        provider = obj.ViewObject.Proxy

        # Mode 0 transfers the still-open create transaction to the task
        # panel.  Non-panel modes retain the documented direct-Create
        # behavior: commit only a transaction opened here, while preserving a
        # caller-owned transaction for programmatic composition.
        if mode != 0 and owns_transaction:
            transaction.commit((obj,))
        elif mode == 0:
            provider._taskTransaction = transaction

        if not obj.ViewObject.Document.setEdit(obj.ViewObject, mode):
            raise RuntimeError("The Lead In/Out editor could not be opened")
        return obj
    except Exception:
        if transaction.owns_transaction() and (owns_transaction or mode == 0):
            transaction.abort()
        raise


if App.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_DressupLeadInOut", CommandPathDressup())

Path.Log.notice("Loading CAM_DressupLeadInOut… done\n")
