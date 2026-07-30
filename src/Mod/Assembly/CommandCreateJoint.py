# SPDX-License-Identifier: LGPL-2.1-or-later
# /**************************************************************************
#                                                                           *
#    Copyright (c) 2023 Ondsel <development@ondsel.com>                     *
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

import os
import FreeCAD as App

from PySide.QtCore import QT_TRANSLATE_NOOP

if App.GuiUp:
    import FreeCADGui as Gui
    from PySide import QtCore, QtGui, QtWidgets

import JointObject
from JointObject import TaskAssemblyCreateJoint
import UtilsAssembly
import Assembly_rc
from VibeCADNativeTransaction import _OwnedDocumentTransaction

# translate = App.Qt.translate

__title__ = "Assembly Commands to Create Joints"
__author__ = "Ondsel"
__url__ = "https://www.freecad.org"


def noOtherTaskActive():
    # Joint type buttons intentionally remain available while the joint task
    # itself is open so the user can switch the joint type in place.
    if JointObject.activeTask is not None:
        return True

    if UtilsAssembly.isAssemblyCommandActive():
        return True

    # Fixed joints are also supported while an App::Part is active. That path
    # must obey the same task/transaction boundary as an active assembly.
    active_part = UtilsAssembly.activePart()
    if active_part is None or Gui.Control.activeDialog():
        return False
    document = active_part.Document
    return (
        document is not None
        and document.getBookedTransactionID() == 0
        and not document.HasPendingTransaction
    )


def isCreateJointActive():
    return UtilsAssembly.assembly_has_at_least_n_parts(1) and noOtherTaskActive()


def activateJoint(index):
    if JointObject.activeTask:
        dialog = Gui.Control.activeTaskDialog(
            JointObject.activeTask.gui_doc,
        )
        if dialog is None:
            return
        dialog.reject()
        if JointObject.activeTask is not None:
            return
    elif (
        index == 0
        and UtilsAssembly.activePart() is not None
        and (
            not UtilsAssembly.assembly_has_at_least_n_parts(2)
            or not noOtherTaskActive()
        )
    ):
        return
    elif not isCreateJointActive():
        return

    container = (
        UtilsAssembly.activeAssembly()
        or UtilsAssembly.activePart()
    )
    if container is None:
        return

    Gui.addModule("JointObject")  # NOLINT
    Gui.doCommand(
        f"panel = JointObject.TaskAssemblyCreateJoint("
        f"{index}, "
        f"document_name={str(container.Document.Name)!r}, "
        f"container_name={str(container.Name)!r})"
    )
    Gui.doCommandGui("dialog = Gui.Control.showDialog(panel, panel.gui_doc)")
    panel = Gui.doCommandEval("panel")
    dialog = Gui.doCommandEval("dialog")
    if dialog is not None:
        dialog.setAutoCloseOnTransactionChange(True)
        dialog.setAutoCloseOnDeletedDocument(True)
        dialog.setDocumentName(panel.doc.Name)


class CommandCreateJointFixed:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointFixed",
            "MenuText": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointFixed",
                "Fixed Joint",
            ),
            "Accel": "F",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointFixed",
                "<p>1 - If an assembly is active : Creates a joint statically locking two parts together, preventing any movement or rotation</p>"
                "<p>2 - If a part is active: Positions sub-parts by matching selected coordinate systems. The second part selected will move.</p>",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        if UtilsAssembly.activePart() is not None:
            return (
                UtilsAssembly.assembly_has_at_least_n_parts(2)
                and noOtherTaskActive()
            )

        return isCreateJointActive()

    def Activated(self):
        activateJoint(0)


class CommandCreateJointRevolute:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointRevolute",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointRevolute", "Revolute Joint"),
            "Accel": "R",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointRevolute",
                "Creates a revolute joint allowing rotation around a single axis between selected parts",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(1)


class CommandCreateJointCylindrical:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointCylindrical",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointCylindrical", "Cylindrical Joint"),
            "Accel": "C",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointCylindrical",
                "Creates a cylindrical joint that allows rotation around and translation along a single axis between assembled parts",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(2)


