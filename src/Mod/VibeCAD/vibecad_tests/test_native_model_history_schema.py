# SPDX-License-Identifier: LGPL-2.1-or-later

from VibeCADNativeModelHistorySchema import model_history_capability_definition


def test_delete_features_contract_exposes_preview_stage_fields() -> None:
    branch = model_history_capability_definition().variants[0].parameters
    assert branch["properties"]["stage"]["enum"] == ["propose", "apply"]
    assert branch["properties"]["preview_id"]["minLength"] == 1
    assert "stage" not in branch["required"]
    assert "preview_id" not in branch["required"]
    assert branch["required"] == ["targets"]


def test_set_suppressed_contract_has_no_preview_stage_fields() -> None:
    branch = model_history_capability_definition().variants[1].parameters
    assert "stage" not in branch["properties"]
    assert "preview_id" not in branch["properties"]
