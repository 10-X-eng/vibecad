# SPDX-License-Identifier: LGPL-2.1-or-later
# /**************************************************************************
#                                                                           *
#    Copyright (c) 2024 Ondsel <development@ondsel.com>                     *
#                                                                           *
#    This file is part of FreeCAD.                                          *
#                                                                           *
#    FreeCAD is free software: you can redistribute it and/or modify it     *
#    under the terms of the GNU Lesser General Public License as            *
#    published by the Free Software Foundation, either version 2.1 of the   *
#    License, or (at your option) any later version.                        *
#                                                                           *
#    FreeCAD is distributed in the hope that it will be useful, but         *
#    WITHOUT ANY WARRANTY; without even the implied warranty of             *
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
#    Lesser General Public License for more details.                        *
#                                                                           *
#    You should have received a copy of the GNU Lesser General Public       *
#    License along with FreeCAD. If not, see                                *
#    <https://www.gnu.org/licenses/>.                                       *
#                                                                           *
# **************************************************************************/

import re
import os
import time
import tempfile
from pathlib import Path

import FreeCAD as App

from pivy import coin
from Part import LineSegment, Compound

from PySide import QtCore
from PySide.QtCore import QT_TRANSLATE_NOOP

if App.GuiUp:
    import FreeCADGui as Gui
    from PySide import QtGui, QtWidgets
    from PySide.QtWidgets import (
        QPushButton,
        QMenu,
        QDialog,
        QComboBox,
        QLineEdit,
        QGridLayout,
        QLabel,
        QDialogButtonBox,
        QFileDialog,
        QProgressDialog,
    )
    from PySide.QtCore import Qt, QPoint
    from PySide.QtGui import QCursor, QIcon, QGuiApplication, QMessageBox

import UtilsAssembly
import Preferences
from VibeCADNativeTransaction import _OwnedDocumentTransaction

translate = App.Qt.translate

__title__ = "Assembly Command Create Simulation"
__author__ = "Ondsel"
__url__ = "https://www.freecad.org"


class CommandCreateSimulation:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateSimulation",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateSimulation", "Simulation"),
            "Accel": "V",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateSimulation",
                "Creates a new simulation of the current assembly",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        if not UtilsAssembly.isAssemblyCommandActive():
            return False

        assembly = UtilsAssembly.activeAssembly()
        joint_types = ["Revolute", "Slider", "Cylindrical"]
        joints = UtilsAssembly.getJointsOfType(assembly, joint_types)
        return len(joints) > 0

    def Activated(self):
        if not self.IsActive():
            return
        assembly = UtilsAssembly.activeAssembly()
        if not assembly:
            return

        self.panel = TaskAssemblyCreateSimulation(
            document_name=assembly.Document.Name,
            assembly_name=assembly.Name,
        )
        dialog = Gui.Control.showDialog(self.panel, self.panel.gui_doc)
        if dialog is not None:
            dialog.setAutoCloseOnDeletedDocument(True)
            dialog.setDocumentName(assembly.Document.Name)


######### Simulation Object ###########
class Simulation:
    def __init__(self, feaPy):
        feaPy.Proxy = self
        feaPy.addExtension("App::GroupExtensionPython")

        if not hasattr(feaPy, "aTimeStart"):
            feaPy.addProperty(
                "App::PropertyTime",
                "aTimeStart",
                "Simulation",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Simulation start time.",
                ),
                locked=True,
            )

        if not hasattr(feaPy, "bTimeEnd"):
            feaPy.addProperty(
                "App::PropertyTime",
                "bTimeEnd",
                "Simulation",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Simulation end time.",
                ),
                locked=True,
            )

        if not hasattr(feaPy, "cTimeStepOutput"):
            feaPy.addProperty(
                "App::PropertyTime",
                "cTimeStepOutput",
                "Simulation",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Simulation time step for output.",
                ),
                locked=True,
            )

        if not hasattr(feaPy, "fGlobalErrorTolerance"):
            feaPy.addProperty(
                "App::PropertyFloat",
                "fGlobalErrorTolerance",
                "Simulation",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Integration global error tolerance.",
                ),
                locked=True,
            )

        if not hasattr(feaPy, "jFramesPerSecond"):
            feaPy.addProperty(
                "App::PropertyInteger",
                "jFramesPerSecond",
                "Simulation",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "Frames Per Second.",
                ),
                locked=True,
            )

        feaPy.aTimeStart = 0.0
        feaPy.bTimeEnd = 1.0
        feaPy.cTimeStepOutput = 1.0e-2
        feaPy.fGlobalErrorTolerance = 1.0e-6
        feaPy.jFramesPerSecond = 30

        self.motionsChangedCallback = None
        UtilsAssembly.markTimelineOperationEditor(
            feaPy,
            "Assembly_EditHistoryOperation",
        )

    def onDocumentRestored(self, feaPy):
        UtilsAssembly.markTimelineOperationEditor(
            feaPy,
            "Assembly_EditHistoryOperation",
        )
        for motion in feaPy.Group:
            UtilsAssembly.markTimelineResource(motion, feaPy)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onChanged(self, feaPy, prop):
        if prop != "Group":
            return
        for motion in feaPy.Group:
            UtilsAssembly.markTimelineResource(motion, feaPy)
        if (
            hasattr(self, "motionsChangedCallback")
            and self.motionsChangedCallback is not None
        ):
            self.motionsChangedCallback()

    def setMotionsChangedCallback(self, callback):
        self.motionsChangedCallback = callback

    def execute(self, feaPy):
        """Do something when doing a recomputation, this method is mandatory"""
        pass

    def getAssembly(self, feaPy):
        assert feaPy.isDerivedFrom("App::FeaturePython"), "Type error"
        return UtilsAssembly.findOwningAssembly(feaPy)


