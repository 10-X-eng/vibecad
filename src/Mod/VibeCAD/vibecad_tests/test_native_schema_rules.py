# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityRegistryError,
    NativeCapabilityVariant,
)
from VibeCADNativeCommonSchema import common_capability_definitions
from VibeCADNativeSchemaRules import (
    MAX_NATIVE_PARAMETER_ARRAY_ITEMS,
    MAX_NATIVE_PARAMETER_TEXT_CHARACTERS,
    NativeSchemaRuleError,
    validate_bounded_parameter_schema,
)


def _object(properties=None, required=()):
    return {
        "type": "object",
        "properties": dict(properties or {}),
        "required": list(required),
        "additionalProperties": False,
    }


def _variant(parameters):
    return NativeCapabilityVariant(
        operation="test",
        description="Exercise one bounded test operation.",
        action_ids=frozenset({"VibeCAD_Test"}),
        surface_ids=frozenset({"model"}),
        exact_target_type=None,
        transaction_behavior="none",
        background_required=False,
        parameters=parameters,
    )


def test_all_common_variant_parameter_schemas_obey_recursive_bounds() -> None:
    definitions = common_capability_definitions()

    for definition in definitions:
        for variant in definition.variants:
            validate_bounded_parameter_schema(variant.parameters)


def test_nested_objects_must_be_closed_and_typed() -> None:
    with pytest.raises(NativeCapabilityRegistryError, match="reject additional"):
        _variant(_object({"target": {"type": "object", "properties": {}}}))

    with pytest.raises(NativeSchemaRuleError, match="supported type or composition"):
        validate_bounded_parameter_schema(_object({"target": {}}))


def test_free_text_must_have_a_finite_positive_bound() -> None:
    for maximum in (None, 0, MAX_NATIVE_PARAMETER_TEXT_CHARACTERS + 1, 1.5):
        schema = {"type": "string"}
        if maximum is not None:
            schema["maxLength"] = maximum
        with pytest.raises(NativeSchemaRuleError, match="bounded maxLength"):
            validate_bounded_parameter_schema(_object({"name": schema}))

    validate_bounded_parameter_schema(
        _object(
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_NATIVE_PARAMETER_TEXT_CHARACTERS,
                }
            }
        )
    )


def test_arrays_require_one_item_schema_and_finite_cardinality() -> None:
    base = {"type": "array", "items": {"type": "boolean"}, "minItems": 0}
    for maximum in (None, 0, MAX_NATIVE_PARAMETER_ARRAY_ITEMS + 1, 2.5):
        schema = dict(base)
        if maximum is not None:
            schema["maxItems"] = maximum
        with pytest.raises(NativeSchemaRuleError, match="bounded minItems/maxItems"):
            validate_bounded_parameter_schema(_object({"values": schema}))

    with pytest.raises(NativeSchemaRuleError, match="one item schema"):
        validate_bounded_parameter_schema(
            _object({"values": {"type": "array", "minItems": 0, "maxItems": 4}})
        )

    with pytest.raises(NativeSchemaRuleError, match="bounded minItems/maxItems"):
        validate_bounded_parameter_schema(
            _object(
                {
                    "values": {
                        "type": "array",
                        "items": {"type": "boolean"},
                        "minItems": 3,
                        "maxItems": 2,
                    }
                }
            )
        )


@pytest.mark.parametrize("keyword", ["$ref", "$defs", "definitions"])
def test_schema_references_are_forbidden(keyword) -> None:
    with pytest.raises(NativeSchemaRuleError, match="cannot use schema references"):
        validate_bounded_parameter_schema(
            _object({"target": {keyword: "#/hidden", "type": "boolean"}})
        )


def test_required_fields_must_be_declared_unique_property_names() -> None:
    for required in (["missing"], ["value", "value"], [7]):
        with pytest.raises(NativeSchemaRuleError, match="required fields"):
            validate_bounded_parameter_schema(
                _object({"value": {"type": "boolean"}}, required)
            )


def test_composition_branches_are_recursively_validated() -> None:
    validate_bounded_parameter_schema(
        _object(
            {
                "choice": {
                    "oneOf": [
                        {"type": "string", "const": "named"},
                        {"type": "boolean"},
                    ]
                }
            }
        )
    )

    with pytest.raises(NativeSchemaRuleError, match="bounded maxLength"):
        validate_bounded_parameter_schema(
            _object(
                {
                    "choice": {
                        "oneOf": [
                            {"type": "string", "const": "named"},
                            {"type": "string"},
                        ]
                    }
                }
            )
        )
