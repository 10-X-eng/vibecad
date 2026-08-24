# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeFluidValues import prepare_fluid_values


def test_fluid_boundary_values_preserve_physical_units_and_modes():
    prepared = prepare_fluid_values(
        "fluid_boundary",
        {
            "condition": {"kind": "inlet_velocity", "velocity_m_s": 12.5},
            "turbulence": {
                "kind": "intensity_length_scale",
                "intensity_ratio": 0.05,
                "length_scale_m": 0.02,
            },
            "thermal": {"kind": "fixed_temperature", "temperature_k": 300.0},
        },
    )

    assert prepared.native == {
        "BoundaryType": "inlet",
        "Subtype": "uniformVelocity",
        "BoundaryValue": 12.5,
        "TurbulenceSpecification": "intensity&LengthScale",
        "TurbulentIntensityValue": 0.05,
        "TurbulentLengthValue": 0.02,
        "ThermalBoundaryType": "fixedValue",
        "TemperatureValue": 300.0,
        "HeatFluxValue": 0.0,
        "HTCoeffValue": 0.0,
    }
    assert prepared.normalized() == {
        "condition": {"kind": "inlet_velocity", "velocity_m_s": 12.5},
        "turbulence": {
            "kind": "intensity_length_scale",
            "intensity_ratio": 0.05,
            "length_scale_m": 0.02,
        },
        "thermal": {"kind": "fixed_temperature", "temperature_k": 300.0},
    }
    assert prepared.allowed_reference_kinds == frozenset({"Face"})
    assert not prepared.allow_empty_references


@pytest.mark.parametrize(
    ("constraint", "native_condition"),
    (
        (
            {
                "condition": {"kind": "wall_no_slip"},
                "turbulence": {"kind": "none"},
                "thermal": {"kind": "adiabatic"},
            },
            ("wall", "fixed", 0.0),
        ),
        (
            {
                "condition": {
                    "kind": "outlet_static_pressure",
                    "pressure_pa": 101325.0,
                },
                "turbulence": {"kind": "none"},
                "thermal": {"kind": "adiabatic"},
            },
            ("outlet", "staticPressure", 101325.0),
        ),
        (
            {
                "condition": {"kind": "symmetry"},
                "turbulence": {"kind": "none"},
                "thermal": {"kind": "adiabatic"},
            },
            ("interface", "symmetry", 0.0),
        ),
    ),
)
def test_fluid_boundary_core_conditions_are_exact(constraint, native_condition):
    prepared = prepare_fluid_values("fluid_boundary", constraint)
    assert (
        prepared.native["BoundaryType"],
        prepared.native["Subtype"],
        prepared.native["BoundaryValue"],
    ) == native_condition


def test_fluid_boundary_rejects_unitless_or_out_of_range_values():
    with pytest.raises(NativeAnalyzeError, match="slip_ratio"):
        prepare_fluid_values(
            "fluid_boundary",
            {
                "condition": {"kind": "wall_partial_slip", "slip_ratio": 1.5},
                "turbulence": {"kind": "none"},
                "thermal": {"kind": "adiabatic"},
            },
        )

    with pytest.raises(NativeAnalyzeError, match="temperature_k"):
        prepare_fluid_values(
            "fluid_boundary",
            {
                "condition": {"kind": "wall_no_slip"},
                "turbulence": {"kind": "none"},
                "thermal": {"kind": "fixed_temperature", "temperature_k": -1.0},
            },
        )
