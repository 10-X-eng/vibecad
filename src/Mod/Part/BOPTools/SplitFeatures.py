# SPDX-License-Identifier: LGPL-2.1-or-later

# /***************************************************************************
# *   Copyright (c) 2016 Victor Titov (DeepSOIC) <vv.titov@gmail.com>       *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This library is free software; you can redistribute it and/or         *
# *   modify it under the terms of the GNU Library General Public           *
# *   License as published by the Free Software Foundation; either          *
# *   version 2 of the License, or (at your option) any later version.      *
# *                                                                         *
# *   This library  is distributed in the hope that it will be useful,      *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this library; see the file COPYING.LIB. If not,    *
# *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
# *   Suite 330, Boston, MA  02111-1307, USA                                *
# *                                                                         *
# ***************************************************************************/

__title__ = "BOPTools.SplitFeatures module"
__author__ = "DeepSOIC"
__url__ = "https://www.freecad.org"
__doc__ = "Shape splitting document objects (features)."

from . import SplitAPI
import FreeCAD
import Part
from PartLinkScope import migrate_many_to_global

if FreeCAD.GuiUp:
    import FreeCADGui
    import PartGui
    from PySide import QtCore, QtGui

    # -------------------------- translation-related code -------------------------
    # See forum thread "A new Part tool is being born... JoinFeatures!"
    # https://forum.freecad.org/viewtopic.php?f=22&t=11112&start=30#p90239
    try:
        _fromUtf8 = QtCore.QString.fromUtf8
    except Exception:

        def _fromUtf8(s):
            return s

    translate = FreeCAD.Qt.translate
# --------------------------/translation-related code --------------------------


def getIconPath(icon_dot_svg):
    return icon_dot_svg


def _selected_shape_objects():
    objects = []
    for selection in FreeCADGui.Selection.getSelectionEx():
        selected = selection.Object
        if not PartGui.isModelingObjectActive(selected):
            continue
        obj = PartGui.resolveModelingObject(selected)
        if (
            obj is not None
            and hasattr(obj, "Shape")
            and not obj.Shape.isNull()
            and obj not in objects
        ):
            objects.append(obj)
    return objects


def _selected_presentation_objects():
    """Return the exact viewport owners replaced by a split command."""

    objects = []
    for selection in FreeCADGui.Selection.getSelectionEx():
        selected = selection.Object
        if not PartGui.isModelingObjectActive(selected):
            continue
        resolved = PartGui.resolveModelingObject(selected)
        if resolved is None:
            continue

        presentation = selected
        if selected.isDerivedFrom("PartDesign::Body"):
            presentation = selected
        else:
            owner = resolved.getParentGeoFeatureGroup()
            if owner is not None and owner.isDerivedFrom("PartDesign::Body"):
                presentation = owner

        if presentation not in objects:
            objects.append(presentation)
    return objects


def _visible_presentation_objects():
    return [
        obj
        for obj in _selected_presentation_objects()
        if obj.ViewObject is not None and bool(obj.Visibility)
    ]


def _replace_visible_presentations(result, presentations):
    if (
        presentations
        and PartGui.setModelingReplacedInputs(
            result,
            presentations,
        )
    ):
        for presentation in presentations:
            presentation.Visibility = False


def _object_expression(obj):
    """Return a recorded command expression for one exact document object."""

    return (
        f"App.getDocument({obj.Document.Name!r})"
        f".getObject({obj.Name!r})"
    )


def _replace_visible_presentations_command(result_expression, presentations):
    presentation_expression = ", ".join(
        _object_expression(presentation)
        for presentation in presentations
    )
    FreeCADGui.doCommand(
        "BOPTools.SplitFeatures._replace_visible_presentations("
        f"{result_expression}, [{presentation_expression}])"
    )


def _has_fragment_operands():
    objects = _selected_shape_objects()
    if len(objects) >= 2:
        return True
    if len(objects) != 1:
        return False
    shape = objects[0].Shape
    return shape.ShapeType == "Compound" and len(shape.childShapes()) >= 2


def _mark_timeline_resource(resource, owner):
    """Persist one private Part implementation object under its operation."""

    from CompoundTools.Explode import _mark_timeline_resource as mark_resource

    mark_resource(resource, owner)


# -------------------------- /common stuff ------------------------------------

# -------------------------- BooleanFragments ---------------------------------


