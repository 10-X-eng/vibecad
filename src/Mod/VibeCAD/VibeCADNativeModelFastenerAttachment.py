# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact parsing, mutation, and proof for Model fastener attachment."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADFastenerAttachment import (
    ModelFastenerAttachment,
    attach_model_fastener_graph,
    attachment_matches,
    circular_edge,
    reference_history_root,
)
from VibeCADFastenerModel import (
    model_fastener_graph_from_body,
    validate_model_fastener_graph,
)
from VibeCADFasteners import FastenerCatalogError
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeElementRef,
    NativeObjectRef,
    document_uid,
    object_identity,
    object_reference,
    resolve_element,
    resolve_object,
)


@dataclass(frozen=True, slots=True)
class PreparedModelFastenerAttachment:
    fastener_ref: NativeObjectRef
    host_ref: NativeObjectRef
    subelement: str
    canonical_key: str


def _object_ref(uid: str, value: Any, *, label: str) -> NativeObjectRef:
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError(f"A fastener-attachment {label} target is invalid.")
    return NativeObjectRef(uid, str(value["object_name"] or ""))


def prepare_model_fastener_attachment(
    document: Any,
    value: Any,
) -> PreparedModelFastenerAttachment:
    """Resolve the Body and circular edge before a transaction opens."""

    if not isinstance(value, Mapping) or set(value) != {"fastener", "host"}:
        raise NativeModelError("A standard-fastener attachment is invalid.")
    uid = document_uid(document)
    fastener_ref = _object_ref(uid, value["fastener"], label="fastener")
    host_value = value["host"]
    if not isinstance(host_value, Mapping) or set(host_value) != {
        "object_name",
        "subelement",
    }:
        raise NativeModelError("A fastener-attachment host edge is invalid.")
    host_ref = NativeObjectRef(uid, str(host_value["object_name"] or ""))
    subelement = str(host_value["subelement"] or "")

    body = resolve_object(
        document,
        fastener_ref,
        expected_types=("PartDesign::Body",),
    )
    host = resolve_object(
        document,
        host_ref,
        expected_types=("PartDesign::Body",),
    )
    resolved_host, _edge = resolve_element(
        document,
        NativeElementRef(host_ref, subelement),
    )
    if resolved_host is not host:
        raise NativeModelError("The exact fastener-attachment host changed.")
    try:
        graph = model_fastener_graph_from_body(document, body)
        validate_model_fastener_graph(
            document,
            graph,
            label=str(graph.body.Label),
            canonical_key=str(graph.identity["canonical_key"]),
        )
        circular_edge(host, subelement)
        if reference_history_root(document, host) is None:
            raise NativeModelError(
                "A standard fastener can attach only to a Body with retained "
                "Design History."
            )
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
            raise NativeModelError(
                "A standard fastener cannot attach to its own retained graph."
            )
        if attachment_matches(document, graph, host, subelement):
            raise NativeModelError(
                "The standard fastener is already attached to that exact edge."
            )
    except NativeModelError:
        raise
    except (FastenerCatalogError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeModelError(str(exc)) from exc
    return PreparedModelFastenerAttachment(
        fastener_ref,
        host_ref,
        subelement,
        str(graph.identity["canonical_key"]),
    )


def attach_model_fastener(
    document: Any,
    *,
    prepared: PreparedModelFastenerAttachment,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedModelFastenerAttachment):
        raise TypeError("prepared must be a PreparedModelFastenerAttachment")
    body = resolve_object(
        document,
        prepared.fastener_ref,
        expected_types=("PartDesign::Body",),
    )
    host = resolve_object(document, prepared.host_ref)
    try:
        graph = model_fastener_graph_from_body(document, body)
        if str(graph.identity["canonical_key"]) != prepared.canonical_key:
            raise NativeModelError("The standard fastener changed after preflight.")
        attachment = attach_model_fastener_graph(
            document,
            body=body,
            host=host,
            subelement=prepared.subelement,
        )
    except NativeModelError:
        raise
    except (FastenerCatalogError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeModelError(str(exc)) from exc
    return NativeMutationDraft(
        value={"attachment": attachment, "prepared": prepared},
        changed=(
            object_identity(attachment.graph.operation),
            object_identity(attachment.graph.body),
        ),
    )


def _vector(value: Any) -> dict[str, float]:
    return {
        "x": float(value.x),
        "y": float(value.y),
        "z": float(value.z),
    }


def verify_model_fastener_attachment(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    attachment = draft.value["attachment"]
    prepared = draft.value["prepared"]
    if not isinstance(attachment, ModelFastenerAttachment) or not isinstance(
        prepared,
        PreparedModelFastenerAttachment,
    ):
        raise NativeModelError("The fastener-attachment verification state is invalid.")
    graph = attachment.graph
    try:
        identity = validate_model_fastener_graph(
            document,
            graph,
            label=str(graph.body.Label),
            canonical_key=prepared.canonical_key,
        )
        current_host, current_names = graph.generator.BaseObject
        names = tuple(str(name) for name in list(current_names or []))
        if (
            current_host is not attachment.definition_host
            or names != attachment.definition_subelements
            or len(names) != 1
            or str(current_host.Shape.getElementIndexedName(names[0]))
            != attachment.requested_subelement
        ):
            raise NativeModelError("The exact fastener attachment changed before commit.")
        edge = circular_edge(
            attachment.requested_host,
            attachment.requested_subelement,
        )
    except NativeModelError:
        raise
    except (FastenerCatalogError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeModelError(str(exc)) from exc

    center = edge.Curve.Center
    base = graph.body.getGlobalPlacement().Base
    if not math.isclose(
        float(base.distanceToPoint(center)),
        0.0,
        rel_tol=0.0,
        abs_tol=1.0e-7,
    ):
        raise NativeModelError("The attached fastener axis origin missed the circular edge.")
    shape = graph.body.Shape
    if shape.isNull() or not shape.isValid() or len(shape.Solids) != 1:
        raise NativeModelError("The attached fastener did not retain one valid solid.")
    return {
        "operation": object_reference(graph.operation),
        "body": object_reference(graph.body),
        "canonical_key": str(identity["canonical_key"]),
        "attachment": {
            "host": object_reference(attachment.requested_host),
            "subelement": attachment.requested_subelement,
            "center_mm": _vector(center),
        },
    }
