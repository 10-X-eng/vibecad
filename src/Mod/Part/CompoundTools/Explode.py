# SPDX-License-Identifier: LGPL-2.1-or-later

import FreeCAD
import Part

from .CompoundFilter import makeCompoundFilter


def _ensure_timeline_property(obj, type_id, name, description):
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
    # Imported and copied objects may retain correctly typed History
    # metadata without its native internal status bits.
    obj.setPropertyStatus(name, ("Hidden", "LockDynamic", "NoRecompute"))
    obj.setEditorMode(name, 2)


def _mark_timeline_operation(operation):
    """Persist one user-visible operation in the document timeline."""

    if operation is None:
        raise ValueError("A Part timeline operation is required")
    _ensure_timeline_property(
        operation,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document timeline classification",
    )
    operation.VibeCADTimelineRole = "operation"
    if "VibeCADTimelineOwner" in operation.PropertiesList:
        _ensure_timeline_property(
            operation,
            "App::PropertyLinkHidden",
            "VibeCADTimelineOwner",
            "Part operation which owns this generated result",
        )
        operation.VibeCADTimelineOwner = None


def _mark_timeline_resource(resource, owner):
    """Persist one generated result under its user-visible operation."""

    if resource is None or owner is None or resource is owner:
        raise ValueError("A Part timeline resource requires a distinct owner")
    if resource.Document is not owner.Document:
        raise ValueError("A Part timeline resource and its owner must share a document")

    _ensure_timeline_property(
        resource,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document timeline classification",
    )
    _ensure_timeline_property(
        resource,
        "App::PropertyLinkHidden",
        "VibeCADTimelineOwner",
        "Part operation which owns this generated result",
    )
    resource.VibeCADTimelineOwner = owner
    resource.VibeCADTimelineRole = "resource"


def _set_timeline_editor(operation, editor):
    """Persist the owned implementation object which edits an operation."""

    if operation is None or editor is None or operation is editor:
        raise ValueError("A Part timeline editor requires distinct objects")
    if operation.Document is not editor.Document:
        raise ValueError("A Part timeline editor and operation must share a document")
    _ensure_timeline_property(
        operation,
        "App::PropertyLinkHidden",
        "VibeCADTimelineEditor",
        "Owned implementation object which edits this operation",
    )
    operation.VibeCADTimelineEditor = editor


def _set_timeline_replaced_inputs(operation, inputs):
    """Persist the visible operands which an operation intentionally hides."""

    if operation is None or operation.Document is None:
        raise ValueError("A Part timeline operation must belong to a document")
    document = operation.Document
    if document.getObject(operation.Name) is not operation:
        raise ValueError("A Part timeline operation must be attached to its document")

    replaced = []
    for input_obj in inputs:
        if (
            input_obj is None
            or input_obj is operation
            or input_obj.Document is not document
            or document.getObject(input_obj.Name) is not input_obj
        ):
            raise ValueError(
                "A replaced timeline input must be a distinct live object "
                "in the operation document"
            )
        if input_obj not in replaced:
            replaced.append(input_obj)

    _ensure_timeline_property(
        operation,
        "App::PropertyLinkListHidden",
        "VibeCADTimelineReplacedInputs",
        "Visible input objects hidden by this operation",
    )
    operation.VibeCADTimelineReplacedInputs = replaced


def _mark_body_outputs_as_timeline_resources(output_component, features):
    """Represent one multi-body result as one visible history operation."""

    _mark_timeline_operation(output_component)
    marked_bodies = set()
    for feature in features:
        body = feature.getParentGeoFeatureGroup()
        if body is None or not body.isDerivedFrom("PartDesign::Body"):
            raise RuntimeError(
                "{feature} has no owning output Body".format(feature=feature.Label)
            )
        if body not in marked_bodies:
            _mark_timeline_resource(body, output_component)
            marked_bodies.add(body)
        _mark_timeline_resource(feature, body)


