# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded active-surface state assembled only from the live document."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from VibeCADNativeTargets import document_uid, object_reference, read_current_selection
from VibeCADRibbonSurface import SURFACE_IDS


MAX_NATIVE_SNAPSHOT_BYTES = 64 * 1024
MAX_WORKING_OBJECTS = 12
MAX_DEFERRED_SECTION_SUMMARIES = 64


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
    *,
    include_object: Callable[[Any], bool] | None = None,
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
        if include_object is not None and not include_object(obj):
            continue
        result.append(concise_object(obj))
        if len(result) >= MAX_WORKING_OBJECTS:
            break
    return result


def _domain_builder(
    surface_id: str,
    background_job: Any | None = None,
    selection: Mapping[str, Any] | None = None,
    structural_revision: int | None = None,
) -> Callable[[Any], Mapping[str, Any]]:
    if surface_id == "model":
        from VibeCADNativeModelSnapshot import build_model_snapshot

        return build_model_snapshot
    if surface_id in {"sketch.setup", "sketch.edit"}:
        from VibeCADNativeSketchSnapshot import build_sketch_snapshot

        return lambda document: build_sketch_snapshot(
            document,
            surface_id,
            selection=selection,
        )
    if surface_id == "assemble":
        from VibeCADNativeAssemblySnapshot import build_assembly_snapshot

        return build_assembly_snapshot
    if surface_id == "mesh":
        from VibeCADNativeMeshSnapshot import build_mesh_snapshot

        return lambda document: build_mesh_snapshot(document, selection=selection)
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
            background_jobs=(
                background_job if isinstance(background_job, tuple) else ()
            ),
        )
    if surface_id == "drawing":
        from VibeCADNativeDrawingSnapshot import build_drawing_snapshot

        return lambda document: build_drawing_snapshot(
            document,
            selection=selection,
            structural_revision=structural_revision,
        )
    if surface_id == "parameters":
        from VibeCADNativeParametersSnapshot import build_parameters_snapshot

        return build_parameters_snapshot
    if surface_id == "aero":
        from VibeCADNativeAeroSnapshot import build_aero_snapshot

        return build_aero_snapshot
    raise NativeSnapshotError(f"No Native state builder exists for {surface_id!r}.")


def build_active_snapshot(
    document: Any,
    surface_id: str,
    native_state: Mapping[str, Any],
    *,
    selection: Mapping[str, Any] | None = None,
    background_job: Any | None = None,
) -> dict[str, Any]:
    base = capture_active_snapshot_base(
        document,
        surface_id,
        native_state,
        selection=selection,
    )
    selected = dict(base.pop("_selection"))
    domain = dict(
        _domain_builder(
            surface_id,
            background_job,
            selected,
            int(base.get("structural_revision", 0) or 0),
        )(document)
    )
    return complete_active_snapshot(base, domain)


