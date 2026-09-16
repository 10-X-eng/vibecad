# SPDX-License-Identifier: LGPL-2.1-or-later
"""Parametric cut states which reuse the preceding state's folded/flat mapping.

Each state owns its cut parameters. Both representations are derived from that
one feature chain. The legacy editable sheet and its JSON mutators are intact.
Factories/mutators join a caller-owned transaction and never recompute.
"""

import hashlib
import io
import json
import uuid
import zipfile

import FreeCAD as App
import Part

import SheetMetalEditable as Editable


def _mutation_thread():
    from PySide import QtCore
    app = QtCore.QCoreApplication.instance()
    if not App.GuiUp or app is None or QtCore.QThread.currentThread() != app.thread():
        raise RuntimeError("Sheet history edits require the GUI thread")


def _chain(state):
    document = Editable.state_owner(state)
    result, visited = [], set()
    while isinstance(state.Proxy, (CircleCutFeature, ProfileCutFeature)):
        if state in visited:
            raise RuntimeError("The sheet history contains a dependency cycle")
        visited.add(state)
        result.append(state)
        base = state.BaseSheet
        if base is None or Editable.state_owner(base) is not document:
            raise RuntimeError("A sheet history input is missing or belongs to another document")
        if state.getParentGeoFeatureGroup() is not base.getParentGeoFeatureGroup():
            raise RuntimeError("Sheet history states must share one coordinate frame")
        state = base
    Editable._editable_owner(state)
    return state, tuple(reversed(result))


def _active(state):
    try:
        document = Editable.state_owner(state)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as error:
        raise RuntimeError("The exact sheet state or its document was closed") from error
    if not document.isObjectUsableAtCurrentTimelinePosition(state):
        raise RuntimeError("This sheet state is not active at the current History position")
    return document


def _transaction(state):
    _mutation_thread()
    document = _active(state)
    if document.Recomputing or document.RecomputePending or document.CooperativeMutationActive:
        raise RuntimeError("Wait for the sheet's current recompute to finish")
    # Native document transactions are booked immediately and acquire their
    # journal lazily at the first mutation.
    if not document.getBookedTransactionID():
        raise RuntimeError("Sheet history edits require the caller's document transaction")
    return document


def _operation(step):
    if isinstance(step.Proxy, ProfileCutFeature):
        return Editable._definition({"version": 1, "operations": [{
            "id": step.OperationId, "kind": "profile", "profile": _profile_key(step),
        }]})["operations"][0]
    return Editable._definition({"version": 1, "operations": [{
        "id": step.OperationId, "kind": "circle", "center": [step.CenterU, step.CenterV],
        "radius": float(step.Radius),
    }]})["operations"][0]


def definition(state):
    """Read the effective shared definition, including native suppression."""
    root, steps = _chain(state)
    result = Editable._definition(root.Definition)
    result["operations"].extend(_operation(step) for step in steps if not step.Suppressed)
    return Editable._definition(result)


def get_prepared(state, *, expected_input_hash=None):
    """Read an active state for a user edit/export, without preparing geometry."""
    _active(state)
    return Editable.get_state_geometry(state, expected_input_hash=expected_input_hash)


def _snapshot(step):
    if isinstance(step.Proxy, ProfileCutFeature):
        return (step.BaseSheet, step.OperationId, getattr(step, _profile_key(step), None),
                bool(step.Suppressed))
    return (step.BaseSheet, step.OperationId, float(step.CenterU), float(step.CenterV),
            float(step.Radius), bool(step.Suppressed))


def _edit_snapshot(step):
    snapshot = _snapshot(step)
    if not isinstance(step.Proxy, ProfileCutFeature) or step.Suppressed:
        return snapshot
    profile = get_profile(step)
    # This runs only at edit/completion boundaries, never during mesh or view
    # reads. Native sketch inputs can change without replacing the Link object.
    properties = ("Geometry", "Constraints", "ExternalGeometry", "Placement",
                  "AttachmentSupport", "AttachmentOffset", "MapMode", "MapReversed",
                  "MapPathParameter", "ExpressionEngine")
    return snapshot, tuple((name, _property_content(profile, name))
                           for name in properties if name in profile.PropertiesList)


def _property_content(obj, name):
    # Native persistence wraps the property and any binary payload in ZIP.
    # Compare every saved entry, excluding the archive's wall-clock timestamps.
    data = bytes(obj.dumpPropertyContent(name, Compression=0))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return tuple((entry.filename, archive.read(entry))
                     for entry in sorted(archive.infolist(), key=lambda entry: entry.filename))


