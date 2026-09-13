# SPDX-License-Identifier: LGPL-2.1-or-later
# ***************************************************************************
# *   Copyright (c) 2017 sliptonic <shopinthewoods@gmail.com>               *
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

"""
The purpose of this file is to collect some handy functions. The reason they
are not in Path.Base.Utils (and there is this confusing naming going on) is that
PathUtils depends on PathJob. Which makes it impossible to use the functions
and classes defined there in PathJob.

So if you add to this file and think about importing anything from PathScripts
other than Path.Log, then it probably doesn't belong here.
"""

import FreeCAD
import Path

translate = FreeCAD.Qt.translate

if False:
    Path.Log.setLevel(Path.Log.Level.DEBUG, Path.Log.thisModule())
    Path.Log.trackModule(Path.Log.thisModule())
else:
    Path.Log.setLevel(Path.Log.Level.INFO, Path.Log.thisModule())


def _getProperty(obj, prop):
    o = obj
    attr = obj
    name = None
    for name in prop.split("."):
        o = attr
        if not hasattr(o, name):
            break
        attr = getattr(o, name)

    if o == attr:
        Path.Log.debug(translate("PathGui", "%s has no property %s (%s)") % (obj.Label, prop, name))
        return (None, None, None)

    # Path.Log.debug("found property %s of %s (%s: %s)" % (prop, obj.Label, name, attr))
    return (o, attr, name)


def getProperty(obj, prop):
    """getProperty(obj, prop) ... answer obj's property defined by its canonical name."""
    o, attr, name = _getProperty(obj, prop)
    return attr


def getPropertyValueString(obj, prop):
    """getPropertyValueString(obj, prop) ... answer a string representation of an object's property's value."""
    attr = getProperty(obj, prop)
    if hasattr(attr, "UserString"):
        return attr.UserString
    return str(attr)


def setProperty(obj, prop, value):
    """setProperty(obj, prop, value) ... set the property value of obj's property defined by its canonical name."""
    o, attr, name = _getProperty(obj, prop)
    if attr is not None and isinstance(value, str):
        if isinstance(attr, bool):
            value = value.lower() in ["true", "1", "yes", "ok"]
        elif isinstance(attr, int):
            value = int(value, 0)
    if o and name:
        setattr(o, name, value)


# NotValidBaseTypeIds = ['Sketcher::SketchObject']
NotValidBaseTypeIds = []


def isValidBaseObject(obj):
    """isValidBaseObject(obj) ... returns true if the object can be used as a base for a job."""
    timeline_role = str(getattr(obj, "VibeCADTimelineRole", "") or "")
    if timeline_role in {"internal", "resource"} and obj.TypeId != "PartDesign::Body":
        return False
    if hasattr(obj, "getParentGeoFeatureGroup") and obj.getParentGeoFeatureGroup():
        # Can't link to anything inside a geo feature group anymore
        Path.Log.debug("%s is inside a geo feature group" % obj.Label)
        return False
    if hasattr(obj, "BitBody") and hasattr(obj, "ShapeName"):
        # ToolBit's are not valid base objects
        return False
    if hasattr(obj, "ToolBitID"):
        return False
    if any(hasattr(ob, "ToolBitID") for ob in getattr(obj, "InListRecursive", [])):
        return False
    if obj.TypeId in NotValidBaseTypeIds:
        Path.Log.debug("%s is blacklisted (%s)" % (obj.Label, obj.TypeId))
        return False
    if hasattr(obj, "Sheets") or hasattr(obj, "TagText"):  # Arch.Panels and Arch.PanelCut
        Path.Log.debug("%s is not an Arch.Panel" % (obj.Label))
        return False
    import Part

    return not Part.getShape(obj).isNull()


def isSolid(obj):
    """isSolid(obj) ... return True if the object is a valid solid."""
    import Part

    shape = Part.getShape(obj)
    return not shape.isNull() and shape.Volume and shape.isClosed()


def opProperty(op, prop, default=None):
    """opProperty(op, prop) ... return the value of property prop of the underlying operation (or None if prop does not exist)"""
    if prop == "Active" and suppressedForOp(op):
        return False
    if hasattr(op, prop):
        return getattr(op, prop)
    if hasattr(op, "Base"):
        return opProperty(op.Base, prop, default)
    return default


def suppressedForOp(op):
    """Return whether an operation or its dressup base is suppressed."""
    visited = set()
    current = op
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if getattr(current, "Suppressed", False):
            return True
        base = getattr(current, "Base", None)
        current = base if hasattr(base, "TypeId") else None
    return False


def toolControllerForOp(op):
    """toolControllerForOp(op) ... return the tool controller used by the op.
    If the op doesn't have its own tool controller but has a Base object, return its tool controller.
    Otherwise return None."""
    return opProperty(op, "ToolController")


def coolantModeForOp(op):
    """coolantModeForOp(op) ... return the coolant mode used by the op.
    If the op doesn't have its own coolant mode but has a Base object, return its coolant mode.
    Otherwise return "None"."""
    return opProperty(op, "CoolantMode", "None")


def activeForOp(op):
    """activeForOp(op) ... return the active property used by the op.
    If the op doesn't have its own active property but has a Base object, return its active property.
    Otherwise return True."""
    return opProperty(op, "Active", True)


_TIMELINE_ROLE_PROPERTY = "VibeCADTimelineRole"
_TIMELINE_OWNER_PROPERTY = "VibeCADTimelineOwner"
_TIMELINE_REPLACED_INPUTS_PROPERTY = "VibeCADTimelineReplacedInputs"
_TIMELINE_PARENT_JOB_PROPERTY = "VibeCADCAMParentJob"
_TIMELINE_PROPERTY_GROUP = "VibeCAD"


