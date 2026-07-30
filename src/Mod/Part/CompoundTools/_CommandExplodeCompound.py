# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2016 Victor Titov (DeepSOIC) <vv.titov@gmail.com>       *
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

__title__ = "CompoundTools._CommandExplodeCompound"
__author__ = "DeepSOIC"
__url__ = "https://www.freecad.org"
__doc__ = (
    "ExplodeCompound: create a bunch of CompoundFilter objects to split a compound into pieces."
)

import FreeCAD

if FreeCAD.GuiUp:
    import FreeCADGui
    import PartGui
    from PySide import QtGui
    from PySide import QtCore

    # translation-related code
    try:
        _fromUtf8 = QtCore.QString.fromUtf8
    except Exception:

        def _fromUtf8(s):
            return s

    translate = FreeCAD.Qt.translate


# command class
def _selected_modeling_objects():
    objects = []
    for raw in FreeCADGui.Selection.getSelection():
        if not PartGui.isModelingObjectActive(raw):
            continue
        obj = PartGui.resolveModelingObject(raw)
        if obj is not None and obj not in objects:
            objects.append(obj)
    return objects


def _selected_presentation_objects():
    """Return the viewport object represented by each modeling operand."""

    objects = []
    for selected in FreeCADGui.Selection.getSelection():
        if not PartGui.isModelingObjectActive(selected):
            continue
        resolved = PartGui.resolveModelingObject(selected)
        if resolved is None:
            continue

        presentation = selected
        if not selected.isDerivedFrom("PartDesign::Body"):
            owner = resolved.getParentGeoFeatureGroup()
            if owner is not None and owner.isDerivedFrom("PartDesign::Body"):
                presentation = owner

        if presentation not in objects:
            objects.append(presentation)
    return objects


def _has_compound_operand():
    selection = _selected_modeling_objects()
    if len(selection) != 1:
        return False
    shape = getattr(selection[0], "Shape", None)
    return (
        shape is not None
        and not shape.isNull()
        and shape.ShapeType in ("Compound", "CompSolid")
    )


def _object_expression(obj):
    """Return a recorded command expression for one exact document object."""

    return (
        f"App.getDocument({obj.Document.Name!r})"
        f".getObject({obj.Name!r})"
    )


class _CommandExplodeCompound:
    "Command to explode a compound"

    def GetResources(self):
        return {
            "Pixmap": "Part_ExplodeCompound",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_ExplodeCompound", "Explode Compound"),
            "Accel": "",
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_ExplodeCompound",
                "Splits a compound into separate, independently editable Bodies",
            ),
        }

    def Activated(self):
        if _has_compound_operand():
            cmdExplode()
        else:
            mb = QtGui.QMessageBox()
            mb.setIcon(mb.Icon.Warning)
            mb.setText(
                translate("Part_ExplodeCompound", "First select a shape that is a compound.", None)
            )
            mb.setWindowTitle(translate("Part_ExplodeCompound", "Bad Selection", None))
            mb.exec_()

    def IsActive(self):
        return (
            FreeCAD.ActiveDocument is not None
            and PartGui.canStartRetainedModelingTask()
            and _has_compound_operand()
        )


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("Part_ExplodeCompound", _CommandExplodeCompound())


def cmdExplode():
    document = FreeCAD.ActiveDocument
    document.openTransaction("Explode")
    try:
        sel = _selected_modeling_objects()
        if len(sel) != 1:
            raise ValueError(
                "Bad selection",
                "More than one object selected. You have selected {num} objects.".format(
                    num=len(sel)
                ),
            )
        obj = sel[0]
        presentation = _selected_presentation_objects()
        if len(presentation) != 1:
            raise RuntimeError(
                "Explode Compound could not resolve one visible source object."
            )
        presentation_obj = presentation[0]
        replace_input = (
            presentation_obj.ViewObject is not None
            and presentation_obj.ViewObject.Visibility
        )
        FreeCADGui.addModule("CompoundTools.Explode")
        source_expression = _object_expression(obj)
        replaced_expression = (
            _object_expression(presentation_obj)
            if replace_input
            else ""
        )
        output_component = FreeCADGui.runDocumentObjectCommand(
            document,
            "CompoundTools.Explode.makeBodyOutputOperation("
            f"{source_expression}, "
            "label='Explode Compound', "
            f"replaced_inputs=[{replaced_expression}])",
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
                "Explode Compound did not return its exact History operation."
            )
    except Exception as ex:
        if document.HasPendingTransaction:
            document.abortTransaction()
        FreeCAD.Console.PrintError("{}\n".format(ex))
        return

    if document.HasPendingTransaction:
        document.commitTransaction()
