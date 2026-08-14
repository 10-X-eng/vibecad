# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2017 sliptonic <shopinthewoods@gmail.com>
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

from PySide import QtCore, QtGui
from PySide.QtCore import QT_TRANSLATE_NOOP
from pivy import coin
import FreeCAD
import FreeCADGui
import Path
import Path.Base.Util as PathUtil
import Path.Base.Gui.GetPoint as PathGetPoint
import Path.Dressup.Tags as PathDressupTag
import Path.Main.Job as PathJob
import PathScripts.PathUtils as PathUtils
import Path.Dressup.Utils as PathDressup
from Path.CommandBoundary import (
    can_start_document_command,
    ensure_task_transaction,
    open_timeline_mode_zero_editor,
)

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())

translate = FreeCAD.Qt.translate


def _request_exact_transaction_close(document, transaction_id, *, abort):
    transaction_id = int(transaction_id)
    if transaction_id == 0:
        raise RuntimeError("The holding-tag task has no owned transaction")
    try:
        document_is_open = FreeCAD.getDocument(document.Name) is document
    except (NameError, RuntimeError):
        document_is_open = False
    if not document_is_open:
        raise RuntimeError("The holding-tag document is no longer open")
    if int(document.getBookedTransactionID()) != transaction_id:
        raise RuntimeError(
            "The holding-tag task no longer owns its document transaction"
        )

    FreeCAD.closeActiveTransaction(abort, transaction_id)
    return int(document.getBookedTransactionID()) != transaction_id


def _close_exact_transaction(document, transaction_id, *, abort):
    if not _request_exact_transaction_close(
        document,
        transaction_id,
        abort=abort,
    ):
        action = "abort" if abort else "commit"
        raise RuntimeError(
            f"Could not {action} the holding-tag transaction "
            f"{transaction_id}"
        )


def _validated_base(base, document):
    if (
        base is None
        or getattr(base, "Document", None) is not document
        or not base.isDerivedFrom("Path::Feature")
        or base.isDerivedFrom("Path::FeatureCompoundPython")
        or not PathDressup.isOp(base)
        or not base.isValid()
        or not getattr(base, "Path", None)
        or not base.Path.Commands
    ):
        return None

    job = PathUtils.findParentJob(base)
    if (
        job is None
        or job.Document is not document
        or not isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob)
        or getattr(job, "Operations", None) is None
    ):
        return None
    return job


def _validate_result(document, result, base, job):
    has_active_source_path = (
        PathUtil.activeForOp(base)
        and getattr(base, "Path", None)
        and bool(base.Path.Commands)
    )
    if (
        result is None
        or result.Document is not document
        or not result.isDerivedFrom("Path::Feature")
        or not isinstance(
            getattr(result, "Proxy", None),
            PathDressupTag.ObjectTagDressup,
        )
        or result.Base is not base
        or result.Proxy.pathData is None
        or not result.isValid()
        or PathUtils.findParentJob(result) is not job
        or result not in job.Operations.Group
        or base in job.Operations.Group
        or (
            has_active_source_path
            and (
                not getattr(result, "Path", None)
                or not result.Path.Commands
            )
        )
    ):
        raise RuntimeError("The holding-tag CAM toolpath is invalid")


def addDebugDisplay():
    return Path.Log.getLevel(Path.Log.thisModule()) == Path.Log.Level.DEBUG