class CommandCreateJointSlider:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointSlider",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointSlider", "Slider Joint"),
            "Accel": "S",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointSlider",
                "Creates a slider joint that allows linear movement along a single axis, but restricts rotation between selected parts",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(3)


class CommandCreateJointBall:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointBall",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointBall", "Ball Joint"),
            "Accel": "B",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointBall",
                "Creates a ball joint that connects parts at a point, allowing unrestricted movement as long as the connection points remain in contact",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(4)


class CommandCreateJointDistance:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointDistance",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointDistance", "Distance Joint"),
            "Accel": "D",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointDistance",
                "<p>Creates a distance joint that fixes the distance between the selected objects</p>"
                "<p>Creates one of several different joints based on the selection. "
                "For example, a distance of 0 between a plane and a cylinder creates a tangent joint. A distance of 0 between planes will make them co-planar.</p>",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(5)


class CommandCreateJointParallel:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointParallel",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointParallel", "Parallel Joint"),
            "Accel": "N",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointParallel",
                "Creates a parallel joint that makes the Z-axis of the selected coordinate systems parallel",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(6)


class CommandCreateJointPerpendicular:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointPerpendicular",
            "MenuText": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointPerpendicular", "Perpendicular Joint"
            ),
            "Accel": "M",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointPerpendicular",
                "Creates a perpendicular joint that makes the Z-axis of the selected coordinate systems perpendicular",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(7)


class CommandCreateJointAngle:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointAngle",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointAngle", "Angle Joint"),
            "Accel": "X",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointAngle",
                "Creates an angle joint that fixes the angle between the Z-axis of the selected coordinate systems",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(8)


class CommandCreateJointRackPinion:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointRackPinion",
            "MenuText": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointRackPinion", "Rack and Pinion Joint"
            ),
            "Accel": "Q",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointRackPinion",
                "<p>Creates a rack and pinion joint that links a part with a slider joint to a part with a revolute joint</p>"
                "<p>Select the same coordinate systems as the revolute and slider joints. The pitch radius defines the movement ratio between the rack and the pinion.</p>",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(9)


class CommandCreateJointScrew:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointScrew",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointScrew", "Screw Joint"),
            "Accel": "W",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointScrew",
                "<p>Creates a screw joint that links a part with a slider joint to a part with a revolute joint</p>"
                "<p>Select the same coordinate systems as the revolute and slider joints. The pitch radius defines the movement ratio between the rotating screw and the sliding part.</p>",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(10)


class CommandCreateJointGears:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointGears",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointGears", "Gears Joint"),
            "Accel": "T",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointGears",
                "<p>Creates a gears joint that links 2 rotating gears together. They will have inverse rotation direction.</p>"
                "<p>Select the same coordinate systems as the revolute joints.</p>",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(11)


class CommandCreateJointBelt:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_CreateJointPulleys",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointBelt", "Belt Joint"),
            "Accel": "L",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointBelt",
                "<p>Creates a belt joint that links 2 rotating objects together. They will have the same rotation direction.</p>"
                "<p>Select the same coordinate systems as the revolute joints.</p>",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()

    def Activated(self):
        activateJoint(12)


