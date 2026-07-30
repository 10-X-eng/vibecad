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

import re
import os
import FreeCAD as App

from pivy import coin
from Part import LineSegment, Compound

from PySide.QtCore import QT_TRANSLATE_NOOP

if App.GuiUp:
    import FreeCADGui as Gui
    from PySide import QtCore, QtGui, QtWidgets
    from PySide.QtWidgets import QPushButton, QMenu

import UtilsAssembly
import Preferences

__title__ = "Assembly Command Create Exploded View"
__author__ = "Ondsel"
__url__ = "https://www.freecad.org"


class CommandCreateView:
    def __init__(self):
        pass

    def GetResources(self):
        return {
            "Pixmap": "Assembly_ExplodedView",
            "MenuText": QT_TRANSLATE_NOOP("Assembly_CreateView", "Exploded View"),
            "Accel": "E",
            "ToolTip": QT_TRANSLATE_NOOP(
                "Assembly_CreateView",
                "Creates an exploded view of the current assembly",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        return (
            UtilsAssembly.isAssemblyCommandActive()
            and UtilsAssembly.assembly_has_at_least_n_parts(2)
        )

    def Activated(self):
        if not self.IsActive():
            return
        assembly = UtilsAssembly.activeAssembly()
        if not assembly:
            return

        Gui.addModule("CommandCreateView")  # NOLINT
        Gui.doCommand(
            "panel = CommandCreateView.TaskAssemblyCreateView("
            f"document_name={str(assembly.Document.Name)!r}, "
            f"assembly_name={str(assembly.Name)!r})"
        )
        self.panel = Gui.doCommandEval("panel")
        Gui.doCommandGui("dialog = Gui.Control.showDialog(panel, panel.gui_doc)")
        dialog = Gui.doCommandEval("dialog")
        if dialog is not None:
            dialog.setAutoCloseOnDeletedDocument(True)
            dialog.setDocumentName(assembly.Document.Name)


######### Exploded View Object ###########
class ExplodedView:
    def __init__(self, expView):
        expView.Proxy = self
        expView.addExtension("App::GroupExtensionPython")

        self.stepsChangedCallback = None
        self.initialPlcs = None
        self._last_applied_placements = []
        UtilsAssembly.markTimelineOperationEditor(
            expView,
            "Assembly_EditHistoryOperation",
        )

    def onDocumentRestored(self, expView):
        self.initialPlcs = None
        self._last_applied_placements = []
        self.migrationScript(expView)
        UtilsAssembly.markTimelineOperationEditor(
            expView,
            "Assembly_EditHistoryOperation",
        )
        for move in expView.Group:
            UtilsAssembly.markTimelineResource(move, expView)

    def migrationScript(self, expView):
        if hasattr(expView, "Moves"):
            expView.addExtension("App::GroupExtensionPython")
            expView.Group = expView.Moves
            expView.removeProperty("Moves")

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onChanged(self, viewObj, prop):
        if prop != "Group":
            return
        for move in viewObj.Group:
            UtilsAssembly.markTimelineResource(move, viewObj)
        if (
            hasattr(self, "stepsChangedCallback")
            and self.stepsChangedCallback is not None
        ):
            self.stepsChangedCallback()

    def setMovesChangedCallback(self, callback):
        self.stepsChangedCallback = callback

    def execute(self, fp):
        """Do something when doing a recomputation, this method is mandatory"""
        # App.Console.PrintMessage("Recompute Python Box feature\n")
        pass

    def _prepareApplicationBaseline(self, assembly):
        """Undo only this proxy's exact previous temporary application.

        Exploded moves are transforms relative to the assembled placement.
        Applying the same view twice must therefore replace its previous
        temporary result rather than compound another transform. If another
        operation changed a part after our previous application, its current
        placement is retained as the new baseline.
        """

        for (
            part,
            object_name,
            object_id,
            baseline,
            applied,
        ) in self._last_applied_placements:
            document = getattr(part, "Document", None)
            if (
                UtilsAssembly._document_is_open(document)
                and document.getObject(object_name) is part
                and int(part.ID) == object_id
                and part.Placement == applied
            ):
                part.Placement = App.Placement(baseline)
                part.purgeTouched()

        self._last_applied_placements = []
        return [
            (part, App.Placement(part.Placement))
            for part in UtilsAssembly.getMovablePartsWithin(assembly)
            if hasattr(part, "Placement")
        ]

    def _rememberAppliedPlacements(self, baselines):
        for part, baseline in baselines:
            document = getattr(part, "Document", None)
            if (
                not UtilsAssembly._document_is_open(document)
                or document.getObject(part.Name) is not part
            ):
                continue
            applied = App.Placement(part.Placement)
            if applied == baseline:
                continue
            self._last_applied_placements.append(
                (
                    part,
                    str(part.Name),
                    int(part.ID),
                    baseline,
                    applied,
                )
            )

    def applyMoves(self, viewObj, com=None, size=None):
        positions = []  # [[p1start, p1end], [p2start, p2end], ...]
        assembly = self.getAssembly(viewObj)
        if assembly is None:
            return positions
        baselines = self._prepareApplicationBaseline(assembly)
        if not UtilsAssembly.isTimelineOperationActive(viewObj):
            return positions
        if com is None:
            com, size = UtilsAssembly.getComAndSize(assembly)
        try:
            for move in viewObj.Group:
                if not UtilsAssembly.isTimelineOperationActive(move):
                    continue
                positions = positions + move.Proxy.applyStep(
                    move,
                    com,
                    size,
                )
        finally:
            # Preserve enough exact transient state to undo even a partially
            # applied view if a later move raises.
            self._rememberAppliedPlacements(baselines)
        return positions

    def explodeTemporarily(self, viewObj):
        self.initialPlcs = (
            UtilsAssembly._saveExactAssemblyPartPlacements(
                self.getAssembly(viewObj)
            )
        )
        self.applyMoves(viewObj)
        for move in viewObj.Group:
            if UtilsAssembly.isTimelineOperationActive(move):
                move.Visibility = True

    def getAssembly(self, viewObj):
        return UtilsAssembly.findOwningAssembly(viewObj)

    def _createSafeLine(self, start, end):
        """Creates a LineSegment shape only if points are not coincident."""
        from Part import Precision

        if (start - end).Length > Precision.confusion():
            return LineSegment(start, end).toShape()
        return None

    def saveAssemblyAndExplode(self, viewObj):
        self.initialPlcs = (
            UtilsAssembly._saveExactAssemblyPartPlacements(
                self.getAssembly(viewObj)
            )
        )

        self.positions = self.applyMoves(viewObj)

        lines = []

        for startPos, endPos in self.positions:
            line = self._createSafeLine(startPos, endPos)
            if line:
                lines.append(line)
        if lines:
            return Compound(lines)

        return None

    def restoreAssembly(self, viewObj):
        if self.initialPlcs is None:
            return

        UtilsAssembly._restoreExactAssemblyPartPlacements(
            self.getAssembly(viewObj),
            self.initialPlcs,
            require_complete=False,
        )

        for move in viewObj.Group:
            move.Visibility = False

    def _calculateExplodedPlacements(self, viewObj):
        """
        Internal helper to calculate final placements for an exploded view without
        applying them.
        Returns:
            - A dictionary mapping {part_object: final_placement}.
            - A list of [start_pos, end_pos] for explosion lines.
        """
        final_placements = {}
        line_positions = []
        factor = 1

        assembly = self.getAssembly(viewObj)
        # Get a snapshot of the assembly's current, un-exploded state
        calculated_placements = UtilsAssembly.saveAssemblyPartsPlacements(assembly)

        com, size = UtilsAssembly.getComAndSize(assembly)

        for move in viewObj.Group:
            if not UtilsAssembly.isTimelineOperationActive(move):
                continue
            if not UtilsAssembly.isRefValid(move.References, 1):
                continue

            if move.MoveType == "Radial":
                distance = move.MovementTransform.Base.Length
                factor = 4 * distance / size

            subs = move.References[1]
            for sub in subs:
                ref = [move.References[0], [sub]]
                obj = UtilsAssembly.getObject(ref)
                if not obj or not hasattr(obj, "Placement"):
                    continue

                # Use the placement from our calculation dictionary, which tracks
                # changes from previous steps.
                current_placement = calculated_placements.get(obj.Name, obj.Placement)

                # The part's shape is already placed, so its BBox.Center is the
                # correct global starting position for the explosion line.
                start_pos = obj.Shape.BoundBox.Center

                if move.MoveType == "Radial":
                    obj_com, obj_size = UtilsAssembly.getComAndSize(obj)
                    init_vec = obj_com - com
                    new_base = current_placement.Base + init_vec * factor
                    new_placement = App.Placement(new_base, current_placement.Rotation)
                else:
                    new_placement = move.MovementTransform * current_placement

                # Store the newly calculated placement for this part
                calculated_placements[obj.Name] = new_placement
                final_placements[obj] = new_placement

                # To find the end_pos, calculate the transformation that takes the part
                # from its current_placement to its new_placement...
                delta_transform = new_placement * current_placement.inverse()
                # ...and apply that same transformation to the start_pos.
                end_pos = delta_transform.multVec(start_pos)
                line_positions.append([start_pos, end_pos])

        return final_placements, line_positions

    def getExplodedShape(self, viewObj):
        """
        Generates a compound shape of the exploded assembly in memory
        without modifying the document. Returns a single Part.Compound.
        """
        final_placements, line_positions = self._calculateExplodedPlacements(viewObj)

        exploded_shapes = []

        # We need to include ALL parts of the assembly, not just the moved ones.
        assembly = self.getAssembly(viewObj)
        all_parts = UtilsAssembly.getMovablePartsWithin(assembly, True)
        visible_parts = [
            part for part in all_parts if hasattr(part, "Visibility") and part.Visibility
        ]

        for part in visible_parts:
            # Get the shape. It's crucial to use .copy()
            shape_copy = part.Shape.copy()

            # If the part was moved, use its calculated final placement.
            # Otherwise, use its current placement from the document.
            final_plc = final_placements.get(part, part.Placement)

            shape_copy.Placement = final_plc
            exploded_shapes.append(shape_copy)

        # Add shapes for the explosion lines
        for start_pos, end_pos in line_positions:
            line = self._createSafeLine(start_pos, end_pos)
            if line:
                exploded_shapes.append(line)

        if exploded_shapes:
            return Compound(exploded_shapes)

        return None


class ViewProviderExplodedView:
    def __init__(self, vobj):
        """Set this object to the proxy object of the actual view provider"""
        vobj.Proxy = self

    def attach(self, vobj):
        """Setup the scene sub-graph of the view provider, this method is mandatory"""
        self.app_obj = vobj.Object

        self.display_mode = coin.SoType.fromName("SoFCSelection").createInstance()

        vobj.addDisplayMode(self.display_mode, "Wireframe")

    def updateData(self, joint, prop):
        """If a property of the handled feature has changed we have the chance to handle this here"""
        # joint is the handled feature, prop is the name of the property that has changed
        pass

    def getDisplayModes(self, obj):
        """Return a list of display modes."""
        return ["Wireframe"]

    def getDefaultDisplayMode(self):
        """Return the name of the default display mode. It must be defined in getDisplayModes."""
        return "Wireframe"

    def onChanged(self, vp, prop):
        """Here we can do something when a single property got changed"""
        # App.Console.PrintMessage("Change property: " + str(prop) + "\n")
        pass

    def getIcon(self):
        return ":/icons/Assembly_ExplodedView.svg"

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

    def doubleClicked(self, vobj):
        operation = vobj.Object
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

        panel = TaskAssemblyCreateView(
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


######### Exploded View Move #########
ExplodedViewStepTypes = [
    "Normal",
    "Radial",
]


class ExplodedViewStep:
    def __init__(self, evStep, type_index=0):
        evStep.Proxy = self

        self.createProperties(evStep)

        evStep.MoveType = ExplodedViewStepTypes  # sets the list
        evStep.MoveType = ExplodedViewStepTypes[type_index]  # set the initial value

    def onDocumentRestored(self, evStep):
        self.createProperties(evStep)
        exploded_view = self.getExplodedView(evStep)
        if exploded_view is not None:
            UtilsAssembly.markTimelineResource(evStep, exploded_view)

    def createProperties(self, evStep):
        self.migrationScript(evStep)

        if not hasattr(evStep, "References"):
            evStep.addProperty(
                "App::PropertyXLinkSubHidden",
                "References",
                "Exploded Move",
                QT_TRANSLATE_NOOP("App::Property", "The objects moved by the move"),
                locked=True,
            )

        if not hasattr(evStep, "MovementTransform"):
            evStep.addProperty(
                "App::PropertyPlacement",
                "MovementTransform",
                "Exploded Move",
                QT_TRANSLATE_NOOP(
                    "App::Property",
                    "This is the movement of the move. The end placement is the result of the start placement * this placement.",
                ),
                locked=True,
            )

        if not hasattr(evStep, "MoveType"):
            evStep.addProperty(
                "App::PropertyEnumeration",
                "MoveType",
                "Exploded Move",
                QT_TRANSLATE_NOOP("App::Property", "The type of the move"),
                locked=True,
            )

    def migrationScript(self, evStep):
        if hasattr(evStep, "Parts"):
            objNames = evStep.ObjNames
            parts = evStep.Parts

            evStep.removeProperty("ObjNames")
            evStep.removeProperty("Parts")

            evStep.addProperty(
                "App::PropertyXLinkSubHidden",
                "References",
                "Exploded Move",
                QT_TRANSLATE_NOOP("App::Property", "The objects moved by the move"),
                locked=True,
            )

            rootObj = None
            paths = []

            for objName, part in zip(objNames, parts):
                # now we need to get the 'selection-root-obj' and the global path
                obj = UtilsAssembly.getObjectInPart(objName, part)
                rootObj, path = UtilsAssembly.getRootPath(obj, part)
                if rootObj is None:
                    continue
                paths.append(path)
                # Note: all the parts should have the same rootObj.

            evStep.References = [rootObj, paths]

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def onChanged(self, evStep, prop):
        """Do something when a property has changed"""
        pass

    def execute(self, fp):
        """Do something when doing a recomputation, this method is mandatory"""
        # App.Console.PrintMessage("Recompute Python Box feature\n")
        pass

    @staticmethod
    def getExplodedView(evStep):
        for obj in evStep.InList:
            proxy = getattr(obj, "Proxy", None)
            if proxy is not None and hasattr(proxy, "setMovesChangedCallback"):
                return obj
        return None

    def applyStep(self, move, com=App.Vector(), size=100):
        exploded_view = self.getExplodedView(move)
        assembly = (
            exploded_view.Proxy.getAssembly(exploded_view)
            if exploded_view is not None
            else None
        )
        if (
            not UtilsAssembly.isTimelineOperationActive(move)
            or exploded_view is None
            or not UtilsAssembly.isTimelineOperationActive(
                exploded_view
            )
            or assembly is None
            or not UtilsAssembly.isRefValid(move.References, 1)
        ):
            return []
        root_object = move.References[0]
        if (
            root_object.Document is not assembly.Document
            or assembly.Document.getObject(root_object.Name)
            is not root_object
            or not UtilsAssembly.isTimelineOperationActive(
                root_object
            )
        ):
            return []

        positions = []
        if move.MoveType == "Radial":
            distance = move.MovementTransform.Base.Length
            factor = 4 * distance / size

        subs = move.References[1]
        for sub in subs:
            ref = [move.References[0], [sub]]
            component, _relative_sub = (
                UtilsAssembly.getComponentReference(
                    assembly,
                    root_object,
                    sub,
                )
            )
            obj = UtilsAssembly.getObject(ref)
            if (
                component is None
                or not UtilsAssembly.isTimelineOperationActive(
                    component
                )
                or obj is None
                or not UtilsAssembly.isTimelineOperationActive(obj)
            ):
                continue
            if obj.Document is not assembly.Document:
                obj = component
            if not hasattr(obj, "Placement"):
                continue

            if move.ViewObject:
                startPos = UtilsAssembly.getCenterOfBoundingBox([obj], [ref])

            if move.MoveType == "Radial":
                objCom, objSize = UtilsAssembly.getComAndSize(obj)
                init_vec = objCom - com
                obj.Placement.Base = obj.Placement.Base + init_vec * factor
            else:
                obj.Placement = move.MovementTransform * obj.Placement

            if move.ViewObject:
                endPos = UtilsAssembly.getCenterOfBoundingBox([obj], [ref])
                positions.append([startPos, endPos])
            obj.purgeTouched()

        view_provider = move.ViewObject
        view_proxy = view_provider.Proxy if view_provider else None
        if view_proxy and hasattr(view_proxy, "redrawLines"):
            view_proxy.redrawLines(move, positions)

        return positions


class ViewProviderExplodedViewStep:
    def __init__(self, vobj):
        """Set this object to the proxy object of the actual view provider"""
        vobj.Proxy = self

    def attach(self, vobj):
        """Setup the scene sub-graph of the view provider, this method is mandatory"""
        self.app_obj = vobj.Object

        pref = Preferences.preferences()

        self.line_thickness = pref.GetInt("StepLineThickness", 3)

        param_step_line_color = pref.GetUnsigned("StepLineColor", 0xCC333300)
        self.so_color = coin.SoBaseColor()
        self.so_color.rgb.setValue(UtilsAssembly.color_from_unsigned(param_step_line_color))

        self.draw_style = coin.SoDrawStyle()
        self.draw_style.style = coin.SoDrawStyle.LINES
        self.draw_style.lineWidth = self.line_thickness
        self.draw_style.linePattern = 0xF0F0  # Dashed line pattern

        # Create a separator to hold all dashed lines
        self.lineSetGroup = coin.SoSeparator()

        self.display_mode = coin.SoType.fromName("SoFCSelection").createInstance()
        self.display_mode.addChild(self.lineSetGroup)  # Add the group to the display mode
        vobj.addDisplayMode(self.display_mode, "Wireframe")

    def updateData(self, stepObj, prop):
        """If a property of the handled feature has changed we have the chance to handle this here"""
        # stepObj is the handled feature, prop is the name of the property that has changed
        pass

    def redrawLines(self, stepObj, positions):
        # Clear existing lines
        self.lineSetGroup.removeAllChildren()

        for startPos, endPos in positions:
            # Create the line
            line = coin.SoLineSet()
            line.numVertices.setValue(2)
            coords = coin.SoCoordinate3()
            coords.point.setValues(0, [startPos, endPos])

            # Create separator for this line to apply the style
            line_sep = coin.SoSeparator()
            line_sep.addChild(self.draw_style)
            line_sep.addChild(self.so_color)
            line_sep.addChild(coords)
            line_sep.addChild(line)

            # Add to the group
            self.lineSetGroup.addChild(line_sep)

    def getDisplayModes(self, obj):
        """Return a list of display modes."""
        modes = []
        modes.append("Wireframe")
        return modes

    def getDefaultDisplayMode(self):
        """Return the name of the default display mode. It must be defined in getDisplayModes."""
        return "Wireframe"

    def onChanged(self, vp, prop):
        """Here we can do something when a single property got changed"""
        # App.Console.PrintMessage("Change property: " + str(prop) + "\n")
        pass

    def getIcon(self):
        return ":/icons/button_add_all.svg"

    def dumps(self):
        """When saving the document this object gets stored using Python's json module.\
                Since we have some un-serializable parts here -- the Coin stuff -- we must define this method\
                to return a tuple of all serializable objects or None."""
        return None

    def loads(self, state):
        """When restoring the serialized object from document we have the chance to set some internals here.\
                Since no data were serialized nothing needs to be done here."""
        return None


def createExplodedViewFeature(document, assembly):
    """Create and return one exact exploded-view operation."""
    if (
        document is None
        or assembly is None
        or assembly.Document is not document
        or document.getObject(assembly.Name) is not assembly
        or not UtilsAssembly.isTimelineOperationActive(assembly)
    ):
        raise RuntimeError(
            "The exploded-view assembly is not live in its document"
        )
    view_group = UtilsAssembly.getViewGroup(assembly)
    if view_group is None or view_group.Document is not document:
        raise RuntimeError("The assembly has no live exploded-view group")
    view_object = view_group.newObject(
        "App::FeaturePython",
        "Exploded View",
    )
    ExplodedView(view_object)
    return view_object


def createExplodedViewStepFeature(document, assembly, move_type_index):
    """Create and return one exact exploded-view movement resource."""
    if (
        document is None
        or assembly is None
        or assembly.Document is not document
        or document.getObject(assembly.Name) is not assembly
        or not UtilsAssembly.isTimelineOperationActive(assembly)
    ):
        raise RuntimeError(
            "The exploded-view step assembly is not live in its document"
        )
    step = assembly.newObject("App::FeaturePython", "Move")
    ExplodedViewStep(step, int(move_type_index))
    return step


class ExplodedViewSelGate:
    def __init__(self, assembly, viewObj):
        self.assembly = assembly
        self.viewObj = viewObj

    def allow(self, doc, obj, sub):
        try:
            exact_context = (
                obj is not None
                and obj.Document is self.assembly.Document
                and obj.Document.getObject(obj.Name) is obj
                and UtilsAssembly.isTimelineOperationActive(
                    self.assembly
                )
                and UtilsAssembly.isTimelineOperationActive(
                    self.viewObj
                )
                and UtilsAssembly.findOwningAssembly(
                    self.viewObj,
                    include_inactive=True,
                )
                is self.assembly
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            exact_context = False
        if not exact_context:
            return False
        comp, new_sub = UtilsAssembly.getComponentReference(self.assembly, obj, sub)
        if UtilsAssembly.isMovableAssemblyComponent(
            self.assembly,
            comp,
        ):
            # Objects within the assembly.
            return True

        if (
            obj in self.viewObj.Group
            and UtilsAssembly.isTimelineOperationActive(obj)
        ):
            # Enable selection of steps object
            return True

        return False


######### Create Exploded View Task ###########
_TaskAssemblyCreateViewBase = QtCore.QObject if App.GuiUp else object


class TaskAssemblyCreateView(_TaskAssemblyCreateViewBase):
    def __init__(
        self,
        viewObj=None,
        document_name=None,
        existing_transaction_id=0,
        assembly_name=None,
    ):
        super().__init__()

        if viewObj is not None:
            operation_document = getattr(viewObj, "Document", None)
            if (
                not UtilsAssembly._document_is_open(operation_document)
                or operation_document.getObject(viewObj.Name) is not viewObj
                or not UtilsAssembly.isTimelineOperationActive(viewObj)
            ):
                raise RuntimeError(
                    "The exploded-view operation is not active and live"
                )
            self.assembly = viewObj.Proxy.getAssembly(viewObj)
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
                    "The exploded-view task lost its exact active assembly"
                )
        else:
            self.assembly = UtilsAssembly.activeAssembly()
        if self.assembly is None:
            raise RuntimeError("An active assembly is required for an exploded view")
        if not UtilsAssembly.isTimelineOperationActive(self.assembly):
            raise RuntimeError(
                "The exploded-view assembly is not active in History"
            )

        self.doc = self.assembly.Document
        if document_name is not None and self.doc.Name != document_name:
            raise RuntimeError("The exploded-view task document changed before launch")
        if viewObj is not None and viewObj.Document is not self.doc:
            raise RuntimeError("The exploded view does not belong to the assembly")
        self.document_uid = str(
            getattr(self.doc, "Uid", "") or ""
        )
        self.assembly_identity = (
            str(self.assembly.Name),
            int(self.assembly.ID),
            self.assembly,
        )

        self.gui_doc = Gui.getDocument(self.doc.Name)
        if self.gui_doc is None:
            raise RuntimeError("The exploded-view task has no GUI document")
        self.view = self.gui_doc.activeView()
        if self.view is None:
            raise RuntimeError("The exploded-view task has no active 3D view")

        self.transaction = UtilsAssembly._TaskTransactionOwner(
            self.doc,
            "Edit Exploded View" if viewObj else "Create Exploded View",
            existing_transaction_id,
        )

        self.form = Gui.PySideUic.loadUi(":/panels/TaskAssemblyCreateView.ui")
        self.form.stepList.installEventFilter(self)
        self.form.stepList.itemClicked.connect(self.onItemClicked)

        self.enable_movement_before_task = bool(
            self.assembly.ViewObject.EnableMovement
        )
        self.dragger_visibility_before_task = bool(
            self.assembly.ViewObject.DraggerVisibility
        )
        self.com, self.size = UtilsAssembly.getComAndSize(self.assembly)
        self.asmDragger = self.assembly.ViewObject.getDragger()
        self.cbFin = self.view.addDraggerCallback(
            self.asmDragger, "addFinishCallback", self.draggerFinished
        )
        self.cbMov = self.view.addDraggerCallback(
            self.asmDragger, "addMotionCallback", self.draggerMoved
        )

        Gui.Selection.clearSelection(self.doc.Name)

        self.form.btnAlignDragger.setMenu(QMenu(self.form.btnAlignDragger))
        actionAlignTo = self.form.btnAlignDragger.menu().addAction("Align to...")
        actionAlignToCenter = self.form.btnAlignDragger.menu().addAction("Align to part center")
        actionAlignToOrigin = self.form.btnAlignDragger.menu().addAction("Align to part origin")

        # Connect actions to the respective functions
        actionAlignTo.triggered.connect(self.onAlignTo)
        actionAlignToCenter.triggered.connect(self.onAlignToCenter)
        actionAlignToOrigin.triggered.connect(self.onAlignToPartOrigin)

        self.form.btnAlignDragger.setEnabled(False)
        self.form.btnAlignDragger.setText("Select a part")
        self.form.btnRadialExplosion.clicked.connect(self.onRadialClicked)

        pref = Preferences.preferences()
        self.form.CheckBox_PartsAsSingleSolid.setChecked(pref.GetBool("PartsAsSingleSolid", True))

        self.initialPlcs = (
            UtilsAssembly._saveExactAssemblyPartPlacements(
                self.assembly
            )
        )

        self.creating_timeline_operation = viewObj is None
        if viewObj:
            self.viewObj = viewObj
            self.timeline_resource_edit = (
                UtilsAssembly.stageTimelineResourceGroupEdit(
                    self.viewObj
                )
            )
            for move in self.viewObj.Group:
                move.Visibility = True
            self.onMovesChanged()

        else:
            self.timeline_resource_edit = None
            self.createExplodedViewObject()
        self.view_identity = (
            str(self.viewObj.Name),
            int(self.viewObj.ID),
            self.viewObj,
        )

        Gui.Selection.addSelectionGate(
            ExplodedViewSelGate(self.assembly, self.viewObj), Gui.Selection.ResolveMode.NoResolve
        )
        Gui.Selection.addObserver(self, Gui.Selection.ResolveMode.NoResolve)

        self.viewObj.Proxy.setMovesChangedCallback(self.onMovesChanged)
        self.callbackMove = self.view.addEventCallback("SoLocation2Event", self.moveMouse)
        self.callbackClick = self.view.addEventCallback("SoMouseButtonEvent", self.clickMouse)
        self.callbackKey = self.view.addEventCallback("SoKeyboardEvent", self.KeyboardEvent)

        self.selectingFeature = False
        self.form.LabelAlignDragger.setVisible(False)
        self.presel_ref = None

        self.blockSetDragger = False
        self.blockDraggerMove = True
        self.currentStep = None
        self.radialExplosion = False

        self.viewObj.purgeTouched()
        # Change transient movement behavior only after construction has
        # completed, so a failed panel launch cannot strand the assembly.
        self.assembly.ViewObject.EnableMovement = False

    def _ownsLiveTaskContext(self):
        assembly_name, assembly_id, exact_assembly = (
            self.assembly_identity
        )
        view_name, view_id, exact_view = self.view_identity
        try:
            return (
                self.transaction.owns_current()
                and UtilsAssembly._document_is_open(self.doc)
                and str(getattr(self.doc, "Uid", "") or "")
                == self.document_uid
                and self.assembly is exact_assembly
                and self.doc.getObject(assembly_name)
                is self.assembly
                and int(self.assembly.ID) == assembly_id
                and UtilsAssembly.isTimelineOperationActive(
                    self.assembly
                )
                and self.viewObj is exact_view
                and self.doc.getObject(view_name) is self.viewObj
                and int(self.viewObj.ID) == view_id
                and UtilsAssembly.isTimelineOperationActive(
                    self.viewObj
                )
                and UtilsAssembly.findOwningAssembly(
                    self.viewObj,
                    include_inactive=True,
                )
                is self.assembly
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return False

    def accept(self):
        if not self._ownsLiveTaskContext():
            App.Console.PrintError(
                "Could not finalize the exploded view: "
                "the task no longer owns its exact Assembly objects and "
                "document transaction\n"
            )
            return False
        try:
            UtilsAssembly._restoreExactAssemblyPartPlacements(
                self.assembly,
                self.initialPlcs,
            )
            for move in self.viewObj.Group:
                move.Visibility = False
            commands = ""
            for move in self.viewObj.Group:
                more = UtilsAssembly.generatePropertySettings(move)
                commands = commands + more
            if commands:
                Gui.doCommand(commands[:-1])  # Don't use the last \n
            self.viewObj.purgeTouched()
            if self.creating_timeline_operation:
                self.doc.finalizeProvisionalTimelineOperationBlock(
                    self.viewObj,
                    [*self.viewObj.Group, self.viewObj],
                )
            else:
                UtilsAssembly.finalizeTimelineResourceGroupEdit(
                    self.viewObj,
                    self.timeline_resource_edit,
                    list(self.viewObj.Group),
                )
        except Exception as error:
            App.Console.PrintError(
                "Could not finalize the exploded view: "
                f"{error}\n"
            )
            return False

        self.deactivate()
        return True

    def reject(self):
        self.deactivate()
        return True

    def autoClosedOnDeletedDocument(self):
        self._deactivate_deleted_document()
        self.transaction.document_deleted()

    def _deactivate_deleted_document(self):
        Gui.Selection.removeSelectionGate()
        Gui.Selection.removeObserver(self)
        Gui.Selection.clearSelection(self.doc.Name)

    def deactivate(self):
        pref = Preferences.preferences()
        pref.SetBool("PartsAsSingleSolid", self.form.CheckBox_PartsAsSingleSolid.isChecked())

        gui_context_live = (
            UtilsAssembly._document_is_open(self.doc)
            and str(getattr(self.doc, "Uid", "") or "")
            == self.document_uid
            and Gui.getDocument(self.doc.Name) is self.gui_doc
        )
        if gui_context_live:
            self.view.removeDraggerCallback(
                self.asmDragger,
                "addFinishCallback",
                self.cbFin,
            )
            self.view.removeDraggerCallback(
                self.asmDragger,
                "addMotionCallback",
                self.cbMov,
            )

        assembly_name, assembly_id, exact_assembly = (
            self.assembly_identity
        )
        assembly_live = (
            gui_context_live
            and self.doc.getObject(assembly_name) is exact_assembly
            and int(exact_assembly.ID) == assembly_id
        )
        if assembly_live:
            exact_assembly.ViewObject.DraggerVisibility = (
                self.dragger_visibility_before_task
            )
            exact_assembly.ViewObject.EnableMovement = (
                self.enable_movement_before_task
            )

        Gui.Selection.removeSelectionGate()
        Gui.Selection.removeObserver(self)
        Gui.Selection.clearSelection(self.doc.Name)

        view_name, view_id, exact_view = self.view_identity
        if (
            gui_context_live
            and self.doc.getObject(view_name) is exact_view
            and int(exact_view.ID) == view_id
        ):
            exact_view.Proxy.setMovesChangedCallback(None)
        if gui_context_live:
            self.view.removeEventCallback(
                "SoLocation2Event",
                self.callbackMove,
            )
            self.view.removeEventCallback(
                "SoMouseButtonEvent",
                self.callbackClick,
            )
            self.view.removeEventCallback(
                "SoKeyboardEvent",
                self.callbackKey,
            )

    def setDragger(self):
        if self.blockSetDragger:
            return

        if not self._ownsLiveTaskContext():
            self.enableDragger(False)
            return

        self.dismissCurrentStep()
        self.selectedRefs = []
        self.selectedObjs = []
        self.selectedObjsInitPlc = []
        self.selectedObjIdentities = []
        selection = Gui.Selection.getSelectionEx("*", 0)
        if not selection:
            self.enableDragger(False)
            return
        for sel in selection:
            if (
                sel.Object is None
                or sel.Object.Document is not self.doc
                or not UtilsAssembly.isTimelineOperationActive(
                    sel.Object
                )
            ):
                continue
            # If you select 2 solids (bodies for example) within an assembly.
            # There'll be a single sel but 2 SubElementNames.

            if not sel.SubElementNames:
                # no subnames, so its a root assembly itself that is selected.
                Gui.Selection.removeSelection(sel.Object)
                continue

            for sub_name in sel.SubElementNames:
                moving_part, new_sub = UtilsAssembly.getComponentReference(
                    self.assembly, sel.Object, sub_name
                )
                if not UtilsAssembly.isMovableAssemblyComponent(
                    self.assembly,
                    moving_part,
                ):
                    continue

                ref = [moving_part, [new_sub]]
                obj = UtilsAssembly.getObject(ref)
                element_name = UtilsAssembly.getElementName(sub_name)

                # Only objects within the assembly, not the assembly and not elements.
                if obj is None or moving_part is None or obj == self.assembly or element_name != "":
                    Gui.Selection.removeSelection(sel.Object, sub_name)
                    continue

                partAsSolid = self.form.CheckBox_PartsAsSingleSolid.isChecked()
                move_occurrence = (
                    partAsSolid
                    or UtilsAssembly.isLink(moving_part)
                    or moving_part.isDerivedFrom(
                        "Assembly::AssemblyLink"
                    )
                )
                if move_occurrence:
                    obj = moving_part

                # truncate the sub name at obj.Name
                if move_occurrence:
                    # We handle both cases separately because with external files there
                    # can be several times the same name. For containing part we are sure it's
                    # the first instance, for the object we are sure it's the last.
                    ref[1][0] = UtilsAssembly.truncateSubAtLast(ref[1][0], obj.Name)
                else:
                    ref[1][0] = UtilsAssembly.truncateSubAtFirst(ref[1][0], obj.Name)

                if not obj in self.selectedObjs and hasattr(obj, "Placement"):
                    ref = [sel.Object, [sub_name]]
                    self.selectedRefs.append(ref)
                    self.selectedObjs.append(obj)
                    self.selectedObjsInitPlc.append(App.Placement(obj.Placement))
                    object_document = obj.Document
                    self.selectedObjIdentities.append(
                        (
                            object_document,
                            str(
                                getattr(object_document, "Uid", "")
                                or ""
                            ),
                            str(obj.Name),
                            int(obj.ID),
                            obj,
                        )
                    )

        if len(self.selectedObjs) != 0:
            self.enableDragger(True)
            self.onAlignToCenter()

        else:
            self.enableDragger(False)

    def _selectedObjectsRemainLive(self):
        if (
            len(self.selectedObjs)
            != len(self.selectedObjsInitPlc)
            or len(self.selectedObjs)
            != len(self.selectedObjIdentities)
        ):
            return False
        try:
            return all(
                UtilsAssembly._document_is_open(document)
                and str(getattr(document, "Uid", "") or "")
                == document_uid
                and document.getObject(name) is exact_object
                and int(exact_object.ID) == object_id
                and exact_object is selected_object
                and UtilsAssembly.isTimelineOperationActive(
                    exact_object
                )
                and UtilsAssembly.isMovableAssemblyComponent(
                    self.assembly,
                    exact_object,
                )
                for selected_object, (
                    document,
                    document_uid,
                    name,
                    object_id,
                    exact_object,
                ) in zip(
                    self.selectedObjs,
                    self.selectedObjIdentities,
                )
            )
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            return False

    def enableDragger(self, val):
        self.assembly.ViewObject.DraggerVisibility = val
        self.form.btnAlignDragger.setEnabled(val)
        if val:
            self.form.btnAlignDragger.setText("Align dragger to...")
        else:
            self.form.btnAlignDragger.setText("Select a part")

    def onMovesChanged(self):
        if (
            hasattr(self, "view_identity")
            and not self._ownsLiveTaskContext()
        ):
            return
        # First reset positions
        UtilsAssembly._restoreExactAssemblyPartPlacements(
            self.assembly,
            self.initialPlcs,
        )

        self.viewObj.Proxy.applyMoves(self.viewObj, self.com, self.size)

        self.form.stepList.clear()
        for move in self.viewObj.Group:
            if UtilsAssembly.isTimelineOperationActive(move):
                item = QtWidgets.QListWidgetItem(move.Name)
                item.setData(
                    QtCore.Qt.UserRole,
                    (str(move.Name), int(move.ID)),
                )
                self.form.stepList.addItem(item)

    def onItemClicked(self, item):
        if not self._ownsLiveTaskContext():
            return
        identity = item.data(QtCore.Qt.UserRole)
        if (
            not isinstance(identity, tuple)
            or len(identity) != 2
        ):
            return
        move = self.doc.getObject(identity[0])
        if (
            move is None
            or int(move.ID) != int(identity[1])
            or move not in self.viewObj.Group
            or not UtilsAssembly.isTimelineOperationActive(move)
        ):
            self.onMovesChanged()
            return
        Gui.Selection.clearSelection(self.doc.Name)
        Gui.Selection.addSelection(self.doc.Name, move.Name, "")
        # we give back the focus to the item as addSelection gave the focus to the 3dview
        self.form.stepList.setCurrentItem(item)

    def onRadialClicked(self):
        self.dismissCurrentStep()

        # Add to selection all the movable parts
        partsAsSolid = self.form.CheckBox_PartsAsSingleSolid.isChecked()
        assemblyParts = UtilsAssembly.getMovablePartsWithin(self.assembly, partsAsSolid)
        self.blockSetDragger = True
        for part in assemblyParts:
            Gui.Selection.addSelection(part, "")
        self.blockSetDragger = False
        self.setDragger()

        self.radialExplosion = True

    def onAlignTo(self):
        self.alignMode = "Custom"
        self.selectingFeature = True
        # We use greedy selection to prevent that clicking again on the solid
        # clears selection before trying to select the whole assembly
        Gui.Selection.setSelectionStyle(Gui.Selection.SelectionStyle.GreedySelection)
        self.enableDragger(False)
        self.form.LabelAlignDragger.setVisible(True)

    def endSelectionMode(self):
        self.selectingFeature = False
        self.enableDragger(True)
        Gui.Selection.setSelectionStyle(Gui.Selection.SelectionStyle.NormalSelection)
        self.form.LabelAlignDragger.setVisible(False)

    def onAlignToCenter(self):
        self.alignMode = "Center"
        self.setDraggerObjectPlc()

    def onAlignToPartOrigin(self):
        self.alignMode = "PartOrigin"
        self.setDraggerObjectPlc()

    def findDraggerInitialPlc(self):
        if len(self.selectedObjs) == 0:
            return

        if self.alignMode == "Custom":
            self.initialDraggerPlc = App.Placement(self.assembly.ViewObject.DraggerPlacement)
        else:
            plc = UtilsAssembly.getGlobalPlacement(self.selectedRefs[0], self.selectedObjs[0])
            self.initialDraggerPlc = App.Placement(plc)
            if self.alignMode == "Center":
                self.initialDraggerPlc.Base = UtilsAssembly.getCenterOfBoundingBox(
                    self.selectedObjs, self.selectedRefs
                )

    def setDraggerObjectPlc(self):
        self.findDraggerInitialPlc()

        self.blockDraggerMove = True
        self.assembly.ViewObject.DraggerPlacement = self.initialDraggerPlc
        self.blockDraggerMove = False

    def createExplodedViewObject(self):

        Gui.addModule("CommandCreateView")
        document_expression = (
            f"App.getDocument({str(self.doc.Name)!r})"
        )
        self.viewObj = Gui.runDocumentObjectCommand(
            self.doc,
            "CommandCreateView.createExplodedViewFeature("
            f"{document_expression}, "
            f"{document_expression}.getObject({str(self.assembly.Name)!r}))",
            "App::FeaturePython",
        )
        Gui.doCommandGui(
            "CommandCreateView.ViewProviderExplodedView("
            f"Gui.getDocument({str(self.doc.Name)!r}).getObject("
            f"{str(self.viewObj.Name)!r}))"
        )

    def createExplodedStepObject(self):
        if not self._ownsLiveTaskContext():
            raise RuntimeError(
                "The exploded-view task lost its exact Assembly context"
            )
        moveType_index = 0
        if self.radialExplosion:
            self.radialExplosion = False
            moveType_index = 1  # 1 = type_index of "Radial"

        document_expression = (
            f"App.getDocument({str(self.doc.Name)!r})"
        )
        self.currentStep = Gui.runDocumentObjectCommand(
            self.doc,
            "CommandCreateView.createExplodedViewStepFeature("
            f"{document_expression}, "
            f"{document_expression}.getObject({str(self.assembly.Name)!r}), "
            f"{moveType_index})",
            "App::FeaturePython",
        )
        Gui.doCommandGui(
            "CommandCreateView.ViewProviderExplodedViewStep("
            f"Gui.getDocument({str(self.doc.Name)!r}).getObject("
            f"{str(self.currentStep.Name)!r}))"
        )

        self.currentStep.MovementTransform = App.Placement()

        # Note: the rootObj of all our refs must be the same since all the
        # objects are within assembly. So we put all the sub in a single ref.
        listOfSubs = []
        for ref in self.selectedRefs:
            listOfSubs.append(ref[1][0])
        self.currentStep.References = [self.selectedRefs[0][0], listOfSubs]

        # Note: self.viewObj.Group.append(self.currentStep) does not work
        listOfMoves = self.viewObj.Group
        listOfMoves.append(self.currentStep)
        self.viewObj.Group = listOfMoves
        UtilsAssembly.markTimelineResource(self.currentStep, self.viewObj)

    def dismissCurrentStep(self):
        if self.currentStep is None:
            return

        if self._selectedObjectsRemainLive():
            for obj, init_plc in zip(
                self.selectedObjs,
                self.selectedObjsInitPlc,
            ):
                obj.Placement = init_plc

        if (
            self._ownsLiveTaskContext()
            and self.doc.getObject(self.currentStep.Name)
            is self.currentStep
        ):
            Gui.doCommand(
                f"App.getDocument({str(self.doc.Name)!r}).removeObject("
                f"{str(self.currentStep.Name)!r})"
            )
        self.currentStep = None

        Gui.Selection.clearSelection(self.doc.Name)

    def draggerMoved(self, event):
        if (
            self.blockDraggerMove
            or not self._ownsLiveTaskContext()
            or not self._selectedObjectsRemainLive()
        ):
            return

        if self.currentStep is None:
            self.createExplodedStepObject()

        # reset the objects position to their position before the current move.
        for obj, init_plc in zip(self.selectedObjs, self.selectedObjsInitPlc):
            obj.Placement = init_plc

        # we update the move Placement.
        draggerPlc = self.assembly.ViewObject.DraggerPlacement
        self.currentStep.MovementTransform = draggerPlc * self.initialDraggerPlc.inverse()

        # Apply the move
        self.currentStep.Proxy.applyStep(self.currentStep, self.com, self.size)

    def draggerFinished(self, event):
        if (
            self.currentStep is None
            or not self._selectedObjectsRemainLive()
        ):
            return
        isRadial = self.currentStep.MoveType == "Radial"
        self.currentStep = None

        if isRadial:
            Gui.Selection.clearSelection(self.doc.Name)
            return

        # Reset the initial placements
        self.findDraggerInitialPlc()

        for i, obj in enumerate(self.selectedObjs):
            self.selectedObjsInitPlc[i] = App.Placement(obj.Placement)

    def moveMouse(self, info):
        if not self.selectingFeature:
            return

        cursor_info = self.view.getObjectInfo(self.view.getCursorPos())

        if not cursor_info or not self.presel_ref:
            self.assembly.ViewObject.DraggerVisibility = False
            return

        ref = self.presel_ref
        element_name = UtilsAssembly.getElementName(ref[1][0])

        if element_name == "":
            vertex_name = ""
        else:
            newPos = App.Vector(cursor_info["x"], cursor_info["y"], cursor_info["z"])
            vertex_name = UtilsAssembly.findElementClosestVertex(self.assembly, ref, newPos)

        ref = UtilsAssembly.addVertexToReference(ref, vertex_name)

        plc = UtilsAssembly.findPlacement(ref)
        global_plc = UtilsAssembly.getGlobalPlacement(ref)
        plc = global_plc * plc

        self.blockDraggerMove = True
        self.assembly.ViewObject.DraggerPlacement = plc
        self.blockDraggerMove = False
        self.assembly.ViewObject.DraggerVisibility = True

    def clickMouse(self, info):
        if info["Button"] == "BUTTON2" and info["State"] == "DOWN":
            if self.selectingFeature:
                self.endSelectionMode()

    # 3D view keyboard handler
    def KeyboardEvent(self, info):
        if info["State"] == "UP" and info["Key"] == "ESCAPE":
            if self.currentStep is None:
                dialog = Gui.Control.activeTaskDialog(self.gui_doc)
                if dialog:
                    dialog.reject()
            else:
                if self.selectingFeature:
                    self.endSelectionMode()
                else:
                    self.dismissCurrentStep()

    # Taskbox keyboard event handler
    def eventFilter(self, watched, event):
        if self.form is not None and watched == self.form.stepList:
            if event.type() == QtCore.QEvent.ShortcutOverride:
                if event.key() == QtCore.Qt.Key_Delete:
                    event.accept()
                    return True  # Indicate that the event has been handled
                return False

            elif event.type() == QtCore.QEvent.KeyPress:
                if event.key() == QtCore.Qt.Key_Delete:
                    selected_indexes = self.form.stepList.selectedIndexes()
                    sorted_indexes = sorted(selected_indexes, key=lambda x: x.row(), reverse=True)
                    for index in sorted_indexes:
                        if not self._ownsLiveTaskContext():
                            return True
                        item = self.form.stepList.item(index.row())
                        identity = item.data(QtCore.Qt.UserRole)
                        if (
                            not isinstance(identity, tuple)
                            or len(identity) != 2
                        ):
                            continue
                        move = self.doc.getObject(identity[0])
                        if (
                            move is None
                            or int(move.ID) != int(identity[1])
                            or move not in self.viewObj.Group
                            or not UtilsAssembly.isTimelineOperationActive(
                                move
                            )
                        ):
                            continue
                        # First remove the link from the viewObj.
                        group = list(self.viewObj.Group)
                        group.remove(move)
                        self.viewObj.Group = group
                        self.doc.removeObject(move.Name)

                    return True  # Consume the event

        return super().eventFilter(watched, event)

    # selectionObserver stuff
    def addSelection(self, doc_name, obj_name, sub_name, mousePos):
        if self.selectingFeature:
            Gui.Selection.removeSelection(doc_name, obj_name, sub_name)
            return

        else:
            if (
                not self._ownsLiveTaskContext()
                or doc_name != self.doc.Name
            ):
                return
            rootObj = self.doc.getObject(obj_name)
            if (
                rootObj is None
                or not UtilsAssembly.isTimelineOperationActive(
                    rootObj
                )
            ):
                return
            moving_part, new_sub = UtilsAssembly.getComponentReference(
                self.assembly, rootObj, sub_name
            )
            ref = [moving_part, [new_sub]]
            obj = UtilsAssembly.getObject(ref)

            if (
                obj is None
                or not UtilsAssembly.isMovableAssemblyComponent(
                    self.assembly,
                    moving_part,
                )
                or not UtilsAssembly.isTimelineOperationActive(obj)
            ):
                return

            if (
                self.form.CheckBox_PartsAsSingleSolid.isChecked()
                or UtilsAssembly.isLink(moving_part)
                or moving_part.isDerivedFrom(
                    "Assembly::AssemblyLink"
                )
            ):
                part = moving_part
            else:
                part = obj

            element_name = UtilsAssembly.getElementName(sub_name)

            if element_name != "":
                # When selecting, we do not want to select an element, but only the containing part.
                Gui.Selection.removeSelection(doc_name, obj_name, sub_name)
                if Gui.Selection.isSelected(part, ""):
                    Gui.Selection.removeSelection(part, "")
                else:
                    Gui.Selection.addSelection(part, "")
            else:
                self.setDragger()
                pass

    def removeSelection(self, doc_name, obj_name, sub_name, mousePos=None):
        if self.selectingFeature:
            self.endSelectionMode()
            self.findDraggerInitialPlc()
            return

        element_name = UtilsAssembly.getElementName(sub_name)
        if element_name == "":
            self.setDragger()
            pass

    def setPreselection(self, doc_name, obj_name, sub_name):
        if not self.selectingFeature or not sub_name:
            self.presel_ref = None
            return

        self.presel_ref = [App.getDocument(doc_name).getObject(obj_name), [sub_name]]

    def clearSelection(self, doc_name):
        self.form.stepList.clearSelection()
        self.setDragger()


if App.GuiUp:
    Gui.addCommand("Assembly_CreateView", CommandCreateView())
