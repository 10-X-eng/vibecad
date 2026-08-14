# SPDX-License-Identifier: LGPL-2.1-or-later

"""Typed backend-specific values for Native FEM solver-control edits."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


@dataclass(frozen=True, slots=True)
class PreparedSolverChanges:
    kind: str
    normalized: Mapping[str, Any]
    native: Mapping[str, Any]


_CALCULIX_NAMES = {
    "analysis_type": "AnalysisType",
    "geometrical_nonlinearity": "GeometricalNonlinearity",
    "material_nonlinearity": "MaterialNonlinearity",
    "eigenmodes_count": "EigenmodesCount",
    "eigenmode_low_hz": "EigenmodeLowLimit",
    "eigenmode_high_hz": "EigenmodeHighLimit",
    "increments_maximum": "IncrementsMaximum",
    "buckling_factors": "BucklingFactors",
    "time_initial_s": "TimeInitialIncrement",
    "time_period_s": "TimePeriod",
    "time_minimum_s": "TimeMinimumIncrement",
    "time_maximum_s": "TimeMaximumIncrement",
    "thermo_mech_steady_state": "ThermoMechSteadyState",
    "use_iteration_control": "IterationsControlParameterTimeUse",
    "split_input_writer": "SplitInputWriter",
    "iteration_control_iterations": "IterationsControlParameterIter",
    "iteration_control_cutbacks": "IterationsControlParameterCutb",
    "iteration_control_field": "IterationsControlParameterField",
    "automatic_incrementation": "AutomaticIncrementation",
    "matrix_solver": "MatrixSolverType",
    "output_3d": "Output3d",
    "reduced_integration": "ReducedIntegration",
    "output_frequency": "OutputFrequency",
    "model_space": "ModelSpace",
    "thermo_mech_type": "ThermoMechType",
    "buckling_accuracy": "BucklingAccuracy",
    "exclude_bending_stiffness": "ExcludeBendingStiffness",
    "pastix_mixed_precision": "PastixMixedPrecision",
    "displace_mesh": "DisplaceMesh",
}
_ELMER_NAMES = {
    "coordinate_system": "CoordinateSystem",
    "bdf_order": "BDFOrder",
    "output_intervals": "OutputIntervals",
    "timestep_intervals": "TimestepIntervals",
    "timestep_sizes_s": "TimestepSizes",
    "simulation_type": "SimulationType",
    "steady_state_max_iterations": "SteadyStateMaxIterations",
    "steady_state_min_iterations": "SteadyStateMinIterations",
    "binary_output": "BinaryOutput",
    "save_geometry_index": "SaveGeometryIndex",
}
_Z88_NAMES = {
    "analysis_type": "AnalysisType",
    "displace_mesh": "DisplaceMesh",
    "solver_type": "SolverType",
    "model_space": "ModelSpace",
    "integration_order_quad": "IntegrationOrderQuad",
    "integration_order_hexa": "IntegrationOrderHexa",
    "integration_order_tria": "IntegrationOrderTria",
    "integration_order_tetra": "IntegrationOrderTetra",
    "relaxation_factor": "RelaxationFactor",
    "shift_factor": "ShiftFactor",
    "iteration_maximum": "IterationMaximum",
    "residual_limit": "ResidualLimit",
    "shell_flag": "ShellFlag",
    "matrix_maximum": "MatrixMaximum",
    "vector_maximum": "VectorMaximum",
}
NAMES_BY_KIND = {
    "calculix": _CALCULIX_NAMES,
    "elmer": _ELMER_NAMES,
    "z88": _Z88_NAMES,
}


def _error(field: str, expected: str) -> NativeAnalyzeError:
    return NativeAnalyzeError(f"changes.{field} must be {expected}.")


def _bool(field: str, value: Any) -> bool:
    if type(value) is not bool:
        raise _error(field, "true or false")
    return value


def _integer(field: str, value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _error(field, f"an integer from {minimum} through {maximum}")
    return value


def _number(
    field: str,
    value: Any,
    minimum: float,
    maximum: float,
    *,
    exclusive_minimum: bool = False,
) -> float:
    if type(value) not in {int, float}:
        raise _error(field, "a finite number")
    number = float(value)
    lower_ok = number > minimum if exclusive_minimum else number >= minimum
    if not math.isfinite(number) or not lower_ok or number > maximum:
        qualifier = "greater than" if exclusive_minimum else "at least"
        raise _error(field, f"a finite number {qualifier} {minimum} and at most {maximum}")
    return number


def _text(field: str, value: Any, allowed: tuple[str, ...]) -> str:
    text = str(value) if isinstance(value, str) else ""
    if text not in allowed:
        raise _error(field, "one of " + ", ".join(allowed))
    return text


def _bounded_text(field: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise _error(field, "text containing at most 256 characters")
    return value


def _integer_list(field: str, value: Any) -> list[int]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 128
        or any(type(item) is not int or not 1 <= item <= 1_000_000_000 for item in value)
    ):
        raise _error(field, "1 to 128 positive integer values")
    return list(value)


def _number_list(field: str, value: Any) -> list[float]:
    if not isinstance(value, list) or not 1 <= len(value) <= 128:
        raise _error(field, "1 to 128 positive finite numbers")
    return [
        _number(field, item, 0.0, 1.0e12, exclusive_minimum=True) for item in value
    ]


def _calculix(field: str, value: Any) -> Any:
    if field in {
        "geometrical_nonlinearity",
        "material_nonlinearity",
        "thermo_mech_steady_state",
        "use_iteration_control",
        "split_input_writer",
        "automatic_incrementation",
        "output_3d",
        "reduced_integration",
        "exclude_bending_stiffness",
        "pastix_mixed_precision",
        "displace_mesh",
    }:
        return _bool(field, value)
    if field == "analysis_type":
        return _text(
            field,
            value,
            (
                "static",
                "frequency",
                "thermomech",
                "check",
                "buckling",
                "electromagnetic",
            ),
        )
    if field == "matrix_solver":
        return _text(
            field,
            value,
            (
                "default",
                "pastix",
                "pardiso",
                "spooles",
                "iterativescaling",
                "iterativecholesky",
            ),
        )
    if field == "model_space":
        return _text(field, value, ("3D", "plane stress", "plane strain", "axisymmetric"))
    if field == "thermo_mech_type":
        return _text(field, value, ("coupled", "uncoupled", "pure heat transfer"))
    if field == "eigenmodes_count":
        return _integer(field, value, 1, 100)
    if field in {"increments_maximum"}:
        return _integer(field, value, 0, 1_000_000_000)
    if field in {"buckling_factors", "output_frequency"}:
        return _integer(field, value, 1, 1_000_000_000)
    if field in {"eigenmode_low_hz", "eigenmode_high_hz"}:
        return _number(field, value, 0.0, 1.0e12)
    if field in {"time_initial_s", "time_period_s", "time_minimum_s", "time_maximum_s"}:
        return _number(field, value, 0.0, 1.0e12, exclusive_minimum=True)
    if field == "buckling_accuracy":
        return _number(field, value, 0.0, 1.0, exclusive_minimum=True)
    return _bounded_text(field, value)


def _elmer(field: str, value: Any) -> Any:
    if field in {"binary_output", "save_geometry_index"}:
        return _bool(field, value)
    if field == "coordinate_system":
        return _text(
            field,
            value,
            (
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
        )
    if field == "simulation_type":
        return _text(field, value, ("Scanning", "Steady State", "Transient"))
    if field == "bdf_order":
        return _integer(field, value, 1, 5)
    if field in {"steady_state_max_iterations", "steady_state_min_iterations"}:
        return _integer(field, value, 0, 1_000_000_000)
    if field in {"output_intervals", "timestep_intervals"}:
        return _integer_list(field, value)
    return _number_list(field, value)


def _z88(field: str, value: Any) -> Any:
    if field == "displace_mesh":
        return _bool(field, value)
    if field == "analysis_type":
        return _text(field, value, ("static", "test"))
    if field == "solver_type":
        return _text(field, value, ("choly", "sorcg", "siccg"))
    if field == "model_space":
        return _text(field, value, ("3D", "plane stress", "axisymmetric", "plate"))
    orders = {
        "integration_order_quad": (2, 3, 4),
        "integration_order_hexa": (1, 2, 3, 4),
        "integration_order_tria": (1, 7, 13),
        "integration_order_tetra": (1, 4, 5),
    }
    if field in orders:
        number = _integer(field, value, min(orders[field]), max(orders[field]))
        if number not in orders[field]:
            raise _error(field, "one of " + ", ".join(str(item) for item in orders[field]))
        return number
    if field == "relaxation_factor":
        return _number(field, value, 0.0, 2.0)
    if field == "shift_factor":
        return _number(field, value, 0.0, 1.0)
    if field == "residual_limit":
        return _number(field, value, 0.0, 1.0e12, exclusive_minimum=True)
    if field == "shell_flag":
        return _integer(field, value, 1, 4)
    return _integer(field, value, 1, 1_000_000_000)


def _native_value(kind: str, field: str, value: Any) -> Any:
    if kind == "z88" and field.startswith("integration_order_"):
        return str(value)
    return value


def prepare_solver_changes(
    kind: str,
    value: Any,
    current_settings: Mapping[str, Any],
) -> PreparedSolverChanges:
    names = NAMES_BY_KIND.get(kind)
    if names is None:
        raise NativeAnalyzeError(
            "This solver backend has no Native document-setting editor.",
            error_code="NATIVE_ANALYZE_SOLVER_SETTINGS_UNAVAILABLE",
        )
    if not isinstance(value, dict) or not value:
        raise NativeAnalyzeError("changes must be one non-empty object.")
    unknown = sorted(set(value) - set(names))
    if unknown:
        raise NativeAnalyzeError(
            "Unsupported solver setting(s): "
            + ", ".join(unknown)
            + ". Accepted settings: "
            + ", ".join(names)
            + "."
        )
    converter = {"calculix": _calculix, "elmer": _elmer, "z88": _z88}[kind]
    normalized = {field: converter(field, item) for field, item in value.items()}
    native = {
        names[field]: _native_value(kind, field, item)
        for field, item in normalized.items()
    }
    prospective = dict(current_settings)
    prospective.update(native)
    if kind == "calculix":
        if float(prospective["EigenmodeLowLimit"]) > float(prospective["EigenmodeHighLimit"]):
            raise NativeAnalyzeError("eigenmode_low_hz must not exceed eigenmode_high_hz.")
        minimum = float(prospective["TimeMinimumIncrement"])
        initial = float(prospective["TimeInitialIncrement"])
        maximum = float(prospective["TimeMaximumIncrement"])
        period = float(prospective["TimePeriod"])
        if not minimum <= initial <= maximum <= period:
            raise NativeAnalyzeError(
                "CalculiX time values must satisfy minimum <= initial <= maximum <= period."
            )
    elif kind == "elmer":
        if int(prospective["SteadyStateMinIterations"]) > int(
            prospective["SteadyStateMaxIterations"]
        ):
            raise NativeAnalyzeError(
                "steady_state_min_iterations must not exceed steady_state_max_iterations."
            )
        if len(prospective["TimestepIntervals"]) != len(prospective["TimestepSizes"]):
            raise NativeAnalyzeError(
                "timestep_intervals and timestep_sizes_s must have equal lengths."
            )
    if all(current_settings.get(name) == item for name, item in native.items()):
        raise NativeAnalyzeError("The requested solver settings already have those values.")
    return PreparedSolverChanges(kind, normalized, native)
