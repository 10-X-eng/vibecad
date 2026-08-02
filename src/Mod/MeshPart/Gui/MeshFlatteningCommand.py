# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2017 Lorenz Lechner                                     *
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

import Mesh
import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartGui
import MeshPartGui  # noqa: F401 - registers MeshPart GUI types

from PySide.QtCore import QT_TRANSLATE_NOOP  # for translations
from VibeCADNativeTransaction import _OwnedDocumentTransaction


_TIMELINE_ROLE = "VibeCADTimelineRole"
_TIMELINE_OWNER = "VibeCADTimelineOwner"


def _ensure_hidden_property(obj, type_id, name, description):
    if name in obj.PropertiesList:
        actual_type = obj.getTypeIdOfProperty(name)
        if actual_type != type_id:
            raise TypeError(f"{obj.Name}.{name} must be {type_id}, not {actual_type}")
    else:
        obj.addProperty(
            type_id,
            name,
            "Timeline",
            description,
            attr=16,
            hidden=True,
            locked=True,
        )
    obj.setPropertyStatus(
        name,
        ("Hidden", "LockDynamic", "NoRecompute"),
    )
    obj.setEditorMode(name, 2)


def _live_source(source, document):
    if (
        source is None
        or document is None
        or getattr(source, "Document", None) is not document
    ):
        return False
    try:
        return (
            App.getDocument(document.Name) is document
            and document.getObject(source.Name) is source
            and PartGui.isModelingObjectActive(source)
        )
    except (NameError, ReferenceError, RuntimeError):
        return False


def _add_property(obj, type_id, name, group, description, value):
    if name not in obj.PropertiesList:
        obj.addProperty(type_id, name, group, description)
    setattr(obj, name, value)


def _linked_object(value):
    if isinstance(value, tuple):
        return value[0]
    return value


class _FlatMeshBoundary:
    """One recomputable boundary produced by the native flatmesh solver."""

    Type = "MeshPart::FlatMeshBoundary"

    def __init__(self, obj):
        obj.Proxy = self
        obj.addExtension("App::SuppressibleExtensionPython")
        _add_property(
            obj,
            "App::PropertyLink",
            "Source",
            "Unwrap",
            "Linked source mesh",
            None,
        )
        _add_property(
            obj,
            "App::PropertyIntegerConstraint",
            "BoundaryIndex",
            "Unwrap",
            "Zero-based boundary returned by the unwrap solver",
            (0, 0, 1000000, 1),
        )
        _add_property(
            obj,
            "App::PropertyIntegerConstraint",
            "Iterations",
            "Unwrap",
            "Number of flatmesh solver passes",
            (5, 1, 1000, 1),
        )
        _add_property(
            obj,
            "App::PropertyFloat",
            "Flatness",
            "Unwrap",
            "Flatmesh convergence target",
            0.95,
        )

    def execute(self, obj):
        if obj.Suppressed:
            obj.Shape = Part.Shape()
            return
        source = obj.Source
        document = getattr(obj, "Document", None)
        if (
            not _live_source(source, document)
            or not isinstance(getattr(source, "Mesh", None), Mesh.Mesh)
        ):
            obj.Shape = Part.Shape()
            raise RuntimeError(
                "Unwrap Mesh requires its live linked source mesh"
            )
        if obj.Iterations < 1 or not 0.0 < obj.Flatness <= 1.0:
            obj.Shape = Part.Shape()
            raise ValueError(
                "Iterations must be positive and Flatness must be in (0, 1]"
            )

        import flatmesh
        import numpy as np

        points = np.array(
            [[point.x, point.y, point.z] for point in source.Mesh.Points]
        )
        faces = np.array([list(face) for face in source.Mesh.Topology[1]])
        flattener = flatmesh.FaceUnwrapper(points, faces)
        flattener.findFlatNodes(obj.Iterations, obj.Flatness)
        boundaries = flattener.getFlatBoundaryNodes()
        index = obj.BoundaryIndex
        if index < 0 or index >= len(boundaries):
            obj.Shape = Part.Shape()
            raise RuntimeError(
                "The linked mesh no longer produces this unwrap boundary"
            )
        polygon = Part.makePolygon(
            [App.Vector(*node) for node in boundaries[index]]
        )
        result = Part.Wire(polygon)
        if result.isNull() or not result.isValid():
            obj.Shape = Part.Shape()
            raise RuntimeError("Unwrap Mesh produced an invalid boundary")
        obj.Shape = result