def capture_active_snapshot_base(
    document: Any,
    surface_id: str,
    native_state: Mapping[str, Any],
    *,
    selection: Mapping[str, Any] | None = None,
    analysis_artifact_names: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Capture detached active-surface identity before domain preparation."""

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
    include_working_object = None
    if surface_id == "analyze":
        from VibeCADNativeGeometrySources import (
            drawing_analysis_artifact_names,
            filter_analyze_selection,
            is_analyze_context_reference,
        )

        analysis_artifacts = (
            drawing_analysis_artifact_names(document)
            if analysis_artifact_names is None
            else analysis_artifact_names
        )
        selected = filter_analyze_selection(
            document,
            selected,
            analysis_artifact_names=analysis_artifacts,
        )

        def include_available_object(obj: Any) -> bool:
            return is_analyze_context_reference(
                document,
                obj,
                analysis_artifact_names=analysis_artifacts,
            )

        include_working_object = include_available_object
    elif surface_id == "drawing":
        from VibeCADNativeGeometrySources import (
            drawing_analysis_artifact_names,
            drawing_source_exclusion_reason,
            filter_drawing_selection,
        )

        analysis_artifacts = (
            drawing_analysis_artifact_names(document)
            if analysis_artifact_names is None
            else analysis_artifact_names
        )

        def include_drawing_object(obj: Any) -> bool:
            return (
                drawing_source_exclusion_reason(
                    document,
                    obj,
                    analysis_artifact_names=analysis_artifacts,
                )
                is None
            )

        selected = filter_drawing_selection(
            document,
            selected,
            analysis_artifact_names=analysis_artifacts,
        )
        include_working_object = include_drawing_object
    elif surface_id == "mesh":
        from VibeCADNativeMeshSnapshot import mesh_object_is_context_active

        include_working_object = mesh_object_is_context_active
    working_set = live_working_set(
        document,
        selected,
        native_state,
        include_object=include_working_object,
    )
    result: dict[str, Any] = {
        "surface_id": surface_id,
        "document": {
            "document_uid": uid,
            "document_name": str(getattr(document, "Name", "") or ""),
        },
        "structural_revision": int(native_state.get("structural_revision") or 0),
        "working_set": working_set,
        "_selection": selected,
    }
    if selected.get("items"):
        result["selection"] = selected
    return result


def complete_active_snapshot(
    base_snapshot: Mapping[str, Any],
    domain: Mapping[str, Any],
) -> dict[str, Any]:
    """Bound and freeze a detached domain under its captured active identity."""

    if not isinstance(base_snapshot, Mapping) or not isinstance(domain, Mapping):
        raise TypeError("Active snapshot completion requires mappings.")
    result = {
        str(name): value
        for name, value in dict(base_snapshot).items()
        if name != "_selection"
    }
    result["domain"] = dict(domain)
    if result.get("surface_id") == "sketch.edit":
        revision = result["domain"].pop("revision", None)
        if not isinstance(revision, str) or not revision.startswith("sketch-v1:"):
            raise NativeSnapshotError(
                "The active Sketch did not provide an exact provider revision."
            )
        result["revision"] = revision
    encoded = _encode_snapshot(result)
    if len(encoded.encode("utf-8")) > MAX_NATIVE_SNAPSHOT_BYTES:
        result = _compact_oversized_snapshot(result, len(encoded.encode("utf-8")))
        encoded = _encode_snapshot(result)
    return json.loads(encoded)


def _encode_snapshot(snapshot: Mapping[str, Any]) -> str:
    return json.dumps(
        snapshot,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _section_summary(name: str, value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": str(name),
        "kind": (
            "array"
            if isinstance(value, list)
            else "object"
            if isinstance(value, Mapping)
            else type(value).__name__
        ),
    }
    if isinstance(value, (list, Mapping)):
        result["item_count"] = len(value)
    return result


def _deferred_domain_manifest(
    domain: Mapping[str, Any],
    original_bytes: int,
) -> dict[str, Any]:
    sections = [
        _section_summary(str(name), value) for name, value in domain.items()
    ]
    result: dict[str, Any] = {
        "kind": str(domain.get("kind") or "unknown"),
        "snapshot_truncated": True,
        "detail_deferred": True,
        "original_snapshot_bytes": int(original_bytes),
        "section_count": len(sections),
        "sections": sections[:MAX_DEFERRED_SECTION_SUMMARIES],
    }
    if len(sections) > MAX_DEFERRED_SECTION_SUMMARIES:
        result["sections_truncated"] = True
    return result


def _bounded_selection(selection: Mapping[str, Any]) -> dict[str, Any]:
    """Keep a useful exact selection even if the bounded base itself is excessive."""

    items = []
    raw_items = list(selection.get("items") or [])
    for raw_item in raw_items[:8]:
        if not isinstance(raw_item, Mapping):
            continue
        item = dict(raw_item)
        subelements = list(item.get("subelements") or [])
        if len(subelements) > 16:
            item["subelements"] = subelements[:16]
            item["subelements_truncated"] = True
        items.append(item)
    result = {
        str(name): value
        for name, value in selection.items()
        if name != "items"
    }
    result["items"] = items
    if len(raw_items) > len(items):
        result["truncated"] = True
    return result


def _compact_oversized_snapshot(
    snapshot: Mapping[str, Any],
    original_bytes: int,
) -> dict[str, Any]:
    """Return bounded provider context instead of rejecting the conversation."""

    result = dict(snapshot)
    domain = result.get("domain")
    if not isinstance(domain, Mapping):
        domain = {}

    if result.get("surface_id") == "drawing":
        from VibeCADNativeDrawingSnapshot import compact_drawing_snapshot_for_bound

        result["domain"] = compact_drawing_snapshot_for_bound(domain)
        if len(_encode_snapshot(result).encode("utf-8")) <= MAX_NATIVE_SNAPSHOT_BYTES:
            return result

    result["domain"] = _deferred_domain_manifest(domain, original_bytes)
    if len(_encode_snapshot(result).encode("utf-8")) <= MAX_NATIVE_SNAPSHOT_BYTES:
        return result

    # Domain detail is already deferred at this point. Remove duplicated working-set
    # context before touching the user's exact selection.
    if result.get("working_set"):
        result["working_set"] = []
        result["working_set_truncated"] = True
    if len(_encode_snapshot(result).encode("utf-8")) <= MAX_NATIVE_SNAPSHOT_BYTES:
        return result

    selection = result.get("selection")
    if isinstance(selection, Mapping):
        result["selection"] = _bounded_selection(selection)
        result["selection_truncated_for_snapshot"] = True
    if len(_encode_snapshot(result).encode("utf-8")) <= MAX_NATIVE_SNAPSHOT_BYTES:
        return result

    # The production budget is large enough for this identity-only form. Keeping this
    # final form deterministic makes a pathological extension safe without weakening
    # the normal 64 KiB contract.
    minimal = {
        name: result[name]
        for name in (
            "surface_id",
            "document",
            "structural_revision",
            "revision",
        )
        if name in result
    }
    minimal["domain"] = {
        "kind": str(domain.get("kind") or "unknown"),
        "snapshot_truncated": True,
        "detail_deferred": True,
        "original_snapshot_bytes": int(original_bytes),
        "section_count": len(domain),
        "sections": [],
    }
    return minimal
