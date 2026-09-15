# SPDX-License-Identifier: LGPL-2.1-or-later
"""Ordinary saved-sheet editing probe; explicitly invoked, never a default test."""

import math

import FreeCAD as App
import FreeCADGui as Gui
import Part

from SMTests import live_sheet_prompt as live


class LiveReopenedSheetPrompt(live.LiveSheetPrompt):
    case_id = "SM-P02"
    workbench = "AssemblyWorkbench"
    prompt = (
        "On this reopened sheet-metal bracket, cut a 10 mm diameter through-hole "
        "centered halfway along the bend and midway across its curved portion. "
        "The hole should continue into the panels on both sides of the bend. "
        "Keep the existing dimensions and editable history, and finish in folded view."
    )

    def wait_for(self, condition):
        while not condition():
            live._events()

    def create_input(self):
        import SheetMetalSourceOperations as Sources
        import SheetMetalOperations as Operations

        Gui.activateWorkbench(self.workbench)
        document = App.newDocument("ReopenedBracket")
        document.UndoMode = 1
        prepared = Sources.prepare(document, {
            "operation": "base_shape", "shape_type": "L-Shape", "thickness": 1.6,
            "bend_radius": 2, "width": 50, "length": 70, "height": 25,
            "origin": "0,0", "fill_gaps": True,
        }, expected_revision=Sources.capture_revision(document))
        run = Sources.start(prepared)
        self.wait_for(run.future.done)
        result = run.future.result()
        self.assertEqual(result["phase"], "ready", result)
        source = document.getObject(result["object_name"])
        face = result["source_geometry"]["reference_faces"][0]["name"]
        run = Operations.start_creation(source, face,
            expected_revision=Operations.capture_source_revision(source))
        self.wait_for(run.future.done)
        result = run.future.result()
        self.assertEqual(result["phase"], "ready", result)
        sheet = document.getObject(result["object_name"])
        self.wait_for(lambda: sheet.ViewObject.Proxy.ready)
        self.sheet_name, self.source_name = sheet.Name, source.Name
        self.source_parameters = Sources.arguments(source)
        self.original_hash = sheet.PreparedInputHash
        document.addObject("Assembly::AssemblyObject", "Assembly")
        document.recomputeAsync()
        self.wait_for(lambda: not (document.Recomputing or document.RecomputePending
                                  or document.CooperativeMutationActive))
        return document

    def open_input(self, document, filename):
        self.wait_for(document.isClosable)
        App.closeDocument(document.Name)
        document = App.openDocument(filename)
        self.wait_for(lambda: not (document.Recomputing or document.RecomputePending
                                  or document.CooperativeMutationActive))
        sheet = document.getObject(self.sheet_name)
        self.wait_for(lambda: sheet.ViewObject.Proxy.ready)
        self.assertIsNone(sheet.Proxy._geometry)
        self.assertEqual(sheet.PreparedInputHash, self.original_hash)
        Gui.getDocument(document.Name).setEdit("Assembly")
        from VibeCADEditState import active_edit_object
        self.assertIs(active_edit_object(), document.getObject("Assembly"))
        return document

    def verify_geometry(self, document, states, summary):
        import SheetMetalEditable as Editable
        import SheetMetalCutHistory as History
        import SheetMetalSourceOperations as Sources
        from SMTests import testEditGeometry

        self.assertEqual(len(states), 2, summary)
        root = document.getObject(self.sheet_name)
        cut, = [state for state in states if state is not root]
        self.assertIsInstance(cut.Proxy, History.CircleCutFeature)
        self.assertIs(cut.BaseSheet, root)
        self.assertEqual(root.SourceFace[0].Name, self.source_name)
        self.assertEqual(Sources.arguments(root.SourceFace[0]), self.source_parameters)
        self.assertEqual(root.PreparedInputHash, self.original_hash)
        self.assertAlmostEqual(float(cut.Radius), 5)
        self.assertEqual(cut.ViewObject.Proxy.mode, "folded")
        self.assertEqual(cut.VibeCADTimelineRole, "operation")
        original = Editable.get_state_geometry(root)
        edited = Editable.get_state_geometry(cut)
        for shape in (edited.folded, edited.flat):
            self.assertTrue(shape.isValid())
            self.assertEqual(len(shape.Solids), 1)
        helper = testEditGeometry.TestEditGeometry()
        _, _, center = helper.bend_center(original)
        origin, along_u, along_v = root.Proxy._frame
        actual = origin + along_u*cut.CenterU + along_v*cut.CenterV
        self.assertLess((actual-center).Length, 1e-4)
        self.assertAlmostEqual(original.flat.Volume-edited.flat.Volume,
                               math.pi*25*1.6, places=4)
        profile = Part.Face(Part.Wire(Part.makeCircle(5, center, original.normal)))
        helper.assert_cut_samples(original, edited, profile)
