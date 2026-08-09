# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared retained-graph attachment for Model standard fasteners."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from VibeCADFastenerModel import (
    ModelFastenerGraph,
    copy_fastener_appearance,
    model_fastener_graph_from_body,
    validate_model_fastener_graph,
)


_EDGE_NAME = re.compile(r"^Edge[1-9][0-9]*$")
_CIRCLE_CURVE = "Part::GeomCircle"


@dataclass(frozen=True, slots=True)
class ModelFastenerAttachment:
    graph: ModelFastenerGraph
    requested_host: Any
    requested_subelement: str
    definition_host: Any
    definition_subelements: tuple[str, ...]


def _timeline_root(obj: Any) -> Any | None:
    current = obj
    seen: set[tuple[str, str]] = set()
    while current is not None:
        document = getattr(current, "Document", None)
        key = (
            str(getattr(document, "Uid", "") or getattr(document, "Name", "")),
            str(getattr(current, "Name", "") or id(current)),
        )
        if key in seen:
            return None
        seen.add(key)
        owner = getattr(current, "VibeCADTimelineOwner", None)
        if owner is None:
            return current
        current = owner
    return None


def reference_history_root(document: Any, selected: Any) -> Any | None:
    """Resolve the History root which must precede an exact host reference."""

    timeline = document.getObject("VibeCADTimeline")
    operations = list(getattr(timeline, "Operations", []) or [])
    operation_set = set(operations)
    candidates: list[Any] = [selected]
    if str(getattr(selected, "TypeId", "") or "") == "PartDesign::Body":
        publication = getattr(selected, "Tip", None)
        state = getattr(publication, "CurrentState", None)
        producer = getattr(state, "Operation", None)
        if producer is not None:
            candidates.insert(0, producer)
        legacy_tip = getattr(selected, "Tip", None)
        if legacy_tip is not None:
            candidates.append(legacy_tip)
    else:
        try:
            body = selected.getParentGeoFeatureGroup()
        except (AttributeError, RuntimeError):
            body = None
        if str(getattr(body, "TypeId", "") or "") == "PartDesign::Body":
            candidates.append(body)
    for candidate in candidates:
        root = _timeline_root(candidate)
        if root in operation_set:
            return root
    return None


def _canonical_edge(host: Any, subelement: str) -> tuple[str, Any]:
    name = str(subelement or "")
    if not name or len(name) > 4096:
        raise RuntimeError("A standard-fastener attachment requires one exact EdgeN.")
    shape = getattr(host, "Shape", None)
    if shape is None:
        raise RuntimeError("The standard-fastener attachment host has no shape.")
    try:
        canonical = str(shape.getElementIndexedName(name))
        edge = shape.getElement(name)
    except Exception as exc:
        raise RuntimeError(
            "The exact standard-fastener attachment edge no longer exists."
        ) from exc
    if _EDGE_NAME.fullmatch(canonical) is None:
        raise RuntimeError("A standard-fastener attachment requires one exact EdgeN.")
    if (
        str(getattr(edge, "ShapeType", "") or "") != "Edge"
        or str(getattr(getattr(edge, "Curve", None), "TypeId", "") or "")
        != _CIRCLE_CURVE
    ):
        raise RuntimeError("A standard fastener can attach only to a circular edge.")
    return canonical, edge


def circular_edge(host: Any, subelement: str) -> Any:
    """Return one exact circular host edge or fail without changing state."""

    _canonical, edge = _canonical_edge(host, subelement)
    return edge


def attachment_matches(
    document: Any,
    graph: ModelFastenerGraph,
    host: Any,
    subelement: str,
) -> bool:
    try:
        requested_name, _edge = _canonical_edge(host, subelement)
    except RuntimeError:
        return False
    current = getattr(graph.generator, "BaseObject", None)
    if not current:
        return False
    try:
        current_host, current_names = current
        names = [str(name) for name in list(current_names or [])]
        if len(names) != 1:
            return False
        current_name = str(current_host.Shape.getElementIndexedName(names[0]))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    current_root = reference_history_root(document, current_host)
    requested_root = reference_history_root(document, host)
    same_source = (
        current_root is requested_root
        if requested_root is not None
        else current_host is host
    )
    return (
        current_name == requested_name
        and same_source
    )


