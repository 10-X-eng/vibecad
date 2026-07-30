# SPDX-License-Identifier: LGPL-2.1-or-later

# ***************************************************************************
# *   Copyright (c) 2016 sliptonic <shopinthewoods@gmail.com>               *
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

import FreeCAD
import Part
import Path
import Path.Base.Util as PathUtil
import Path.Main.Job as PathJob

import Path.Dressup.Utils as PathDressup
import PathScripts.PathUtils as PathUtils
from Path.CommandBoundary import (
    ExactDocumentObjectIdentity,
    can_start_document_command,
    is_timeline_input_usable,
)
from VibeCADNativeTransaction import _OwnedDocumentTransaction

from PySide.QtCore import QT_TRANSLATE_NOOP

if FreeCAD.GuiUp:
    import FreeCADGui

translate = FreeCAD.Qt.translate

__title__ = "FreeCAD Path Commands"
__author__ = "sliptonic"
__url__ = "https://www.freecad.org"


def _validated_parent_job(operation, document):
    job = PathUtils.findParentJob(operation)
    if (
        job is None
        or job.Document is not document
        or not isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob)
        or getattr(job, "Operations", None) is None
        or not is_timeline_input_usable(job, document)
    ):
        return None
    return job


def _selected_toggle_operations():
    document = FreeCAD.ActiveDocument
    selection = FreeCADGui.Selection.getSelection()
    if document is None or not selection:
        return []

    if len(selection) == 1 and hasattr(selection[0], "Group"):
        selected_group = selection[0]
        if selected_group.Document is document and isinstance(
            getattr(selected_group, "Proxy", None),
            PathJob.ObjectJob,
        ):
            selection = list(selected_group.Operations.Group)
        else:
            parent_job = PathUtils.findParentJob(selected_group)
            if (
                parent_job is not None
                and parent_job.Document is document
                and isinstance(
                    getattr(parent_job, "Proxy", None),
                    PathJob.ObjectJob,
                )
                and getattr(parent_job, "Operations", None) is selected_group
            ):
                selection = list(selected_group.Group)

    operations = []
    for selected in selection:
        operation = PathDressup.baseOp(selected)
        if (
            operation is None
            or operation.Document is not document
            or not is_timeline_input_usable(operation, document)
            or not hasattr(operation, "Active")
            or _validated_parent_job(operation, document) is None
        ):
            return []
        if operation not in operations:
            operations.append(operation)
    return operations


def _selected_copy_operations():
    document = FreeCAD.ActiveDocument
    selection = FreeCADGui.Selection.getSelection()
    if document is None or not selection:
        return []

    operations = []
    for selected in selection:
        job = _validated_parent_job(selected, document)
        timeline_operation = (
            "VibeCADTimelineRole" in selected.PropertiesList
            and str(selected.VibeCADTimelineRole) == "operation"
        )
        if (
            selected.Document is not document
            or not is_timeline_input_usable(selected, document)
            or job is None
            or selected not in job.Operations.Group
            or not (PathDressup.isOp(selected) or timeline_operation)
        ):
            return []
        operations.append((selected, job))
    return operations


def _recompute_and_validate(document, operations):
    document.recompute()
    if any(
        operation.Document is not document or not operation.isValid() for operation in operations
    ):
        raise RuntimeError("A CAM operation is invalid")


def _remove_copied_timeline_replacement(operation):
    """Make a copied operation source-preserving.

    A copy is a new sibling result.  It must never inherit the source
    operation's promise to replace or reveal an earlier object when the copy
    is moved through history or deleted.
    """
    property_name = "VibeCADTimelineReplacedInputs"
    if property_name not in operation.PropertiesList:
        return
    if operation.getTypeIdOfProperty(property_name) != "App::PropertyLinkListHidden":
        raise RuntimeError(f"{operation.Name} has invalid CAM replacement metadata")

    operation.setPropertyStatus(property_name, "-LockDynamic")
    if not operation.removeProperty(property_name):
        raise RuntimeError(f"{operation.Name} retained copied CAM replacement metadata")


