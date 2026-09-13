# SPDX-License-Identifier: LGPL-2.1-or-later

from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeManufactureSimulationControlSchema import (
    MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME,
    manufacture_simulation_control_capability_definition,
)


def test_cam_simulation_close_is_one_exact_task_control() -> None:
    definition = manufacture_simulation_control_capability_definition()

    assert definition.name == MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME
    assert tuple(variant.operation for variant in definition.variants) == ("close",)
    variant = definition.variants[0]
    assert variant.action_ids == frozenset({"CAMSimulationClose"})
    assert variant.transaction_behavior == "presentation"
    schema = provider_visible_native_schema(
        definition.provider_schema(("close",))
    )
    parameters = schema["parameters"]["oneOf"][0]
    assert parameters["required"] == ["simulation_id"]
    assert set(parameters["properties"]) == {"simulation_id"}
    assert parameters["additionalProperties"] is False
