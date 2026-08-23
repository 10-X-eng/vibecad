# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded FEM analysis, material, and catalog reads."""

from __future__ import annotations

from typing import Any

from VibeCADNativeAnalyzeMaterials import search_material_catalog
from VibeCADNativeAnalyzeElementState import element_definition_state
from VibeCADNativeAnalyzeConstraintState import electromagnetic_constraint_state
from VibeCADNativeAnalyzeFluidState import fluid_constraint_state
from VibeCADNativeAnalyzeGeometricalState import geometrical_feature_state
from VibeCADNativeAnalyzeSupportState import support_condition_state
from VibeCADNativeAnalyzeConnectionState import connection_state
from VibeCADNativeAnalyzeLoadState import load_state
from VibeCADNativeAnalyzeThermalState import thermal_condition_state
from VibeCADNativeAnalyzeMeshState import fem_mesh_definition_state
from VibeCADNativeAnalyzeMeshRefinementState import mesh_refinement_state
from VibeCADNativeAnalyzeMeshRefinementTarget import prepare_mesh_refinement_target
from VibeCADNativeAnalyzeSolverState import prepare_solver_target, solver_state
from VibeCADNativeAnalyzeEquationState import prepare_equation_target, equation_state
from VibeCADNativeAnalyzeResultState import prepare_result_target, result_state
from VibeCADNativeAnalyzeResults import result_purge_state
from VibeCADNativeAnalyzeStudyState import study_state
from VibeCADNativeAnalyzeMeshOutputState import (
    inspect_fem_mesh_elements as _inspect_fem_mesh_elements,
)
from VibeCADNativeAnalyzeState import analysis_state, material_state
from VibeCADNativeAnalyzeTargets import (
    prepare_analysis_target,
    prepare_electromagnetic_constraint_target,
    prepare_fluid_constraint_target,
    prepare_geometrical_feature_target,
    prepare_support_condition_target,
    prepare_connection_target,
    prepare_load_target,
    prepare_thermal_condition_target,
    prepare_fem_mesh_definition_target,
    prepare_element_definition_target,
    prepare_material_target,
)


def inspect_analysis(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_analysis_target(document, document_uid, target)
    return {
        "analysis": analysis_state(prepared.analysis),
        "result_graph": result_purge_state(prepared.analysis),
    }


def inspect_study(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_analysis_target(document, document_uid, target)
    return {"study": study_state(prepared.analysis)}


def inspect_material(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_material_target(document, document_uid, target)
    return {"material": material_state(prepared.material)}


def inspect_material_catalog(
    *,
    query: Any,
    category: Any,
    limit: Any,
) -> dict[str, Any]:
    return search_material_catalog(query, category, limit)


def inspect_element_definition(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_element_definition_target(document, document_uid, target)
    return {"element_definition": element_definition_state(prepared.element)}


def inspect_electromagnetic_constraint(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_electromagnetic_constraint_target(document, document_uid, target)
    return {
        "electromagnetic_constraint": electromagnetic_constraint_state(
            prepared.constraint
        )
    }


def inspect_fluid_constraint(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_fluid_constraint_target(document, document_uid, target)
    return {"fluid_constraint": fluid_constraint_state(prepared.constraint)}


def inspect_geometrical_feature(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_geometrical_feature_target(document, document_uid, target)
    return {"geometrical_feature": geometrical_feature_state(prepared.feature)}


def inspect_support_condition(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_support_condition_target(document, document_uid, target)
    return {"support_condition": support_condition_state(prepared.condition)}


def inspect_connection(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_connection_target(document, document_uid, target)
    return {"connection": connection_state(prepared.connection)}


def inspect_load(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_load_target(document, document_uid, target)
    return {"load": load_state(prepared.load)}


def inspect_thermal_condition(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_thermal_condition_target(document, document_uid, target)
    return {"thermal_condition": thermal_condition_state(prepared.condition)}


def inspect_fem_mesh_definition(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_fem_mesh_definition_target(document, document_uid, target)
    return {"fem_mesh_definition": fem_mesh_definition_state(prepared.mesh)}


def inspect_mesh_refinement(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_mesh_refinement_target(document, document_uid, target)
    return {"mesh_refinement": mesh_refinement_state(prepared.refinement)}


def inspect_fem_mesh_elements(
    document: Any,
    document_uid: str,
    **values: Any,
) -> dict[str, Any]:
    return _inspect_fem_mesh_elements(document, document_uid, **values)


def inspect_solver(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_solver_target(document, document_uid, target)
    return {"solver": solver_state(prepared.solver)}


def inspect_equation(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_equation_target(document, document_uid, target)
    return {"equation": equation_state(prepared.equation)}


def inspect_result(
    document: Any,
    document_uid: str,
    target: Any,
) -> dict[str, Any]:
    prepared = prepare_result_target(document, document_uid, target)
    return {"result": result_state(prepared.result)}
