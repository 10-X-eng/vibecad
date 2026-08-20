# SPDX-License-Identifier: LGPL-2.1-or-later

from VibeCADNativeAnalyzeAnalysis import stamp_created_fem_graph
from VibeCADNativeAnalyzeSolverExecution import stamp_solver_execution_unqualified


def test_fem_solve_is_model_unqualified() -> None:
    stamped = stamp_solver_execution_unqualified({"result": {"object_name": "CCX_Results"}})
    assert stamped["claim_ceiling"] == "model_unqualified"
    assert stamped["solved"] is True
    assert stamped["qualified"] is False
    created = stamp_created_fem_graph({"created_analysis": {"name": "Analysis"}})
    assert created["claim_ceiling"] == "not_solved"
    assert created["claim_ceiling"] != stamped["claim_ceiling"]
