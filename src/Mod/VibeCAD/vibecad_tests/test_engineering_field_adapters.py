# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADAnalysisContracts import AnalysisContractError
from VibeCADEngineeringFieldAdapters import (
    fields_from_openfoam_summary,
    fields_from_result_state,
    presentation_from_result_state,
)


def _flow_summary() -> dict:
    return {
        "pressure_unit": "Pa",
        "velocity_unit": "m/s",
        "pressure_range_pa": [101200.0, 101800.0],
        "velocity_magnitude_range_m_s": [0.0, 24.1],
        "maximum_velocity_m_s": 24.1,
        "turbulence_model": "kOmegaSST",
        "static_pressure_drop_pa": 142.0,
        "boundaries": [],
    }


def test_legacy_field_state_projects_known_semantics_without_live_arrays():
    fields = fields_from_result_state(
        {
            "fields": [
                {
                    "name": "vonMises",
                    "semantic": "von_mises_stress",
                    "components": 1,
                    "value_count": 4800,
                    "range": [12.4, 347.8],
                    "unit": "MPa",
                },
                {
                    "name": "DisplacementVectors",
                    "semantic": "displacement",
                    "components": 3,
                    "value_count": 4800,
                },
            ]
        }
    )

    assert fields[0].semantic == "stress.von_mises"
    assert fields[0].label == "Von Mises Stress"
    assert fields[0].unit == "MPa"
    assert (fields[0].minimum, fields[0].maximum) == (12.4, 347.8)
    assert fields[0].presentation == "scalar"
    assert fields[1].semantic == "displacement.vector"
    assert fields[1].presentation == "vector"
    assert fields[1].unit is None
    assert fields[1].minimum is fields[1].maximum is None
    assert "value_count" not in fields[0].to_dict()


def test_vtk_duplicate_names_keep_exact_association_identity():
    fields = fields_from_result_state(
        {
            "fields": [
                {
                    "name": "Temperature",
                    "association": "point",
                    "components": 1,
                    "value_count": 10,
                    "unit": "K",
                    "range": [293.15, 350.0],
                },
                {
                    "name": "Temperature",
                    "association": "cell",
                    "components": 1,
                    "value_count": 4,
                    "unit": "K",
                    "range": [294.0, 340.0],
                },
            ]
        }
    )

    assert tuple(field.field_id for field in fields) == (
        "point:Temperature", "cell:Temperature"
    )
    assert tuple(field.association for field in fields) == ("point", "cell")


def test_openfoam_summary_projects_only_exact_available_ranges():
    fields = fields_from_openfoam_summary(_flow_summary())

    assert tuple(field.field_id for field in fields) == (
        "Pressure", "Velocity", "TurbulentKineticEnergy"
    )
    assert fields[0].unit == "Pa"
    assert (fields[0].minimum, fields[0].maximum) == (101200.0, 101800.0)
    assert fields[1].components == 3
    assert fields[1].presentation == "vector"
    assert fields[2].unit == "m²/s²"
    assert fields[2].minimum is fields[2].maximum is None


def test_result_presentation_merges_vtk_and_flow_without_duplicate_semantics():
    presentation = presentation_from_result_state(
        {
            "label": "Duct CFD",
            "result_kind": "pipeline",
            "state_sha256": "a" * 64,
            "analysis_owners": ["Analysis"],
            "post_pipeline_owners": ["Pipeline"],
            "timeline_owner_chain": ["Pipeline", "Frame"],
            "point_count": 100,
            "cell_count": 80,
            "field_count": 2,
            "fields_truncated": False,
            "fields": [
                {
                    "name": "Pressure",
                    "association": "point",
                    "components": 1,
                    "value_count": 100,
                    "unit": "Pa",
                    "range": [101200.0, 101800.0],
                }
            ],
            "flow": _flow_summary(),
        }
    )

    assert presentation.title == "Duct CFD"
    assert tuple(field.semantic for field in presentation.fields).count("pressure") == 1
    assert {field.semantic for field in presentation.fields} == {
        "pressure", "velocity.magnitude", "turbulence.kinetic_energy"
    }
    assert {metric.metric_id for metric in presentation.metrics} == {
        "maximum_velocity", "pressure_drop"
    }
    extension = presentation.extension.to_value()
    assert extension["source_state_sha256"] == "a" * 64
    assert extension["analysis_owners"] == ["Analysis"]
    assert extension["post_pipeline_owners"] == ["Pipeline"]
    assert extension["timeline_owner_chain"] == ["Pipeline", "Frame"]
    assert extension["point_count"] == 100
    assert extension["cell_count"] == 80
    assert extension["large_arrays_copied"] is False
    assert extension["presentation_owner_unchanged"] is True


@pytest.mark.parametrize(
    "state,message",
    (
        ({"fields": "not-a-sequence"}, "sequence"),
        ({"fields": [{"name": "Pressure", "components": 0}]}, "component"),
        (
            {"fields": [{"name": "Pressure", "components": 1, "range": [2, 1]}]},
            "range",
        ),
        (
            {"fields": [{"name": "Pressure", "components": 1, "association": "face"}]},
            "association",
        ),
    ),
)
def test_malformed_result_metadata_is_refused(state, message):
    with pytest.raises(AnalysisContractError, match=message):
        fields_from_result_state(state)