class ViewProviderSimulation:
    def __init__(self, vpDoc):
        vpDoc.Proxy = self
        self.Object = vpDoc.Object
        self.setProperties(vpDoc)

    def setProperties(self, vpDoc):
        if not hasattr(vpDoc, "Decimals"):
            vpDoc.addProperty(
                "App::PropertyInteger",
                "Decimals",
                "Space",
                QT_TRANSLATE_NOOP(
                    "App::Property", "The number of decimals to use for calculated texts"
                ),
                locked=True,
            )
            vpDoc.Decimals = 9

    def attach(self, vpDoc):
        """Setup the scene sub-graph of the view provider, this method is mandatory"""
        self.app_obj = vpDoc.Object

        self.display_mode = coin.SoType.fromName("SoFCSelection").createInstance()

        vpDoc.addDisplayMode(self.display_mode, "Wireframe")

    def updateData(self, feaPy, prop):
        """If a property of the handled feature has changed we have the chance to handle this here"""
        pass

    def getDisplayModes(self, vpDoc):
        """Return a list of display modes."""
        return ["Wireframe"]

    def getDefaultDisplayMode(self):
        """Return the name of the default display mode. It must be defined in getDisplayModes."""
        return "Wireframe"

    def onChanged(self, vpDoc, prop):
        """Here we can do something when a single property got changed"""
        pass

    def getIcon(self):
        return ":/icons/Assembly_CreateSimulation.svg"

    def dumps(self):
        """When saving the document this object gets stored using Python's json module.\
                Since we have some un-serializable parts here -- the Coin stuff -- we must define this method\
                to return a tuple of all serializable objects or None."""
        return None

    def loads(self, state):
        """When restoring the serialized object from document we have the chance to set some internals here.\
                Since no data were serialized nothing needs to be done here."""
        return None

    def claimChildren(self):
        return self.app_obj.Group

    def doubleClicked(self, vpDoc):
        operation = vpDoc.Object
        if not UtilsAssembly.isTimelineOperationActive(operation):
            return False
        assembly = operation.Proxy.getAssembly(operation)
        if (
            assembly is None
            or not UtilsAssembly.isTimelineOperationActive(assembly)
        ):
            return False

        task = Gui.Control.activeTaskDialog()
        if task:
            task.reject()
            if Gui.Control.activeTaskDialog() is not None:
                return False

        if UtilsAssembly.activeAssembly() != assembly:
            gui_document = Gui.getDocument(assembly.Document.Name)
            if gui_document is None:
                return False
            gui_document.setEdit(assembly)
            if UtilsAssembly.activeAssembly() is not assembly:
                return False

        panel = TaskAssemblyCreateSimulation(
            operation,
            document_name=assembly.Document.Name,
            existing_transaction_id=assembly.Document.getBookedTransactionID(),
        )
        dialog = Gui.Control.showDialog(panel, panel.gui_doc)
        if dialog is not None:
            dialog.setAutoCloseOnDeletedDocument(True)
            dialog.setDocumentName(assembly.Document.Name)

        return True

    def onDelete(self, vobj, subelements):
        for obj in self.claimChildren():
            obj.Document.removeObject(obj.Name)
        return True


########### Motion Object #############
MotionTypes = [
    "Angular",
    "Linear",
]


def _motionTypeMatchesJoint(motion_type, joint):
    if motion_type not in MotionTypes or joint is None:
        return False

    joint_type = getattr(joint, "JointType", "")
    if joint_type == "Revolute":
        return motion_type == "Angular"
    if joint_type == "Slider":
        return motion_type == "Linear"
    if joint_type == "Cylindrical":
        return True
    return False


class Motion:
    def __init__(self, feaPy, motionType=MotionTypes[0], joint=None, formula=""):
        feaPy.Proxy = self

        self.createProperties(feaPy)

        feaPy.MotionType = MotionTypes  # sets the list
        feaPy.MotionType = motionType  # set the initial value
        feaPy.Joint = joint
        feaPy.Formula = formula

    def onDocumentRestored(self, feaPy):
        self.createProperties(feaPy)
        simulation = self.getSimulation(feaPy)
        if simulation is not None:
            UtilsAssembly.markTimelineResource(feaPy, simulation)

    def createProperties(self, feaPy):
        if not hasattr(feaPy, "Joint"):
            feaPy.addProperty(
                "App::PropertyXLinkSubHidden",
                "Joint",
                "Motion",
                QT_TRANSLATE_NOOP("App::Property", "The joint that is moved by the motion"),
                locked=True,
            )

        if not hasattr(feaPy, "Formula"):
            feaPy.addProperty(
                "App::PropertyString",
                "Formula",
                "Motion",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "This is the formula of the motion. For example '1.0*time'.",
                ),
                locked=True,
            )

        if not hasattr(feaPy, "MotionType"):
            feaPy.addProperty(
                "App::PropertyEnumeration",
                "MotionType",
                "Motion",
                QT_TRANSLATE_NOOP("App::Property", "The type of the motion"),
                locked=True,
            )

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onChanged(self, feaPy, prop):
        pass

    def execute(self, feaPy):
        """Do something when doing a recomputation, this method is mandatory"""
        pass

    def getSimulation(self, feaPy):
        for obj in feaPy.InList:
            if hasattr(obj, "Proxy"):
                if hasattr(obj.Proxy, "setMotionsChangedCallback"):
                    return obj
        return None

    def getAssembly(self, feaPy):
        simulation = self.getSimulation(feaPy)
        if simulation is not None:
            return simulation.Proxy.getAssembly(simulation)
        return None


