# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2022 sliptonic <shopinthewoods@gmail.com>               *
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

from PySide import QtCore
from PySide.QtCore import QT_TRANSLATE_NOOP
import FreeCAD
import FreeCADGui
import Path
import Path.Base.Util as PathUtil
import Path.Dressup.DogboneII as DogboneII
import Path.Dressup.Utils as PathDressup
import PathScripts.PathUtils as PathUtils
from Path.CommandBoundary import (
    TaskDocumentTransaction,
    begin_task_launch,
    can_start_document_command,
    is_document_object,
    open_timeline_mode_zero_editor,
)

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())

translate = FreeCAD.Qt.translate


class Marker(object):
    def __init__(self, pt, r, h, ena):
        if Path.Geom.isRoughly(h, 0):
            h = 0.1
        self.pt = pt
        self.r = r
        self.h = h
        self.ena = ena
        self.sep = coin.SoSeparator()
        self.pos = coin.SoTranslation()
        self.pos.translation = (pt.x, pt.y, pt.z + h / 2)
        self.rot = coin.SoRotationXYZ()
        self.rot.axis = self.rot.X
        self.rot.angle = DogboneII.PI / 2
        self.cyl = coin.SoCylinder()
        self.cyl.radius = r
        self.cyl.height = h
        # self.cyl.removePart(self.cyl.TOP)
        # self.cyl.removePart(self.cyl.BOTTOM)
        self.material = coin.SoMaterial()
        self.sep.addChild(self.pos)
        self.sep.addChild(self.rot)
        self.sep.addChild(self.material)
        self.sep.addChild(self.cyl)
        self.lowlight()

    def setSelected(self, selected):
        if selected:
            self.highlight()
        else:
            self.lowlight()

    def highlight(self):
        self.material.diffuseColor = self.color(1)
        self.material.transparency = 0.75

    def lowlight(self):
        self.material.diffuseColor = self.color(0)
        self.material.transparency = 0.90

    def _colorEnabled(self, id):
        if id == 1:
            return coin.SbColor(0.0, 0.9, 0.0)
        return coin.SbColor(0.0, 0.9, 0.0)

    def _colorDisabled(self, id):
        if id == 1:
            return coin.SbColor(0.9, 0.0, 0.0)
        return coin.SbColor(0.9, 0.0, 0.0)

    def color(self, id):
        if self.ena:
            return self._colorEnabled(id)
        return self._colorDisabled(id)