class _FlatFace:
    """Recomputable flattened surface linked to one exact source face."""

    Type = "MeshPart::FlatFace"

    def __init__(self, obj):
        obj.Proxy = self
        obj.addExtension("App::SuppressibleExtensionPython")
        _add_property(
            obj,
            "App::PropertyLinkSub",
            "Source",
            "Unwrap",
            "Exact source face",
            None,
        )
        _add_property(
            obj,
            "App::PropertyLength",
            "TessellationTolerance",
            "Unwrap",
            "Surface tessellation tolerance used by flatmesh",
            0.01,
        )
        _add_property(
            obj,
            "App::PropertyIntegerConstraint",
            "Iterations",
            "Unwrap",
            "Number of flatmesh solver passes",
            (5, 1, 1000, 1),
        )
        _add_property(
            obj,
            "App::PropertyFloat",
            "Flatness",
            "Unwrap",
            "Flatmesh convergence target",
            0.99,
        )
        _add_property(
            obj,
            "App::PropertyIntegerConstraint",
            "UDegree",
            "Surface",
            "Flattened B-spline degree in U",
            (3, 1, 25, 1),
        )
        _add_property(
            obj,
            "App::PropertyIntegerConstraint",
            "VDegree",
            "Surface",
            "Flattened B-spline degree in V",
            (3, 1, 25, 1),
        )
        _add_property(
            obj,
            "App::PropertyIntegerConstraint",
            "MaximumSegments",
            "Surface",
            "Maximum B-spline segmentation",
            (10, 1, 1000, 1),
        )

    def execute(self, obj):
        if obj.Suppressed:
            obj.Shape = Part.Shape()
            return
        source_value = obj.Source
        source = _linked_object(source_value)
        subelements = source_value[1] if isinstance(source_value, tuple) else ()
        document = getattr(obj, "Document", None)
        if (
            not _live_source(source, document)
            or len(subelements) != 1
            or not subelements[0]
        ):
            obj.Shape = Part.Shape()
            raise RuntimeError(
                "Unwrap Face requires one live linked source face"
            )
        selected = source.getSubObject(subelements[0])
        if not isinstance(selected, Part.Face):
            obj.Shape = Part.Shape()
            raise RuntimeError("The linked unwrap subelement is not a face")
        if (
            obj.TessellationTolerance <= 0.0
            or obj.Iterations < 1
            or not 0.0 < obj.Flatness <= 1.0
        ):
            obj.Shape = Part.Shape()
            raise ValueError("Unwrap Face settings are outside their valid range")

        import flatmesh

        face = selected.toNurbs().Faces[0]
        surface = face.Surface
        surface.setUNotPeriodic()
        surface.setVNotPeriodic()
        bspline = surface.toBSpline(
            1,
            "C0",
            "C0",
            obj.UDegree,
            obj.VDegree,
            obj.MaximumSegments,
        )
        face = bspline.toShape()
        face.tessellate(obj.TessellationTolerance)
        flattener = flatmesh.FaceUnwrapper(face)
        flattener.findFlatNodes(obj.Iterations, obj.Flatness)
        poles = flattener.interpolateFlatFace(face)
        u_count = len(bspline.getPoles())
        v_count = len(bspline.getPoles()[0])
        if len(poles) != u_count * v_count:
            obj.Shape = Part.Shape()
            raise RuntimeError(
                "Unwrap Face returned the wrong number of surface poles"
            )
        index = 0
        for u_value in range(u_count):
            for v_value in range(v_count):
                bspline.setPole(
                    u_value + 1,
                    v_value + 1,
                    App.Vector(poles[index]),
                )
                index += 1
        result = bspline.toShape()
        if result.isNull() or not result.isValid():
            obj.Shape = Part.Shape()
            raise RuntimeError("Unwrap Face produced an invalid surface")
        obj.Shape = result


def _mark_source_preserving_outputs(outputs, source):
    """Present one unwrap command as one durable document-history operation."""

    outputs = list(outputs)
    document = getattr(source, "Document", None)
    if (
        not outputs
        or document is None
        or document.getObject(source.Name) is not source
        or any(
            output is None
            or getattr(output, "Document", None) is not document
            or document.getObject(output.Name) is not output
            for output in outputs
        )
        or len({output.Name for output in outputs}) != len(outputs)
    ):
        raise RuntimeError(
            "An unwrap command must produce distinct live outputs in its "
            "source document"
        )

    operation = outputs[-1]
    if "Source" not in operation.PropertiesList:
        _ensure_hidden_property(
            operation,
            "App::PropertyLinkHidden",
            "Source",
            "Geometry unwrapped by this operation",
        )
        operation.Source = source
    elif _linked_object(operation.Source) is not source:
        raise RuntimeError(
            "An unwrap operation must retain its exact source link"
        )

    document.publishProvisionalTimelineOperationBlock(
        operation,
        outputs[:-1],
    )
    return operation


def _begin_unwrap(document, label):
    return _OwnedDocumentTransaction(document, label)


def _finish_unwrap(document, transaction, outputs):
    try:
        document.recompute()
        if any(
            not output.isValid()
            or output.Shape.isNull()
            or not output.Shape.isValid()
            for output in outputs
        ):
            raise RuntimeError(
                "The linked unwrap features did not recompute successfully"
            )
        transaction.commit()
    except Exception:
        transaction.abort()
        raise


def _abort_unwrap(transaction):
    transaction.abort()


