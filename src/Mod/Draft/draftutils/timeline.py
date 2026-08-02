# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit document-timeline contracts for accepted Draft GUI commands.

Draft's public modeling functions intentionally keep their historic behavior.
The GUI uses the helpers in this module when one human command replaces
visible input geometry or produces several implementation objects which must
appear as one semantic operation in VibeCAD's document-wide history.
"""

import FreeCAD as App

from draftutils.transaction import object_is_usable_at_current_position


_ROLE = "VibeCADTimelineRole"
_OWNER = "VibeCADTimelineOwner"
_REPLACED_INPUTS = "VibeCADTimelineReplacedInputs"


class _SelectionReference:
    """Minimal immutable selection record consumed by Draft geometry code."""

    def __init__(self, obj, subelement_names):
        self.Object = obj
        self.SubElementNames = tuple(subelement_names)

    def isDerivedFrom(self, type_id):
        return type_id == "Gui::SelectionObject"


def selection_references(items):
    """Resolve explicit objects/subelements without rereading GUI selection."""

    items = list(items)
    document = None
    for obj, _subelements in items:
        if document is None:
            document = getattr(obj, "Document", None)
        if not object_is_usable_at_current_position(obj, document):
            raise ValueError(
                "Draft selection references must be usable at the current "
                "History position in one document"
            )
    return [
        _SelectionReference(obj, subelements)
        for obj, subelements in items
    ]


def _flatten_objects(objects):
    """Return document objects from an explicitly ordered nested result."""

    if objects is None:
        return []
    if hasattr(objects, "Document") and hasattr(objects, "Name"):
        return [objects]
    if isinstance(objects, (str, bytes)):
        raise TypeError("Timeline inputs must be document objects")

    flattened = []
    for item in objects:
        flattened.extend(_flatten_objects(item))
    return flattened


def _flatten_required_outputs(outputs):
    """Return outputs while rejecting a partially failed command result."""

    if outputs is None:
        raise RuntimeError("A tracked Draft command produced a missing output")
    if hasattr(outputs, "Document") and hasattr(outputs, "Name"):
        return [outputs]
    if isinstance(outputs, (str, bytes)):
        raise TypeError("Timeline outputs must be document objects")

    flattened = []
    for output in outputs:
        flattened.extend(_flatten_required_outputs(output))
    return flattened


def _live_in_document(obj, document):
    return (
        obj is not None
        and document is not None
        and getattr(obj, "Document", None) is document
        and document.getObject(obj.Name) is obj
    )


def _unique_live(objects, document=None):
    result = []
    for obj in _flatten_objects(objects):
        obj_document = getattr(obj, "Document", None)
        if document is None:
            document = obj_document
        if not _live_in_document(obj, document):
            raise ValueError("Timeline objects must be live in one document")
        if obj not in result:
            result.append(obj)
    return result


def source_objects(selection):
    """Return unique document objects from an explicit GUI selection."""

    objects = []
    for selected in selection:
        obj = getattr(selected, "Object", selected)
        if obj not in objects:
            objects.append(obj)
    exact_objects = _unique_live(objects)
    if any(
        not object_is_usable_at_current_position(obj, obj.Document)
        for obj in exact_objects
    ):
        raise ValueError(
            "Draft sources must be usable at the current History position"
        )
    return exact_objects


def _ensure_property(obj, type_id, name, description):
    if name in obj.PropertiesList:
        actual_type = obj.getTypeIdOfProperty(name)
        if actual_type != type_id:
            raise TypeError(
                "{object}.{name} must be {expected}, not {actual}".format(
                    object=obj.Name,
                    name=name,
                    expected=type_id,
                    actual=actual_type,
                )
            )
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

    # Copies and imported objects can retain the value without its native
    # internal-metadata status bits. Reassert the complete contract before
    # the document History accepts the object.
    obj.setPropertyStatus(name, ("Hidden", "LockDynamic", "NoRecompute"))
    obj.setEditorMode(name, 2)


def visible_inputs(objects):
    """Capture exact live inputs which are visible before a command runs."""

    exact_inputs = []
    document = None
    for obj in _flatten_objects(objects):
        if document is None:
            document = getattr(obj, "Document", None)
        if not object_is_usable_at_current_position(obj, document):
            raise ValueError(
                "Draft command inputs must be usable at the current History "
                "position in one document"
            )
        view = getattr(obj, "ViewObject", None)
        if view is not None and view.Visibility and obj not in exact_inputs:
            exact_inputs.append(obj)
    return exact_inputs


def mark_operation(operation):
    """Mark one explicit result as the user-visible history operation."""

    document = getattr(operation, "Document", None)
    if not _live_in_document(operation, document):
        raise ValueError("A Draft timeline operation must be live in its document")

    _ensure_property(
        operation,
        "App::PropertyString",
        _ROLE,
        "Document timeline classification",
    )
    operation.VibeCADTimelineRole = "operation"

    if _OWNER in operation.PropertiesList:
        if operation.getTypeIdOfProperty(_OWNER) != "App::PropertyLinkHidden":
            raise TypeError(
                "{object}.{name} must be App::PropertyLinkHidden".format(
                    object=operation.Name,
                    name=_OWNER,
                )
            )
        operation.VibeCADTimelineOwner = None
    return operation


def mark_resource(resource, owner):
    """Make one generated implementation object belong to *owner*."""

    document = getattr(owner, "Document", None)
    if (
        resource is owner
        or not _live_in_document(owner, document)
        or not _live_in_document(resource, document)
    ):
        raise ValueError(
            "A Draft timeline resource and its distinct owner must be live "
            "in one document"
        )

    _ensure_property(
        resource,
        "App::PropertyString",
        _ROLE,
        "Document timeline classification",
    )
    _ensure_property(
        resource,
        "App::PropertyLinkHidden",
        _OWNER,
        "Draft operation which owns this generated result",
    )
    resource.VibeCADTimelineOwner = owner
    resource.VibeCADTimelineRole = "resource"
    return resource


def set_replaced_inputs(operation, inputs):
    """Persist only the operands captured as visible before the command."""

    document = getattr(operation, "Document", None)
    if not _live_in_document(operation, document):
        raise ValueError("A Draft replacement operation must be live")

    exact_inputs = _unique_live(inputs, document)
    for input_obj in exact_inputs:
        if input_obj is operation:
            raise ValueError("A Draft operation cannot replace itself")

    _ensure_property(
        operation,
        "App::PropertyLinkListHidden",
        _REPLACED_INPUTS,
        "Visible input objects hidden by this operation",
    )
    operation.VibeCADTimelineReplacedInputs = exact_inputs
    return exact_inputs


def accept_outputs(outputs, replaced_inputs=()):
    """Accept creation-ordered outputs as one semantic Draft operation.

    The final output is the operation because it is the final surviving
    object created by the command. Earlier outputs are owned resources. This
    explicit ordering lets the timeline's Previous action cross the complete
    command in one step instead of stopping between implementation objects.
    """

    ordered_outputs = _unique_live(_flatten_required_outputs(outputs))
    if not ordered_outputs:
        return None

    operation = ordered_outputs[-1]
    document = operation.Document
    if (
        document.getBookedTransactionID() == 0
        or not document.HasPendingTransaction
    ):
        raise RuntimeError(
            "Accepting Draft outputs requires one caller-owned transaction"
        )
    exact_inputs = _unique_live(
        replaced_inputs,
        document,
    )
    if exact_inputs:
        set_replaced_inputs(operation, exact_inputs)

    document.publishProvisionalTimelineOperationBlock(
        operation,
        ordered_outputs[:-1],
    )

    if exact_inputs:
        for input_obj in exact_inputs:
            view = getattr(input_obj, "ViewObject", None)
            if view is not None:
                view.Visibility = False

    return operation


def accept_derived_output(output, sources=()):
    """Track one linked result without changing source presentation."""

    document = getattr(output, "Document", None)
    exact_sources = _unique_live(sources, document)
    if any(
        not object_is_usable_at_current_position(source, document)
        for source in exact_sources
    ):
        raise ValueError(
            "Draft derived sources must be usable at the current History "
            "position"
        )
    return accept_outputs([output])


def join_replacement(wires):
    """Join copies of selected wires and preserve the inputs as history."""

    from draftfunctions import join
    from draftmake import make_copy

    inputs = _unique_live(wires)
    if len(inputs) < 2:
        return []
    visible = visible_inputs(inputs)
    copies = [make_copy.make_copy(obj) for obj in inputs]
    if any(obj is None for obj in copies):
        raise RuntimeError("Draft Join could not copy every selected wire")
    outputs = join.join_wires(copies)
    outputs = _unique_live(outputs or [])
    if not outputs:
        raise RuntimeError("Draft Join produced no result")
    accept_outputs(outputs, visible)
    return outputs


def split_replacement(wire, point, edge_index):
    """Split a copy of one wire and preserve the original as history."""

    from draftfunctions import split
    from draftmake import make_copy

    inputs = _unique_live([wire])
    visible = visible_inputs(inputs)
    working = make_copy.make_copy(wire)
    if working is None:
        raise RuntimeError("Draft Split could not copy the selected wire")
    new = split.split(working, point, edge_index)
    if new is None:
        working.Document.removeObject(working.Name)
        return None
    accept_outputs([working, new], visible)
    return new


def upgrade_replacement(objects):
    """Run the existing Upgrade engine without deleting source history."""

    from draftfunctions import upgrade

    inputs = _unique_live(objects)
    visible = visible_inputs(inputs)
    outputs, deletions = upgrade.upgrade(inputs, delete=False)
    if outputs:
        accept_outputs(outputs, visible)
    return outputs, deletions


def downgrade_replacement(objects):
    """Run the existing Downgrade engine without deleting source history."""

    from draftfunctions import downgrade

    inputs = _unique_live(objects)
    visible = visible_inputs(inputs)
    outputs, deletions = downgrade.downgrade(inputs, delete=False)
    if outputs:
        accept_outputs(outputs, visible)
    return outputs, deletions


def convert_wire_replacement(obj):
    """Convert one Draft Wire/B-spline without discarding its source."""

    from draftmake import make_bspline
    from draftmake import make_wire
    from draftutils import gui_utils
    from draftutils import utils

    inputs = _unique_live([obj])
    visible = visible_inputs(inputs)
    placement = obj.Placement if "Placement" in obj.PropertiesList else None
    object_type = utils.get_type(obj)
    if object_type == "Wire":
        result = make_bspline.make_bspline(
            obj.Points,
            closed=obj.Closed,
            placement=placement,
        )
    elif object_type == "BSpline":
        result = make_wire.make_wire(
            obj.Points,
            closed=obj.Closed,
            placement=placement,
            face=None,
            support=None,
            bs2wire=True,
        )
    else:
        raise TypeError("Draft conversion requires one Wire or B-spline")

    if result is None:
        raise RuntimeError("Draft Wire/B-spline conversion produced no result")
    gui_utils.format_object(result, obj)
    accept_outputs([result], visible)
    return result


def offset(obj, delta, copy=False, occ=False):
    """Apply a GUI offset with explicit in-place/copy/replacement semantics."""

    from draftfunctions import offset as offset_function

    inputs = _unique_live([obj])
    if copy:
        return offset_function.offset(obj, delta, copy=True, occ=occ)
    if not occ:
        return offset_function.offset(obj, delta, copy=False, occ=False)

    visible = visible_inputs(inputs)
    result = offset_function.offset(obj, delta, copy=True, occ=True)
    if result is not obj:
        accept_outputs([result], visible)
    return result


def move(selection, vector, copy=False, subelements=False):
    """Run Move and group copy-mode outputs into one semantic operation."""

    from draftfunctions import move as move_function

    result = move_function.move(
        selection,
        vector,
        copy=copy,
        subelements=subelements,
    )
    if copy and result:
        accept_outputs(result)
    return result


def rotate(
    selection,
    angle,
    center=App.Vector(0, 0, 0),
    axis=App.Vector(0, 0, 1),
    copy=False,
    subelements=False,
):
    """Run Rotate and group copy-mode outputs into one semantic operation."""

    from draftfunctions import rotate as rotate_function

    result = rotate_function.rotate(
        selection,
        angle,
        center=center,
        axis=axis,
        copy=copy,
        subelements=subelements,
    )
    if copy and result:
        accept_outputs(result)
    return result


def scale(
    selection,
    scale_vector,
    center=App.Vector(0, 0, 0),
    copy=False,
    clone=False,
    subelements=False,
):
    """Run Scale with explicit in-place, copy, or replacement history."""

    from draftfunctions import scale as scale_function

    inputs = source_objects(selection)
    visible = visible_inputs(inputs)
    result = scale_function.scale(
        selection,
        scale_vector,
        center=center,
        copy=copy,
        clone=clone,
        subelements=subelements,
        preserve_replaced=True,
    )
    outputs = _unique_live(result or [])
    if copy or clone:
        if outputs:
            accept_outputs(outputs)
        return result

    replaced = [obj for obj in visible if obj not in outputs]
    generated = [obj for obj in outputs if obj not in inputs]
    if generated:
        accept_outputs(generated, replaced)
    return result


def mirror(objects, point_1, point_2):
    """Run Mirror and group all created mirrors into one operation."""

    from draftfunctions import mirror as mirror_function

    result = mirror_function.mirror(objects, point_1, point_2)
    if result:
        accept_outputs(result)
    return result


def fillet(selection, radius, chamfer=False, replace_inputs=False):
    """Create a fillet and optionally preserve its exact replaced inputs."""

    from draftmake import make_fillet

    inputs = []
    for selected in selection:
        obj = getattr(selected, "Object", selected)
        if obj not in inputs:
            inputs.append(obj)
    visible = visible_inputs(inputs) if replace_inputs else []
    result = make_fillet.make_fillet(
        selection,
        radius=radius,
        chamfer=chamfer,
        delete=False,
    )
    if result is not None and replace_inputs:
        accept_outputs([result], visible)
    return result


def convert_to_sketch(objects):
    """Convert Draft objects to one Sketch and preserve source history."""

    from draftmake import make_sketch

    inputs = _unique_live(objects)
    visible = visible_inputs(inputs)
    result = make_sketch.make_sketch(inputs, autoconstraints=True)
    if result is not None:
        accept_outputs([result], visible)
    return result


def convert_to_draft(objects):
    """Convert Sketches to Draft outputs as one tracked operation."""

    from draftfunctions import draftify

    inputs = _unique_live(objects)
    visible = visible_inputs(inputs)
    outputs = []
    for obj in inputs:
        outputs.append(draftify.draftify(obj, delete=False))
    accept_outputs(outputs, visible)
    return outputs


def convert_draft_sketch_replacement(objects):
    """Run the bidirectional Draft/Sketch GUI conversion as one operation."""

    from draftfunctions import draftify
    from draftmake import make_sketch

    inputs = _unique_live(objects)
    visible = visible_inputs(inputs)
    all_sketches = all(obj.isDerivedFrom("Sketcher::SketchObject") for obj in inputs)
    all_draft = all(
        obj.isDerivedFrom("Part::Part2DObjectPython")
        or obj.isDerivedFrom("Part::Feature")
        for obj in inputs
    )

    if all_draft:
        outputs = [make_sketch.make_sketch(inputs, autoconstraints=True)]
    elif all_sketches:
        outputs = [draftify.draftify(obj, delete=False) for obj in inputs]
    else:
        outputs = []
        for obj in inputs:
            if obj.isDerivedFrom("Sketcher::SketchObject"):
                outputs.append(draftify.draftify(obj, delete=False))
            elif obj.isDerivedFrom("Part::Part2DObjectPython") or obj.isDerivedFrom(
                "Part::Feature"
            ):
                outputs.append(make_sketch.make_sketch(obj, autoconstraints=True))

    if any(output is not None for output in outputs):
        accept_outputs(outputs, visible)
    return outputs
