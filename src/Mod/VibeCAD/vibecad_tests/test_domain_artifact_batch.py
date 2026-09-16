# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared native artifact preparation must submit independent outputs together."""
import sys
from types import SimpleNamespace

import pytest
import VibeCADVibeScriptDomainRuntime as runtime


def test_brep_outputs_are_one_native_parallel_batch(tmp_path, monkeypatch):
    paths = [tmp_path / "first.brep", tmp_path / "second.brep"]
    for path in paths:
        path.touch()
    calls = []
    shapes = [object(), object()]
    def load(values):
        calls.append(values)
        return shapes
    monkeypatch.setitem(sys.modules, "Part", SimpleNamespace(readBrepShapes=load))
    outputs = [{"name": "first", "artifact_kind": "brep", "artifact_path": paths[0].name},
               {"name": "metadata", "artifact_kind": "json"},
               {"name": "second", "artifact_kind": "brep", "artifact_path": paths[1].name}]
    assert runtime._prepare_brep_outputs(outputs, tmp_path) == {0: shapes[0], 2: shapes[1]}
    assert calls == [[str(path) for path in paths]]
    outputs[2]["artifact_path"] = "../outside.brep"
    with pytest.raises(ValueError):
        runtime._prepare_brep_outputs(outputs, tmp_path)
    assert len(calls) == 1