def prepare_cut_snapshot(previous, frame, operation, suppressed, base_hash, profile_shape=None):
    """Replay one native cut using detached geometry and immutable values."""
    profile_brep = None
    if suppressed:
        geometry = previous
    elif operation["kind"] == "circle":
        origin, u_axis, v_axis = frame
        center = origin + u_axis*operation["center"][0] + v_axis*operation["center"][1]
        profile = Part.Face(Part.Wire(Part.makeCircle(operation["radius"], center, previous.normal)))
        geometry = previous.cut(profile)
    else:
        geometry = previous.cut(previous.develop_profile(Editable._profile_face(profile_shape)))
        profile_brep = Editable._persistent_brep(profile_shape)
    values = [base_hash, operation, suppressed]
    if operation["kind"] == "profile":
        values.append(profile_brep)
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return geometry, digest


class CircleCutFeature(Editable.PreparedSheetState):
    def __init__(self, obj):
        self._reset()
        obj.addProperty("App::PropertyLink", "BaseSheet", "Sheet", "Preceding shared sheet state")
        obj.addProperty("App::PropertyString", "OperationId", "Cut", "Stable identity of this cut")
        obj.addProperty("App::PropertyLength", "Radius", "Cut", "Developed hole radius")
        obj.addProperty("App::PropertyFloat", "CenterU", "Cut", "Center in the sheet's developed U frame")
        obj.addProperty("App::PropertyFloat", "CenterV", "Cut", "Center in the sheet's developed V frame")
        obj.addProperty("App::PropertyString", "Operation", "Results", "Derived shared cut description",
                        attr=16)
        obj.addProperty("App::PropertyString", "Definition", "Results", "Effective shared definition",
                        attr=16)
        obj.addProperty("Part::PropertyPartShape", "FlatShape", "Results", "Developed cut result")
        obj.addProperty("App::PropertyString", "PreparedInputHash", "Results", "Prepared state fingerprint")
        obj.addExtension("App::SuppressibleExtensionPython")
        for name in ("BaseSheet", "OperationId", "Operation", "Definition"):
            obj.setEditorMode(name, 1)
        for name in ("FlatShape", "PreparedInputHash"):
            obj.setEditorMode(name, 2)
        obj.OperationId = uuid.uuid4().hex
        obj.Proxy = self

    def _reset(self):
        self._geometry = self._base_geometry = self._key = self._frame = None

    def onChanged(self, obj, name):
        if name in ("BaseSheet", "OperationId", "Radius", "CenterU", "CenterV", "Suppressed"):
            self._reset()

    def dumps(self):
        return None

    def loads(self, state):
        self._reset()

    def onDocumentRestored(self, obj):
        self._reset()

    def execute(self, obj):
        self._reset()
        key = _snapshot(obj)
        operation = _operation(obj)
        obj.Operation = json.dumps(operation, sort_keys=True, separators=(",", ":"))
        obj.Definition = Editable._encode(definition(obj))
        base = obj.BaseSheet
        from SheetMetalPreparation import prepare_for_recompute
        previous = prepare_for_recompute(base)
        frame = base.Proxy._frame
        geometry, digest = prepare_cut_snapshot(
            previous, frame, operation, bool(obj.Suppressed), base.PreparedInputHash)
        if _snapshot(obj) != key or Editable.get_state_geometry(base) is not previous:
            raise RuntimeError("Sheet inputs changed during cut preparation")
        obj.Shape = geometry.folded
        obj.FlatShape = geometry.flat
        obj.PreparedInputHash = digest
        self._key, self._base_geometry = key, previous
        self._frame, self._geometry = frame, geometry

    def prepared_geometry(self, obj, *, expected_input_hash=None):
        # Walk the exact dependency chain once. Recursively validating every
        # predecessor's complete ancestry makes a single read quadratic.
        root, steps = _chain(obj)
        previous = Editable.get_prepared(root)
        for step in steps:
            proxy = step.Proxy
            if (proxy._geometry is None or proxy._key != _snapshot(step)
                    or {"Touched", "Invalid"}.intersection(step.State)
                    or proxy._base_geometry is not previous
                    or (isinstance(proxy, ProfileCutFeature) and not proxy.profile_current(step))):
                raise RuntimeError("Prepare the current cut state before editing")
            previous = proxy._geometry
        if expected_input_hash is not None and expected_input_hash != obj.PreparedInputHash:
            raise RuntimeError("The cut state changed after this edit was prepared")
        return self._geometry

    def edit_expectation(self, obj):
        from SheetMetalOperations import _expectation
        root, steps = _chain(obj)
        return _expectation(root), tuple(_edit_snapshot(step) for step in steps)