def makeBooleanFragments(name):
    """makeBooleanFragments(name): makes an BooleanFragments object."""
    obj = FreeCAD.ActiveDocument.addObject("Part::FeaturePython", name)
    FeatureBooleanFragments(obj)
    if FreeCAD.GuiUp:
        ViewProviderBooleanFragments(obj.ViewObject)
    return obj


class FeatureBooleanFragments:
    """The BooleanFragments feature object."""

    def __init__(self, obj):
        obj.addProperty(
            "App::PropertyLinkListGlobal",
            "Objects",
            "BooleanFragments",
            "Object to compute intersections between.",
            locked=True,
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "Mode",
            "BooleanFragments",
            "- Standard: wires, shells, compsolids remain in one piece.\n"
            "- Split: wires, shells, compsolids are split.\n"
            "- CompSolid: make compsolid from solid fragments.",
            locked=True,
        )
        obj.Mode = ["Standard", "Split", "CompSolid"]
        obj.addProperty(
            "App::PropertyLength",
            "Tolerance",
            "BooleanFragments",
            "Tolerance when intersecting (fuzzy value). "
            "In addition to tolerances of the shapes.",
            locked=True,
        )

        obj.Proxy = self
        self.Type = "FeatureBooleanFragments"

    def onDocumentRestored(self, obj):
        migrate_many_to_global(obj, "Objects")

    def execute(self, selfobj):
        shapes = [obj.Shape for obj in selfobj.Objects]
        if len(shapes) == 1 and shapes[0].ShapeType == "Compound":
            shapes = shapes[0].childShapes()
        if len(shapes) < 2:
            raise ValueError(
                "At least two shapes are needed for computing boolean fragments. Got only {num}.".format(
                    num=len(shapes)
                )
            )
        selfobj.Shape = SplitAPI.booleanFragments(shapes, selfobj.Mode, selfobj.Tolerance)


