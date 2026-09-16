# SPDX-License-Identifier: LGPL-2.1-or-later
"""Explicit ordinary-request flange probe; excluded from default tests."""

import FreeCAD as App

from SMTests import live_sheet_prompt as live


class LiveSheetFlangePrompt(live.LiveSheetPrompt):
    case_id = "SM-P03"
    prompt = (
        "Add a 20 mm flange along the positive-X edge of this plate, with a "
        "90 degree bend and a 2 mm inside bend radius. Keep the existing hole "
        "and plate dimensions, retain editable history, and finish in flat view."
    )

    def wait_for(self, condition):
        while not condition():
            live._events()

    def close_input(self, name, uid):
        document = App.listDocuments().get(name)
        if document is not None and str(document.Uid) == uid:
            self.wait_for(document.isClosable)
            App.closeDocument(name)

    def create_input(self):
        import SheetMetalSourceOperations as Sources
        import SheetMetalOperations as Operations
        import SheetMetalEditable as Editable
        import SheetMetalHistoryOperations as Shared

        document = App.newDocument("FlangePlate")
        self.addCleanup(self.close_input, document.Name, str(document.Uid))
        document.UndoMode = 1
        prepared = Sources.prepare(document, {
            "operation": "base_shape", "shape_type": "Flat", "thickness": 1.6,
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
        root = document.getObject(result["object_name"])
        center = Editable.get_state_geometry(root).flat_face.CenterOfMass
        prepared = Shared.prepare(root, {
            "operation": "add_circle", "center": list(center), "radius": 3,
        }, expected_revision=Shared.capture_revision(root))
        run = Shared.start(prepared)
        self.wait_for(run.future.done)
        result = run.future.result()
        self.assertEqual(result["phase"], "ready", result)
        cut = document.getObject(result["object_name"])
        self.wait_for(lambda: cut.ViewObject.Proxy.ready)
        Shared.switch(cut, "flat")
        self.source_name, self.cut_name = source.Name, cut.Name
        self.source_parameters = Sources.arguments(source)
        self.original_hash = cut.PreparedInputHash
        self.original_brep = cut.Shape.exportBrepToString()
        return document

    def verify_geometry(self, document, states, summary):
        import SheetMetalSourceFeatures as Features
        import SheetMetalSourceOperations as Sources
        import SheetMetalEditable as Editable

        self.assertEqual(len(states), 3, summary)
        source, cut = document.getObject(self.source_name), document.getObject(self.cut_name)
        self.assertEqual(Sources.arguments(source), self.source_parameters)
        self.assertEqual(cut.PreparedInputHash, self.original_hash)
        self.assertEqual(cut.Shape.exportBrepToString(), self.original_brep)
        flanges = [obj for obj in document.Objects
                   if isinstance(getattr(obj, "Proxy", None), Features.Flange)]
        self.assertEqual(len(flanges), 1)
        flange, = flanges
        self.assertIs(flange.baseObject[0], cut)
        self.assertEqual(float(flange.length), 20)
        self.assertEqual(float(flange.angle), 90)
        self.assertEqual(float(flange.radius), 2)
        self.assertEqual(flange.VibeCADTimelineRole, "operation")
        self.assertEqual(flange.VibeCADTimelineEditCommand, "SheetMetal_EditSource")
        bounds = cut.Shape.BoundBox
        for name in flange.baseObject[1]:
            boundary = cut.Shape.getElement(name).BoundBox
            self.assertAlmostEqual(boundary.XMin, bounds.XMax, places=5)
            self.assertAlmostEqual(boundary.XMax, bounds.XMax, places=5)
            self.assertAlmostEqual(boundary.YLength, bounds.YLength, places=5)
        final = [obj for obj in states if getattr(obj, "SourceFace", None)
                 and obj.SourceFace[0] is flange]
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0].ViewObject.Proxy.mode, "flat")
        geometry = Editable.get_state_geometry(final[0])
        for shape in (geometry.folded, geometry.flat):
            self.assertTrue(shape.isValid())
            self.assertEqual(len(shape.Solids), 1)
        self.assertFalse(flange.Shape.isInside(App.Vector(0, 0, .8), 1e-7, True))
        self.assertTrue(flange.Shape.isInside(App.Vector(5, 0, .8), 1e-7, True))
