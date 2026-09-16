# SPDX-License-Identifier: LGPL-2.1-or-later
"""Exact surface correspondence behind picks on the cached sheet display."""

import unittest
from concurrent.futures import ThreadPoolExecutor

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartGui
from pivy import coin

from SMTests import testEditGeometry, testPresentation


def face_at(shape, point):
    for index, face in enumerate(shape.Faces):
        if face.distToShape(Part.Vertex(point))[0] < 1e-6:
            return index
    raise AssertionError("Fixture point is not on a result face")


class TestSurfaceMapping(unittest.TestCase):
    def test_folded_picks_on_both_skins_map_to_the_same_definition(self):
        for reverse in (False, True):
            geometry = testEditGeometry.TestEditGeometry().prepare(multiple=True, reverse=reverse)
            for region in geometry.mapping.regions:
                face = region._face
                u0, u1, v0, v1 = face.ParameterRange
                u, v = (u0+u1)/2, (v0+v1)/2
                folded = face.valueAt(u, v)
                for offset in (0, geometry.mapping.thickness):
                    point = folded-face.normalAt(u, v)*offset
                    index = face_at(geometry.folded, point)
                    selected, mapped, flat = geometry.map_surface_point("folded", index, point)
                    self.assertEqual(selected, region.face_index)
                    self.assertLess((mapped-folded).Length, 1e-6)
                    self.assertLess((flat-region.to_flat(folded)).Length, 1e-6)

    def test_flat_picks_on_both_skins_recover_the_folded_bend(self):
        fixture = testEditGeometry.TestEditGeometry()
        geometry = fixture.prepare()
        region, folded, flat = fixture.bend_center(geometry)
        for offset in (0, geometry.mapping.thickness):
            point = flat-geometry.normal*offset
            selected, mapped, developed = geometry.map_surface_point(
                "flat", face_at(geometry.flat, point), point)
            self.assertEqual(selected, region.face_index)
            self.assertLess((mapped-folded).Length, 1e-6)
            self.assertLess((developed-flat).Length, 1e-6)

    def test_cut_walls_and_removed_material_are_not_sheet_skin_picks(self):
        fixture = testEditGeometry.TestEditGeometry()
        geometry = fixture.prepare()
        _, _, center = fixture.bend_center(geometry)
        edited = geometry.cut(fixture.circle(center, geometry.normal, 5))
        walls = []
        for index, face in enumerate(edited.flat.Faces):
            u0, u1, v0, v1 = face.ParameterRange
            u, v = (u0+u1)/2, (v0+v1)/2
            point = face.valueAt(u, v)
            if (face.distToShape(Part.Vertex(point))[0] < 1e-6
                    and abs(face.normalAt(u, v).dot(geometry.normal)) < .5):
                walls.append(index)
                with self.assertRaises(ValueError):
                    edited.map_surface_point("flat", index, point)
        self.assertTrue(walls)
        for index in range(len(edited.flat.Faces)):
            with self.assertRaises(ValueError):
                edited.map_surface_point("flat", index, center)


