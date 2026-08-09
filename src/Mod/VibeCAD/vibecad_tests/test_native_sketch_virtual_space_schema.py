# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from jsonschema import Draft202012Validator

from VibeCADNativeSketchConstraintSchema import (
    sketch_constraint_capability_definition,
)


def _base(target: dict) -> dict:
    return {
        "operation": "set_virtual_space",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 4,
        "expected_constraint_count": 2,
        "expected_external_geometry_count": 0,
        "target": target,
    }


def _validator() -> Draft202012Validator:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "set_virtual_space"
    )
    return Draft202012Validator(variant.provider_parameters())


def test_variant_maps_the_dual_host_action_as_a_mutation() -> None:
    definition = sketch_constraint_capability_definition()
    variant = next(
        item for item in definition.variants if item.operation == "set_virtual_space"
    )

    assert definition.primary_classification == "mutation"
    assert variant.action_ids == frozenset({"Sketcher_SwitchVirtualSpace"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert (
        variant.exact_target_type
        == "ActiveSketchExactVirtualSpaceViewOrConstraints"
    )
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False


def test_schema_accepts_exact_view_and_constraint_targets() -> None:
    validator = _validator()
    view = _base(
        {
            "kind": "view",
            "expected_shown_virtual_space": False,
            "shown_virtual_space": True,
        }
    )
    constraints = _base(
        {
            "kind": "constraints",
            "constraints": [
                {
                    "constraint_index": 0,
                    "expected_virtual_space": False,
                    "virtual_space": True,
                }
            ],
        }
    )

    assert list(validator.iter_errors(view)) == []
    assert list(validator.iter_errors(constraints)) == []


def test_schema_rejects_open_mixed_empty_and_unbounded_targets() -> None:
    validator = _validator()
    invalid = (
        {**_base({"kind": "constraints", "constraints": []})},
        _base(
            {
                "kind": "view",
                "expected_shown_virtual_space": False,
                "shown_virtual_space": True,
                "constraints": [],
            }
        ),
        _base(
            {
                "kind": "constraints",
                "constraints": [
                    {
                        "constraint_index": index,
                        "expected_virtual_space": False,
                        "virtual_space": True,
                    }
                    for index in range(17)
                ],
            }
        ),
        {**_base({"kind": "view", "expected_shown_virtual_space": False})},
        {
            **_base(
                {
                    "kind": "view",
                    "expected_shown_virtual_space": False,
                    "shown_virtual_space": True,
                }
            ),
            "unexpected": True,
        },
    )
    assert all(list(validator.iter_errors(value)) for value in invalid)