def create_circle_step(base, center, radius, *, representation="flat", region=None,
                       name="SheetHole"):
    document = _transaction(base)
    get_prepared(base)
    radius = Editable._number(radius)
    if radius <= 0:
        raise ValueError("A cut radius must be positive")
    center = App.Vector(*(Editable._number(value) for value in center))
    u, v = Editable._cut_center(base, center, representation, region)
    from SheetMetalCutGui import CutViewProvider, ensure_commands_registered
    ensure_commands_registered()
    obj = document.addObject("Part::FeaturePython", name,
                             viewType="PartGui::ViewProviderCachedDetailsPython")
    CircleCutFeature(obj)
    obj.BaseSheet = base
    obj.Radius, obj.CenterU, obj.CenterV = radius, u, v
    container = base.getParentGeoFeatureGroup()
    if container is not None:
        container.addObject(obj)
    obj.Operation = json.dumps(_operation(obj), sort_keys=True, separators=(",", ":"))
    obj.Definition = Editable._encode(definition(obj))
    CutViewProvider(obj.ViewObject)
    _publish(obj, base)
    base.Visibility = False
    return obj


def _publish(step, base, *, editor_command="SheetMetal_EditHistoryCut"):
    import PartGui
    from tool_impl.service.domain_runtime import (
        _ensure_timeline_property, _mark_timeline_operation, finalize_new_timeline_operation,
    )
    replaced = PartGui.setModelingReplacedInputs(step, [base])
    _ensure_timeline_property(step, "App::PropertyString", "VibeCADTimelineEditCommand",
                              "Edit this sheet cut")
    step.VibeCADTimelineEditCommand = editor_command
    if replaced:
        finalize_new_timeline_operation(step)
    else:
        _mark_timeline_operation(step)
        step.Document.finalizeProvisionalTimelineOperationBlock(step, [step])


def update_circle_step(step, *, radius=None, center=None, representation="flat", region=None):
    _transaction(step)
    if not isinstance(step.Proxy, CircleCutFeature):
        raise ValueError("Select one circular sheet cut")
    if radius is None and center is None:
        raise ValueError("Choose a radius or center to change")
    if radius is not None:
        radius = Editable._number(radius)
        if radius <= 0:
            raise ValueError("A cut radius must be positive")
    position = None
    if center is not None:
        center = App.Vector(*(Editable._number(value) for value in center))
        position = Editable._cut_center(step, center, representation, region, updating=True)
    if radius is not None:
        step.Radius = radius
    if position is not None:
        step.CenterU, step.CenterV = position
    step.Operation = json.dumps(_operation(step), sort_keys=True, separators=(",", ":"))
    step.Definition = Editable._encode(definition(step))


def start_radius_edit(step, radius, *, expected_revision):
    """Use the shared short transaction and native async-completion coordinator."""
    _mutation_thread()
    import SheetMetalOperations as Operations
    from VibeCADNativeMutation import NativeMutationDraft, run_human_mutation
    from VibeCADNativeTargets import object_identity
    _active(step)
    Operations._check_revision(step, expected_revision)
    def mutate(document):
        if document is not step.Document:
            raise RuntimeError("The cut belongs to another document")
        update_circle_step(step, radius=radius)
        return NativeMutationDraft(value=step.Proxy.edit_expectation(step),
                                   changed=(object_identity(step),))
    def verify(document, draft):
        if document is not step.Document or step.Proxy.edit_expectation(step) != draft.value:
            raise RuntimeError("The requested cut parameters did not persist")
        return {"operation": "update_circle_step", "operation_id": step.OperationId,
                "object_name": step.Name}
    result = run_human_mutation(document=step.Document, transaction_name="Edit sheet hole",
                                mutate=mutate, verify=verify)
    run = Operations.EditRun(step, dict(result), step.Proxy.edit_expectation(step))
    try:
        step.Document.recomputeAsync()
    except Exception as error:
        run._finish("failed", str(error))
    else:
        run._schedule()
    return run


def _profile_key(step):
    return Editable._PROFILE_PREFIX + step.OperationId


def _check_profile_input(state, profile):
    document = Editable.state_owner(state)
    if profile is None:
        raise ValueError("The cut's native sketch is missing")
    if Editable._owner(profile) is not document:
        raise RuntimeError("The cut profile must belong to the sheet's own document")
    if not profile.isDerivedFrom("Sketcher::SketchObject"):
        raise ValueError("A cut profile must be a native sketch")
    if profile.getParentGeoFeatureGroup() is not state.getParentGeoFeatureGroup():
        raise RuntimeError("The cut profile must share the sheet's coordinate frame")


