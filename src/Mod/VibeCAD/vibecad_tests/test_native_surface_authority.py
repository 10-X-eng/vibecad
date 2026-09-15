# SPDX-License-Identifier: LGPL-2.1-or-later

"""Static fail-closed boundary for human-selected Native surfaces."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from types import SimpleNamespace


_MODULE_ROOT = Path(__file__).resolve().parents[1]
_REGISTRY_IMPORTERS = frozenset(
    {
        "VibeCADNativeProviderContext.py",
        "VibeCADNativeSessionFactory.py",
    }
)
_BINDING_IMPORTERS = frozenset(
    {
        "VibeCADNativeRegistry.py",
        "VibeCADNativeRuntimeRegistry.py",
    }
)
_IMPLEMENTATION_LOOKUP_OWNERS = frozenset(
    {
        "VibeCADNativeCapabilityRegistry.py",
        "VibeCADNativeDispatch.py",
    }
)
_FORBIDDEN_GUI_CALLS = frozenset(
    {
        "activateWorkbench",
        "runCommand",
        "setEdit",
        "resetEdit",
    }
)


def _native_modules() -> tuple[Path, ...]:
    return tuple(sorted(_MODULE_ROOT.glob("VibeCADNative*.py")))


def _imports(tree: ast.AST) -> tuple[tuple[str, int], ...]:
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            result.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            result.extend((alias.name, node.lineno) for alias in node.names)
    return tuple(result)


def test_native_domains_have_no_raw_surface_or_edit_activation_calls() -> None:
    violations = []
    for path in _native_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _FORBIDDEN_GUI_CALLS
            ):
                violations.append((path.name, node.lineno, node.func.attr))

    assert violations == []


def test_shared_surface_authority_owns_raw_gui_activation(monkeypatch) -> None:
    from VibeCADSurfaceAuthority import activate_workbench, enter_edit_mode

    calls = []
    gui_document = SimpleNamespace(
        setEdit=lambda object_name: calls.append(("edit", object_name)) or True
    )
    monkeypatch.setitem(
        sys.modules,
        "FreeCADGui",
        SimpleNamespace(
            activateWorkbench=lambda workbench: calls.append(
                ("workbench", workbench)
            )
        ),
    )

    activate_workbench("TechDrawWorkbench")

    assert enter_edit_mode(gui_document, "Sketch") is True
    assert calls == [
        ("workbench", "TechDrawWorkbench"),
        ("edit", "Sketch"),
    ]


def test_only_session_assembly_can_import_the_complete_registry() -> None:
    violations = []
    for path in _native_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module, line in _imports(tree):
            if module == "VibeCADNativeRegistry" and path.name not in _REGISTRY_IMPORTERS:
                violations.append((path.name, line, module))

    assert violations == []


def test_workspace_deactivation_only_leaves_the_exact_active_assembly(monkeypatch):
    from VibeCADSurfaceAuthority import deactivate_assembly
    from VibeCADEditState import ActiveEditState
    import VibeCADEditState
    import pytest

    document = SimpleNamespace(HasPendingTransaction=False)
    assembly = SimpleNamespace(TypeId="Assembly::AssemblyObject", Document=document)
    state = {"edit": ActiveEditState(True, assembly), "assembly": assembly}
    calls = []
    def reset():
        calls.append("deactivate")
        state.update(edit=ActiveEditState(False), assembly=None)
        return True
    assembly.ViewObject = SimpleNamespace(doubleClicked=reset)
    gui_document = SimpleNamespace(Document=document, resetEdit=reset,
        activeView=lambda: SimpleNamespace(getActiveObject=lambda key: state["assembly"]))
    monkeypatch.setattr(VibeCADEditState, "active_edit_state", lambda gui: state["edit"])
    control = SimpleNamespace(activeDialog=lambda: False)
    monkeypatch.setitem(sys.modules, "FreeCADGui", SimpleNamespace(Control=control))

    assert deactivate_assembly(gui_document) is True
    assert calls == ["deactivate"]
    assert deactivate_assembly(gui_document) is False
    for edit in (ActiveEditState(True),
                 ActiveEditState(True, SimpleNamespace(TypeId="Sketcher::SketchObject"))):
        state["edit"] = edit
        assert deactivate_assembly(gui_document) is False
    assert calls == ["deactivate"]

    state.update(edit=ActiveEditState(True, assembly), assembly=assembly)
    assembly.Document = object()
    with pytest.raises(RuntimeError, match="document"):
        deactivate_assembly(gui_document)
    assembly.Document = document
    control.activeDialog = lambda: True
    with pytest.raises(RuntimeError, match="task"):
        deactivate_assembly(gui_document)
    control.activeDialog = lambda: False
    document.HasPendingTransaction = True
    with pytest.raises(RuntimeError, match="transaction"):
        deactivate_assembly(gui_document)
    document.HasPendingTransaction = False
    assert calls == ["deactivate"]

    assembly.ViewObject.doubleClicked = lambda: True
    with pytest.raises(RuntimeError, match="deactivate"):
        deactivate_assembly(gui_document)


def test_only_registry_assembly_can_import_runtime_binding_modules() -> None:
    violations = []
    for path in _native_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module, line in _imports(tree):
            if module.endswith("Bindings") and path.name not in _BINDING_IMPORTERS:
                violations.append((path.name, line, module))

    assert violations == []


def test_only_dispatch_and_registry_core_can_lookup_hidden_implementations() -> None:
    violations = []
    for path in _native_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if (
                node.func.attr == "implementation"
                and path.name not in _IMPLEMENTATION_LOOKUP_OWNERS
            ):
                violations.append((path.name, node.lineno, node.func.attr))
            if node.func.attr == "handler" and path.name != "VibeCADNativeDispatch.py":
                violations.append((path.name, node.lineno, node.func.attr))

    assert violations == []
