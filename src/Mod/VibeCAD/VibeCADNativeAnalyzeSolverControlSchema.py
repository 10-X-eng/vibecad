# SPDX-License-Identifier: LGPL-2.1-or-later

"""Backend-specific Native contract for FEM solver-control settings."""

from __future__ import annotations

from VibeCADNativeAnalyzeSolverSchema import SOLVER_TARGET
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME = "analyze.solver_control"
_BOOL = {"type": "boolean"}
_POSITIVE_INTEGER = {"type": "integer", "minimum": 1, "maximum": 1_000_000_000}
_NONNEGATIVE_INTEGER = {"type": "integer", "minimum": 0, "maximum": 1_000_000_000}
_POSITIVE_NUMBER = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e12}
_NONNEGATIVE_NUMBER = {"type": "number", "minimum": 0.0, "maximum": 1.0e12}
_CONTROL_TEXT = {"type": "string", "maxLength": 256}


def _enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


def _integer_list() -> dict:
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": 128,
        "items": _POSITIVE_INTEGER,
    }


def _number_list() -> dict:
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": 128,
        "items": _POSITIVE_NUMBER,
    }


_CALCULIX_CHANGES = {
    "analysis_type": _enum(
        "static",
        "frequency",
        "thermomech",
        "check",
        "buckling",
        "electromagnetic",
    ),
    "geometrical_nonlinearity": _BOOL,
    "material_nonlinearity": _BOOL,
    "eigenmodes_count": {"type": "integer", "minimum": 1, "maximum": 100},
    "eigenmode_low_hz": _NONNEGATIVE_NUMBER,
    "eigenmode_high_hz": _NONNEGATIVE_NUMBER,
    "increments_maximum": _NONNEGATIVE_INTEGER,
    "buckling_factors": _POSITIVE_INTEGER,
    "time_initial_s": _POSITIVE_NUMBER,
    "time_period_s": _POSITIVE_NUMBER,
    "time_minimum_s": _POSITIVE_NUMBER,
    "time_maximum_s": _POSITIVE_NUMBER,
    "thermo_mech_steady_state": _BOOL,
    "use_iteration_control": _BOOL,
    "split_input_writer": _BOOL,
    "iteration_control_iterations": _CONTROL_TEXT,
    "iteration_control_cutbacks": _CONTROL_TEXT,
    "iteration_control_field": _CONTROL_TEXT,
    "automatic_incrementation": _BOOL,
    "matrix_solver": _enum(
        "default",
        "pastix",
        "pardiso",
        "spooles",
        "iterativescaling",
        "iterativecholesky",
    ),
    "output_3d": _BOOL,
    "reduced_integration": _BOOL,
    "output_frequency": _POSITIVE_INTEGER,
    "model_space": _enum("3D", "plane stress", "plane strain", "axisymmetric"),
    "thermo_mech_type": _enum("coupled", "uncoupled", "pure heat transfer"),
    "buckling_accuracy": {
        "type": "number",
        "exclusiveMinimum": 0.0,
        "maximum": 1.0,
    },
    "exclude_bending_stiffness": _BOOL,
    "pastix_mixed_precision": _BOOL,
    "displace_mesh": _BOOL,
}
_ELMER_CHANGES = {
    "coordinate_system": _enum(
        "Cartesian",
        "Cartesian 1D",
        "Cartesian 2D",
        "Cartesian 3D",
        "Polar 2D",
        "Polar 3D",
        "Cylindric",
        "Cylindric Symmetric",
        "Axi Symmetric",
    ),
    "bdf_order": {"type": "integer", "minimum": 1, "maximum": 5},
    "output_intervals": _integer_list(),
    "timestep_intervals": _integer_list(),
    "timestep_sizes_s": _number_list(),
    "simulation_type": _enum("Scanning", "Steady State", "Transient"),
    "steady_state_max_iterations": _NONNEGATIVE_INTEGER,
    "steady_state_min_iterations": _NONNEGATIVE_INTEGER,
    "binary_output": _BOOL,
    "save_geometry_index": _BOOL,
}
_OPENFOAM_CHANGES = {
    "max_iterations": _POSITIVE_INTEGER,
    "write_every_iterations": _POSITIVE_INTEGER,
    "pressure_tolerance": {
        "type": "number",
        "minimum": 1.0e-15,
        "maximum": 1.0,
    },
    "velocity_tolerance": {
        "type": "number",
        "minimum": 1.0e-15,
        "maximum": 1.0,
    },
}
_Z88_CHANGES = {
    "analysis_type": _enum("static", "test"),
    "displace_mesh": _BOOL,
    "solver_type": _enum("choly", "sorcg", "siccg"),
    "model_space": _enum("3D", "plane stress", "axisymmetric", "plate"),
    "integration_order_quad": {"type": "integer", "enum": [2, 3, 4]},
    "integration_order_hexa": {"type": "integer", "enum": [1, 2, 3, 4]},
    "integration_order_tria": {"type": "integer", "enum": [1, 7, 13]},
    "integration_order_tetra": {"type": "integer", "enum": [1, 4, 5]},
    "relaxation_factor": {"type": "number", "minimum": 0.0, "maximum": 2.0},
    "shift_factor": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "iteration_maximum": _POSITIVE_INTEGER,
    "residual_limit": _POSITIVE_NUMBER,
    "shell_flag": {"type": "integer", "minimum": 1, "maximum": 4},
    "matrix_maximum": _POSITIVE_INTEGER,
    "vector_maximum": _POSITIVE_INTEGER,
}
SOLVER_CONTROL_FIELDS_BY_BACKEND = {
    "calculix": _CALCULIX_CHANGES,
    "elmer": _ELMER_CHANGES,
    "openfoam": _OPENFOAM_CHANGES,
    "z88": _Z88_CHANGES,
}


def _parameters(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": {"target": SOLVER_TARGET, **properties},
        "required": ["target"],
        "minProperties": 2,
        "additionalProperties": False,
    }


def _variant(
    operation: str,
    backend: str,
    context_action_id: str,
    properties: dict,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=(
            f"Change only explicitly supplied {backend} document settings on one exact "
            "solver; this does not run the solver."
        ),
        action_ids=frozenset({"FEM_SolverControl", context_action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type="ExactFemSolverSettingsAndHistory",
        transaction_behavior="document",
        background_required=False,
        parameters=_parameters(properties),
    )


def analyze_solver_control_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_SOLVER_CONTROL_CAPABILITY_NAME,
        description=(
            "Edit the real document-level settings shown by each supported FEM solver "
            "task panel, without launching a backend process."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "update_calculix",
                "CalculiX",
                "VibeCAD_AnalyzeUpdateCalculiXSolver",
                _CALCULIX_CHANGES,
            ),
            _variant(
                "update_elmer",
                "Elmer",
                "VibeCAD_AnalyzeUpdateElmerSolver",
                _ELMER_CHANGES,
            ),
            _variant(
                "update_openfoam",
                "OpenFOAM",
                "VibeCAD_AnalyzeUpdateOpenFOAMSolver",
                _OPENFOAM_CHANGES,
            ),
            _variant(
                "update_z88",
                "Z88",
                "VibeCAD_AnalyzeUpdateZ88Solver",
                _Z88_CHANGES,
            ),
        ),
    )


def register_analyze_solver_control_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_solver_control_capability_definition())
