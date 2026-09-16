# SPDX-License-Identifier: MIT
"""InitGui also runs from an application-installed Mod directory."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def test_init_gui_uses_imported_module_location_without_dunder_file(tmp_path, monkeypatch):
    installed = tmp_path / "application" / "Mod" / "SendCutSendPresets"
    installed.mkdir(parents=True)
    (installed / "SCSCommand.py").write_text("")
    app = SimpleNamespace(getUserAppDataDir=lambda: str(tmp_path / "user"))
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(sys.modules, "SCS_locator", SimpleNamespace(PATH=str(installed)))
    for name in ("sheetmetal_integration", "pending_unfold"):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(setup=lambda: None))
    monkeypatch.setattr(sys, "path", list(sys.path))
    namespace = {"os": os, "sys": sys, "FreeCAD": app, "Workbench": object,
                 "Gui": SimpleNamespace(addWorkbench=lambda wb: None)}
    local = {}
    script = Path(__file__).resolve().parents[1] / "InitGui.py"
    exec(compile(script.read_text(), str(script), "exec"), namespace, local)
    assert local["SCS_MOD_DIR"] == str(installed)
    assert local["SCS_ICON"] == str(installed / "resources" / "icons" / "SCS_Presets.svg")
