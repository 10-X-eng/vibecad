# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import AeroConfig
import AeroFlightCard
import AeroMass


def test_flight_card_computes_loading_and_refuses_airworthiness() -> None:
    cfg = AeroConfig.finalize(dict(AeroConfig.VOIDER_DEFAULTS))
    mass = {
        "used_mass_kg": 0.15,
        "used_mass_source": "declared_auw",
        "declared_auw_g": 150.0,
    }
    results = {
        "CL": 1.2,
        "CD": 0.2,
        "CLalpha": 5.0,
        "Cmalpha": -0.4,
        "V_loaf": 6.0,
        "P_hover": 20.0,
        "P_cruise": 4.0,
        "source": "VLM",
        "hover": {"source": "momentum-theory"},
    }
    card = AeroFlightCard.build_card(cfg, results, mass)
    assert card["claim_ceiling"] == "not_airworthy"
    assert card["not_airworthy"] is True
    assert card["hover_source"] == "momentum-theory"
    assert card["wing_loading_n_m2"] > 0
    assert card["disk_loading_n_m2"] > 0
    assert card["hover_margin_tw"] > 0
    assert card["pitch_stable"] is True
    assert card["endurance_hover_min"] is None
    assert card["endurance_state"] == "evidence_waiting"


def test_endurance_uses_declared_battery_but_stays_unqualified() -> None:
    cfg = AeroConfig.finalize(dict(AeroConfig.VOIDER_DEFAULTS))
    cfg["battery_wh"] = 10.0
    mass = {"used_mass_kg": 0.15, "used_mass_source": "declared_auw"}
    results = {
        "CL": 1.0,
        "P_hover": 30.0,
        "source": "NeuralFoil",
        "hover": {"source": "momentum-theory"},
        "Cmalpha": -0.2,
        "CLalpha": 5.0,
    }
    card = AeroFlightCard.build_card(cfg, results, mass)
    assert card["endurance_hover_min"] > 0
    assert card["endurance_state"] == "model_unqualified"
    assert card["claim_ceiling"] == "not_airworthy"


def test_mass_without_shapes_stays_declared() -> None:
    class _Doc:
        Objects = []

        def getObject(self, _name):
            return None

    cfg = AeroConfig.finalize(dict(AeroConfig.VOIDER_DEFAULTS))
    measured = AeroMass.measure_document(_Doc(), cfg)
    assert measured["used_mass_source"] == "declared_auw"
    assert measured["claim_ceiling"] == "mass_declared"
    assert measured["evidence_state"] == "evidence_waiting"


def test_partial_cad_mass_is_reported_but_not_used_as_total() -> None:
    class _Shape:
        Volume = 1000.0
        CenterOfMass = type("Center", (), {"x": 5.0, "y": 0.0, "z": 0.0})()

    part = type(
        "Part",
        (),
        {"Name": "lower_wing", "Label": "lower_wing", "Shape": _Shape()},
    )()

    class _Doc:
        Objects = [part]

        def getObject(self, name):
            return part if name == "lower_wing" else None

    cfg = AeroConfig.finalize(dict(AeroConfig.VOIDER_DEFAULTS))
    measured = AeroMass.measure_document(_Doc(), cfg)

    assert measured["cad_mass_kg"] is not None
    assert measured["cad_mass_complete"] is False
    assert measured["used_mass_kg"] == measured["declared_mass_kg"]
    assert measured["used_mass_source"] == "declared_auw"
    assert measured["claim_ceiling"] == "mass_declared"


def test_explicitly_complete_cad_mass_can_be_used_as_total() -> None:
    class _Shape:
        Volume = 1000.0
        CenterOfMass = type("Center", (), {"x": 5.0, "y": 0.0, "z": 0.0})()

    part = type(
        "Part",
        (),
        {"Name": "lower_wing", "Label": "lower_wing", "Shape": _Shape()},
    )()

    class _Doc:
        Objects = [part]

        def getObject(self, name):
            return part if name == "lower_wing" else None

    cfg = AeroConfig.finalize(
        {**AeroConfig.VOIDER_DEFAULTS, "cad_mass_complete": True}
    )
    measured = AeroMass.measure_document(_Doc(), cfg)

    assert measured["cad_mass_complete"] is True
    assert measured["used_mass_kg"] == measured["cad_mass_kg"]
    assert measured["used_mass_source"] == "cad_volume"
    assert measured["claim_ceiling"] == "mass_from_cad"
