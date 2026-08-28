# SPDX-License-Identifier: LGPL-2.1-or-later

"""Installed compatibility facade for durable Analysis metadata."""

from pathlib import Path

from tool_impl.analysis_persistence import (
    ANALYSIS_METADATA_SCHEMA_VERSION,
    SUPPORTED_ANALYSIS_METADATA_MIGRATIONS,
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
from tool_impl.analysis_recovery import (
    AnalysisProviderRecoveryCoordinator,
    AnalysisProviderRecoveryError,
    AnalysisProviderRecoveryResult,
)


def analysis_user_data_root(data_root: str | Path | None = None) -> Path:
    """Return the governed per-user root for durable Analysis runtime data."""

    if data_root is None:
        from VibeCADProject import vibecad_data_dir

        base = vibecad_data_dir()
    else:
        base = Path(data_root).expanduser()
    return base / "analysis-runtime"


def open_user_analysis_metadata_store(
    *,
    data_root: str | Path | None = None,
    fault_injector=None,
    maximum_artifacts_per_analysis: int = DEFAULT_MAXIMUM_ARTIFACTS_PER_ANALYSIS,
    maximum_artifact_bytes_per_analysis: int = DEFAULT_MAXIMUM_ARTIFACT_BYTES_PER_ANALYSIS,
) -> AnalysisMetadataStore:
    """Open the single per-user Analysis metadata store without mutating it."""

    return AnalysisMetadataStore(
        analysis_user_data_root(data_root),
        fault_injector=fault_injector,
        maximum_artifacts_per_analysis=maximum_artifacts_per_analysis,
        maximum_artifact_bytes_per_analysis=maximum_artifact_bytes_per_analysis,
    )


def discover_user_analysis_records(
    *,
    data_root: str | Path | None = None,
    document_uid: str | None = None,
) -> tuple[dict, ...]:
    """Discover bounded current-schema records across the per-user store."""

    store = open_user_analysis_metadata_store(data_root=data_root)
    if document_uid is None:
        return store.list_records()
    return store.find_by_document_uid(document_uid)


def migrate_user_analysis_records(
    *, data_root: str | Path | None = None,
) -> tuple[dict, ...]:
    """Explicitly migrate supported records in the per-user Analysis store."""

    return open_user_analysis_metadata_store(data_root=data_root).migrate_records()


__all__ = (
    "ANALYSIS_METADATA_SCHEMA_VERSION",
    "SUPPORTED_ANALYSIS_METADATA_MIGRATIONS",
    "DEFAULT_MAXIMUM_ARTIFACTS_PER_ANALYSIS",
    "DEFAULT_MAXIMUM_ARTIFACT_BYTES_PER_ANALYSIS",
    "MAX_DISCOVERABLE_ANALYSES",
    "ALLOWED_TRANSITIONS",
    "KNOWN_STATES",
    "TERMINAL_STATES",
    "AnalysisMetadataStore",
    "AnalysisPersistenceError",
    "AnalysisProviderRecoveryCoordinator",
    "AnalysisProviderRecoveryError",
    "AnalysisProviderRecoveryResult",
    "AnalysisStoreBusy",
    "DurableRuntimeLifecycle",
    "analysis_user_data_root",
    "discover_user_analysis_records",
    "migrate_user_analysis_records",
    "new_job_record",
    "open_user_analysis_metadata_store",
    "restart_disposition_for_record",
)
