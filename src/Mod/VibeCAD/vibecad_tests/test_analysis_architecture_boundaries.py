# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import ast
from pathlib import Path


VIBECAD_DIR = Path(__file__).resolve().parent.parent
GENERIC_ANALYSIS_FILES = (
    VIBECAD_DIR / "tool_impl" / "analysis_contracts.py",
    VIBECAD_DIR / "tool_impl" / "analysis_artifacts.py",
    VIBECAD_DIR / "tool_impl" / "analysis_local_provider.py",
    VIBECAD_DIR / "tool_impl" / "analysis_runtime.py",
    VIBECAD_DIR / "VibeCADAnalysisProviders.py",
)
FORBIDDEN_IMPORT_PREFIXES = (
    "FreeCAD",
    "FreeCADGui",
    "Fem",
    "VibeCADAero",
    "VibeCADNativeAnalyzeSolverExecution",
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return tuple(imported)


def test_generic_analysis_layer_has_no_freecad_aero_or_fem_solver_imports() -> None:
    violations: list[str] = []
    for path in GENERIC_ANALYSIS_FILES:
        assert path.is_file(), f"Missing generic Analysis module: {path.name}"
        for module in _imports(path):
            if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                violations.append(f"{path.name}: {module}")

    assert violations == [], (
        "Generic Analysis modules must remain host/domain neutral; forbidden imports: "
        + ", ".join(violations)
    )