class PathDressupTagTaskPanel:
    DataX = QtCore.Qt.ItemDataRole.UserRole
    DataY = QtCore.Qt.ItemDataRole.UserRole + 1
    DataZ = QtCore.Qt.ItemDataRole.UserRole + 2
    DataID = QtCore.Qt.ItemDataRole.UserRole + 3

    def __init__(self, obj, viewProvider, jvoVisibility=None):
        self.document = obj.Document
        job = _validated_base(obj.Base, self.document)
        if job is None:
            raise RuntimeError("The holding-tag task has an invalid base operation")
        self.transactionId = ensure_task_transaction(
            "Edit HoldingTags Dress-up",
            self.document,
        )
        self.obj = obj
        self.obj.Proxy.obj = obj
        self.viewProvider = viewProvider
        self.form = FreeCADGui.PySideUic.loadUi(":/panels/HoldingTagsEdit.ui")
        self.getPoint = PathGetPoint.TaskPanel(self.form.cbTagGeneration, True)
        self.jvo = job.ViewObject
        if jvoVisibility is None:
            self.jvoVisible = self.jvo.isVisible()
            if self.jvoVisible:
                self.jvo.hide()
                self.obj.ViewObject.show()
        else:
            self.jvoVisible = jvoVisibility
        self.pt = FreeCAD.Vector(0, 0, 0)

        self.isDirty = True
        self.buttonBox = None
        self.tags = None
        self.Positions = None
        self.Disabled = None
        self.editItem = None

    def getStandardButtons(self):
        return (
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Apply | QtGui.QDialogButtonBox.Cancel
        )

    def clicked(self, button):
        if button == QtGui.QDialogButtonBox.Apply:
            self.getFields()
            self.obj.Proxy.execute(self.obj)
            self.isDirty = False

    def modifyStandardButtons(self, buttonBox):
        self.buttonBox = buttonBox
        self.getPoint.buttonBox = buttonBox

    def abort(self):
        # Called from ViewProvider.unsetEdit() while Gui::Document is already
        # closing the exact adopted transaction.
        self.cleanup(False)

    def reject(self):
        # TaskView marks this exact edit transaction for rollback before
        # entering the Python reject callback. resetEdit() performs that
        # rollback only after ViewProvider teardown releases the edit lock.
        transaction_id = self.transactionId
        transaction_closed = _request_exact_transaction_close(
            self.document,
            transaction_id,
            abort=True,
        )
        self.cleanup(True)
        if (
            not transaction_closed
            and int(self.document.getBookedTransactionID())
            == transaction_id
        ):
            raise RuntimeError(
                "The holding-tag transaction did not roll back"
            )
        self.transactionId = 0
        return True

    def accept(self):
        try:
            self.getFields()
            self.document.recompute()
            job = _validated_base(self.obj.Base, self.document)
            if job is None:
                raise RuntimeError(
                    "The holding-tag task lost its base operation"
                )
            _validate_result(
                self.document,
                self.obj,
                self.obj.Base,
                job,
            )
        except Exception as error:
            FreeCAD.Console.PrintError(
                translate(
                    "CAM_DressupTag",
                    "Holding tags could not be accepted: %s",
                )
                % error
                + "\n"
            )
            # A rejected Accept attempt remains provisional in its original
            # transaction. The panel stays open for correction; a later
            # Cancel rolls the complete gesture back through TaskView.
            return False

        # Gui::Document owns the transaction while an editor is active.
        # An editor launched directly from Python may not have adopted the
        # transaction, so first request an exact close. If the edit lock
        # defers it, resetEdit() releases the lock and performs the same exact
        # commit.
        transaction_id = self.transactionId
        transaction_closed = _request_exact_transaction_close(
            self.document,
            transaction_id,
            abort=False,
        )
        self.cleanup(True)
        if (
            not transaction_closed
            and int(self.document.getBookedTransactionID())
            == transaction_id
        ):
            raise RuntimeError(
                "The holding-tag edit transaction did not finish"
            )
        self.transactionId = 0
        return True

    def cleanup(self, gui):
        self.viewProvider.clearTaskPanel()
        if gui:
            gui_document = FreeCADGui.getDocument(self.document.Name)
            if gui_document is not None:
                gui_document.resetEdit()
            FreeCADGui.Control.closeDialog()
            self.document.recompute()
            if self.jvoVisible:
                self.jvo.show()

    def getTags(self, includeCurrent):
        tags = []
        index = self.form.lwTags.currentRow()
        for i in range(0, self.form.lwTags.count()):
            item = self.form.lwTags.item(i)
            enabled = item.checkState() == QtCore.Qt.CheckState.Checked
            x = item.data(self.DataX)
            y = item.data(self.DataY)
            # print("(%.2f, %.2f) i=%d/%s" % (x, y, i, index))
            if includeCurrent or i != index:
                tags.append((x, y, enabled))
        return tags

    def getFields(self):
        width = FreeCAD.Units.Quantity(self.form.ifWidth.text()).Value
        height = FreeCAD.Units.Quantity(self.form.ifHeight.text()).Value
        angle = self.form.dsbAngle.value()
        radius = FreeCAD.Units.Quantity(self.form.ifRadius.text()).Value
        minCount = self.form.sbMinCount.value()
        maxCount = self.form.sbMaxCount.value()

        tags = self.getTags(True)
        positions = []
        disabled = []
        for i, (x, y, enabled) in enumerate(tags):
            positions.append(FreeCAD.Vector(x, y, 0))
            if not enabled:
                disabled.append(i)

        if width != self.obj.Width:
            self.obj.Width = width
            self.isDirty = True
        if height != self.obj.Height:
            self.obj.Height = height
            self.isDirty = True
        if angle != self.obj.Angle:
            self.obj.Angle = angle
            self.isDirty = True
        if radius != self.obj.Radius:
            self.obj.Radius = radius
            self.isDirty = True
        if positions != self.obj.Positions:
            self.obj.Positions = positions
            self.isDirty = True
        if disabled != self.obj.Disabled:
            self.obj.Disabled = disabled
            self.isDirty = True
        if minCount != self.obj.Proxy.minCount:
            self.obj.Proxy.minCount = minCount
            self.isDirty = True
        if maxCount != self.obj.Proxy.maxCount:
            self.obj.Proxy.maxCount = maxCount
            self.isDirty = True

    def updateTagsView(self):
        Path.Log.track()
        self.form.lwTags.blockSignals(True)
        self.form.lwTags.clear()
        for i, pos in enumerate(self.Positions):
            lbl = "%d: (%.2f, %.2f)" % (i, pos.x, pos.y)
            item = QtGui.QListWidgetItem(lbl)
            item.setData(self.DataX, pos.x)
            item.setData(self.DataY, pos.y)
            item.setData(self.DataZ, pos.z)
            item.setData(self.DataID, i)
            if i in self.Disabled:
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            else:
                item.setCheckState(QtCore.Qt.CheckState.Checked)
            flags = QtCore.Qt.ItemFlag.ItemIsSelectable
            flags |= QtCore.Qt.ItemFlag.ItemIsEnabled
            flags |= QtCore.Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
            self.form.lwTags.addItem(item)
        self.form.lwTags.blockSignals(False)
        self.whenTagSelectionChanged()
        self.viewProvider.updatePositions(self.Positions, self.Disabled)
        self.updateButtonEnable()

    def updateButtonEnable(self):
        if self.Disabled:
            self.form.pbSwitch.setText("Enable All")
        else:
            self.form.pbSwitch.setText("Disable All")

    def generateNewTags(self):
        self.getFields()
        if not self.obj.Proxy.generateTags(self.obj):
            self.obj.Proxy.execute(self.obj)
        self.Positions = self.obj.Positions
        self.Disabled = self.obj.Disabled
        self.updateTagsView()

    def copyNewTags(self):
        objs = self.document.Objects
        tags = [o for o in objs if "DressupTag" in o.Name and self.obj.Name != o.Name]
        tagsLabels = [t.Label for t in tags]
        form = FreeCADGui.PySideUic.loadUi(":/panels/DlgTCChooser.ui")
        form.setWindowTitle("Dressup Tag")
        form.label.setText("Copy tags from")
        form.uiToolController.addItems(tagsLabels)
        r = form.exec()
        if r:
            index = form.uiToolController.currentIndex()
            if not self.obj.Proxy.copyTags(self.obj, tags[index]):
                self.obj.Proxy.execute(self.obj)
            self.Positions = self.obj.Positions
            self.Disabled = self.obj.Disabled
            self.updateTagsView()

    def updateModel(self):
        self.getFields()
        self.updateTagsView()
        self.isDirty = True

    def selectTagWithId(self, index):
        Path.Log.track(index)
        self.form.lwTags.setCurrentRow(index)

    def whenTagSelectionChanged(self):
        index = self.form.lwTags.currentRow()
        self.form.pbRemove.setEnabled(index != -1)
        self.form.pbEdit.setEnabled(index != -1)
        self.viewProvider.selectTag(index)

    def whenTagsViewChanged(self):
        self.updateTagsViewWith(self.getTags(True))

    def updateTagsViewWith(self, tags):
        self.tags = tags
        self.Positions = [FreeCAD.Vector(t[0], t[1], 0) for t in tags]
        self.Disabled = [i for i, t in enumerate(tags) if not t[2]]
        self.updateTagsView()

    def removeSelectedTag(self):
        self.updateTagsViewWith(self.getTags(False))

    def clearTagsList(self):
        self.updateTagsViewWith([])  # remove all tags

    def switchTags(self):
        if self.Disabled:  # enable all tags
            self.Disabled = []
        else:  # disable all tags
            tags = self.getTags(True)
            self.Disabled = list(range(len(tags)))
        self.updateTagsView()

    def addNewTagAt(self, point, obj):
        if point and obj and self.obj.Proxy.pointIsOnPath(self.obj, point):
            Path.Log.info("addNewTagAt(%.2f, %.2f)" % (point.x, point.y))
            self.Positions.append(FreeCAD.Vector(point.x, point.y, 0))
            self.updateTagsView()
        else:
            Path.Log.notice("ignore new tag at %s (obj=%s, on-path=%d" % (point, obj, 0))

    def addNewTag(self):
        self.tags = self.getTags(True)
        self.getPoint.getPoint(self.addNewTagAt)

    def editTagAt(self, point, obj):
        Path.Log.track(point, obj)
        if point and self.obj.Proxy.pointIsOnPath(self.obj, point):
            tags = []
            for i, (x, y, enabled) in enumerate(self.tags):
                if i == self.editItem:
                    tags.append((point.x, point.y, enabled))
                else:
                    tags.append((x, y, enabled))
            self.updateTagsViewWith(tags)

    def editTag(self, item):
        if item:
            self.tags = self.getTags(True)
            self.editItem = item.data(self.DataID)
            x = item.data(self.DataX)
            y = item.data(self.DataY)
            z = item.data(self.DataZ)
            self.getPoint.getPoint(self.editTagAt, FreeCAD.Vector(x, y, z))

    def editSelectedTag(self):
        self.editTag(self.form.lwTags.currentItem())

    def setFields(self):
        self.updateTagsView()
        self.form.ifHeight.setText(
            FreeCAD.Units.Quantity(self.obj.Height, FreeCAD.Units.Length).UserString
        )
        self.form.ifWidth.setText(
            FreeCAD.Units.Quantity(self.obj.Width, FreeCAD.Units.Length).UserString
        )
        self.form.dsbAngle.setValue(self.obj.Angle)
        self.form.ifRadius.setText(
            FreeCAD.Units.Quantity(self.obj.Radius, FreeCAD.Units.Length).UserString
        )
        self.form.sbMinCount.setValue(self.obj.Proxy.minCount)
        self.form.sbMaxCount.setValue(self.obj.Proxy.maxCount)

    def setupUi(self):
        self.Positions = self.obj.Positions
        self.Disabled = self.obj.Disabled

        self.setFields()
        self.updateButtonEnable()

        if self.obj.Proxy.supportsTagGeneration(self.obj):
            self.form.pbGenerate.clicked.connect(self.generateNewTags)

        objs = self.document.Objects
        if any(o for o in objs if "DressupTag" in o.Name and self.obj.Name != o.Name):
            self.form.pbCopy.clicked.connect(self.copyNewTags)
        else:
            self.form.pbCopy.hide()

        self.form.lwTags.itemChanged.connect(self.whenTagsViewChanged)
        self.form.lwTags.itemSelectionChanged.connect(self.whenTagSelectionChanged)
        self.form.lwTags.itemActivated.connect(self.editTag)

        self.form.pbClear.clicked.connect(self.clearTagsList)
        self.form.pbRemove.clicked.connect(self.removeSelectedTag)
        self.form.pbEdit.clicked.connect(self.editSelectedTag)
        self.form.pbAdd.clicked.connect(self.addNewTag)
        self.form.pbSwitch.clicked.connect(self.switchTags)

        self.viewProvider.turnMarkerDisplayOn(True)