class BaseCommand(object):
    def __init__(self):
        pass

    def IsActive(self):
        return (
            App.ActiveDocument is not None
            and App.ActiveDocument.getBookedTransactionID() == 0
            and not App.ActiveDocument.HasPendingTransaction
        )


class CreateFlatMesh(BaseCommand):
    """create flat wires from a meshed face"""

    def GetResources(self):
        return {
            "Pixmap": "MeshPart_CreateFlatMesh.svg",
            "MenuText": QT_TRANSLATE_NOOP("MeshPart_CreateFlatMesh", "Unwrap Mesh"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "MeshPart_CreateFlatMesh", "Finds a flat representation of a mesh"
            ),
        }

    def Activated(self):
        import numpy as np
        import flatmesh

        selected = Gui.Selection.getSelection()
        if len(selected) != 1:
            return
        obj = selected[0]
        document = getattr(obj, "Document", None)
        if document is None or document is not App.ActiveDocument:
            return

        points = np.array([[i.x, i.y, i.z] for i in obj.Mesh.Points])
        faces = np.array([list(i) for i in obj.Mesh.Topology[1]])
        flattener = flatmesh.FaceUnwrapper(points, faces)
        flattener.findFlatNodes(5, 0.95)
        boundaries = flattener.getFlatBoundaryNodes()
        if not _live_source(obj, document):
            raise RuntimeError("The selected mesh changed before it could be unwrapped")
        transaction = _begin_unwrap(document, "Unwrap mesh")
        try:
            outputs = []
            for index, _edge in enumerate(boundaries, start=1):
                output = document.addObject(
                    "Part::FeaturePython",
                    "UnwrappedMeshBoundary",
                )
                _FlatMeshBoundary(output)
                output.Label = f"Unwrapped Mesh Boundary {index}"
                output.Source = obj
                output.BoundaryIndex = index - 1
                outputs.append(output)
            if not outputs:
                raise RuntimeError("Unwrap Mesh produced no boundary geometry")
            operation = _mark_source_preserving_outputs(outputs, obj)
            operation.Label = "Unwrapped Mesh"
        except Exception:
            _abort_unwrap(transaction)
            raise
        _finish_unwrap(document, transaction, outputs)

    def IsActive(self):
        if not super(CreateFlatMesh, self).IsActive():
            return False
        selected = Gui.Selection.getSelection()
        return (
            len(selected) == 1
            and getattr(selected[0], "Document", None) is App.ActiveDocument
            and isinstance(getattr(selected[0], "Mesh", None), Mesh.Mesh)
            and _live_source(selected[0], App.ActiveDocument)
        )


class CreateFlatFace(BaseCommand):
    """create a flat face from a single face
    only full faces are supported right now"""

    def GetResources(self):
        return {
            "Pixmap": "MeshPart_CreateFlatFace.svg",
            "MenuText": QT_TRANSLATE_NOOP("MeshPart_CreateFlatFace", "Unwrap Face"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "MeshPart_CreateFlatFace", "Finds a flat representation of a face"
            ),
        }

    def Activated(self):
        import flatmesh

        selected = Gui.Selection.getSelectionEx()
        if (
            len(selected) != 1
            or len(selected[0].SubObjects) != 1
            or len(selected[0].SubElementNames) != 1
        ):
            return
        source = selected[0].Object
        document = getattr(source, "Document", None)
        if document is None or document is not App.ActiveDocument:
            return
        if not _live_source(source, document):
            raise RuntimeError("The selected face changed before it could be unwrapped")
        transaction = _begin_unwrap(document, "Unwrap face")
        try:
            output = document.addObject(
                "Part::FeaturePython",
                "UnwrappedFace",
            )
            _FlatFace(output)
            output.Label = "Unwrapped Face"
            output.Source = (
                source,
                [selected[0].SubElementNames[0]],
            )
            _mark_source_preserving_outputs([output], source)
        except Exception:
            _abort_unwrap(transaction)
            raise
        _finish_unwrap(document, transaction, [output])

    def IsActive(self):
        if not super(CreateFlatFace, self).IsActive():
            return False
        selected = Gui.Selection.getSelectionEx()
        return (
            len(selected) == 1
            and getattr(selected[0].Object, "Document", None) is App.ActiveDocument
            and len(selected[0].SubObjects) == 1
            and len(selected[0].SubElementNames) == 1
            and isinstance(selected[0].SubObjects[0], Part.Face)
            and _live_source(selected[0].Object, App.ActiveDocument)
        )


# Test if pybind11 dependency is available
try:
    import flatmesh  # noqa: F401 - optional command availability probe

    Gui.addCommand("MeshPart_CreateFlatMesh", CreateFlatMesh())
    Gui.addCommand("MeshPart_CreateFlatFace", CreateFlatFace())
except ImportError:
    App.Console.PrintLog("flatmesh-commands are not available\n")
    App.Console.PrintLog("flatmesh needs pybind11 as build dependency\n")
except AttributeError:
    # Can happen when running FreeCAD in headless mode
    pass
