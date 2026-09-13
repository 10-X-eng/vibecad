# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2017 Shai Seger <shaise at gmail>                       *
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
"""
Command and task window handler for the OpenGL based CAM simulator
"""

import os
import uuid
import FreeCAD
import Path.Base.Util as PathUtil
import Path.Dressup.Utils as PathDressup
from Path.Main import SimulatorGLPreparation
from Path.CommandBoundary import active_jobs, can_start_document_command
from PathScripts import PathUtils
import CAMSimulator

from FreeCAD import Vector

# lazily loaded modules
from lazy_loader.lazy_loader import LazyLoader

Mesh = LazyLoader("Mesh", globals(), "Mesh")
Part = LazyLoader("Part", globals(), "Part")

if FreeCAD.GuiUp:
    import FreeCADGui
    from PySide import QtGui, QtCore
    from PySide.QtGui import QDialogButtonBox

_filePath = os.path.dirname(os.path.abspath(__file__))
_active_native_prepared_simulation = None


def IsSame(x, y):
    """Check if two floats are the same within an epsilon"""
    return SimulatorGLPreparation.is_same(x, y)


def RadiusAt(edge, p):
    """Find the tool radius within a point on its circumference"""
    return SimulatorGLPreparation.radius_at(edge, p)


class CAMSimTaskUi:
    """Handles the simulator task panel"""

    def __init__(self, parent):
        # this will create a Qt widget from our ui file
        self.form = FreeCADGui.PySideUic.loadUi(":/panels/TaskCAMSimulator.ui")
        self.parent = parent

    def getStandardButtons(self, *_args):
        """Task panel needs only Close button"""
        return QDialogButtonBox.Close

    def reject(self):
        """User Pressed the Close button"""
        self.parent.cancel()
        FreeCADGui.Control.closeDialog()


def TSError(msg):
    """Display error message"""
    QtGui.QMessageBox.information(None, "Path Simulation", msg)


