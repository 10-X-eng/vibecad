# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact retained standard-fastener insertion for the Model ribbon."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADFastenerModel import (
    ModelFastenerGraph,
    create_model_fastener_graph,
    edit_model_fastener_graph,
    model_fastener_graph_from_body,
    validate_model_fastener_graph,
)
from VibeCADFasteners import (
    FastenerCatalogError,
    compatible_fastener_standards,
    resolve_fastener,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    resolve_object,
)


_REQUIRED_DEFINITION_FIELDS = frozenset(
    {
        "standard",
        "nominal_thread",
        "model_thread",
        "left_handed",
    }
)
_OPTIONAL_DEFINITION_FIELDS = frozenset({"length_mm"})
_OPTION_OVERRIDE_FIELD = "catalog_option_overrides"
_LEGACY_OPTION_FIELD = "options"
_OPTION_KINDS = {
    "body_width_code": "text",
    "pitch": "text",
    "thickness_code": "text",
    "slot_width": "text",
    "key_size": "text",
    "blind": "boolean",
    "external_diameter_mm": "length",
    "thread_length_mm": "length",
    "number_of_starts": "starts",
}


@dataclass(frozen=True, slots=True)
class PreparedModelFastener:
    constructor: Mapping[str, Any]
    identity: Mapping[str, Any]


def _text(value: Any, *, field: str) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > 128:
        raise NativeModelError(f"A standard-fastener {field} is invalid.")
    return clean