class ViewProviderMotion:
    def __init__(self, vp):
        vp.Proxy = self
        self.updateLabel()

    def attach(self, vpDoc):
        """Setup the scene sub-graph of the view provider, this method is mandatory"""
        self.app_obj = vpDoc.Object

        self.display_mode = coin.SoType.fromName("SoFCSelection").createInstance()

        vpDoc.addDisplayMode(self.display_mode, "Wireframe")

    def updateData(self, feaPy, prop):
        """If a property of the handled feature has changed we have the chance to handle this here"""
        pass

    def getDisplayModes(self, vpDoc):
        """Return a list of display modes."""
        return ["Wireframe"]

    def getDefaultDisplayMode(self):
        """Return the name of the default display mode. It must be defined in getDisplayModes."""
        return "Wireframe"

    def onChanged(self, vpDoc, prop):
        """Here we can do something when a single property got changed"""
        # App.Console.PrintMessage("Change property: " + str(prop) + "\n")
        pass

    def getIcon(self):
        if self.app_obj.MotionType == "Angular":
            return ":/icons/button_rotate.svg"

        return ":/icons/button_right.svg"

    def dumps(self):
        """When saving the document this object gets stored using Python's json module.\
                Since we have some un-serializable parts here -- the Coin stuff -- we must define this method\
                to return a tuple of all serializable objects or None."""
        return None

    def loads(self, state):
        """When restoring the serialized object from document we have the chance to set some internals here.\
                Since no data were serialized nothing needs to be done here."""
        return None

    def doubleClicked(self, vpDoc):
        return self.openEditDialog()

    def openEditDialog(self, existing_transaction_id=0):
        motion = self.app_obj
        document = getattr(motion, "Document", None)
        if (
            not UtilsAssembly._document_is_open(document)
            or document.getObject(motion.Name) is not motion
            or not UtilsAssembly.isTimelineOperationActive(motion)
        ):
            return False
        document_uid = str(getattr(document, "Uid", "") or "")
        motion_identity = (
            str(motion.Name),
            int(motion.ID),
            motion,
        )

        assembly = self.getAssembly(activate=False)

        if (
            assembly is None
            or not UtilsAssembly.isTimelineOperationActive(assembly)
        ):
            return False
        assembly_identity = (
            str(assembly.Name),
            int(assembly.ID),
            assembly,
        )

        gui_document = Gui.getDocument(document.Name)
        if gui_document is None:
            return False

        booked_transaction_id = int(document.getBookedTransactionID())
        existing_transaction_id = int(existing_transaction_id or 0)
        if existing_transaction_id:
            if (
                booked_transaction_id != existing_transaction_id
                or not Gui.Control.ownsCommandTransaction(
                    gui_document,
                    existing_transaction_id,
                )
            ):
                return False
            owned_transaction = None
        else:
            if (
                booked_transaction_id
                or document.HasPendingTransaction
                or Gui.Control.activeDialog(gui_document)
            ):
                return False
            owned_transaction = _OwnedDocumentTransaction(
                document,
                "Edit Assembly motion",
            )

        joint = None
        if motion.Joint is not None:
            joint = motion.Joint[0]

        try:
            dialog = MotionEditDialog(
                assembly,
                motion.MotionType,
                joint,
                motion.Formula,
            )
            accepted = bool(dialog.exec_())
            if accepted:
                motion_name, motion_id, exact_motion = (
                    motion_identity
                )
                assembly_name, assembly_id, exact_assembly = (
                    assembly_identity
                )
                if (
                    not UtilsAssembly._document_is_open(document)
                    or str(getattr(document, "Uid", "") or "")
                    != document_uid
                    or document.getObject(motion_name)
                    is not exact_motion
                    or int(exact_motion.ID) != motion_id
                    or document.getObject(assembly_name)
                    is not exact_assembly
                    or int(exact_assembly.ID) != assembly_id
                    or not UtilsAssembly.isTimelineOperationActive(
                        exact_assembly
                    )
                    or not UtilsAssembly.isTimelineOperationActive(motion)
                ):
                    raise RuntimeError(
                        "The Assembly motion changed identity while editing"
                    )
                selected_joint = dialog.joint
                if (
                    selected_joint is None
                    or selected_joint.Document is not document
                    or document.getObject(selected_joint.Name)
                    is not selected_joint
                    or int(selected_joint.ID) <= 0
                    or not UtilsAssembly.isTimelineOperationActive(
                        selected_joint
                    )
                    or selected_joint not in assembly.Joints
                ):
                    raise RuntimeError(
                        "The Assembly motion requires one active joint "
                        "from its exact assembly"
                    )
                if not _motionTypeMatchesJoint(
                    dialog.motionType,
                    selected_joint,
                ):
                    raise RuntimeError(
                        "The Assembly motion type is incompatible with "
                        "the selected joint"
                    )
                motion.MotionType = dialog.motionType
                motion.Joint = selected_joint
                motion.Formula = dialog.formula
                self.updateLabel()
                document.recompute()

            if owned_transaction is not None:
                if accepted:
                    owned_transaction.commit()
                else:
                    owned_transaction.abort()
            return accepted
        except Exception:
            if owned_transaction is not None:
                owned_transaction.abort()
            raise

    def updateLabel(self):
        if self.app_obj.Joint is None:
            return

        typeStr = "Linear" if self.app_obj.MotionType == "Linear" else "Angular"

        self.app_obj.Label = "{label} ({type_})".format(
            label=self.app_obj.Joint[0].Label, type_=translate("Assembly", typeStr)
        )

    def getAssembly(self, activate=True):
        assembly = self.app_obj.Proxy.getAssembly(self.app_obj)

        if assembly is None:
            return None

        if activate and UtilsAssembly.activeAssembly() != assembly:
            gui_document = Gui.getDocument(assembly.Document.Name)
            if gui_document is None:
                return None
            gui_document.setEdit(assembly)

        return assembly


