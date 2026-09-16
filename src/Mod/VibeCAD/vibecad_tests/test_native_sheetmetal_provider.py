# SPDX-License-Identifier: LGPL-2.1-or-later
"""Provider-visible references must satisfy the existing sheet tool contracts."""

import pytest

from VibeCADProvider import _model_visible_native_context, _provider_visible_tool_result


@pytest.mark.parametrize("capability", ("sheet_metal.inspect", "sheet_metal.create", "sheet_metal.edit"))
def test_sheet_tool_results_preserve_document_identity_required_by_targets(capability):
    target = {"document_uid": "sheet-document-uuid", "object_name": "SheetHole"}
    result = _provider_visible_tool_result({"ok": True, "_vibecad_native_result": True,
        "target": target, "items": [{"target": target}], "object_id": 19,
        "receipt": {"capability": capability}}, tool_name=capability)
    assert result["target"] == target
    assert result["items"][0]["target"] == target
    assert "object_id" not in result
    assert "receipt" not in result


def test_sheet_context_and_active_read_keep_the_same_exact_references():
    target = {"document_uid": "sheet-document-uuid", "object_name": "Profile"}
    snapshot = {"surface_id": "sheet_metal", "document": {"document_name": "Sheet"},
                "domain": {"kind": "sheet_metal", "objects": [target]},
                "selection": {"items": [{"object": target}]}}
    context = _model_visible_native_context({"native_state": snapshot})
    assert context["state"]["domain"]["objects"] == [target]
    assert context["state"]["selection"]["items"][0]["object"] == target
    result = _provider_visible_tool_result({**snapshot, "ok": True,
        "_vibecad_native_result": True}, tool_name="state.read")
    assert result["domain"]["objects"] == [target]


def test_other_surfaces_keep_existing_compact_reference_behavior():
    target = {"document_uid": "internal-uuid", "object_name": "Pad"}
    result = _provider_visible_tool_result({"ok": True, "_vibecad_native_result": True,
        "target": target}, tool_name="model.inspect")
    assert result["target"] == {"object_name": "Pad"}
    context = _model_visible_native_context({"native_state": {
        "surface_id": "model", "domain": {"objects": [target]}}})
    assert context["state"]["domain"]["objects"] == [{"object_name": "Pad"}]
