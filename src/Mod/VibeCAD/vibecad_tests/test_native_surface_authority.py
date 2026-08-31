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
