# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-guarded reads for catalogs used by Native Model tools."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeDesignHoleCatalog import read_hole_catalog
from VibeCADFasteners import FastenerCatalogError, search_catalog
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeRuntimeContext import NativeRuntimeContext


_FASTENER_FIELDS = frozenset(
    {"query", "family", "standard", "nominal_thread", "length_mm", "limit"}
)


def _optional_text(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    if not clean or len(clean) > 128:
        raise NativeModelError(f"A fastener catalog {field} filter is invalid.")
    return clean


def _concise_fastener_search(result: Mapping[str, Any]) -> dict[str, Any]:
    requested = dict(result.get("requested") or {})
    exact_standard = bool(requested.get("standard"))
    exact_thread = bool(requested.get("nominal_thread"))
    rows = []
    for raw in list(result.get("results") or []):
        row = dict(raw)
        concise = {
            name: row[name]
            for name in (
                "standard",
                "family",
                "description",
                "requires_length",
                "supports_model_thread",
                "supports_left_handed",
                "option_names",
                "available_options",
                "requested_match",
                "request_error",
                "nearest_valid_lengths_mm",
                "nominal_thread",
                "default_options",
                "valid_lengths_mm",
                "arbitrary_positive_length",
                "constructor",
                "canonical_key",
            )
            if name in row
        }
        constructor = concise.get("constructor")
        if isinstance(constructor, Mapping):
            provider_constructor = dict(constructor)
            legacy_options = provider_constructor.pop("options", {})
            if "catalog_option_overrides" not in provider_constructor:
                provider_constructor["catalog_option_overrides"] = dict(
                    legacy_options or {}
                )
            if provider_constructor.get("length_mm") is None:
                provider_constructor.pop("length_mm", None)
            concise["constructor"] = provider_constructor
        if exact_standard and not exact_thread:
            concise["nominal_threads"] = list(row.get("nominal_threads") or [])
        rows.append(concise)
    return {
        "catalog": str(result.get("catalog") or ""),
        "catalog_version": str(result.get("catalog_version") or ""),
        "generator_revision": str(result.get("generator_revision") or ""),
        "model_thread_limits": dict(result.get("model_thread_limits") or {}),
        "requested": requested,
        "total_matches": int(result.get("total_matches") or 0),
        "returned": int(result.get("returned") or 0),
        "truncated": bool(result.get("truncated")),
        "results": rows,
    }


class NativeModelCatalogRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def read_catalog(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise NativeArgumentError("Native capability arguments must be an object.")
        values = dict(arguments)
        operation = str(values.pop("operation", "") or "").strip()
        allowed = (
            frozenset({"standard"})
            if operation == "hole_threads"
            else _FASTENER_FIELDS if operation == "fasteners" else None
        )
        if allowed is None:
            raise NativeArgumentError("Native capability operation is unavailable.")
        if not set(values).issubset(allowed):
            raise NativeArgumentError(
                "Native capability arguments do not match the selected operation."
            )
        self._context.guard()
        if operation == "hole_threads":
            standard = values.get("standard")
            return read_hole_catalog(
                None if standard is None else str(standard)
            )
        query = str(values.get("query") or "")
        if len(query) > 256:
            raise NativeModelError(
                "A fastener catalog query must contain at most 256 characters."
            )
        limit = values.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 25:
            raise NativeModelError("A fastener catalog limit must be from 1 through 25.")
        length = values.get("length_mm")
        if length is not None:
            if isinstance(length, bool):
                raise NativeModelError("A fastener catalog length is invalid.")
            length = float(length)
            if not 0.0 < length <= 1_000_000.0:
                raise NativeModelError("A fastener catalog length is invalid.")
        try:
            result = search_catalog(
                query,
                family=_optional_text(values.get("family"), field="family"),
                standard=_optional_text(values.get("standard"), field="standard"),
                nominal_thread=_optional_text(
                    values.get("nominal_thread"),
                    field="nominal_thread",
                ),
                length_mm=length,
                limit=limit,
            )
        except FastenerCatalogError as exc:
            raise NativeModelError(str(exc)) from exc
        return _concise_fastener_search(result)
