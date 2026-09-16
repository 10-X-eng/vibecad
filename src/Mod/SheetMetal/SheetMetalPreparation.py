# SPDX-License-Identifier: LGPL-2.1-or-later
"""Rebuild transient edit mappings from saved inputs without changing a document.

Only the GUI owner reads features or publishes proxy caches. Workers receive
detached shapes, values, and immutable prepared geometry, as native cut recompute
does. Saved result properties and document History are never rewritten here.
"""

from concurrent.futures import Future
from dataclasses import dataclass
import json
import math
import re
import threading

from PySide import QtCore

import SheetMetalEditable as Editable
import SheetMetalCutHistory as History
import SheetMetalHistoryOperations as Shared
from SheetMetalPresentation import _gui_thread


@dataclass(frozen=True)
class _State:
    obj: object
    proxy: object
    key: tuple
    folded: object
    flat: object
    fingerprint: str
    profile_shape: object
    geometry: object


@dataclass(frozen=True)
class _Observation:
    revision: object
    root_key: tuple
    source_shape: object
    thickness: object
    profiles: tuple
    states: tuple


def _observe(sheet):
    _gui_thread()
    revision = Shared.capture_revision(sheet)
    root, steps = History._chain(sheet)
    key = Editable._inputs(root)
    source = key[0]
    profiles = tuple((name, profile, profile.Shape) for name, profile in key[-1])
    states = []
    for obj in (root, *steps):
        if type(obj.Proxy) not in (Editable.EditableSheetFeature, History.CircleCutFeature,
                                   History.ProfileCutFeature):
            raise RuntimeError("This sheet feature does not support detached mapping preparation")
        if any({"Touched", "Invalid"}.intersection(dep.State) for dep in (obj, *obj.OutListRecursive)):
            raise RuntimeError("Finish the sheet's pending edits before preparing its mapping")
        if obj.Shape.isNull() or obj.FlatShape.isNull() or not obj.PreparedInputHash:
            raise RuntimeError("The sheet has no saved folded/flat result to prepare")
        profile_shape = (History.get_profile(obj).Shape if isinstance(obj.Proxy, History.ProfileCutFeature)
                         and not obj.Suppressed else None)
        states.append(_State(obj, obj.Proxy, key if obj is root else History._snapshot(obj),
            obj.Shape, obj.FlatShape, obj.PreparedInputHash, profile_shape, obj.Proxy._geometry))
    return _Observation(revision, key, source.Shape, Editable.nominal_thickness(source), profiles, tuple(states))


def _same_shape(left, right):
    return left is right if left is None or right is None else left.isEqual(right)


def _same(left, right):
    if (left.revision != right.revision or left.root_key != right.root_key
            or left.thickness != right.thickness or not _same_shape(left.source_shape, right.source_shape)
            or len(left.profiles) != len(right.profiles) or len(left.states) != len(right.states)):
        return False
    for a, b in zip(left.profiles, right.profiles):
        if a[:2] != b[:2] or not _same_shape(a[2], b[2]):
            return False
    for a, b in zip(left.states, right.states):
        if (a.obj is not b.obj or a.proxy is not b.proxy or a.key != b.key
                or a.fingerprint != b.fingerprint or a.geometry is not b.geometry
                or not _same_shape(a.folded, b.folded) or not _same_shape(a.flat, b.flat)
                or not _same_shape(a.profile_shape, b.profile_shape)):
            return False
    return True


def _payload(observed):
    # Reuse the deepest current ancestor instead of replacing another branch's
    # valid mapping. SheetGeometry owns its shapes and cut() returns a new state.
    seed = None
    for index, state in enumerate(observed.states):
        try:
            geometry = Editable.get_state_geometry(state.obj)
        except (RuntimeError, ValueError):
            break
        seed = index, geometry, state.proxy._frame, state.fingerprint
    root_inputs = None
    if seed is None:
        _, face, definition, factor, material, _ = observed.root_key
        definition = Editable._definition(definition)
        inputs = (observed.source_shape.copy(), face, definition, factor, material,
                  {name: shape.copy() for name, _, shape in observed.profiles}, observed.thickness)
        # An uncut root also persists a detached copy of its complete source.
        # It can retain the original transformed coordinates more faithfully
        # than copying the restored source's rounded location matrices again.
        saved_source = observed.states[0].folded.copy() if not definition["operations"] else None
        root_inputs = inputs, saved_source, observed.states[0].fingerprint
    cuts = tuple((_restored_operation(state.obj), bool(state.obj.Suppressed),
                  None if state.profile_shape is None else state.profile_shape.copy())
                 for state in observed.states[1:])
    return root_inputs, cuts, seed


def _restored_operation(step):
    """Recover full-precision cut values retained in the saved JSON result.

    ZipWriter saves native floats in fixed format with digits10 + 1 decimal
    places (16 for double). JSON retains enough digits to round-trip a double.
    Accept only that exact serialization, and still require the complete
    rebuilt chain's exact saved fingerprints.
    """
    current = History._operation(step)
    if current["kind"] != "circle":
        return current
    try:
        saved = Editable._definition({"version": 1, "operations": [
            json.loads(step.Operation)]})["operations"][0]
    except (ValueError, TypeError, AttributeError):
        return current
    if saved["kind"] != "circle" or saved["id"] != current["id"]:
        return current
    first = (*current["center"], current["radius"])
    second = (*saved["center"], saved["radius"])
    if all(x == y or x == float(format(y, ".16f")) for x, y in zip(first, second)):
        return saved
    return current