def _ensureTimelineProperty(obj, type_id, name, description):
    """Create one hidden, persistent timeline metadata property."""
    if name in obj.PropertiesList:
        actual_type = obj.getTypeIdOfProperty(name)
        if actual_type != type_id:
            raise TypeError(
                f"{obj.Name}.{name} must be {type_id}, not {actual_type}"
            )
    else:
        obj.addProperty(
            type_id,
            name,
            _TIMELINE_PROPERTY_GROUP,
            description,
            attr=16,  # App::Prop_NoRecompute
            hidden=True,
            locked=True,
        )

    # Imported CAM assets and copied native features can already carry
    # correctly typed timeline metadata without its complete internal-storage
    # contract.  Core History publication validates these statuses, not merely
    # property-editor presentation.
    obj.setPropertyStatus(
        name,
        ("Hidden", "LockDynamic", "NoRecompute"),
    )
    obj.setEditorMode(name, 2)


def markTimelineOperation(obj):
    """Mark a durable CAM object as a user-visible history operation."""
    if obj is None or not hasattr(obj, "PropertiesList"):
        # Setup-sheet operation prototypes intentionally expose the same
        # Python proxy API without being document objects.
        return obj

    capture = getattr(
        getattr(obj, "Proxy", None),
        "_captureInitialTimelineOperation",
        None,
    )
    if capture is not None and capture(obj):
        return obj

    _ensureTimelineProperty(
        obj,
        "App::PropertyString",
        _TIMELINE_ROLE_PROPERTY,
        "Document timeline classification",
    )
    if _TIMELINE_OWNER_PROPERTY in obj.PropertiesList:
        _ensureTimelineProperty(
            obj,
            "App::PropertyLinkHidden",
            _TIMELINE_OWNER_PROPERTY,
            "Durable operation which owns this internal CAM resource",
        )
        setattr(obj, _TIMELINE_OWNER_PROPERTY, None)
    setattr(obj, _TIMELINE_ROLE_PROPERTY, "operation")
    return obj


def restoreTimelineOperation(obj):
    """Restore an operation role without promoting an owned resource.

    CAM operation proxies are reused for copied ancestors and multi-copy
    outputs which intentionally belong to one later semantic operation.
    Their persisted resource role is authoritative across save/reopen.
    Legacy objects with no explicit resource role retain the normal operation
    migration.
    """
    if obj is None or not hasattr(obj, "PropertiesList"):
        return obj
    if _TIMELINE_ROLE_PROPERTY in obj.PropertiesList:
        _ensureTimelineProperty(
            obj,
            "App::PropertyString",
            _TIMELINE_ROLE_PROPERTY,
            "Document timeline classification",
        )
        if (
            getattr(obj, _TIMELINE_ROLE_PROPERTY) == "resource"
        ):
            if _TIMELINE_OWNER_PROPERTY in obj.PropertiesList:
                _ensureTimelineProperty(
                    obj,
                    "App::PropertyLinkHidden",
                    _TIMELINE_OWNER_PROPERTY,
                    "Durable operation which owns this internal CAM resource",
                )
            return obj
    return markTimelineOperation(obj)


def _isLiveTimelineObject(obj, document):
    """Return whether ``obj`` is still the named object in ``document``."""
    try:
        return (
            obj is not None
            and document is not None
            and obj.Document is document
            and bool(obj.Name)
            and document.getObject(obj.Name) is obj
        )
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def markTimelineReplacedInputs(operation, inputs):
    """Persist the exact public inputs hidden by one CAM operation.

    Callers must pass the command-authored input set; this helper deliberately
    does not infer replacements from links, groups, dependency edges, or
    visibility after the command.  Passing an empty iterable clears an
    operation's replacement set while retaining a valid explicit contract.
    """
    document = getattr(operation, "Document", None)
    if not _isLiveTimelineObject(operation, document):
        raise ValueError(
            "A CAM replacement operation must be live in its document"
        )

    exact_inputs = []
    for input_obj in inputs:
        if (
            input_obj is operation
            or not _isLiveTimelineObject(input_obj, document)
        ):
            raise ValueError(
                "A CAM replaced input must be a distinct live object "
                "in the operation document"
            )
        if input_obj not in exact_inputs:
            exact_inputs.append(input_obj)

    if _TIMELINE_OWNER_PROPERTY in operation.PropertiesList:
        _ensureTimelineProperty(
            operation,
            "App::PropertyLinkHidden",
            _TIMELINE_OWNER_PROPERTY,
            "Durable operation which owns this internal CAM resource",
        )
        if getattr(operation, _TIMELINE_OWNER_PROPERTY) is not None:
            raise TypeError(
                "A CAM replacement operation cannot retain resource-owner metadata"
            )

    markTimelineOperation(operation)
    _ensureTimelineProperty(
        operation,
        "App::PropertyLinkListHidden",
        _TIMELINE_REPLACED_INPUTS_PROPERTY,
        "Visible input objects hidden by this operation",
    )
    setattr(
        operation,
        _TIMELINE_REPLACED_INPUTS_PROPERTY,
        exact_inputs,
    )
    return operation


