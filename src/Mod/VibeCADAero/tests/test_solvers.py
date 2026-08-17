# SPDX-License-Identifier: LGPL-2.1-or-later

"""Physics contracts for the Voider aero stack port."""

from __future__ import annotations

import math

import pytest

import AeroConfig as config
import AeroSolvers as solvers


def test_atmosphere_constants():
    assert solvers.RHO == 1.225
    assert solvers.MU == 1.81e-5
    assert solvers.G == 9.80665


def test_biplane_reference_area():
    span_m, chord_m = 0.5, 0.09
    assert solvers.biplane_area(span_m, chord_m) == 2 * span_m * chord_m


def test_loaf_speed_formula():
    mass_kg = 0.1496
    area = 0.09
    cl = 0.8
    expected = math.sqrt(2 * mass_kg * solvers.G / (solvers.RHO * area * cl))
    assert solvers.loaf_speed(mass_kg, area, cl) == pytest.approx(expected)


def test_hover_is_labeled_momentum_theory():
    mass_kg = 0.1496
    result = solvers.hover_power(
        mass_kg,
        n_props=2,
        prop_diameter_m=0.178,
        figure_of_merit=0.55,
        thrust_to_weight=1.9,
    )
    weight = mass_kg * solvers.G
    thrust = 1.9 * weight
    disk = 2 * math.pi * (0.178 / 2.0) ** 2
    induced = math.sqrt(thrust / (2.0 * solvers.RHO * disk))
    assert result["source"] == "momentum-theory"
    assert result["P_hover"] == pytest.approx(thrust * induced / 0.55)
    assert result["T_W"] == 1.9
    assert "CFD" not in result["source"]


def test_cruise_power_uses_prop_eta_0_65():
    drag = 0.2
    speed = 8.0
    result = solvers.cruise_power(drag, speed, prop_eta=0.65)
    assert result["P_cruise"] == pytest.approx(drag * speed / 0.65)
    assert result["prop_eta"] == 0.65


def test_coefficient_source_priority_aerobuildup_then_vlm_then_neuralfoil():
    assert (
        solvers.pick_force_coefficients(
            aerobuildup={"CL": 0.9, "CD": 0.04, "CM": -0.02},
            vlm={"CL": 0.7, "CD": 0.05, "CM": -0.01},
            neuralfoil={"CL": 0.6, "CD": 0.06, "CM": 0.0},
        )["source"]
        == "AeroBuildup"
    )
    assert (
        solvers.pick_force_coefficients(
            aerobuildup=None,
            vlm={"CL": 0.7, "CD": 0.05, "CM": -0.01},
            neuralfoil={"CL": 0.6, "CD": 0.06, "CM": 0.0},
        )["source"]
        == "VLM"
    )
    assert (
        solvers.pick_force_coefficients(
            aerobuildup=None,
            vlm=None,
            neuralfoil={"CL": 0.6, "CD": 0.06, "CM": 0.0},
        )["source"]
        == "NeuralFoil"
    )


def test_two_point_vlm_derivatives_are_per_radian():
    alpha = 4.0
    cl1, cm1 = 0.70, -0.020
    cl2, cm2 = 0.80, -0.010
    derivatives = solvers.vlm_stability_derivatives(alpha, cl1, cm1, alpha + 2.0, cl2, cm2)
    d_rad = math.radians(2.0)
    assert derivatives["CLalpha"] == pytest.approx((0.80 - 0.70) / d_rad)
    assert derivatives["Cmalpha"] == pytest.approx((-0.010 - -0.020) / d_rad)


def test_pitch_unstable_when_cmalpha_positive():
    assert solvers.pitch_unstable(0.01) is True
    assert solvers.pitch_unstable(-0.01) is False
    assert solvers.pitch_unstable(None) is False


def test_analyze_section_uses_injected_neuralfoil():
    calls = {}

    def fake_nf(coords, alpha, Re, model_size="large"):
        calls["args"] = (coords, alpha, Re, model_size)
        return {
            "CL": 0.85,
            "CD": 0.025,
            "CM": -0.03,
            "analysis_confidence": 0.9,
        }

    cfg = config.resolve_geometry(None)
    coords = [[1.0, 0.0], [0.0, 0.07], [0.0, 0.0], [1.0, 0.0]]
    result = solvers.run_section(
        cfg,
        coords,
        neuralfoil_fn=fake_nf,
    )
    assert result["CL"] == 0.85
    assert result["source"] == "NeuralFoil"
    assert calls["args"][3] == "large"
    assert calls["args"][1] == 4.0
    assert result["V_loaf"] == pytest.approx(
        solvers.loaf_speed(0.1496, cfg["reference_area_m2"], 0.85)
    )
    assert result["Re"] == pytest.approx(
        solvers.RHO * result["V_loaf"] * cfg["chord_m"] / solvers.MU
    )


def test_analyze_writes_hover_and_cruise_from_section_coefficients():
    cfg = config.resolve_geometry(None)

    def fake_nf(coords, alpha, Re, model_size="large"):
        return {"CL": 0.8, "CD": 0.04, "CM": -0.02, "analysis_confidence": 0.8}

    result = solvers.analyze(
        cfg,
        coords=[[1.0, 0.0], [0.0, 0.07], [1.0, 0.0]],
        neuralfoil_fn=fake_nf,
        aerobuildup_fn=None,
        vlm_fn=None,
    )
    assert result["source"] == "NeuralFoil"
    assert result["hover"]["source"] == "momentum-theory"
    q = 0.5 * solvers.RHO * result["V_loaf"] ** 2
    drag = q * cfg["reference_area_m2"] * 0.04
    assert result["P_cruise"] == pytest.approx(drag * result["V_loaf"] / 0.65)
    assert result["PitchUnstable"] is False


def test_missing_solver_error_names_bundled_python_pip():
    with pytest.raises(solvers.AeroDependencyError) as exc:
        solvers.require_backend("neuralfoil", available=False)
    message = str(exc.value)
    assert "neuralfoil" in message
    assert "pip install" in message
    assert "python.exe" in message