class MotionEditDialog:
    def __init__(
        self, assembly, motionType=MotionTypes[0], joint=None, formula="initialValue + 5*time"
    ):
        self.assembly = assembly
        self.motionType = motionType
        self.joint = joint
        self.formula = formula

        # Create a non-modal, frameless dialog
        self.dialog = QDialog()
        self.dialog.setWindowFlags(Qt.Popup)
        self.initialPos = QCursor.pos()
        self.dialog.setMinimumSize(500, 200)  # Set a reasonable minimum size

        # Create the joints combobox
        self.joint_combo = QComboBox(self.dialog)
        self.setup_joint_combo()

        # Create the motion type combobox
        self.motion_type_combo = QComboBox(self.dialog)
        self.setup_motiontype_combo()

        def on_motion_type_changed(text):
            self.motionType = text

        self.motion_type_combo.currentTextChanged.connect(on_motion_type_changed)

        def on_joint_changed(index):
            self.joint = self.joint_combo.itemData(index)
            self.setup_motiontype_combo()  # Refresh the motion combo box based on the new joint type

        self.joint_combo.currentIndexChanged.connect(on_joint_changed)

        # Create the line edit for the formula
        formula_edit = QLineEdit(self.dialog)
        formula_edit.setText(self.formula)
        formula_edit.setPlaceholderText(translate("Assembly", "Enter your formula…"))

        # Connect the line edit to update the Formula property
        def on_formula_changed(text):
            self.formula = text

        formula_edit.textChanged.connect(on_formula_changed)

        self.setupHelpSection()

        # Create Ok and Cancel buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self.dialog
        )
        button_box.accepted.connect(self.dialog.accept)
        button_box.rejected.connect(self.dialog.reject)

        # Set up the layout of the dialog
        layout = QGridLayout(self.dialog)

        # Add labels and widgets to the layout
        layout.addWidget(QLabel("Joint"), 0, 0)
        layout.addWidget(self.joint_combo, 0, 1)

        layout.addWidget(QLabel("Motion Type"), 1, 0)
        layout.addWidget(self.motion_type_combo, 1, 1)

        layout.addWidget(QLabel("Formula"), 2, 0)
        layout.addWidget(formula_edit, 2, 1)

        # Add the help label above the buttons
        layout.addWidget(self.help_label0, 3, 0, 1, 2)
        layout.addWidget(self.help_label1, 4, 0, 1, 2)
        layout.addWidget(self.help_label2, 5, 0, 1, 2)
        layout.addWidget(self.help_label3, 6, 0, 1, 2)
        layout.addWidget(self.help_label4, 7, 0, 1, 2)
        layout.addWidget(self.help_label5, 8, 0, 1, 2)
        layout.addWidget(self.help_label6, 9, 0, 1, 2)
        layout.addWidget(self.help_label7, 10, 0, 1, 2)
        # Add the help button and button box in the next row
        layout.addWidget(self.help_button, 11, 0)

        layout.addWidget(button_box, 11, 1)

        self.positionDialog()

    def setupHelpSection(self):

        # Create the help QLabels and set them to be initially hidden
        self.help_label0 = QLabel(
            translate(
                "Assembly",
                "In capital are variables that you need to replace with actual values. 'initialValue' is dynamically replaced by the current angle or distance. More details about each example in its tooltip.",
            ),
            self.dialog,
        )
        self.help_label1 = QLabel(translate("Assembly", " - Linear: C + VEL*time"), self.dialog)
        self.help_label2 = QLabel(
            translate("Assembly", " - Quadratic: C + VEL*time + ACC*time^2"), self.dialog
        )
        self.help_label3 = QLabel(
            translate("Assembly", " - Harmonic: C + AMP*sin(VEL*time - PHASE)"), self.dialog
        )
        self.help_label4 = QLabel(
            translate("Assembly", " - Exponential: C*exp(time/TIMEC)"), self.dialog
        )
        self.help_label5 = QLabel(
            translate(
                "Assembly",
                " - Smooth Step: L1 + (L2 - L1)*((1/2) + (1/pi)*arctan(SLOPE*(time - T0)))",
            ),
            self.dialog,
        )
        self.help_label6 = QLabel(
            translate(
                "Assembly",
                " - Smooth Square Impulse: (H/pi)*(arctan(SLOPE*(time - T1)) - arctan(SLOPE*(time - T2)))",
            ),
            self.dialog,
        )
        self.help_label7 = QLabel(
            translate(
                "Assembly",
                " - Smooth Ramp Top Impulse: ((1/pi)*(arctan(1000*(time - T1)) - arctan(1000*(time - T2))))*(((H2 - H1)/(T2 - T1))*(time - T1) + H1)",
            ),
            self.dialog,
        )

        self.help_label1.setToolTip(
            translate(
                "Assembly",
                """C is a constant offset.
VEL is a velocity or slope or gradient of the straight line.""",
            )
        )
        self.help_label2.setToolTip(
            translate(
                "Assembly",
                """C is a constant offset.
VEL is the velocity or slope or gradient of the straight line.
ACC is the acceleration or coefficient of the second order. The function is a parabola.""",
            )
        )
        self.help_label3.setToolTip(
            translate(
                "Assembly",
                """C is a constant offset.
AMP is the amplitude of the sine wave.
VEL is the angular velocity in radians per second.
PHASE is the phase of the sine wave.""",
            )
        )
        self.help_label4.setToolTip(
            translate(
                "Assembly",
                """C is a constant.
TIMEC is the time constant of the exponential function.""",
            )
        )
        self.help_label5.setToolTip(
            translate(
                "Assembly",
                """L1 is step level before time = T0.
L2 is step level after time = T0.
SLOPE defines the steepness of the transition between L1 and L2 about time = T0. Higher values gives sharper cornered steps. SLOPE = 1000 or greater are suitable.""",
            )
        )
        self.help_label6.setToolTip(
            translate(
                "Assembly",
                """H is the height of the impulse.
T1 is the start of the impulse.
T2 is the end of the impulse.
SLOPE defines the steepness of the transition between 0 and H about time = T1 and T2. Higher values gives sharper cornered impulses. SLOPE = 1000 or greater are suitable.""",
            )
        )
        self.help_label7.setToolTip(
            translate(
                "Assembly",
                """This is similar to the square impulse but the top has a sloping ramp. It is good for building a smooth piecewise linear function by adding a series of these.
T1 is the start of the impulse.
T2 is the end of the impulse.
H1 is the height at T1 at the beginning of the ramp.
H2 is the height at T2 at the end of the ramp.
SLOPE defines the steepness of the transition between 0 and H1 and H2 to 0 about time = T1 and T2 respectively. Higher values gives sharper cornered impulses. SLOPE = 1000 or greater are suitable.""",
            )
        )

        self.help_label0.setWordWrap(True)
        self.help_label1.setWordWrap(True)
        self.help_label2.setWordWrap(True)
        self.help_label3.setWordWrap(True)
        self.help_label4.setWordWrap(True)
        self.help_label5.setWordWrap(True)
        self.help_label6.setWordWrap(True)
        self.help_label7.setWordWrap(True)

        width = 1000
        self.help_label0.setFixedWidth(width)
        self.help_label1.setFixedWidth(width)
        self.help_label2.setFixedWidth(width)
        self.help_label3.setFixedWidth(width)
        self.help_label4.setFixedWidth(width)
        self.help_label5.setFixedWidth(width)
        self.help_label6.setFixedWidth(width)
        self.help_label7.setFixedWidth(width)

        self.help_label0.setVisible(False)
        self.help_label1.setVisible(False)
        self.help_label2.setVisible(False)
        self.help_label3.setVisible(False)
        self.help_label4.setVisible(False)
        self.help_label5.setVisible(False)
        self.help_label6.setVisible(False)
        self.help_label7.setVisible(False)

        self.help_label1.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.help_label2.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.help_label3.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.help_label4.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.help_label5.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.help_label6.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.help_label7.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # Create the Help button
        self.help_button = QPushButton(translate("Assembly", "Help"), self.dialog)

        # Slot to toggle help visibility and button text
        def toggle_help():
            show = not self.help_label1.isVisible()
            self.help_label0.setVisible(show)
            self.help_label1.setVisible(show)
            self.help_label2.setVisible(show)
            self.help_label3.setVisible(show)
            self.help_label4.setVisible(show)
            self.help_label5.setVisible(show)
            self.help_label6.setVisible(show)
            self.help_label7.setVisible(show)

            if show:
                self.help_button.setText(translate("Assembly", "Hide help"))
            else:
                self.help_button.setText(translate("Assembly", "Help"))

            self.positionDialog()

        self.help_button.clicked.connect(toggle_help)

    def positionDialog(self):
        self.dialog.adjustSize()

        # Get the screen where the mouse is located
        screen = QGuiApplication.screenAt(self.initialPos)
        screen_geometry = (
            screen.availableGeometry()
            if screen
            else QApplication.primaryScreen().availableGeometry()
        )

        # Calculate the position of the dialog to ensure it stays within the screen
        dialog_position = self.initialPos

        # Adjust position to keep the dialog within the screen bounds
        if dialog_position.x() + self.dialog.width() > screen_geometry.right():
            dialog_position.setX(screen_geometry.right() - self.dialog.width())
        if dialog_position.y() + self.dialog.height() > screen_geometry.bottom():
            dialog_position.setY(screen_geometry.bottom() - self.dialog.height())

        # Ensure the dialog does not go above or to the left of the screen
        if dialog_position.x() < screen_geometry.left():
            dialog_position.setX(screen_geometry.left())
        if dialog_position.y() < screen_geometry.top():
            dialog_position.setY(screen_geometry.top())

        # Move the dialog to the final position
        self.dialog.move(dialog_position)

    def setup_joint_combo(self):
        # Function to set up the joint combo box based on the selected motion type

        self.joint_combo.clear()  # Clear existing items

        jointTypes = ["Revolute", "Slider", "Cylindrical"]

        joints = UtilsAssembly.getJointsOfType(self.assembly, jointTypes)

        # Add joints to the combo box with labels and icons
        for joint in joints:
            joint_label = joint.Label
            joint_icon = QIcon(joint.ViewObject.Icon)
            self.joint_combo.addItem(joint_icon, joint_label, userData=joint)

        # Set the current value based on the object's Joint property
        if self.joint in joints:
            self.joint_combo.setCurrentText(self.joint.Label)
        elif len(joints) > 0:
            self.joint = joints[0]

    def setup_motiontype_combo(self):
        self.motion_type_combo.clear()  # Clear existing items

        if self.joint is None:
            return

        if self.joint.JointType == "Revolute":
            types = ["Angular"]
        elif self.joint.JointType == "Slider":
            types = ["Linear"]
        else:
            types = ["Angular", "Linear"]

        self.motion_type_combo.addItems(types)

        # Set current value based on the object's MotionType
        if self.motionType in types:
            self.motion_type_combo.setCurrentText(self.motionType)
        else:
            # self.motionType is no longer available, so we reset it to first entry
            self.motionType = types[0]

    def exec_(self):
        return self.dialog.exec()