def shouldRestoreTimelineReplacedInput(operation, input_obj):
    """Return whether deleting ``operation`` should reveal ``input_obj``.

    Objects without replacement metadata predate the explicit contract and
    retain the legacy CAM deletion behavior.  A present contract is
    authoritative; malformed metadata fails closed.
    """
    try:
        if _TIMELINE_REPLACED_INPUTS_PROPERTY not in operation.PropertiesList:
            return True
        if (
            operation.getTypeIdOfProperty(
                _TIMELINE_REPLACED_INPUTS_PROPERTY
            )
            != "App::PropertyLinkListHidden"
        ):
            return False
        return input_obj in getattr(
            operation,
            _TIMELINE_REPLACED_INPUTS_PROPERTY,
        )
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def markTimelineParentJob(operation, job):
    """Persist the exact Job which structurally owns a CAM operation.

    A deleting document removes an operation from its group before invoking
    the Python proxy's ``onDelete`` callback.  Dress-ups which replace their
    base in the Job therefore cannot rediscover that Job from the document
    graph while restoring the base.  This explicit link preserves the command
    boundary without guessing from labels, active documents, or other Jobs.
    """
    document = getattr(operation, "Document", None)
    if (
        not _isLiveTimelineObject(operation, document)
        or not _isLiveTimelineObject(job, document)
        or operation is job
        or not hasattr(job, "Operations")
    ):
        raise ValueError(
            "A CAM operation parent must be a distinct live Job "
            "in the operation document"
        )

    _ensureTimelineProperty(
        operation,
        "App::PropertyLinkHidden",
        _TIMELINE_PARENT_JOB_PROPERTY,
        "Exact Job which owns this CAM operation",
    )
    setattr(operation, _TIMELINE_PARENT_JOB_PROPERTY, job)
    return operation


def timelineParentJob(operation):
    """Return an operation's valid persisted parent Job, or ``None``."""
    try:
        if (
            _TIMELINE_PARENT_JOB_PROPERTY not in operation.PropertiesList
            or operation.getTypeIdOfProperty(
                _TIMELINE_PARENT_JOB_PROPERTY
            )
            != "App::PropertyLinkHidden"
        ):
            return None
        job = getattr(operation, _TIMELINE_PARENT_JOB_PROPERTY)
        document = operation.Document
        if (
            not _isLiveTimelineObject(job, document)
            or job is operation
            or not hasattr(job, "Operations")
        ):
            return None
        return job
    except (AttributeError, ReferenceError, RuntimeError):
        return None


def markTimelineResource(obj, owner):
    """Mark internal CAM state as belonging to one durable history operation."""
    try:
        document = owner.Document
    except (AttributeError, ReferenceError, RuntimeError):
        document = None

    if not _isLiveTimelineObject(owner, document):
        raise ValueError("A CAM timeline resource owner must be live in its document")
    if obj is owner or not _isLiveTimelineObject(obj, document):
        raise ValueError(
            "A CAM timeline resource must be a distinct live object "
            "in the owner document"
        )

    capture = getattr(
        getattr(owner, "Proxy", None),
        "_captureInitialTimelineResource",
        None,
    )
    if capture is not None and capture(obj):
        return obj

    _ensureTimelineProperty(
        obj,
        "App::PropertyString",
        _TIMELINE_ROLE_PROPERTY,
        "Document timeline classification",
    )
    _ensureTimelineProperty(
        obj,
        "App::PropertyLinkHidden",
        _TIMELINE_OWNER_PROPERTY,
        "Durable operation which owns this internal CAM resource",
    )
    setattr(obj, _TIMELINE_ROLE_PROPERTY, "resource")
    setattr(obj, _TIMELINE_OWNER_PROPERTY, owner)
    return obj


def createTimelineOperationController(
    document,
    name,
    label,
    operation_kind,
    outputs,
):
    """Create one non-geometric CAM history operation owning exact outputs."""
    exact_outputs = []
    for output in outputs:
        if not _isLiveTimelineObject(output, document):
            raise ValueError(
                "A CAM controller output must be live in its document"
            )
        if output not in exact_outputs:
            exact_outputs.append(output)
    if not exact_outputs:
        raise ValueError("A CAM controller requires at least one output")

    controller = document.addObject("App::FeaturePython", name)
    if controller is None:
        raise RuntimeError("The CAM history controller could not be created")
    controller.Label = label
    markTimelineOperation(controller)
    _ensureTimelineProperty(
        controller,
        "App::PropertyString",
        "CAMOperationKind",
        "CAM operation represented by this history step",
    )
    _ensureTimelineProperty(
        controller,
        "App::PropertyLinkListHidden",
        "CAMOutputs",
        "Exact CAM objects produced by this history step",
    )
    controller.CAMOperationKind = operation_kind
    controller.CAMOutputs = exact_outputs
    if controller.ViewObject:
        controller.ViewObject.ShowInTree = False

    for output in exact_outputs:
        markTimelineResource(output, controller)
    return controller


def markTimelineResourceTree(obj, owner):
    """Mark one CAM resource and its explicit internal representation tree.

    Tool bits own a hidden BitBody, and that Body owns its native feature
    history through Group.  These objects are implementation resources of the
    same durable CAM operation, not independent document-history steps.
    Traversal is deliberately limited to those two ownership properties so
    external model references are never reclassified.
    """
    pending = [obj]
    visited = set()
    while pending:
        resource = pending.pop()
        if resource is None or resource in visited:
            continue
        visited.add(resource)
        markTimelineResource(resource, owner)

        bit_body = getattr(resource, "BitBody", None)
        if bit_body is not None:
            pending.append(bit_body)
        pending.extend(getattr(resource, "Group", ()))
        proxy = getattr(resource, "Proxy", None)
        if proxy is not None:
            timeline_resources = getattr(
                proxy,
                "timelineVisualResources",
                None,
            )
            if timeline_resources is not None:
                pending.extend(timeline_resources())
    return obj


class _TimelineDirectResourceReplacement:
    """Opaque exact-identity token for one retained CAM owner update."""

    __slots__ = (
        "document",
        "document_uid",
        "owner_identity",
        "old_resource_identities",
        "replacement_index",
        "consumed",
    )

    def __init__(
        self,
        document,
        owner,
        old_resources,
        replacement_index,
    ):
        self.document = document
        self.document_uid = str(
            getattr(document, "Uid", "") or ""
        )
        self.owner_identity = (
            str(owner.Name),
            int(owner.ID),
        )
        self.old_resource_identities = tuple(
            (str(resource.Name), int(resource.ID))
            for resource in old_resources
        )
        self.replacement_index = int(replacement_index)
        self.consumed = False


