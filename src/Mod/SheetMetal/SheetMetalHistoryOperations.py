# SPDX-License-Identifier: LGPL-2.1-or-later
"""Revision-bound commands for the shared native sheet feature chain."""

from dataclasses import dataclass
import json

import FreeCAD as App

import SheetMetalEditable as Editable
import SheetMetalCutHistory as History
import SheetMetalOperations as Operations
from SheetMetalPresentation import _gui_thread
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeMutation import NativeMutationDraft, run_human_mutation
from VibeCADNativeTargets import object_identity


_FIELDS = {**Operations._FIELDS,
           "set_suppressed": frozenset({"operation_id", "suppressed"}),
           "replace_profile": frozenset({"operation_id", "profile"})}


def owner(state):
    _gui_thread()
    try:
        return Editable.state_owner(state)
    except (AttributeError, ReferenceError, RuntimeError, TypeError) as error:
        raise RuntimeError("The exact sheet state or its document was closed") from error


def _display_state(state):
    """A suppressed operation displays its preceding unsuppressed result."""
    document, visited = owner(state), set()
    while isinstance(state.Proxy, (History.CircleCutFeature, History.ProfileCutFeature)) and state.Suppressed:
        if state in visited:
            raise RuntimeError("The sheet history contains a dependency cycle")
        visited.add(state)
        state = state.BaseSheet
        if owner(state) is not document:
            raise RuntimeError("The preceding sheet belongs to another document")
    return state


def capture_revision(state):
    _gui_thread()
    document = owner(state)
    if document.isObjectUsableAtCurrentTimelinePosition(state):
        return Operations.capture_revision(state)
    # Retain a current suppressed cut in its editor so it can be restored.
    # This is not permission to edit a future operation or export it as active.
    _, steps = History._chain(state)
    timelines = document.findObjects("App::DocumentTimeline")
    if not steps or not state.Suppressed or len(timelines) != 1:
        raise RuntimeError("This sheet is not active at the current History position")
    active = list(timelines[0].Operations)[:int(timelines[0].Position)]
    display = _display_state(state)
    if (any(step not in active for step in steps)
            or not document.isObjectUsableAtCurrentTimelinePosition(display)):
        raise RuntimeError("This sheet is not active at the current History position")
    return Operations._capture_revision(document, state.Name, state.PreparedInputHash)


def _check_revision(state, expected):
    current = capture_revision(state)
    value = expected.summary() if isinstance(expected, Operations.SheetRevision) else expected
    if not isinstance(value, dict) or value != current.summary():
        raise RuntimeError("The sheet document changed; reload its current revision and retry")
    return current


def _cuts(state):
    root, steps = History._chain(state)
    return [(entry, root, False) for entry in Editable._definition(root.Definition)["operations"]] + [
        (History._operation(step), step, True) for step in steps]


def _history_summary(state, root, steps):
    """Describe the existing native entries without enrolling or editing them."""
    document = state.Document
    timelines = document.findObjects("App::DocumentTimeline")
    if len(timelines) > 1:
        raise RuntimeError("The document must have one native operation History")
    timeline = timelines[0] if timelines else None
    operations = list(timeline.Operations) if timeline is not None else []
    indices = {obj: index for index, obj in enumerate(operations)}
    def target(obj):
        if obj is None:
            return None
        if obj.Document is not document:
            raise RuntimeError("The sheet History reference belongs to another document")
        return {"document_uid": document.Uid, "object_name": obj.Name}
    entries = []
    for obj in (root, *steps):
        operation = None if obj is root else History._operation(obj)
        entry = {
            "target": target(obj), "label": obj.Label,
            "kind": "sheet" if operation is None else operation["kind"],
            "operation_id": None if operation is None else operation["id"],
            "predecessor": target(obj.SourceFace[0] if obj is root else obj.BaseSheet),
            "timeline_index": indices.get(obj),
            "active": document.isObjectUsableAtCurrentTimelinePosition(obj),
            "suppressed": bool(getattr(obj, "Suppressed", False)),
            "editor_command": getattr(obj, "VibeCADTimelineEditCommand", None),
        }
        if isinstance(obj.Proxy, History.ProfileCutFeature):
            try:
                entry["profile"] = target(History.get_profile(obj))
            except (RuntimeError, ValueError) as error:
                entry.update(profile=None, profile_error=str(error))
        entries.append(entry)
    return {
        "position": int(timeline.Position) if timeline is not None else None,
        "operation_count": len(operations), "states": entries,
        "display_state": target(_display_state(state)),
    }