class ViewProviderBooleanFragments:
    """A View Provider for the Part BooleanFragments feature."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def getIcon(self):
        return ":/icons/booleans/Part_BooleanFragments.svg"

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def claimChildren(self):
        return self.Object.Objects

    def onDelete(self, feature, subelements):
        try:
            for obj in self.claimChildren():
                obj.ViewObject.show()
        except Exception as err:
            FreeCAD.Console.PrintError("Error in onDelete: " + str(err))
        return True

    def canDragObjects(self):
        return True

    def canDropObjects(self):
        return True

    def canDragObject(self, dragged_object):
        return True

    def canDropObject(self, incoming_object):
        return hasattr(incoming_object, "Shape")

    def dragObject(self, selfvp, dragged_object):
        objs = self.Object.Objects
        objs.remove(dragged_object)
        self.Object.Objects = objs

    def dropObject(self, selfvp, incoming_object):
        self.Object.Objects = self.Object.Objects + [incoming_object]


def cmdCreateBooleanFragmentsFeature(name, mode):
    """cmdCreateBooleanFragmentsFeature(name, mode): implementation of GUI command to create
    BooleanFragments feature (GFA). Mode can be "Standard", "Split", or "CompSolid"."""
    document = FreeCAD.ActiveDocument
    operands = _selected_shape_objects()
    presentations = _visible_presentation_objects()
    document.openTransaction("Create Boolean Fragments")
    FreeCADGui.addModule("BOPTools.SplitFeatures")
    result = FreeCADGui.runDocumentObjectCommand(
        document,
        f"BOPTools.SplitFeatures.makeBooleanFragments(name={name!r})",
        "Part::Feature",
    )
    result_expression = _object_expression(result)
    FreeCADGui.doCommand(
        f"{result_expression}.Objects = ["
        + ", ".join(_object_expression(operand) for operand in operands)
        + "]"
    )
    FreeCADGui.doCommand(
        f"{result_expression}.Mode = {mode!r}"
    )

    try:
        FreeCADGui.doCommand(
            f"{result_expression}.Proxy.execute({result_expression})"
        )
        FreeCADGui.doCommand(f"{result_expression}.purgeTouched()")
    except Exception as err:
        mb = QtGui.QMessageBox()
        mb.setIcon(mb.Icon.Warning)
        error_text1 = translate("Part_SplitFeatures", "Computing the result failed with an error:")
        error_text2 = translate(
            "Part_SplitFeatures",
            "Click 'Continue' to create the feature anyway, or 'Abort' to cancel.",
        )
        mb.setText(error_text1 + "\n\n" + str(err) + "\n\n" + error_text2)
        mb.setWindowTitle(translate("Part_SplitFeatures", "Bad Selection", None))
        btnAbort = mb.addButton(QtGui.QMessageBox.StandardButton.Abort)
        btnOK = mb.addButton(
            translate("Part_SplitFeatures", "Continue", None),
            QtGui.QMessageBox.ButtonRole.ActionRole,
        )
        mb.setDefaultButton(btnOK)

        mb.exec_()

        if mb.clickedButton() is btnAbort:
            document.abortTransaction()
            return

    _replace_visible_presentations_command(
        result_expression,
        presentations,
    )

    document.commitTransaction()


class CommandBooleanFragments:
    """Command to create BooleanFragments feature."""

    def GetResources(self):
        return {
            "Pixmap": getIconPath("Part_BooleanFragments.svg"),
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_BooleanFragments", "Boolean Fragments"),
            "Accel": "",
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_BooleanFragments",
                "Creates a boolean union which is sliced at the intersections of the selected shapes",
            ),
        }

    def Activated(self):
        if _has_fragment_operands():
            cmdCreateBooleanFragmentsFeature(name="BooleanFragments", mode="Standard")
        else:
            mb = QtGui.QMessageBox()
            mb.setIcon(mb.Icon.Warning)
            mb.setText(
                translate(
                    "Part_SplitFeatures",
                    "Select at least two objects, or one or more compounds. "
                    "If only one compound is selected, the compounded shapes will be intersected between each other "
                    "(otherwise, compounds with self-intersections are invalid).",
                    None,
                )
            )
            mb.setWindowTitle(translate("Part_SplitFeatures", "Bad Selection", None))
            mb.exec_()

    def IsActive(self):
        return (
            FreeCAD.ActiveDocument is not None
            and PartGui.canStartRetainedModelingTask()
            and _has_fragment_operands()
        )


# -------------------------- /BooleanFragments --------------------------------

# -------------------------- Slice --------------------------------------------


def makeSlice(name):
    """makeSlice(name): makes an Slice object."""
    obj = FreeCAD.ActiveDocument.addObject("Part::FeaturePython", name)
    FeatureSlice(obj)
    if FreeCAD.GuiUp:
        ViewProviderSlice(obj.ViewObject)
    return obj


class FeatureSlice:
    """The Slice feature object."""

    def __init__(self, obj):
        obj.addProperty(
            "App::PropertyLinkGlobal", "Base", "Slice", "Object to be sliced.", locked=True
        )
        obj.addProperty(
            "App::PropertyLinkListGlobal",
            "Tools",
            "Slice",
            "Objects that slice.",
            locked=True,
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "Mode",
            "Slice",
            "- Standard: wires, shells, compsolids remain in one piece.\n"
            "- Split: wires, shells, compsolids are split.\n"
            "- CompSolid: make compsolid from solid fragments.",
            locked=True,
        )
        obj.Mode = ["Standard", "Split", "CompSolid"]
        obj.addProperty(
            "App::PropertyLength",
            "Tolerance",
            "Slice",
            "Tolerance when intersecting (fuzzy value). "
            "In addition to tolerances of the shapes.",
            locked=True,
        )

        obj.Proxy = self
        self.Type = "FeatureSlice"

    def onDocumentRestored(self, obj):
        migrate_many_to_global(obj, "Base", "Tools")

    def execute(self, selfobj):
        if len(selfobj.Tools) < 1:
            raise ValueError("No slicing objects supplied!")

        # helper function to get the shape from object or group
        def get_shape(obj):
            """get shape from a part object or compound from a group."""
            if hasattr(obj, "Shape"):
                return obj.Shape
            elif hasattr(obj, "Group"):  # it's a group/container from ie. slice apart
                shapes = []
                for child in obj.Group:
                    if hasattr(child, "Shape"):
                        shapes.append(child.Shape)
                if shapes:
                    return Part.makeCompound(shapes)
            return None

        # get base shape
        base_shape = get_shape(selfobj.Base)
        if base_shape is None or base_shape.isNull():
            raise ValueError("Base object has no valid shape!")

        # get tool shapes
        tool_shapes = []
        for tool in selfobj.Tools:
            shape = get_shape(tool)
            if shape is not None and not shape.isNull():
                tool_shapes.append(shape)

        if len(tool_shapes) < 1:
            raise ValueError("No valid tool shapes available")

        selfobj.Shape = SplitAPI.slice(
            base_shape,
            tool_shapes,
            selfobj.Mode,
            selfobj.Tolerance,
        )


class ViewProviderSlice:
    """A View Provider for the Part Slice feature."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def getIcon(self):
        return ":/icons/booleans/Part_Slice.svg"

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def claimChildren(self):
        return [self.Object.Base] + self.Object.Tools

    def onDelete(self, feature, subelements):
        try:
            for obj in self.claimChildren():
                obj.ViewObject.show()
        except Exception as err:
            FreeCAD.Console.PrintError("Error in onDelete: " + str(err))
        return True


