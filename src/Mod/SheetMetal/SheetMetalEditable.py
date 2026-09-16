# SPDX-License-Identifier: LGPL-2.1-or-later
"""A shared document definition for folded and developed sheet-metal edits.

The source is an existing parametric SheetMetal feature. Its upstream history
continues to own base geometry; this feature owns the manufacturing allowance
and cut history. Shape and FlatShape are derived outputs, never separate edit
histories. Mutators join the caller's document transaction and do not recompute.
Commands must schedule native asynchronous recompute after their immediate edit.
"""

import hashlib
import json
import math
import re
import uuid

import FreeCAD as App
import Part

from SheetMetalEditGeometry import SheetGeometry
from SheetMetalNewUnfolder import BendAllowanceCalculator
from SheetMetalSourceFeatures import nominal_thickness


_INPUT_PROPERTIES = {"SourceFace", "Definition", "KFactor", "Material", "ProfileSources"}
_PROFILE_PREFIX = "CutProfile_"
_PROFILE_LINK = re.compile(_PROFILE_PREFIX+r"[0-9a-f]{32}")


def _persistent_brep(shape):
    # Use the same numeric precision as native document persistence. Reading
    # through OCCT also normalizes its transient Checked flags; never parse or
    # rewrite topology flags ourselves. Signed zero is geometrically identical
    # but can change when OCCT reconstructs coordinate frames on load.
    restored = Part.Shape()
    restored.importBrepFromString(shape.exportBrepToString(True))
    return re.sub(r"(?<!\S)-0\.0+(?=\s|$)", lambda match: match[0][1:],
                  restored.exportBrepToString(True))


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Sheet dimensions must be finite numbers")
    return float(value)