def inspect(state):
    revision = capture_revision(state)
    root, steps = History._chain(state)
    result = Operations.inspect(root)
    result.update(revision=revision.summary(), state=list(state.State), prepared=False,
                  definition=History.definition(state),
                  history=_history_summary(state, root, steps),
                  representation=_display_state(state).ViewObject.Proxy.mode,
                  cuts=[{**entry, "native": native, "object_name": owner.Name,
                         "suppressed": bool(owner.Suppressed) if native else False}
                        for entry, owner, native in _cuts(state)])
    result.pop("error", None)
    try:
        # Inspection may show the bypass geometry of a suppressed current cut.
        # Export and cut creation continue to require History.get_prepared().
        Editable.get_state_geometry(state)
    except (RuntimeError, ValueError) as error:
        status = state.getStatusString() if "Invalid" in state.State else ""
        result["error"] = status or str(error)
    else:
        result["prepared"] = True
    return result


def check_profile(state, profile):
    capture_revision(state)
    History._check_profile(state, profile)
    if not state.Document.isObjectUsableAtCurrentTimelinePosition(profile):
        raise RuntimeError("The sketch is not active at the current History position")


def check_profile_input(state, profile):
    """Check inputs for a new downstream cut, allowing a sketch on its parent."""
    capture_revision(state)
    History._check_profile_input(state, profile)
    if not state.Document.isObjectUsableAtCurrentTimelinePosition(profile):
        raise RuntimeError("The sketch is not active at the current History position")


def switch(state, representation):
    _gui_thread()
    display = _display_state(state)
    History._active(display)
    Operations.switch(display, representation)


@dataclass(frozen=True)
class PreparedEdit:
    revision: Operations.SheetRevision
    arguments_json: str
    _sheet: object
    _cut: object = None
    _profile: object = None
    _legacy: object = None


def prepare(state, arguments, *, expected_revision):
    revision = _check_revision(state, expected_revision)
    operation, values = strict_variant_arguments(arguments, _FIELDS, defaults=Operations._DEFAULTS)
    # Derive native parameter targets from the same canonical ordering that
    # survives the frozen JSON request's round trip at execution time.
    values = json.loads(json.dumps(values, allow_nan=False, sort_keys=True))
    root, _ = History._chain(state)
    cut, profile, legacy = None, None, None
    if "operation_id" in values:
        matches = [(entry, owner, native) for entry, owner, native in _cuts(state)
                   if entry["id"] == values["operation_id"]]
        if len(matches) != 1:
            raise ValueError("The requested sheet cut no longer exists")
        entry, cut, native = matches[0]
        if not native:
            if operation not in ("update_circle", "remove_operation"):
                raise ValueError("This legacy cut uses its original definition editor")
            legacy = Operations.prepare(root, {"operation": operation, **values},
                                        expected_revision=Operations.capture_revision(root))
        elif operation == "remove_operation":
            raise ValueError("Suppress a native cut to retain its editable dependencies")
        elif operation == "set_suppressed":
            if type(values["suppressed"]) is not bool:
                raise ValueError("Suppression must be true or false")
        elif operation == "update_circle" and entry["kind"] != "circle":
            raise ValueError("Edit this profile through its native sketch")
        elif operation == "replace_profile" and entry["kind"] != "profile":
            raise ValueError("Select one sketch cut")
    if operation in ("add_circle", "add_profile"):
        History.get_prepared(state)
    if legacy is None and operation in ("add_circle", "update_circle"):
        target = state if operation == "add_circle" else cut
        History._active(target)
        if values["representation"] not in ("flat", "folded"):
            raise ValueError("Representation must be folded or flat")
        if values["region"] is not None and (type(values["region"]) is not int or values["region"] < 0):
            raise ValueError("A region must be a nonnegative index in the prepared sheet")
        if values["radius"] is not None and Editable._number(values["radius"]) <= 0:
            raise ValueError("A circle radius must be positive")
        center = values["center"]
        if operation == "add_circle" and (center is None or values["radius"] is None):
            raise ValueError("A new circle requires its center and radius")
        if center is not None:
            if not isinstance(center, list) or len(center) != 3:
                raise ValueError("A cut center requires three coordinates in millimeters")
            center = App.Vector(*(Editable._number(value) for value in center))
            Editable._cut_center(target, center, values["representation"], values["region"],
                                 updating=operation == "update_circle")
        if center is None and values["radius"] is None:
            raise ValueError("Choose a circle center or radius to update")
    if operation in ("add_profile", "replace_profile"):
        target = values["profile"]
        if (not isinstance(target, dict) or set(target) != {"document_uid", "object_name"}
                or target["document_uid"] != revision.document_uid
                or not isinstance(target["object_name"], str)):
            raise ValueError("Select an exact cut sketch in this document")
        profile = state.Document.getObject(target["object_name"])
        if operation == "add_profile":
            check_profile_input(state, profile)
        else:
            check_profile(cut, profile)
    if operation in ("set_material", "set_parameters"):
        legacy = Operations.prepare(root, {"operation": operation, **values},
                                    expected_revision=Operations.capture_revision(root))
    return PreparedEdit(revision, json.dumps({"operation": operation, **values},
                                             sort_keys=True, allow_nan=False), state, cut, profile, legacy)