def _apply_copied_timeline_contract(
    document,
    copied_source_order,
    copied_outputs,
):
    """Finalize one complete copied semantic closure."""
    copied_objects = list(copied_source_order)
    exact_outputs = list(copied_outputs)
    copied_names = [str(copied.Name) for copied in copied_objects]
    ordered_names = [str(copied.Name) for copied in copied_source_order]
    output_names = [str(output.Name) for output in exact_outputs]
    if (
        not copied_objects
        or not exact_outputs
        or len(copied_names) != len(set(copied_names))
        or len(ordered_names) != len(set(ordered_names))
        or set(ordered_names) != set(copied_names)
        or len(output_names) != len(set(output_names))
        or any(not any(output is copied for copied in copied_objects) for output in exact_outputs)
    ):
        raise RuntimeError("The copied CAM graph has no exact source chronology")

    for copied in copied_objects:
        _remove_copied_timeline_replacement(copied)

    if len(exact_outputs) == 1:
        operation = exact_outputs[0]
        for resource in copied_objects:
            if resource is operation:
                continue
            PathUtil.markTimelineResource(resource, operation)
            if resource.ViewObject:
                resource.ViewObject.Visibility = False
        PathUtil.markTimelineOperation(operation)
        return operation, list(copied_source_order)

    controller = PathUtil.createTimelineOperationController(
        document,
        "CAMOperationCopy",
        translate("CAM_OperationCopy", "Copied CAM Operations"),
        "Copy CAM operations",
        exact_outputs,
    )
    for resource in copied_objects:
        PathUtil.markTimelineResource(resource, controller)
        if not any(resource is output for output in exact_outputs) and resource.ViewObject:
            resource.ViewObject.Visibility = False
    return controller, [*copied_source_order, controller]


