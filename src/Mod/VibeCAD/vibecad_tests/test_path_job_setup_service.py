# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared CAM setup configuration stays exact and setup-scoped."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


_CAM_ROOT = Path(__file__).resolve().parents[2] / "CAM"
_MODULE_PATH = _CAM_ROOT / "Path" / "Main" / "JobSetup.py"


def _service():
    cam_root = str(_CAM_ROOT)
    if cam_root not in sys.path:
        sys.path.insert(0, cam_root)
    spec = importlib.util.spec_from_file_location("vibecad_test_job_setup", _MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("CAM Job setup service could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Proxy:
    def __init__(self) -> None:
        self.executions = 0

    def execute(self, _job) -> None:
        self.executions += 1


class _Job:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.Label = name
        self.Description = ""
        self.Machine = ""
        self.PostProcessor = "grbl"
        self.PostProcessorArgs = ""
        self.SplitOutput = False
        self.OrderOutputBy = "Fixture"
        self.Fixtures = ["G54"]
        self.GeometryTolerance = 0.01
        self.Proxy = _Proxy()


def test_setup_configuration_is_bounded_and_stable() -> None:
    service = _service()
    setup = _Job("TopSetup")

    first = service.setup_configuration_state(setup)
    second = service.setup_configuration_state(setup)

    assert first == second
    assert first["object_name"] == "TopSetup"
    assert first["fixtures"] == ["G54"]
    assert len(first["state_sha256"]) == 64


def test_setup_update_validates_every_change_before_mutating() -> None:
    service = _service()
    setup = _Job("RearSetup")
    before = service.setup_configuration_state(setup)

    with pytest.raises(ValueError, match="machine"):
        service.apply_setup_configuration(
            setup,
            {
                "label": "Changed too early",
                "machine": "Missing mill",
                "fixtures": ["G55"],
            },
            machine_names=("3-axis mill",),
            postprocessor_names=("grbl",),
        )

    assert service.setup_configuration_state(setup) == before
    assert setup.Proxy.executions == 0


def test_existing_catalog_values_remain_editable_without_forced_recompute() -> None:
    service = _service()
    setup = _Job("LegacySetup")
    setup.Machine = "Retired Shop Mill"
    setup.PostProcessor = "retired_post"

    result = service.apply_setup_configuration(
        setup,
        {
            "machine": "Retired Shop Mill",
            "postprocessor": "retired_post",
            "description": "Keep the saved setup usable.",
        },
        machine_names=("Current Mill",),
        postprocessor_names=("grbl",),
        recompute=False,
    )

    assert result["machine"] == "Retired Shop Mill"
    assert result["postprocessor"] == "retired_post"
    assert setup.Proxy.executions == 0


def test_setup_update_changes_only_the_explicit_setup() -> None:
    service = _service()
    first = _Job("FirstSetup")
    second = _Job("SecondSetup")
    second_before = service.setup_configuration_state(second)

    result = service.apply_setup_configuration(
        first,
        {
            "label": "First side finish",
            "description": "Finish the first side after roughing.",
            "machine": "3-axis mill",
            "postprocessor": "linuxcnc",
            "postprocessor_args": "--no-show-editor",
            "fixtures": ["G55", "G56"],
            "split_output": True,
            "output_order": "Tool",
            "geometry_tolerance_mm": 0.005,
        },
        machine_names=("3-axis mill",),
        postprocessor_names=("grbl", "linuxcnc"),
    )

    assert result == service.setup_configuration_state(first)
    assert result["label"] == "First side finish"
    assert result["machine"] == "3-axis mill"
    assert result["fixtures"] == ["G55", "G56"]
    assert result["geometry_tolerance_mm"] == 0.005
    assert first.Proxy.executions == 1
    assert service.setup_configuration_state(second) == second_before
    assert second.Proxy.executions == 0


def test_setup_options_are_exact_searchable_and_paged() -> None:
    service = _service()

    machines = service.search_setup_options(
        "machine",
        query="mill",
        offset=1,
        page_size=1,
        machine_names=("Bench Mill", "Five Axis Mill", "Router"),
        postprocessor_names=("grbl",),
    )
    posts = service.search_setup_options(
        "postprocessor",
        query="cnc",
        offset=0,
        page_size=2,
        machine_names=("Bench Mill",),
        postprocessor_names=("grbl", "linuxcnc", "linuxcnc_legacy"),
    )

    assert machines == {
        "category": "machine",
        "query": "mill",
        "offset": 1,
        "count": 1,
        "total": 2,
        "next_offset": None,
        "values": ["Five Axis Mill"],
    }
    assert posts == {
        "category": "postprocessor",
        "query": "cnc",
        "offset": 0,
        "count": 2,
        "total": 2,
        "next_offset": None,
        "values": ["linuxcnc", "linuxcnc_legacy"],
    }