def _validate_body_outputs(features, expected_count=None):
    """Require complete, independently owned Body outputs before commit."""
    if expected_count is not None and len(features) != expected_count:
        raise RuntimeError(
            "Expected {expected} output Bodies, created {actual}.".format(
                expected=expected_count,
                actual=len(features),
            )
        )
    if not features:
        raise RuntimeError("The compound did not produce any output Bodies.")

    bodies = set()
    for feature in features:
        body = feature.getParentGeoFeatureGroup()
        if body is None or not body.isDerivedFrom("PartDesign::Body"):
            raise RuntimeError(
                "{feature} is not owned by an independent Body.".format(
                    feature=feature.Label,
                )
            )
        if body in bodies:
            raise RuntimeError("Multiple output pieces were placed in one Body.")
        bodies.add(body)
        if body.Tip is not feature:
            raise RuntimeError(
                "{body} does not publish its extracted piece as its Tip.".format(
                    body=body.Label,
                )
            )
        if feature.Shape.isNull() or not feature.Shape.isValid():
            raise RuntimeError(
                "{feature} did not produce valid geometry.".format(
                    feature=feature.Label,
                )
            )

def _finalize_body_output_timeline(output_component, features):
    """Finalize one multi-Body result as a single canonical history block."""

    _validate_body_outputs(features)
    document = output_component.Document
    ordered_resources = []
    resource_owners = []
    for feature in features:
        body = feature.getParentGeoFeatureGroup()
        ordered_resources.extend((feature, body))
        resource_owners.extend((body, output_component))
    editor = getattr(
        output_component,
        "VibeCADTimelineEditor",
        None,
    )
    if editor is not None:
        if (
            editor is output_component
            or editor.Document is not document
            or document.getObject(editor.Name) is not editor
        ):
            raise RuntimeError(
                "The multi-body result has an invalid exact editor"
            )
        ordered_resources.insert(0, editor)
        resource_owners.insert(0, output_component)
    document.publishProvisionalTimelineOperationBlock(
        output_component,
        ordered_resources,
        resource_owners,
    )


def _make_body_outputs(compound_obj, child_count, output_component=None):
    """Create one independently renderable Body for every compound child."""
    document = compound_obj.Document
    create_output_component = output_component is None
    if output_component is not None and (
        output_component.Document is not document
        or not output_component.isDerivedFrom("App::Part")
    ):
        raise ValueError("output_component must be an App::Part in the source document")

    output_bodies = []
    features_created = []
    for index in range(child_count):
        piece_number = index + 1
        body_name = "{obj.Name}_Piece{piece}".format(
            obj=compound_obj,
            piece=piece_number,
        )
        body = (
            document.addObject("PartDesign::Body", body_name)
            if create_output_component
            else output_component.newObject("PartDesign::Body", body_name)
        )
        body.Label = "{obj.Label} Piece {piece}".format(
            obj=compound_obj,
            piece=piece_number,
        )
        feature = makeCompoundFilter(
            "{obj.Name}_child{child}".format(
                obj=compound_obj,
                child=index,
            ),
            body,
        )
        feature.Label = "Extract Piece {piece}".format(piece=piece_number)
        feature.Base = compound_obj
        feature.FilterType = "specific items"
        feature.items = str(index)
        if feature.ViewObject is not None:
            feature.ViewObject.DontUnhideOnDelete = True
            feature.ViewObject.Visibility = True
        body.Tip = feature
        if body.ViewObject is not None:
            body.ViewObject.Visibility = True
        output_bodies.append(body)
        features_created.append(feature)

    if create_output_component:
        output_component = document.addObject(
            "App::Part",
            "Exploded_" + compound_obj.Name,
        )
        output_component.Label = "Exploded {obj.Label}".format(obj=compound_obj)
        for body in output_bodies:
            output_component.addObject(body)

    return output_component, features_created


