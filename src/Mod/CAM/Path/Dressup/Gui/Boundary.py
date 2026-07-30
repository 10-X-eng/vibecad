# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2019 sliptonic <shopinthewoods@gmail.com>               *
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

from PySide import QtGui
from PySide.QtCore import QT_TRANSLATE_NOOP
import FreeCAD
import FreeCADGui
import Path
import Path.Base.Util as PathUtil
import Path.Dressup.Boundary as PathDressupPathBoundary
import Path.Dressup.Utils as PathDressup
from Path.CommandBoundary import (
    TaskDocumentTransaction,
    begin_task_launch,
    can_start_document_command,
    is_document_object,
    open_timeline_mode_zero_editor,
)
import PathGui

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


translate = FreeCAD.Qt.translate


class TaskPanel(object):
    def __init__(self, obj, viewProvider, transaction=None):
        self.obj = obj
        self.viewProvider = viewProvider
        if transaction is None:
            transaction = TaskDocumentTransaction(
                obj,
                "Edit Boundary Dress-up",
            )
        elif transaction.document is not obj.Document:
            raise RuntimeError("The Boundary task transaction belongs to another document")
        self.transaction = transaction
        self.document = self.transaction.document
        self.form = FreeCADGui.PySideUic.loadUi(":/panels/DressupPathBoundary.ui")
        self.base = obj.Base
        self.baseName = str(self.base.Name) if self.base is not None else ""
        self.baseId = int(self.base.ID) if self.base is not None else 0
        self.visibilityBase = bool(
            self.base is not None and self.base.ViewObject and self.base.ViewObject.Visibility
        )
        if obj.Stock:
            self.stock = obj.Stock
            self.stockName = str(self.stock.Name)
            self.stockId = int(self.stock.ID)
            self.visibilityBoundary = obj.Stock.ViewObject.Visibility
            obj.Stock.ViewObject.setTemporaryVisibility(True)
        else:
            self.stock = None
            self.stockName = ""
            self.stockId = 0
            self.visibilityBoundary = False

        self.buttonBox = None
        self.isDirty = False

        self.stockFromBase = None
        self.stockFromExisting = None
        self.stockCreateBox = None
        self.stockCreateCylinder = None
        self.stockEdit = None

    def getStandardButtons(self):
        return (
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Apply | QtGui.QDialogButtonBox.Cancel
        )

    def modifyStandardButtons(self, buttonBox):
        self.buttonBox = buttonBox

    def setDirty(self):
        self.isDirty = True
        self.buttonBox.button(QtGui.QDialogButtonBox.Apply).setEnabled(True)

    def setClean(self):
        self.isDirty = False
        self.buttonBox.button(QtGui.QDialogButtonBox.Apply).setEnabled(False)

    def clicked(self, button):
        # callback for standard buttons
        if button == QtGui.QDialogButtonBox.Apply:
            self.updateDressup()
            self.transaction.recompute((self.obj,))

    def abort(self):
        if not self.transaction.is_open():
            self.closeDeletedDocumentTask()
            return True
        self.transaction.abort()
        self.restorePreviewVisibility()
        self.cleanup(False)
        return True

    def reject(self):
        if not self.transaction.is_open():
            self.closeDeletedDocumentTask()
            return True
        self.transaction.abort()
        self.restorePreviewVisibility()
        self.cleanup(True)
        return True

    def accept(self):
        if not self.transaction.is_open():
            self.closeDeletedDocumentTask()
            return True
        if self.isDirty:
            self.updateDressup()
        self.transaction.recompute((self.obj,))
        self.restorePreviewVisibility()
        if self.document.isProvisionallyEnrolledInTimelineByCurrentTransaction(self.obj):
            resources = []
            if (
                self.obj.Stock is not None
                and self.obj.Stock.Document is self.document
                and self.document.getObject(self.obj.Stock.Name) is self.obj.Stock
            ):
                resources.append(self.obj.Stock)
            self.document.publishProvisionalTimelineOperationBlock(
                self.obj,
                resources,
            )
        if self.base is not None and self.base in self.obj.VibeCADTimelineReplacedInputs:
            self.base.ViewObject.Visibility = False
        self.transaction.commit((self.obj,), recompute=False)
        self.cleanup(True)
        return True

    def resolveExactObject(self, obj, name, object_id):
        if obj is None or not name or object_id <= 0:
            return None
        try:
            return (
                obj
                if (
                    obj.Document is self.document
                    and self.document.getObject(name) is obj
                    and self.document.getObject(object_id) is obj
                    and int(obj.ID) == object_id
                )
                else None
            )
        except (AttributeError, NameError, ReferenceError, RuntimeError):
            return None

    def restorePreviewVisibility(self):
        base = self.resolveExactObject(
            self.base,
            self.baseName,
            self.baseId,
        )
        if base is not None and base.ViewObject:
            base.ViewObject.setTemporaryVisibility(self.visibilityBase)

        stock = self.resolveExactObject(
            self.stock,
            self.stockName,
            self.stockId,
        )
        if stock is not None and stock.ViewObject:
            stock.ViewObject.setTemporaryVisibility(self.visibilityBoundary)

    def closeDeletedDocumentTask(self):
        self.viewProvider.clearTaskPanel()
        self.transaction.close_dialog()

    def cleanup(self, gui):
        if self.transaction.is_open():
            self.viewProvider.clearTaskPanel()
        if gui:
            self.transaction.reset_edit()
            self.transaction.close_dialog()
            self.transaction.recompute_after_close()

    def updateDressup(self):
        if self.obj.Inside != self.form.stockInside.isChecked():
            self.obj.Inside = self.form.stockInside.isChecked()
        self.stockEdit.getFields(self.obj)
        self.setClean()

    def updateStockEditor(self, index, force=False):
        import Path.Main.Gui.Job as PathJobGui
        import Path.Main.Stock as PathStock

        def setupFromBaseEdit():
            Path.Log.track(index, force)
            if force or not self.stockFromBase:
                self.stockFromBase = PathJobGui.StockFromBaseBoundBoxEdit(
                    self.obj, self.form, force
                )
            self.stockEdit = self.stockFromBase

        def setupCreateBoxEdit():
            Path.Log.track(index, force)
            if force or not self.stockCreateBox:
                self.stockCreateBox = PathJobGui.StockCreateBoxEdit(self.obj, self.form, force)
            self.stockEdit = self.stockCreateBox

        def setupCreateCylinderEdit():
            Path.Log.track(index, force)
            if force or not self.stockCreateCylinder:
                self.stockCreateCylinder = PathJobGui.StockCreateCylinderEdit(
                    self.obj, self.form, force
                )
            self.stockEdit = self.stockCreateCylinder

        def setupFromExisting():
            Path.Log.track(index, force)
            if force or not self.stockFromExisting:
                self.stockFromExisting = PathJobGui.StockFromExistingEdit(
                    self.obj, self.form, force
                )
            if self.stockFromExisting.candidates(self.obj):
                self.stockEdit = self.stockFromExisting
                return True
            return False

        if index == -1:
            if self.obj.Stock is None or PathJobGui.StockFromBaseBoundBoxEdit.IsStock(self.obj):
                setupFromBaseEdit()
            elif PathJobGui.StockCreateBoxEdit.IsStock(self.obj):
                setupCreateBoxEdit()
            elif PathJobGui.StockCreateCylinderEdit.IsStock(self.obj):
                setupCreateCylinderEdit()
            elif PathJobGui.StockFromExistingEdit.IsStock(self.obj):
                setupFromExisting()
            else:
                Path.Log.error(
                    translate("PathJob", "Unsupported stock object %s") % self.obj.Stock.Label
                )
        else:
            if index == PathJobGui.StockFromBaseBoundBoxEdit.Index:
                setupFromBaseEdit()
            elif index == PathJobGui.StockCreateBoxEdit.Index:
                setupCreateBoxEdit()
            elif index == PathJobGui.StockCreateCylinderEdit.Index:
                setupCreateCylinderEdit()
            elif index == PathJobGui.StockFromExistingEdit.Index:
                if not setupFromExisting():
                    setupFromBaseEdit()
                    index = -1
            else:
                Path.Log.error(
                    translate("PathJob", "Unsupported stock type %s (%d)")
                    % (self.form.stock.currentText(), index)
                )
        self.stockEdit.activate(self.obj, index == -1)

    def setupUi(self):
        self.updateStockEditor(-1, False)
        self.form.stockInside.setChecked(self.obj.Inside)

        self.form.stock.currentIndexChanged.connect(self.updateStockEditor)
        if hasattr(self.form.stockInside, "checkStateChanged"):  # Qt version >= 6.7.0
            self.form.stockInside.checkStateChanged.connect(self.setDirty)
        else:  # Qt version < 6.7.0
            self.form.stockInside.stateChanged.connect(self.setDirty)
        self.form.stockExtXneg.textChanged.connect(self.setDirty)
        self.form.stockExtXpos.textChanged.connect(self.setDirty)
        self.form.stockExtYneg.textChanged.connect(self.setDirty)
        self.form.stockExtYpos.textChanged.connect(self.setDirty)
        self.form.stockExtZneg.textChanged.connect(self.setDirty)
        self.form.stockExtZpos.textChanged.connect(self.setDirty)
        self.form.stockBoxLength.textChanged.connect(self.setDirty)
        self.form.stockBoxWidth.textChanged.connect(self.setDirty)
        self.form.stockBoxHeight.textChanged.connect(self.setDirty)
        self.form.stockCylinderRadius.textChanged.connect(self.setDirty)
        self.form.stockCylinderHeight.textChanged.connect(self.setDirty)