class CAMSimulation:
    """Handles and prepares CAM jobs for simulation"""

    def __init__(self):
        self.debug = False
        self.stdrot = FreeCAD.Rotation(Vector(0, 0, 1), 0)
        self.iprogress = 0
        self.numCommands = 0
        self.simperiod = 20
        self.quality = 10
        self.resetSimulation = False
        self.jobs = []
        self.initdone = False
        self.taskForm = None
        self.disableAnim = False
        self.firstDrill = True
        self.millSim = None
        self.job = None
        self.activeOps = []
        self.ioperation = 0
        self.stock = None
        self.busy = False
        self.operations = []
        self.baseShape = None
        self.nativePrepared = False
        self.nativeSimulationId = None
        self._preparedStockMesh = None
        self._preparedBaseMesh = None
        self._preparedRuns = ()
        self._preparedView = None

    def Connect(self, but, sig):
        """Connect task panel buttons"""
        QtCore.QObject.connect(but, QtCore.SIGNAL("clicked()"), sig)

    def FindClosestEdge(self, edges, px, pz):
        """Convert tool shape to tool profile needed by GL simulator"""
        return SimulatorGLPreparation.find_closest_edge(edges, px, pz)

    def FindTopMostEdge(self, edges):
        """Examine tool solid edges and find the top most one"""
        return SimulatorGLPreparation.find_topmost_edge(edges)

    def GetToolProfile(self, tool, resolution):
        """Get the edge profile of a tool solid. Basically locating the
        side edge that OCC creates on any revolved object
        """
        return SimulatorGLPreparation.build_tool_profile(tool.Shape, resolution)

    def Activate(self):
        """Invoke the simulator task panel"""
        self.initdone = False
        self.taskForm = CAMSimTaskUi(self)
        form = self.taskForm.form
        self.Connect(form.toolButtonPlay, self.SimPlay)
        form.sliderAccuracy.valueChanged.connect(self.onAccuracyBarChange)
        self.onAccuracyBarChange()

        prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/CAM")
        if prefs.GetBool("SimulatorFollowsVisibility"):
            form.followsVisibility.setCheckState(QtCore.Qt.CheckState.Checked)
        form.followsVisibility.clicked.connect(self.followsVisibilityChange)

        self._populateJobSelection(form)
        form.comboJobs.currentIndexChanged.connect(self.onJobChange)
        self.onJobChange()
        form.listOperations.itemChanged.connect(self.onOperationItemChange)
        FreeCADGui.Control.showDialog(self.taskForm)
        self.disableAnim = False
        self.firstDrill = True
        self.millSim = CAMSimulator.PathSim()
        self.initdone = True
        self.job = self.jobs[self.taskForm.form.comboJobs.currentIndex()]
        # self.SetupSimulation()

    def ActivatePrepared(
        self,
        job,
        operations,
        quality,
        stockShape,
        stockMesh,
        baseShape,
        baseMesh,
        runs,
    ):
        """Present one exact, already prepared simulation on the GUI thread."""

        if not FreeCAD.GuiUp:
            raise RuntimeError("Prepared GL simulation requires the VibeCAD GUI")
        selected = tuple(operations)
        if not selected:
            raise ValueError("Prepared GL simulation requires at least one operation")
        if not isinstance(stockMesh, bytes) or not stockMesh:
            raise ValueError("Prepared GL simulation requires one stock mesh")
        if baseShape is None and baseMesh is not None:
            raise ValueError("A prepared base mesh requires one base shape")
        if baseShape is not None and (not isinstance(baseMesh, bytes) or not baseMesh):
            raise ValueError("A prepared base shape requires one base mesh")

        self.initdone = False
        self.nativePrepared = True
        self.job = job
        self.jobs = [job]
        self.operations = list(selected)
        self.activeOps = list(selected)
        self.quality = int(quality)
        self.stock = stockShape
        self.baseShape = baseShape
        self._preparedStockMesh = stockMesh
        self._preparedBaseMesh = baseMesh
        self._preparedRuns = tuple(runs)

        self.taskForm = CAMSimTaskUi(self)
        form = self.taskForm.form
        self.Connect(form.toolButtonPlay, self.ReplayPrepared)
        form.sliderAccuracy.setValue(self.quality)
        self.onAccuracyBarChange()
        form.sliderAccuracy.setEnabled(False)
        form.followsVisibility.setEnabled(False)
        form.comboJobs.addItem(job.ViewObject.Icon, job.Label)
        form.comboJobs.setEnabled(False)
        for operation in selected:
            item = QtGui.QListWidgetItem(operation.ViewObject.Icon, operation.Label)
            item.setCheckState(QtCore.Qt.CheckState.Checked)
            form.listOperations.addItem(item)
        form.listOperations.setEnabled(False)

        FreeCADGui.Control.showDialog(self.taskForm)
        try:
            self.millSim = CAMSimulator.PathSim()
            self.ReplayPrepared()
        except Exception:
            FreeCADGui.Control.closeDialog()
            raise
        self.disableAnim = False
        self.firstDrill = True
        self.initdone = True

    def ReplayPrepared(self):
        """Reload the immutable prepared program without document work."""

        self.millSim.ResetSimulation(FreeCADGui.getDocument(self.job.Document))
        for run in self._preparedRuns:
            self.millSim.AddTool(
                list(run["tool_profile"]),
                int(run["tool_number"]),
                float(run["diameter_mm"]),
                1.0,
            )
            for command in run["gcode"]:
                self.millSim.AddGCode(command)
        self.millSim.BeginPreparedSimulation(
            self.stock,
            self._preparedStockMesh,
            float(self.quality),
        )
        self._preparedView = FreeCADGui.getMainWindow().getActiveWindow()
        if self.baseShape is not None:
            self.millSim.SetPreparedBaseShape(
                self.baseShape,
                self._preparedBaseMesh,
            )

    def _populateJobSelection(self, form):
        """Make Job selection combobox"""
        # Get list of Job objects in active document
        jobList = active_jobs()

        # Get name of selected Job
        jobName = ""
        selection = FreeCADGui.Selection.getSelection()
        if selection:  #  Identify job selected by user
            job = PathUtils.findParentJob(selection[0])
            if job in jobList:
                jobName = job.Name

        # Prepare combobox
        form.comboJobs.blockSignals(True)
        form.comboJobs.clear()
        form.comboJobs.blockSignals(False)

        # Get index of selected Job
        setJobIdx = 0
        for i, job in enumerate(jobList):
            # Populate the job selection combobox
            form.comboJobs.addItem(job.ViewObject.Icon, job.Label)
            self.jobs.append(job)
            if job.Name == jobName:
                setJobIdx = i

        # Preselect GUI-selected job in the combobox
        form.comboJobs.setCurrentIndex(setJobIdx)

    def SetupSimulation(self):
        """Prepare all selected job operations for simulation"""
        form = self.taskForm.form
        self.activeOps = []
        self.numCommands = 0
        self.ioperation = 0
        for i in range(form.listOperations.count()):
            if form.listOperations.item(i).checkState() == QtCore.Qt.CheckState.Checked:
                self.firstDrill = True
                self.activeOps.append(self.operations[i])
                self.numCommands += len(self.operations[i].Path.Commands)

        self.stock = self.job.Stock.Shape.copy()
        self.busy = False

    def onJobChange(self):
        """When a new job is selected from the drop-down, update job operation list"""
        prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/CAM")
        followsVisibility = prefs.GetBool("SimulatorFollowsVisibility")
        form = self.taskForm.form
        j = self.jobs[form.comboJobs.currentIndex()]
        self.job = j
        form.listOperations.clear()
        self.operations = []
        allhidden = all(
            not op.Visibility for op in j.Operations.OutList if PathUtil.opProperty(op, "Active")
        )
        for op in j.Operations.OutList:
            if PathUtil.opProperty(op, "Active"):
                listItem = QtGui.QListWidgetItem(op.ViewObject.Icon, op.Label)
                listItem.setFlags(listItem.flags() | QtCore.Qt.ItemIsUserCheckable)
                if followsVisibility and not op.Visibility and not allhidden:
                    listItem.setCheckState(QtCore.Qt.CheckState.Unchecked)
                else:
                    listItem.setCheckState(QtCore.Qt.CheckState.Checked)
                self.operations.append(op)
                form.listOperations.addItem(listItem)
        if len(j.Model.OutList) > 0:
            self.baseShape = Part.makeCompound([o.Shape.copy() for o in j.Model.OutList])
        else:
            self.baseShape = None

    def onAccuracyBarChange(self):
        """Update simulation quality"""
        form = self.taskForm.form
        self.quality = form.sliderAccuracy.value()
        qualText = QtCore.QT_TRANSLATE_NOOP("CAM_Simulator", "High")
        if self.quality < 4:
            qualText = QtCore.QT_TRANSLATE_NOOP("CAM_Simulator", "Low")
        elif self.quality < 9:
            qualText = QtCore.QT_TRANSLATE_NOOP("CAM_Simulator", "Medium")
        form.labelAccuracy.setText(qualText)

    def followsVisibilityChange(self):
        """Update job list in accordance with operations visibility"""
        form = self.taskForm.form
        state = form.followsVisibility.isChecked()
        prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/CAM")
        prefs.SetBool("SimulatorFollowsVisibility", state)
        self.onJobChange()

    def onOperationItemChange(self, _item):
        """Check if at least one operation is selected to enable the Play button"""
        playvalid = False
        form = self.taskForm.form
        for i in range(form.listOperations.count()):
            if form.listOperations.item(i).checkState() == QtCore.Qt.CheckState.Checked:
                playvalid = True
                break
        form.toolButtonPlay.setEnabled(playvalid)

    def SimPlay(self):
        """Activate the simulation"""
        self.SetupSimulation()
        self.millSim.ResetSimulation(FreeCADGui.getDocument(self.job.Document))
        for op in self.activeOps:
            tool = PathDressup.toolController(op).Tool
            toolNumber = PathDressup.toolController(op).ToolNumber
            toolProfile = self.GetToolProfile(tool, 0.5)
            self.millSim.AddTool(toolProfile, toolNumber, tool.Diameter, 1)
            opCommands = PathUtils.getPathWithPlacement(op).Commands
            for cmd in opCommands:
                self.millSim.AddCommand(cmd)
        self.millSim.BeginSimulation(self.stock, self.quality)
        if self.baseShape is not None:
            self.millSim.SetBaseShape(self.baseShape, 1)

    def cancel(self):
        """Cancel the simulation"""

        global _active_native_prepared_simulation
        preparedView = self._preparedView
        self._preparedView = None
        if preparedView is not None:
            try:
                FreeCADGui.getMainWindow().removeWindow(preparedView)
            except RuntimeError:
                pass
        if _active_native_prepared_simulation is self:
            _active_native_prepared_simulation = None


