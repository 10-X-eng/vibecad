# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared sheet commands: short transactions, native async geometry, exact revisions.

The ribbon and native assistant use this domain path. No MCP server, generated
Python, nested event loop, or synchronous document recompute is involved.
"""

from concurrent.futures import Future
from dataclasses import dataclass
import json
import uuid
import weakref

import FreeCAD as App
import FreeCADGui as Gui

import SheetMetalEditable as Editable
from SheetMetalPresentation import _gui_thread
from VibeCADCore import get_service
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeMutation import NativeMutationDraft, run_human_mutation
from VibeCADNativeTargets import object_identity


_sessions = {}
_runs = weakref.WeakSet()
_PARAMETERS = {
    "thickness": ("Thickness", "thickness"),
    "bend_radius": ("Radius", "radius"),
    "flange_length": ("Length", "length"),
    "bend_angle": ("Angle", "angle"),
}

_NUMERIC_PROPERTIES = {
    "App::PropertyLength", "App::PropertyDistance", "App::PropertyAngle",
    "App::PropertyFloat", "App::PropertyFloatConstraint",
}
_FIELDS = {
    "add_circle": frozenset({"center", "radius", "representation", "region"}),
    "update_circle": frozenset({"operation_id", "center", "radius", "representation", "region"}),
    "remove_operation": frozenset({"operation_id"}),
    "add_profile": frozenset({"profile"}),
    "set_material": frozenset({"material", "k_factor"}),
    "set_parameters": frozenset({"changes"}),
}
_DEFAULTS = {
    "add_circle": {"representation": "flat", "region": None},
    "update_circle": {"center": None, "radius": None, "representation": "flat", "region": None},
    "set_material": {"material": None, "k_factor": None},
}


def _document_ready(document):
    if (document.Recomputing or document.RecomputePending or document.CooperativeMutationActive
            or get_service().document_change_batch_active(str(document.Uid))):
        raise RuntimeError("Wait for the document's current operation to finish")
    if document.HasPendingTransaction or document.getBookedTransactionID():
        raise RuntimeError("Finish the current document transaction first")


@dataclass(frozen=True)
class SheetRevision:
    document_uid: str
    document_session: str
    object_name: str
    structural_revision: int
    input_hash: str

    def summary(self):
        return dict(vars(self))


def capture_revision(sheet):
    """Capture an exact open-document epoch plus the shared native revision.

    This remains available for an invalid feature so it can be repaired. It is
    not evidence that geometry is prepared; readers/exporters must also call
    get_prepared(). Native revisions reject change-and-Undo (ABA) stale edits.
    """
    _gui_thread()
    document = Editable.state_owner(sheet)
    if (getattr(sheet, "VibeCADTimelineRole", None) == "operation"
            and not document.isObjectUsableAtCurrentTimelinePosition(sheet)):
        raise RuntimeError("This sheet is not active at the current History position")
    return _capture_revision(document, sheet.Name, sheet.PreparedInputHash)


def _capture_revision(document, object_name, input_hash):
    _document_ready(document)
    import VibeCADGui
    VibeCADGui._connect_document_observer()
    if not VibeCADGui._document_observer_connected:
        raise RuntimeError("Native document revision tracking is unavailable")
    uid = str(document.Uid)
    session = _sessions.get(uid)
    if session is None or session[0] is not document:
        session = document, uuid.uuid4().hex
        _sessions[uid] = session
    state = get_service().native_document_state_store()
    state.ensure_document(uid)
    return SheetRevision(uid, session[1], object_name, state.current_revision(uid), input_hash)


def capture_source_revision(source):
    """Identify the exact upstream source before creating its editable sheet."""
    _gui_thread()
    document = Editable._owner(source)
    if not document.isObjectUsableAtCurrentTimelinePosition(source):
        raise RuntimeError("This source is not active at the current History position")
    return _capture_revision(document, source.Name, "")


def start_creation(source, reference_face, *, expected_revision,
                   transaction_runner=run_human_mutation, recompute_queue=None):
    """Create from one source face and prepare both representations asynchronously."""
    from SheetMetalPresentation import create_presented_sheet
    if not callable(transaction_runner) or (recompute_queue is not None and not callable(recompute_queue)):
        raise TypeError("Sheet transaction and recompute callbacks must be callable")
    current = capture_source_revision(source)
    expected = expected_revision.summary() if isinstance(expected_revision, SheetRevision) else expected_revision
    if current.summary() != expected:
        raise RuntimeError("The source document changed; select its current face and retry")
    if isinstance(getattr(source, "Proxy", None), Editable.PreparedSheetState):
        raise ValueError("This is already an editable sheet")
    if any({"Touched", "Invalid"}.intersection(obj.State) for obj in (source, *source.OutListRecursive)):
        raise RuntimeError("Prepare the source geometry before creating an editable sheet")
    # Cached worker evidence can reject a known end wall before adding a Tree /
    # History entry. Restored sources and faces outside the bounded scan still
    # receive the full thickness check on the geometry worker.
    from SheetMetalSourceFeatures import Fold, get_prepared_source
    if getattr(getattr(source, "Proxy", None), "_prepared_source", None) is not None:
        evidence = get_prepared_source(source)
        candidates = [face["name"] for face in evidence.get("reference_faces", ())]
        if reference_face in evidence.get("reference_faces_checked", ()) and reference_face not in candidates:
            raise ValueError(
                f"{reference_face} does not match the source stock thickness; select a planar "
                f"sheet skin. Candidate faces: {', '.join(candidates) or 'none in the inspected faces'}")
    created = []
    def mutate(document):
        if document is not source.Document:
            raise RuntimeError("The source belongs to another document")
        sheet = create_presented_sheet(source, reference_face, tree_details=True, consume_source=False)
        if isinstance(getattr(source, "Proxy", None), Fold):
            # This source forms an existing blank using its own allowance.
            # Keep the inverse development consistent after parameter edits.
            sheet.KFactor = source.kfactor
            sheet.setExpression("KFactor", f"{source.Name}.kfactor")
        _publish_creation_history(sheet, source)
        source.Visibility = False
        created.append(sheet)
        return NativeMutationDraft(value=sheet, created=(object_identity(sheet),),
                                   changed=(object_identity(source),))
    def verify(document, draft):
        sheet = draft.value
        if sheet.Document is not document or sheet.SourceFace != (source, [reference_face]):
            raise RuntimeError("The editable sheet lost its exact source face")
        return {"operation": "create", "object_name": sheet.Name}
    immediate = transaction_runner(document=source.Document, transaction_name="Create editable sheet",
                                   mutate=mutate, verify=verify)
    sheet = created[-1]
    run = EditRun(sheet, dict(immediate), _expectation(sheet))
    try:
        if recompute_queue is None:
            sheet.Document.recomputeAsync()
        else:
            recompute_queue(sheet.Document)
    except Exception as error:
        run._finish("failed", str(error))
    else:
        run._schedule()
    return run


def _publish_creation_history(sheet, source):
    """Publish exact new sheet identity through the native History contract."""
    import PartGui
    from tool_impl.service.domain_runtime import (
        _ensure_timeline_property, _mark_timeline_operation, finalize_new_timeline_operation,
    )
    # Body-owned results use native Tip history; the helper deliberately returns
    # False for that case. Root results record the exact consumed input instead.
    replaces_source = PartGui.setModelingReplacedInputs(sheet, [source])
    _ensure_timeline_property(sheet, "App::PropertyString", "VibeCADTimelineEditCommand",
                              "Open this sheet's source dimensions")
    sheet.VibeCADTimelineEditCommand = "SheetMetal_EditParameters"
    if replaces_source:
        finalize_new_timeline_operation(sheet)
    else:
        # Native Body insertion has already changed Tip/input visibility. This
        # identity was auto-enrolled as a modeling feature by our transaction;
        # finalize that enrollment after its native grouping/display changes.
        _mark_timeline_operation(sheet)
        sheet.Document.finalizeProvisionalTimelineOperationBlock(sheet, [sheet])


def _check_revision(sheet, expected):
    current = capture_revision(sheet)
    value = expected.summary() if isinstance(expected, SheetRevision) else expected
    if not isinstance(value, dict) or value != current.summary():
        raise RuntimeError("The sheet document changed; inspect its current revision and retry")
    return current


def _parameter_properties(source):
    """Keep legacy aliases and expose the separate dimensions of a base shape."""
    from SheetMetalBaseShapeCmd import SMBaseShape
    properties = dict(_PARAMETERS)
    if isinstance(getattr(source, "Proxy", None), SMBaseShape):
        properties["width"] = ("width",)
        if source.shapeType != "Flat":
            properties["height"] = ("height",)
        if source.shapeType in ("Hat", "Box"):
            properties["flange_width"] = ("flangeWidth",)
    return properties


def inspect(sheet):
    """Read the shared definition, parameters and prepared-region descriptions."""
    revision = capture_revision(sheet)
    Editable._editable_owner(sheet)
    source = sheet.SourceFace[0]
    parameters = {}
    properties = _parameter_properties(source)
    for semantic, aliases in properties.items():
        matches = [name for name in aliases if source is not None and name in source.PropertiesList]
        if len(matches) == 1 and source.getTypeIdOfProperty(matches[0]) in _NUMERIC_PROPERTIES:
            name = matches[0]
            parameters[semantic] = {"property": name, "value": float(getattr(source, name)),
                                    "editable": "ReadOnly" not in source.getPropertyStatus(name)}
    if "width" in properties and "flange_length" in parameters:
        # Keep the existing public field and its meaning; the base builder's
        # longitudinal length is separate from its formed wall height.
        parameters["flange_length"]["label"] = "Base length (mm)"
    result = {
        "revision": revision.summary(), "definition": Editable._definition(sheet.Definition),
        "material": sheet.Material, "k_factor": sheet.KFactor, "parameters": parameters,
        "state": list(sheet.State), "prepared": False, "regions": [],
        "representation": getattr(sheet.ViewObject.Proxy, "mode", None),
    }
    try:
        geometry = Editable.get_prepared(sheet)
    except (RuntimeError, ValueError) as error:
        status = sheet.getStatusString() if "Invalid" in sheet.State else ""
        result["error"] = status or str(error)
    else:
        result["prepared"] = True
        origin, u_axis, v_axis = sheet.Proxy._frame
        result["flat_frame"] = {"origin": list(origin), "u_axis": list(u_axis),
                                "v_axis": list(v_axis), "normal": list(geometry.normal)}
        result["regions"] = [{"region": region.face_index, "kind": region.kind,
                              "source_face": f"Face{region.face_index+1}"}
                             for region in geometry.mapping.regions]
    return result


@dataclass(frozen=True)
class PreparedEdit:
    revision: SheetRevision
    arguments_json: str
    _sheet: object
    _profile: object = None
    _parameters: tuple = ()


def _source_parameters(sheet, changes):
    source = sheet.SourceFace[0]
    available = _parameter_properties(source)
    if not isinstance(changes, dict) or not changes or set(changes)-set(available):
        raise ValueError("Choose dimensions exposed by this sheet's current parameter inspection")
    result = []
    for name, value in changes.items():
        value = Editable._number(value)
        if ((name in ("thickness", "flange_length", "width", "height", "flange_width") and value <= 0)
                or (name == "bend_radius" and value < 0)
                or (name == "bend_angle" and (value == 0 or abs(value) > 180))):
            raise ValueError("Sheet source dimensions are outside their supported range")
        properties = [candidate for candidate in available[name] if candidate in source.PropertiesList]
        if len(properties) != 1:
            raise ValueError(f"This upstream feature does not expose an unambiguous {name}")
        property_name = properties[0]
        if source.getTypeIdOfProperty(property_name) not in _NUMERIC_PROPERTIES:
            raise ValueError(f"The upstream {name} is not a supported numeric property")
        if "ReadOnly" in source.getPropertyStatus(property_name):
            raise ValueError(f"The upstream {name} is read-only")
        result.append((property_name, value))
    return tuple(result)


def prepare(sheet, arguments, *, expected_revision):
    """Validate and freeze an edit without changing the model or opening Undo."""
    revision = _check_revision(sheet, expected_revision)
    Editable._editable_owner(sheet)
    operation, values = strict_variant_arguments(arguments, _FIELDS, defaults=_DEFAULTS)
    # Freeze caller-owned JSON before it can be changed while this edit is queued.
    values = json.loads(json.dumps(values, allow_nan=False))
    needs_geometry = operation in ("add_circle", "add_profile") or (
        operation == "update_circle" and values["center"] is not None)
    if needs_geometry:
        Editable.get_prepared(sheet)
    profile, parameters = None, ()
    if operation in ("add_circle", "update_circle"):
        if values["representation"] not in ("folded", "flat"):
            raise ValueError("Representation must be folded or flat")
        if values["region"] is not None and (
                type(values["region"]) is not int or values["region"] < 0):
            raise ValueError("A region must be a nonnegative index in the prepared sheet")
        radius = values["radius"]
        if radius is not None and Editable._number(radius) <= 0:
            raise ValueError("A circle radius must be positive")
        center = values["center"]
        if operation == "add_circle" and (center is None or radius is None):
            raise ValueError("A new circle requires its center and radius")
        if center is not None:
            if not isinstance(center, list) or len(center) != 3:
                raise ValueError("A cut center requires three coordinates in millimeters")
            center = App.Vector(*(Editable._number(value) for value in center))
            Editable._cut_center(sheet, center, values["representation"], values["region"],
                                 updating=operation == "update_circle")
        if operation == "update_circle" and radius is None and center is None:
            raise ValueError("Choose a circle center or radius to update")
    if operation in ("update_circle", "remove_operation"):
        existing = next((entry for entry in Editable._definition(sheet.Definition)["operations"]
                         if entry["id"] == values["operation_id"]), None)
        if existing is None:
            raise ValueError("The requested sheet operation no longer exists")
        if operation == "update_circle" and existing["kind"] != "circle":
            raise ValueError("Edit a profile operation through its native sketch")
    elif operation == "add_profile":
        target = values["profile"]
        if (not isinstance(target, dict) or set(target) != {"document_uid", "object_name"}
                or target["document_uid"] != revision.document_uid
                or not isinstance(target["object_name"], str)):
            raise ValueError("Select an exact cut profile in the sheet's document")
        profile = sheet.Document.getObject(target["object_name"])
        if profile is None:
            raise ValueError("The requested cut profile no longer exists")
        Editable._check_profile_owner(sheet, profile)
    elif operation == "set_material":
        material, factor = values["material"], values["k_factor"]
        if material is not None and not isinstance(material, str):
            raise ValueError("A sheet material must be text")
        if factor is not None and not 0 <= Editable._number(factor) <= 1:
            raise ValueError("ANSI K-factor must be between zero and one")
        if material is None and factor is None:
            raise ValueError("Choose a material or K-factor to update")
    elif operation == "set_parameters":
        parameters = _source_parameters(sheet, values["changes"])
    return PreparedEdit(revision, json.dumps({"operation": operation, **values},
                                            sort_keys=True, allow_nan=False),
                        sheet, profile, parameters)


def _expectation(sheet):
    if not isinstance(sheet.Proxy, Editable.EditableSheetFeature):
        return sheet.Proxy.edit_expectation(sheet)
    source, faces = sheet.SourceFace
    parameters = tuple((name, float(getattr(source, name)))
                       for aliases in _parameter_properties(source).values() for name in aliases
                       if source is not None and name in source.PropertiesList
                       and source.getTypeIdOfProperty(name) in _NUMERIC_PROPERTIES)
    bindings = tuple((entry["profile"], getattr(sheet, entry["profile"], None))
                     for entry in Editable._definition(sheet.Definition)["operations"]
                     if entry["kind"] == "profile")
    return (source, tuple(faces), sheet.Definition, sheet.Material, sheet.KFactor,
            tuple(getattr(sheet, "ProfileSources", ())), parameters, bindings)


def _mutate(prepared):
    sheet = prepared._sheet
    values = json.loads(prepared.arguments_json)
    operation = values.pop("operation")
    identity = values.get("operation_id")
    if operation == "add_circle":
        values["center"] = App.Vector(*values["center"])
        identity = Editable.add_circle_cut(sheet, **values)
    elif operation == "update_circle":
        if values["center"] is not None:
            values["center"] = App.Vector(*values["center"])
        Editable.update_circle_cut(sheet, **values)
    elif operation == "remove_operation":
        Editable.remove_operation(sheet, **values)
    elif operation == "add_profile":
        identity = Editable.add_profile_cut(sheet, prepared._profile)
    elif operation == "set_material":
        if values["material"] is not None:
            sheet.Material = values["material"]
        if values["k_factor"] is not None:
            from SheetMetalSourceFeatures import Fold
            source = sheet.SourceFace[0]
            if (isinstance(source.Proxy, Fold)
                    and dict(sheet.ExpressionEngine).get("KFactor") == f"{source.Name}.kfactor"):
                source.kfactor = values["k_factor"]
            sheet.KFactor = values["k_factor"]
    elif operation == "set_parameters":
        source = sheet.SourceFace[0]
        for name, value in prepared._parameters:
            setattr(source, name, value)
            if float(getattr(source, name)) != value:
                raise ValueError(f"The upstream feature rejected {name}")
    return identity, _expectation(sheet)


class EditRun:
    def __init__(self, sheet, immediate_result, expectation, *, revision_reader=capture_revision,
                 owner_reader=Editable.state_owner, expectation_reader=_expectation,
                 geometry_reader=Editable.get_state_geometry, ready_result=None):
        self.id = uuid.uuid4().hex
        self.future = Future()
        self._sheet, self._document = sheet, sheet.Document
        self._immediate = immediate_result
        # The immediate runner's Undo eligibility predates async recompute.
        # A native adapter must query/finalize its ledger at the new revision;
        # do not publish that earlier boolean as current completion evidence.
        self._immediate.pop("assistant_undo_available", None)
        self._expectation = expectation
        self._revision_reader = revision_reader
        self._owner_reader = owner_reader
        self._expectation_reader = expectation_reader
        self._geometry_reader = geometry_reader
        self._ready_result = ready_result
        self._queued = False
        _runs.add(self)

    def status(self):
        _gui_thread()
        return self.future.result() if self.future.done() else {
            "job_id": self.id, **self._immediate, "phase": "pending", "terminal": False}

    def _finish(self, phase, error=None):
        if self.future.done():
            return
        result = {"job_id": self.id, **self._immediate, "phase": phase, "terminal": True}
        if error:
            result["error"] = error
        if phase == "ready":
            result["revision"] = self._revision_reader(self._sheet).summary()
            if self._ready_result is not None:
                result.update(self._ready_result(self._sheet))
        _runs.discard(self)
        self.future.set_result(result)

    def _schedule(self):
        if not self.future.done() and not self._queued:
            self._queued = True
            Gui.deferToNextFrame(_poll, weakref.ref(self))


def _poll(reference):
    run = reference()
    if run is None or run.future.done():
        return
    _gui_thread()
    run._queued = False
    try:
        run._owner_reader(run._sheet)
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        # Native deletion can leave a detached wrapper with Name=None until
        # this deferred completion runs. It no longer identifies a live sheet.
        run._finish("closed", "The exact sheet or its document was closed")
        return
    document = run._document
    if (document.Recomputing or document.RecomputePending or document.CooperativeMutationActive
            or get_service().document_change_batch_active(str(document.Uid))
            or document.HasPendingTransaction or document.getBookedTransactionID()):
        run._schedule()
        return
    try:
        unchanged = run._expectation_reader(run._sheet) == run._expectation
    except (AttributeError, ReferenceError, RuntimeError, ValueError):
        unchanged = False
    if not unchanged:
        run._finish("superseded", "The sheet was edited again while geometry was preparing")
        return
    try:
        run._geometry_reader(run._sheet)
    except (RuntimeError, ValueError) as error:
        run._finish("failed", run._sheet.getStatusString() or str(error))
    else:
        run._finish("ready")


def start(prepared, *, transaction_runner=run_human_mutation):
    """Commit the brief domain mutation and return asynchronous geometry status.

    The native AI adapter supplies its existing receipt/Undo transaction runner;
    the human path defaults to the matching native human runner. Neither runner
    is asked to recompute. Later geometry failure remains explicitly repairable.
    """
    _gui_thread()
    if not isinstance(prepared, PreparedEdit):
        raise TypeError("Prepare the exact sheet edit first")
    sheet = prepared._sheet
    _check_revision(sheet, prepared.revision)
    applied = []
    operation = json.loads(prepared.arguments_json)["operation"]
    def mutate(document):
        if document is not sheet.Document:
            raise RuntimeError("The transaction belongs to another document")
        applied.append(_mutate(prepared))
        # Verify persistent intent here; geometry completes after this finite
        # transaction. Empty recompute_targets deliberately prevents sync work.
        changed = (object_identity(sheet),)
        if operation == "set_parameters":
            changed += (object_identity(sheet.SourceFace[0]),)
        return NativeMutationDraft(value=applied[-1], changed=changed)
    def verify(document, draft):
        identity, expectation = draft.value
        if document is not sheet.Document or _expectation(sheet) != expectation:
            raise RuntimeError("The requested sheet edit did not persist")
        return {"operation": operation, "operation_id": identity, "object_name": sheet.Name}
    immediate = transaction_runner(document=sheet.Document, transaction_name="Edit sheet metal",
                                   mutate=mutate, verify=verify)
    run = EditRun(sheet, dict(immediate), applied[-1][1])
    try:
        sheet.Document.recomputeAsync()
    except Exception as error:
        run._finish("failed", str(error))
    else:
        run._schedule()
    return run


def switch(sheet, representation):
    _gui_thread()
    Editable.state_owner(sheet)
    sheet.ViewObject.Proxy.switch(representation)


class _Observer:
    def slotRecomputedDocument(self, document):
        for run in tuple(_runs):
            if run._document is document:
                run._schedule()

    def slotDeletedDocument(self, document):
        uid = str(document.Uid)
        if uid in _sessions and _sessions[uid][0] is document:
            del _sessions[uid]
        for run in tuple(_runs):
            if run._document is document:
                run._schedule()

    def slotDeletedObject(self, obj):
        for run in tuple(_runs):
            if run._sheet is obj:
                run._schedule()


_observer = _Observer()
App.addDocumentObserver(_observer)
