# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import tool_impl.analysis_fem_adapter as installed_adapter
import VibeCADNativeAnalyzeSolverExecutionAdapter as compatibility_adapter
import VibeCADNativeAnalyzeSolverExecutionRuntime as execution_runtime


def test_fem_execution_runtime_uses_installed_host_analysis_adapter() -> None:
    assert (
        execution_runtime.adopt_isolated_solver_execution
        is installed_adapter.adopt_isolated_solver_execution
    )
    assert (
        execution_runtime.commit_solver_execution
        is installed_adapter.commit_solver_execution
    )
    assert (
        execution_runtime.verify_solver_execution
        is installed_adapter.verify_solver_execution
    )


def test_root_fem_adapter_is_only_a_compatibility_reexport() -> None:
    assert (
        compatibility_adapter.prepare_solver_execution_request
        is installed_adapter.prepare_solver_execution_request
    )
    assert compatibility_adapter.run_solver_execution is installed_adapter.run_solver_execution
    assert (
        compatibility_adapter.adopt_isolated_solver_execution
        is installed_adapter.adopt_isolated_solver_execution
    )
    assert (
        compatibility_adapter.PreparedFEMSolverExecution
        is installed_adapter.PreparedFEMSolverExecution
    )