def activate_prepared_simulation(**prepared):
    """Open one Native-owned prepared GL simulation and retain task ownership."""

    global _active_native_prepared_simulation
    if _active_native_prepared_simulation is not None:
        raise RuntimeError("A Native prepared GL simulation is already active")
    simulation = CAMSimulation()
    simulation.nativeSimulationId = uuid.uuid4().hex
    simulation.ActivatePrepared(**prepared)
    _active_native_prepared_simulation = simulation
    return simulation


def owns_active_prepared_simulation(document):
    """Return whether the active task belongs to this exact Native document."""

    global _active_native_prepared_simulation
    simulation = _active_native_prepared_simulation
    if simulation is None:
        return False
    try:
        simulation_document = simulation.job.Document
    except (AttributeError, ReferenceError, RuntimeError):
        _active_native_prepared_simulation = None
        return False
    if simulation_document is not document:
        return False
    try:
        guiDocument = FreeCADGui.getDocument(document)
        return bool(FreeCADGui.Control.activeDialog(guiDocument))
    except Exception:
        return False


def active_prepared_simulation():
    """Return the retained prepared simulation for bounded host inspection."""

    return _active_native_prepared_simulation


class CommandCAMSimulate:
    """FreeCAD invoke simulation task panel command"""

    def GetResources(self):
        """Command info"""
        return {
            "Pixmap": "CAM_SimulatorGL",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("CAM_Simulator", "CAM Simulator"),
            "Accel": "P, N",
            "ToolTip": QtCore.QT_TRANSLATE_NOOP("CAM_Simulator", "Simulates G-code on stock"),
        }

    def IsActive(self):
        """Command is active if at least one CAM job exists"""
        return can_start_document_command() and bool(active_jobs())

    def Activated(self):
        """Activate the simulation"""
        if not self.IsActive():
            return

        CamSimulation = CAMSimulation()
        CamSimulation.Activate()


if FreeCAD.GuiUp:
    # register the FreeCAD command
    FreeCADGui.addCommand("CAM_SimulatorGL", CommandCAMSimulate())
    FreeCAD.Console.PrintLog("Loading PathSimulator Gui… done\n")
