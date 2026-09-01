# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact caching guards for isolated Native CAM path generation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeManufactureOperationGeneration as generation
from VibeCADNativeManufactureErrors import NativeManufactureError


def _cache_key(tmp_path, name: str, snapshot: bytes, tolerance: float) -> str:
    root = tmp_path / name
    root.mkdir()
    snapshot_path = root / "snapshot.FCStd"
    child_path = root / "child.py"
    command_path = root / "FreeCADCmd"
    snapshot_path.write_bytes(snapshot)
    child_path.write_text("# fixed child\n", encoding="utf-8")
    command_path.write_bytes(b"fixed command")
    command_path.chmod(0o700)
    workspace = generation.OperationGenerationWorkspace(
        SimpleNamespace(cleanup=lambda: None),
        root,
        generation._freeze_file(
            command_path,
            1024,
            executable=True,
            hash_contents=False,
        ),
        generation._freeze_file(child_path, 1024),
    )
    request = {
        "operation": "pocket_shape",
        "job": {
            "object_name": "SetupA",
            "expected_state_sha256": "a" * 64,
        },
    }
    request_bytes = generation._canonical_request(
        "document-a",
        request,
        geometry_tolerance_mm=tolerance,
    )
    captured = generation.CapturedOperationGeneration(
        request,
        request_bytes,
        None,
        "SetupA",
        "a" * 64,
        tolerance,
    )
    materialized = generation.MaterializedOperationGeneration(
        workspace,
        captured,
        snapshot_path,
    )
    return generation.freeze_operation_generation(materialized).cache_key


def test_cache_is_setup_scoped_and_invalidated_by_path_preferences(tmp_path) -> None:
    first = _cache_key(tmp_path, "first", b"document with setup B at revision 1", 0.01)
    unrelated_setup_changed = _cache_key(
        tmp_path,
        "second",
        b"document with setup B at revision 2",
        0.01,
    )
    tolerance_changed = _cache_key(
        tmp_path,
        "third",
        b"document with setup B at revision 2",
        0.02,
    )

    assert unrelated_setup_changed == first
    assert tolerance_changed != first


def test_cache_hit_still_authenticates_frozen_runtime(tmp_path, monkeypatch) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    files = {}
    for name, content in {
        "FreeCADCmd": b"fixed command",
        "child.py": b"fixed child",
        "snapshot.FCStd": b"fixed snapshot",
        "request.json": b"fixed request",
    }.items():
        path = root / name
        path.write_bytes(content)
        files[name] = path
    files["FreeCADCmd"].chmod(0o700)
    workspace = generation.OperationGenerationWorkspace(
        SimpleNamespace(cleanup=lambda: None),
        root,
        generation._freeze_file(
            files["FreeCADCmd"],
            1024,
            executable=True,
            hash_contents=False,
        ),
        generation._freeze_file(files["child.py"], 1024),
    )
    frozen = generation.FrozenOperationGeneration(
        workspace,
        generation._freeze_file(files["snapshot.FCStd"], 1024),
        generation._freeze_file(files["request.json"], 1024),
        root / "result.json",
        {},
        "cache-key",
        "SetupA",
    )
    artifact = {
        "schema": "vibecad-cam-path-result-v1",
        "ok": True,
        "commands": [{"name": "G0", "parameters": {}, "annotations": {}}],
        "center_mm": [0.0, 0.0, 0.0],
        "cycle_time": "00:00:00",
        "derived_properties": {},
        "generation_property_changes": [],
        "generation_diagnostics": {},
    }
    monkeypatch.setattr(
        generation,
        "_cache_get",
        lambda _key: generation.json.dumps(artifact).encode("utf-8"),
    )
    files["child.py"].write_bytes(b"changed child")

    with pytest.raises(NativeManufactureError) as caught:
        generation.generate_operation_path(
            frozen,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
        )

    assert caught.value.error_code == "NATIVE_MANUFACTURE_PATH_WORKER_INVALID"


def test_generated_plain_json_python_state_is_publishable() -> None:
    changes = generation._generation_property_changes(
        {
            "GeneratedState": {
                "type": "App::PropertyPythonObject",
                "value": "",
            }
        },
        {
            "GeneratedState": {
                "type": "App::PropertyPythonObject",
                "value": {"regions": [{"depth_mm": 3.5}], "complete": True},
            }
        },
    )

    assert changes == [
        {
            "name": "GeneratedState",
            "type": "App::PropertyPythonObject",
            "value": {"regions": [{"depth_mm": 3.5}], "complete": True},
        }
    ]
    assert generation._property_assignment(
        "App::PropertyPythonObject",
        changes[0]["value"],
    ) == changes[0]["value"]
