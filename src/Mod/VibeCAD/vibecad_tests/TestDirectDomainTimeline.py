# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI timeline contracts for retained direct domain tools."""

from __future__ import annotations

import os
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtGui

from tool_impl.service import (
    assembly_create_assembly,
    assembly_insert_component,
    cam_add_tool,
    cam_create_job,
    fem_create_analysis,
    techdraw_create_page,
)


def _timeline(document):
    return document.getObject("VibeCADTimeline")


def _assert_semantic_block(test, document, root, resources):
    timeline = _timeline(document)
    test.assertIsNotNone(timeline)
    operations = list(timeline.Operations)
    root_index = operations.index(root)
    test.assertEqual(
        operations[root_index - len(resources): root_index + 1],
        [*resources, root],
    )
    test.assertEqual(root.VibeCADTimelineRole, "operation")
    for resource in resources:
        test.assertEqual(resource.VibeCADTimelineRole, "resource")
        test.assertIs(resource.VibeCADTimelineOwner, root)
        test.assertEqual(
            resource.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )


def _owned_resources(document, root):
    timeline = _timeline(document)
    result = []
    for candidate in list(timeline.Operations):
        current = candidate
        visited = set()
        while (
            current is not None
            and current not in visited
            and getattr(current, "VibeCADTimelineRole", "") == "resource"
        ):
            visited.add(current)
            current = getattr(current, "VibeCADTimelineOwner", None)
        if current is root and candidate is not root:
            result.append(candidate)
    return result


def _update_gui():
    Gui.updateGui()
    Gui.updateGui()


def _timeline_button(object_name):
    for _attempt in range(100):
        button = Gui.getMainWindow().findChild(QtGui.QToolButton, object_name)
        if button is not None:
            return button
        _update_gui()
    raise AssertionError(f"Timeline button is unavailable: {object_name}")


def _move_before_block(controller, root, resources):
    _timeline_button("VibeCADFeatureTimelineEnd").click()
    _update_gui()
    operations = list(controller.Operations)
    block_begin = operations.index(root) - len(resources)
    previous = _timeline_button("VibeCADFeatureTimelinePrevious")
    for _attempt in range(len(operations) + 1):
        if controller.Position <= block_begin:
            break
        previous.click()
        _update_gui()
    if controller.Position != block_begin:
        raise AssertionError(
            f"Timeline stopped at {controller.Position}, expected {block_begin}"
        )


class _Service:
    def __init__(self, document):
        self.document = document

    def _active_document(self):
        return self.document

    def _assembly_objects(self):
        return [
            obj
            for obj in self.document.Objects
            if obj.isDerivedFrom("Assembly::AssemblyObject")
        ]


