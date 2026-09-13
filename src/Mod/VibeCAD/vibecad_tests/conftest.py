# SPDX-License-Identifier: LGPL-2.1-or-later

"""Test bootstrap: stub FreeCAD and expose required source module directories.

The guardrail tests validate tool contracts and pack wiring, none of which
require a running FreeCAD. Tool modules defer their FreeCAD imports into
run() bodies, but a few top-level VibeCAD modules import FreeCAD at module
scope, so minimal stubs are installed before any VibeCAD import happens.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types

VIBECAD_DIR = Path(__file__).resolve().parent.parent
TECHDRAW_DIR = VIBECAD_DIR.parent / "TechDraw"
FEM_DIR = VIBECAD_DIR.parent / "Fem"


def _install_freecad_stubs() -> None:
    for name in ("FreeCAD", "FreeCADGui"):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.GuiUp = False
            sys.modules[name] = module


_install_freecad_stubs()

for module_dir in (FEM_DIR, TECHDRAW_DIR, VIBECAD_DIR):
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
