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


def _camera_without_dynamic_clipping(camera):
    return "\n".join(
        line
        for line in str(camera).splitlines()
        if "nearDistance" not in line and "farDistance" not in line
    )


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
                "source": _simulation_source().replace(
                    "result = {'Model':model, 'Base':base, 'Arm':arm, "
                    "'Hinge':hinge, 'Drive':drive, 'Simulation':simulation, "
                    "'Diagnostics':diagnostics}",
                    "presentation = api.exploded_view(model, "
                    "[{'components':[arm], 'transform':[0,0,25]}], "
                    "label='Casing Off')\n"
                    "result = {'Model':model, 'Base':base, 'Arm':arm, "
                    "'Hinge':hinge, 'Drive':drive, 'Simulation':simulation, "
                    "'Presentation':presentation, 'Diagnostics':diagnostics}",
                ),
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
                    {"name": "Presentation", "type": "exploded_view"},
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
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertIsNone(Gui.getDocument(self.document.Name).getInEdit())
        self.assertGreaterEqual(assembly.numberOfFrames(), 2)
        self.assertTrue(panel.animationTimer.isActive())
        self.assertFalse(panel.form.AddButton.isEnabled())
        self.assertFalse(panel.form.RemoveButton.isEnabled())
        self.assertFalse(panel.form.TimeStartSpinBox.isEnabled())
        self.assertEqual(list(simulation.Group), [drive])
        self.assertFalse(simulation.VibeCADCollisionFree)
        self.assertEqual(simulation.VibeCADCollidingPairCount, 1)
        self.assertTrue(panel.collisionStatusLabel.isVisible())
        self.assertIn("Collision", panel.collisionStatusLabel.text())

        Gui.Control.activeTaskDialog().reject()
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

    def test_active_player_saves_baseline_closes_and_reopens(self):
        from CommandCreateSimulation import ViewProviderSimulation, openSimulation

        objects = self._publish_simulation()
        simulation = objects["Simulation"]
        assembly = objects["Model"]
        base = objects["Base"]
        arm = objects["Arm"]
        component_placements = {
            name: component.Placement.copy()
            for name, component in (("Base", base), ("Arm", arm))
        }
        trace_before = json.loads(simulation.VibeCADSimulationTracePreview)

        panel = openSimulation(simulation, autoplay=True)
        last_frame = assembly.numberOfFrames() - 1
        panel.setFrameValue(last_frame)
        self.assertEqual(panel.form.frameSlider.value(), last_frame)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertFalse(self.document.HasPendingTransaction)

        saved_path = self.root / "playback-roundtrip.FCStd"
        self.document.saveAs(str(saved_path))

        # Saving persists solved baseline placements, then resumes the exact
        # transient frame in the live player without dirtying the document.
        self.assertEqual(panel.form.frameSlider.value(), last_frame)
        self.assertFalse(Gui.getDocument(self.document.Name).Modified)
        closing_name = self.document.Name
        App.closeDocument(closing_name)
        Gui.updateGui()
        self.assertNotIn(closing_name, App.listDocuments())
        self.assertIsNone(Gui.Control.activeTaskDialog())

        self.document = App.openDocument(str(saved_path))
        Gui.activeDocument().activeView().viewAxonometric()
        stale_outputs = [
            (obj.Name, str(getattr(obj, "VibeCADStaleReason", "") or ""))
            for obj in self.document.Objects
            if str(getattr(obj, "VibeCADVibeScriptProgramId", "") or "")
            and str(getattr(obj, "VibeCADDerivedState", "") or "") == "stale"
        ]
        self.assertEqual(
            stale_outputs,
            [],
            "Restoring App::Link occurrences must not invalidate accepted programs",
        )
        reopened_simulation = next(
            obj
            for obj in self.document.Objects
            if str(getattr(obj, "VibeCADVibeScriptOutputType", "") or "")
            == "simulation"
        )
        reopened_assembly = reopened_simulation.Proxy.getAssembly(
            reopened_simulation
        )
        self.assertIsInstance(
            reopened_simulation.ViewObject.Proxy,
            ViewProviderSimulation,
        )
        self.assertEqual(
            json.loads(reopened_simulation.VibeCADSimulationTracePreview),
            trace_before,
        )
        for output_name, expected_placement in component_placements.items():
            reopened_component = next(
                obj
                for obj in self.document.Objects
                if str(
                    getattr(obj, "VibeCADVibeScriptOutputName", "") or ""
                )
                == output_name
            )
            self.assertEqual(reopened_component.Placement, expected_placement)

        reopened_panel = openSimulation(reopened_simulation, autoplay=True)
        self.assertIs(reopened_panel.assembly, reopened_assembly)
        self.assertGreaterEqual(reopened_assembly.numberOfFrames(), 2)
        self.assertTrue(reopened_panel.animationTimer.isActive())
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_player_composes_and_restores_one_explicit_presentation(self):
        from CommandCreateSimulation import openSimulation

        objects = self._publish_simulation()
        simulation = objects["Simulation"]
        presentation = objects["Presentation"]
        base = objects["Base"]
        arm = objects["Arm"]
        solved_arm_placement = arm.Placement.copy()
        base_visibility = bool(base.ViewObject.Visibility)
        step_visibility = [
            bool(step.ViewObject.Visibility) for step in presentation.Group
        ]
        applied_placements = list(presentation.Proxy._last_applied_placements)
        view = Gui.activeDocument().activeView()
        camera_before = str(view.getCamera())

        panel = openSimulation(
            simulation,
            autoplay=True,
            presentation=presentation,
            hidden_components=[base],
            camera="front",
        )
        self.assertIs(panel.presentation, presentation)
        self.assertEqual(panel.presentation_camera, camera_before)
        self.assertFalse(base.ViewObject.Visibility)
        self.assertTrue(presentation.Proxy._last_applied_placements)
        self.assertTrue(all(step.ViewObject.Visibility for step in presentation.Group))

        Gui.Control.activeTaskDialog().reject()
        Gui.updateGui()
        self.assertIsNone(Gui.Control.activeTaskDialog())
        self.assertEqual(arm.Placement, solved_arm_placement)
        self.assertEqual(bool(base.ViewObject.Visibility), base_visibility)
        self.assertEqual(
            _camera_without_dynamic_clipping(view.getCamera()),
            _camera_without_dynamic_clipping(camera_before),
        )
        self.assertEqual(
            presentation.Proxy._last_applied_placements,
            applied_placements,
        )
        self.assertEqual(
            [bool(step.ViewObject.Visibility) for step in presentation.Group],
            step_visibility,
        )

    def test_service_tool_routes_explicit_playback_state(self):
        from tool_impl.service import (
            assembly_play_simulation,
            assembly_stop_simulation,
        )

        objects = self._publish_simulation()
        presentation = objects["Presentation"]
        base = objects["Base"]
        base_visibility = bool(base.ViewObject.Visibility)

        response = assembly_play_simulation.run(
            _Service(self.document, self.root),
            reference_for_target(self.document, objects["Simulation"]),
            presentation=reference_for_target(self.document, presentation),
            hidden_components=[reference_for_target(self.document, base)],
            camera="isometric",
            autoplay=False,
            time_seconds=0.04,
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["hidden_component_count"], 1)
        self.assertEqual(response["camera"], "isometric")
        self.assertFalse(response["playing"])
        self.assertEqual(response["frame"], 3)
        self.assertAlmostEqual(response["time_seconds"], 0.04)
        self.assertEqual(response["frame_kind"], "solver_output")
        self.assertGreaterEqual(response["frame_count"], 3)
        self.assertTrue(response["collision_alert"])
        self.assertFalse(response["collision_summary"]["collision_free"])
        self.assertEqual(
            response["collision_summary"]["colliding_pair_count"],
            1,
        )
        self.assertEqual(len(response["displayed_frame_collisions"]), 1)
        self.assertFalse(base.ViewObject.Visibility)
        self.assertTrue(presentation.Proxy._last_applied_placements)

        stopped = assembly_stop_simulation.run(
            _Service(self.document, self.root)
        )
        self.assertTrue(stopped["ok"], stopped)
        self.assertTrue(stopped["stopped"])
        self.assertFalse(stopped["playing"])
        self.assertTrue(stopped["restored"])
        self.assertEqual(
            stopped["simulation"]["object_name"],
            objects["Simulation"].Name,
        )
        self.assertIsNone(Gui.Control.activeTaskDialog())
        self.assertEqual(bool(base.ViewObject.Visibility), base_visibility)

    def test_service_tool_maps_start_time_to_first_solver_frame(self):
        from tool_impl.service import (
            assembly_play_simulation,
            assembly_stop_simulation,
        )

        objects = self._publish_simulation()
        simulation = objects["Simulation"]

        response = assembly_play_simulation.run(
            _Service(self.document, self.root),
            reference_for_target(self.document, simulation),
            autoplay=False,
            time_seconds=float(simulation.aTimeStart.Value),
        )
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["frame"], 1)
        self.assertEqual(response["frame_kind"], "solver_output")
        self.assertAlmostEqual(
            response["time_seconds"],
            float(simulation.aTimeStart.Value),
        )

        stopped = assembly_stop_simulation.run(_Service(self.document, self.root))
        self.assertTrue(stopped["ok"], stopped)
        self.assertTrue(stopped["stopped"])

        already_stopped = assembly_stop_simulation.run(
            _Service(self.document, self.root)
        )
        self.assertTrue(already_stopped["ok"], already_stopped)
        self.assertFalse(already_stopped["stopped"])

    def test_incomplete_collision_analysis_alerts_without_blocking_playback(self):
        from CommandCreateSimulation import openSimulation
        from tool_impl.service import (
            assembly_play_simulation,
            assembly_stop_simulation,
        )

        objects = self._publish_simulation()
        simulation = objects["Simulation"]
        validation = json.loads(simulation.VibeCADAssemblySimulationValidation)
        warning = {
            "code": "COLLISION_ANALYSIS_INCOMPLETE",
            "stage": "simulation_collision",
            "message": "Injected geometry-engine failure",
        }
        validation["collision_summary"].update(
            {
                "status": "incomplete",
                "analysis_complete": False,
                "collision_free": False,
                "colliding_frame_count": 0,
                "colliding_pair_count": 0,
                "first_collision": None,
                "worst_collision": None,
                "pairs": [],
                "warning_count": 1,
                "warnings": [warning],
            }
        )
        simulation.VibeCADAssemblySimulationValidation = json.dumps(
            validation,
            sort_keys=True,
            separators=(",", ":"),
        )

        panel = openSimulation(
            simulation,
            autoplay=False,
            time_seconds=float(simulation.aTimeStart.Value),
        )
        self.assertIn("incomplete", panel.collisionStatusLabel.text().lower())
        self.assertIn(warning["message"], panel.collisionStatusLabel.toolTip())
        stopped = assembly_stop_simulation.run(_Service(self.document, self.root))
        self.assertTrue(stopped["ok"], stopped)
        self.assertTrue(stopped["stopped"])

        response = assembly_play_simulation.run(
            _Service(self.document, self.root),
            reference_for_target(self.document, simulation),
            autoplay=False,
            time_seconds=float(simulation.aTimeStart.Value),
        )
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["collision_alert"])
        self.assertEqual(response["collision_alert_reason"], "analysis_incomplete")
        self.assertEqual(response["collision_summary"]["warnings"], [warning])
        stopped = assembly_stop_simulation.run(_Service(self.document, self.root))
        self.assertTrue(stopped["ok"], stopped)
        self.assertTrue(stopped["stopped"])


if __name__ == "__main__":
    unittest.main()
