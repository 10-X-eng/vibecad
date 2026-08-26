# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compatibility import for the installed FEM Analysis adapter."""

from __future__ import annotations

from tool_impl.analysis_fem_adapter import (
    CompletedFEMSolverExecution,
    FEM_ANALYSIS_ADAPTER_ID,
    FEM_ANALYSIS_ADAPTER_VERSION,
    PreparedFEMSolverExecution,
    adopt_isolated_solver_execution,
    commit_solver_execution,
    discard_solver_execution_request,
    prepare_solver_execution_request,
    run_solver_execution,
    verify_solver_execution,
)

__all__ = (
    "CompletedFEMSolverExecution",
    "FEM_ANALYSIS_ADAPTER_ID",
    "FEM_ANALYSIS_ADAPTER_VERSION",
    "PreparedFEMSolverExecution",
    "adopt_isolated_solver_execution",
    "commit_solver_execution",
    "discard_solver_execution_request",
    "prepare_solver_execution_request",
    "run_solver_execution",
    "verify_solver_execution",
)