class _CommandSelectLoop:
    "the Path command to complete loop selection definition"

    def GetResources(self):
        return {
            "Pixmap": "CAM_SelectLoop",
            "MenuText": QT_TRANSLATE_NOOP("CAM_SelectLoop", "Finish Selecting Loop"),
            "Accel": "P, L",
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_SelectLoop",
                "Completes the selection of edges or faces that forms a loop"
                "\n\nSelect vertical faces: searching loops faces which forms the walls."
                "\n\nSelect horizontal face: searching inner edges of the face or coplanar faces."
                "\n\nSelect one edge: searching loop edges in horizontal plane"
                "\nor wire which contain selected edge."
                "\n\nSelect two edges: searching loop edges in wires of the shape"
                "\nor tangent edges."
                "\n\nSelect three or more edges: searching horizontal wires."
                "\n\nWithout sub selection all edges of the shape will be selected.",
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        selection = FreeCADGui.Selection.getSelectionEx()
        if not selection:
            return False

        document = FreeCAD.ActiveDocument
        return all(
            item.Object is not None
            and item.Object.Document is document
            and item.Object.isDerivedFrom("Part::Feature")
            and is_timeline_input_usable(
                item.Object,
                document,
            )
            for item in selection
        )

    def Activated(self):
        if not self.IsActive():
            return

        selection = FreeCADGui.Selection.getSelectionEx()
        if not selection:
            return
        if any(not s.Object.isDerivedFrom("Part::Feature") for s in selection):
            return

        newSelection = []
        for sel in selection:
            obj = sel.Object
            subs = sel.SubObjects
            edges = None
            names = None

            if not sel.SubObjects:
                names = [f"Edge{i}" for i in range(1, len(obj.Shape.Edges) + 1)]

            elif all(isinstance(sub, Part.Face) for sub in subs):
                # face(s) selected
                edges = PathUtils.innerEdgesFromFace(obj, subs[0])
                if not edges:
                    if all(Path.Geom.isVertical(face) for face in subs):
                        names = PathUtils.horizontalFaceLoops(obj, subs)
                    elif Path.Geom.isHorizontal(subs[0]):
                        names = PathUtils.horizontalFacesAtHeight(obj, subs[0].CenterOfMass.z)
                    if not names:
                        edges = [e for sub in subs for e in sub.Edges]

            elif isinstance(subs[0], Part.Edge):
                if len(subs) == 1:
                    # one edge selected: searching horizontal edge loop
                    edges = PathUtils.horizontalEdgeLoop(obj, subs[0])
                elif len(subs) == 2:
                    # two edges selected: searching wire in shape which contain both edges
                    edges = PathUtils.loopdetect(obj, subs[0], subs[1])
                    if not edges:
                        # two edges selected: searching edges in tangency
                        edges = PathUtils.tangentEdgeLoop(obj, subs[0], subs[1])

                if not edges:
                    # searching all horizontal wires which contains selected edges
                    edges = PathUtils.wiresdetect(obj, subs)

            if edges and not names:
                hashList = [e.hashCode() for e in edges]
                objEdges = obj.Shape.Edges
                names = [f"Edge{i}" for i, e in enumerate(objEdges, 1) if e.hashCode() in hashList]

            if names:
                newSelection.append((obj, names))
            else:
                Path.Log.warning(
                    translate(
                        "CAM_SelectLoop",
                        "Closed loop detection failed in model %s."
                        " This type of selection not supported yet." % obj.Label,
                    )
                )

        if newSelection:
            FreeCADGui.Selection.clearSelection()
            for obj, names in newSelection:
                FreeCADGui.Selection.addSelection(obj, names)


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("CAM_SelectLoop", _CommandSelectLoop())


class _ToggleOperation:
    "command definition to toggle Operation Active state"

    def GetResources(self):
        return {
            "Pixmap": "CAM_OpActive",
            "MenuText": QT_TRANSLATE_NOOP("CAM_OpActiveToggle", "Toggle Operation"),
            "Accel": "P, X",
            "ToolTip": QT_TRANSLATE_NOOP(
                "CAM_OpActiveToggle", "Toggles the active state of the operation"
            ),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        return bool(_selected_toggle_operations())

    def Activated(self):
        document = FreeCAD.ActiveDocument
        if document is None or not can_start_document_command(document):
            return

        operations = _selected_toggle_operations()
        if not operations:
            return
        transaction = _OwnedDocumentTransaction(
            document,
            "Toggle CAM operations",
        )
        try:
            identities = [
                ExactDocumentObjectIdentity(operation, document) for operation in operations
            ]
            operations = [identity.resolve(require_timeline=True) for identity in identities]
            states = [operation.Active for operation in operations]
            if all(states) or not any(states):
                for operation in operations:
                    operation.Active = not operation.Active
            else:
                for operation in operations:
                    operation.Active = True

            _recompute_and_validate(document, operations)
        except Exception:
            transaction.abort()
            raise
        transaction.commit()


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("CAM_OpActiveToggle", _ToggleOperation())


class _CopyOperation:
    "the Path Copy Operation command definition"

    def GetResources(self):
        return {
            "Pixmap": "CAM_OpCopy",
            "MenuText": QT_TRANSLATE_NOOP("CAM_OperationCopy", "Copy Operation"),
            "ToolTip": QT_TRANSLATE_NOOP("CAM_OperationCopy", "Copies the operation in the job"),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        if not can_start_document_command():
            return False
        return bool(_selected_copy_operations())

    def Activated(self):
        document = FreeCAD.ActiveDocument
        if document is None or not can_start_document_command(document):
            return

        selection = _selected_copy_operations()
        if not selection:
            return
        selected_identities = [
            (
                ExactDocumentObjectIdentity(selected, document),
                ExactDocumentObjectIdentity(job, document),
            )
            for selected, job in selection
        ]

        timeline = document.getObject("VibeCADTimeline")
        if timeline is None:
            raise RuntimeError("The CAM document has no operation timeline")
        source_operations = list(timeline.Operations)
        source_position = int(timeline.Position)
        source_indices = {}
        for index, operation in enumerate(source_operations):
            name = str(operation.Name)
            if not name or document.getObject(name) is not operation or name in source_indices:
                raise RuntimeError("The CAM source timeline is inconsistent")
            source_indices[name] = (operation, index)

        transaction = _OwnedDocumentTransaction(
            document,
            "Copy CAM operations",
        )
        try:
            selection = [
                (
                    selected_identity.resolve(require_timeline=True),
                    job_identity.resolve(require_timeline=True),
                )
                for selected_identity, job_identity in selected_identities
            ]
            selected_sources = [selected for selected, _job in selection]
            if any(
                selected is other
                for index, selected in enumerate(selected_sources)
                for other in selected_sources[index + 1 :]
            ):
                raise RuntimeError("A CAM operation can only be copied once per command")
            source_closure = list(
                document.semanticTimelineCopyClosure(
                    selected_sources,
                )
            )
            if not source_closure:
                raise RuntimeError("The selected CAM operations have no history closure")
            for source in source_closure:
                source_name = str(source.Name)
                source_entry = source_indices.get(source_name)
                if (
                    source_entry is None
                    or source_entry[0] is not source
                    or source_entry[1] >= source_position
                ):
                    raise RuntimeError(
                        "The selected CAM operation has an inactive or "
                        "incomplete source-timeline closure"
                    )

            copied_source_order = list(document.copyObject(source_closure, False))
            for selected_identity, job_identity in selected_identities:
                selected_identity.resolve(require_timeline=True)
                job_identity.resolve(require_timeline=True)
            if len(copied_source_order) != len(source_closure):
                raise RuntimeError("Could not copy the complete CAM history closure")
            source_copy_pairs = list(zip(source_closure, copied_source_order))
            if (
                len(source_copy_pairs) != len(source_closure)
                or len({str(copied.Name) for copied in copied_source_order})
                != len(copied_source_order)
                or any(
                    copied.Document is not document or document.getObject(copied.Name) is not copied
                    for copied in copied_source_order
                )
            ):
                raise RuntimeError("The copied CAM history closure is incomplete")

            def copied_for(source):
                for exact_source, copied in source_copy_pairs:
                    if exact_source is source:
                        return copied
                raise RuntimeError(
                    "The selected CAM output is absent from its copied " "history closure"
                )

            selected_entries = sorted(
                (
                    source_indices[str(selected.Name)][1],
                    selected,
                    job,
                    copied_for(selected),
                )
                for selected, job in selection
            )
            copied_outputs = []
            for _index, _source, job, copied_output in selected_entries:
                if (
                    job.Document is not document
                    or document.getObject(job.Name) is not job
                    or getattr(job, "Operations", None) is None
                    or not isinstance(
                        getattr(job, "Proxy", None),
                        PathJob.ObjectJob,
                    )
                ):
                    raise RuntimeError("The selected CAM job is no longer available")
                job.Proxy.addOperation(copied_output)
                if (
                    copied_output not in job.Operations.Group
                    or PathUtils.findParentJob(copied_output) is not job
                ):
                    raise RuntimeError("Could not add the copied operation to its CAM job")
                copied_outputs.append(copied_output)

            (
                timeline_operation,
                adoption_order,
            ) = _apply_copied_timeline_contract(
                document,
                copied_source_order,
                copied_outputs,
            )
            created = list(copied_source_order)
            if timeline_operation not in created:
                created.append(timeline_operation)
            document.adoptImportedTimelineOperations(
                adoption_order,
                adoption_order,
            )
            _recompute_and_validate(document, created)
        except Exception:
            transaction.abort()
            raise
        transaction.commit()


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("CAM_OperationCopy", _CopyOperation())


class _CAMSubshapeResource:
    """Persistent parametric source for a selected CAM face or edge."""

    def __init__(self, obj, source, subname, subtype=None):
        obj.addProperty(
            "App::PropertyLinkSub",
            "Source",
            "CAM",
            "Exact model subelement consumed by this CAM operation",
        )
        obj.addProperty(
            "App::PropertyString",
            "SubshapeCollection",
            "CAM",
            "Optional containing shape collection used for extraction",
        )
        obj.Source = (source, [subname])
        obj.SubshapeCollection = subtype or ""
        obj.Proxy = self

    def dumps(self):
        return None

    def loads(self, _state):
        return None

    def onDocumentRestored(self, obj):
        self.obj = obj

    def execute(self, obj):
        obj.Shape = Part.Shape()
        source_link = obj.Source
        if not source_link or not isinstance(source_link, tuple) or len(source_link) != 2:
            raise RuntimeError("The CAM subshape source link is missing")
        source, subnames = source_link
        document = obj.Document
        if (
            source is None
            or source.Document is not document
            or document.getObject(source.Name) is not source
            or not source.isValid()
            or len(subnames) != 1
        ):
            raise RuntimeError("The CAM subshape source identity is invalid")
        shape = findShape(
            source.Shape,
            str(subnames[0]),
            str(obj.SubshapeCollection) or None,
        )
        if shape is None or shape.isNull():
            raise RuntimeError("The selected CAM subshape no longer exists")
        obj.Shape = shape.copy()


def createSubshapeResource(
    document,
    source,
    subname,
    subtype=None,
    name="CAMSubshape",
):
    """Create one exact, recomputing resource for a selected model subshape."""

    if (
        document is None
        or source is None
        or source.Document is not document
        or document.getObject(source.Name) is not source
        or not source.isValid()
        or not is_timeline_input_usable(source, document)
        or not hasattr(source, "Shape")
        or source.Shape.isNull()
        or not (str(subname).startswith("Face") or str(subname).startswith("Edge"))
        or subtype not in (None, "Wires")
    ):
        raise RuntimeError("A CAM subshape resource requires one live face or edge")
    resource = document.addObject("Part::FeaturePython", str(name))
    if resource is None:
        raise RuntimeError("The CAM subshape resource was not created")
    _CAMSubshapeResource(
        resource,
        source,
        str(subname),
        subtype,
    )
    if FreeCAD.GuiUp:
        resource.ViewObject.Proxy = 0
    return resource


# \c findShape() is referenced from Gui/Command.cpp and used by Path.Area commands.
# Do not remove!
def findShape(shape, subname=None, subtype=None):
    """To find a higher order shape containing the subshape with subname.
    E.g. to find the wire containing 'Edge1' in shape,
        findShape(shape,'Edge1','Wires')
    """
    if not subname:
        return shape
    ret = shape.getElement(subname)
    if not subtype or not ret or ret.isNull():
        return ret
    if subname.startswith("Face"):
        tp = "Faces"
    elif subname.startswith("Edge"):
        tp = "Edges"
    elif subname.startswith("Vertex"):
        tp = "Vertex"
    else:
        return ret
    for obj in getattr(shape, subtype):
        for sobj in getattr(obj, tp):
            if sobj.isEqual(ret):
                return obj
    return ret
