# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded active-surface state assembled only from the live document."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from VibeCADNativeTargets import document_uid, object_reference, read_current_selection
from VibeCADRibbonSurface import SURFACE_IDS


MAX_NATIVE_SNAPSHOT_BYTES = 64 * 1024
MAX_WORKING_OBJECTS = 12


class NativeSnapshotError(RuntimeError):
    """The active Native domain cannot produce a bounded exact snapshot."""


def concise_object(obj: Any) -> dict[str, Any]:
    """Read one object summary without publishing or retaining read-side state."""

    state_before = tuple(
        sorted(str(value) for value in list(getattr(obj, "State", []) or []))
    )
    try:
        result: dict[str, Any] = object_reference(obj)
        label = str(getattr(obj, "Label", "") or "").strip()
        if label and label != result["object_name"]:
            result["label"] = label[:160]
        if state_before:
            result["state"] = list(state_before[:8])
        return result
    finally:
        state_after = tuple(
            sorted(str(value) for value in list(getattr(obj, "State", []) or []))
        )
        if (
            state_after != state_before
            and "Touched" not in state_before
            and "Touched" in state_after
        ):
            try:
                obj.purgeTouched()
            except (AttributeError, ReferenceError, RuntimeError) as exc:
                raise NativeSnapshotError(
                    "Reading an object summary changed its transient document state."
                ) from exc
            state_after = tuple(
                sorted(str(value) for value in list(getattr(obj, "State", []) or []))
            )
        if state_after != state_before:
            raise NativeSnapshotError(
                "Reading an object summary changed its transient document state."
            )


def objects_of_type(document: Any, *type_ids: str) -> list[Any]:
    expected = tuple(str(value) for value in type_ids if str(value))
    result = []
    for obj in list(getattr(document, "Objects", []) or []):
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id in expected:
            result.append(obj)
            continue
        is_derived = getattr(obj, "isDerivedFrom", None)
        if not callable(is_derived):
            continue
        for value in expected:
            try:
                if is_derived(value):
                    result.append(obj)
                    break
            except Exception:
                continue
    return result


def _selection_names(selection: Mapping[str, Any]) -> list[str]:
    names = []
    for item in list(selection.get("items") or []):
        if not isinstance(item, Mapping):
            continue
        reference = item.get("object")
        if not isinstance(reference, Mapping):
            continue
        name = str(reference.get("object_name") or "")
        if name and name not in names:
            names.append(name)
    return names


def _receipt_names(native_state: Mapping[str, Any]) -> list[str]:
    names = []
    receipts = list(native_state.get("recent_receipts") or [])
    for receipt in reversed(receipts):
        if not isinstance(receipt, Mapping):
            continue
        for category in ("created", "changed", "replaced"):
            for value in list(receipt.get(category) or []):
                if not isinstance(value, Mapping):
                    continue
                name = str(value.get("object_name") or "")
                if name and name not in names:
                    names.append(name)
    return names


def live_working_set(
    document: Any,
    selection: Mapping[str, Any],
    native_state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    get_object = getattr(document, "getObject", None)
    if not callable(get_object):
        return []
    ordered_names = [
        *_selection_names(selection),
        *_receipt_names(native_state),
    ]
    result = []
    seen = set()
    for name in ordered_names:
        if name in seen:
            continue
        seen.add(name)
        obj = get_object(name)
        if obj is None or getattr(obj, "Document", None) is not document:
            continue
        result.append(concise_object(obj))
        if len(result) >= MAX_WORKING_OBJECTS:
            break
    return result


def _domain_builder(
    surface_id: str,
    background_job: Any | None = None,
    selection: Mapping[str, Any] | None = None,
) -> Callable[[Any], Mapping[str, Any]]:
    if surface_id == "model":
        from VibeCADNativeModelSnapshot import build_model_snapshot

        return build_model_snapshot
    if surface_id in {"sketch.setup", "sketch.edit"}:
        from VibeCADNativeSketchSnapshot import build_sketch_snapshot

        return lambda document: build_sketch_snapshot(document, surface_id)
    if surface_id == "assemble":
        from VibeCADNativeAssemblySnapshot import build_assembly_snapshot

        return build_assembly_snapshot
    if surface_id == "mesh":
        from VibeCADNativeMeshSnapshot import build_mesh_snapshot

        return build_mesh_snapshot
    if surface_id == "analyze":
        from VibeCADNativeAnalyzeSnapshot import build_analyze_snapshot

        return lambda document: build_analyze_snapshot(
            document,
            background_job=background_job,
        )
    if surface_id == "manufacture":
        from VibeCADNativeManufactureSnapshot import build_manufacture_snapshot

        return lambda document: build_manufacture_snapshot(
            document,
            selection=selection,
        )
    if surface_id == "drawing":
        from VibeCADNativeDrawingSnapshot import build_drawing_snapshot

        return lambda document: build_drawing_snapshot(
            document,
            selection=selection,
        )
    if surface_id == "parameters":
        from VibeCADNativeParametersSnapshot import build_parameters_snapshot

        return build_parameters_snapshot
    raise NativeSnapshotError(f"No Native state builder exists for {surface_id!r}.")


def build_active_snapshot(
    document: Any,
    surface_id: str,
    native_state: Mapping[str, Any],
    *,
    selection: Mapping[str, Any] | None = None,
    background_job: Any | None = None,
) -> dict[str, Any]:
    if surface_id not in SURFACE_IDS or surface_id == "unavailable":
        raise NativeSnapshotError(f"Invalid active Native surface {surface_id!r}.")
    if not isinstance(native_state, Mapping):
        raise TypeError("native_state must be a mapping")
    uid = document_uid(document)
    if str(native_state.get("document_uid") or "") != uid:
        raise NativeSnapshotError("Native state belongs to another document.")
    selected = dict(selection) if selection is not None else read_current_selection(document)
    if str(selected.get("document_uid") or "") != uid:
        raise NativeSnapshotError("Native selection belongs to another document.")
    domain = dict(
        _domain_builder(
            surface_id,
            background_job,
            selected,
        )(document)
    )
    result: dict[str, Any] = {
        "surface_id": surface_id,
        "document": {
            "document_uid": uid,
            "document_name": str(getattr(document, "Name", "") or ""),
        },
        "structural_revision": int(native_state.get("structural_revision") or 0),
        "domain": domain,
        "working_set": live_working_set(document, selected, native_state),
    }
    if surface_id == "sketch.edit":
        revision = domain.pop("revision", None)
        if not isinstance(revision, str) or not revision.startswith("sketch-v1:"):
            raise NativeSnapshotError(
                "The active Sketch did not provide an exact provider revision."
            )
        result["revision"] = revision
    if selected.get("items"):
        result["selection"] = selected
    encoded = json.dumps(
        result,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > MAX_NATIVE_SNAPSHOT_BYTES:
        raise NativeSnapshotError("The active Native state snapshot exceeds its bound.")
    return json.loads(encoded)
