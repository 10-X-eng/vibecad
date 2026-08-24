# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from VibeCADNativeAnalyzeThermalResultBindings import _temperature_field
from VibeCADNativeAnalyzeThermalResultSchema import (
    ANALYZE_SHOW_TEMPERATURE,
    ANALYZE_TEMPERATURE_RESULTS,
    analyze_thermal_result_capability_definitions,
)


def test_temperature_results_are_two_focused_calls() -> None:
    definitions = {
        definition.name: definition
        for definition in analyze_thermal_result_capability_definitions()
    }

    read = definitions[ANALYZE_TEMPERATURE_RESULTS].variants[0]
    show = definitions[ANALYZE_SHOW_TEMPERATURE].variants[0]

    assert read.operation == "read"
    assert show.operation == "show"
    assert set(read.parameters["properties"]) == {"result_name"}
    assert set(show.parameters["properties"]) == {"result_name"}


def test_temperature_field_uses_exact_result_range_and_si_unit() -> None:
    field = _temperature_field(
        {
            "fields": [
                {
                    "name": "temperature",
                    "association": "point",
                    "components": 1,
                    "value_count": 4,
                    "range": [298.15, 423.15],
                    "unit": "K",
                }
            ]
        }
    )

    assert field == {
        "name": "temperature",
        "association": "point",
        "components": 1,
        "value_count": 4,
        "range": [298.15, 423.15],
        "unit": "K",
    }