class _TimelineResourceGraphExtension:
    """Opaque exact-identity token for extending one retained CAM owner."""

    __slots__ = (
        "document",
        "document_uid",
        "owner_identity",
        "old_resource_identities",
        "consumed",
    )

    def __init__(self, document, owner, old_resources):
        self.document = document
        self.document_uid = str(
            getattr(document, "Uid", "") or ""
        )
        self.owner_identity = (
            str(owner.Name),
            int(owner.ID),
        )
        self.old_resource_identities = tuple(
            (str(resource.Name), int(resource.ID))
            for resource in old_resources
        )
        self.consumed = False


class _TimelineResourceGraphEdit:
    """Opaque exact-identity token for one retained CAM graph edit."""

    __slots__ = (
        "document",
        "document_uid",
        "owner_identity",
        "old_resource_identities",
        "new_resource_identities",
        "replacement_identities",
        "consumed",
    )

    def __init__(self, document, owner, old_resources):
        self.document = document
        self.document_uid = str(
            getattr(document, "Uid", "") or ""
        )
        self.owner_identity = (
            str(owner.Name),
            int(owner.ID),
        )
        self.old_resource_identities = tuple(
            (str(resource.Name), int(resource.ID))
            for resource in old_resources
        )
        self.new_resource_identities = []
        self.replacement_identities = {}
        self.consumed = False


def _timelineSemanticRoot(obj, document):
    """Resolve one persisted CAM resource-owner chain exactly."""

    current = obj
    visited = set()
    while (
        _isLiveTimelineObject(current, document)
        and _TIMELINE_ROLE_PROPERTY in current.PropertiesList
        and current.getTypeIdOfProperty(
            _TIMELINE_ROLE_PROPERTY
        )
        == "App::PropertyString"
        and getattr(current, _TIMELINE_ROLE_PROPERTY) == "resource"
    ):
        identity = (str(current.Name), int(current.ID))
        if identity in visited:
            raise RuntimeError(
                "A CAM timeline resource has a cyclic owner graph"
            )
        visited.add(identity)
        if (
            _TIMELINE_OWNER_PROPERTY not in current.PropertiesList
            or current.getTypeIdOfProperty(
                _TIMELINE_OWNER_PROPERTY
            )
            != "App::PropertyLinkHidden"
        ):
            raise RuntimeError(
                "A CAM timeline resource has invalid owner metadata"
            )
        current = getattr(current, _TIMELINE_OWNER_PROPERTY)
    return current if _isLiveTimelineObject(current, document) else None


def _timelineOwnedResourceGraph(owner):
    """Return one tracked owner's exact canonical History resource graph."""

    document = getattr(owner, "Document", None)
    if (
        not _isLiveTimelineObject(owner, document)
        or _TIMELINE_ROLE_PROPERTY not in owner.PropertiesList
        or owner.getTypeIdOfProperty(
            _TIMELINE_ROLE_PROPERTY
        )
        != "App::PropertyString"
        or getattr(owner, _TIMELINE_ROLE_PROPERTY) != "operation"
        or _timelineSemanticRoot(owner, document) is not owner
    ):
        raise ValueError(
            "A retained CAM resource owner must be one live tracked operation"
        )
    timeline = document.getObject("VibeCADTimeline")
    if (
        timeline is None
        or timeline.TypeId != "App::DocumentTimeline"
    ):
        raise RuntimeError(
            "The retained CAM operation has no native document timeline"
        )
    operations = tuple(timeline.Operations)
    if owner not in operations:
        raise RuntimeError(
            "The retained CAM operation is absent from document History"
        )
    resources = [
        candidate
        for candidate in operations
        if candidate is not owner
        and _timelineSemanticRoot(candidate, document) is owner
    ]
    direct_roots = [
        resource
        for resource in resources
        if getattr(resource, _TIMELINE_OWNER_PROPERTY, None)
        is owner
    ]
    return document, resources, direct_roots


def _canonicalTimelineResourceOrder(owner, resources):
    """Return exact nested post-order for one explicit resource set."""

    document = getattr(owner, "Document", None)
    ordered_input = []
    for resource in resources:
        if (
            resource is owner
            or resource in ordered_input
            or not _isLiveTimelineObject(resource, document)
            or _timelineSemanticRoot(resource, document) is not owner
        ):
            raise ValueError(
                "A CAM resource graph must contain distinct live resources "
                "of one exact semantic owner"
            )
        ordered_input.append(resource)

    children = {owner: []}
    for resource in ordered_input:
        children[resource] = []
    for resource in ordered_input:
        direct_owner = getattr(
            resource,
            _TIMELINE_OWNER_PROPERTY,
            None,
        )
        if direct_owner not in children:
            raise RuntimeError(
                "A CAM resource graph omits an exact intermediate owner"
            )
        children[direct_owner].append(resource)

    result = []
    visiting = set()
    visited = set()

    def visit(resource):
        identity = (str(resource.Name), int(resource.ID))
        if identity in visiting:
            raise RuntimeError(
                "A CAM resource graph contains a cyclic owner relation"
            )
        if identity in visited:
            return
        visiting.add(identity)
        for child in children[resource]:
            visit(child)
        visiting.remove(identity)
        visited.add(identity)
        result.append(resource)

    for resource in children[owner]:
        visit(resource)
    if len(result) != len(ordered_input):
        raise RuntimeError(
            "A CAM resource graph is not connected to its semantic owner"
        )
    return result


