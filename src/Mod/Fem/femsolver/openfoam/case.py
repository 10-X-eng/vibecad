# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Mapping


_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUPPORTED_CONDITIONS = frozenset(
    {
        "wall_no_slip",
        "wall_slip",
        "inlet_total_pressure",
        "inlet_velocity",
        "inlet_volumetric_flow",
        "inlet_mass_flow",
        "outlet_total_pressure",
        "outlet_static_pressure",
        "outlet_velocity",
        "outlet_outflow",
        "symmetry",
    }
)
_PRESSURE_CONDITIONS = frozenset(
    {
        "inlet_total_pressure",
        "outlet_total_pressure",
        "outlet_static_pressure",
        "outlet_velocity",
        "outlet_outflow",
    }
)


@dataclass(frozen=True, slots=True)
class SteadyIncompressibleCase:
    density_kg_m3: float
    kinematic_viscosity_m2_s: float
    max_iterations: int
    write_every_iterations: int
    pressure_tolerance: float
    velocity_tolerance: float
    initial_velocity_m_s: tuple[float, float, float]
    initial_pressure_pa: float
    patches: Mapping[str, Mapping[str, object]]


def _number(value: object, name: str, *, positive: bool = False) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'finite'}")
    return format(number, ".15g")


def _positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _header(location: str, name: str, field_class: str = "dictionary") -> str:
    return (
        "FoamFile\n"
        "{\n"
        "    format      ascii;\n"
        f"    class       {field_class};\n"
        f'    location    "{location}";\n'
        f"    object      {name};\n"
        "}\n\n"
    )


def _validate_patch(name: str, condition: Mapping[str, object]) -> str:
    if not _WORD.fullmatch(name):
        raise ValueError(f"Invalid OpenFOAM patch name {name!r}")
    kind = str(condition.get("kind") or "")
    if kind not in _SUPPORTED_CONDITIONS:
        raise ValueError(f"OpenFOAM does not support boundary condition {kind!r}")
    return kind


def _u_patch(kind: str, condition: Mapping[str, object], density: str) -> str:
    if kind == "wall_no_slip":
        return "        type            noSlip;\n"
    if kind == "wall_slip":
        return "        type            slip;\n"
    if kind == "symmetry":
        return "        type            symmetry;\n"
    if kind == "inlet_velocity":
        speed = _number(condition.get("velocity_m_s"), "velocity_m_s")
        return (
            "        type            surfaceNormalFixedValue;\n"
            f"        refValue        uniform -{speed};\n"
        )
    if kind == "outlet_velocity":
        speed = _number(condition.get("velocity_m_s"), "velocity_m_s")
        return (
            "        type            surfaceNormalFixedValue;\n"
            f"        refValue        uniform {speed};\n"
        )
    if kind == "inlet_volumetric_flow":
        rate = _number(condition.get("flow_m3_s"), "flow_m3_s")
        return (
            "        type            flowRateInletVelocity;\n"
            f"        volumetricFlowRate constant {rate};\n"
            "        value           uniform (0 0 0);\n"
        )
    if kind == "inlet_mass_flow":
        rate = _number(condition.get("flow_kg_s"), "flow_kg_s")
        return (
            "        type            flowRateInletVelocity;\n"
            f"        massFlowRate    constant {rate};\n"
            f"        rhoInlet        {density};\n"
            "        value           uniform (0 0 0);\n"
        )
    if kind in {"inlet_total_pressure", "outlet_total_pressure"}:
        return (
            "        type            pressureInletOutletVelocity;\n"
            "        value           uniform (0 0 0);\n"
        )
    return "        type            zeroGradient;\n"


def _p_patch(
    kind: str,
    condition: Mapping[str, object],
    density_value: float,
) -> str:
    if kind in {"wall_no_slip", "wall_slip"}:
        return "        type            zeroGradient;\n"
    if kind == "symmetry":
        return "        type            symmetry;\n"
    if kind in {"inlet_total_pressure", "outlet_total_pressure"}:
        pressure = float(condition.get("pressure_pa")) / density_value
        return (
            "        type            totalPressure;\n"
            f"        p0              uniform {_number(pressure, 'kinematic pressure')};\n"
            "        value           uniform 0;\n"
        )
    if kind == "outlet_static_pressure":
        pressure = float(condition.get("pressure_pa")) / density_value
        return (
            "        type            fixedValue;\n"
            f"        value           uniform {_number(pressure, 'kinematic pressure')};\n"
        )
    if kind in {"outlet_velocity", "outlet_outflow"}:
        return (
            "        type            fixedValue;\n        value           uniform 0;\n"
        )
    return "        type            zeroGradient;\n"