def _positive_length(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise NativeModelError(f"A standard-fastener {field} is invalid.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeModelError(f"A standard-fastener {field} is invalid.") from exc
    if not math.isfinite(result) or not 0.0 < result <= 1_000_000.0:
        raise NativeModelError(f"A standard-fastener {field} is invalid.")
    return result


def _options(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeModelError("Standard-fastener options must be an object.")
    supplied = dict(value)
    unknown = sorted(set(supplied) - set(_OPTION_KINDS))
    if unknown:
        raise NativeModelError("A standard-fastener option is unavailable.")
    result = {}
    for name, raw in supplied.items():
        kind = _OPTION_KINDS[name]
        if kind == "text":
            result[name] = _text(raw, field=name)
        elif kind == "boolean":
            if not isinstance(raw, bool):
                raise NativeModelError(f"A standard-fastener {name} is invalid.")
            result[name] = raw
        elif kind == "length":
            result[name] = _positive_length(raw, field=name)
        else:
            if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 4:
                raise NativeModelError(f"A standard-fastener {name} is invalid.")
            result[name] = raw
    return result


def prepare_model_fastener(value: Any) -> PreparedModelFastener:
    if not isinstance(value, Mapping):
        raise NativeModelError("A standard-fastener definition is invalid.")
    supplied_fields = set(value)
    extra_fields = supplied_fields - (
        _REQUIRED_DEFINITION_FIELDS | _OPTIONAL_DEFINITION_FIELDS
    )
    if not _REQUIRED_DEFINITION_FIELDS.issubset(supplied_fields) or extra_fields not in (
        set(),
        {_OPTION_OVERRIDE_FIELD},
        {_LEGACY_OPTION_FIELD},
    ):
        raise NativeModelError("A standard-fastener definition is invalid.")
    model_thread = value["model_thread"]
    left_handed = value["left_handed"]
    if not isinstance(model_thread, bool) or not isinstance(left_handed, bool):
        raise NativeModelError("Standard-fastener hand and thread controls are invalid.")
    raw_length = value.get("length_mm")
    length = (
        None
        if raw_length is None
        else _positive_length(raw_length, field="length_mm")
    )
    constructor = {
        "standard": _text(value["standard"], field="standard"),
        "nominal_thread": _text(
            value["nominal_thread"],
            field="nominal_thread",
        ),
        "length_mm": length,
        "model_thread": model_thread,
        "left_handed": left_handed,
        "options": _options(
            value.get(
                _OPTION_OVERRIDE_FIELD,
                value.get(_LEGACY_OPTION_FIELD, {}),
            )
        ),
    }
    try:
        identity = resolve_fastener(**constructor)
    except FastenerCatalogError as exc:
        raise NativeModelError(str(exc)) from exc
    return PreparedModelFastener(constructor, dict(identity))


def create_model_fastener(
    document: Any,
    *,
    label: str,
    prepared: PreparedModelFastener,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedModelFastener):
        raise TypeError("prepared must be a PreparedModelFastener")
    try:
        graph = create_model_fastener_graph(
            document,
            label=label,
            **dict(prepared.constructor),
        )
    except FastenerCatalogError as exc:
        raise NativeModelError(str(exc)) from exc
    if (
        str(graph.identity.get("canonical_key") or "")
        != str(prepared.identity.get("canonical_key") or "")
    ):
        raise NativeModelError("The standard-fastener catalog changed during insertion.")
    return NativeMutationDraft(
        value={"graph": graph, "label": label, "prepared": prepared},
        recompute_targets=(graph.operation, graph.body),
        created=(object_identity(graph.operation), object_identity(graph.body)),
    )


def preflight_model_fastener_edit(
    document: Any,
    value: Any,
    prepared: PreparedModelFastener,
) -> NativeObjectRef:
    """Prove an exact modern fastener target before opening a transaction."""

    if not isinstance(prepared, PreparedModelFastener):
        raise TypeError("prepared must be a PreparedModelFastener")
    if not isinstance(value, Mapping) or set(value) != {"object_name"}:
        raise NativeModelError("A standard-fastener edit target is invalid.")
    target = NativeObjectRef(
        str(getattr(document, "Uid", "") or ""),
        str(value["object_name"] or ""),
    )
    body = resolve_object(
        document,
        target,
        expected_types=("PartDesign::Body",),
    )
    try:
        graph = model_fastener_graph_from_body(document, body)
        validate_model_fastener_graph(
            document,
            graph,
            label=str(graph.body.Label),
            canonical_key=str(graph.identity["canonical_key"]),
        )
        requested_standard = str(prepared.identity["standard"])
        compatible = compatible_fastener_standards(graph.generator)
        if requested_standard not in compatible:
            raise NativeModelError(
                f"standard {requested_standard!r} cannot replace "
                f"{graph.identity['standard']!r} in place. "
                f"Compatible standards: {compatible}."
            )
    except NativeModelError:
        raise
    except (FastenerCatalogError, RuntimeError, ValueError) as exc:
        raise NativeModelError(str(exc)) from exc
    return target


def edit_model_fastener(
    document: Any,
    *,
    target: NativeObjectRef,
    label: str,
    prepared: PreparedModelFastener,
) -> NativeMutationDraft:
    """Edit one exact retained fastener and preserve all graph identities."""

    if not isinstance(target, NativeObjectRef):
        raise TypeError("target must be a NativeObjectRef")
    if not isinstance(prepared, PreparedModelFastener):
        raise TypeError("prepared must be a PreparedModelFastener")
    body = resolve_object(
        document,
        target,
        expected_types=("PartDesign::Body",),
    )
    try:
        graph = edit_model_fastener_graph(
            document,
            body=body,
            label=label,
            **dict(prepared.constructor),
        )
    except (FastenerCatalogError, RuntimeError, ValueError) as exc:
        raise NativeModelError(str(exc)) from exc
    if (
        str(graph.identity.get("canonical_key") or "")
        != str(prepared.identity.get("canonical_key") or "")
    ):
        raise NativeModelError("The standard-fastener catalog changed during editing.")
    return NativeMutationDraft(
        value={"graph": graph, "label": label, "prepared": prepared},
        changed=(object_identity(graph.operation), object_identity(graph.body)),
    )


def verify_model_fastener(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    graph = draft.value["graph"]
    prepared = draft.value["prepared"]
    if not isinstance(graph, ModelFastenerGraph) or not isinstance(
        prepared,
        PreparedModelFastener,
    ):
        raise NativeModelError("The standard-fastener verification state is invalid.")
    try:
        identity = validate_model_fastener_graph(
            document,
            graph,
            label=draft.value["label"],
            canonical_key=str(prepared.identity["canonical_key"]),
        )
    except (FastenerCatalogError, RuntimeError, ValueError) as exc:
        raise NativeModelError(str(exc)) from exc
    return {
        "operation": object_reference(graph.operation),
        "body": object_reference(graph.body),
        "fastener": {
            "canonical_key": str(identity["canonical_key"]),
            "part_number": str(identity["part_number"]),
            "standard": str(identity["standard"]),
            "nominal_thread": str(identity["nominal_size"]),
            "model_thread": bool(identity["model_thread"]),
            "left_handed": bool(identity["left_handed"]),
            "catalog_option_overrides": dict(identity["options"]),
        }
        | (
            {"length_mm": identity["length_mm"]}
            if identity["length_mm"] is not None
            else {}
        ),
        "solid_count": len(graph.body.Shape.Solids),
        "volume_mm3": float(graph.body.Shape.Volume),
    }
