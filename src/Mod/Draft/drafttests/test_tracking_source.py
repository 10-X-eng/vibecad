# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source-level completeness checks for Draft's Fusion-style tracking."""

import ast
from pathlib import Path
import unittest


_DRAFT_ROOT = Path(__file__).resolve().parents[1]
_GUI_TOOLS = _DRAFT_ROOT / "draftguitools"
_TASK_PANELS = _DRAFT_ROOT / "drafttaskpanels"


def _module(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _literal_return(function):
    returns = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Return)
    ]
    if len(returns) != 1:
        raise AssertionError(
            f"{function.name} must have one literal command-list return"
        )
    return ast.literal_eval(returns[0].value)


def _literal_assignments(path):
    values = {}
    for node in _module(path).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except (TypeError, ValueError):
                pass
    return values


class DraftTrackingSourceTest(unittest.TestCase):
    """Proves every shipped Draft surface has an explicit contract."""

    def test_complete_command_inventory_is_disjoint_and_expands_to_82(self):
        tools = _module(_DRAFT_ROOT / "draftutils" / "init_tools.py")
        function_names = {
            "get_draft_drawing_commands",
            "get_draft_annotation_commands",
            "get_draft_modification_commands",
            "get_draft_utility_commands_menu",
            "get_draft_snap_commands",
        }
        top_level = set()
        for node in tools.body:
            if isinstance(node, ast.FunctionDef) and node.name in function_names:
                top_level.update(
                    command
                    for command in _literal_return(node)
                    if command != "Separator"
                )
        self.assertEqual(len(top_level), 69)

        contracts = _literal_assignments(
            _DRAFT_ROOT / "drafttests" / "test_timeline_gui.py"
        )
        contract_names = (
            "DRAFT_COMPOSITE_COMMANDS",
            "DRAFT_STANDALONE_CREATION_COMMANDS",
            "DRAFT_SOURCE_PRESERVING_COMMANDS",
            "DRAFT_EXACT_REPLACEMENT_COMMANDS",
            "DRAFT_MODE_DEPENDENT_COMMANDS",
            "DRAFT_IN_PLACE_COMMANDS",
            "DRAFT_VIEW_SELECTION_OR_PREFERENCE_COMMANDS",
        )
        classified = [set(contracts[name]) for name in contract_names]
        for index, contract in enumerate(classified):
            for other in classified[index + 1 :]:
                self.assertFalse(contract & other)

        children = contracts["DRAFT_COMPOSITE_CHILDREN"]
        expanded = set(top_level)
        for commands in children.values():
            expanded.update(commands)
        self.assertEqual(len(expanded), 82)
        self.assertEqual(set().union(*classified), expanded)

        init_gui = (_DRAFT_ROOT / "InitGui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"Draft_Hyperlink"', init_gui)

    def test_every_delayed_shipped_action_declares_exact_inputs(self):
        calls = []
        for directory in (_GUI_TOOLS, _TASK_PANELS):
            for path in sorted(directory.glob("*.py")):
                for node in ast.walk(_module(path)):
                    if not (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "commit"
                    ):
                        continue
                    receiver = node.func.value
                    is_draft_commit = (
                        isinstance(receiver, ast.Name)
                        and receiver.id == "self"
                    ) or (
                        isinstance(receiver, ast.Attribute)
                        and isinstance(receiver.value, ast.Name)
                        and receiver.value.id == "self"
                        and receiver.attr in {"sourceCmd", "source_command"}
                    )
                    if not is_draft_commit:
                        continue
                    calls.append((path, node))
                    self.assertIn(
                        "inputs",
                        {keyword.arg for keyword in node.keywords},
                        f"{path.name}:{node.lineno}",
                    )
        self.assertGreaterEqual(len(calls), 40)

    def test_delayed_execution_never_parses_command_strings_for_identity(self):
        source = (
            _DRAFT_ROOT / "draftutils" / "todo.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import re", source)
        self.assertNotIn("_ACTIVE_DOCUMENT_GET_OBJECT", source)
        self.assertIn(
            "def _capture_object_references(document, objects)",
            source,
        )
        self.assertIn("elif len(entry) == 3:", source)
        self.assertGreaterEqual(
            source.count("validate_execution_context()"),
            3,
        )

    def test_exact_transaction_keeps_the_first_requested_outcome(self):
        source = (
            _DRAFT_ROOT / "draftutils" / "transaction.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "self._requested_abort != abort",
            source,
        )
        self.assertIn(
            "refusing {requested}",
            source,
        )
        self.assertIn(
            "App.closeActiveTransaction(abort, self.transaction_id)",
            source,
        )

    def test_dynamic_edit_surfaces_use_exact_owned_transactions(self):
        edit = (
            _GUI_TOOLS / "gui_edit.py"
        ).read_text(encoding="utf-8")
        edit_actions = (
            _GUI_TOOLS / "gui_edit_draft_objects.py"
        ).read_text(encoding="utf-8")
        for label in (
            "Delete Point",
            "Set Point as First",
            "Add Point",
            "Open Wire",
            "Close Wire",
            "Reverse Wire",
            "Move Arc",
            "Set First Angle",
            "Set Last Angle",
            "Set Radius",
            "Make Sharp",
            "Make Tangent",
            "Make Symmetric",
        ):
            self.assertIn(label, edit_actions)
        self.assertIn("OwnedDocumentTransaction", edit)
        self.assertIn("commit_edit_transaction", edit)
        self.assertIn("abort_edit_transaction", edit)

        working_plane = (
            _DRAFT_ROOT
            / "draftviewproviders"
            / "view_wpproxy.py"
        ).read_text(encoding="utf-8")
        layers = (
            _DRAFT_ROOT / "draftviewproviders" / "view_layer.py"
        ).read_text(encoding="utf-8")
        for label in (
            "Save Camera Position",
            "Save Visibility of Objects",
        ):
            self.assertIn(label, working_plane)
        for label in (
            "Reassign Properties of Layer",
            "Reassign Properties of All Layers",
            "Merge Layer Duplicates",
        ):
            self.assertIn(label, layers)
        self.assertIn("run_document_mutation", working_plane)
        self.assertIn("run_document_mutation", layers)

    def test_timeline_metadata_is_hidden_and_source_presentation_is_preserved(self):
        source = (
            _DRAFT_ROOT / "draftutils" / "timeline.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'obj.setPropertyStatus(name, ("Hidden", "LockDynamic", "NoRecompute"))',
            source,
        )
        self.assertIn("obj.setEditorMode(name, 2)", source)
        module = _module(_DRAFT_ROOT / "draftutils" / "timeline.py")
        derived = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "accept_derived_output"
        )
        derived_source = ast.get_source_segment(source, derived)
        self.assertNotIn("Visibility =", derived_source)

    def test_human_array_commands_leave_source_presentation_to_history(self):
        factories = (
            ("draftmake/make_orthoarray.py", "make_ortho_array"),
            ("draftmake/make_polararray.py", "make_polar_array"),
            ("draftmake/make_circulararray.py", "make_circular_array"),
            ("draftmake/make_patharray.py", "make_path_array"),
            ("draftmake/make_patharray.py", "make_path_twisted_array"),
            ("draftmake/make_pointarray.py", "make_point_array"),
        )
        for relative_path, function_name in factories:
            path = _DRAFT_ROOT / relative_path
            function = next(
                node
                for node in _module(path).body
                if isinstance(node, ast.FunctionDef)
                and node.name == function_name
            )
            self.assertEqual(function.args.args[-1].arg, "hide_base")
            self.assertIs(ast.literal_eval(function.args.defaults[-1]), True)
            source = ast.get_source_segment(
                path.read_text(encoding="utf-8"),
                function,
            )
            self.assertIn("hide_base", source)

        gui_commands = (
            _TASK_PANELS / "task_orthoarray.py",
            _TASK_PANELS / "task_polararray.py",
            _TASK_PANELS / "task_circulararray.py",
            _GUI_TOOLS / "gui_patharray.py",
            _GUI_TOOLS / "gui_pathtwistedarray.py",
            _GUI_TOOLS / "gui_pointarray.py",
        )
        for path in gui_commands:
            source = path.read_text(encoding="utf-8")
            self.assertIn('_cmd += "hide_base=False"', source)
            self.assertLess(
                source.index('_cmd += "hide_base=False"'),
                source.index("accept_derived_output"),
            )

    def test_interactive_previews_do_not_create_document_objects(self):
        for filename in ("gui_lines.py", "gui_splines.py", "gui_beziers.py"):
            source = (_GUI_TOOLS / filename).read_text(encoding="utf-8")
            module = _module(_GUI_TOOLS / filename)
            for node in ast.walk(module):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"addObject", "removeObject"}
                ):
                    continue
                self.fail(
                    f"{filename}:{node.lineno} mutates the document for an "
                    "interactive curve preview"
                )
            self.assertNotIn("self.obj.Shape =", source)

    def test_exact_view_owns_snap_and_grid_cleanup(self):
        snapper = (
            _GUI_TOOLS / "gui_snapper.py"
        ).read_text(encoding="utf-8")
        original_base = (
            _GUI_TOOLS / "gui_base_original.py"
        ).read_text(encoding="utf-8")
        tracker = (
            _GUI_TOOLS / "gui_trackers.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def off(self, view=None):", snapper)
        self.assertIn(
            "self.setTrackers(update_grid=False, view=view)",
            snapper,
        )
        self.assertIn("Gui.Snapper.off(view=self.view)", original_base)
        self.assertRegex(
            tracker,
            r"scene_view\s+if scene_view is not None\s+"
            r"else gui_utils\.get_3d_view\(\)",
        )

    def test_edit_and_trimex_resolve_exact_parametric_sources(self):
        edit = (_GUI_TOOLS / "gui_edit.py").read_text(encoding="utf-8")
        trimex = (_GUI_TOOLS / "gui_trimex.py").read_text(encoding="utf-8")
        transaction = (
            _DRAFT_ROOT / "draftutils" / "transaction.py"
        ).read_text(encoding="utf-8")
        for required in (
            "self.edited_references",
            "def _resolve_edited_object",
            "document_name=obj.Document.Name",
        ):
            self.assertIn(required, edit)
        self.assertIn(
            "document.isObjectUsableAtCurrentTimelinePosition(obj)",
            transaction,
        )
        self.assertIn("make_facebinder.make_facebinder", trimex)
        self.assertIn("timeline.selection_references", trimex)
        self.assertNotIn('self.doc.addObject("Part::Feature", "Face")', trimex)

    def test_task_dialogs_do_not_close_or_reset_the_ambient_document(self):
        offenders = []
        for directory in (
            _DRAFT_ROOT,
            _GUI_TOOLS,
            _TASK_PANELS,
            _DRAFT_ROOT / "draftviewproviders",
        ):
            paths = (
                [directory / "DraftGui.py"]
                if directory == _DRAFT_ROOT
                else sorted(directory.glob("*.py"))
            )
            for path in paths:
                if not path.exists():
                    continue
                source = path.read_text(encoding="utf-8")
                for forbidden in (
                    "Control.closeDialog()",
                    "Gui.ActiveDocument.resetEdit()",
                    "FreeCADGui.ActiveDocument.resetEdit()",
                ):
                    if forbidden in source:
                        offenders.append(f"{path.name}: {forbidden}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
