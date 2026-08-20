# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from VibeCADNativeModelBooleanSchema import model_boolean_capability_definition
from VibeCADNativeModelDressupSchema import model_dressup_capability_definition
from VibeCADNativeModelFeatureSchema import focused_model_feature_capability_definitions
from VibeCADNativeModelHistorySchema import model_history_capability_definition
from VibeCADNativeModelHoleSchema import model_hole_capability_definition
from VibeCADNativeModelTransformSchema import model_transform_capability_definition
from VibeCADNativeState import NATIVE_PREVIEW_FAMILIES


def _allowlisted_definitions():
    return (
        *focused_model_feature_capability_definitions(),
        model_dressup_capability_definition(),
        model_boolean_capability_definition(),
        model_transform_capability_definition(),
        model_hole_capability_definition(),
        model_history_capability_definition(),
    )


def _schema_digest(definition) -> str:
    operations = tuple(variant.operation for variant in definition.variants)
    encoded = json.dumps(
        definition.provider_schema(operations),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_native_gui_contract_hashes_match_frozen_fixture_after_schema_extras() -> None:
    fixture_path = Path(__file__).with_name("native_gui_contract_sha256.json")
    expected = json.loads(fixture_path.read_text(encoding="utf-8"))
    observed = {
        definition.name: _schema_digest(definition)
        for definition in _allowlisted_definitions()
        if definition.name in NATIVE_PREVIEW_FAMILIES
    }
    assert set(observed) == set(NATIVE_PREVIEW_FAMILIES)
    assert "model.sketch" not in observed
    assert observed == expected
    for definition in _allowlisted_definitions():
        if definition.name not in NATIVE_PREVIEW_FAMILIES:
            continue
        serialized = json.dumps(definition.provider_schema(
            tuple(variant.operation for variant in definition.variants)
        ))
        assert '"stage"' in serialized
        assert '"preview_id"' in serialized