def cmdCreateSliceFeature(
    name,
    mode,
    transaction=True,
    *,
    return_result=False,
    document=None,
):
    """cmdCreateSliceFeature(name, mode): implementation of GUI command to create
    Slice feature. Mode can be "Standard", "Split", or "CompSolid"."""
    if document is None:
        document = FreeCAD.ActiveDocument
    operands = _selected_shape_objects()
    presentations = _visible_presentation_objects()
    if transaction:
        document.openTransaction("Create Slice")
    FreeCADGui.addModule("BOPTools.SplitFeatures")
    result = FreeCADGui.runDocumentObjectCommand(
        document,
        f"BOPTools.SplitFeatures.makeSlice(name={name!r})",
        "Part::Feature",
    )
    result_expression = _object_expression(result)
    operand_expression = (
        "["
        + ", ".join(
            _object_expression(operand)
            for operand in operands
        )
        + "]"
    )
    FreeCADGui.doCommand(
        f"{result_expression}.Base = {operand_expression}[0]\n"
        f"{result_expression}.Tools = {operand_expression}[1:]"
    )
    FreeCADGui.doCommand(
        f"{result_expression}.Mode = {mode!r}"
    )

    try:
        FreeCADGui.doCommand(
            f"{result_expression}.Proxy.execute({result_expression})"
        )
        FreeCADGui.doCommand(f"{result_expression}.purgeTouched()")
    except Exception as err:
        mb = QtGui.QMessageBox()
        mb.setIcon(mb.Icon.Warning)
        error_text1 = translate("Part_SplitFeatures", "Computing the result failed with an error:")
        error_text2 = translate(
            "Part_SplitFeatures",
            "Click 'Continue' to create the feature anyway, or 'Abort' to cancel.",
        )
        mb.setText(error_text1 + "\n\n" + str(err) + "\n\n" + error_text2)
        mb.setWindowTitle(translate("Part_SplitFeatures", "Bad Selection", None))
        btnAbort = mb.addButton(QtGui.QMessageBox.StandardButton.Abort)
        btnOK = mb.addButton(
            translate("Part_SplitFeatures", "Continue", None),
            QtGui.QMessageBox.ButtonRole.ActionRole,
        )
        mb.setDefaultButton(btnOK)

        mb.exec_()

        if mb.clickedButton() is btnAbort:
            if transaction:
                document.abortTransaction()
            return False

    if transaction:
        _replace_visible_presentations_command(
            result_expression,
            presentations,
        )

    if transaction:
        document.commitTransaction()
    return result if return_result else True


def cmdSliceApart():
    document = FreeCAD.ActiveDocument
    replaced_inputs = [
        obj
        for obj in _selected_presentation_objects()
        if obj.ViewObject is not None and obj.ViewObject.Visibility
    ]
    document.openTransaction("Slice apart")
    try:
        slice_feature = cmdCreateSliceFeature(
            name="Slice",
            mode="Split",
            transaction=False,
            return_result=True,
            document=document,
        )
        if slice_feature is None:
            if document.HasPendingTransaction:
                document.abortTransaction()
            return
        if (
            not document
            .isProvisionallyEnrolledInTimelineByCurrentTransaction(
                slice_feature
            )
        ):
            raise RuntimeError(
                "Slice Apart did not return its exact Slice feature."
            )

        FreeCADGui.addModule("CompoundTools.Explode")
        slice_expression = _object_expression(slice_feature)
        replaced_expression = ", ".join(
            _object_expression(obj)
            for obj in replaced_inputs
        )
        output_component = FreeCADGui.runDocumentObjectCommand(
            document,
            "CompoundTools.Explode.makeBodyOutputOperation("
            f"{slice_expression}, "
            "label='Slice Apart', "
            f"replaced_inputs=[{replaced_expression}], "
            f"editor={slice_expression})",
            "App::Part",
        )
        if (
            output_component.Document is not document
            or document.getObject(output_component.Name)
            is not output_component
            or getattr(
                output_component,
                "VibeCADTimelineRole",
                None,
            )
            != "operation"
        ):
            raise RuntimeError(
                "Slice Apart did not return its exact History operation."
            )

        if document.HasPendingTransaction:
            document.commitTransaction()
    except Exception:
        if document.HasPendingTransaction:
            document.abortTransaction()
        raise