def stageTimelineDirectResourceReplacement(owner, old_resource):
    """Stage one exact one-for-one direct resource replacement.

    This is the retained-root path used when an accepted CAM Job replaces its
    Stock.  It stages the owner's complete persisted resource graph, not a
    document-object delta.  The returned token is opaque and is valid only in
    the same caller-owned transaction.
    """

    document, old_resources, direct_roots = (
        _timelineOwnedResourceGraph(owner)
    )
    if (
        old_resource is owner
        or not _isLiveTimelineObject(old_resource, document)
        or _TIMELINE_ROLE_PROPERTY not in old_resource.PropertiesList
        or getattr(old_resource, _TIMELINE_ROLE_PROPERTY) != "resource"
        or _TIMELINE_OWNER_PROPERTY not in old_resource.PropertiesList
        or old_resource.getTypeIdOfProperty(
            _TIMELINE_OWNER_PROPERTY
        )
        != "App::PropertyLinkHidden"
        or getattr(old_resource, _TIMELINE_OWNER_PROPERTY) is not owner
    ):
        raise ValueError(
            "The replaced CAM object must be one direct resource of its owner"
        )

    if old_resource not in old_resources:
        raise RuntimeError(
            "The replaced CAM resource is absent from its exact History graph"
        )
    document.stageTimelineOperationResourceReconciliation(
        owner,
        direct_roots,
    )
    return _TimelineDirectResourceReplacement(
        document,
        owner,
        old_resources,
        old_resources.index(old_resource),
    )


def finalizeTimelineDirectResourceReplacement(
    owner,
    token,
    new_resource,
):
    """Finalize the exact direct-resource replacement staged by its pair."""

    if (
        not isinstance(token, _TimelineDirectResourceReplacement)
        or token.consumed
    ):
        raise ValueError(
            "A live unconsumed CAM resource-replacement token is required"
        )
    document = token.document
    if (
        getattr(owner, "Document", None) is not document
        or str(getattr(document, "Uid", "") or "")
        != token.document_uid
        or (
            str(getattr(owner, "Name", "")),
            int(getattr(owner, "ID", -1)),
        )
        != token.owner_identity
        or not _isLiveTimelineObject(owner, document)
    ):
        raise RuntimeError(
            "The CAM resource-replacement owner changed identity"
        )
    if (
        new_resource is owner
        or not _isLiveTimelineObject(new_resource, document)
        or _TIMELINE_ROLE_PROPERTY
        not in new_resource.PropertiesList
        or new_resource.getTypeIdOfProperty(
            _TIMELINE_ROLE_PROPERTY
        )
        != "App::PropertyString"
        or getattr(new_resource, _TIMELINE_ROLE_PROPERTY)
        != "resource"
        or _TIMELINE_OWNER_PROPERTY
        not in new_resource.PropertiesList
        or new_resource.getTypeIdOfProperty(
            _TIMELINE_OWNER_PROPERTY
        )
        != "App::PropertyLinkHidden"
        or getattr(new_resource, _TIMELINE_OWNER_PROPERTY)
        is not owner
    ):
        raise ValueError(
            "The new CAM object must be one exact direct resource of its owner"
        )

    final_resources = []
    for index, (name, object_id) in enumerate(
        token.old_resource_identities
    ):
        if index == token.replacement_index:
            final_resources.append(new_resource)
            continue
        resource = document.getObject(name)
        if (
            not _isLiveTimelineObject(resource, document)
            or int(resource.ID) != object_id
            or _timelineSemanticRoot(resource, document) is not owner
        ):
            raise RuntimeError(
                "A retained CAM resource changed exact identity"
            )
        final_resources.append(resource)

    if any(
        resource is new_resource
        for index, resource in enumerate(final_resources)
        if index != token.replacement_index
    ):
        raise RuntimeError(
            "The new CAM resource aliases a retained resource"
        )
    final_resources = _canonicalTimelineResourceOrder(
        owner,
        final_resources,
    )
    old_index_by_identity = {
        identity: index
        for index, identity in enumerate(
            token.old_resource_identities
        )
    }
    replacement_identity = (
        str(new_resource.Name),
        int(new_resource.ID),
    )
    state_sources = []
    final_indices_by_identity = {}
    for final_index, resource in enumerate(final_resources):
        identity = (str(resource.Name), int(resource.ID))
        final_indices_by_identity[identity] = final_index
        if identity == replacement_identity:
            state_sources.append(token.replacement_index)
        else:
            state_sources.append(old_index_by_identity[identity])
    consumer_replacements = []
    for old_index, old_identity in enumerate(
        token.old_resource_identities
    ):
        final_identity = (
            replacement_identity
            if old_index == token.replacement_index
            else old_identity
        )
        consumer_replacements.append(
            final_indices_by_identity[final_identity]
        )
    document.finalizeProvisionalTimelineOperationResourceReconciliation(
        owner,
        final_resources,
        state_sources,
        consumer_replacements,
    )
    token.consumed = True
    return new_resource


def stageTimelineResourceGraphExtension(owner):
    """Stage one retained owner before exact new resources are authored."""

    document, old_resources, direct_roots = (
        _timelineOwnedResourceGraph(owner)
    )
    document.stageTimelineOperationResourceReconciliation(
        owner,
        direct_roots,
    )
    return _TimelineResourceGraphExtension(
        document,
        owner,
        old_resources,
    )


def stageTimelineResourceGraphEdit(owner):
    """Stage a retained Job before an exact multi-resource task edit."""

    document, old_resources, direct_roots = (
        _timelineOwnedResourceGraph(owner)
    )
    document.stageTimelineOperationResourceReconciliation(
        owner,
        direct_roots,
    )
    return _TimelineResourceGraphEdit(
        document,
        owner,
        old_resources,
    )


