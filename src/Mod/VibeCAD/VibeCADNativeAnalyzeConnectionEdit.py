# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed in-place edits of exact paired FEM connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeAnalyzeConnectionCreate import (
    connection_label,
    prepare_connection_endpoints,
)
from VibeCADNativeAnalyzeConnectionState import connection_state
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
    require_boundary,
)
from VibeCADNativeAnalyzeState import analysis_state, is_live
from VibeCADNativeAnalyzeTargets import (
    PreparedConnectionTarget,
    PreparedGeometryReference,
    connection_target_still_exact,
    geometry_references_still_exact,
    prepare_connection_target,
    reference_value,
)
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedConnectionUpdate:
    boundary: AnalyzeCreationBoundary
    target: PreparedConnectionTarget
    analysis: Any
    analysis_state_sha256: str
    label: str
    endpoints: tuple[PreparedGeometryReference, PreparedGeometryReference]
    values: PreparedConnectionValues
    values_changed: bool


def _owner_analysis(document: Any, connection: Any) -> Any:
    owners = []
    for obj in tuple(document.Objects):
        try:
            if obj.isDerivedFrom("Fem::FemAnalysis") and connection in tuple(
                obj.Group or ()
            ):
                owners.append(obj)
        except Exception:
            continue
    if len(owners) != 1:
        raise NativeAnalyzeError("The FEM connection must belong to exactly one analysis.")
    return owners[0]


def _require_current_history(document: Any, connection: Any) -> None:
    timeline = getattr(document, "VibeCADTimeline", None)
    operations = tuple(getattr(timeline, "Operations", ()) or ())
    if (
        connection not in operations
        or str(getattr(connection, "VibeCADTimelineRole", "") or "") != "operation"
        or getattr(connection, "VibeCADTimelineOwner", None) is not None
    ):
        raise NativeAnalyzeError(
            "The FEM connection is not one durable root operation in current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INVALID",
        )
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(connection))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The FEM connection is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )


def _current_endpoints(connection: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    result = []
    for source, raw_names in tuple(getattr(connection, "References", ()) or ()):
        names = (raw_names,) if isinstance(raw_names, str) else tuple(raw_names or ())
        source_state = mesh_object_state(source)["state_sha256"]
        result.extend(
            {
                "object_name": str(source.Name),
                "expected_state_sha256": source_state,
                "subelement": str(name),
            }
            for name in names
        )
    if len(result) != 2:
        raise NativeAnalyzeError(
            "The exact FEM connection no longer has one slave and one master endpoint."
        )
    return result[0], result[1]


def prepare_connection_update(
    document: Any,
    document_uid: str,
    *,
    kind: str,
    target: Any,
    changes: Any,
) -> PreparedConnectionUpdate:
    prepared_target = prepare_connection_target(
        document,
        document_uid,
        target,
        expected_kind=kind,
    )
    connection = prepared_target.connection
    _require_current_history(document, connection)
    if not isinstance(changes, Mapping) or not changes:
        raise NativeAnalyzeError(
            "changes must be one non-empty FEM connection edit object."
        )
    allowed = {"label", "slave", "master", "connection"}
    if not set(changes) <= allowed:
        raise NativeAnalyzeError(
            f"changes accepts only {', '.join(sorted(allowed))}."
        )
    current_state = connection_state(connection)
    values_changed = "connection" in changes
    values = prepare_connection_values(
        kind,
        changes["connection"] if values_changed else current_state["definition"],
    )
    label = (
        connection_label(changes["label"], field="changes.label")
        if "label" in changes
        else str(connection.Label)
    )
    current_slave, current_master = _current_endpoints(connection)
    endpoints = prepare_connection_endpoints(
        document,
        document_uid,
        slave=changes.get("slave", current_slave),
        master=changes.get("master", current_master),
    )
    owner = _owner_analysis(document, connection)
    if (
        label == str(connection.Label)
        and references_match(connection, endpoints)
        and values.normalized() == current_state["definition"]
    ):
        raise NativeAnalyzeError(
            "The requested FEM connection edit would make no change.",
            error_code="NATIVE_ANALYZE_NO_CHANGE",
        )
    return PreparedConnectionUpdate(
        creation_boundary(document),
        prepared_target,
        owner,
        analysis_state(owner)["state_sha256"],
        label,
        endpoints,
        values,
        values_changed,
    )


def update_connection(
    document: Any,
    prepared: PreparedConnectionUpdate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedConnectionUpdate):
        raise TypeError("prepared must be a PreparedConnectionUpdate")
    require_boundary(document, prepared.boundary)
    if not connection_target_still_exact(prepared.target):
        raise NativeAnalyzeError(
            "The exact FEM connection changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if analysis_state(prepared.analysis)["state_sha256"] != prepared.analysis_state_sha256:
        raise NativeAnalyzeError(
            "The owning FEM analysis changed after connection edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    if not geometry_references_still_exact(prepared.endpoints):
        raise NativeAnalyzeError(
            "Connection endpoint geometry changed after edit preflight.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
        )
    connection = prepared.target.connection
    connection.Label = prepared.label
    if prepared.values_changed:
        apply_connection_values(connection, prepared.values)
    connection.References = reference_value(prepared.endpoints)
    return NativeMutationDraft(
        value={"connection": connection, "prepared": prepared},
        recompute_targets=(connection,),
        changed=(object_identity(connection),),
    )


def verify_connection_update(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    connection = draft.value["connection"]
    prepared = draft.value["prepared"]
    require_boundary(document, prepared.boundary)
    state = connection_state(connection)
    if (
        not is_live(document, connection)
        or str(connection.Label) != prepared.label
        or state["connection_kind"] != prepared.target.kind
        or state["definition"] != prepared.values.normalized()
        or not references_match(connection, prepared.endpoints)
        or connection not in tuple(prepared.analysis.Group or ())
        or analysis_state(prepared.analysis)["state_sha256"]
        != prepared.analysis_state_sha256
        or not geometry_references_still_exact(prepared.endpoints)
        or not bool(connection.isValid())
    ):
        raise NativeAnalyzeError("The FEM connection edit failed its exact postcondition.")
    return {"updated_connection": state}
