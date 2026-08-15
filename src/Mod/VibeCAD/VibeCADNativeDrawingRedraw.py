# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact preflight, adoption, and verification for Native page redraw."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionWorker import projection_snapshot
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import (
    drawing_source_state,
    is_drawing_view,
    is_part_drawing_view,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


MAX_REDRAW_VIEWS = 128


@dataclass(frozen=True, slots=True)
class RedrawViewState:
    view: Any
    object_name: str
    type_id: str
    kind: str
    state_sha256: str
    state: dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PreparedPageRedraw:
    page: Any
    page_state_before: dict[str, Any]
    views: tuple[RedrawViewState, ...]
    objects_before: tuple[tuple[int, str, str], ...]
    timeline_before: tuple[tuple[int, str, str], ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[str, bool], ...]
    cache_before: tuple[tuple[str, str, dict[str, Any], str], ...] = field(
        default=(),
        repr=False,
        compare=False,
    )


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _object_graph(objects: tuple[Any, ...]) -> tuple[tuple[int, str, str], ...]:
    return tuple(
        (
            int(getattr(obj, "ID", -1)),
            str(getattr(obj, "Name", "") or ""),
            str(getattr(obj, "TypeId", "") or ""),
        )
        for obj in objects
    )


def _document_graph(document: Any) -> tuple[tuple[int, str, str], ...]:
    return _object_graph(tuple(document.Objects))


def _timeline(document: Any) -> tuple[tuple[int, str, str], ...]:
    timeline = document.getObject("VibeCADTimeline")
    return (
        _object_graph(tuple(getattr(timeline, "Operations", ()) or ()))
        if timeline
        else ()
    )


def _selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _visibility(document: Any) -> tuple[tuple[str, bool], ...]:
    return tuple(
        (str(obj.Name), bool(obj.ViewObject.Visibility))
        for obj in tuple(document.Objects)
        if getattr(obj, "ViewObject", None) is not None
    )


def _vector(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        return [
            round(float(getattr(value, name)), 12)
            for name in ("x", "y", "z")
        ]
    except (AttributeError, TypeError, ValueError):
        return None


def _links(view: Any, property_name: str) -> list[dict[str, str]]:
    values = tuple(getattr(view, property_name, ()) or ())
    result = []
    for obj in values:
        result.append(
            {
                "document_uid": str(getattr(getattr(obj, "Document", None), "Uid", "") or ""),
                "object_name": str(getattr(obj, "Name", "") or ""),
                "type_id": str(getattr(obj, "TypeId", "") or ""),
            }
        )
    return result


_SAFE_PROPERTY_TYPES = frozenset(
    {
        "App::PropertyAngle",
        "App::PropertyBool",
        "App::PropertyBoolList",
        "App::PropertyColor",
        "App::PropertyDistance",
        "App::PropertyEnumeration",
        "App::PropertyFloat",
        "App::PropertyFloatConstraint",
        "App::PropertyFloatList",
        "App::PropertyInteger",
        "App::PropertyIntegerList",
        "App::PropertyLink",
        "App::PropertyLinkList",
        "App::PropertyLinkSub",
        "App::PropertyLinkSubList",
        "App::PropertyString",
        "App::PropertyStringList",
        "App::PropertyVector",
        "App::PropertyVectorList",
        "App::PropertyXLink",
        "App::PropertyXLinkList",
    }
)
_NON_INPUT_PROPERTY_GROUPS = frozenset(
    {"Precomputed Dimension", "Precomputed Projection"}
)


def _stable_value(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Drawing property value must be finite")
        return round(value, 12)
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    document = getattr(value, "Document", None)
    object_name = str(getattr(value, "Name", "") or "")
    if document is not None and object_name:
        return {
            "document_uid": str(getattr(document, "Uid", "") or ""),
            "object_name": object_name,
            "type_id": str(getattr(value, "TypeId", "") or ""),
        }
    if all(hasattr(value, name) for name in ("x", "y", "z")):
        return [round(float(getattr(value, name)), 12) for name in ("x", "y", "z")]
    quantity = getattr(value, "Value", None)
    if isinstance(quantity, (int, float)):
        return _stable_value(float(quantity))
    return str(value)


def _view_properties(view: Any) -> dict[str, Any]:
    result = {}
    for name in sorted(str(item) for item in tuple(view.PropertiesList or ())):
        try:
            property_type = str(view.getTypeIdOfProperty(name) or "")
            group = str(view.getGroupOfProperty(name) or "")
            if group in _NON_INPUT_PROPERTY_GROUPS:
                continue
            item: dict[str, Any] = {"type": property_type}
            if property_type in _SAFE_PROPERTY_TYPES:
                item["value"] = _stable_value(view.getPropertyByName(name))
            result[name] = item
        except Exception as exc:
            raise NativeDrawingError(
                f"Drawing view {view.Name!r} has an unreadable authored property.",
                error_code="NATIVE_DRAWING_REDRAW_VIEW_INVALID",
            ) from exc
    return result


def _source_geometry(view: Any) -> list[dict[str, Any]]:
    result = []
    names = set()
    for property_name in ("Source", "XSource"):
        for source in tuple(getattr(view, property_name, ()) or ()):
            name = str(getattr(source, "Name", "") or "")
            if not name or name in names or is_drawing_view(source):
                continue
            names.add(name)
            try:
                state = drawing_source_state(source)
            except Exception as exc:
                raise NativeDrawingError(
                    f"Drawing source {name!r} has no exact projectable shape state.",
                    error_code="NATIVE_DRAWING_REDRAW_SOURCE_INVALID",
                ) from exc
            result.append(
                {
                    "object_name": name,
                    "type_id": str(source.TypeId),
                    "state_sha256": str(state["state_sha256"]),
                }
            )
    return result


def _projection_cache(view: Any) -> tuple[dict[str, Any], str]:
    snapshot = view.getPrecomputedProjection()
    values = {
        "edges": snapshot["edges"].copy(),
        "faces": snapshot["faces"].copy(),
        "edge_classes": tuple(int(item) for item in snapshot["edge_classes"]),
        "edge_visibility": tuple(bool(item) for item in snapshot["edge_visibility"]),
        "source_indices": tuple(int(item) for item in snapshot["source_indices"]),
        "centroid": tuple(
            float(getattr(snapshot["centroid"], name)) for name in ("x", "y", "z")
        ),
    }
    semantic = {
        "projected_elements": view.getProjectedElementDescriptors(),
        "face_count": len(tuple(values["faces"].Faces)),
        "metadata": {
            name: values[name]
            for name in (
                "edge_classes",
                "edge_visibility",
                "source_indices",
                "centroid",
            )
        },
    }
    encoded = json.dumps(
        semantic,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return values, hashlib.sha256(encoded).hexdigest()


def _dimension_cache(view: Any) -> tuple[dict[str, Any], str]:
    snapshot = view.getPrecomputedDimension()
    values = {
        "vectors": tuple(
            tuple(float(getattr(vector, name)) for name in ("x", "y", "z"))
            for vector in snapshot["vectors"]
        ),
        "scalars": tuple(float(item) for item in snapshot["scalars"]),
        "flags": tuple(bool(item) for item in snapshot["flags"]),
    }
    return values, _digest(values)


def _capture_caches(
    views: tuple[RedrawViewState, ...],
) -> tuple[tuple[str, str, dict[str, Any], str], ...]:
    result = []
    for item in views:
        if item.kind == "projection":
            snapshot, signature = _projection_cache(item.view)
        elif item.kind == "dimension":
            snapshot, signature = _dimension_cache(item.view)
        else:
            continue
        result.append((item.object_name, item.kind, snapshot, signature))
    return tuple(result)


def redraw_view_state(view: Any) -> dict[str, Any]:
    """Return inputs that determine one existing page view's recomputation."""

    if not is_drawing_view(view):
        raise TypeError("view must be a TechDraw::DrawView")
    type_id = str(view.TypeId)
    if is_part_drawing_view(view):
        kind = "projection"
    elif bool(getattr(view, "isDerivedFrom", lambda _name: False)("TechDraw::DrawViewDimension")):
        kind = "dimension"
    else:
        kind = "dependent"
    inputs = {
        "object_name": str(view.Name),
        "type_id": type_id,
        "kind": kind,
        "source": _links(view, "Source"),
        "x_source": _links(view, "XSource"),
        "source_geometry": _source_geometry(view),
        "direction": _vector(getattr(view, "Direction", None)),
        "x_direction": _vector(getattr(view, "XDirection", None)),
        "properties": _view_properties(view),
    }
    return {**inputs, "state_sha256": _digest(inputs)}


def _active_page_views(page: Any) -> tuple[Any, ...]:
    getter = getattr(page, "getAllActiveViews", None)
    if callable(getter):
        try:
            page_views = tuple(getter() or ())
        except Exception as exc:
            raise NativeDrawingError(
                "The exact Drawing page view graph is unreadable.",
                error_code="NATIVE_DRAWING_REDRAW_VIEW_INVALID",
            ) from exc
    else:
        page_views = tuple(getattr(page, "Views", ()) or ())
    document = getattr(page, "Document", None)
    canonical = []
    names = set()
    for view in page_views:
        name = str(getattr(view, "Name", "") or "")
        object_id = int(getattr(view, "ID", -1))
        current = document.getObject(name) if document is not None and name else None
        if (
            current is None
            or int(getattr(current, "ID", -2)) != object_id
            or name in names
        ):
            raise NativeDrawingError(
                "The exact Drawing page contains an invalid or duplicate view identity.",
                error_code="NATIVE_DRAWING_REDRAW_VIEW_INVALID",
            )
        names.add(name)
        canonical.append(current)
    return tuple(canonical)


def _view_graph_identity(views: tuple[Any, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            str(getattr(view, "Name", "") or ""),
            str(getattr(view, "TypeId", "") or ""),
        )
        for view in views
    )


def _require_current_document_sources(document: Any, view: Any) -> None:
    for property_name in ("Source", "XSource"):
        for source in tuple(getattr(view, property_name, ()) or ()):
            if getattr(source, "Document", None) is not document:
                raise NativeDrawingError(
                    f"Drawing view {view.Name!r} has a source outside the active document.",
                    error_code="NATIVE_DRAWING_REDRAW_EXTERNAL_SOURCE_UNSUPPORTED",
                )


def prepare_page_redraw(
    document: Any,
    *,
    target: Mapping[str, Any],
) -> PreparedPageRedraw:
    page = resolve_object(
        document,
        {
            "document_uid": str(document.Uid),
            "object_name": target["object_name"],
        },
        expected_types=("TechDraw::DrawPage",),
    )
    page_state = drawing_page_state(page)
    if str(target["expected_state_sha256"]) != page_state["state_sha256"]:
        raise NativeDrawingError(
            "The exact Drawing page changed after it was inspected.",
            error_code="NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    views = _active_page_views(page)
    if not 1 <= len(views) <= MAX_REDRAW_VIEWS:
        raise NativeDrawingError(
            f"A page redraw requires 1 to {MAX_REDRAW_VIEWS} active views.",
            error_code="NATIVE_DRAWING_REDRAW_VIEW_LIMIT",
        )
    states = []
    for view in views:
        if not is_drawing_view(view) or getattr(view, "Document", None) is not document:
            raise NativeDrawingError(
                "The exact Drawing page contains an unsupported view object.",
                error_code="NATIVE_DRAWING_REDRAW_VIEW_INVALID",
            )
        _require_current_document_sources(document, view)
        state = redraw_view_state(view)
        states.append(
            RedrawViewState(
                view=view,
                object_name=str(view.Name),
                type_id=str(view.TypeId),
                kind=str(state["kind"]),
                state_sha256=str(state["state_sha256"]),
                state=state,
            )
        )
    frozen_views = tuple(states)
    return PreparedPageRedraw(
        page=page,
        page_state_before=page_state,
        views=frozen_views,
        objects_before=_document_graph(document),
        timeline_before=_timeline(document),
        selection_before=_selection(document),
        visibility_before=_visibility(document),
        cache_before=_capture_caches(frozen_views),
    )


def validate_prepared_page_redraw(
    document: Any,
    prepared: PreparedPageRedraw,
) -> None:
    if not isinstance(prepared, PreparedPageRedraw):
        raise TypeError("prepared must be a PreparedPageRedraw")
    page_name = str(prepared.page_state_before["object_name"])
    current_page = document.getObject(page_name)
    if (
        current_page is None
        or str(getattr(current_page, "TypeId", "")) != "TechDraw::DrawPage"
        or _document_graph(document) != prepared.objects_before
        or _timeline(document) != prepared.timeline_before
        or drawing_page_state(current_page)["state_sha256"]
        != prepared.page_state_before["state_sha256"]
    ):
        raise NativeDrawingError(
            "The exact Drawing page or document structure changed during redraw.",
            error_code="NATIVE_DRAWING_REDRAW_STALE",
        )
    current_views = _active_page_views(current_page)
    if _view_graph_identity(current_views) != tuple(
        (item.object_name, item.type_id) for item in prepared.views
    ):
        raise NativeDrawingError(
            "The exact Drawing page view graph changed during redraw.",
            error_code="NATIVE_DRAWING_REDRAW_STALE",
        )
    for expected in prepared.views:
        current_view = document.getObject(expected.object_name)
        if current_view is None:
            raise NativeDrawingError(
                f"Drawing view {expected.object_name!r} was removed during redraw.",
                error_code="NATIVE_DRAWING_REDRAW_STALE",
            )
        current_state = redraw_view_state(current_view)
        if (
            str(current_view.TypeId) != expected.type_id
            or current_state["state_sha256"] != expected.state_sha256
        ):
            before_properties = dict(expected.state.get("properties") or {})
            current_properties = dict(current_state.get("properties") or {})
            changed_properties = sorted(
                name
                for name in set(before_properties) | set(current_properties)
                if before_properties.get(name) != current_properties.get(name)
            )[:32]
            changed_fields = sorted(
                name
                for name in set(expected.state) | set(current_state)
                if name not in {"properties", "state_sha256"}
                and expected.state.get(name) != current_state.get(name)
            )[:16]
            raise NativeDrawingError(
                f"Drawing view {expected.object_name!r} changed during redraw.",
                error_code="NATIVE_DRAWING_REDRAW_STALE",
                repair={
                    "current_state_sha256": current_state["state_sha256"],
                    "changed_properties": changed_properties,
                    "changed_fields": changed_fields,
                    "source_geometry_changed": (
                        expected.state.get("source_geometry")
                        != current_state.get("source_geometry")
                    ),
                },
            )
    for object_name, kind, _snapshot, expected_signature in prepared.cache_before:
        view = document.getObject(object_name)
        if view is None:
            raise NativeDrawingError(
                f"Drawing view {object_name!r} was removed during redraw.",
                error_code="NATIVE_DRAWING_REDRAW_STALE",
            )
        if kind == "projection":
            _current, current_signature = _projection_cache(view)
        else:
            _current, current_signature = _dimension_cache(view)
        if current_signature != expected_signature:
            raise NativeDrawingError(
                f"Drawing view {object_name!r} cache changed during redraw.",
                error_code="NATIVE_DRAWING_REDRAW_STALE",
            )


def capture_page_redraw_commit_state(
    document: Any,
    prepared: PreparedPageRedraw,
) -> PreparedPageRedraw:
    validate_prepared_page_redraw(document, prepared)
    page = document.getObject(str(prepared.page_state_before["object_name"]))
    views = tuple(
        replace(item, view=document.getObject(item.object_name))
        for item in prepared.views
    )
    return replace(
        prepared,
        page=page,
        views=views,
        selection_before=_selection(document),
        visibility_before=_visibility(document),
    )


def restore_page_redraw_commit_state(
    document: Any,
    prepared: PreparedPageRedraw,
) -> bool:
    """Restore live TechDraw caches after a failed atomic adoption."""

    import FreeCAD as App

    restored = False
    for object_name, kind, snapshot, expected_signature in prepared.cache_before:
        view = document.getObject(object_name)
        if view is None:
            raise NativeDrawingError(
                "Drawing page redraw failed and its prior cache could not be restored.",
                error_code="NATIVE_DRAWING_REDRAW_ROLLBACK_FAILED",
            )
        if kind == "projection":
            _current, current_signature = _projection_cache(view)
            if current_signature == expected_signature:
                continue
            view.setPrecomputedProjection(
                {
                    **snapshot,
                    "centroid": App.Vector(*snapshot["centroid"]),
                }
            )
        elif kind == "dimension":
            _current, current_signature = _dimension_cache(view)
            if current_signature == expected_signature:
                continue
            view.setPrecomputedDimension(
                {
                    **snapshot,
                    "vectors": [App.Vector(*value) for value in snapshot["vectors"]],
                }
            )
        view.purgeTouched()
        if kind == "projection":
            _restored, restored_signature = _projection_cache(view)
        else:
            _restored, restored_signature = _dimension_cache(view)
        if restored_signature != expected_signature:
            raise NativeDrawingError(
                "Drawing page redraw failed and its prior cache could not be verified.",
                error_code="NATIVE_DRAWING_REDRAW_ROLLBACK_FAILED",
            )
        restored = True
    return restored


class _PageRedrawCacheMutation:
    """Exact non-undo savepoint for derived Drawing cache replacement."""

    creates_undo_entry = False

    def __init__(
        self,
        document: Any,
        _name: str,
        prepared: PreparedPageRedraw,
    ) -> None:
        validate_prepared_page_redraw(document, prepared)
        self._document = document
        self._prepared = prepared
        self._closed = False

    def commit(self) -> None:
        self._closed = True

    def abort(self) -> None:
        if self._closed:
            return
        restore_page_redraw_commit_state(self._document, self._prepared)
        self._closed = True


def page_redraw_transaction_factory(
    prepared: PreparedPageRedraw,
) -> Any:
    """Return a derived-cache boundary that never pollutes user undo history."""

    return lambda document, name: _PageRedrawCacheMutation(
        document,
        name,
        prepared,
    )


def adopt_page_redraw(
    document: Any,
    *,
    prepared: PreparedPageRedraw,
    worker_result: Any,
) -> NativeMutationDraft:
    import FreeCAD as App

    expected_names = tuple(item.object_name for item in prepared.views)
    if tuple(worker_result.view_names) != expected_names:
        raise NativeDrawingError(
            "The detached redraw result changed the exact page view graph.",
            error_code="NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    projections = {item.object_name: item for item in worker_result.projections}
    dimensions = {item.object_name: item for item in worker_result.dimensions}
    dependent_views = []
    for expected in prepared.views:
        view = expected.view
        if expected.kind == "projection":
            projection = projections.get(expected.object_name)
            if projection is None or projection.type_id != expected.type_id:
                raise NativeDrawingError(
                    f"Detached redraw omitted projection {expected.object_name!r}.",
                    error_code="NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
                )
            view.setPrecomputedProjection(projection_snapshot(projection.projection))
            view.purgeTouched()
        elif expected.kind == "dimension":
            dimension = dimensions.get(expected.object_name)
            if dimension is None or dimension.type_id != expected.type_id:
                raise NativeDrawingError(
                    f"Detached redraw omitted dimension {expected.object_name!r}.",
                    error_code="NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
                )
            view.setPrecomputedDimension(
                {
                    "vectors": [App.Vector(*value) for value in dimension.vectors],
                    "scalars": list(dimension.scalars),
                    "flags": list(dimension.flags),
                }
            )
            view.purgeTouched()
        else:
            # These views have no transferable TechDraw cache.  Recompute only
            # this bounded, prevalidated dependent set after every expensive
            # part projection and dimension cache has been adopted.
            view.touch()
            dependent_views.append(view)
    prepared.page.requestPaint()
    changed = (prepared.page, *(item.view for item in prepared.views))
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "worker_result": worker_result,
        },
        recompute_targets=tuple(dependent_views),
        changed=tuple(object_identity(obj) for obj in changed),
    )


def _projection_counts(view: Any) -> tuple[int, int, int]:
    snapshot = view.getPrecomputedProjection()
    edges = len(tuple(snapshot["edges"].Edges))
    faces = len(tuple(snapshot["faces"].Faces))
    visible = sum(bool(value) for value in snapshot["edge_visibility"])
    return edges, faces, visible


def _dimension_values(view: Any) -> tuple[tuple[tuple[float, float, float], ...], tuple[float, ...], tuple[bool, ...]]:
    snapshot = view.getPrecomputedDimension()
    vectors = tuple(
        (float(value.x), float(value.y), float(value.z))
        for value in snapshot["vectors"]
    )
    return vectors, tuple(float(value) for value in snapshot["scalars"]), tuple(
        bool(value) for value in snapshot["flags"]
    )


def verify_page_redraw(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedPageRedraw = draft.value["prepared"]
    worker = draft.value["worker_result"]
    if (
        _document_graph(document) != prepared.objects_before
        or _timeline(document) != prepared.timeline_before
        or _view_graph_identity(_active_page_views(prepared.page))
        != tuple((item.object_name, item.type_id) for item in prepared.views)
    ):
        raise NativeDrawingError(
            "Page redraw changed document structure or History.",
            error_code="NATIVE_DRAWING_REDRAW_POSTCONDITION_FAILED",
        )
    summaries = []
    projection_map = {item.object_name: item for item in worker.projections}
    dimension_map = {item.object_name: item for item in worker.dimensions}
    for expected in prepared.views:
        view = expected.view
        if not bool(view.isValid()):
            raise NativeDrawingError(
                f"Redrawn view {expected.object_name!r} is invalid.",
                error_code="NATIVE_DRAWING_REDRAW_POSTCONDITION_FAILED",
            )
        summary: dict[str, Any] = {
            "object_name": expected.object_name,
            "type_id": expected.type_id,
            "kind": expected.kind,
        }
        if expected.kind == "projection":
            result = projection_map[expected.object_name]
            edges, faces, visible = _projection_counts(view)
            if (edges, faces, visible) != (
                result.projection.edge_count,
                result.projection.face_count,
                result.projection.visible_edge_count,
            ):
                raise NativeDrawingError(
                    f"Redrawn projection {expected.object_name!r} failed readback.",
                    error_code="NATIVE_DRAWING_REDRAW_POSTCONDITION_FAILED",
                )
            summary.update(
                {
                    "edge_count": edges,
                    "face_count": faces,
                    "visible_edge_count": visible,
                }
            )
        elif expected.kind == "dimension":
            result = dimension_map[expected.object_name]
            if _dimension_values(view) != (
                result.vectors,
                result.scalars,
                result.flags,
            ):
                raise NativeDrawingError(
                    f"Redrawn dimension {expected.object_name!r} failed readback.",
                    error_code="NATIVE_DRAWING_REDRAW_POSTCONDITION_FAILED",
                )
        summaries.append(summary)
    actual_visibility = tuple(
        (name, bool(document.getObject(name).ViewObject.Visibility))
        for name, _value in prepared.visibility_before
        if document.getObject(name) is not None
    )
    if (
        _selection(document) != prepared.selection_before
        or actual_visibility != prepared.visibility_before
    ):
        raise NativeDrawingError(
            "Page redraw changed selection or object visibility.",
            error_code="NATIVE_DRAWING_REDRAW_POSTCONDITION_FAILED",
        )
    page_state = drawing_page_state(prepared.page)
    return {
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "redrawn_views": summaries,
    }