def _check_profile(state, profile):
    _check_profile_input(state, profile)
    if state in profile.OutListRecursive:
        raise ValueError("A cut profile must not depend on the resulting sheet")


def get_profile(step):
    """Resolve the exact native sketch link owned by this profile operation."""
    Editable.state_owner(step)
    if not isinstance(step.Proxy, ProfileCutFeature):
        raise ValueError("Select one sketch cut")
    key = _profile_key(step)
    if key not in step.PropertiesList or step.getTypeIdOfProperty(key) != "App::PropertyLink":
        raise ValueError("The cut's native sketch link is missing")
    profile = getattr(step, key)
    _check_profile(step, profile)
    return profile


class ProfileCutFeature(Editable.PreparedSheetState):
    """One native sketch dependency, retaining the preceding bend mapping."""

    def __init__(self, obj):
        self._reset()
        obj.addProperty("App::PropertyLink", "BaseSheet", "Sheet", "Preceding shared sheet state")
        obj.addProperty("App::PropertyString", "OperationId", "Cut", "Stable identity of this cut")
        obj.addProperty("App::PropertyString", "Operation", "Results", "Derived shared cut description",
                        attr=16)
        obj.addProperty("App::PropertyString", "Definition", "Results", "Effective shared definition",
                        attr=16)
        obj.addProperty("Part::PropertyPartShape", "FlatShape", "Results", "Developed cut result")
        obj.addProperty("App::PropertyString", "PreparedInputHash", "Results", "Prepared state fingerprint")
        obj.addExtension("App::SuppressibleExtensionPython")
        obj.OperationId = uuid.uuid4().hex
        key = _profile_key(obj)
        obj.addProperty("App::PropertyLink", key, "Profiles", "Native sketch defining this cut")
        for name in ("BaseSheet", "OperationId", "Operation", "Definition"):
            obj.setEditorMode(name, 1)
        for name in (key, "FlatShape", "PreparedInputHash"):
            obj.setEditorMode(name, 2)
        obj.Proxy = self

    def _reset(self):
        self._geometry = self._base_geometry = self._key = self._frame = self._profile_shape = None

    def onChanged(self, obj, name):
        if name in ("BaseSheet", "OperationId", "Suppressed") or Editable._PROFILE_LINK.fullmatch(name):
            self._reset()

    def dumps(self):
        return None

    def loads(self, state):
        self._reset()

    def onDocumentRestored(self, obj):
        self._reset()

    def profile_current(self, obj):
        if obj.Suppressed:
            return True
        try:
            profile = get_profile(obj)
            return (self._profile_shape is not None
                    and profile.Shape.isSame(self._profile_shape)
                    and not any({"Touched", "Invalid"}.intersection(dep.State)
                                for dep in (profile, *profile.OutListRecursive)))
        except (RuntimeError, ValueError, AttributeError, ReferenceError, TypeError):
            return False

    def execute(self, obj):
        self._reset()
        key = _snapshot(obj)
        operation = _operation(obj)
        obj.Operation = json.dumps(operation, sort_keys=True, separators=(",", ":"))
        obj.Definition = Editable._encode(definition(obj))
        base = obj.BaseSheet
        from SheetMetalPreparation import prepare_for_recompute
        previous = prepare_for_recompute(base)
        profile, shape, snapshot = None, None, None
        if not obj.Suppressed:
            profile = get_profile(obj)
            shape = profile.Shape
            snapshot = shape.copy()
        geometry, digest = prepare_cut_snapshot(previous, base.Proxy._frame, operation,
                                              bool(obj.Suppressed), base.PreparedInputHash, snapshot)
        if (_snapshot(obj) != key or Editable.get_state_geometry(base) is not previous
                or (profile is not None and not profile.Shape.isSame(shape))):
            raise RuntimeError("Sheet inputs changed during sketch-cut preparation")
        obj.Shape, obj.FlatShape = geometry.folded, geometry.flat
        obj.PreparedInputHash = digest
        self._key, self._base_geometry, self._profile_shape = key, previous, shape
        self._frame, self._geometry = base.Proxy._frame, geometry

    def prepared_geometry(self, obj, *, expected_input_hash=None):
        # The shared validator walks mixed hole/sketch chains once and checks
        # each profile snapshot without serializing geometry on view reads.
        return CircleCutFeature.prepared_geometry(self, obj, expected_input_hash=expected_input_hash)

    def edit_expectation(self, obj):
        return CircleCutFeature.edit_expectation(self, obj)