class TaskPanel(object):
    DataIds = QtCore.Qt.ItemDataRole.UserRole
    DataState = QtCore.Qt.ItemDataRole.UserRole + 1

    def __init__(self, viewProvider, obj, transaction=None):
        self.viewProvider = viewProvider
        self.obj = obj
        if transaction is None:
            transaction = TaskDocumentTransaction(
                obj,
                "Edit Dogbone Dress-up",
            )
        elif transaction.document is not obj.Document:
            raise RuntimeError(
                "The Dogbone task transaction belongs to another document"
            )
        self.transaction = transaction
        self.document = self.transaction.document
        self.form = FreeCADGui.PySideUic.loadUi(":/panels/DogboneEdit.ui")
        self.s = None
        # self.height = 10 ???
        self.markers = []

    def reject(self):
        if not self.transaction.is_open():
            self.closeDeletedDocumentTask()
            return True
        self.transaction.abort()
        self.clearTaskPanel()
        self.transaction.close_dialog()
        self.transaction.recompute_after_close()
        if self.s is not None:
            FreeCADGui.Selection.removeObserver(self.s)
            self.s = None
        self.cleanup()
        return True

    def accept(self):
        if not self.transaction.is_open():
            self.closeDeletedDocumentTask()
            return True
        self.getFields()
        self.transaction.commit((self.obj,))
        self.clearTaskPanel()
        self.transaction.reset_edit()
        self.transaction.close_dialog()
        self.transaction.recompute_after_close()
        if self.s is not None:
            FreeCADGui.Selection.removeObserver(self.s)
            self.s = None
        self.cleanup()
        return True

    def clearTaskPanel(self):
        if self.viewProvider.panel is self:
            self.viewProvider.panel = None

    def closeDeletedDocumentTask(self):
        """Release GUI-only state after the edited document disappeared."""

        self.transaction.close_dialog()
        self.viewProvider.panel = None
        if self.s is not None:
            FreeCADGui.Selection.removeObserver(self.s)
            self.s = None
        # The scene graph belongs to the deleted GUI document.  Do not touch
        # its nodes after deletion; dropping our Python references is enough.
        self.markers = []

    def cleanup(self):
        self.viewProvider.showMarkers(False)
        for m in self.markers:
            self.viewProvider.switch.removeChild(m.sep)
        self.markers = []

    def getFields(self):
        self.obj.Style = str(self.form.styleCombo.currentText())
        self.obj.Side = str(self.form.sideCombo.currentText())
        self.obj.Incision = str(self.form.incisionCombo.currentText())
        self.obj.Custom = self.form.custom.value()
        blacklist = []
        for i in range(0, self.form.bones.count()):
            item = self.form.bones.item(i)
            if item.checkState() == QtCore.Qt.CheckState.Unchecked:
                blacklist.extend(item.data(self.DataIds))
        self.obj.BoneBlacklist = sorted(blacklist)
        self.obj.Proxy.execute(self.obj)

    def updateBoneList(self):
        itemList = []
        for state in self.obj.Proxy.boneStates(self.obj):
            pos = state.position()
            ids = ",".join([str(nr) for nr in state.boneIDs()])
            lbl = f"({pos.x:.2f}, {pos.y:.2f}): {ids}"
            item = QtGui.QListWidgetItem(lbl)
            if state.isEnabled():
                item.setCheckState(QtCore.Qt.CheckState.Checked)
            else:
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            flags = QtCore.Qt.ItemFlag.ItemIsSelectable
            flags |= QtCore.Qt.ItemFlag.ItemIsEnabled
            flags |= QtCore.Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
            item.setData(self.DataIds, state.boneIDs())
            item.setData(self.DataState, state)
            itemList.append(item)

        markers = []
        self.form.bones.clear()
        for item in sorted(itemList, key=lambda item: item.data(self.DataState).boneIDs()[0]):
            self.form.bones.addItem(item)
            state = item.data(self.DataState)
            loc = state.boneTip()
            r = self.obj.Proxy.toolRadius(self.obj)
            zs = state.zLevels()
            markers.append(
                Marker(
                    FreeCAD.Vector(loc.x, loc.y, min(zs)),
                    r,
                    max(1, max(zs) - min(zs)),
                    state.isEnabled(),
                )
            )
        for m in self.markers:
            self.viewProvider.switch.removeChild(m.sep)
        for m in markers:
            self.viewProvider.switch.addChild(m.sep)
        self.markers = markers

    def updateUI(self):
        customSelected = self.obj.Incision == DogboneII.Incision.Custom
        self.form.custom.setEnabled(customSelected)
        self.form.customLabel.setEnabled(customSelected)
        self.updateBoneList()

    def updateModel(self):
        if not self.transaction.is_open():
            return
        self.getFields()
        self.updateUI()
        self.transaction.recompute((self.obj,))

    def setupCombo(self, combo, text, items):
        if items and len(items) > 0:
            for i in range(combo.count(), -1, -1):
                combo.removeItem(i)
            combo.addItems(items)
        index = combo.findText(text, QtCore.Qt.MatchFixedString)
        if index >= 0:
            combo.setCurrentIndex(index)

    def setFields(self):
        self.setupCombo(self.form.styleCombo, self.obj.Style, DogboneII.Style.All)
        self.setupCombo(self.form.sideCombo, self.obj.Side, DogboneII.Side.All)
        self.setupCombo(self.form.incisionCombo, self.obj.Incision, DogboneII.Incision.All)
        self.form.custom.setMinimum(0.0)
        self.form.custom.setDecimals(3)
        self.form.custom.setValue(self.obj.Custom)
        self.updateUI()

    def open(self):
        self.s = SelObserver(self.document)
        # install the function mode resident
        FreeCADGui.Selection.addObserver(self.s)

    def setupUi(self):
        self.setFields()
        # now that the form is filled, setup the signal handlers
        self.form.styleCombo.currentIndexChanged.connect(self.updateModel)
        self.form.sideCombo.currentIndexChanged.connect(self.updateModel)
        self.form.incisionCombo.currentIndexChanged.connect(self.updateModel)
        self.form.custom.valueChanged.connect(self.updateModel)
        self.form.bones.itemChanged.connect(self.updateModel)
        self.form.bones.itemSelectionChanged.connect(self.updateMarkers)

        self.viewProvider.showMarkers(True)

    def updateMarkers(self):
        index = self.form.bones.currentRow()
        for i, m in enumerate(self.markers):
            m.setSelected(i == index)


class SelObserver(object):
    def __init__(self, document):
        self.document = document
        import Path.Op.Gui.Selection as PST

        PST.eselect()

    def __del__(self):
        import Path.Op.Gui.Selection as PST

        PST.clear()

    def addSelection(self, doc, obj, sub, pnt):
        if doc != self.document.Name:
            return
        selected = self.document.getObject(obj)
        if selected is not None:
            FreeCADGui.Selection.addSelection(selected)
        FreeCADGui.updateGui()