def explodeCompound(
    compound_obj,
    b_group=None,
    *,
    body_outputs=False,
    output_component=None,
):
    """Extract every child of a compound into a separate parametric object.

    ``b_group`` preserves the historical group/document behavior.  VibeCAD's
    multi-result commands pass ``body_outputs=True`` so every sibling result
    owns an independent Part Design Body and Tip.  ``output_component`` may be
    an existing App::Part in the same document when an operation also needs to
    retain private implementation geometry.

    Returns ``(container, child_features)`` for both output modes.
    """

    if isinstance(compound_obj, FreeCAD.GeoFeature) and isinstance(
        compound_obj.getPropertyOfGeometry(), Part.Shape
    ):
        sh = compound_obj.getPropertyOfGeometry()
    else:
        raise TypeError("Object must be App.GeoFeature with Part.Shape property")

    n = len(sh.childShapes(False, False))
    if body_outputs:
        if b_group is not None:
            raise ValueError("b_group cannot be combined with body_outputs")
        return _make_body_outputs(compound_obj, n, output_component)

    body_target = None
    if b_group is None:
        try:
            parent = compound_obj.getParentGeoFeatureGroup()
        except (AttributeError, RuntimeError):
            parent = None
        if parent is not None and parent.isDerivedFrom("PartDesign::Body"):
            body_target = parent
            b_group = False
        else:
            b_group = n > 1
    if body_target is not None:
        group = body_target
    elif b_group:
        group = compound_obj.Document.addObject(
            "App::DocumentObjectGroup", "GrExplode_" + compound_obj.Name
        )
        group.Label = "Exploded {obj.Label}".format(obj=compound_obj)
    else:
        group = compound_obj.Document
    features_created = []
    for i in range(0, n):
        cf = makeCompoundFilter(
            "{obj.Name}_child{child_num}".format(obj=compound_obj, child_num=i), group
        )
        cf.Label = "{obj.Label}.{child_num}".format(obj=compound_obj, child_num=i)
        cf.Base = compound_obj
        cf.FilterType = "specific items"
        cf.items = str(i)
        if cf.ViewObject is not None:
            cf.ViewObject.DontUnhideOnDelete = True
        features_created.append(cf)
    return (group, features_created)


def makeBodyOutputOperation(
    compound_obj,
    *,
    label,
    replaced_inputs=(),
    editor=None,
):
    """Create, validate, and publish one exact multi-Body history operation.

    The returned App::Part is the semantic History operation. Every generated
    Body and extraction feature is published as its private resource. An
    optional editor (for example the Slice feature behind Slice Apart) is
    retained as a hidden owned resource instead of becoming a second visible
    history step.
    """

    if compound_obj is None or compound_obj.Document is None:
        raise ValueError("A live compound source is required")
    document = compound_obj.Document
    if document.getObject(compound_obj.Name) is not compound_obj:
        raise ValueError("The compound source must be live in its document")
    if editor is not None and (
        editor is not compound_obj
        or editor.Document is not document
        or document.getObject(editor.Name) is not editor
    ):
        raise ValueError(
            "The body-output editor must be the exact compound source"
        )

    expected_count = len(
        compound_obj.Shape.childShapes(False, False)
    )
    output_component, output_features = explodeCompound(
        compound_obj,
        body_outputs=True,
    )
    output_component.Label = label

    if editor is not None:
        output_component.addObject(editor)
        _set_timeline_editor(output_component, editor)

    visible_inputs = []
    for input_obj in replaced_inputs:
        if (
            input_obj is not None
            and input_obj.Document is document
            and document.getObject(input_obj.Name) is input_obj
            and input_obj not in visible_inputs
        ):
            visible_inputs.append(input_obj)
    if visible_inputs:
        _set_timeline_replaced_inputs(
            output_component,
            visible_inputs,
        )
        presentations_to_hide = visible_inputs
    else:
        presentations_to_hide = [compound_obj]

    if editor is not None and editor.ViewObject is not None:
        editor.ViewObject.ShowInTree = False
        editor.ViewObject.Visibility = False

    document.recompute()
    _validate_body_outputs(output_features, expected_count)
    _finalize_body_output_timeline(
        output_component,
        output_features,
    )
    # Publication proves that every pre-existing operation still has its
    # accepted state from the transaction boundary. Hide replaced
    # presentations only after that atomic proof; the ordinary visibility
    # observer then records their new accepted state in the same transaction.
    for presentation in presentations_to_hide:
        if presentation.ViewObject is not None:
            presentation.ViewObject.Visibility = False
    return output_component
