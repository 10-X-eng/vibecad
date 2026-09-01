# SPDX-License-Identifier: LGPL-2.1-or-later

"""Minimal exact Manufacture state published to the provider."""

from __future__ import annotations

from typing import Any, Mapping


_REFERENCE_FIELDS = (
    "document_uid",
    "object_name",
    "type_id",
    "label",
    "state_sha256",
    "resource_name",
    "position",
    "active",
    "command_count",
    "toolpath_valid",
    "toolpath_issue",
    "tool_controller",
    "tool_number",
    "tool_length_offset",
    "spindle_speed_rpm",
    "spindle_direction",
    "horizontal_feed_mm_per_minute",
    "vertical_feed_mm_per_minute",
    "ramp_feed_mm_per_minute",
    "lead_in_feed_mm_per_minute",
    "lead_out_feed_mm_per_minute",
    "horizontal_rapid_mm_per_minute",
    "vertical_rapid_mm_per_minute",
    "shape_type",
    "readable",
    "issue",
)
_SETUP_FIELDS = (
    "object_name",
    "type_id",
    "label",
    "state_sha256",
    "counts",
    "models_truncated",
    "tools_truncated",
    "operations_truncated",
    "stock",
    "machine",
    "postprocessor",
    "configuration",
    "toolpath_validity",
    "readiness",
    "relationship",
)
_DOMAIN_FIELDS = (
    "kind",
    "job_count",
    "jobs_truncated",
    "active_job_resolution",
    "model_candidate_count",
    "model_candidates",
    "model_candidates_truncated",
    "job_creation",
    "remaining_stock_result_count",
    "remaining_stock_results_truncated",
    "active_simulation",
)


def _reference(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result = {name: value[name] for name in _REFERENCE_FIELDS if name in value}
    tool = value.get("tool")
    if isinstance(tool, Mapping):
        result["tool"] = _reference(tool)
    start_point = value.get("start_point")
    if isinstance(start_point, Mapping):
        result["start_point"] = dict(start_point)
    return result


def _setup(value: Any, *, focused: bool = False) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result = {name: value[name] for name in _SETUP_FIELDS if name in value}
    for name in ("models", "tools", "operations"):
        items = value.get(name)
        if isinstance(items, list):
            result[name] = [_reference(item) for item in items]
    if focused:
        ordered = value.get("ordered_operations")
        if isinstance(ordered, list):
            result["ordered_operations"] = [_reference(item) for item in ordered]
    return result


def compact_manufacture_provider_state(
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep actionable exact CAM facts and omit unrelated ribbon domains."""

    result = {
        str(name): value
        for name, value in state.items()
        if name != "domain"
    }
    domain = state.get("domain")
    if not isinstance(domain, Mapping):
        return result
    compact = {name: domain[name] for name in _DOMAIN_FIELDS if name in domain}
    jobs = domain.get("jobs")
    if isinstance(jobs, list):
        compact["jobs"] = [
            setup
            for value in jobs
            if (setup := _setup(value)) is not None
        ]
    active = _setup(domain.get("active_job"), focused=True)
    compact["active_job"] = active
    catalog = domain.get("tool_catalog")
    if isinstance(catalog, Mapping):
        compact["tool_catalog"] = {
            name: catalog[name]
            for name in ("state_sha256", "count")
            if name in catalog
        }
    retained = domain.get("remaining_stock_results")
    if isinstance(retained, list):
        compact["remaining_stock_results"] = [
            {
                name: value[name]
                for name in (
                    "object_name",
                    "label",
                    "type_id",
                    "state_sha256",
                    "source_setup",
                    "source_current",
                    "provenance_valid",
                    "resolution_mm",
                    "program_sha256",
                )
                if name in value
            }
            for value in retained
            if isinstance(value, Mapping)
        ]
    result["domain"] = compact
    return result