class CommandGroupGearBelt:
    def GetCommands(self):
        return ("Assembly_CreateJointGears", "Assembly_CreateJointBelt")

    def GetResources(self):
        """Set icon, menu and tooltip."""
        return {
            "Pixmap": "Assembly_CreateJointGears",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateJointGearBelt", "Gears/Belt Joint"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateJointGearBelt",
                "<p>Creates a gears or belt joint that links 2 rotating gears together</p>"
                "<p>Select the same coordinate systems as the revolute joints.</p>",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return isCreateJointActive()


def _assemblyOwnsGroundingComponent(assembly, component):
    if (
        assembly is None
        or component is None
        or component.Document is not assembly.Document
        or not UtilsAssembly.isTimelineOperationActive(assembly)
        or not UtilsAssembly.isTimelineOperationActive(component)
    ):
        return False
    if assembly.hasObject(component, True):
        return True
    if component.TypeId != "App::LinkElement":
        return False
    link_group = UtilsAssembly.getLinkGroup(component)
    return (
        link_group is not None
        and link_group.Document is assembly.Document
        and assembly.hasObject(link_group, True)
        and UtilsAssembly.isTimelineOperationActive(link_group)
    )


def createGroundedJoint(obj, assembly=None, record=True):
    if assembly is None:
        assembly = UtilsAssembly.activeAssembly()
    if (
        assembly is None
        or obj is None
        or obj.Document is not assembly.Document
        or assembly.Document.getObject(obj.Name) is not obj
        or assembly.Document.getObject(assembly.Name) is not assembly
        or not _assemblyOwnsGroundingComponent(assembly, obj)
    ):
        return

    document = assembly.Document
    if record:
        Gui.addModule("CommandCreateJoint")
        Gui.addModule("JointObject")
        document_expression = (
            f"App.getDocument({str(document.Name)!r})"
        )
        ground = Gui.runDocumentObjectCommand(
            document,
            "CommandCreateJoint.createGroundedJointFeature("
            f"{document_expression}.getObject({str(obj.Name)!r}), "
            f"{document_expression}.getObject({str(assembly.Name)!r}))",
            "App::FeaturePython",
        )
        Gui.doCommandGui(
            "JointObject.ViewProviderGroundedJoint("
            f"Gui.getDocument({str(document.Name)!r}).getObject("
            f"{str(ground.Name)!r}))"
        )
    else:
        # Insert Component creates provisional links directly and records their
        # durable replay trace only on Accept. Ground its provisional first
        # component the same way, otherwise the accepted trace contains both
        # this live creation and a second reconstructed GroundedJoint.
        ground = createGroundedJointFeature(obj, assembly)
        JointObject.ViewProviderGroundedJoint(ground.ViewObject)

    document.recompute()
    return ground


def createGroundedJointFeature(obj, assembly):
    """Create and return the exact grounded-joint model object."""
    if (
        obj is None
        or assembly is None
        or obj.Document is not assembly.Document
        or assembly.Document.getObject(obj.Name) is not obj
        or assembly.Document.getObject(assembly.Name) is not assembly
        or not _assemblyOwnsGroundingComponent(assembly, obj)
    ):
        raise RuntimeError(
            "Grounded-joint inputs must be exact live objects in one document"
        )
    joint_group = UtilsAssembly.getJointGroup(assembly)
    if joint_group is None or joint_group.Document is not assembly.Document:
        raise RuntimeError("The assembly has no live joint group")
    ground = joint_group.newObject(
        "App::FeaturePython",
        "GroundedJoint",
    )
    JointObject.GroundedJoint(ground, obj)
    return ground


def _selectedGroundingComponents(assembly):
    components = []
    seen = set()
    for selection in Gui.Selection.getSelectionEx("*", 0):
        try:
            selected = selection.Object
            if selected is None:
                continue

            candidates = list(selection.SubElementNames)
            if not candidates:
                if hasattr(selected, "ObjectToGround"):
                    component = selected.ObjectToGround
                    if (
                        selected.Document is assembly.Document
                        and UtilsAssembly.findOwningAssembly(
                            selected,
                            include_inactive=True,
                        )
                        is assembly
                        and UtilsAssembly.isTimelineOperationActive(selected)
                        and UtilsAssembly.isTimelineOperationActive(component)
                        and _assemblyOwnsGroundingComponent(
                            assembly,
                            component,
                        )
                        and component.Name not in seen
                    ):
                        seen.add(component.Name)
                        components.append(component)
                    continue
                if (
                    selected in assembly.Group
                    and selected.isDerivedFrom("App::Link")
                    and UtilsAssembly.isTimelineOperationActive(selected)
                ):
                    if selected.Name not in seen:
                        seen.add(selected.Name)
                        components.append(selected)
                    continue

            for sub_name in candidates:
                resolved = selected.resolveSubElement(sub_name)
                if resolved and hasattr(resolved[0], "ObjectToGround"):
                    component = resolved[0].ObjectToGround
                else:
                    component, _new_sub = UtilsAssembly.getComponentReference(
                        assembly,
                        selected,
                        sub_name,
                    )
                if (
                    component is None
                    or not _assemblyOwnsGroundingComponent(
                        assembly,
                        component,
                    )
                    or component.Name in seen
                ):
                    continue
                seen.add(component.Name)
                components.append(component)
        except (AttributeError, RuntimeError, TypeError):
            # Tree and 3D selections can become stale while the document
            # updates. A stale entry is not a valid grounding target, and
            # command activation itself must remain safe.
            continue
    return components


class CommandToggleGrounded:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_ToggleGrounded",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_ToggleGrounded", "Toggle Grounded"),
            "Accel": "G",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_ToggleGrounded",
                "<p>Toggles the grounding of a part.</p>"
                "<p>Grounding a part permanently locks its position in the assembly, preventing any movement or rotation.",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        if (
            not UtilsAssembly.isAssemblyCommandActive()
            or not UtilsAssembly.assembly_has_at_least_n_parts(1)
        ):
            return False
        return bool(
            _selectedGroundingComponents(
                UtilsAssembly.activeAssembly(),
            )
        )

    def Activated(self):
        if not self.IsActive():
            return

        assembly = UtilsAssembly.activeAssembly()
        if not assembly:
            return

        joint_group = UtilsAssembly.getJointGroup(assembly)
        components = _selectedGroundingComponents(assembly)
        if joint_group is None or not components:
            return

        document = assembly.Document
        transaction = _OwnedDocumentTransaction(
            document,
            "Toggle grounded",
        )
        try:
            for component in components:
                grounded_joint = next(
                    (
                        joint
                        for joint in joint_group.Group
                        if hasattr(joint, "ObjectToGround")
                        and UtilsAssembly.isTimelineOperationActive(joint)
                        and joint.ObjectToGround == component
                    ),
                    None,
                )
                if grounded_joint is not None:
                    Gui.doCommand(
                        f"document = App.getDocument({str(document.Name)!r})\n"
                        f"document.removeObject({str(grounded_joint.Name)!r})\n"
                        "document.recompute()\n"
                    )
                elif createGroundedJoint(component, assembly) is None:
                    raise RuntimeError(
                        f"Could not ground component {component.Label}"
                    )

            document.recompute()
            if not assembly.isValid() or not joint_group.isValid():
                raise RuntimeError("Grounding produced an invalid assembly")
            if any(
                hasattr(joint, "ObjectToGround")
                and (
                    joint.ObjectToGround is None
                    or joint.ObjectToGround.Document is not document
                )
                for joint in joint_group.Group
            ):
                raise RuntimeError("Grounding produced an invalid component link")
        except Exception:
            transaction.abort()
            raise
        transaction.commit()


