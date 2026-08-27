# SPDX-License-Identifier: LGPL-2.1-or-later

"""Durable explicit relationships between CAM setups."""

from __future__ import annotations

from typing import Any, Mapping


_GROUP = "Setup Relationship"
_KIND = "remaining_stock"
_LINKS = (
    (
        "PreviousSetup",
        "The earlier machining setup that produced this setup's stock",
    ),
    (
        "RemainingStockResult",
        "The retained material result adopted as this setup's stock",
    ),
    (
        "RemainingStockSolid",
        "The exact solid converted from the retained material result",
    ),
)
_STRINGS = (
    (
        "SetupRelationshipKind",
        "The explicit relationship between this setup and its predecessor",
    ),
    (
        "PreviousSetupStateSHA256",
        "Exact predecessor setup state adopted by this setup",
    ),
    (
        "RemainingStockResultStateSHA256",
        "Exact retained-result state adopted by this setup",
    ),
    (
        "MachiningProgramSHA256",
        "Exact machining program used to produce the retained result",
    ),
    (
        "ConversionArtifactSHA256",
        "Verified BREP artifact adopted as setup stock",
    ),
)


def _digest(value: Any, noun: str) -> str:
    result = str(value or "").strip()
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"{noun} must be a 64-character lowercase SHA-256 digest")
    return result


def _same_document(job: Any, value: Any, noun: str) -> Any:
    document = getattr(job, "Document", None)
    name = str(getattr(value, "Name", "") or "")
    if (
        document is None
        or value is None
        or getattr(value, "Document", None) is not document
        or not name
        or document.getObject(name) is not value
    ):
        raise ValueError(f"{noun} must be a live object in the setup document")
    return value


def _add_read_only(job: Any, type_id: str, name: str, description: str) -> None:
    if name not in tuple(getattr(job, "PropertiesList", ()) or ()):
        job.addProperty(type_id, name, _GROUP, description, attr=16)
    job.setEditorMode(name, 1)


def set_remaining_stock_relationship(
    job: Any,
    *,
    previous_setup: Any,
    remaining_stock_result: Any,
    remaining_stock_solid: Any,
    previous_setup_state_sha256: str,
    remaining_stock_result_state_sha256: str,
    machining_program_sha256: str,
    conversion_artifact_sha256: str,
) -> dict[str, Any]:
    """Attach one explicit predecessor/result/solid relationship to a new setup."""

    previous = _same_document(job, previous_setup, "previous_setup")
    result = _same_document(job, remaining_stock_result, "remaining_stock_result")
    solid = _same_document(job, remaining_stock_solid, "remaining_stock_solid")
    if previous is job:
        raise ValueError("A CAM setup cannot use itself as its previous setup")
    values = {
        "SetupRelationshipKind": _KIND,
        "PreviousSetupStateSHA256": _digest(
            previous_setup_state_sha256,
            "previous_setup_state_sha256",
        ),
        "RemainingStockResultStateSHA256": _digest(
            remaining_stock_result_state_sha256,
            "remaining_stock_result_state_sha256",
        ),
        "MachiningProgramSHA256": _digest(
            machining_program_sha256,
            "machining_program_sha256",
        ),
        "ConversionArtifactSHA256": _digest(
            conversion_artifact_sha256,
            "conversion_artifact_sha256",
        ),
    }
    for name, description in _LINKS:
        _add_read_only(job, "App::PropertyLink", name, description)
    for name, description in _STRINGS:
        _add_read_only(job, "App::PropertyString", name, description)
    job.PreviousSetup = previous
    job.RemainingStockResult = result
    job.RemainingStockSolid = solid
    for name, value in values.items():
        setattr(job, name, value)
    return remaining_stock_relationship(job) or {}


def remaining_stock_relationship(job: Any) -> dict[str, Any] | None:
    """Read the durable authored relationship without inferring one."""

    properties = set(str(name) for name in getattr(job, "PropertiesList", ()) or ())
    required = {name for name, _description in (*_LINKS, *_STRINGS)}
    if not required.issubset(properties):
        return None
    kind = str(getattr(job, "SetupRelationshipKind", "") or "")
    if kind != _KIND:
        return None
    previous = getattr(job, "PreviousSetup", None)
    result = getattr(job, "RemainingStockResult", None)
    solid = getattr(job, "RemainingStockSolid", None)
    return {
        "kind": kind,
        "previous_setup": previous,
        "remaining_stock_result": result,
        "remaining_stock_solid": solid,
        "previous_setup_state_sha256": str(job.PreviousSetupStateSHA256),
        "remaining_stock_result_state_sha256": str(
            job.RemainingStockResultStateSHA256
        ),
        "machining_program_sha256": str(job.MachiningProgramSHA256),
        "conversion_artifact_sha256": str(job.ConversionArtifactSHA256),
    }


def relationship_references(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    """Return concise durable object names for a relationship state."""

    if not isinstance(value, Mapping):
        return None
    result = {"kind": str(value.get("kind") or "")}
    for field in (
        "previous_setup",
        "remaining_stock_result",
        "remaining_stock_solid",
    ):
        obj = value.get(field)
        result[field] = str(getattr(obj, "Name", "") or "")
    return result
