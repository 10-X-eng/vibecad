# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI gate for source-managed native Assembly simulation playback."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part

from vibecad_tests.assembly_vibescript_api_integration import (
    _Service,
    _candidate_capture,
    _document_objects,
    _prepare_and_execute,
    _reference_schema,
    _simulation_source,
)
from VibeCADDocumentReferences import reference_for_target
from VibeCADModelingSurface import resolve_modeling_surface
from VibeCADVibeScriptDomainPublication import publish_candidate
from VibeCADVibeScriptDomainRuntime import (
    accept_candidate,
    retain_candidate,
    validate_candidate,
)
from VibeCADVibeScriptDomains import get_vibescript_pack


class TestAssemblySimulationPlayback(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        self.root = Path(tempfile.mkdtemp(prefix="vibecad-simulation-playback-"))
        self.document = App.newDocument("VibeCADSimulationPlayback")
        Gui.activateView("Gui::View3DInventor", True)
        Gui.activateWorkbench("AssemblyWorkbench")

    def tearDown(self):
        if Gui.Control.activeTaskDialog() is not None:
            Gui.Control.closeDialog()
        if self.document.Name in App.listDocuments():
            App.closeDocument(self.document.Name)
        shutil.rmtree(self.root, ignore_errors=True)

    def _publish_simulation(self):
        pack = get_vibescript_pack("AssemblyWorkbench")
        self.assertIsNotNone(pack)
        base = self.document.addObject("Part::Feature", "PlaybackBase")
        base.Shape = Part.makeBox(20, 20, 8)
        arm = self.document.addObject("Part::Feature", "PlaybackArm")
        arm.Shape = Part.makeBox(30, 4, 4)
        self.document.recompute()
        references = {
            "base": reference_for_target(self.document, base),
            "arm": reference_for_target(self.document, arm),
        }
        service = _Service(self.document, self.root)
        capture = {
            "pack": pack,
            "project_root": str(self.root),
            "document_name": self.document.Name,
            "document_uid": str(self.document.Uid),
            "document_revision": service.provider_document_revision(),
            "document_objects": _document_objects(self.document),
            "surface": resolve_modeling_surface("AssemblyWorkbench", "vibescript").summary(),
            "freecad_home": str(Path(App.getHomePath()).resolve()),
            "timeout_seconds": 60.0,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
        }
        create = _candidate_capture(
            capture,
            operation="create_program",
            tool_name="vibescript.assembly.create_program",
            arguments={
                "program_name": "Native Playback Contract",
                "source": _simulation_source(),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "base": _reference_schema(),
                        "arm": _reference_schema(),
                    },
                    "required": ["base", "arm"],
                    "additionalProperties": False,
                },
                "inputs": references,
                "expected_outputs": [
                    {"name": "Model", "type": "assembly"},
                    {"name": "Base", "type": "component_link"},
                    {"name": "Arm", "type": "component_link"},
                    {"name": "Hinge", "type": "joint"},
                    {"name": "Drive", "type": "motion"},
                    {"name": "Simulation", "type": "simulation"},
                    {"name": "Diagnostics", "type": "solver_diagnostics"},
                ],
            },
        )
        prepared, execution = _prepare_and_execute(create, service)
        self.assertTrue(execution.get("ok"), execution)
        validated = validate_candidate(prepared, execution)
        retain_candidate(prepared, status="validated")
        accepted = accept_candidate(
            prepared,
            publish_candidate(service, prepared, validated),
        )
        objects = {
            name: self.document.getObject(details["object_name"])
            for name, details in accepted["live_outputs"].items()
        }
        return objects

    def test_saved_vibescript_simulation_opens_as_read_only_native_player(self):
        from CommandCreateSimulation import ViewProviderSimulation, openSimulation

        objects = self._publish_simulation()
        simulation = objects["Simulation"]
        assembly = objects["Model"]
        drive = objects["Drive"]
        self.assertIsInstance(simulation.ViewObject.Proxy, ViewProviderSimulation)
        group_before = list(simulation.Group)
        parameters_before = (
            float(simulation.aTimeStart.Value),
            float(simulation.bTimeEnd.Value),
            float(simulation.cTimeStepOutput.Value),
            float(simulation.fGlobalErrorTolerance),
            int(simulation.jFramesPerSecond),
        )
        trace_before = json.loads(simulation.VibeCADSimulationTracePreview)

        panel = openSimulation(simulation, autoplay=True)
        self.assertTrue(panel.playback_only)
        self.assertIs(panel.assembly, assembly)
        self.assertGreaterEqual(assembly.numberOfFrames(), 2)
        self.assertTrue(panel.animationTimer.isActive())
        self.assertFalse(panel.form.AddButton.isEnabled())
        self.assertFalse(panel.form.RemoveButton.isEnabled())
        self.assertFalse(panel.form.TimeStartSpinBox.isEnabled())
        self.assertEqual(list(simulation.Group), [drive])

        Gui.Control.closeDialog()
        Gui.updateGui()
        self.assertIsNone(Gui.Control.activeTaskDialog())
        self.assertFalse(panel.animationTimer.isActive())
        self.assertEqual(list(simulation.Group), group_before)
        self.assertEqual(
            (
                float(simulation.aTimeStart.Value),
                float(simulation.bTimeEnd.Value),
                float(simulation.cTimeStepOutput.Value),
                float(simulation.fGlobalErrorTolerance),
                int(simulation.jFramesPerSecond),
            ),
            parameters_before,
        )
        self.assertEqual(
            json.loads(simulation.VibeCADSimulationTracePreview),
            trace_before,
        )


if __name__ == "__main__":
    unittest.main()