def _validateTimelineResourceGraphEdit(owner, token):
    if (
        not isinstance(token, _TimelineResourceGraphEdit)
        or token.consumed
    ):
        raise ValueError(
            "A live unconsumed CAM resource-graph edit token is required"
        )
    document = token.document
    if (
        getattr(owner, "Document", None) is not document
        or str(getattr(document, "Uid", "") or "")
        != token.document_uid
        or (
            str(getattr(owner, "Name", "")),
            int(getattr(owner, "ID", -1)),
        )
        != token.owner_identity
        or not _isLiveTimelineObject(owner, document)
    ):
        raise RuntimeError(
            "The CAM resource-graph edit owner changed identity"
        )
    return document


def recordTimelineResourceGraphAddition(owner, token, resources):
    """Record exact provisional resources authored by one staged Job task."""

    document = _validateTimelineResourceGraphEdit(owner, token)
    old_identities = set(token.old_resource_identities)
    for resource in resources:
        identity = (
            str(getattr(resource, "Name", "")),
            int(getattr(resource, "ID", -1)),
        )
        if (
            identity in old_identities
            or identity in token.new_resource_identities
            or resource is owner
            or not _isLiveTimelineObject(resource, document)
            or not document
            .isProvisionallyEnrolledInTimelineByCurrentTransaction(
                resource
            )
            or _timelineSemanticRoot(resource, document) is not owner
        ):
            raise ValueError(
                "Every CAM graph addition must be one distinct exact "
                "current-transaction resource of its retained owner"
            )
        token.new_resource_identities.append(identity)
    return tuple(resources)


def recordTimelineResourceGraphReplacement(
    owner,
    token,
    old_resource,
    new_resource,
):
    """Record one exact old-to-new resource identity mapping."""

    old_identity = (
        str(getattr(old_resource, "Name", "")),
        int(getattr(old_resource, "ID", -1)),
    )
    return _recordTimelineResourceGraphReplacementIdentity(
        owner,
        token,
        old_identity,
        new_resource,
    )


def _recordTimelineResourceGraphReplacementIdentity(
    owner,
    token,
    old_identity,
    new_resource,
):
    """Record a replacement after its exact old identity was retired."""

    _validateTimelineResourceGraphEdit(owner, token)
    if (
        not isinstance(old_identity, tuple)
        or len(old_identity) != 2
        or not isinstance(old_identity[0], str)
        or not old_identity[0]
        or isinstance(old_identity[1], bool)
        or not isinstance(old_identity[1], int)
        or old_identity[1] < 0
    ):
        raise ValueError("An exact retired CAM resource identity is required")
    old_index = None
    if old_identity in token.old_resource_identities:
        old_index = token.old_resource_identities.index(old_identity)
        if old_index in token.replacement_identities:
            raise RuntimeError(
                "One live staged CAM resource cannot be replaced twice"
            )
    elif old_identity in token.new_resource_identities:
        token.new_resource_identities.remove(old_identity)
        for candidate_index, replacement_identity in tuple(
            token.replacement_identities.items()
        ):
            if replacement_identity == old_identity:
                old_index = candidate_index
                break
    else:
        raise ValueError(
            "The replaced CAM resource is absent from the staged graph edit"
        )
    recordTimelineResourceGraphAddition(
        owner,
        token,
        (new_resource,),
    )
    if old_index is not None:
        token.replacement_identities[old_index] = (
            str(new_resource.Name),
            int(new_resource.ID),
        )
    return new_resource


def discardTimelineResourceGraphAdditions(owner, token, resources):
    """Forget exact new task resources which are deleted before acceptance."""

    _validateTimelineResourceGraphEdit(owner, token)
    for resource in resources:
        identity = (
            str(getattr(resource, "Name", "")),
            int(getattr(resource, "ID", -1)),
        )
        if identity in token.old_resource_identities:
            continue
        if identity not in token.new_resource_identities:
            raise ValueError(
                "A discarded CAM addition was never recorded by this task"
            )
        if identity in token.replacement_identities.values():
            raise RuntimeError(
                "A replacement CAM resource requires another replacement "
                "before it can be discarded"
            )
        token.new_resource_identities.remove(identity)


def finalizeTimelineResourceGraphEdit(owner, token):
    """Publish one staged Job's exact retained, replaced, and added graph."""

    document = _validateTimelineResourceGraphEdit(owner, token)
    final_resources = []
    state_sources = []
    final_indices_by_identity = {}
    replacement_new_identities = set(
        token.replacement_identities.values()
    )

    for old_index, old_identity in enumerate(
        token.old_resource_identities
    ):
        final_identity = token.replacement_identities.get(
            old_index,
            old_identity,
        )
        resource = document.getObject(final_identity[0])
        if (
            resource is None
            or int(resource.ID) != final_identity[1]
        ):
            if old_index in token.replacement_identities:
                raise RuntimeError(
                    "A replacement CAM resource was deleted before acceptance"
                )
            continue
        if _timelineSemanticRoot(resource, document) is not owner:
            raise RuntimeError(
                "A retained CAM resource changed semantic ownership"
            )
        final_indices_by_identity[final_identity] = len(
            final_resources
        )
        final_resources.append(resource)
        state_sources.append(old_index)

    for identity in token.new_resource_identities:
        if identity in replacement_new_identities:
            continue
        resource = document.getObject(identity[0])
        if (
            resource is None
            or int(resource.ID) != identity[1]
            or _timelineSemanticRoot(resource, document) is not owner
        ):
            raise RuntimeError(
                "A recorded CAM resource addition changed before acceptance"
            )
        final_indices_by_identity[identity] = len(final_resources)
        final_resources.append(resource)
        state_sources.append(-1)

    resource_state_sources = {
        (str(resource.Name), int(resource.ID)): state_source
        for resource, state_source in zip(
            final_resources,
            state_sources,
        )
    }
    final_resources = _canonicalTimelineResourceOrder(
        owner,
        final_resources,
    )
    state_sources = [
        resource_state_sources[
            (str(resource.Name), int(resource.ID))
        ]
        for resource in final_resources
    ]
    final_indices_by_identity = {
        (str(resource.Name), int(resource.ID)): index
        for index, resource in enumerate(final_resources)
    }
    consumer_replacements = []
    for old_index, old_identity in enumerate(
        token.old_resource_identities
    ):
        final_identity = token.replacement_identities.get(
            old_index,
            old_identity,
        )
        consumer_replacements.append(
            final_indices_by_identity.get(final_identity, -1)
        )

    document.finalizeProvisionalTimelineOperationResourceReconciliation(
        owner,
        final_resources,
        state_sources,
        consumer_replacements,
    )
    token.consumed = True
    return tuple(final_resources)


