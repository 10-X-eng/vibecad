# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

import pytest

import AeroDetachedAnalysis as detached


def _config() -> dict[str, object]:
    return {
        "airfoil": "naca:0012",
        "mass_kg": 1.2,
        "reference_area_m2": 0.4,
        "chord_m": 0.2,
        "span_m": 1.0,
        "gap_m": 0.1,
        "alpha_deg": 4.0,
        "xyz_ref": [0.05, 0.0, 0.05],
    }


def test_prepared_analysis_is_serializable_and_detached_from_live_inputs() -> None:
    config = _config()
    coordinates = [[0.0, 0.0], [0.5, 0.08], [1.0, 0.0]]

    prepared = detached.PreparedAeroAnalysis.create(
        operation="analyze",
        config=config,
        coordinates=coordinates,
        airfoil_source="naca:0012",
    )

    config["alpha_deg"] = 99.0
    coordinates[1][1] = 99.0

    assert prepared.operation == "analyze"
    assert prepared.run_section_solve is True
    assert prepared.run_vlm_solve is True
    assert prepared.config()["alpha_deg"] == 4.0
    assert prepared.coordinates() == [[0.0, 0.0], [0.5, 0.08], [1.0, 0.0]]
    assert json.loads(prepared.config_json)["airfoil"] == "naca:0012"


@pytest.mark.parametrize(
    ("operation", "section", "vlm"),
    (
        ("analyze", True, True),
        ("section", True, False),
        ("vlm", False, True),
    ),
)
def test_operation_maps_to_exact_legacy_solver_flags(
    operation: str,
    section: bool,
    vlm: bool,
) -> None:
    prepared = detached.PreparedAeroAnalysis.create(
        operation=operation,
        config=_config(),
        coordinates=[[0.0, 0.0], [1.0, 0.0]],
        airfoil_source="naca:0012",
    )

    assert prepared.run_section_solve is section
    assert prepared.run_vlm_solve is vlm


def test_execute_uses_only_detached_values_and_preserves_solver_payload() -> None:
    prepared = detached.PreparedAeroAnalysis.create(
        operation="section",
        config=_config(),
        coordinates=[[0.0, 0.0], [1.0, 0.0]],
        airfoil_source="naca:0012",
    )
    captured: dict[str, object] = {}

    def solve(config, *, coords, run_section_solve, run_vlm_solve):
        captured.update(
            config=config,
            coords=coords,
            run_section_solve=run_section_solve,
            run_vlm_solve=run_vlm_solve,
        )
        return {"CL": 0.75, "CD": 0.04, "source": "test-solver"}

    completed = detached.execute(prepared, solver=solve)

    assert captured["config"] == prepared.config()
    assert captured["coords"] == prepared.coordinates()
    assert captured["run_section_solve"] is True
    assert captured["run_vlm_solve"] is False
    assert completed.prepared is prepared
    assert completed.payload() == {
        "CD": 0.04,
        "CL": 0.75,
        "airfoil_source": "naca:0012",
        "source": "test-solver",
    }


def test_detached_contract_rejects_non_json_state_and_unknown_operations() -> None:
    config = _config()
    config["live_document"] = object()
    with pytest.raises(detached.AeroDetachedContractError):
        detached.PreparedAeroAnalysis.create(
            operation="analyze",
            config=config,
            coordinates=[[0.0, 0.0], [1.0, 0.0]],
            airfoil_source="naca:0012",
        )

    with pytest.raises(detached.AeroDetachedContractError):
        detached.PreparedAeroAnalysis.create(
            operation="unknown",
            config=_config(),
            coordinates=[[0.0, 0.0], [1.0, 0.0]],
            airfoil_source="naca:0012",
        )