def start(prepared, *, transaction_runner=run_human_mutation, recompute_queue=None):
    """Commit one short transaction, then prepare the entire result chain.

    Native callers can supply an owned parameter transaction and tracked queue.
    They retain responsibility for their asynchronous completion receipts.
    """
    _gui_thread()
    if not callable(transaction_runner) or (recompute_queue is not None and not callable(recompute_queue)):
        raise TypeError("Sheet transaction and recompute callbacks must be callable")
    if not isinstance(prepared, PreparedEdit):
        raise TypeError("Prepare the exact sheet edit first")
    state = prepared._sheet
    checked = prepare(state, json.loads(prepared.arguments_json), expected_revision=prepared.revision)
    # A frozen wrapper is not proof that its embedded targets match the request.
    # Resolve and validate them again before opening any document transaction.
    if (checked != prepared or checked._cut is not prepared._cut
            or checked._profile is not prepared._profile
            or (checked._legacy is not None and checked._legacy._sheet is not prepared._legacy._sheet)):
        raise ValueError("The prepared sheet targets no longer match the exact request")
    arguments = json.loads(prepared.arguments_json)
    operation = arguments.pop("operation")
    applied = []
    def mutate(document):
        if document is not state.Document:
            raise RuntimeError("The edit belongs to another document")
        result, changed, created = state, [], []
        if prepared._legacy is not None:
            Operations._mutate(prepared._legacy)
            changed.append(prepared._legacy._sheet)
            if operation == "set_parameters":
                changed.append(prepared._legacy._sheet.SourceFace[0])
        elif operation == "add_circle":
            result = History.create_circle_step(state, **arguments)
            created.append(result)
            changed.append(state)
        elif operation == "add_profile":
            result = History.create_profile_step(state, prepared._profile)
            created.append(result)
            changed.extend((state, prepared._profile))
        elif operation == "update_circle":
            arguments.pop("operation_id")
            History.update_circle_step(prepared._cut, **arguments)
            changed.append(prepared._cut)
        elif operation == "set_suppressed":
            prepared._cut.Suppressed = arguments["suppressed"]
            changed.append(prepared._cut)
        elif operation == "replace_profile":
            History.replace_profile_step(prepared._cut, prepared._profile)
            changed.extend((prepared._cut, prepared._profile))
        else:
            raise ValueError("This sheet operation has no native command implementation")
        if created:
            # Initial mesh publication reads this presentation-only mode.
            result.ViewObject.Proxy.mode = state.ViewObject.Proxy.mode
        expectation = Operations._expectation(result)
        applied.append((result, expectation))
        return NativeMutationDraft(value=(result, expectation),
            created=tuple(object_identity(obj) for obj in created),
            changed=tuple(object_identity(obj) for obj in changed))
    def verify(document, draft):
        result, expectation = draft.value
        if result.Document is not document or Operations._expectation(result) != expectation:
            raise RuntimeError("The requested sheet edit did not persist")
        return {"operation": operation, "object_name": result.Name}
    immediate = transaction_runner(document=state.Document, transaction_name="Edit sheet metal",
                                   mutate=mutate, verify=verify)
    result, expectation = applied[-1]
    run = Operations.EditRun(result, dict(immediate), expectation, revision_reader=capture_revision)
    try:
        if recompute_queue is None:
            result.Document.recomputeAsync()
        else:
            recompute_queue(result.Document)
    except Exception as error:
        run._finish("failed", str(error))
    else:
        run._schedule()
    return run
