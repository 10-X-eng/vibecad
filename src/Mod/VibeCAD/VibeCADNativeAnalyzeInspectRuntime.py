# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact bounded FEM reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeInspect import (
    inspect_analysis,
    inspect_electromagnetic_constraint,
    inspect_fluid_constraint,
    inspect_geometrical_feature,
    inspect_support_condition,
    inspect_connection,
    inspect_load,
    inspect_thermal_condition,
    inspect_fem_mesh_definition,
    inspect_mesh_refinement,
    inspect_fem_mesh_elements,
    inspect_solver,
    inspect_equation,
    inspect_result,
    inspect_element_definition,
    inspect_material,
    inspect_material_catalog,
)
from VibeCADNativeAnalyzePostSampling import linearized_stress_summary
from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeRuntimeContext import NativeRuntimeContext


_VARIANTS = {
    "analysis": frozenset({"target"}),
    "material": frozenset({"target"}),
    "material_catalog": frozenset({"query", "category", "limit"}),
    "element_definition": frozenset({"target"}),
    "electromagnetic_constraint": frozenset({"target"}),
    "fluid_constraint": frozenset({"target"}),
    "geometrical_feature": frozenset({"target"}),
    "support_condition": frozenset({"target"}),
    "connection": frozenset({"target"}),
    "load": frozenset({"target"}),
    "thermal_condition": frozenset({"target"}),
    "fem_mesh_definition": frozenset({"target"}),
    "mesh_refinement": frozenset({"target"}),
    "fem_mesh_elements": frozenset({"target", "element_kind", "offset", "page_size"}),
    "solver": frozenset({"target"}),
    "equation": frozenset({"target"}),
    "result": frozenset({"target"}),
    "linearized_stress": frozenset({"target"}),
}


class NativeAnalyzeInspectRuntime:
    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def inspect(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        operation, values = strict_variant_arguments(arguments, _VARIANTS)
        context = self._context
        context.guard()
        if operation == "analysis":
            result = inspect_analysis(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "material":
            result = inspect_material(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "material_catalog":
            result = inspect_material_catalog(**values)
        elif operation == "element_definition":
            result = inspect_element_definition(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "electromagnetic_constraint":
            result = inspect_electromagnetic_constraint(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "fluid_constraint":
            result = inspect_fluid_constraint(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "geometrical_feature":
            result = inspect_geometrical_feature(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "support_condition":
            result = inspect_support_condition(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "connection":
            result = inspect_connection(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "load":
            result = inspect_load(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "thermal_condition":
            result = inspect_thermal_condition(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "fem_mesh_definition":
            result = inspect_fem_mesh_definition(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "mesh_refinement":
            result = inspect_mesh_refinement(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "fem_mesh_elements":
            result = inspect_fem_mesh_elements(
                context.document,
                context.document_uid,
                **values,
            )
        elif operation == "solver":
            result = inspect_solver(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "equation":
            result = inspect_equation(
                context.document,
                context.document_uid,
                values["target"],
            )
        elif operation == "result":
            result = inspect_result(
                context.document,
                context.document_uid,
                values["target"],
            )
        else:
            result = linearized_stress_summary(
                context.document,
                context.document_uid,
                values["target"],
            )
        context.guard()
        return result