class CommandSlice:
    """Command to create Slice feature."""

    def GetResources(self):
        return {
            "Pixmap": getIconPath("Part_Slice.svg"),
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_Slice", "Slice to Compound"),
            "Accel": "",
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_Slice",
                "Slices the selected object by using other objects as cutting tools and storing the results in one compound",
            ),
        }

    def Activated(self):
        if len(_selected_shape_objects()) > 1:
            cmdCreateSliceFeature(name="Slice", mode="Split")
        else:
            mb = QtGui.QMessageBox()
            mb.setIcon(mb.Icon.Warning)
            mb.setText(
                translate(
                    "Part_SplitFeatures",
                    "Select at least two objects. "
                    "The first one is the object to be sliced; "
                    "the rest are objects to slice with.",
                    None,
                )
            )
            mb.setWindowTitle(translate("Part_SplitFeatures", "Bad Selection", None))
            mb.exec_()

    def IsActive(self):
        return (
            FreeCAD.ActiveDocument is not None
            and PartGui.canStartRetainedModelingTask()
            and len(_selected_shape_objects()) > 1
        )


class CommandSliceApart:
    """Command to create exploded Slice feature."""

    def GetResources(self):
        return {
            "Pixmap": getIconPath("Part_SliceApart.svg"),
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_SliceApart", "Slice Apart"),
            "Accel": "",
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_SliceApart",
                "Slices the first selected Body with the others and creates "
                "independently editable output Bodies",
            ),
        }

    def Activated(self):
        if len(_selected_shape_objects()) > 1:
            cmdSliceApart()
        else:
            mb = QtGui.QMessageBox()
            mb.setIcon(mb.Icon.Warning)
            mb.setText(
                translate(
                    "Part_SplitFeatures",
                    "Select at least two objects. "
                    "The first one is the object to be sliced; "
                    "the rest are objects to slice with.",
                    None,
                )
            )
            mb.setWindowTitle(translate("Part_SplitFeatures", "Bad Selection", None))
            mb.exec_()

    def IsActive(self):
        return (
            FreeCAD.ActiveDocument is not None
            and PartGui.canStartRetainedModelingTask()
            and len(_selected_shape_objects()) > 1
        )


# -------------------------- /Slice -------------------------------------------

# -------------------------- XOR ----------------------------------------------


def makeXOR(name):
    """makeXOR(name): makes an XOR object."""
    obj = FreeCAD.ActiveDocument.addObject("Part::FeaturePython", name)
    FeatureXOR(obj)
    if FreeCAD.GuiUp:
        ViewProviderXOR(obj.ViewObject)
    return obj


class FeatureXOR:
    """The XOR feature object."""

    def __init__(self, obj):
        obj.addProperty(
            "App::PropertyLinkListGlobal",
            "Objects",
            "XOR",
            "Object to compute intersections between.",
            locked=True,
        )
        obj.addProperty(
            "App::PropertyLength",
            "Tolerance",
            "XOR",
            "Tolerance when intersecting (fuzzy value). "
            "In addition to tolerances of the shapes.",
            locked=True,
        )

        obj.Proxy = self
        self.Type = "FeatureXOR"

    def onDocumentRestored(self, obj):
        migrate_many_to_global(obj, "Objects")

    def execute(self, selfobj):
        shapes = [obj.Shape for obj in selfobj.Objects]
        if len(shapes) == 1 and shapes[0].ShapeType == "Compound":
            shapes = shapes[0].childShapes()
        if len(shapes) < 2:
            raise ValueError(
                "At least two shapes are needed for computing XOR. Got only {num}.".format(
                    num=len(shapes)
                )
            )
        selfobj.Shape = SplitAPI.xor(shapes, selfobj.Tolerance)