class TestPickReader(unittest.TestCase):
    def test_native_reader_rejects_a_missing_pick(self):
        with self.assertRaises(TypeError):
            PartGui.getPickedFaceIndex(None)

    def test_native_reader_rejects_worker_thread_access(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(PartGui.getPickedFaceIndex, None)
            with self.assertRaisesRegex(RuntimeError, "GUI thread"):
                future.result()


class TestRenderedSelection(unittest.TestCase):
    def setUp(self):
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.view = self.fixture.view
        self.sheet = self.fixture.fixture.sheet

    def ray(self, point, normal, root=None):
        action = coin.SoRayPickAction(coin.SbViewportRegion(800, 600))
        action.setRay(coin.SbVec3f(*(point+normal*10)), coin.SbVec3f(*(-normal)))
        action.apply(self.sheet.ViewObject.RootNode if root is None else root)
        self.assertIsNotNone(action.getPickedPoint())
        return action

    def test_real_rendered_bend_pick_projects_to_the_exact_surface(self):
        fixture = self.fixture.fixture
        region, folded, flat = fixture.bend_pick()
        face = fixture.doc.BaseBend.Shape.Faces[region]
        normal = face.normalAt(*face.Surface.parameter(folded))
        action = self.ray(folded, normal)
        picked = self.view.pick(action.getPickedPoint())
        self.assertEqual(picked.region, region)
        self.assertLess((App.Vector(*picked.folded)-folded).Length, 1e-5)
        self.assertLess((App.Vector(*picked.flat)-flat).Length, 1e-5)
        self.assertIs(self.view.validate_pick(picked), picked)
        self.view.switch("flat")
        self.assertIs(self.view.validate_pick(picked), picked)

    def test_rendered_folded_pick_edits_both_solids_in_one_undo_entry(self):
        import SheetMetalEditable as Editable
        fixture = self.fixture.fixture
        region, folded, _ = fixture.bend_pick()
        face = fixture.doc.BaseBend.Shape.Faces[region]
        action = self.ray(folded, face.normalAt(*face.Surface.parameter(folded)))
        picked = self.view.pick(action.getPickedPoint())
        before = self.sheet.Shape.Volume, self.sheet.FlatShape.Volume
        undo = fixture.doc.UndoCount
        fixture.edit(lambda: Editable.add_circle_cut(
            self.sheet, App.Vector(*picked.folded), 5, representation="folded",
            region=picked.region, expected_input_hash=picked.input_hash))
        fixture.assert_valid_pair()
        self.assertEqual(fixture.doc.UndoCount, undo+1)
        self.assertLess(self.sheet.Shape.Volume, before[0])
        self.assertLess(self.sheet.FlatShape.Volume, before[1])
        fixture.doc.undo()
        fixture.recompute()
        self.assertAlmostEqual(self.sheet.Shape.Volume, before[0], places=6)
        self.assertAlmostEqual(self.sheet.FlatShape.Volume, before[1], places=6)

    def test_flat_opposite_skin_pick_and_stale_revision_rejection(self):
        import SheetMetalEditable as Editable
        geometry = Editable.get_prepared(self.sheet)
        _, folded, flat = self.fixture.fixture.bend_pick()
        self.view.switch("flat")
        point = flat-geometry.normal*geometry.mapping.thickness
        action = self.ray(point, -geometry.normal)
        picked = self.view.pick(action.getPickedPoint())
        self.assertLess((App.Vector(*picked.folded)-folded).Length, 1e-5)
        self.assertLess((App.Vector(*picked.flat)-flat).Length, 1e-5)
        self.fixture.fixture.doc.BaseBend.Length = 35
        with self.assertRaises(RuntimeError):
            self.view.validate_pick(picked)
        with self.assertRaises(RuntimeError):
            self.view.pick(action.getPickedPoint())
        self.fixture.fixture.recompute()
        self.fixture.wait_for(lambda: self.view.ready)
        with self.assertRaises(RuntimeError):
            self.view.validate_pick(picked)

    def test_canvas_pick_uses_the_exact_view_and_can_create_a_shared_cut(self):
        import SheetMetalEditable as Editable
        fixture = self.fixture.fixture
        geometry = Editable.get_prepared(self.sheet)
        self.view.switch("flat")
        active = Gui.getDocument(fixture.doc.Name).activeView()
        active.setAnimationEnabled(False)
        # Look down onto the selected flat skin, including a vertical base face.
        active.setCameraOrientation(App.Rotation(App.Vector(0, 0, 1), geometry.normal))
        for obj in fixture.doc.Objects:
            if hasattr(obj, "Visibility"):
                obj.Visibility = obj is self.sheet
        active.fitAll()
        active.redraw()
        _, _, flat = fixture.bend_pick()
        position = active.getPointOnScreen(flat)
        picked = self.view.pick_screen(active, position)
        before = self.sheet.Shape.Volume
        self.view.validate_pick(picked)
        fixture.edit(lambda: Editable.add_circle_cut(
            self.sheet, App.Vector(*picked.flat), 5, expected_input_hash=picked.input_hash))
        fixture.assert_valid_pair()
        self.assertLess(self.sheet.Shape.Volume, before)
        self.assertLess(self.sheet.FlatShape.Volume, before)

    def test_canvas_pick_does_not_reach_through_another_visible_object(self):
        import SheetMetalEditable as Editable
        fixture = self.fixture.fixture
        geometry = Editable.get_prepared(self.sheet)
        self.view.switch("flat")
        active = Gui.getDocument(fixture.doc.Name).activeView()
        active.setAnimationEnabled(False)
        active.setCameraOrientation(App.Rotation(App.Vector(0, 0, 1), geometry.normal))
        for obj in fixture.doc.Objects:
            if hasattr(obj, "Visibility"):
                obj.Visibility = obj is self.sheet
        _, _, flat = fixture.bend_pick()
        blocker = fixture.doc.addObject("Part::Feature", "Occluder")
        blocker.Shape = Part.Face(Part.Wire(Part.makeCircle(
            8, flat+geometry.normal*5, geometry.normal)))
        blocker.Visibility = True
        fixture.recompute()
        self.fixture.wait_for(lambda: self.view.ready)
        active.fitAll()
        position = active.getPointOnScreen(flat)
        def blocker_visible():
            active.redraw()
            info = active.getObjectInfo(position)
            return info is not None and info.get("Object") == blocker.Name
        self.fixture.wait_for(blocker_visible)
        with self.assertRaises(ValueError):
            self.view.pick_screen(active, position)

    def test_foreign_document_pick_and_record_are_rejected(self):
        other = testPresentation.TestPresentation()
        self.addCleanup(other.doCleanups)
        other.setUp()
        region, folded, _ = self.fixture.fixture.bend_pick()
        face = self.fixture.fixture.doc.BaseBend.Shape.Faces[region]
        action = self.ray(folded, face.normalAt(*face.Surface.parameter(folded)))
        picked = self.view.pick(action.getPickedPoint())
        with self.assertRaises(ValueError):
            other.view.pick(action.getPickedPoint())
        with self.assertRaises(RuntimeError):
            other.view.validate_pick(picked)
        with self.assertRaises(ValueError):
            self.view.pick_screen(Gui.activeDocument().activeView(), (100, 100))

    def test_nested_container_motion_keeps_picks_in_the_shared_model_frame(self):
        fixture = self.fixture.fixture
        container = fixture.doc.addObject("App::Part", "Container")
        container.addObject(fixture.doc.BaseBend)
        fixture.recompute()
        self.fixture.wait_for(lambda: self.view.ready)
        self.assertIs(self.sheet.getParentGeoFeatureGroup(), container)
        generation = self.view._generation
        container.Placement = App.Placement(App.Vector(31, -27, 12),
                                            App.Rotation(App.Vector(1, 2, 3), 37))
        fixture.settle()
        region, folded, flat = fixture.bend_pick()
        face = fixture.doc.BaseBend.Shape.Faces[region]
        normal = face.normalAt(*face.Surface.parameter(folded))
        transform = container.getGlobalPlacement()
        action = self.ray(transform.multVec(folded), transform.Rotation.multVec(normal),
                          container.ViewObject.RootNode)
        picked = self.view.pick(action.getPickedPoint())
        self.assertLess((App.Vector(*picked.folded)-folded).Length, 1e-5)
        self.assertLess((App.Vector(*picked.flat)-flat).Length, 1e-5)
        self.assertEqual(self.view._generation, generation)