if App.GuiUp:
    Gui.addCommand("Assembly_ToggleGrounded", CommandToggleGrounded())
    Gui.addCommand("Assembly_CreateJointFixed", CommandCreateJointFixed())
    Gui.addCommand("Assembly_CreateJointRevolute", CommandCreateJointRevolute())
    Gui.addCommand("Assembly_CreateJointCylindrical", CommandCreateJointCylindrical())
    Gui.addCommand("Assembly_CreateJointSlider", CommandCreateJointSlider())
    Gui.addCommand("Assembly_CreateJointBall", CommandCreateJointBall())
    Gui.addCommand("Assembly_CreateJointDistance", CommandCreateJointDistance())
    Gui.addCommand("Assembly_CreateJointParallel", CommandCreateJointParallel())
    Gui.addCommand("Assembly_CreateJointPerpendicular", CommandCreateJointPerpendicular())
    Gui.addCommand("Assembly_CreateJointAngle", CommandCreateJointAngle())
    Gui.addCommand("Assembly_CreateJointRackPinion", CommandCreateJointRackPinion())
    Gui.addCommand("Assembly_CreateJointScrew", CommandCreateJointScrew())
    Gui.addCommand("Assembly_CreateJointGears", CommandCreateJointGears())
    Gui.addCommand("Assembly_CreateJointBelt", CommandCreateJointBelt())
    Gui.addCommand("Assembly_CreateJointGearBelt", CommandGroupGearBelt())