class DirectDomainTimelineTest(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        self.document = App.newDocument("DirectDomainTimeline")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self.service = _Service(self.document)
        self.saved_path = None

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except RuntimeError:
                pass
        if self.document.Name in App.listDocuments():
            App.closeDocument(self.document.Name)
        if self.saved_path and os.path.exists(self.saved_path):
            os.remove(self.saved_path)

    def test_techdraw_page_and_template_are_one_durable_step(self):
        Gui.activateWorkbench("TechDrawWorkbench")
        response = techdraw_create_page.run(
            self.service,
            sheet_size="a4_landscape",
            label="Direct Drawing",
        )
        self.assertTrue(response.get("ok"), response)
        page = self.document.getObject(response["page"])
        template = self.document.getObject(response["template"])
        _assert_semantic_block(self, self.document, page, [template])

        page_name = page.Name
        template_name = template.Name
        self.document.undo()
        self.assertIsNone(self.document.getObject(page_name))
        self.assertIsNone(self.document.getObject(template_name))
        self.document.redo()
        page = self.document.getObject(page_name)
        template = self.document.getObject(template_name)
        _assert_semantic_block(self, self.document, page, [template])

        temp = tempfile.NamedTemporaryFile(suffix=".FCStd", delete=False)
        temp.close()
        self.saved_path = temp.name
        self.document.saveAs(self.saved_path)
        App.closeDocument(self.document.Name)

        self.document = App.openDocument(self.saved_path)
        self.service.document = self.document
        page = self.document.getObject(page_name)
        template = self.document.getObject(template_name)
        _assert_semantic_block(self, self.document, page, [template])

    def test_fem_analysis_owns_its_default_solver(self):
        Gui.activateWorkbench("FemWorkbench")
        response = fem_create_analysis.run(
            self.service,
            label="Direct Analysis",
            analysis_type="static",
        )
        self.assertTrue(response.get("ok"), response)
        analysis = self.document.getObject(response["analysis"])
        solver = self.document.getObject(response["solver"])
        self.assertIn(solver, list(analysis.Group))
        _assert_semantic_block(self, self.document, analysis, [solver])

        analysis_name = analysis.Name
        solver_name = solver.Name
        self.document.undo()
        self.assertIsNone(self.document.getObject(analysis_name))
        self.assertIsNone(self.document.getObject(solver_name))
        self.document.redo()
        analysis = self.document.getObject(analysis_name)
        solver = self.document.getObject(solver_name)
        _assert_semantic_block(self, self.document, analysis, [solver])

    def test_assembly_creation_and_component_insertion_are_distinct_steps(self):
        Gui.activateWorkbench("AssemblyWorkbench")
        response = assembly_create_assembly.run(
            self.service,
            label="Direct Assembly",
        )
        self.assertTrue(response.get("ok"), response)
        created = response["transaction"]["result"]
        assembly = self.document.getObject(created["assembly"])
        joint_group = self.document.getObject(created["joint_group"])
        _assert_semantic_block(self, self.document, assembly, [])
        self.assertNotIn(joint_group, list(_timeline(self.document).Operations))

        source = self.document.addObject("Part::Feature", "DirectComponent")
        source.Shape = Part.makeBox(8.0, 6.0, 4.0)
        source.Label = "Direct Component"
        self.document.recompute()
        insert = assembly_insert_component.run(
            self.service,
            assembly_name=assembly.Name,
            source_object_name=source.Name,
            label="Direct Component 1",
            local_position={"x": 0.0, "y": 0.0, "z": 0.0},
        )
        self.assertTrue(insert.get("ok"), insert)
        component = self.document.getObject(
            insert["mutation"]["component"]
        )
        _assert_semantic_block(self, self.document, component, [])
        self.assertIs(component.LinkedObject, source)

        component_name = component.Name
        assembly_name = assembly.Name
        source_name = source.Name
        self.document.undo()
        self.assertIsNone(self.document.getObject(component_name))
        self.assertIsNotNone(self.document.getObject(assembly_name))
        self.assertIsNotNone(self.document.getObject(source_name))
        self.document.redo()
        component = self.document.getObject(component_name)
        _assert_semantic_block(self, self.document, component, [])

    def test_cam_job_and_added_tool_remain_one_owned_job_block(self):
        Gui.activateWorkbench("CAMWorkbench")
        source = self.document.addObject("Part::Feature", "MachinedSolid")
        source.Shape = Part.makeBox(30.0, 20.0, 8.0)
        source.Label = "Machined Solid"
        hidden_source = self.document.addObject(
            "Part::Feature",
            "InitiallyHiddenMachinedSolid",
        )
        hidden_source.Shape = Part.makeBox(12.0, 10.0, 5.0)
        hidden_source.Label = "Initially Hidden Machined Solid"
        hidden_source.ViewObject.Visibility = False
        self.document.recompute()

        response = cam_create_job.run(
            self.service,
            label="Direct CAM Job",
            model_object_names=[source.Name, hidden_source.Name],
            stock_margins_mm={"x": 1.0, "y": 1.0, "z": 1.0},
        )
        self.assertTrue(response.get("ok"), response)
        created = response["transaction"]["result"]
        job = self.document.getObject(created["job"])
        initial_resources = _owned_resources(self.document, job)
        _assert_semantic_block(
            self,
            self.document,
            job,
            initial_resources,
        )
        self.assertEqual(
            list(job.VibeCADTimelineReplacedInputs),
            [source],
        )
        self.assertFalse(source.ViewObject.Visibility)
        self.assertFalse(hidden_source.ViewObject.Visibility)
        _move_before_block(
            _timeline(self.document),
            job,
            initial_resources,
        )
        self.assertTrue(source.ViewObject.Visibility)
        self.assertFalse(hidden_source.ViewObject.Visibility)
        _timeline_button("VibeCADFeatureTimelineEnd").click()
        _update_gui()
        self.assertFalse(source.ViewObject.Visibility)
        self.assertFalse(hidden_source.ViewObject.Visibility)

        tool_response = cam_add_tool.run(
            self.service,
            job_name=job.Name,
            label="6 mm Endmill",
            tool_geometry={
                "shape": "endmill",
                "diameter_mm": 6.0,
                "length_mm": 50.0,
                "flutes": 2,
                "cutting_edge_height_mm": 18.0,
                "shank_diameter_mm": 6.0,
            },
            tool_number=1,
            spindle_rpm=12_000.0,
            horizontal_feed_mm_per_min=600.0,
            vertical_feed_mm_per_min=200.0,
        )
        self.assertTrue(tool_response.get("ok"), tool_response)
        tool_result = tool_response["transaction"]["result"]
        controller_name = tool_result["tool_controller"]
        tool_bit_name = tool_result["tool_bit"]
        final_resources = _owned_resources(self.document, job)
        _assert_semantic_block(
            self,
            self.document,
            job,
            final_resources,
        )
        self.assertIn(
            self.document.getObject(controller_name),
            final_resources,
        )
        self.assertIn(
            self.document.getObject(tool_bit_name),
            final_resources,
        )

        job_name = job.Name
        self.document.undo()
        self.assertIsNotNone(self.document.getObject(job_name))
        self.assertIsNone(self.document.getObject(controller_name))
        self.assertIsNone(self.document.getObject(tool_bit_name))
