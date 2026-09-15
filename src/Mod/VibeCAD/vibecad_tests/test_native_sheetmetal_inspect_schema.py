# SPDX-License-Identifier: LGPL-2.1-or-later
"""The native sheet inspection contract is discoverable, bounded and read-only."""

from jsonschema import Draft202012Validator

from VibeCADNativeRegistry import build_native_capability_registry
from VibeCADNativeSheetMetalInspectSchema import sheetmetal_inspect_capability_definition


def test_native_sheet_inspection_is_registered_with_a_read_only_binding():
    definition = sheetmetal_inspect_capability_definition()
    registry = build_native_capability_registry()
    assert definition.name == "sheet_metal.inspect"
    assert definition.name in registry.shared_definition_names
    assert registry.definition(definition.name) == definition
    assert callable(registry.implementation(definition.name).handler)
    assert callable(registry.implementation(definition.name).async_handler)
    assert definition.primary_classification == "read"
    assert {variant.operation for variant in definition.variants} == {
        "list_sheets", "read_sheet", "list_history", "list_cuts", "list_regions", "prepare"}
    for variant in definition.variants:
        assert variant.surface_ids == frozenset({"model", "sheet_metal"})
        assert variant.transaction_behavior == "none"
        assert variant.background_required is (variant.operation == "prepare")


def test_sheet_inspection_schema_rejects_ambiguous_targets_and_unbounded_pages():
    definition = sheetmetal_inspect_capability_definition()
    for operation in ("list_sheets", "read_sheet", "list_history", "list_cuts", "list_regions", "prepare"):
        schema = definition.provider_schema((operation,))["parameters"]
        validator = Draft202012Validator(schema)
        valid = {"operation": operation}
        if operation != "list_sheets":
            valid["target"] = {"document_uid": "current-document", "object_name": "SheetCut"}
        assert not list(validator.iter_errors(valid))
        assert list(validator.iter_errors({**valid, "surprise": True}))
        if operation not in ("read_sheet", "prepare"):
            for key, value in (("offset", -1), ("offset", True), ("page_size", 0),
                               ("page_size", 33), ("expected_revision", -1)):
                assert list(validator.iter_errors({**valid, key: value}))
            assert not list(validator.iter_errors({**valid, "offset": 2, "page_size": 4,
                                                   "expected_revision": 7}))
        if operation != "list_sheets":
            for target in ({"object_name": "SheetCut"}, {"document_uid": "current-document"},
                           {"document_uid": "current-document", "object_name": "a label"},
                           {**valid["target"], "label": "SheetCut"}):
                assert list(validator.iter_errors({**valid, "target": target}))
