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

__title__ = "CompoundTools._CommandCompoundFilter"
__author__ = "DeepSOIC, Bernd Hahnebach"
__url__ = "https://www.freecad.org"
__doc__ = "Compound Filter: remove some children from a compound (features)."


import FreeCAD

if FreeCAD.GuiUp:
    import FreeCADGui
    import PartGui
    from PySide import QtGui
    from PySide import QtCore

    # translation-related code
    # (see forum thread "A new Part tool is being born... JoinFeatures!"
    # https://forum.freecad.org/viewtopic.php?f=22&t=11112&start=30#p90239 )
    try:
        _fromUtf8 = QtCore.QString.fromUtf8
    except Exception:

        def _fromUtf8(s):
            return s

    translate = FreeCAD.Qt.translate


# command class
def _is_shape_object(obj):
    shape = getattr(obj, "Shape", None)
    return shape is not None and not shape.isNull()


def _selected_modeling_objects():
    objects = []
    for raw in FreeCADGui.Selection.getSelection():
        if not PartGui.isModelingObjectActive(raw):
            continue
        obj = PartGui.resolveModelingObject(raw)
        if obj is not None and obj not in objects:
            objects.append(obj)
    return objects


def _has_filter_operands():
    selection = _selected_modeling_objects()
    return (
        len(selection) in (1, 2)
        and _is_shape_object(selection[0])
        and selection[0].Shape.ShapeType in ("Compound", "CompSolid")
        and (len(selection) == 1 or _is_shape_object(selection[1]))
    )


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


class _CommandCompoundFilter:
    "Command to create CompoundFilter feature"

    def GetResources(self):
        return {
            "Pixmap": "Part_CompoundFilter",
            "MenuText": QtCore.QT_TRANSLATE_NOOP("Part_CompoundFilter", "Compound Filter"),
            "Accel": "",
            "ToolTip": QtCore.QT_TRANSLATE_NOOP(
                "Part_CompoundFilter",
                "Filters out objects from the selected compound "
                "by characteristics like volume,\n"
                "area, or length, or by choosing specific items.\n"
                "If a second object is selected, it will be used "
                "as reference, for example,\n"
                "for collision or distance filtering.",
            ),
        }

    def Activated(self):
        if _has_filter_operands():
            cmdCreateCompoundFilter(name="CompoundFilter")
        else:
            mb = QtGui.QMessageBox()
            mb.setIcon(mb.Icon.Warning)
            mb.setText(
                translate(
                    "Part_CompoundFilter",
                    "First select a shape that is a compound. "
                    "If a second object is selected (optional) "
                    "it will be treated as a stencil.",
                    None,
                )
            )
            mb.setWindowTitle(translate("Part_CompoundFilter", "Bad Selection", None))
            mb.exec_()

    def IsActive(self):
        return (
            FreeCAD.ActiveDocument is not None
            and PartGui.canStartRetainedModelingTask()
            and _has_filter_operands()
        )


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("Part_CompoundFilter", _CommandCompoundFilter())


# helper
def cmdCreateCompoundFilter(name):
    document = FreeCAD.ActiveDocument
    sel = _selected_modeling_objects()
    presentations = _visible_presentations(sel)
    document.openTransaction("Create CompoundFilter")
    try:
        FreeCADGui.addModule("CompoundTools.CompoundFilter")
        result = FreeCADGui.runDocumentObjectCommand(
            document,
            "CompoundTools.CompoundFilter."
            f"makeCompoundFilter(name={name!r})",
            "Part::Feature",
        )
        result_expression = _object_expression(result)
        FreeCADGui.doCommand(
            f"{result_expression}.Base = {_object_expression(sel[0])}"
        )
        if len(sel) == 2:
            FreeCADGui.doCommand(
                f"{result_expression}.Stencil = "
                f"{_object_expression(sel[1])}"
            )
            FreeCADGui.doCommand(
                f"{result_expression}.FilterType = 'collision-pass'"
            )
        else:
            FreeCADGui.doCommand(
                f"{result_expression}.FilterType = 'window-volume'"
            )

        FreeCADGui.doCommand(
            f"{result_expression}.Proxy.execute({result_expression})"
        )
        FreeCADGui.doCommand(f"{result_expression}.purgeTouched()")
        if result.Shape.isNull() or not result.Shape.isValid():
            raise RuntimeError(
                "Compound Filter did not produce valid geometry"
            )

        presentation_expression = ", ".join(
            _object_expression(presentation)
            for presentation in presentations
        )
        FreeCADGui.addModule(
            "CompoundTools._CommandCompoundFilter"
        )
        FreeCADGui.doCommand(
            "CompoundTools._CommandCompoundFilter."
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
                "Part_CompoundFilter",
                "Compound Filter failed",
                None,
            ),
            str(err),
        )
