# SPDX-License-Identifier: LGPL-2.1-or-later

"""Stable comparison helpers for installed-host FEM publication evidence."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping


SUPPORTED_SOLVERS = ("calculix", "elmer", "z88", "mystran")
PUBLICATION_PARITY_DIMENSIONS = (
    "result object graph",
    "solver result membership",
    "canonical History order",
    "timeline ownership",
    "input and state hashes",
    "publication receipt presence",
    "public JSON",
    "save and reopen persistence",
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _replace_identity(value: Any, aliases: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            key = str(key)
            if key in {"object_id", "solver_id", "root_id"}:
                continue
            if key == "document_uid":
                normalized[key] = "document-uid"
                continue
            if key == "state_sha256":
                digest = str(item or "")
                if not _SHA256.fullmatch(digest):
                    raise ValueError("state_sha256 must be one lowercase SHA-256 digest")
                normalized[key] = "state-sha256"
                continue
            normalized[key] = _replace_identity(item, aliases)
        return normalized
    if isinstance(value, list):
        return [_replace_identity(item, aliases) for item in value]
    if isinstance(value, tuple):
        return [_replace_identity(item, aliases) for item in value]
    if isinstance(value, str):
        return aliases.get(value, value)
    return value


def normalize_publication_evidence(
    evidence: Mapping[str, Any], *, solver_key: str
) -> dict[str, Any]:
    """Remove only per-document object names and integer IDs from A/B evidence."""

    if solver_key not in SUPPORTED_SOLVERS:
        raise ValueError(f"Unsupported FEM solver key: {solver_key}")
    value = deepcopy(dict(evidence))
    solver_name = str(value.get("solver", "") or "")
    root_name = str(value.get("root", "") or "")
    resources = tuple(value.get("resources", ()) or ())
    aliases = {solver_name: "solver", root_name: "root"}
    for index, resource in enumerate(resources):
        if isinstance(resource, Mapping):
            name = str(resource.get("object_name", "") or "")
        else:
            name = str(resource or "")
        if name:
            aliases[name] = f"resource:{index}"
    normalized = _replace_identity(value, aliases)
    normalized.pop("root_id", None)
    normalized.pop("solver_id", None)
    normalized["solver"] = solver_key
    normalized["root"] = "root"
    return normalized


def assert_publication_parity(
    legacy: Mapping[str, Any], host: Mapping[str, Any], *, solver_key: str
) -> dict[str, Any]:
    """Return normalized evidence or fail with the exact divergent payloads."""

    normalized_legacy = normalize_publication_evidence(legacy, solver_key=solver_key)
    normalized_host = normalize_publication_evidence(host, solver_key=solver_key)
    if normalized_legacy != normalized_host:
        raise AssertionError(
            f"Installed FEM publication parity failed for {solver_key}: "
            f"legacy={normalized_legacy!r} host={normalized_host!r}"
        )
    return normalized_host
