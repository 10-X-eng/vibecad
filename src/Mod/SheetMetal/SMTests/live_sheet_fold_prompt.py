# SPDX-License-Identifier: LGPL-2.1-or-later
"""Explicit ordinary-request internal fold probe; excluded from default tests."""

import FreeCAD as App
import Part

from SMTests import live_sheet_flange_prompt


class LiveSheetFoldPrompt(live_sheet_flange_prompt.LiveSheetFlangePrompt):
    case_id = "SM-P04"
    prompt = (
        "Fold this plate 90 degrees along the existing bend-line sketch, using a "
        "2 mm inside radius. Keep the hole and original stock dimensions, retain "
        "editable history, and finish in flat view."
    )

    def create_input(self):
        from VibeCADNativeTransaction import _OwnedDocumentTransaction

        document = super().create_input()
        cut = document.getObject(self.cut_name)
        face = next(face for face in cut.Shape.Faces
                    if isinstance(face.Surface, Part.Plane) and face.normalAt(0, 0).z > .9)
        bounds = face.BoundBox
        transaction = _OwnedDocumentTransaction(document, "Create bend line fixture")
        try:
            sketch = document.addObject("Sketcher::SketchObject", "BendLine")
            sketch.Placement.Base = App.Vector(0, 0, bounds.ZMax)
            x = bounds.Center.x + 10
            sketch.addGeometry(Part.LineSegment(App.Vector(x, bounds.YMin-5, 0),
                                               App.Vector(x, bounds.YMax+5, 0)), False)
            transaction.commit()
        except Exception:
            transaction.abort()
            raise
        document.recomputeAsync()
        self.wait_for(lambda: not document.Recomputing and not document.RecomputePending
                      and document.isClosable())
        self.bend_sketch_name = sketch.Name
        self.bend_sketch_brep = sketch.Shape.exportBrepToString()
        self.blank_volume = cut.FlatShape.Volume
        self.blank_size = sorted((cut.FlatShape.BoundBox.XLength, cut.FlatShape.BoundBox.YLength))
        return document

    def verify_geometry(self, document, states, summary):
        import SheetMetalSourceFeatures as Features
        import SheetMetalSourceOperations as Sources
        import SheetMetalEditable as Editable

        source, cut = document.getObject(self.source_name), document.getObject(self.cut_name)
        sketch = document.getObject(self.bend_sketch_name)
        self.assertEqual(Sources.arguments(source), self.source_parameters)
        self.assertEqual(cut.PreparedInputHash, self.original_hash)
        self.assertEqual(cut.Shape.exportBrepToString(), self.original_brep)
        self.assertEqual(sketch.Shape.exportBrepToString(), self.bend_sketch_brep)
        self.assertEqual(len(states), 3, summary)
        fold, = [obj for obj in document.Objects
                 if isinstance(getattr(obj, "Proxy", None), Features.Fold)]
        self.assertIs(fold.baseObject[0], cut)
        self.assertIs(fold.BendLine, sketch)
        self.assertEqual(float(fold.angle), 90)
        self.assertEqual(float(fold.radius), 2)
        self.assertEqual(fold.VibeCADTimelineRole, "operation")
        self.assertEqual(fold.VibeCADTimelineEditCommand, "SheetMetal_EditSource")
        self.assertGreater(fold.Shape.BoundBox.ZLength, 10)
        final, = [obj for obj in states if getattr(obj, "SourceFace", None)
                  and obj.SourceFace[0] is fold]
        self.assertEqual(final.ViewObject.Proxy.mode, "flat")
        geometry = Editable.get_state_geometry(final)
        for shape in (geometry.folded, geometry.flat):
            self.assertTrue(shape.isValid())
            self.assertEqual(len(shape.Solids), 1)
        self.assertAlmostEqual(geometry.flat.Volume, self.blank_volume, places=4)
        for actual, expected in zip(sorted((geometry.flat.BoundBox.XLength, geometry.flat.BoundBox.YLength)),
                                    self.blank_size):
            self.assertAlmostEqual(actual, expected, places=4)