class DressupPathBoundaryViewProvider(object):
    def __init__(self, vobj):
        self.attach(vobj)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def attach(self, vobj):
        self.vobj = vobj
        self.obj = vobj.Object
        self.panel = None
        self._taskTransaction = None
        self._timelineObjectsBeforeTask = None

    def claimChildren(self):
        return [self.obj.Base, self.obj.Stock]

    def onDelete(self, vobj, args=None):
        if vobj.Object and vobj.Object.Proxy:
            vobj.Object.Proxy.onDelete(vobj.Object, args)
        return True

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, vobj=None):
        return open_timeline_mode_zero_editor(self.obj)

    def setEdit(self, vobj, mode=0):
        transaction = self._taskTransaction
        self._taskTransaction = None
        if transaction is None:
            transaction = TaskDocumentTransaction(
                vobj.Object,
                "Edit Boundary Dress-up",
            )
        try:
            panel = TaskPanel(
                vobj.Object,
                self,
                transaction=transaction,
            )
            self.setupTaskPanel(panel)
            return True
        except Exception:
            self.panel = None
            transaction.close_dialog()
            if transaction.owns_transaction():
                transaction.abort()
            raise

    def unsetEdit(self, vobj, mode=0):
        if self.panel:
            self.panel.abort()

    def setupTaskPanel(self, panel):
        self.panel = panel
        panel.transaction.close_dialog()
        panel.transaction.show_dialog(panel)
        panel.setupUi()

    def clearTaskPanel(self):
        self.panel = None

    def getIcon(self):
        if PathUtil.activeForOp(self.obj):
            return ":/icons/CAM_Dressup.svg"
        else:
            return ":/icons/CAM_OpActive.svg"


