# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compatibility import for the installed host Analysis runtime implementation."""

from __future__ import annotations

from tool_impl.analysis_runtime import (
    AnalysisRuntimeCancelled,
    AnalysisRuntimeError,
    AnalysisRuntimeManager,
    AnalysisRuntimeMessages,
    AnalysisRuntimeSnapshot,
    CleanupHandler,
    CommitHandler,
    CommitValidator,
    DEFAULT_MAX_PROGRESS_MESSAGE_CHARS,
    DEFAULT_MAX_RESULT_BYTES,
    DEFAULT_MAX_RUNTIME_JOBS,
    DiagnosticSink,
    DocumentThreadDispatcher,
    PrepareHandler,
    ProgressReporter,
    _AnalysisJob,
    _TERMINAL_PHASES,
)

__all__ = (
    "AnalysisRuntimeCancelled",
    "AnalysisRuntimeError",
    "AnalysisRuntimeManager",
    "AnalysisRuntimeMessages",
    "AnalysisRuntimeSnapshot",
    "CleanupHandler",
    "CommitHandler",
    "CommitValidator",
    "DEFAULT_MAX_PROGRESS_MESSAGE_CHARS",
    "DEFAULT_MAX_RESULT_BYTES",
    "DEFAULT_MAX_RUNTIME_JOBS",
    "DiagnosticSink",
    "DocumentThreadDispatcher",
    "PrepareHandler",
    "ProgressReporter",
    "_AnalysisJob",
    "_TERMINAL_PHASES",
)
