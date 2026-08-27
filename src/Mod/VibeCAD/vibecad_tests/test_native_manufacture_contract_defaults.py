# SPDX-License-Identifier: LGPL-2.1-or-later

"""Natural first-page and no-template defaults for Native CAM contracts."""

from __future__ import annotations

from VibeCADNativeManufactureInspectSchema import (
    manufacture_inspect_capability_definition,
)
from VibeCADNativeManufactureJobSchema import manufacture_job_capability_definition
from VibeCADNativeManufactureToolSchema import (
    manufacture_tool_catalog_capability_definition,
    manufacture_tool_capability_definition,
)


def _branch(definition, operation: str) -> dict:
    return definition.provider_schema((operation,))["parameters"]["oneOf"][0]


def test_create_job_does_not_require_an_explicit_no_template_sentinel() -> None:
    branch = _branch(manufacture_job_capability_definition(), "create_job")

    assert "template" in branch["properties"]
    assert "template" not in branch["required"]


def test_catalog_and_setup_reads_default_to_the_first_bounded_page() -> None:
    catalog = _branch(
        manufacture_tool_catalog_capability_definition(),
        "list_tools",
    )
    setups = _branch(manufacture_inspect_capability_definition(), "list_setups")
    job = _branch(manufacture_inspect_capability_definition(), "read_job")

    assert set(catalog["required"]) == {"expected_catalog_state_sha256"}
    assert catalog["properties"]["offset"]["default"] == 0
    assert catalog["properties"]["page_size"]["default"] == 32
    assert setups["required"] == []
    assert setups["properties"]["query"]["default"] == ""
    assert setups["properties"]["offset"]["default"] == 0
    assert setups["properties"]["page_size"]["default"] == 32
    assert set(job["required"]) == {"target"}
    assert job["properties"]["operation_offset"]["default"] == 0
    assert job["properties"]["page_size"]["default"] == 32


def test_adding_a_catalog_tool_only_requires_the_two_exact_owners() -> None:
    branch = _branch(
        manufacture_tool_capability_definition(),
        "create_controller",
    )

    assert set(branch["required"]) == {"job_target", "catalog_tool"}
    assert branch["properties"]["tool_property_changes"]["default"] == []


def test_model_geometry_read_is_exact_paged_and_drilling_aware() -> None:
    definition = manufacture_inspect_capability_definition()
    branch = _branch(definition, "read_model_geometry")

    assert set(branch["required"]) == {"target", "elements"}
    assert branch["properties"]["elements"]["enum"] == [
        "faces",
        "edges",
        "drillable",
    ]
    assert branch["properties"]["offset"]["default"] == 0
    assert branch["properties"]["page_size"]["default"] == 32
    assert next(
        variant
        for variant in definition.variants
        if variant.operation == "read_model_geometry"
    ).background_required is True
