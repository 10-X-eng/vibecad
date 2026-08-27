# SPDX-License-Identifier: LGPL-2.1-or-later

"""Installed compatibility facade for durable Analysis metadata."""

from tool_impl.analysis_persistence import (
    ANALYSIS_METADATA_SCHEMA_VERSION,
    ALLOWED_TRANSITIONS,
    KNOWN_STATES,
    TERMINAL_STATES,
    AnalysisMetadataStore,
    AnalysisPersistenceError,
    AnalysisStoreBusy,
    new_job_record,
)

__all__ = (
    "ANALYSIS_METADATA_SCHEMA_VERSION",
    "ALLOWED_TRANSITIONS",
    "KNOWN_STATES",
    "TERMINAL_STATES",
    "AnalysisMetadataStore",
    "AnalysisPersistenceError",
    "AnalysisStoreBusy",
    "new_job_record",
)
