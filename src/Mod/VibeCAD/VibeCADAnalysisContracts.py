# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compatibility import for installed host Analysis contracts."""

from __future__ import annotations

from tool_impl.analysis_contracts import (
    ANALYSIS_SCHEMA_VERSION,
    AnalysisCommand,
    AnalysisContractError,
    CanonicalJson,
    CurrentnessReport,
    DependencyRecord,
    DependencySnapshot,
    ExecutionSpec,
    PreparedAnalysis,
    PreparedInputManifest,
    environment_sha256,
    json_sha256,
)

__all__ = (
    "ANALYSIS_SCHEMA_VERSION",
    "AnalysisCommand",
    "AnalysisContractError",
    "CanonicalJson",
    "CurrentnessReport",
    "DependencyRecord",
    "DependencySnapshot",
    "ExecutionSpec",
    "PreparedAnalysis",
    "PreparedInputManifest",
    "environment_sha256",
    "json_sha256",
)