class ViewProviderXOR:
    """A View Provider for the Part XOR feature."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def getIcon(self):
        return ":/icons/booleans/Part_XOR.svg"

    def attach(self, vobj):
        self.ViewObject = vobj
        self.Object = vobj.Object

    def dumps(self):
        return None

    def loads(self, state):
        return None

    def claimChildren(self):
        return self.Object.Objects

    def onDelete(self, feature, subelements):
        try:
            for obj in self.claimChildren():
                obj.ViewObject.show()
        except Exception as err:
            FreeCAD.Console.PrintError("Error in onDelete: " + str(err))
        return True

    def canDragObjects(self):
        return True

    def canDropObjects(self):
        return True

    def canDragObject(self, dragged_object):
        return True

    def canDropObject(self, incoming_object):
        return hasattr(incoming_object, "Shape")

    def dragObject(self, selfvp, dragged_object):
        objs = self.Object.Objects
        objs.remove(dragged_object)
        self.Object.Objects = objs

    def dropObject(self, selfvp, incoming_object):
        self.Object.Objects = self.Object.Objects + [incoming_object]


def cmdCreateXORFeature(name):
    """cmdCreateXORFeature(name): implementation of GUI command to create
    XOR feature (GFA). Mode can be "Standard", "Split", or "CompSolid"."""
    document = FreeCAD.ActiveDocument
    operands = _selected_shape_objects()
    presentations = _visible_presentation_objects()
    document.openTransaction("Create Boolean XOR")
    FreeCADGui.addModule("BOPTools.SplitFeatures")
    result = FreeCADGui.runDocumentObjectCommand(
        document,
        f"BOPTools.SplitFeatures.makeXOR(name={name!r})",
        "Part::Feature",
    )
    result_expression = _object_expression(result)
    FreeCADGui.doCommand(
        f"{result_expression}.Objects = ["
        + ", ".join(_object_expression(operand) for operand in operands)
        + "]"
    )

    try:
        FreeCADGui.doCommand(
            f"{result_expression}.Proxy.execute({result_expression})"
        )
        FreeCADGui.doCommand(f"{result_expression}.purgeTouched()")
    except Exception as err:
        mb = QtGui.QMessageBox()
        mb.setIcon(mb.Icon.Warning)
        error_text1 = translate("Part_SplitFeatures", "Computing the result failed with an error:")
        error_text2 = translate(
            "Part_SplitFeatures",
            "Click 'Continue' to create the feature anyway, or 'Abort' to cancel.",
        )
        mb.setText(error_text1 + "\n\n" + str(err) + "\n\n" + error_text2)
        mb.setWindowTitle(translate("Part_SplitFeatures", "Bad Selection", None))
        btnAbort = mb.addButton(QtGui.QMessageBox.StandardButton.Abort)
        btnOK = mb.addButton(
            translate("Part_SplitFeatures", "Continue", None),
            QtGui.QMessageBox.ButtonRole.ActionRole,
        )
        mb.setDefaultButton(btnOK)

        mb.exec_()

        if mb.clickedButton() is btnAbort:
            document.abortTransaction()
            return

    _replace_visible_presentations_command(
        result_expression,
        presentations,
    )

    document.commitTransaction()


class CommandXOR:
    """Command to create XOR feature."""

    def GetResources(self):
        return {
            "Pixmap": getIconPath("Part_XOR.svg"),
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_XOR", "Boolean XOR"),
            "Accel": "",
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_XOR",
                "Performs an 'exclusive OR' boolean operation with two or more selected objects,\n"
                "or with the shapes inside a compound.\n"
                "Overlapping volumes of the shapes will be removed.",
            ),
        }

    def Activated(self):
        if _has_fragment_operands():
            cmdCreateXORFeature(name="XOR")
        else:
            mb = QtGui.QMessageBox()
            mb.setIcon(mb.Icon.Warning)
            mb.setText(
                translate(
                    "Part_SplitFeatures",
                    "Select at least two objects, or one or more compounds. "
                    "If only one compound is selected, the compounded shapes will be intersected between each other "
                    "(otherwise, compounds with self-intersections are invalid).",
                    None,
                )
            )
            mb.setWindowTitle(translate("Part_SplitFeatures", "Bad Selection", None))
            mb.exec_()

    def IsActive(self):
        return (
            FreeCAD.ActiveDocument is not None
            and PartGui.canStartRetainedModelingTask()
            and _has_fragment_operands()
        )


# -------------------------- /XOR ---------------------------------------------


def addCommands():
    FreeCADGui.addCommand("Part_BooleanFragments", CommandBooleanFragments())
    FreeCADGui.addCommand("Part_Slice", CommandSlice())
    FreeCADGui.addCommand("Part_SliceApart", CommandSliceApart())
    FreeCADGui.addCommand("Part_XOR", CommandXOR())
