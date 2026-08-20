# SPDX-License-Identifier: LGPL-2.1-or-later

from VibeCADNativeAnalyzeAnalysis import stamp_created_fem_graph


def test_created_fem_graph_is_not_solved() -> None:
    stamped = stamp_created_fem_graph({"created_analysis": {"name": "Analysis"}})
    assert stamped["claim_ceiling"] == "not_solved"
    assert stamped["solved"] is False
    assert stamped["claim_ceiling"] != "model_unqualified"