def Create(base, name="DressupPathBoundary"):
    transaction = TaskDocumentTransaction(
        base,
        "Create a Boundary dressup",
        allow_caller_transaction=True,
    )
    base_was_visible = False
    try:
        base_was_visible = bool(base.ViewObject and base.ViewObject.Visibility)
        obj = PathDressupPathBoundary.Create(base, name)
        provider = DressupPathBoundaryViewProvider(obj.ViewObject)
        obj.ViewObject.Proxy = provider
        provider._taskTransaction = transaction
        PathUtil.markTimelineReplacedInputs(
            obj,
            [base] if base_was_visible else [],
        )
        obj.Base.ViewObject.setTemporaryVisibility(False)
        obj.Stock.ViewObject.Visibility = False
        if not obj.ViewObject.Document.setEdit(obj.ViewObject, 0):
            raise RuntimeError("The Boundary dress-up editor could not be opened")
        return obj
    except Exception:
        if transaction.owns_transaction():
            transaction.abort()
        try:
            if (
                base.Document is transaction.document
                and transaction.document.getObject(base.Name) is base
                and base.ViewObject
            ):
                base.ViewObject.setTemporaryVisibility(base_was_visible)
        except (AttributeError, NameError, ReferenceError, RuntimeError):
            pass
        raise


class CommandPathDressupPathBoundary:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Dressup",
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupPathBoundary", "Boundary"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_DressupPathBoundary",
                "Creates a boundary dress-up from a selected toolpath",
            ),
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        op = PathDressup.selection()
        if not is_document_object(op):
            return False
        baseOp = PathDressup.baseOp(op)
        if not hasattr(baseOp, "ClearanceHeight"):
            return False
        if not hasattr(baseOp, "SafeHeight"):
            return False

        return True

    def Activated(self):
        if not self.IsActive():
            return

        # check that the selection contains exactly what we want
        op = PathDressup.selection(verbose=True)
        if not op:
            return

        # everything ok!
        launch = begin_task_launch(
            "Create Path Boundary Dress-up",
            op.Document,
        )
        FreeCADGui.addModule("Path.Dressup.Gui.Boundary")
        try:
            FreeCADGui.doCommand(
                "Path.Dressup.Gui.Boundary.Create("
                "FreeCAD.getDocument(%r).getObject(%r))" % (op.Document.Name, op.Name)
            )
            launch.require_claimed()
        except Exception:
            launch.abort()
            raise
        # FreeCAD.ActiveDocument.commitTransaction()  # Final `commitTransaction()` called via TaskPanel.accept()
        op.Document.recompute()


if FreeCAD.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_DressupPathBoundary", CommandPathDressupPathBoundary())

Path.Log.notice("Loading PathDressupPathBoundaryGui... done\n")