class HoldingTagMarker:
    def __init__(self, point, colors):
        self.point = point
        self.color = colors
        self.sep = coin.SoSeparator()
        self.pos = coin.SoTranslation()
        self.pos.translation = (point.x, point.y, point.z)
        self.sphere = coin.SoSphere()
        self.scale = coin.SoType.fromName("SoShapeScale").createInstance()
        self.scale.setPart("shape", self.sphere)
        self.scale.scaleFactor.setValue(14)
        self.material = coin.SoMaterial()
        self.sep.addChild(self.pos)
        self.sep.addChild(self.material)
        self.sep.addChild(self.scale)
        self.enabled = True
        self.selected = False

    def setSelected(self, select):
        self.selected = select
        self.sphere.radius = 1.5 if select else 1.0
        self.setEnabled(self.enabled)

    def setEnabled(self, enabled):
        self.enabled = enabled
        if enabled:
            self.material.diffuseColor = self.color[0] if not self.selected else self.color[2]
            self.material.transparency = 0.0
        else:
            self.material.diffuseColor = self.color[1] if not self.selected else self.color[2]
            self.material.transparency = 0.6


class PathDressupTagViewProvider:
    def __init__(self, vobj):
        Path.Log.track()
        self.vobj = vobj
        self.panel = None

        self.debugDisplay()

        # initialized later
        self.obj = None
        self.tags = None
        self.switch = None
        self.colors = None

    def debugDisplay(self):
        # if False and addDebugDisplay():
        #    if not hasattr(self.vobj, 'Debug'):
        #        self.vobj.addProperty('App::PropertyLink', 'Debug', 'Debug', QT_TRANSLATE_NOOP('CAM_DressupTag', 'Some elements for debugging'))
        #        dbg = self.vobj.Object.Document.addObject('App::DocumentObjectGroup', 'TagDebug')
        #        self.vobj.Debug = dbg
        #    return True
        return False

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def setupColors(self):
        def colorForColorValue(val):
            v = [((val >> n) & 0xFF) / 255.0 for n in [24, 16, 8, 0]]
            return coin.SbColor(v[0], v[1], v[2])

        pref = Path.Preferences.preferences()
        #                                                      R         G          B          A
        npc = pref.GetUnsigned("DefaultPathMarkerColor", ((85 * 256 + 255) * 256 + 0) * 256 + 255)
        hpc = pref.GetUnsigned(
            "DefaultHighlightPathColor", ((255 * 256 + 125) * 256 + 0) * 256 + 255
        )
        dpc = pref.GetUnsigned(
            "DefaultDisabledPathColor", ((205 * 256 + 205) * 256 + 205) * 256 + 154
        )
        self.colors = [
            colorForColorValue(npc),
            colorForColorValue(dpc),
            colorForColorValue(hpc),
        ]

    def attach(self, vobj):
        Path.Log.track()
        self.setupColors()
        self.vobj = vobj
        self.obj = vobj.Object
        self.tags = []
        self.switch = coin.SoSwitch()
        vobj.RootNode.addChild(self.switch)
        self.turnMarkerDisplayOn(False)

    def turnMarkerDisplayOn(self, display):
        sw = coin.SO_SWITCH_ALL if display else coin.SO_SWITCH_NONE
        self.switch.whichChild = sw

    def claimChildren(self):
        Path.Log.track()
        # if self.debugDisplay():
        #    return [self.obj.Base, self.vobj.Debug]
        return [self.obj.Base]

    def onDelete(self, arg1=None, arg2=None):
        """this makes sure that the base operation is added back to the job and visible"""
        Path.Log.track()
        if (
            self.obj.Base
            and self.obj.Base.ViewObject
            and PathUtil.shouldRestoreTimelineReplacedInput(
                self.obj,
                self.obj.Base,
            )
        ):
            self.obj.Base.ViewObject.Visibility = True
        job = PathUtils.findParentJob(self.obj)
        if arg1.Object and arg1.Object.Base and job:
            job.Proxy.addOperation(arg1.Object.Base, arg1.Object)
            arg1.Object.Base = None
        # if self.debugDisplay():
        #    self.vobj.Debug.removeObjectsFromDocument()
        #    self.vobj.Debug.Document.removeObject(self.vobj.Debug.Name)
        #    self.vobj.Debug = None
        return True

    def updatePositions(self, positions, disabled):
        for tag in self.tags:
            self.switch.removeChild(tag.sep)
        tags = []
        for i, p in enumerate(positions):
            tag = HoldingTagMarker(self.obj.Proxy.pointAtBottom(self.obj, p), self.colors)
            tag.setEnabled(i not in disabled)
            tags.append(tag)
            self.switch.addChild(tag.sep)
        self.tags = tags

    def updateData(self, obj, propName):
        Path.Log.track(propName)
        if "Disabled" == propName:
            self.updatePositions(obj.Positions, obj.Disabled)

    def onModelChanged(self):
        Path.Log.track()
        # if self.debugDisplay():
        #    self.vobj.Debug.removeObjectsFromDocument()
        #    for solid in self.obj.Proxy.solids:
        #        tag = self.obj.Document.addObject('Part::Feature', 'tag')
        #        tag.Shape = solid
        #        if tag.ViewObject and self.vobj.Debug.ViewObject:
        #            tag.ViewObject.Visibility = self.vobj.Debug.ViewObject.Visibility
        #            tag.ViewObject.Transparency = 80
        #        self.vobj.Debug.addObject(tag)
        #    tag.purgeTouched()

    def supportsDocumentTimelineEdit(self):
        return True

    def doubleClicked(self, vobj=None):
        return open_timeline_mode_zero_editor(self.obj)

    def setEdit(self, vobj, mode=0):
        panel = PathDressupTagTaskPanel(vobj.Object, self)
        self.setupTaskPanel(panel)
        return True

    def unsetEdit(self, vobj, mode):
        if hasattr(self, "panel") and self.panel:
            self.panel.abort()

    def setupTaskPanel(self, panel):
        self.panel = panel
        FreeCADGui.Control.closeDialog()
        FreeCADGui.Control.showDialog(panel)
        panel.setupUi()
        FreeCADGui.Selection.addSelectionGate(self)
        FreeCADGui.Selection.addObserver(self)

    def clearTaskPanel(self):
        self.panel = None
        FreeCADGui.Selection.removeSelectionGate()
        FreeCADGui.Selection.removeObserver(self)
        self.turnMarkerDisplayOn(False)

    # SelectionObserver interface

    def selectTag(self, index):
        Path.Log.track(index)
        for i, tag in enumerate(self.tags):
            tag.setSelected(i == index)

    def tagAtPoint(self, point, matchZ):
        x = point[0]
        y = point[1]
        z = point[2]
        if self.tags and not matchZ:
            z = self.tags[0].point.z
        p = FreeCAD.Vector(x, y, z)
        for i, tag in enumerate(self.tags):
            if Path.Geom.pointsCoincide(p, tag.point, tag.sphere.radius.getValue() * 1.3):
                return i
        return -1

    def allow(self, doc, obj, sub):
        if obj == self.obj:
            return True
        return False

    def addSelection(self, doc, obj, sub, point):
        Path.Log.track(doc, obj, sub, point)
        if hasattr(self, "panel") and self.panel:
            i = self.tagAtPoint(point, sub is None)
            self.panel.selectTagWithId(i)
        FreeCADGui.updateGui()

    def getIcon(self):
        if PathUtil.activeForOp(self.obj):
            return ":/icons/CAM_Dressup.svg"
        else:
            return ":/icons/CAM_OpActive.svg"


