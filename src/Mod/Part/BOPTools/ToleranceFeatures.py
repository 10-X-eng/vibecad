# SPDX-License-Identifier: LGPL-2.1-or-later

# /***************************************************************************
# *   Copyright (c) 2024 Eric Price (CorvusCorax)                           *
# *                      <eric.price[at]tuebingen.mpg.de>                   *
# *                                                                         *
# *   This file is part of FreeCAD.                                         *
# *                                                                         *
# *   FreeCAD is free software: you can redistribute it and/or modify it    *
# *   under the terms of the GNU Lesser General Public License as           *
# *   published by the Free Software Foundation, either version 2.1 of the  *
# *   License, or (at your option) any later version.                       *
# *                                                                         *
# *   FreeCAD is distributed in the hope that it will be useful, but        *
# *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
# *   Lesser General Public License for more details.                       *
# *                                                                         *
# *   You should have received a copy of the GNU Lesser General Public      *
# *   License along with FreeCAD. If not, see                               *
# *   <https://www.gnu.org/licenses/>.                                      *
# ***************************************************************************/

__title__ = "BOPTools.ToleranceFeatures module"
__author__ = "CorvusCorax"
__url__ = "https://www.freecad.org"
__doc__ = "Implementation of document objects (features) to adjust/manipulate tolerances."

import FreeCAD
import Part
from PartLinkScope import migrate_many_to_global

if FreeCAD.GuiUp:
    import FreeCADGui
    import PartGui
    from PySide import QtCore, QtGui

    # -------------------------- common stuff -------------------------------------

    # -------------------------- translation-related code -------------------------

    try:
        _fromUtf8 = QtCore.QString.fromUtf8
    except Exception:

        def _fromUtf8(s):
            return s

    translate = FreeCAD.Qt.translate
# --------------------------/translation-related code -------------------------


def getParamRefine():
    return FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Part/Boolean").GetBool(
        "RefineModel"
    )


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


def _visible_presentations(operands):
    presentations = []
    for operand in operands:
        presentation = PartGui.resolveModelingPresentationObject(
            operand
        )
        if (
            presentation is not None
            and presentation not in presentations
            and bool(presentation.Visibility)
        ):
            presentations.append(presentation)
    return presentations


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


def cmdCreateToleranceSetFeature(name, minTolerance=1e-7, maxTolerance=0):
    """cmdCreateToleranceSetFeature(name, minTolerance, maxTolerance): generalized implementation of GUI commands."""
    document = FreeCAD.ActiveDocument
    operands = _selected_shape_objects()
    presentations = _visible_presentations(operands)

    document.openTransaction("Create ToleranceSet")
    try:
        FreeCADGui.addModule("BOPTools.ToleranceFeatures")
        result = FreeCADGui.runDocumentObjectCommand(
            document,
            "BOPTools.ToleranceFeatures."
            f"makeToleranceSet(name={name!r})",
            "Part::Feature",
        )
        result_expression = _object_expression(result)
        FreeCADGui.doCommand(
            f"{result_expression}.minTolerance = {minTolerance!r}"
        )
        FreeCADGui.doCommand(
            f"{result_expression}.maxTolerance = {maxTolerance!r}"
        )
        FreeCADGui.doCommand(
            f"{result_expression}.Objects = ["
            + ", ".join(_object_expression(obj) for obj in operands)
            + "]"
        )
        FreeCADGui.doCommand(
            f"{result_expression}.Proxy.execute({result_expression})"
        )
        FreeCADGui.doCommand(f"{result_expression}.purgeTouched()")
        if result.Shape.isNull() or not result.Shape.isValid():
            raise RuntimeError(
                "Tolerance Set did not produce valid geometry"
            )

        presentation_expression = ", ".join(
            _object_expression(presentation)
            for presentation in presentations
        )
        FreeCADGui.doCommand(
            "BOPTools.ToleranceFeatures."
            "_replace_visible_presentations("
            f"{result_expression}, [{presentation_expression}])"
        )
        FreeCADGui.addModule("PartGui")
        FreeCADGui.doCommand(
            "PartGui.publishDesignDefinitionBlock("
            f"[{result_expression}])"
        )
        document.commitTransaction()
    except Exception as err:
        document.abortTransaction()
        QtGui.QMessageBox.warning(
            FreeCADGui.getMainWindow(),
            translate(
                "Part_ToleranceFeatures",
                "Tolerance Set failed",
                None,
            ),
            str(err),
        )