def attach_model_fastener_graph(
    document: Any,
    *,
    body: Any,
    host: Any,
    subelement: str,
) -> ModelFastenerAttachment:
    """Attach one modern fastener graph and preserve all document identities."""

    import PartDesign

    if document is None or int(document.getBookedTransactionID()) == 0:
        raise RuntimeError(
            "Model fastener attachment requires one active document transaction."
        )
    graph = model_fastener_graph_from_body(document, body)
    validate_model_fastener_graph(
        document,
        graph,
        label=str(graph.body.Label),
        canonical_key=str(graph.identity["canonical_key"]),
    )
    if (
        host is None
        or getattr(host, "Document", None) is not document
        or document.getObject(str(getattr(host, "Name", "") or "")) is not host
    ):
        raise RuntimeError("The exact standard-fastener attachment host no longer exists.")
    canonical_subelement, _edge = _canonical_edge(host, subelement)
    if any(
        host is item
        for item in (
            graph.body,
            graph.publication,
            graph.state,
            graph.operation,
            graph.generator,
        )
    ):
        raise RuntimeError("A standard fastener cannot attach to its own retained graph.")
    if attachment_matches(document, graph, host, canonical_subelement):
        raise RuntimeError("The standard fastener is already attached to that exact edge.")

    dependency_root = reference_history_root(document, host)
    timeline = document.getObject("VibeCADTimeline")
    history = list(getattr(timeline, "Operations", []) or [])
    if (
        dependency_root is not None
        and dependency_root is not graph.operation
        and dependency_root in history
        and graph.operation in history
        and history.index(graph.operation) < history.index(dependency_root)
    ):
        document.reorderTimelineOperationBlocksAfter(
            [graph.operation],
            dependency_root,
        )

    edit = PartDesign.beginDesignOperationEdit(graph.operation)
    exact_host, exact_names = PartDesign.resolveDesignDefinitionSubelementReference(
        graph.operation,
        host,
        [subelement],
    )
    names = tuple(str(name) for name in list(exact_names or []))
    if exact_host is None or len(names) != 1:
        raise RuntimeError("The circular edge did not resolve into exact Design History.")
    graph.generator.BaseObject = (exact_host, list(names))
    if document.recompute([graph.generator, graph.operation], True, True) is False:
        raise RuntimeError("The attached standard fastener failed to recompute.")
    error = str(getattr(graph.generator, "VibeCADFastenerError", "") or "")
    if error:
        raise RuntimeError(error)
    outputs = list(PartDesign.finalizeDesignOperationEdit(edit) or [])
    if len(outputs) != 1 or outputs[0] is not graph.body:
        raise RuntimeError(
            "The attached standard fastener did not retain its exact Body."
        )
    copy_fastener_appearance(graph.generator, graph.body)
    updated = model_fastener_graph_from_body(document, graph.body)
    if (
        updated.publication is not graph.publication
        or updated.state is not graph.state
        or updated.operation is not graph.operation
        or updated.generator is not graph.generator
    ):
        raise RuntimeError("The attached standard fastener replaced retained identities.")
    validate_model_fastener_graph(
        document,
        updated,
        label=str(updated.body.Label),
        canonical_key=str(updated.identity["canonical_key"]),
    )
    try:
        retained_host, retained_names = updated.generator.BaseObject
        retained_names = tuple(
            str(name) for name in list(retained_names or [])
        )
        retained_canonical = str(
            retained_host.Shape.getElementIndexedName(retained_names[0])
        )
    except (AttributeError, IndexError, RuntimeError, TypeError) as exc:
        raise RuntimeError(
            "The standard fastener did not retain its exact circular-edge reference."
        ) from exc
    if (
        retained_host is not exact_host
        or len(retained_names) != 1
        or retained_canonical != canonical_subelement
    ):
        raise RuntimeError(
            "The standard fastener changed its exact circular-edge reference."
        )
    return ModelFastenerAttachment(
        updated,
        host,
        canonical_subelement,
        retained_host,
        retained_names,
    )