def _definition(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid sheet definition JSON") from exc
    if (not isinstance(value, dict) or set(value) != {"version", "operations"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["operations"], list)):
        raise ValueError("Unsupported sheet definition format")
    operations = []
    ids = set()
    for operation in value["operations"]:
        if not isinstance(operation, dict):
            raise ValueError("Unsupported sheet operation")
        identity = operation.get("id")
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("Sheet operations need unique nonempty IDs")
        if (operation.get("kind") == "circle"
                and set(operation) == {"id", "kind", "center", "radius"}):
            center = operation["center"]
            if not isinstance(center, (list, tuple)) or len(center) != 2:
                raise ValueError("A cut center needs two developed sheet coordinates")
            radius = _number(operation["radius"])
            if radius <= 0:
                raise ValueError("A cut radius must be positive")
            operations.append({"id": identity, "kind": "circle",
                               "center": [_number(center[0]), _number(center[1])],
                               "radius": radius})
        elif (operation.get("kind") == "profile" and set(operation) == {"id", "kind", "profile"}
              and isinstance(operation["profile"], str)
              and _PROFILE_LINK.fullmatch(operation["profile"])):
            operations.append(dict(operation))
        else:
            raise ValueError("Unsupported sheet operation")
        ids.add(identity)
    return {"version": 1, "operations": operations}


def _encode(value):
    return json.dumps(_definition(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _owner(obj):
    try:
        document = obj.Document
        if (document is None or App.getDocument(document.Name) is not document
                or document.getObject(obj.Name) is not obj):
            raise RuntimeError("The sheet no longer belongs to the open document")
    except (ReferenceError, NameError) as exc:
        raise RuntimeError("The sheet document is no longer open") from exc
    return document


def _inputs(obj):
    document = _editable_owner(obj)
    source, names = obj.SourceFace
    if (source is None or source.Document is not document
            or document.getObject(source.Name) is not source or len(names) != 1):
        raise RuntimeError("Select one source face in the sheet's own document")
    profiles = _profile_sources(obj, _definition(obj.Definition))
    return source, names[0], obj.Definition, float(obj.KFactor), obj.Material, profiles


def _editable_owner(obj):
    document = _owner(obj)
    if not isinstance(getattr(obj, "Proxy", None), EditableSheetFeature):
        raise RuntimeError("The selected object is not an editable sheet")
    return document


def _ensure_profile_sources(obj):
    # Older circular-only features need no migration on restore. Add the native
    # dependency property only when a caller first adds a profile operation.
    if "ProfileSources" not in obj.PropertiesList:
        obj.addProperty("App::PropertyLinkList", "ProfileSources", "Sheet",
                        "Native sketches referenced by shared cut operations")
        obj.setEditorMode("ProfileSources", 1)


def _check_profile_owner(obj, profile):
    document = _editable_owner(obj)
    if _owner(profile) is not document:
        raise RuntimeError("The cut profile must belong to the sheet's own document")
    if not profile.isDerivedFrom("Sketcher::SketchObject"):
        raise ValueError("A cut profile must be a native sketch")
    if profile.getParentGeoFeatureGroup() is not obj.getParentGeoFeatureGroup():
        raise RuntimeError("The cut profile must share the sheet's coordinate frame")
    if obj in profile.OutListRecursive:
        raise ValueError("A cut profile must not depend on the resulting sheet")


def _profile_sources(obj, definition):
    names = {operation["profile"] for operation in definition["operations"]
             if operation["kind"] == "profile"}
    profiles = []
    properties = set(obj.PropertiesList)
    for name in sorted(names):
        if name not in properties or obj.getTypeIdOfProperty(name) != "App::PropertyLink":
            raise ValueError("A referenced cut profile is missing")
        profile = getattr(obj, name)
        if profile is None:
            raise ValueError("A referenced cut profile is missing")
        _check_profile_owner(obj, profile)
        profiles.append((name, profile))
    return tuple(profiles)


def _sync_profile_sources(obj, definition):
    used = {operation["profile"] for operation in definition["operations"]
            if operation["kind"] == "profile"}
    profiles = []
    for name in obj.PropertiesList:
        if _PROFILE_LINK.fullmatch(name) and obj.getTypeIdOfProperty(name) == "App::PropertyLink":
            if name not in used:
                # Retain the property for Undo/native persistence, but release
                # the unused dependency. Deleting one sketch cannot shift the
                # binding of any other operation as it could with list indices.
                setattr(obj, name, None)
            else:
                profile = getattr(obj, name)
                if profile is not None and profile not in profiles:
                    profiles.append(profile)
    if "ProfileSources" in obj.PropertiesList:
        obj.ProfileSources = profiles


def _profile_face(shape):
    wires = shape.Wires
    if (not wires or any(not wire.isClosed() for wire in wires)
            or sum(len(wire.Edges) for wire in wires) != len(shape.Edges)):
        raise ValueError("A cut profile must contain only closed wires")
    face = Part.makeFace(wires, "Part::FaceMakerBullseye")
    if not face.isValid() or not face.Faces:
        raise ValueError("The cut profile does not form valid planar faces")
    return face


def prepare_geometry_snapshot(snapshot, face_name, definition, factor, material,
                              profile_snapshots, source_thickness):
    """Build geometry from detached shapes and values, without document access."""
    if not math.isfinite(factor) or not 0 <= factor <= 1:
        raise ValueError("ANSI K-factor must be between zero and one")
    # Resolve the native subelement reference explicitly; no guessed face
    # index or alternate face is used if the source no longer resolves it.
    face = snapshot.getElement(face_name)
    if not isinstance(face, Part.Face) or not isinstance(face.Surface, Part.Plane):
        raise ValueError("The stationary reference must resolve to a planar face")
    root = snapshot.findSubShape(face)[1]-1
    geometry = SheetGeometry.prepare(
        snapshot, root, BendAllowanceCalculator.from_single_value(factor, "ansi"),
        expected_thickness=source_thickness)
    origin = face.valueAt(0, 0)
    along_u = face.valueAt(1, 0)-origin
    along_v = face.valueAt(0, 1)-origin
    for operation in definition["operations"]:
        if operation["kind"] == "circle":
            u, v = operation["center"]
            center = origin+along_u*u+along_v*v
            profile = Part.Face(Part.Wire(Part.makeCircle(
                operation["radius"], center, geometry.normal)))
        else:
            profile = _profile_face(profile_snapshots[operation["profile"]])
        geometry = geometry.cut(profile)
    fingerprint = hashlib.sha256()
    fingerprint.update(_persistent_brep(snapshot).encode("utf-8"))
    fingerprint.update(json.dumps([face_name, definition, factor, material],
                                  sort_keys=True, allow_nan=False).encode("utf-8"))
    for name, profile in sorted(profile_snapshots.items()):
        fingerprint.update(json.dumps([name, _persistent_brep(profile)]).encode("utf-8"))
    return geometry, (origin, along_u, along_v), fingerprint.hexdigest()


class PreparedSheetState:
    """Common read contract for independently persisted sheet history states.

    Legacy mutators still require EditableSheetFeature. New state types provide
    prepared_geometry without implicitly accepting those mutators.
    """

    def prepared_geometry(self, obj, *, expected_input_hash=None):
        raise NotImplementedError


def state_owner(obj):
    document = _owner(obj)
    if not isinstance(getattr(obj, "Proxy", None), PreparedSheetState):
        raise RuntimeError("The selected object is not a shared sheet state")
    return document


def get_state_geometry(obj, *, expected_input_hash=None):
    """Read a prepared state; native recompute may read a suppressed predecessor."""
    state_owner(obj)
    return obj.Proxy.prepared_geometry(obj, expected_input_hash=expected_input_hash)


class EditableSheetFeature(PreparedSheetState):
    def __init__(self, obj):
        self._reset()
        obj.addProperty("App::PropertyLinkSub", "SourceFace", "Sheet",
                        "Stationary face of the upstream sheet feature")
        obj.addProperty("App::PropertyString", "Definition", "Sheet",
                        "Versioned shared cut history")
        obj.addProperty("App::PropertyFloat", "KFactor", "Sheet",
                        "ANSI neutral-axis allowance factor")
        obj.addProperty("App::PropertyString", "Material", "Sheet",
                        "Material selected for this sheet")
        _ensure_profile_sources(obj)
        obj.addProperty("Part::PropertyPartShape", "FlatShape", "Results",
                        "Developed solid derived from the same definition")
        obj.addProperty("App::PropertyString", "PreparedInputHash", "Results",
                        "Content fingerprint of the prepared input snapshot")
        obj.setEditorMode("Definition", 1)
        obj.setEditorMode("FlatShape", 2)
        obj.setEditorMode("PreparedInputHash", 2)
        obj.Definition = _encode({"version": 1, "operations": []})
        obj.KFactor = 0.42
        obj.Proxy = self

    def _reset(self):
        self._geometry = None
        self._key = None
        self._source_shape = None
        self._source_thickness = None
        self._profile_shapes = ()
        self._frame = None

    def prepared_geometry(self, obj, *, expected_input_hash=None):
        return get_prepared(obj, expected_input_hash=expected_input_hash)

    def onChanged(self, obj, name):
        if name in _INPUT_PROPERTIES or _PROFILE_LINK.fullmatch(name):
            self._reset()

    def onDocumentRestored(self, obj):
        # Serialized output shapes remain available for display. Rebuild the
        # in-memory mapping only when a command schedules geometry preparation.
        self._reset()

    def dumps(self):
        return None

    def loads(self, state):
        self._reset()

    def execute(self, obj):
        self._reset()
        key = _inputs(obj)
        source, face_name, definition_text, factor, material, profiles = key
        definition = _definition(definition_text)
        if not math.isfinite(factor) or not 0 <= factor <= 1:
            raise ValueError("ANSI K-factor must be between zero and one")
        source_shape = source.Shape
        source_thickness = nominal_thickness(source)
        snapshot = source_shape.copy()
        profile_shapes = tuple((name, profile, profile.Shape) for name, profile in profiles)
        profile_snapshots = {name: shape.copy() for name, _, shape in profile_shapes}
        geometry, frame, fingerprint = prepare_geometry_snapshot(
            snapshot, face_name, definition, factor, material, profile_snapshots, source_thickness)
        if (_inputs(obj) != key or not source.Shape.isSame(source_shape)
                or nominal_thickness(source) != source_thickness
                or any(not profile.Shape.isSame(shape) for _, profile, shape in profile_shapes)):
            raise RuntimeError("Sheet inputs changed during geometry preparation")
        # Complete both solids before publishing any derived property. The
        # fingerprint is published last and the cache becomes readable afterward.
        obj.Shape = geometry.folded
        obj.FlatShape = geometry.flat
        obj.PreparedInputHash = fingerprint
        self._key = key
        self._source_shape = source_shape
        self._source_thickness = source_thickness
        self._profile_shapes = profile_shapes
        self._frame = frame
        self._geometry = geometry


def create_sheet(source, reference_face, *, name="EditableSheet", view_type=None):
    """Add an editable sheet to the exact source document; caller recomputes."""
    document = _owner(source)
    if not isinstance(reference_face, str):
        raise ValueError("A stationary face reference is required")
    face = source.Shape.getElement(reference_face)
    if not isinstance(face, Part.Face) or not isinstance(face.Surface, Part.Plane):
        raise ValueError("The stationary reference must be a planar source face")
    view_options = {} if view_type is None else {"viewType": view_type}
    obj = document.addObject("Part::FeaturePython", name, **view_options)
    EditableSheetFeature(obj)
    obj.SourceFace = source, [reference_face]
    container = source.getParentGeoFeatureGroup()
    if container is not None:
        # Shapes and picks remain in the source container's local frame. Native
        # scene transforms move both together without rebuilding the geometry.
        container.addObject(obj)
    return obj


def get_prepared(obj, *, expected_input_hash=None):
    """Return current prepared geometry without performing geometry work.

    A restored file retains its solid outputs, but its mapping must be prepared
    asynchronously before accepting mapped edits. A content hash is an input
    fingerprint, not a globally unique document identity or monotonic revision.
    """
    key = _inputs(obj)
    proxy = obj.Proxy
    if (not isinstance(proxy, EditableSheetFeature) or proxy._geometry is None
            or proxy._key != key or not key[0].Shape.isSame(proxy._source_shape)
            or nominal_thickness(key[0]) != proxy._source_thickness
            or any(not profile.Shape.isSame(shape) for _, profile, shape in proxy._profile_shapes)
            or "Touched" in obj.State or "Invalid" in obj.State
            or any({"Touched", "Invalid"}.intersection(dependency.State)
                   for dependency in obj.OutListRecursive)):
        raise RuntimeError("Prepare the current sheet geometry before editing")
    if expected_input_hash is not None and expected_input_hash != obj.PreparedInputHash:
        raise RuntimeError("The sheet changed after this edit was prepared")
    return proxy._geometry


def set_definition(obj, definition):
    """Validate and replace shared history within the caller's transaction."""
    _editable_owner(obj)
    encoded = _encode(definition)
    parsed = _definition(encoded)
    _profile_sources(obj, parsed)
    obj.Definition = encoded
    _sync_profile_sources(obj, parsed)


def create_cut_sketch(obj, *, name="SheetCutProfile", expected_input_hash=None):
    """Create a native sketch on the stationary face; caller draws/recomputes.

    Attachment is to the upstream source, never to this result (which would
    create a dependency cycle once the sketch becomes a cut operation).
    """
    get_prepared(obj, expected_input_hash=expected_input_hash)
    document = _editable_owner(obj)
    sketch = document.addObject("Sketcher::SketchObject", name)
    container = obj.getParentGeoFeatureGroup()
    if container is not None:
        container.addObject(sketch)
    source, names = obj.SourceFace
    sketch.AttachmentSupport = [(source, names[0])]
    sketch.MapMode = "FlatFace"
    return sketch


def add_profile_cut(obj, profile, *, expected_input_hash=None):
    """Link an editable closed-wire sketch within the caller's transaction."""
    get_prepared(obj, expected_input_hash=expected_input_hash)
    _check_profile_owner(obj, profile)
    definition = _definition(obj.Definition)
    identity = uuid.uuid4().hex
    link_name = _PROFILE_PREFIX+identity
    definition["operations"].append({"id": identity, "kind": "profile", "profile": link_name})
    _ensure_profile_sources(obj)
    # A per-operation native link survives recursive copy/import name remaps
    # and retains a missing binding if its sketch is deleted. The JSON contains
    # this stable property key, never a document object name to resolve manually.
    obj.addProperty("App::PropertyLink", link_name, "Profiles", "Shared cut profile")
    obj.setEditorMode(link_name, 2)
    setattr(obj, link_name, profile)
    set_definition(obj, definition)
    return identity


def profile_coordinates(obj, profile, point, *, representation="flat", region=None,
                        allow_removed=False, expected_input_hash=None):
    """Map a pick in the sheet's container frame into native sketch XY space.

    Existing profile points can be edited through material that their cut has
    already removed by explicitly setting allow_removed. This performs no
    mutation or geometry preparation; the caller edits the sketch afterward.
    """
    get_prepared(obj, expected_input_hash=expected_input_hash)
    _check_profile_owner(obj, profile)
    if any({"Touched", "Invalid"}.intersection(dependency.State)
           for dependency in (profile, *profile.OutListRecursive)):
        raise RuntimeError("Prepare the current profile before mapping an edit")
    for coordinate in (point.x, point.y, point.z):
        _number(coordinate)
    u, v = _cut_center(obj, point, representation, region, updating=allow_removed)
    origin, along_u, along_v = obj.Proxy._frame
    local = profile.Placement.inverse().multVec(origin+along_u*u+along_v*v)
    if abs(local.z) > 1e-6:
        raise ValueError("The cut profile must lie on the developed sheet plane")
    return App.Vector(local.x, local.y, 0)


def _cut_center(obj, center, representation, region, *, updating=False):
    geometry = get_state_geometry(obj)
    if representation == "folded":
        if region is None:
            raise ValueError("A folded edit needs a region from the prepared sheet")
        if updating:
            # Existing feature anchors may lie inside the void made by that
            # feature. Resolve against its base material surface for editing.
            center = geometry._region(region).to_flat(center)
        else:
            center = geometry.to_flat(region, center)
    elif representation != "flat":
        raise ValueError("Representation must be folded or flat")
    origin, along_u, along_v = obj.Proxy._frame
    if abs((center-origin).dot(geometry.normal)) > 1e-6:
        raise ValueError("The cut center must lie on the developed sheet plane")
    delta = center-origin
    return [delta.dot(along_u), delta.dot(along_v)]


def add_circle_cut(obj, center, radius, *, representation="flat", region=None,
                   expected_input_hash=None):
    get_prepared(obj, expected_input_hash=expected_input_hash)
    position = _cut_center(obj, center, representation, region)
    definition = _definition(obj.Definition)
    identity = uuid.uuid4().hex
    definition["operations"].append({"id": identity, "kind": "circle",
                                     "center": position, "radius": radius})
    set_definition(obj, definition)
    return identity


def update_circle_cut(obj, operation_id, *, center=None, radius=None,
                      representation="flat", region=None, expected_input_hash=None):
    _editable_owner(obj)
    # Parameter repair needs only the definition. Requiring the failed result
    # here would make an oversized/invalid cut impossible to repair in its panel.
    if center is not None or expected_input_hash is not None:
        get_prepared(obj, expected_input_hash=expected_input_hash)
    definition = _definition(obj.Definition)
    operation = next((op for op in definition["operations"] if op["id"] == operation_id), None)
    if operation is None:
        raise ValueError("The cut operation no longer exists")
    if operation["kind"] != "circle":
        raise ValueError("Edit a profile cut through its native sketch")
    if center is not None:
        operation["center"] = _cut_center(obj, center, representation, region, updating=True)
    if radius is not None:
        operation["radius"] = radius
    set_definition(obj, definition)


def remove_operation(obj, operation_id, *, expected_input_hash=None):
    """Remove an operation, including one whose geometry currently fails."""
    _editable_owner(obj)
    if expected_input_hash is not None:
        get_prepared(obj, expected_input_hash=expected_input_hash)
    definition = _definition(obj.Definition)
    operations = definition["operations"]
    if not any(operation["id"] == operation_id for operation in operations):
        raise ValueError("The sheet operation no longer exists")
    definition["operations"] = [op for op in operations if op["id"] != operation_id]
    # Permit repair even if another remaining operation also has a missing
    # profile. Recompute will keep the result invalid until all faults are fixed.
    obj.Definition = _encode(definition)
    _sync_profile_sources(obj, definition)
