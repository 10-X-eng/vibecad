# SPDX-License-Identifier: LGPL-2.1-or-later
"""Explicit relief-and-fold request starting without sketches; not a default test."""

import FreeCAD as App
from SMTests import live_sheet_flange_prompt


class LiveSheetTabPrompt(live_sheet_flange_prompt.LiveSheetFlangePrompt):
    case_id = 'SM-P05'
    prompt = (
        'Create an internal folded tab in this plate. In the flat sheet, center a '
        'U-shaped slot on the plate: its outside width and depth are both 20 mm, '
        'the slot is 1 mm wide, and its mouth opens toward positive Y. '
        'Make a bend line across the tab 5 mm below that mouth and fold the tab '
        '90 degrees with a 2 mm inside radius. Keep the surrounding plate flat '
        'and its outside dimensions unchanged. Retain editable relief-cut and '
        'bend-line sketches and sheet history. Finish in flat view.'
    )

    def create_input(self):
        import SheetMetalSourceOperations as Sources
        import SheetMetalOperations as Operations
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared
        doc = App.newDocument('InternalTabPlate')
        self.addCleanup(self.close_input, doc.Name, str(doc.Uid))
        doc.UndoMode = 1
        request = Sources.prepare(doc, {
            'operation': 'base_shape', 'shape_type': 'Flat', 'thickness': 1.6,
            'bend_radius': 2, 'width': 50, 'length': 70, 'height': 25,
            'origin': '0,0', 'fill_gaps': True,
        }, expected_revision=Sources.capture_revision(doc))
        run = Sources.start(request)
        self.wait_for(run.future.done)
        result = run.future.result()
        self.assertEqual(result['phase'], 'ready', result)
        source = doc.getObject(result['object_name'])
        run = Operations.start_creation(source, result['source_geometry']['reference_faces'][0]['name'],
            expected_revision=Operations.capture_source_revision(source))
        self.wait_for(run.future.done)
        result = run.future.result()
        self.assertEqual(result['phase'], 'ready', result)
        root = doc.getObject(result['object_name'])
        self.wait_for(lambda: root.ViewObject.Proxy.ready)
        Shared.switch(root, 'flat')
        self.source_name, self.root_name = source.Name, root.Name
        self.source_arguments = Sources.arguments(source)
        self.parent_brep = root.Shape.exportBrepToString()
        self.stock_volume = root.FlatShape.Volume
        self.stock_size = sorted((root.FlatShape.BoundBox.XLength, root.FlatShape.BoundBox.YLength))
        self.stock_center = root.Shape.CenterOfMass
        return doc

    def verify_geometry(self, document, states, summary):
        import SheetMetalEditable as Editable
        import SheetMetalSourceFeatures as Features
        import SheetMetalSourceOperations as Sources
        import SheetMetalCutHistory as History
        root = document.getObject(self.root_name)
        self.assertEqual(root.Shape.exportBrepToString(), self.parent_brep)
        self.assertEqual(Sources.arguments(document.getObject(self.source_name)), self.source_arguments)
        fold, = [obj for obj in document.Objects if isinstance(getattr(obj, 'Proxy', None), Features.Fold)]
        cut = fold.baseObject[0]
        self.assertIsInstance(cut.Proxy, History.ProfileCutFeature)
        self.assertIs(History._chain(cut)[0], root)
        self.assertEqual(History.get_profile(cut).TypeId, 'Sketcher::SketchObject')
        self.assertEqual(fold.BendLine.TypeId, 'Sketcher::SketchObject')
        self.assertEqual(len(fold.BendLine.Shape.Edges), 1)
        self.assertEqual(float(fold.angle), 90)
        self.assertEqual(float(fold.radius), 2)
        final, = [obj for obj in states if getattr(obj, 'SourceFace', None) and obj.SourceFace[0] is fold]
        self.assertEqual(final.ViewObject.Proxy.mode, 'flat')
        geometry = Editable.get_state_geometry(final)
        for shape in (geometry.folded, geometry.flat):
            self.assertTrue(shape.isValid())
            self.assertEqual(len(shape.Solids), 1)
        # Two 20x1 legs plus the non-overlapping 18x1 free end.
        self.assertAlmostEqual(geometry.flat.Volume, self.stock_volume - 58 * 1.6, places=3)
        for actual, expected in zip(sorted((geometry.flat.BoundBox.XLength, geometry.flat.BoundBox.YLength)), self.stock_size):
            self.assertAlmostEqual(actual, expected, places=4)
        self.assertGreater(geometry.folded.BoundBox.ZLength, 8)
        for x, y in ((20, 15), (-20, 15), (20, -15), (-20, -15)):
            point = self.stock_center + App.Vector(x, y, 0)
            self.assertTrue(root.Shape.isInside(point, 1e-6, True))
            self.assertTrue(geometry.folded.isInside(point, 1e-6, True), 'The surrounding plate moved')
        self.assertEqual(fold.VibeCADTimelineEditCommand, 'SheetMetal_EditSource')
        self.assertEqual(cut.VibeCADTimelineRole, 'operation')