class ViewProviderDressup(object):
    def __init__(self, vobj):
        self.vobj = vobj
        self.obj = None
        self.panel = None

    def attach(self, vobj):
        self.obj = vobj.Object
        self.panel = None
        if self.obj and self.obj.Base:
            for i in self.obj.Base.InList:
                if hasattr(i, "Group"):
                    group = i.Group
                    for g in group:
                        if g.Name == self.obj.Base.Name:
                            group.remove(g)
                    i.Group = group
            # FreeCADGui.ActiveDocument.getObject(obj.Base.Name).Visibility = False
        self.switch = coin.SoSwitch()
        vobj.RootNode.addChild(self.switch)

    def showMarkers(self, on):
        sw = coin.SO_SWITCH_ALL if on else coin.SO_SWITCH_NONE
        self.switch.whichChild = sw

    def claimChildren(self):
        return [self.obj.Base]

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, vobj=None):
        return open_timeline_mode_zero_editor(self.obj)

    def setEdit(self, vobj, mode=0):
        transaction = TaskDocumentTransaction(
            vobj.Object,
            "Edit Dogbone Dress-up",
        )
        try:
            panel = TaskPanel(
                self,
                vobj.Object,
                transaction=transaction,
            )
            self.panel = panel
            transaction.close_dialog()
            transaction.show_dialog(panel)
            panel.setupUi()
            return True
        except Exception:
            self.panel = None
            transaction.close_dialog()
            if transaction.owns_transaction():
                transaction.abort()
            raise

    def unsetEdit(self, vobj, mode=0):
        if self.panel is not None:
            self.panel.reject()
        return False

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onDelete(self, arg1=None, arg2=None):
        """this makes sure that the base operation is added back to the project and visible"""
        if arg1.Object and arg1.Object.Base:
            gui_document = FreeCADGui.getDocument(
                arg1.Object.Document.Name
            )
            if PathUtil.shouldRestoreTimelineReplacedInput(
                arg1.Object,
                arg1.Object.Base,
            ):
                gui_document.getObject(
                    arg1.Object.Base.Name
                ).Visibility = True
            job = PathUtils.findParentJob(arg1.Object)
            if job:
                job.Proxy.addOperation(arg1.Object.Base, arg1.Object)
            arg1.Object.Base = None
        return True

    def getIcon(self):
        if PathUtil.activeForOp(self.obj):
            return ":/icons/CAM_Dressup.svg"
        else:
            return ":/icons/CAM_OpActive.svg"


def _validated_base(base, document):
    if (
        not is_document_object(base, document)
        or not base.isDerivedFrom("Path::Feature")
        or not PathDressup.isOp(base)
        or not base.isValid()
        or not getattr(base, "Path", None)
        or not base.Path.Commands
    ):
        return None
    job = PathUtils.findParentJob(base)
    controller = PathUtil.toolControllerForOp(base)
    tool = getattr(controller, "Tool", None)
    diameter = getattr(getattr(tool, "Diameter", None), "Value", 0.0)
    if (
        not is_document_object(job, document)
        or getattr(job, "Operations", None) is None
        or base not in tuple(job.Operations.Group or ())
        or not hasattr(getattr(job, "Proxy", None), "addOperation")
        or controller is None
        or float(diameter) <= 0.0
    ):
        return None
    return job


def CreateInTransaction(base, name="DressupDogbone", hide_base=True):
    """Create one Dogbone dress-up inside its caller-owned transaction."""

    document = getattr(base, "Document", None)
    if document is None:
        raise RuntimeError("A CAM Dogbone dress-up requires one live base operation")
    job = _validated_base(base, document)
    if job is None:
        raise RuntimeError("The selected CAM operation cannot be dogbone-dressed")

    base_was_visible = bool(base.ViewObject and base.ViewObject.Visibility)
    obj = DogboneII.Create(base, name)
    job.Proxy.addOperation(obj, base)
    if FreeCAD.GuiUp:
        obj.ViewObject.Proxy = ViewProviderDressup(obj.ViewObject)
        PathUtil.markTimelineReplacedInputs(
            obj,
            [base] if base_was_visible else [],
        )
        if hide_base:
            obj.Base.ViewObject.Visibility = False
    return obj


def Create(base, name="DressupDogbone"):
    """
    Create(obj, name='DressupDogbone')… dresses the given Path.Op.Profile object with dogbones.
    """
    return CreateInTransaction(base, name)


class CommandDressupDogboneII(object):
    def GetResources(self):
        return {
            "Pixmap": "CAM_Dressup",
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupDogbone", "Dogbone"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_DressupDogbone",
                "Creates a dogbone dress-up object from a selected toolpath",
            ),
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        document = FreeCAD.ActiveDocument
        return _validated_base(PathDressup.selection(), document) is not None

    def Activated(self):
        if not self.IsActive():
            return

        # check that the selection contains exactly what we want
        op = PathDressup.selection(verbose=True)
        if not op:
            return

        # everything ok!
        launch = begin_task_launch(
            "Create Dogbone Dress-up",
            op.Document,
        )
        try:
            FreeCADGui.addModule("Path.Dressup.Gui.DogboneII")
            FreeCADGui.doCommand(
                "obj = Path.Dressup.Gui.DogboneII.Create("
                "FreeCAD.getDocument(%r).getObject(%r))"
                % (op.Document.Name, op.Name)
            )
            FreeCADGui.doCommand(
                "obj.ViewObject.Document.setEdit(obj.ViewObject, 0)"
            )
            launch.require_claimed()
        except Exception:
            launch.abort()
            raise
        # FreeCAD.ActiveDocument.commitTransaction()  # Final `commitTransaction()` called via TaskPanel.accept()
        op.Document.recompute()


if FreeCAD.GuiUp:
    import FreeCADGui
    from PySide import QtGui
    from pivy import coin

    FreeCADGui.addCommand("CAM_DressupDogbone", CommandDressupDogboneII())

FreeCAD.Console.PrintLog("Loading DressupDogboneII… done\n")
