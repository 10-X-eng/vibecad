# SPDX-License-Identifier: LGPL-2.1-or-later

"""Source contracts for exact native FEM factory results."""

from pathlib import Path
import re
import unittest


def _find_fem_source_root():
    """Find the source tree from either an in-tree or copied build test."""

    starts = (Path.cwd(), Path(__file__).resolve())
    inspected = []
    for start in starts:
        for parent in (start, *start.parents):
            for candidate in (
                parent / "src" / "Mod" / "Fem",
                parent,
            ):
                if candidate in inspected:
                    continue
                inspected.append(candidate)
                if (candidate / "Gui" / "Command.cpp").is_file():
                    return candidate
    raise FileNotFoundError(
        "Could not locate the FEM source tree from the test or working "
        f"directory; inspected: {inspected}"
    )


FEM_ROOT = _find_fem_source_root()
COMMAND_SOURCE = FEM_ROOT / "Gui" / "Command.cpp"
MANAGER_SOURCE = FEM_ROOT / "femcommands" / "manager.py"
PYTHON_COMMANDS_SOURCE = FEM_ROOT / "femcommands" / "commands.py"


def _section(source, start, end):
    return source.split(start, 1)[1].split(end, 1)[0]


def _without_cpp_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


class TestExactNativeFemCreationSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.command = COMMAND_SOURCE.read_text(encoding="utf-8")
        cls.manager = MANAGER_SOURCE.read_text(encoding="utf-8")
        cls.python_commands = PYTHON_COMMANDS_SOURCE.read_text(
            encoding="utf-8"
        )

    def test_shared_factory_captures_and_retains_exact_identity(self):
        helpers = _section(
            self.command,
            "class ExactFemObject",
            "// OvG: Visibility automation",
        )
        self.assertIn("document->getObjectByID(objectId)", helpers)
        self.assertIn(
            "Gui::Command::runDocumentObjectCommand(",
            helpers,
        )
        self.assertIn("Base::Type::fromName(typeId)", helpers)
        self.assertNotIn("document->getObject(name.c_str())", helpers)
        self.assertIn(
            "const ExactFemObject exactAnalysis(analysis)",
            helpers,
        )
        self.assertRegex(
            helpers,
            r"startFemObjectEditor\(\s*Gui::Command\* command,\s*"
            r"const ExactFemObject& exactObject",
        )

    def test_no_shipped_cpp_factory_recovers_active_or_named_output(self):
        active_source = _without_cpp_comments(self.command)
        self.assertNotIn(
            "App.ActiveDocument.ActiveObject",
            active_source,
        )
        self.assertNotIn(
            "App.activeDocument().ActiveObject",
            active_source,
        )
        self.assertNotRegex(
            active_source,
            r"getObject\([^)]*(?:FeatName|featureName|"
            r"functionGroupName|uniqueElementsName)",
        )
        direct_factories = re.findall(
            r"(?:doCommand|Gui::Command::doCommand)\s*\([^;]+?"
            r"addObject\(['\"]Fem::",
            active_source,
            flags=re.DOTALL,
        )
        self.assertEqual(direct_factories, [])

    def test_node_and_element_sets_keep_exact_factory_results(self):
        node_commands = _section(
            self.command,
            "static void DefineNodesCallback",
            "// start of Erase Elements code",
        )
        element_commands = _section(
            self.command,
            "static void DefineElementsCallback",
            "// end of Erase Elements code",
        )
        for body, type_id in (
            (node_commands, "Fem::FemSetNodesObject"),
            (element_commands, "Fem::FemSetElementNodesObject"),
        ):
            self.assertIn("createFemObject(", body)
            self.assertIn(type_id, body)
            self.assertIn("addFemObjectToAnalysis(", body)
            self.assertIn("startFemObjectEditor(this,", body)
            self.assertNotIn("ActiveObject", body)

    def test_post_factories_use_exact_handles_end_to_end(self):
        filter_body = _section(
            self.command,
            "void setupFilter(",
            "std::string Plot()",
        )
        function_body = _section(
            self.command,
            "void CmdFemPostFunctions::activated",
            "Gui::Action* CmdFemPostFunctions::createAction",
        )
        pipeline_body = _section(
            self.command,
            "void CmdFemPostPipelineFromResult::activated",
            "bool CmdFemPostPipelineFromResult::isActive",
        )

        self.assertIn("const ExactFemObject exactFilter", filter_body)
        self.assertIn("exactFilter.get()", filter_body)
        self.assertIn(
            "startFemObjectEditor(cmd, exactFilter)",
            filter_body,
        )
        self.assertNotIn("getObject(FeatName", filter_body)

        self.assertIn("ExactFemObject exactProvider", function_body)
        self.assertIn("const ExactFemObject exactFeature", function_body)
        self.assertIn("exactFeature.get()", function_body)
        self.assertIn(
            "startFemObjectEditor(this, exactFeature)",
            function_body,
        )
        self.assertNotIn("getObject(featureName", function_body)

        self.assertIn("const ExactFemObject exactResult", pipeline_body)
        self.assertIn("const ExactFemObject exactPipeline", pipeline_body)
        self.assertIn("exactPipeline.get()", pipeline_body)
        self.assertNotIn("getObject(featureName", pipeline_body)

    def test_python_history_metadata_is_fully_canonicalized(self):
        helper = _section(
            self.manager,
            "def _canonicalize_timeline_property",
            "def _mark_timeline_operation",
        )
        self.assertIn("obj.setEditorMode(property_name, 2)", helper)
        self.assertIn(
            '(\"Hidden\", \"LockDynamic\", \"NoRecompute\")',
            helper,
        )
        self.assertGreaterEqual(
            self.manager.count("_canonicalize_timeline_property("),
            7,
        )

    def test_new_analysis_publishes_default_solver_as_one_exact_block(self):
        helper = _section(
            self.python_commands,
            "def _capture_exact_object_identity",
            "class _Analysis",
        )
        analysis = _section(
            self.python_commands,
            "class _Analysis",
            "class _ClippingPlaneAdd",
        )

        self.assertIn("document.getObject(int(obj.ID)) is not obj", helper)
        self.assertIn("by_name = document.getObject(name)", helper)
        self.assertIn("by_id = document.getObject(object_id)", helper)
        self.assertIn("def createDefaultSolverFeature(", helper)
        self.assertNotIn("ActiveObject", helper)

        self.assertGreaterEqual(
            analysis.count("runDocumentObjectCommand("),
            2,
        )
        self.assertIn("_mark_timeline_operation", analysis)
        self.assertIn("_mark_timeline_resource", analysis)
        self.assertIn(
            "publishProvisionalTimelineOperationBlock(",
            analysis,
        )
        self.assertIn(
            "operations[analysis_index - 1] is not solver",
            analysis,
        )
        self.assertNotIn("FreeCADGui.runCommand(", analysis)
        self.assertNotIn("ActiveObject", analysis)
        self.assertNotIn("for candidate in document.Objects", analysis)


if __name__ == "__main__":
    unittest.main()