def finalizeTimelineResourceGraphExtension(
    owner,
    token,
    new_resources,
):
    """Atomically append exact authored resources to a staged owner graph."""

    if (
        not isinstance(token, _TimelineResourceGraphExtension)
        or token.consumed
    ):
        raise ValueError(
            "A live unconsumed CAM resource-extension token is required"
        )
    document = token.document
    if (
        getattr(owner, "Document", None) is not document
        or str(getattr(document, "Uid", "") or "")
        != token.document_uid
        or (
            str(getattr(owner, "Name", "")),
            int(getattr(owner, "ID", -1)),
        )
        != token.owner_identity
        or not _isLiveTimelineObject(owner, document)
    ):
        raise RuntimeError(
            "The CAM resource-extension owner changed identity"
        )

    retained_resources = []
    for name, object_id in token.old_resource_identities:
        resource = document.getObject(name)
        if (
            not _isLiveTimelineObject(resource, document)
            or int(resource.ID) != object_id
            or _timelineSemanticRoot(resource, document) is not owner
        ):
            raise RuntimeError(
                "A retained CAM resource changed exact identity"
            )
        retained_resources.append(resource)

    exact_new_resources = []
    for resource in new_resources:
        if (
            resource is owner
            or resource in exact_new_resources
            or resource in retained_resources
            or not _isLiveTimelineObject(resource, document)
            or not document
            .isProvisionallyEnrolledInTimelineByCurrentTransaction(
                resource
            )
            or _TIMELINE_ROLE_PROPERTY
            not in resource.PropertiesList
            or resource.getTypeIdOfProperty(
                _TIMELINE_ROLE_PROPERTY
            )
            != "App::PropertyString"
            or getattr(resource, _TIMELINE_ROLE_PROPERTY)
            != "resource"
            or _TIMELINE_OWNER_PROPERTY
            not in resource.PropertiesList
            or resource.getTypeIdOfProperty(
                _TIMELINE_OWNER_PROPERTY
            )
            != "App::PropertyLinkHidden"
            or _timelineSemanticRoot(resource, document) is not owner
        ):
            raise ValueError(
                "Every CAM graph extension member must be one distinct exact "
                "new resource of its retained owner"
            )
        exact_new_resources.append(resource)
    if not exact_new_resources:
        raise ValueError(
            "A CAM resource graph extension requires at least one new resource"
        )

    final_resources = _canonicalTimelineResourceOrder(
        owner,
        retained_resources + exact_new_resources,
    )
    old_index_by_identity = {
        identity: index
        for index, identity in enumerate(
            token.old_resource_identities
        )
    }
    final_indices_by_identity = {}
    state_sources = []
    for final_index, resource in enumerate(final_resources):
        identity = (str(resource.Name), int(resource.ID))
        final_indices_by_identity[identity] = final_index
        state_sources.append(
            old_index_by_identity.get(identity, -1)
        )
    document.finalizeProvisionalTimelineOperationResourceReconciliation(
        owner,
        final_resources,
        state_sources,
        [
            final_indices_by_identity[identity]
            for identity in token.old_resource_identities
        ],
    )
    token.consumed = True
    return tuple(exact_new_resources)


def cancelTimelineResourceGraphExtension(owner, token):
    """Restore an unchanged retained graph after an exact extension fails."""

    if (
        not isinstance(token, _TimelineResourceGraphExtension)
        or token.consumed
    ):
        raise ValueError(
            "A live unconsumed CAM resource-extension token is required"
        )
    document = token.document
    if (
        getattr(owner, "Document", None) is not document
        or str(getattr(document, "Uid", "") or "")
        != token.document_uid
        or (
            str(getattr(owner, "Name", "")),
            int(getattr(owner, "ID", -1)),
        )
        != token.owner_identity
        or not _isLiveTimelineObject(owner, document)
    ):
        raise RuntimeError(
            "The CAM resource-extension owner changed identity"
        )

    retained_resources = []
    for name, object_id in token.old_resource_identities:
        resource = document.getObject(name)
        if (
            not _isLiveTimelineObject(resource, document)
            or int(resource.ID) != object_id
            or _timelineSemanticRoot(resource, document) is not owner
        ):
            raise RuntimeError(
                "A retained CAM resource changed during failed extension"
            )
        retained_resources.append(resource)
    retained_resources = _canonicalTimelineResourceOrder(
        owner,
        retained_resources,
    )
    old_index_by_identity = {
        identity: index
        for index, identity in enumerate(
            token.old_resource_identities
        )
    }
    final_indices_by_identity = {
        (str(resource.Name), int(resource.ID)): index
        for index, resource in enumerate(retained_resources)
    }
    document.finalizeProvisionalTimelineOperationResourceReconciliation(
        owner,
        retained_resources,
        [
            old_index_by_identity[
                (str(resource.Name), int(resource.ID))
            ]
            for resource in retained_resources
        ],
        [
            final_indices_by_identity[identity]
            for identity in token.old_resource_identities
        ],
    )
    token.consumed = True


