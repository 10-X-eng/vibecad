# SPDX-License-Identifier: LGPL-2.1-or-later

"""Windows process-lifecycle contracts for background Mesh operations."""

import ast
from pathlib import Path


VIBECAD_ROOT = Path(__file__).resolve().parents[1]


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} was not found in {path.name}")


def test_direct_gmsh_launch_suppresses_the_windows_console() -> None:
    node = _function_node(VIBECAD_ROOT / "VibeCADNativeMeshGmsh.py", "run_gmsh_remesh")
    popen_calls = [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and isinstance(item.func.value, ast.Name)
        and item.func.value.id == "subprocess"
        and item.func.attr == "Popen"
    ]

    assert len(popen_calls) == 1
    assert "creationflags" in {keyword.arg for keyword in popen_calls[0].keywords}


def test_isolated_gmsh_launch_suppresses_the_windows_console() -> None:
    node = _function_node(
        VIBECAD_ROOT / "VibeCADMeshTessellationChild.py",
        "_tessellate",
    )
    run_calls = [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and isinstance(item.func.value, ast.Name)
        and item.func.value.id == "subprocess"
        and item.func.attr == "run"
    ]

    assert len(run_calls) == 1
    assert "creationflags" in {keyword.arg for keyword in run_calls[0].keywords}


def test_isolated_mesh_worker_cancels_the_complete_process_tree() -> None:
    path = VIBECAD_ROOT / "VibeCADIsolatedMeshWorker.py"
    node = _function_node(path, "_stop")
    calls = [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == "terminate_process_tree"
    ]

    assert len(calls) == 1
