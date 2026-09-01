# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression tests for independent FEM study/resource graphs."""

from __future__ import annotations

from types import SimpleNamespace

from VibeCADCore import VibeCADService
from vibescript_fem_api import FEMDomainAPI


EXPORTS = (
    "analysis",
    "solver",
    "material",
    "constraint",
    "load_case",
    "mesh",
    "solve",
)
OUTPUT_TYPES = (
    "analysis",
    "solver",
    "material",
    "constraint",
    "load_case",
    "mesh",
    "result",
)


def _study(api: FEMDomainAPI, document_uid: str, suffix: str):
    reference = {"document_uid": document_uid, "object_name": "SourceSolid"}
    solver = api.solver(
        reduced_integration=suffix == "A",
        label=f"Solver {suffix}",
    )
    material = api.material(
        name=f"Steel {suffix}",
        youngs_modulus_mpa=210000,
        poisson_ratio=0.3,
        density_kg_m3=7850,
        label=f"Material {suffix}",
    )
    constraint = api.constraint(
        "fixed",
        reference,
        {"type": "subelement", "name": "Face1"},
        label=f"Fixed {suffix}",
    )
    load_case = api.load_case([constraint], label=f"Load case {suffix}")
    mesh = api.mesh(
        reference,
        method="inline",
        nodes=[
            [0, 0, 0],
            [1, 0, 0],
            [1, 1, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [1, 1, 1],
            [0, 1, 1],
        ],
        elements=[[0, 1, 2, 3, 4, 5, 6, 7]],
        element_type="hexa8",
        label=f"Mesh {suffix}",
    )
    analysis = api.analysis(
        solver,
        [material],
        [load_case],
        mesh,
        label=f"Study {suffix}",
    )
    result = api.solve(analysis, execution="validate_only", label=f"Result {suffix}")
    return {
        "solver": solver,
        "material": material,
        "constraint": constraint,
        "load_case": load_case,
        "mesh": mesh,
        "analysis": analysis,
        "result": result,
    }


def test_fem_api_can_describe_three_disjoint_study_graphs() -> None:
    api = FEMDomainAPI(EXPORTS, OUTPUT_TYPES)
    first = _study(api, "doc-128", "A")
    second = _study(api, "doc-128", "B")
    third = _study(api, "doc-128", "C")

    assert first["analysis"] != second["analysis"]
    assert second["analysis"] != third["analysis"]
    assert first["result"] != second["result"]
    assert second["result"] != third["result"]
    assert first["analysis"].arguments[0] is first["solver"]
    assert second["analysis"].arguments[0] is second["solver"]
    assert first["analysis"].arguments[3] is first["mesh"]
    assert second["analysis"].arguments[3] is second["mesh"]
    assert first["result"].arguments[0] is first["analysis"]
    assert second["result"].arguments[0] is second["analysis"]
    assert first["analysis"].arguments[0] is not second["analysis"].arguments[0]
    assert first["analysis"].arguments[3] is not second["analysis"].arguments[3]
    assert third["analysis"].arguments[0] is third["solver"]
    assert third["analysis"].arguments[3] is third["mesh"]
    assert third["result"].arguments[0] is third["analysis"]


def test_fem_targeting_requires_exact_internal_name_and_never_defaults() -> None:
    first = SimpleNamespace(Name="Analysis", Label="Shared label")
    second = SimpleNamespace(Name="Analysis001", Label="Shared label")
    service = object.__new__(VibeCADService)
    service._fem_analyses = lambda: [first, second]

    assert service._get_fem_analysis("Analysis") is first
    assert service._get_fem_analysis("Shared label") is None
    assert service._get_fem_analysis(None) is None
    assert service._get_fem_analysis("") is None


def test_fem_analysis_catalog_is_paged_and_keeps_exact_selection() -> None:
    from tool_impl.service import domain_runtime

    first = SimpleNamespace(Name="Analysis", Label="Shared label")
    second = SimpleNamespace(Name="Analysis001", Label="Shared label")
    service = object.__new__(VibeCADService)
    service._fem_analyses = lambda: [first, second]
    service._get_fem_analysis = lambda name=None: (
        second if name == "Analysis001" else None
    )
    service._fem_analysis_summary = lambda analysis: {
        "name": analysis.Name,
        "label": analysis.Label,
    }

    page = domain_runtime.fem_summary(
        service,
        analysis_name="Analysis001",
        offset=1,
        limit=1,
    )

    assert page["analysis_count"] == 2
    assert page["returned_count"] == 1
    assert page["analyses"] == [{"name": "Analysis001", "label": "Shared label"}]
    assert page["selected_analysis"] == {
        "name": "Analysis001",
        "label": "Shared label",
    }
