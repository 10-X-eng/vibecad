# SPDX-License-Identifier: MIT
"""Both preset dialogs use the same document-owned, single-update operation."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


def test_apply_all_keeps_one_document_and_disables_nested_recomputes(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    document = SimpleNamespace(Name="Owner")
    app = SimpleNamespace(ActiveDocument=document)
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(sys.modules, "FreeCADGui", SimpleNamespace())
    spec = importlib.util.spec_from_file_location("scs_batch_test", root / "bend_actions.py")
    actions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actions)
    target = SimpleNamespace(Document=document)
    monkeypatch.setattr(actions, "collect_bend_targets", lambda: [target])
    calls = []
    def sheet(*args, **kwargs):
        calls.append(("sheet", kwargs))
        app.ActiveDocument = SimpleNamespace(Name="Other")
        return "sheet"
    monkeypatch.setattr(actions, "create_material_sheet_for_entry", sheet)
    monkeypatch.setattr(actions, "apply_entry_to_bends",
                        lambda *args, **kwargs: calls.append(("bends", kwargs)) or "bends")
    boundaries = []
    monkeypatch.setitem(sys.modules, "preset_update", SimpleNamespace(
        run_document_update=lambda owner, operation: boundaries.append(owner) or operation()
    ))
    result = actions.apply_preset({"k": 0.5}, mat_name="5052 Aluminum")
    assert result == "sheet\nbends"
    assert boundaries == [document]
    assert [kind for kind, kwargs in calls] == ["sheet", "bends"]
    assert all(kwargs["document"] is document and kwargs["recompute"] is False
               for _, kwargs in calls)
    assert calls[1][1]["targets"] == [target]
    assert calls[1][1]["warn_dialog"] is False


def test_both_apply_all_buttons_use_one_combined_action():
    import ast
    for filename in ("SCSCommand.py", "CustomPresets.py"):
        path = Path(__file__).resolve().parents[1] / filename
        tree = ast.parse(path.read_text())
        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == "apply_all")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
        calls = []
        dialog = SimpleNamespace(
            _apply_preset_action=lambda: calls.append("combined"),
            apply_to_selection=lambda: calls.append("bends"),
            create_material_sheet=lambda: calls.append("sheet"),
            status=SimpleNamespace(text=lambda: ""), _set_status=lambda text: None,
        )
        namespace["apply_all"](dialog)
        assert calls == ["combined"], filename
