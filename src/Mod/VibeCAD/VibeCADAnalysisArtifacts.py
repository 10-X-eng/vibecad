# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compatibility import for installed host Analysis artifact primitives."""

from __future__ import annotations

from tool_impl.analysis_artifacts import (
    DEFAULT_MAXIMUM_BYTES,
    DEFAULT_MAXIMUM_FILES,
    FEM_COMPAT_DIGEST_ALGORITHM,
    STREAM_BLOCK_BYTES,
    AnalysisArtifactError,
    SealedDirectory,
    seal_directory,
)

__all__ = (
    "DEFAULT_MAXIMUM_BYTES",
    "DEFAULT_MAXIMUM_FILES",
    "FEM_COMPAT_DIGEST_ALGORITHM",
    "STREAM_BLOCK_BYTES",
    "AnalysisArtifactError",
    "SealedDirectory",
    "seal_directory",
)