def CreateInTransaction(baseObject, name="DressupTag", hide_base=True):
    """Create one holding-tag replacement inside its caller-owned transaction."""

    document = getattr(baseObject, "Document", None)
    job = _validated_base(baseObject, document)
    if job is None:
        raise RuntimeError("The selected CAM operation cannot receive holding tags")
    base_was_visible = bool(
        baseObject.ViewObject and baseObject.ViewObject.Visibility
    )
    obj = PathDressupTag.Create(baseObject, name, generate=False)
    if obj is None:
        raise RuntimeError("Could not create the holding-tag dress-up")
    view_provider = PathDressupTagViewProvider(obj.ViewObject)
    obj.ViewObject.Proxy = view_provider
    PathUtil.markTimelineReplacedInputs(
        obj,
        [baseObject] if base_was_visible else [],
    )
    if hide_base:
        baseObject.ViewObject.Visibility = False
    return obj


def Create(baseObject, name="DressupTag"):
    """
    Create(basePath, name = 'DressupTag') ... create tag dressup object for the given base path.
    Use this command only iff the UI is up - for batch processing see PathDressupTag.Create
    """
    document = FreeCAD.ActiveDocument
    if document is None or not can_start_document_command(document):
        raise RuntimeError("Holding tags require an idle active document")

    job = _validated_base(baseObject, document)
    if job is None:
        raise RuntimeError(
            "Holding tags require one valid profile toolpath"
        )

    transaction_id = ensure_task_transaction(
        "Create a Tag dressup",
        document,
    )
    view_provider = None
    try:
        base_was_visible = bool(
            baseObject.ViewObject
            and baseObject.ViewObject.Visibility
        )
        obj = PathDressupTag.Create(baseObject, name)
        if obj is None:
            raise RuntimeError("Could not create the holding-tag dress-up")
        PathUtil.markTimelineReplacedInputs(
            obj,
            [baseObject] if base_was_visible else [],
        )
        if baseObject.ViewObject:
            baseObject.ViewObject.Visibility = False
        view_provider = PathDressupTagViewProvider(obj.ViewObject)
        obj.ViewObject.Proxy = view_provider
        document.recompute()
        _validate_result(document, obj, baseObject, job)

        if not obj.ViewObject.Document.setEdit(obj.ViewObject, 0):
            raise RuntimeError("Could not open the holding-tag editor")
        if (
            not FreeCADGui.Control.activeDialog()
            or getattr(view_provider, "panel", None) is None
            or view_provider.panel.transactionId != transaction_id
        ):
            raise RuntimeError("The holding-tag editor did not start correctly")
        return obj
    except Exception:
        if view_provider is not None:
            view_provider.clearTaskPanel()
        if FreeCADGui.Control.activeDialog():
            FreeCADGui.Control.closeDialog()
        if int(document.getBookedTransactionID()) == transaction_id:
            _close_exact_transaction(
                document,
                transaction_id,
                abort=True,
            )
        raise


class CommandPathDressupTag:
    def GetResources(self):
        return {
            "Pixmap": "CAM_Dressup",
            "MenuText": QT_TRANSLATE_NOOP("CAM_DressupTag", "Tag"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_DressupTag", "Creates a tag dress-up object from a selected toolpath"
            ),
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        return (
            _validated_base(
                PathDressup.selection(),
                FreeCAD.ActiveDocument,
            )
            is not None
        )

    def Activated(self):
        if not self.IsActive():
            return

        # check that the selection contains exactly what we want
        op = PathDressup.selection(verbose=True)
        if not op:
            return

        FreeCADGui.addModule("Path.Dressup.Gui.Tags")
        FreeCADGui.doCommand(
            "Path.Dressup.Gui.Tags.Create("
            "App.getDocument(%r).getObject(%r))"
            % (op.Document.Name, op.Name)
        )


if FreeCAD.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_DressupTag", CommandPathDressupTag())

Path.Log.notice("Loading PathDressupTagGui... done\n")
