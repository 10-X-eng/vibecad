# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for bounded optional CAMotics data handling."""

from __future__ import annotations

import json
import math
import struct
import sys
import types
from types import SimpleNamespace

import pytest

import VibeCADNativeManufactureCamoticsWorker as Worker
from VibeCADNativeManufactureCamoticsSchema import (
    manufacture_camotics_capability_definition,
)
from VibeCADNativeManufactureErrors import NativeManufactureError


def _triangle(*, normal_x: float = 0.0) -> bytes:
    header = b"CAMotics unit surface".ljust(80, b"\0")
    facet = struct.pack(
        "<12fH",
        normal_x,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        4.0,
        0.0,
        0.0,
        0.0,
        3.0,
        0.0,
        0,
    )
    return header + struct.pack("<I", 1) + facet


def _failure_code(action) -> str:
    with pytest.raises(NativeManufactureError) as raised:
        action()
    return str(raised.value.failure()["error_code"])


def test_schema_has_two_closed_requests_and_no_provider_process_fields() -> None:
    schema = manufacture_camotics_capability_definition().provider_schema(("camotics",))
    branch = schema["parameters"]["oneOf"][0]
    request = branch["properties"]["request"]

    assert branch["required"] == ["job", "operations", "request"]
    assert branch["additionalProperties"] is False
    assert [item["properties"]["kind"]["const"] for item in request["oneOf"]] == [
        "read_result",
        "launch",
    ]
    assert all(
        item["required"] == ["kind", "resolution"]
        and item["additionalProperties"] is False
        for item in request["oneOf"]
    )
    property_names = set()

    def collect(value) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("properties"), dict):
                property_names.update(value["properties"])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(schema)
    assert not property_names.intersection({"path", "executable", "command"})
    assert len(json.dumps(schema, separators=(",", ":")).encode("utf-8")) < 5_000


def test_path_result_requires_bounded_finite_explicit_times() -> None:
    assert Worker._path_facts([{"time": 0.25}, {"time": 0.75}]) == (2, 1.0)
    assert _failure_code(lambda: Worker._path_facts([{}])) == (
        "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID"
    )
    assert _failure_code(lambda: Worker._path_facts([{"time": math.inf}])) == (
        "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID"
    )
    assert _failure_code(lambda: Worker._path_facts([{"time": -0.1}])) == (
        "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID"
    )


def test_binary_surface_checks_shape_length_and_every_coordinate() -> None:
    digest, facets, bounds = Worker._surface_facts(_triangle())
    assert len(digest) == 64
    assert facets == 1
    assert bounds == ((0.0, 0.0, 0.0), (4.0, 3.0, 0.0))

    truncated = _triangle()[:-1]
    assert _failure_code(lambda: Worker._surface_facts(truncated)) == (
        "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID"
    )
    assert _failure_code(
        lambda: Worker._surface_facts(_triangle(normal_x=math.nan))
    ) == "NATIVE_MANUFACTURE_CAMOTICS_RESULT_INVALID"


def test_program_bound_is_enforced_while_gcode_is_appended(monkeypatch) -> None:
    class FakeCommand:
        def __init__(self, name, _parameters):
            self._name = name

        def toGCode(self):
            return self._name

    class FakePath:
        def __init__(self, commands):
            self.Commands = tuple(commands)

    path_module = types.ModuleType("Path")
    path_module.Command = FakeCommand
    path_module.Path = FakePath
    path_scripts = types.ModuleType("PathScripts")
    path_utils = types.ModuleType("PathScripts.PathUtils")
    monkeypatch.setitem(sys.modules, "Path", path_module)
    monkeypatch.setitem(sys.modules, "PathScripts", path_scripts)
    monkeypatch.setitem(sys.modules, "PathScripts.PathUtils", path_utils)
    monkeypatch.setattr(Worker, "MAX_CAMOTICS_PROGRAM_BYTES", 24)
    run = SimpleNamespace(
        tool_number=1,
        operation_name="Profile",
        placement=SimpleNamespace(isIdentity=lambda: True),
        commands=(SimpleNamespace(name="G1 X123456789", parameters=()),),
    )
    frozen = SimpleNamespace(runs=(run,), command_count=1)

    assert _failure_code(
        lambda: Worker._program(
            frozen,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
        )
    ) == "NATIVE_MANUFACTURE_SIMULATION_LIMIT"


def test_program_rejects_embedded_control_lines(monkeypatch) -> None:
    class FakeCommand:
        def __init__(self, name, _parameters):
            self._name = name

        def toGCode(self):
            return self._name

    class FakePath:
        def __init__(self, commands):
            self.Commands = tuple(commands)

    path_module = types.ModuleType("Path")
    path_module.Command = FakeCommand
    path_module.Path = FakePath
    path_scripts = types.ModuleType("PathScripts")
    path_utils = types.ModuleType("PathScripts.PathUtils")
    monkeypatch.setitem(sys.modules, "Path", path_module)
    monkeypatch.setitem(sys.modules, "PathScripts", path_scripts)
    monkeypatch.setitem(sys.modules, "PathScripts.PathUtils", path_utils)
    run = SimpleNamespace(
        tool_number=1,
        operation_name="Profile",
        placement=SimpleNamespace(isIdentity=lambda: True),
        commands=(SimpleNamespace(name="G1 X1\nM30", parameters=()),),
    )
    frozen = SimpleNamespace(runs=(run,), command_count=1)

    assert _failure_code(
        lambda: Worker._program(
            frozen,
            cancelled=lambda: False,
            progress=lambda _percent, _message: None,
        )
    ) == "NATIVE_MANUFACTURE_TOOLPATH_INVALID"