def create_profile_step(base, profile, *, name="SheetSketchCut"):
    """Add one sketch-cut History state in the caller's short transaction."""
    document = _transaction(base)
    get_prepared(base)
    # The input sheet may support the sketch: the new result depends on both.
    # Only attaching a sketch to that result creates a cycle (checked on read
    # and replacement by _check_profile).
    _check_profile_input(base, profile)
    if not document.isObjectUsableAtCurrentTimelinePosition(profile):
        raise RuntimeError("The sketch is not active at the current History position")
    if any({"Touched", "Invalid"}.intersection(dep.State)
           for dep in (profile, *profile.OutListRecursive)):
        raise RuntimeError("Prepare the current sketch before creating its cut")
    from SheetMetalCutGui import ProfileCutViewProvider, ensure_commands_registered
    ensure_commands_registered()
    obj = document.addObject("Part::FeaturePython", name,
                             viewType="PartGui::ViewProviderCachedDetailsPython")
    ProfileCutFeature(obj)
    obj.BaseSheet = base
    setattr(obj, _profile_key(obj), profile)
    container = base.getParentGeoFeatureGroup()
    if container is not None:
        container.addObject(obj)
    obj.Operation = json.dumps(_operation(obj), sort_keys=True, separators=(",", ":"))
    obj.Definition = Editable._encode(definition(obj))
    ProfileCutViewProvider(obj.ViewObject)
    _publish(obj, base, editor_command="SheetMetal_EditHistoryProfile")
    base.Visibility = False
    profile.Visibility = False
    return obj


def profile_coordinates(step, point, *, representation="flat", region=None):
    """Map an existing cut handle into its linked sketch in either representation."""
    get_prepared(step)
    profile = get_profile(step)
    for coordinate in point:
        Editable._number(coordinate)
    u, v = Editable._cut_center(step, point, representation, region, updating=True)
    origin, along_u, along_v = step.Proxy._frame
    flat = origin + along_u*u + along_v*v
    placement = profile.Placement
    geometry = step.Proxy._base_geometry
    normal = placement.Rotation.multVec(App.Vector(0, 0, 1))
    authored = flat
    if (abs(abs(normal.dot(geometry.normal))-1) > 1e-6
            or abs((placement.Base-origin).dot(geometry.normal)) > 1e-6):
        # Reuse the prepared correspondence, including points removed by this
        # cut. Moving a handle must not re-unfold or run profile booleans in GUI.
        candidates = []
        for patch in geometry.mapping.regions:
            if patch.kind != "plane":
                continue
            outward = patch._face.normalAt(0, 0)
            if abs(abs(outward.dot(normal))-1) > 1e-6:
                continue
            depth = (placement.Base-patch._face.CenterOfMass).dot(outward)
            if min(abs(depth), abs(depth+geometry.mapping.thickness)) > 1e-6:
                continue
            try:
                candidates.append(patch.to_folded(flat) + outward*depth)
            except ValueError:
                continue
        if not candidates:
            raise ValueError("The selected point does not map to the cut sketch's sheet region")
        authored = candidates[0]
        if any((candidate-authored).Length > 1e-6 for candidate in candidates[1:]):
            raise ValueError("The folded cut sketch has ambiguous sheet-region correspondence")
    local = placement.inverse().multVec(authored)
    if abs(local.z) > 1e-6:
        raise ValueError("The cut profile must lie on the developed sheet plane")
    return App.Vector(local.x, local.y, 0)


def replace_profile_step(step, profile):
    """Repair or replace a sketch link while retaining the cut's History identity."""
    document = _transaction(step)
    if not isinstance(step.Proxy, ProfileCutFeature):
        raise ValueError("Select one sketch cut")
    _check_profile(step, profile)
    if not document.isObjectUsableAtCurrentTimelinePosition(profile):
        raise RuntimeError("The sketch is not active at the current History position")
    timelines = document.findObjects("App::DocumentTimeline")
    if len(timelines) != 1:
        raise RuntimeError("The document must have one native operation History")
    operations = list(timelines[0].Operations)
    if step not in operations or profile not in operations:
        raise RuntimeError("The cut and its replacement sketch must have native History entries")
    if operations.index(profile) > operations.index(step):
        # Rebase the complete downstream operation closure before adding the
        # new link. Native validation keeps ownership blocks and chronology intact.
        document.reorderTimelineOperationDependentClosureAfter(step, profile)
    setattr(step, _profile_key(step), profile)
    profile.Visibility = False
