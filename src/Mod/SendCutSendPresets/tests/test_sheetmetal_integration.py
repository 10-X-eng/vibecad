# SPDX-License-Identifier: MIT
"""SheetMetal attachment is event-driven and repeated activation is harmless."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def integration(monkeypatch):
    calls, queued, connections = [], [], []
    wb = SimpleNamespace(
        appendToolbar=lambda *args: calls.append("toolbar"),
        appendMenu=lambda *args: calls.append("menu"),
    )
    monkeypatch.setitem(sys.modules, "FreeCAD", SimpleNamespace(GuiUp=True))
    monkeypatch.setitem(sys.modules, "FreeCADGui", SimpleNamespace(
        listWorkbenches=lambda: {"SMWorkbench": wb},
        getWorkbench=lambda name: wb,
        getMainWindow=lambda: SimpleNamespace(workbenchActivated=SimpleNamespace(
            connect=connections.append)),
    ))
    monkeypatch.setitem(sys.modules, "CustomPresets", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pending_unfold", SimpleNamespace(_post_apply=queued.append))
    spec = importlib.util.spec_from_file_location("scs_integration_test",
        Path(__file__).resolve().parents[1] / "sheetmetal_integration.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, wb, calls, queued, connections


def test_repeated_workbench_activation_does_not_duplicate_entries(integration):
    module, wb, calls, queued, connections = integration
    for _ in range(3):
        assert module.install_into_sheetmetal()
        module._on_workbench_activated("SMWorkbench")
    assert calls == ["toolbar", "menu"]


def test_setup_queues_one_attempt_and_hooks_activation_once(integration):
    module, wb, calls, queued, connections = integration
    module.setup()
    module.setup()
    assert len(connections) == 1
    assert len(queued) == 1
    queued.pop()()
    assert calls == ["toolbar", "menu"]


def test_partial_attachment_retries_only_the_missing_entry(integration):
    module, wb, calls, queued, connections = integration
    def unavailable(*args):
        raise RuntimeError("Workbench is initializing")
    wb.appendMenu = unavailable
    assert not module.install_into_sheetmetal()
    wb.appendMenu = lambda *args: calls.append("menu")
    assert module.install_into_sheetmetal()
    assert calls == ["toolbar", "menu"]