_REAL = re.compile(r"[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?")


def _same_source_with_roundoff(left, right):
    """Require identical serialized topology and coordinate-scale roundoff.

    This is only a compatibility check between two saved copies of an uncut
    source. It never rewrites BRep data, changes a fingerprint, or substitutes
    for checking the candidate against the exact saved fingerprint.
    """
    first = Editable._persistent_brep(left).split()
    second = Editable._persistent_brep(right).split()
    if len(first) != len(second):
        return False
    # Composing locations can cancel large coordinates down to almost zero;
    # ULPs of that result alone do not bound the arithmetic's roundoff.
    scale = max(1.0, left.BoundBox.DiagonalLength, right.BoundBox.DiagonalLength)
    precision = 32 * math.ulp(1.0)
    for a, b in zip(first, second):
        if a == b:
            continue
        if not _REAL.fullmatch(a) or not _REAL.fullmatch(b):
            return False
        x, y = float(a), float(b)
        if (not math.isfinite(x) or not math.isfinite(y)
                or abs(x-y) > precision * max(scale, abs(x), abs(y))):
            return False
    return True


def _build(root_inputs, cuts, seed):
    """Worker entry point: no document, feature, view, or GUI calls."""
    if seed is None:
        inputs, saved_source, expected = root_inputs
        geometry, frame, fingerprint = Editable.prepare_geometry_snapshot(*inputs)
        if (fingerprint != expected and saved_source is not None
                and _same_source_with_roundoff(inputs[0], saved_source)):
            candidate = Editable.prepare_geometry_snapshot(saved_source, *inputs[1:])
            if candidate[2] == expected:
                geometry, frame, fingerprint = candidate
        results = [(geometry, frame, fingerprint)]
        first_cut = 0
    else:
        index, geometry, frame, fingerprint = seed
        results = [None] * (index + 1)
        first_cut = index
    for operation, suppressed, shape in cuts[first_cut:]:
        geometry, fingerprint = History.prepare_cut_snapshot(
            geometry, frame, operation, suppressed, fingerprint, shape)
        results.append((geometry, frame, fingerprint))
    return tuple(results)


class _Completion(QtCore.QObject):
    ready = QtCore.Signal(object)


class PreparationRun(QtCore.QObject):
    def __init__(self, observed, payload):
        super().__init__()
        self.future = Future()
        self.finished = False
        self._observed = observed
        if payload is None:
            self.finished = True
            self._observed = None
            self.future.set_result({"object_name": observed.states[-1].obj.Name,
                                    "input_hash": observed.states[-1].fingerprint})
            return
        self._completion = completion = _Completion()
        completion.ready.connect(self._finish, QtCore.Qt.QueuedConnection)

        def work():
            try:
                outcome = _build(*payload), None
            except Exception as error:
                outcome = None, error
            try:
                completion.ready.emit(outcome)
            except RuntimeError:
                pass  # Application exit destroyed the receiver.

        self._worker = threading.Thread(target=work, name="sheet-prepare", daemon=True)
        self._worker.start()

    def _finish(self, outcome):
        _gui_thread()
        self.finished = True
        observed, self._observed = self._observed, None
        if not self.future.set_running_or_notify_cancel():
            return
        results, error = outcome
        try:
            if not _same(observed, _observe(observed.states[-1].obj)):
                raise RuntimeError("The sheet changed during mapping preparation; prepare its current revision")
            if error is not None:
                raise error
            if len(results) != len(observed.states) or any(
                    result is not None and result[2] != state.fingerprint
                    for state, result in zip(observed.states, results)):
                raise RuntimeError("The saved sheet fingerprint does not match its prepared inputs")
            # Validate the entire result before publishing any cache. No event
            # processing or document property writes occur during publication.
            for index, (state, result) in enumerate(zip(observed.states, results)):
                if result is None:
                    continue
                geometry, frame, _ = result
                proxy = state.proxy
                proxy._key, proxy._frame = state.key, frame
                if index == 0:
                    proxy._source_shape = observed.source_shape
                    proxy._source_thickness = observed.thickness
                    proxy._profile_shapes = observed.profiles
                else:
                    proxy._base_geometry = observed.states[index - 1].proxy._geometry
                    if isinstance(proxy, History.ProfileCutFeature):
                        proxy._profile_shape = state.profile_shape
                proxy._geometry = geometry
            for state in observed.states:
                view = getattr(getattr(state.obj, "ViewObject", None), "Proxy", None)
                if view is not None:
                    view._queue_refresh()
            self.future.set_result({"object_name": observed.states[-1].obj.Name,
                                    "input_hash": observed.states[-1].fingerprint})
        except Exception as error:
            self.future.set_exception(error)


def start_preparation(sheet, *, expected_revision):
    """Prepare missing caches for one exact saved sheet revision asynchronously.

    Retain the returned run until its future finishes. Cancelling that future
    discards publication; it does not terminate a worker or a GUI process.
    """
    _gui_thread()
    observed = _observe(sheet)
    expected = expected_revision.summary() if hasattr(expected_revision, "summary") else expected_revision
    if observed.revision.summary() != expected:
        raise RuntimeError("The sheet revision changed; inspect its current state before preparation")
    payload = _payload(observed)
    if payload[2] is not None and payload[2][0] == len(observed.states) - 1:
        # Keep the same run/future contract without queuing duplicate geometry.
        return PreparationRun(observed, None)
    return PreparationRun(observed, payload)
