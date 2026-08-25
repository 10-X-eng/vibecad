# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import VibeCADNativeAnalyzeMeshLifecycleBindings as bindings
from VibeCADNativeAnalyzeMeshRuntime import NativeAnalyzeMeshRuntime


def test_focused_gmsh_edit_retargets_an_existing_mesh_definition(monkeypatch) -> None:
    runtime = object.__new__(NativeAnalyzeMeshRuntime)
    requests: list[dict] = []
    runtime.execute = lambda request, *, ticket: requests.append(request) or {
        "updated": True
    }
    mesh_state = {
        "object_name": "Mesh",
        "state_sha256": "a" * 64,
        "mesher": "gmsh",
        "settings": {
            "maximum_size_mm": 12.0,
            "minimum_size_mm": 2.0,
            "element_order": "second",
        },
    }
    monkeypatch.setattr(
        bindings,
        "current_state",
        lambda _runtime, name, _reader: (SimpleNamespace(Name=name), mesh_state),
    )
    monkeypatch.setattr(
        bindings,
        "current_target",
        lambda _runtime, name, _reader: {
            "object_name": name,
            "expected_state_sha256": "b" * 64,
        },
    )

    result = bindings._edit_values(
        runtime,
        {"mesh_name": "Mesh", "source_name": "TwoBodyDomain"},
        ticket=SimpleNamespace(),
    )

    assert requests == [
        {
            "operation": "update_gmsh",
            "target": {
                "object_name": "Mesh",
                "expected_state_sha256": "a" * 64,
            },
            "source": {
                "object_name": "TwoBodyDomain",
                "expected_state_sha256": "b" * 64,
            },
        }
    ]
    assert result == {"updated": True, "mesh_name": "Mesh"}
