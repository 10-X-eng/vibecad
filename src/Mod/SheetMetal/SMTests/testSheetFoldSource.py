# SPDX-License-Identifier: LGPL-2.1-or-later
"""Internal folds retain upstream sketches, exact source history and flat stock."""

import unittest
import os
from pathlib import Path

import FreeCAD as App
import Part

from SMTests import testPresentation, testSheetSourceCreation


class TestSheetFoldSource(unittest.TestCase):
    def setUp(self):
        self.fixture = testSheetSourceCreation.TestSheetSourceCreation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.doc = self.fixture.doc

    def fold_input(self):
        source, _ = self.fixture.flange_input()
        face_name, face = next((f"Face{i}", face) for i, face in enumerate(source.Shape.Faces, 1)
                              if isinstance(face.Surface, Part.Plane) and face.normalAt(0, 0).z > .9)
        bounds = face.BoundBox

        def create_sketch():
            sketch = self.doc.addObject("Sketcher::SketchObject", "BendLine")
            sketch.Placement.Base = App.Vector(0, 0, bounds.ZMax)
            x = bounds.Center.x + 10
            sketch.addGeometry(Part.LineSegment(App.Vector(x, bounds.YMin-5, 0),
                                               App.Vector(x, bounds.YMax+5, 0)), False)
            return sketch

        sketch = self.fixture.model.edit(create_sketch)
        return source, sketch, {"operation": "fold_from_sketch", "object_name": source.Name,
            "subelements": [face_name], "sketch_name": sketch.Name, "bend_radius": 2,
            "bend_angle": 90, "invert": False, "invert_bend": False,
            "k_factor": .5, "position": "middle"}

    def test_internal_fold_keeps_inputs_and_updates_shared_geometry_with_undo(self):
        import SheetMetalSourceFeatures as Features
        import SheetMetalSourceOperations as Sources
        import SheetMetalOperations as Operations
        import SheetMetalEditable as Editable
        from SheetMetalFoldCmd import SMFoldWall

        source, sketch, arguments = self.fold_input()
        before = source.Shape.exportBrepToString(), sketch.Shape.exportBrepToString()
        undo = self.doc.UndoCount
        prepared = Sources.prepare(self.doc, arguments, expected_revision=Sources.capture_revision(self.doc))
        run = Sources.start(prepared)
        self.assertFalse(self.doc.HasPendingTransaction)
        result = self.fixture.wait(run.future)
        self.assertEqual(result["phase"], "ready", result)
        fold = self.doc.getObject(result["object_name"])
        self.assertIsInstance(fold.Proxy, SMFoldWall)
        self.assertIs(fold.baseObject[0], source)
        self.assertIs(fold.BendLine, sketch)
        self.assertEqual(self.doc.UndoCount, undo+1)
        self.assertEqual(Sources.arguments(fold), arguments)
        self.assertEqual(fold.VibeCADTimelineEditCommand, "SheetMetal_EditSource")
        self.assertEqual(Features.nominal_thickness(fold), 1.6)
        self.assertEqual((source.Shape.exportBrepToString(), sketch.Shape.exportBrepToString()), before)
        self.assertTrue(fold.Shape.isValid())
        self.assertGreater(fold.Shape.BoundBox.ZLength, 10)

        face = result["source_geometry"]["reference_faces"][0]["name"]
        run = Operations.start_creation(fold, face,
            expected_revision=Operations.capture_source_revision(fold))
        result = self.fixture.wait(run.future)
        self.assertEqual(result["phase"], "ready", result)
        sheet = self.doc.getObject(result["object_name"])
        geometry = Editable.get_state_geometry(sheet)
        self.assertTrue(geometry.flat.isValid())
        self.assertEqual(len(geometry.flat.Solids), 1)
        flat_size = sorted((geometry.flat.BoundBox.XLength, geometry.flat.BoundBox.YLength))
        source_size = sorted((source.Shape.BoundBox.XLength, source.Shape.BoundBox.YLength))
        for actual, expected in zip(flat_size, source_size):
            self.assertAlmostEqual(actual, expected, places=4)
        self.assertEqual(sheet.KFactor, .5)
        fingerprint = sheet.PreparedInputHash
        run = Sources.update(fold, {"bend_angle": 60},
                             expected_revision=Operations.capture_source_revision(fold))
        result = self.fixture.wait(run.future)
        self.assertEqual(result["phase"], "ready", result)
        self.assertNotEqual(sheet.PreparedInputHash, fingerprint)
        self.assertTrue(Editable.get_state_geometry(sheet).folded.isValid())
        self.doc.undo()
        self.fixture.model.recompute()
        self.assertEqual(float(fold.angle), 90)
        self.assertEqual(sheet.PreparedInputHash, fingerprint)
        self.assertEqual((source.Shape.exportBrepToString(), sketch.Shape.exportBrepToString()), before)
        import SheetMetalHistoryOperations as Shared
        presentation = testPresentation.TestPresentation()
        presentation.view = sheet.ViewObject.Proxy
        presentation.wait_for(lambda: presentation.view.ready)
        Shared.switch(sheet, "flat")
        prepared = Operations.prepare(sheet,
            {"operation": "set_material", "material": "Test steel", "k_factor": .3},
            expected_revision=Operations.capture_revision(sheet))
        run = Operations.start(prepared)
        result = self.fixture.wait(run.future)
        self.assertEqual(result["phase"], "ready", result)
        self.assertEqual(float(fold.kfactor), .3)
        self.assertEqual(sheet.KFactor, .3)
        self.assertEqual(sheet.ViewObject.Proxy.mode, "flat")
        geometry = Editable.get_state_geometry(sheet)
        for actual, expected in zip(sorted((geometry.flat.BoundBox.XLength, geometry.flat.BoundBox.YLength)),
                                    source_size):
            self.assertAlmostEqual(actual, expected, places=4)
        self.doc.saveAs(str(Path(os.environ["VIBECAD_TEST_OUTPUT"]) / "internal-fold.FCStd"))

    def test_upstream_input_hiding_uses_its_document_not_the_active_one(self):
        import SheetMetalTools
        source, _ = self.fixture.flange_input()
        source.Visibility = True
        foreign = App.newDocument("OtherFoldDocument")
        def close_foreign():
            self.fixture.model.settle(foreign)
            App.closeDocument(foreign.Name)
        self.addCleanup(close_foreign)
        shadow = foreign.addObject("Part::Feature", source.Name)
        shadow.Visibility = True
        self.assertIs(App.ActiveDocument, foreign)
        SheetMetalTools.smHideObjects(source)
        self.assertTrue(shadow.Visibility, "The active document's namesake must remain unchanged")
        self.assertFalse(source.Visibility)
        App.setActiveDocument(self.doc.Name)

    def test_internal_tab_uses_relief_history_without_bending_the_surrounding_plate(self):
        import SheetMetalEditable as Editable
        import SheetMetalOperations as Operations
        import SheetMetalSourceOperations as Sources
        import SheetMetalCutHistory as History

        source, bend_line, arguments = self.fold_input()
        run = Operations.start_creation(source, arguments["subelements"][0],
            expected_revision=Operations.capture_source_revision(source))
        ready = self.fixture.wait(run.future)
        self.assertEqual(ready["phase"], "ready", ready)
        root = self.doc.getObject(ready["object_name"])
        profile = self.fixture.model.edit(lambda: Editable.create_cut_sketch(root))
        center = profile.Placement.inverse().multVec(Editable.get_state_geometry(root).flat_face.CenterOfMass)
        outline = [(-10, 5), (-10, -15), (10, -15), (10, 5),
                   (9, 5), (9, -14), (-9, -14), (-9, 5)]
        points = [center + App.Vector(x, y, 0) for x, y in outline]

        def draw():
            profile.addGeometry([Part.LineSegment(a, b)
                                 for a, b in zip(points, points[1:]+points[:1])], False)
            bend_line.delGeometry(0)
            bend_line.Placement = profile.Placement
            bend_line.addGeometry(Part.LineSegment(center+App.Vector(-9.5, 0, 0),
                                                  center+App.Vector(9.5, 0, 0)), False)

        self.fixture.model.edit(draw)
        cut = self.fixture.model.edit(lambda: History.create_profile_step(root, profile))
        parent = cut.Shape.exportBrepToString(), cut.FlatShape.exportBrepToString(), cut.OperationId
        face = next(f"Face{i}" for i, face in enumerate(cut.Shape.Faces, 1)
                    if isinstance(face.Surface, Part.Plane) and face.normalAt(0, 0).z > .9)
        arguments.update(object_name=cut.Name, subelements=[face], k_factor=root.KFactor)
        run = Sources.start(Sources.prepare(self.doc, arguments,
            expected_revision=Sources.capture_revision(self.doc)))
        ready = self.fixture.wait(run.future)
        self.assertEqual(ready["phase"], "ready", ready)
        fold = self.doc.getObject(ready["object_name"])
        self.assertEqual((cut.Shape.exportBrepToString(), cut.FlatShape.exportBrepToString(), cut.OperationId), parent)
        self.assertIs(fold.baseObject[0], cut)
        self.assertIs(History.get_profile(cut), profile)
        self.assertTrue(fold.Shape.isValid())
        self.assertEqual(len(fold.Shape.Solids), 1)
        outside_tab = profile.Placement.multVec(center+App.Vector(25, 15, -.8))
        inside_tab = profile.Placement.multVec(center+App.Vector(0, -10, -.8))
        self.assertTrue(cut.Shape.isInside(outside_tab, 1e-6, True))
        self.assertTrue(cut.Shape.isInside(inside_tab, 1e-6, True))
        self.assertTrue(fold.Shape.isInside(outside_tab, 1e-6, True))
        self.assertFalse(fold.Shape.isInside(inside_tab, 1e-6, True))
        run = Operations.start_creation(fold, ready["source_geometry"]["reference_faces"][0]["name"],
            expected_revision=Operations.capture_source_revision(fold))
        ready = self.fixture.wait(run.future)
        self.assertEqual(ready["phase"], "ready", ready)
        sheet = self.doc.getObject(ready["object_name"])
        flat = Editable.get_state_geometry(sheet).flat
        self.assertTrue(flat.isValid())
        self.assertAlmostEqual(flat.Volume, cut.FlatShape.Volume, places=4)
        self.doc.saveAs(str(Path(os.environ["VIBECAD_TEST_OUTPUT"]) / "internal-tab.FCStd"))

    def test_invalid_fold_inputs_leave_no_objects_or_undo(self):
        import SheetMetalSourceOperations as Sources
        source, sketch, arguments = self.fold_input()
        before = tuple(self.doc.Objects), self.doc.UndoCount
        for change in ({"sketch_name": source.Name}, {"sketch_name": "MissingSketch"},
                       {"subelements": ["Edge1"]}, {"subelements": ["Face1", "Face2"]},
                       {"bend_angle": 181}, {"bend_angle": 0}, {"k_factor": -1},
                       {"k_factor": 1.1}, {"invert_bend": "false"}):
            with self.subTest(change=change), self.assertRaises((ValueError, RuntimeError)):
                Sources.prepare(self.doc, {**arguments, **change},
                                expected_revision=Sources.capture_revision(self.doc))
            self.assertEqual((tuple(self.doc.Objects), self.doc.UndoCount), before)

    def test_native_fold_creation_uses_owned_async_dispatch(self):
        from VibeCADNativeSheetMetalCreateRuntime import NativeSheetMetalCreateRuntime
        source, sketch, arguments = self.fold_input()
        context = self.fixture.context
        ticket = context.state.begin_call(self.doc.Uid, "sheet_metal.create")
        self.addCleanup(lambda: context.state.cancel_mutation(ticket))
        future = NativeSheetMetalCreateRuntime(context).execute_async(arguments, ticket=ticket)
        self.assertFalse(future.done())
        ready = self.fixture.wait(future)
        self.assertEqual(ready["phase"], "ready", ready)
        fold = self.doc.getObject(ready["object_name"])
        self.assertIs(fold.baseObject[0], source)
        self.assertIs(fold.BendLine, sketch)
        self.assertTrue(fold.Shape.isValid())
        self.assertIsNotNone(context.state.completed_mutation_receipt(ticket))

    def test_construction_bend_line_reports_the_exact_sketch_to_repair(self):
        import SheetMetalSourceOperations as Sources
        source, sketch, arguments = self.fold_input()
        self.fixture.model.edit(lambda: sketch.toggleConstruction(0))
        before = tuple(self.doc.Objects), self.doc.UndoCount, Sources.capture_revision(self.doc)
        with self.assertRaises(ValueError) as caught:
            Sources.prepare(self.doc, arguments, expected_revision=Sources.capture_revision(self.doc))
        message = str(caught.exception)
        self.assertIn(sketch.Name, message)
        self.assertIn("construction", message.lower())
        self.assertIn("turn off", message.lower())
        self.assertEqual((tuple(self.doc.Objects), self.doc.UndoCount, Sources.capture_revision(self.doc)), before)
        self.assertTrue(sketch.getConstruction(0))

    def test_fold_rejects_a_skin_off_the_bend_sketch_plane_before_mutation(self):
        import SheetMetalSourceOperations as Sources
        source, sketch, arguments = self.fold_input()
        wrong_face = next(f"Face{i}" for i, face in enumerate(source.Shape.Faces, 1)
                          if isinstance(face.Surface, Part.Plane) and face.normalAt(0, 0).z < -.9)
        before = tuple(self.doc.Objects), self.doc.UndoCount, Sources.capture_revision(self.doc)
        with self.assertRaisesRegex(ValueError, "bend sketch plane") as caught:
            Sources.prepare(self.doc, {**arguments, "subelements": [wrong_face]},
                            expected_revision=Sources.capture_revision(self.doc))
        self.assertIn(arguments["subelements"][0], str(caught.exception))
        self.assertEqual((tuple(self.doc.Objects), self.doc.UndoCount, Sources.capture_revision(self.doc)), before)

    def test_native_fold_result_supplies_valid_shared_state_followup(self):
        from VibeCADNativeSheetMetalCreateRuntime import NativeSheetMetalCreateRuntime
        from VibeCADProvider import _provider_visible_tool_result
        import SheetMetalEditable as Editable
        source, sketch, arguments = self.fold_input()
        context = self.fixture.context
        runtime = NativeSheetMetalCreateRuntime(context)
        ticket = context.state.begin_call(self.doc.Uid, "sheet_metal.create")
        self.addCleanup(lambda ticket=ticket: context.state.cancel_mutation(ticket))
        ready = self.fixture.wait(runtime.execute_async(arguments, ticket=ticket))
        visible = _provider_visible_tool_result({**ready, "ok": True, "_vibecad_native_result": True},
                                                tool_name="sheet_metal.create")
        self.assertFalse(visible["shared_state_created"])
        next_step = visible["next_step"]
        self.assertEqual(next_step["tool"], "sheet_metal.create")
        self.assertEqual(next_step["arguments"]["object_name"], ready["object_name"])
        self.assertIn(next_step["arguments"]["reference_face"],
                      [face["name"] for face in ready["source_geometry"]["reference_faces"]])
        ticket = context.state.begin_call(self.doc.Uid, "sheet_metal.create")
        self.addCleanup(lambda ticket=ticket: context.state.cancel_mutation(ticket))
        shared = self.fixture.wait(runtime.execute_async(next_step["arguments"], ticket=ticket))
        self.assertEqual(shared["phase"], "ready", shared)
        sheet = self.doc.getObject(shared["object_name"])
        self.assertEqual(sheet.SourceFace[0].Name, ready["object_name"])
        self.assertTrue(Editable.get_state_geometry(sheet).flat.isValid())

    def test_fold_preflight_uses_transformed_source_and_sketch_planes(self):
        import SheetMetalSourceOperations as Sources
        source, sketch, arguments = self.fold_input()
        transform = App.Placement(App.Vector(100, 40, 50), App.Rotation(App.Vector(0, 1, 0), 20))
        def move():
            source.Placement = transform
            sketch.Placement = transform.multiply(sketch.Placement)
        self.fixture.model.edit(move)
        prepared = Sources.prepare(self.doc, arguments, expected_revision=Sources.capture_revision(self.doc))
        run = Sources.start(prepared)
        ready = self.fixture.wait(run.future)
        self.assertEqual(ready["phase"], "ready", ready)
        self.assertTrue(self.doc.getObject(ready["object_name"]).Shape.isValid())