def _boundary_field(
    patches: Mapping[str, Mapping[str, object]],
    renderer,
) -> str:
    result = ["boundaryField\n{\n"]
    for name, condition in patches.items():
        result.extend(
            (f"    {name}\n", "    {\n", renderer(name, condition), "    }\n")
        )
    result.append("}\n")
    return "".join(result)


def build_case_files(case: SteadyIncompressibleCase) -> dict[str, str]:
    density = float(case.density_kg_m3)
    density_text = _number(density, "density_kg_m3", positive=True)
    viscosity = _number(
        case.kinematic_viscosity_m2_s,
        "kinematic_viscosity_m2_s",
        positive=True,
    )
    max_iterations = _positive_integer(case.max_iterations, "max_iterations")
    write_interval = _positive_integer(
        case.write_every_iterations,
        "write_every_iterations",
    )
    pressure_tolerance = _number(
        case.pressure_tolerance,
        "pressure_tolerance",
        positive=True,
    )
    velocity_tolerance = _number(
        case.velocity_tolerance,
        "velocity_tolerance",
        positive=True,
    )
    velocity = tuple(
        _number(value, f"initial_velocity_m_s[{index}]")
        for index, value in enumerate(case.initial_velocity_m_s)
    )
    if len(velocity) != 3:
        raise ValueError("initial_velocity_m_s must have three components")
    pressure = _number(
        float(case.initial_pressure_pa) / density,
        "initial kinematic pressure",
    )
    patches = dict(case.patches)
    if not patches:
        raise ValueError("OpenFOAM requires at least one boundary patch")
    kinds = {
        name: _validate_patch(name, condition) for name, condition in patches.items()
    }
    if not any(kind in _PRESSURE_CONDITIONS for kind in kinds.values()):
        raise ValueError(
            "OpenFOAM requires a pressure-defining inlet or outlet boundary"
        )

    u_boundary = _boundary_field(
        patches,
        lambda name, condition: _u_patch(kinds[name], condition, density_text),
    )
    p_boundary = _boundary_field(
        patches,
        lambda name, condition: _p_patch(kinds[name], condition, density),
    )
    return {
        "system/controlDict": _header("system", "controlDict")
        + "solver          incompressibleFluid;\n"
        + "startFrom       startTime;\nstartTime       0;\n"
        + f"stopAt          endTime;\nendTime         {max_iterations};\n"
        + "deltaT          1;\nwriteControl    timeStep;\n"
        + f"writeInterval   {write_interval};\n"
        + "purgeWrite      0;\nwriteFormat     ascii;\n"
        + "writePrecision  10;\nwriteCompression off;\n"
        + "timeFormat      general;\ntimePrecision   10;\n"
        + "runTimeModifiable false;\n",
        "system/fvSchemes": _header("system", "fvSchemes")
        + "ddtSchemes { default steadyState; }\n"
        + "gradSchemes { default Gauss linear; }\n"
        + "divSchemes\n{\n    default none;\n"
        + "    div(phi,U) bounded Gauss linearUpwind grad(U);\n"
        + "    div((nuEff*dev2(T(grad(U))))) Gauss linear;\n}\n"
        + "laplacianSchemes { default Gauss linear corrected; }\n"
        + "interpolationSchemes { default linear; }\n"
        + "snGradSchemes { default corrected; }\n",
        "system/fvSolution": _header("system", "fvSolution")
        + "solvers\n{\n"
        + "    p { solver GAMG; smoother GaussSeidel; "
        + f"tolerance {pressure_tolerance}; relTol 0.1; }}\n"
        + "    U { solver smoothSolver; smoother symGaussSeidel; "
        + f"tolerance {velocity_tolerance}; relTol 0.1; }}\n"
        + "}\nSIMPLE\n{\n    nNonOrthogonalCorrectors 0;\n"
        + "    consistent yes;\n    residualControl\n    {\n"
        + f"        p {pressure_tolerance};\n        U {velocity_tolerance};\n"
        + "    }\n}\nrelaxationFactors\n{\n    equations\n"
        + '    {\n        U 0.9;\n        ".*" 0.9;\n    }\n}\n',
        "constant/physicalProperties": _header("constant", "physicalProperties")
        + f"viscosityModel  constant;\nnu              {viscosity};\n",
        "constant/momentumTransport": _header("constant", "momentumTransport")
        + "simulationType  laminar;\n",
        "0/U": _header("0", "U", "volVectorField")
        + "dimensions      [0 1 -1 0 0 0 0];\n"
        + f"internalField   uniform ({' '.join(velocity)});\n"
        + u_boundary,
        "0/p": _header("0", "p", "volScalarField")
        + "dimensions      [0 2 -2 0 0 0 0];\n"
        + f"internalField   uniform {pressure};\n"
        + p_boundary,
    }
