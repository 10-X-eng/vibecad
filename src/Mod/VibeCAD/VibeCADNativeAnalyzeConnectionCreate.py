# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact creation of paired FEM contact and tie connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeConnectionState import connection_kind, connection_state
from VibeCADNativeAnalyzeConnectionValues import (
    PreparedConnectionValues,
    apply_connection_values,
    prepare_connection_values,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeGeometryCreate import references_match
from VibeCADNativeAnalyzeHistory import (
    AnalyzeCreationBoundary,
    creation_boundary,
    publish_operation,
    require_boundary,
    verify_operation_block,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedAnalysisTarget,
    PreparedGeometryReference,
    analysis_target_still_exact,
    geometry_references_still_exact,
    prepare_analysis_target,
    prepare_geometry_references,
    reference_value,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedConnectionCreate:
    boundary: AnalyzeCreationBoundary
    analysis: PreparedAnalysisTarget
    members_before: tuple[Any, ...]
    endpoints: tuple[PreparedGeometryReference, PreparedGeometryReference]
    kind: str
    label: str
    values: PreparedConnectionValues


def connection_label(value: Any, *, field: str = "label") -> str:
    label = str(value or "").strip()
    if not label or len(label) > 160:
        raise NativeAnalyzeError(f"{field} must contain 1 to 160 visible characters.")
    return label


def _endpoint_payload(value: Any, *, field: str) -> dict[str, Any]:
    required = {"object_name", "expected_state_sha256", "subelement"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise NativeAnalyzeError(
            f"{field} must contain only object_name, expected_state_sha256, and subelement."
        )
    return {
        "object_name": value["object_name"],
        "expected_state_sha256": value["expected_state_sha256"],
        "subelements": [value["subelement"]],
    }


def prepare_connection_endpoints(
    document: Any,
    document_uid: str,
    *,
    slave: Any,
    master: Any,
) -> tuple[PreparedGeometryReference, PreparedGeometryReference]:
    slave_refs = prepare_geometry_references(
        document,
        document_uid,
        [_endpoint_payload(slave, field="slave")],
        allowed_kinds=frozenset({"Edge", "Face"}),
    )
    master_refs = prepare_geometry_references(
        document,
        document_uid,
        [_endpoint_payload(master, field="master")],
        allowed_kinds=frozenset({"Edge", "Face"}),
    )
    slave_ref, master_ref = slave_refs[0], master_refs[0]
    if slave_ref.shape_kind != master_ref.shape_kind:
        raise NativeAnalyzeError(
            "Slave and master must both be faces, or both be edges for a 2D model."
        )
    if (
        slave_ref.source is master_ref.source
        and slave_ref.subelements == master_ref.subelements
    ):
        raise NativeAnalyzeError("Slave and master must be different subelements.")
    return slave_ref, master_ref


def prepare_connection_create(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    analysis: Any,
    label: Any,
    slave: Any,
    master: Any,
    connection: Any,
) -> PreparedConnectionCreate:
    target = prepare_analysis_target(document, document_uid, analysis)
    return PreparedConnectionCreate(
        creation_boundary(document),
        target,
        tuple(target.analysis.Group or ()),
        prepare_connection_endpoints(
            document,
            document_uid,
            slave=slave,
            master=master,
        ),
        kind,
        connection_label(label),
        prepare_connection_values(kind, connection),
    )


def _factory(document: Any, kind: str) -> Any:
    import ObjectsFem

    if kind == "contact":
        return ObjectsFem.makeConstraintContact(
            document,
            document.getUniqueObjectName("Contact"),
        )
    if kind == "tie":
        return ObjectsFem.makeConstraintTie(
            document,
            document.getUniqueObjectName("Tie"),
        )
    raise NativeAnalyzeError("The requested FEM connection kind is unavailable.")


def create_connection(
    document: Any,
    prepared: PreparedConnectionCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedConnectionCreate):
        raise TypeError("prepared must be a PreparedConnectionCreate")
    require_boundary(document, prepared.boundary)
    if not analysis_target_still_exact(prepared.analysis):
        raise NativeAnalyzeError(
            "The exact FEM analysis changed after connection preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.endpoints):
        raise NativeAnalyzeError(
            "Connection endpoint geometry changed after preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    connection = _factory(document, prepared.kind)
    if connection is None or connection_kind(connection) != prepared.kind:
        raise NativeAnalyzeError("The FEM connection factory returned the wrong type.")
    connection.Label = prepared.label
    if prepared.kind == "contact":
        connection.SurfaceBehavior = "Linear"
        connection.EnableThermalContact = False
        connection.ThermalContactConductance = []
    apply_connection_values(connection, prepared.values)
    connection.References = reference_value(prepared.endpoints)
    prepared.analysis.analysis.addObject(connection)
    if connection not in tuple(prepared.analysis.analysis.Group or ()):
        raise NativeAnalyzeError("The FEM connection was not added to its analysis.")
    publish_operation(document, prepared.boundary, connection)
    return NativeMutationDraft(
        value={"connection": connection, "prepared": prepared},
        recompute_targets=(connection, prepared.analysis.analysis),
        created=(object_identity(connection),),
        changed=(object_identity(prepared.analysis.analysis),),
    )


def verify_connection_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    connection = draft.value["connection"]
    prepared = draft.value["prepared"]
    analysis = prepared.analysis.analysis
    verify_operation_block(document, prepared.boundary, connection)
    state = connection_state(connection)
    if (
        not is_live(document, connection)
        or connection_kind(connection) != prepared.kind
        or str(connection.Label) != prepared.label
        or state["definition"] != prepared.values.normalized()
        or not references_match(connection, prepared.endpoints)
        or tuple(analysis.Group or ()) != (*prepared.members_before, connection)
        or not geometry_references_still_exact(prepared.endpoints)
        or not bool(connection.isValid())
    ):
        raise NativeAnalyzeError("The new FEM connection failed its exact postcondition.")
    owner_state = analysis_state(analysis)
    if owner_state["state_sha256"] == prepared.analysis.expected_state_sha256:
        raise NativeAnalyzeError("The FEM analysis did not record its new connection.")
    return {
        "analysis_target": {
            "object_name": owner_state["object_name"],
            "expected_state_sha256": owner_state["state_sha256"],
            "expected_member_count": owner_state["member_count"],
        },
        "created_connection": state,
    }