def getIconPath(icon_dot_svg):
    return icon_dot_svg


# -------------------------- /common stuff ------------------------------------

# -------------------------- Connect ------------------------------------------


def makeToleranceSet(name):
    """makeToleranceSet(name): makes an ToleranceSet object."""
    obj = FreeCAD.ActiveDocument.addObject("Part::FeaturePython", name)
    FeatureToleranceSet(obj)
    if FreeCAD.GuiUp:
        ViewProviderToleranceSet(obj.ViewObject)
    return obj


class FeatureToleranceSet:
    """The PartToleranceSetFeature object."""

    def __init__(self, obj):
        obj.addProperty(
            "App::PropertyLinkListGlobal",
            "Objects",
            "ToleranceSet",
            "Objects to have tolerance adjusted.",
            locked=True,
        )
        obj.addProperty(
            "App::PropertyBool",
            "Refine",
            "ToleranceSet",
            "True = refine resulting shape. False = output as is.",
            locked=True,
        )
        obj.addProperty(
            "App::PropertyLength", "minTolerance", "ToleranceSet", "0.1 nm", locked=True
        )
        obj.addProperty("App::PropertyLength", "maxTolerance", "ToleranceSet", "0", locked=True)
        obj.Refine = getParamRefine()

        obj.Proxy = self
        self.Type = "FeatureToleranceSet"

    def onDocumentRestored(self, obj):
        migrate_many_to_global(obj, "Objects")
        if not hasattr(obj, "maxTolerance"):
            obj.addProperty("App::PropertyLength", "maxTolerance", "ToleranceSet", "0", locked=True)

    def execute(self, selfobj):
        shapes = []
        for obj in selfobj.Objects:
            sh = obj.Shape.copy(True, False)
            sh.limitTolerance(selfobj.minTolerance, selfobj.maxTolerance)
            if selfobj.Refine:
                sh.fix(selfobj.minTolerance, selfobj.minTolerance, selfobj.maxTolerance)
                sh = sh.removeSplitter()
                sh.fix(selfobj.minTolerance, selfobj.minTolerance, selfobj.maxTolerance)
            shapes.append(sh)

        if len(shapes) > 1:
            rst = Part.makeCompound(shapes)
        else:
            rst = shapes[0]
        selfobj.Shape = rst


class ViewProviderToleranceSet:
    """A View Provider for the Part ToleranceSet feature."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def getIcon(self):
        return ":/icons/tools/Part_ToleranceSet.svg"

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


class CommandToleranceSet:
    """Command to create ToleranceSet feature."""

    def GetResources(self):
        return {
            "Pixmap": getIconPath("Part_ToleranceSet.svg"),
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_ToleranceSet", "Set Tolerance"),
            "Accel": "",
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_ToleranceSet",
                "Creates a parametric copy of the selected object with all contained tolerances set to at least a certain minimum value",
            ),
        }

    def Activated(self):
        if _selected_shape_objects():
            cmdCreateToleranceSetFeature(name="Tolerance")
        else:
            mb = QtGui.QMessageBox()
            mb.setIcon(mb.Icon.Warning)
            mb.setText(
                translate("Part_ToleranceSet", "Select at least one object or compounds", None)
            )
            mb.setWindowTitle(translate("Part_ToleranceSet", "Bad Selection", None))
            mb.exec_()

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and bool(_selected_shape_objects())


# -------------------------- /Connect -----------------------------------------


def addCommands():
    FreeCADGui.addCommand("Part_ToleranceSet", CommandToleranceSet())
