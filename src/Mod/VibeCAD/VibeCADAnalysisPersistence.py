# SPDX-License-Identifier: LGPL-2.1-or-later

"""Installed compatibility facade for durable Analysis metadata."""

from tool_impl.analysis_persistence import (
    ANALYSIS_METADATA_SCHEMA_VERSION,
    DEFAULT_MAXIMUM_ARTIFACTS_PER_ANALYSIS,
    DEFAULT_MAXIMUM_ARTIFACT_BYTES_PER_ANALYSIS,
    MAX_DISCOVERABLE_ANALYSES,
    ALLOWED_TRANSITIONS,
    KNOWN_STATES,
    TERMINAL_STATES,
    AnalysisMetadataStore,
    AnalysisPersistenceError,
    AnalysisStoreBusy,
    DurableRuntimeLifecycle,
    new_job_record,
    restart_disposition_for_record,
)

__all__ = (
    "ANALYSIS_METADATA_SCHEMA_VERSION",
    "DEFAULT_MAXIMUM_ARTIFACTS_PER_ANALYSIS",
    "DEFAULT_MAXIMUM_ARTIFACT_BYTES_PER_ANALYSIS",
    "MAX_DISCOVERABLE_ANALYSES",
    "ALLOWED_TRANSITIONS",
    "KNOWN_STATES",
    "TERMINAL_STATES",
    "AnalysisMetadataStore",
    "AnalysisPersistenceError",
    "AnalysisStoreBusy",
    "DurableRuntimeLifecycle",
    "new_job_record",
    "restart_disposition_for_record",
)