######### Create Simulation Task ###########
class TaskAssemblyCreateSimulation(QtCore.QObject):
    def __init__(
        self,
        simFeaturePy=None,
        document_name=None,
        existing_transaction_id=0,
        assembly_name=None,
    ):
        super().__init__()

        if simFeaturePy is not None:
            operation_document = getattr(simFeaturePy, "Document", None)
            if (
                not UtilsAssembly._document_is_open(operation_document)
                or operation_document.getObject(simFeaturePy.Name)
                is not simFeaturePy
                or not UtilsAssembly.isTimelineOperationActive(simFeaturePy)
            ):
                raise RuntimeError(
                    "The simulation operation is not active and live"
                )
            self.assembly = simFeaturePy.Proxy.getAssembly(simFeaturePy)
        elif document_name is not None and assembly_name is not None:
            try:
                task_document = App.getDocument(document_name)
            except (NameError, RuntimeError):
                task_document = None
            self.assembly = (
                task_document.getObject(assembly_name)
                if task_document is not None
                else None
            )
            if (
                self.assembly is None
                or not self.assembly.isDerivedFrom(
                    "Assembly::AssemblyObject"
                )
                or UtilsAssembly.activeAssembly() is not self.assembly
            ):
                raise RuntimeError(
                    "The simulation task lost its exact active assembly"
                )
        else:
            self.assembly = UtilsAssembly.activeAssembly()
        if self.assembly is None:
            raise RuntimeError("An active assembly is required for a simulation")
        if not UtilsAssembly.isTimelineOperationActive(self.assembly):
            raise RuntimeError(
                "The simulation assembly is not active in History"
            )

        self.initialPlcs = (
            UtilsAssembly._saveExactAssemblyPartPlacements(
                self.assembly
            )
        )

        self.doc = self.assembly.Document
        if document_name is not None and self.doc.Name != document_name:
            raise RuntimeError("The simulation task document changed before launch")
        if simFeaturePy is not None and simFeaturePy.Document is not self.doc:
            raise RuntimeError("The simulation does not belong to the assembly")
        self.document_uid = str(
            getattr(self.doc, "Uid", "") or ""
        )
        Gui.Selection.clearSelection(self.doc.Name)
        self.assembly_identity = (
            str(self.assembly.Name),
            int(self.assembly.ID),
            self.assembly,
        )

        self.gui_doc = Gui.getDocument(self.doc.Name)
        if self.gui_doc is None:
            raise RuntimeError("The simulation task has no GUI document")

        self.view = self.gui_doc.activeView()

        if self.view is None:
            raise RuntimeError("The simulation task has no active 3D view")

        self.transaction = UtilsAssembly._TaskTransactionOwner(
            self.doc,
            (
                "Edit " + simFeaturePy.Label + " Simulation"
                if simFeaturePy
                else "Create Simulation"
            ),
            existing_transaction_id,
        )

        self.runKinematicsTimer = QtCore.QTimer()
        self.runKinematicsTimer.setSingleShot(True)
        self.runKinematicsTimer.timeout.connect(self.displayLastFrame)

        self.animationTimer = QtCore.QTimer()
        self.animationTimer.setInterval(50)  # ms
        self.animationTimer.timeout.connect(self.playAnimation)

        self.form = Gui.PySideUic.loadUi(":/panels/TaskAssemblyCreateSimulation.ui")
        self.form.motionList.installEventFilter(self)
        self.setSpinboxPrecision(self.form.TimeStartSpinBox, 9)
        self.setSpinboxPrecision(self.form.TimeEndSpinBox, 9)
        self.setSpinboxPrecision(self.form.TimeStepOutputSpinBox, 9)
        self.setSpinboxPrecision(self.form.GlobalErrorToleranceSpinBox, 9, App.Units.Length)
        self.form.motionList.itemDoubleClicked.connect(self.onItemDoubleClicked)
        self.form.TimeStartSpinBox.valueChanged.connect(self.onTimeStartChanged)
        self.form.TimeEndSpinBox.valueChanged.connect(self.onTimeEndChanged)
        self.form.TimeStepOutputSpinBox.valueChanged.connect(self.onTimeStepOutputChanged)
        self.form.GlobalErrorToleranceSpinBox.valueChanged.connect(
            self.onGlobalErrorToleranceChanged
        )
        self.form.RunKinematicsButton.clicked.connect(self.runKinematics)
        self.form.frameSlider.valueChanged.connect(self.onFrameChanged)
        self.form.FramesPerSecondSpinBox.valueChanged.connect(self.onFramesPerSecondChanged)
        self.form.PlayBackwardButton.clicked.connect(self.animationTimerStartBackward)
        self.form.PlayForwardButton.clicked.connect(self.animationTimerStartForward)
        self.form.StepBackwardButton.clicked.connect(self.stepBackward)
        self.form.StepForwardButton.clicked.connect(self.stepForward)
        self.form.StopButton.clicked.connect(self.stopAnimation)
        self.form.AddButton.clicked.connect(self.addMotionClicked)
        self.form.RemoveButton.clicked.connect(self.deleteSelectedMotions)
        self.form.groupBox_player.hide()
        self.form.SaveAnimationButton.clicked.connect(self.saveAnimation)
        self.form.SaveAnimationButton.hide()

        self.creating_timeline_operation = simFeaturePy is None
        if simFeaturePy:
            self.simFeaturePy = simFeaturePy
            self.timeline_resource_edit = (
                UtilsAssembly.stageTimelineResourceGroupEdit(
                    self.simFeaturePy
                )
            )
            self.onMotionsChanged()
        else:
            self.timeline_resource_edit = None
            self.createSimulationObject()
        self.simulation_identity = (
            str(self.simFeaturePy.Name),
            int(self.simFeaturePy.ID),
            self.simFeaturePy,
        )

        self.setUiInitialValues()

        self.simFeaturePy.Proxy.setMotionsChangedCallback(self.onMotionsChanged)

        self.currentFrm = 1
        self.startFrm = 1
        self.endFrm = 100
        self.fps = 30
        self.deltaTime = 1.0 / self.fps
        self.startTime = time.time()
        self.index = 0

    def _ownsLiveTaskContext(self):
        assembly_name, assembly_id, exact_assembly = (
            self.assembly_identity
        )
        simulation_name, simulation_id, exact_simulation = (
            self.simulation_identity
        )
        try:
            return (
                self.transaction.owns_current()
                and UtilsAssembly._document_is_open(self.doc)
                and str(getattr(self.doc, "Uid", "") or "")
                == self.document_uid
                and self.doc.getObject(assembly_name)
                is exact_assembly
                and int(exact_assembly.ID) == assembly_id
                and self.assembly is exact_assembly
                and UtilsAssembly.isTimelineOperationActive(
                    self.assembly
                )
                and self.doc.getObject(simulation_name)
                is exact_simulation
                and int(exact_simulation.ID) == simulation_id
                and self.simFeaturePy is exact_simulation
                and UtilsAssembly.isTimelineOperationActive(
                    self.simFeaturePy
                )
                and UtilsAssembly.findOwningAssembly(
                    self.simFeaturePy,
                    include_inactive=True,
                )
                is self.assembly
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return False

    def setUiInitialValues(self):
        self.form.TimeStartSpinBox.setProperty("rawValue", self.simFeaturePy.aTimeStart.Value)
        self.form.TimeEndSpinBox.setProperty("rawValue", self.simFeaturePy.bTimeEnd.Value)
        self.form.TimeStepOutputSpinBox.setProperty(
            "rawValue", self.simFeaturePy.cTimeStepOutput.Value
        )
        self.form.GlobalErrorToleranceSpinBox.setProperty(
            "rawValue", self.simFeaturePy.fGlobalErrorTolerance
        )
        self.form.FramesPerSecondSpinBox.setValue(self.simFeaturePy.jFramesPerSecond)

    def setSpinboxPrecision(self, spinbox, precision, unit=App.Units.TimeSpan):
        q = App.Units.Quantity()
        q.Unit = unit
        q.Format = {"Precision": precision}
        spinbox.setProperty("value", q)

    def accept(self):
        if not self._ownsLiveTaskContext():
            App.Console.PrintError(
                "Could not finalize the simulation: "
                "the task no longer owns its exact Assembly objects and "
                "document transaction\n"
            )
            return False
        try:
            UtilsAssembly._restoreExactAssemblyPartPlacements(
                self.assembly,
                self.initialPlcs,
            )
            if self.creating_timeline_operation:
                self.doc.finalizeProvisionalTimelineOperationBlock(
                    self.simFeaturePy,
                    [*self.simFeaturePy.Group, self.simFeaturePy],
                )
            else:
                UtilsAssembly.finalizeTimelineResourceGroupEdit(
                    self.simFeaturePy,
                    self.timeline_resource_edit,
                    list(self.simFeaturePy.Group),
                )
        except Exception as error:
            App.Console.PrintError(
                "Could not finalize the simulation: "
                f"{error}\n"
            )
            return False

        self.deactivate()
        return True

    def reject(self):
        self.deactivate()
        return True

    def autoClosedOnDeletedDocument(self):
        self.animationTimer.stop()
        self.transaction.document_deleted()

    def deactivate(self):
        self.animationTimer.stop()
        simulation_name, simulation_id, exact_simulation = (
            self.simulation_identity
        )
        if (
            UtilsAssembly._document_is_open(self.doc)
            and str(getattr(self.doc, "Uid", "") or "")
            == self.document_uid
            and self.doc.getObject(simulation_name)
            is exact_simulation
            and int(exact_simulation.ID) == simulation_id
        ):
            exact_simulation.Proxy.setMotionsChangedCallback(None)

    def onTimeStartChanged(self, quantity):
        if not self._ownsLiveTaskContext():
            return
        self.simFeaturePy.aTimeStart = self.form.TimeStartSpinBox.property("rawValue")

    def onTimeEndChanged(self, quantity):
        if not self._ownsLiveTaskContext():
            return
        self.simFeaturePy.bTimeEnd = self.form.TimeEndSpinBox.property("rawValue")

    def onTimeStepOutputChanged(self, quantity):
        if not self._ownsLiveTaskContext():
            return
        self.simFeaturePy.cTimeStepOutput = self.form.TimeStepOutputSpinBox.property("rawValue")

    def onGlobalErrorToleranceChanged(self, quantity):
        if not self._ownsLiveTaskContext():
            return
        self.simFeaturePy.fGlobalErrorTolerance = self.form.GlobalErrorToleranceSpinBox.property(
            "rawValue"
        )

    def onItemDoubleClicked(self, item):
        if not self._ownsLiveTaskContext():
            return
        identity = item.data(QtCore.Qt.UserRole)
        if (
            not isinstance(identity, tuple)
            or len(identity) != 2
        ):
            return
        motion = self.doc.getObject(identity[0])
        if (
            motion is None
            or int(motion.ID) != int(identity[1])
            or motion not in self.simFeaturePy.Group
            or not UtilsAssembly.isTimelineOperationActive(motion)
        ):
            self.onMotionsChanged()
            return
        motion.ViewObject.Proxy.openEditDialog(
            self.transaction.transaction_id,
        )
        self.onMotionsChanged()

    def createSimulationObject(self):
        sim_group = UtilsAssembly.getSimulationGroup(self.assembly)
        self.simFeaturePy = sim_group.newObject("App::FeaturePython", "Simulation")
        Simulation(self.simFeaturePy)
        ViewProviderSimulation(self.simFeaturePy.ViewObject)

    def createMotionObject(self, motionType, joint, formula):
        if (
            not self._ownsLiveTaskContext()
            or not _motionTypeMatchesJoint(motionType, joint)
            or joint.Document is not self.doc
            or self.doc.getObject(joint.Name) is not joint
            or joint not in self.assembly.Joints
            or not UtilsAssembly.isTimelineOperationActive(joint)
        ):
            raise RuntimeError(
                "A motion requires one compatible active joint from this "
                "exact assembly"
            )
        motion = self.assembly.newObject("App::FeaturePython", "Motion")
        Motion(motion, motionType, joint, formula)
        ViewProviderMotion(motion.ViewObject)

        listOfMotions = self.simFeaturePy.Group
        listOfMotions.append(motion)
        self.simFeaturePy.Group = listOfMotions
        UtilsAssembly.markTimelineResource(motion, self.simFeaturePy)

    def onMotionsChanged(self):
        if (
            hasattr(self, "simulation_identity")
            and not self._ownsLiveTaskContext()
        ):
            return
        self.form.motionList.clear()
        for motion in self.simFeaturePy.Group:
            if not UtilsAssembly.isTimelineOperationActive(motion):
                continue
            item = QtWidgets.QListWidgetItem(motion.Label)
            item.setData(
                QtCore.Qt.UserRole,
                (str(motion.Name), int(motion.ID)),
            )
            self.form.motionList.addItem(item)

    def runKinematics(self):
        if not self._ownsLiveTaskContext():
            return
        self.assembly.generateSimulation(self.simFeaturePy)
        nFrms = self.assembly.numberOfFrames()
        self.form.frameSlider.setMaximum(nFrms - 1)
        self.setFrameValue(nFrms - 1)
        self.form.groupBox_player.show()
        self.form.SaveAnimationButton.show()

    def onFrameChanged(self, val):
        if not self._ownsLiveTaskContext():
            return
        self.assembly.updateForFrame(val)
        self.form.FrameLabel.setText(translate("Assembly", "Frame" + " " + str(val)))
        time = float(val * self.simFeaturePy.cTimeStepOutput)
        self.form.FrameTimeLabel.setText(f"{time:.2f} s")

    def onFramesPerSecondChanged(self):
        if not self._ownsLiveTaskContext():
            return
        self.simFeaturePy.jFramesPerSecond = self.form.FramesPerSecondSpinBox.value()

    def playBackward(self):
        pass

    def animationTimerStartForward(self):
        self.direction = 1
        self.animationTimerStart()

    def animationTimerStartBackward(self):
        self.direction = -1
        self.animationTimerStart()

    def animationTimerStart(self):
        self.animationTimer.stop()
        if not self._ownsLiveTaskContext():
            return
        self.currentFrm = self.form.frameSlider.value()
        self.startFrm = 1
        self.endFrm = self.form.frameSlider.maximum()
        if self.startFrm >= self.endFrm:
            return

        self.fps = self.simFeaturePy.jFramesPerSecond
        self.deltaTime = 1.0 / self.fps
        self.startTime = time.time()
        self.index = self.currentFrm
        self.animationTimer.setInterval(self.deltaTime * 1000)  # ms
        self.animationTimer.start()

    def playAnimation(self):
        if not self._ownsLiveTaskContext():
            self.animationTimer.stop()
            return
        range_ = self.endFrm - self.startFrm
        offset = self.currentFrm - self.startFrm
        count = int((time.time() - self.startTime) / self.deltaTime)
        self.index = ((self.direction * count + offset) % range_) + self.startFrm
        self.setFrameValue(self.index)

    def displayLastFrame(self):
        if not self._ownsLiveTaskContext():
            return
        nFrms = self.assembly.numberOfFrames()
        self.setFrameValue(nFrms - 1)

    def stepBackward(self):
        self.animationTimer.stop()
        if not self._ownsLiveTaskContext():
            return

        nextFrm = self.form.frameSlider.value() - 1
        if nextFrm < 1:
            nextFrm = self.form.frameSlider.maximum()  # wraparound
        self.setFrameValue(nextFrm)

    def stepForward(self):
        self.animationTimer.stop()
        if not self._ownsLiveTaskContext():
            return

        nextFrm = self.form.frameSlider.value() + 1
        if nextFrm > self.form.frameSlider.maximum():
            nextFrm = 1  # wraparound
        self.setFrameValue(nextFrm)

    def setFrameValue(self, val):
        if val < 1:
            val = 1
        if val > self.form.frameSlider.maximum():
            val = self.form.frameSlider.maximum()

        self.form.frameSlider.setValue(val)

    def stopAnimation(self):
        self.animationTimer.stop()

    def addMotionClicked(self):
        if not self._ownsLiveTaskContext():
            return
        dialog = MotionEditDialog(self.assembly)
        if dialog.exec_():
            self.createMotionObject(dialog.motionType, dialog.joint, dialog.formula)

    # Taskbox keyboard event handler
    def eventFilter(self, watched, event):
        if self.form is not None and watched == self.form.motionList:
            if event.type() == QtCore.QEvent.ShortcutOverride:
                if event.key() == QtCore.Qt.Key_Delete:
                    event.accept()
                    return True  # Indicate that the event has been handled
                return False

            elif event.type() == QtCore.QEvent.KeyPress:
                if event.key() == QtCore.Qt.Key_Delete:
                    self.deleteSelectedMotions()
                    return True  # Consume the event

        return super().eventFilter(watched, event)

    def deleteSelectedMotions(self):
        if not self._ownsLiveTaskContext():
            return
        selected_indexes = self.form.motionList.selectedIndexes()
        sorted_indexes = sorted(selected_indexes, key=lambda x: x.row(), reverse=True)
        for index in sorted_indexes:
            item = self.form.motionList.item(index.row())
            identity = item.data(QtCore.Qt.UserRole)
            if (
                not isinstance(identity, tuple)
                or len(identity) != 2
            ):
                continue
            motion = self.doc.getObject(identity[0])
            if (
                motion is None
                or int(motion.ID) != int(identity[1])
                or motion not in self.simFeaturePy.Group
                or not UtilsAssembly.isTimelineOperationActive(
                    motion
                )
            ):
                continue
            group = list(self.simFeaturePy.Group)
            group.remove(motion)
            self.simFeaturePy.Group = group
            self.doc.removeObject(motion.Name)

    def saveAnimation(self):
        if not self._ownsLiveTaskContext():
            return
        num_frames = self.assembly.numberOfFrames()
        if num_frames <= 1:
            QMessageBox.warning(
                self.form,
                translate("Assembly", "Animation"),
                translate("Assembly", "Not enough frames to create an animation."),
            )
            return

        # Prompt user for file location and type
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self.form,
            translate("Assembly", "Save Animation"),
            "",
            "MP4 Video (*.mp4);;Animated GIF (*.gif);;AVI Video (*.avi)",
        )

        if not file_path:
            return  # User cancelled

        # Get parameters
        view = self.view
        width, height = view.getSize()
        # Ensure dimensions are even, as required by many video codecs
        if width % 2 != 0:
            width -= 1
        if height % 2 != 0:
            height -= 1
        fps = self.form.FramesPerSecondSpinBox.value()

        # Setup temporary directory and progress bar
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            progress = QProgressDialog(
                translate("Assembly", "Generating Frames…"),
                translate("Assembly", "Cancel"),
                0,
                num_frames,
                self.form,
            )
            progress.setWindowModality(Qt.WindowModal)
            progress.show()

            original_frame = self.form.frameSlider.value()

            try:
                # Generate and save all frames as temporary images
                frame_files = []
                for i in range(num_frames):
                    progress.setValue(i)
                    if progress.wasCanceled():
                        App.Console.PrintMessage("Animation save cancelled.\n")
                        return

                    self.assembly.updateForFrame(i)
                    Gui.updateGui()  # Ensure the 3D view is redrawn

                    frame_filename = temp_path / f"frame_{i:05d}.png"
                    view.saveImage(str(frame_filename), width, height, "Current")
                    frame_files.append(str(frame_filename))

                # Assemble the final animation file
                progress.setLabelText(translate("Assembly", "Assembling animation…"))
                progress.setMaximum(0)  # Indeterminate progress

                success = False
                file_extension = Path(file_path).suffix.lower()
                if file_extension == ".gif":
                    success = self.create_gif(file_path, frame_files, fps)
                elif file_extension in [".mp4", ".avi"]:
                    success = self.create_video(file_path, frame_files, fps, (width, height))

                if success:
                    App.Console.PrintMessage(f"Animation successfully saved to {file_path}\n")

            except Exception as e:
                errMsg = (
                    translate("Assembly", "An error occurred while saving the animation")
                    + ": "
                    + str(e)
                )
                QMessageBox.critical(self.form, "Error", errMsg)
            finally:
                progress.close()
                # Restore original state
                self.assembly.updateForFrame(original_frame)
                self.form.frameSlider.setValue(original_frame)

    def create_gif(self, output_path, frame_files, fps):
        """Creates an animated GIF from a list of image files using Pillow."""
        try:
            from PIL import Image
        except ImportError:
            errMsg = translate(
                "Assembly", "Pillow (PIL) is not installed. It is required for GIF export."
            )
            QMessageBox.critical(self.form, "Error", errMsg)
            return False

        pil_images = [Image.open(f) for f in frame_files]
        duration_ms = int(1000 / fps)
        pil_images[0].save(
            output_path,
            save_all=True,
            append_images=pil_images[1:],
            optimize=True,
            duration=duration_ms,
            loop=0,  # 0 means loop forever
        )
        return True

    def create_video(self, output_path, frame_files, fps, size):
        """Creates a video file from a list of image files using OpenCV."""
        try:
            import cv2
        except ImportError:
            errMsg = translate(
                "Assembly", "OpenCV is not installed. It is required for video export."
            )
            QMessageBox.critical(self.form, "Error", errMsg)
            return False

        file_extension = Path(output_path).suffix.lower()

        # Select codec based on file type
        if file_extension == ".mp4":
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # or 'avc1'
        elif file_extension == ".avi":
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
        else:
            # Fallback for other types, may not be supported
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        video_writer = cv2.VideoWriter(output_path, fourcc, fps, size)
        if not video_writer.isOpened():
            errMsg = translate("Assembly", "Could not open video writer. Check codecs.")
            QMessageBox.critical(self.form, "Error", errMsg)
            return False

        for filename in frame_files:
            # OpenCV reads images in BGR format by default
            frame = cv2.imread(str(filename))
            video_writer.write(frame)

        video_writer.release()
        return True


if App.GuiUp:
    Gui.addCommand("Assembly_CreateSimulation", CommandCreateSimulation())