def timelineVisualResourceGraph(tool):
    """Return a tool bit's exact explicit display-resource graph."""

    document = getattr(tool, "Document", None)
    if not _isLiveTimelineObject(tool, document):
        raise ValueError(
            "A CAM tool display graph requires one live tool object"
        )
    resources = []
    pending = []
    bit_body = getattr(tool, "BitBody", None)
    if bit_body is not None:
        pending.append(bit_body)
    pending.extend(getattr(tool, "Group", ()))
    proxy = getattr(tool, "Proxy", None)
    explicit = getattr(proxy, "timelineVisualResources", None)
    if callable(explicit):
        pending.extend(explicit())

    while pending:
        resource = pending.pop(0)
        if resource is None or resource is tool or resource in resources:
            continue
        if not _isLiveTimelineObject(resource, document):
            raise RuntimeError(
                "A CAM tool returned a missing display resource"
            )
        resources.append(resource)
        child_body = getattr(resource, "BitBody", None)
        if child_body is not None:
            pending.append(child_body)
        pending.extend(getattr(resource, "Group", ()))
    return tuple(resources)


def toolControllerResourceGraph(controller):
    """Return one new controller and its exact tool/display graph."""

    document = getattr(controller, "Document", None)
    if not _isLiveTimelineObject(controller, document):
        raise ValueError(
            "A CAM controller graph requires one live controller"
        )
    tool = getattr(controller, "Tool", None)
    if not _isLiveTimelineObject(tool, document):
        raise RuntimeError(
            "A CAM controller has no exact live tool"
        )
    return (
        controller,
        tool,
        *timelineVisualResourceGraph(tool),
    )


def publishProvisionalToolBit(tool):
    """Atomically publish one standalone tool and its display resources."""

    document = getattr(tool, "Document", None)
    resources = timelineVisualResourceGraph(tool)
    document.publishProvisionalTimelineOperationBlock(
        tool,
        resources,
    )
    return tool


def publishProvisionalToolController(controller):
    """Atomically publish one standalone controller/tool resource graph."""

    document = getattr(controller, "Document", None)
    graph = toolControllerResourceGraph(controller)
    document.publishProvisionalTimelineOperationBlock(
        controller,
        graph[1:],
    )
    return controller


def captureTimelineObjects(document):
    """Capture exact live object identities before one CAM command starts."""
    if document is None or not hasattr(document, "Objects"):
        raise ValueError(
            "A CAM timeline snapshot requires a live document"
        )
    objects = tuple(document.Objects)
    if any(obj.Document is not document for obj in objects):
        raise RuntimeError(
            "The CAM document returned a cross-document object"
        )
    return objects


def finalizeProvisionalTimelineOperation(
    operation,
    objects_before,
):
    """Canonicalize exactly the semantic objects created by one CAM command.

    ``objects_before`` remains accepted for source compatibility, but it is
    not used to infer command outputs. The native transaction enrollment is
    the authoritative creation proof for each exact object in the operation's
    explicit semantic closure.
    """
    document = getattr(operation, "Document", None)
    if not _isLiveTimelineObject(operation, document):
        raise ValueError(
            "A provisional CAM operation must be live in its document"
        )

    root = operation
    visited = []
    while (
        _TIMELINE_ROLE_PROPERTY in root.PropertiesList
        and getattr(root, _TIMELINE_ROLE_PROPERTY) == "resource"
    ):
        if any(root is candidate for candidate in visited):
            raise RuntimeError(
                "A provisional CAM operation has a cyclic owner graph"
            )
        visited.append(root)
        if (
            _TIMELINE_OWNER_PROPERTY not in root.PropertiesList
            or root.getTypeIdOfProperty(
                _TIMELINE_OWNER_PROPERTY
            )
            != "App::PropertyLinkHidden"
        ):
            raise RuntimeError(
                "A provisional CAM resource has invalid owner metadata"
            )
        root = getattr(root, _TIMELINE_OWNER_PROPERTY)
        if not _isLiveTimelineObject(root, document):
            raise RuntimeError(
                "A provisional CAM resource has no live owner"
            )

    del objects_before

    closure = list(
        document.semanticTimelineCopyClosure([root])
    )
    ordered_new_objects = [
        candidate
        for candidate in closure
        if document
        .isProvisionallyEnrolledInTimelineByCurrentTransaction(
            candidate
        )
    ]
    if not ordered_new_objects:
        return ()

    if any(root is candidate for candidate in ordered_new_objects):
        ordered_new_objects = [
            candidate
            for candidate in ordered_new_objects
            if candidate is not root
        ]
        ordered_new_objects.append(root)

    document.finalizeProvisionalTimelineOperationBlock(
        root,
        ordered_new_objects,
    )
    return tuple(ordered_new_objects)


def getPublicObject(obj):
    """getPublicObject(obj) ... returns the object which should be used to reference a feature of the given object."""
    if hasattr(obj, "getParentGeoFeatureGroup"):
        body = obj.getParentGeoFeatureGroup()
        if body:
            return getPublicObject(body)
    return obj


def clearExpressionEngine(obj):
    """clearExpressionEngine(obj) ... removes all expressions from obj.

    There is currently a bug that invalidates the DAG if an object
    is deleted that still has one or more expressions attached to it.
    Use this function to remove all expressions before deletion."""
    if hasattr(obj, "ExpressionEngine"):
        for attr, expr in obj.ExpressionEngine:
            obj.setExpression(attr, None)
