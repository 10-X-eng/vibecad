# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native meshes and presentation-only switching in a private GUI."""

import tempfile
import os
import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui, QtWidgets
from pivy import coin

from SMTests import testEditableSheet as fixtures


class TestDetachedMeshing(unittest.TestCase):
    def test_sheet_meshing_reads_each_surface_once(self):
        import SheetMetalPresentation as Presentation
        reads = []
        normal_reads = []
        class Face:
            def __init__(self, face):
                self.face = face
            @property
            def Surface(self):
                reads.append(self.face)
                return self.face.Surface
            def __getattr__(self, name):
                return getattr(self.face, name)
            def normalAt(self, u, v):
                normal_reads.append(self.face)
                return self.face.normalAt(u, v)
        class Shape:
            def __init__(self, shape):
                self.Faces = [Face(face) for face in shape.Faces]
                self.Edges = shape.Edges
            def copy(self):
                return self
        shape = Part.makeCylinder(8, 12)
        meshes = Presentation.prepare_pair((Shape(shape),), App.Placement(), .1,
                                           threading.Event())
        self.assertEqual(len(reads), len(shape.Faces))
        self.assertGreater(len(meshes[0].triangles), 20)
        for normal in meshes[0].normals:
            self.assertAlmostEqual(App.Vector(*normal).Length, 1.0)
        self.assertEqual(sum(isinstance(face.Surface, Part.Plane) for face in normal_reads), 2)

    def test_prepared_normals_match_oriented_faces_in_local_frame(self):
        import SheetMetalPresentation as Presentation
        placement = App.Placement(App.Vector(7, -12, 9),
                                  App.Rotation(App.Vector(1, 2, 3), 39))
        inverse = placement.inverse()
        for shape in (Part.makeBox(5, 8, 11), Part.makeCylinder(8, 12), Part.makeSphere(5)):
            shape.Placement = placement
            shape.reverse()
            mesh, = Presentation.prepare_pair((shape,), placement, .1, threading.Event())
            for triangle, face_id in zip(mesh.triangles, mesh.face_ids):
                face = shape.Faces[face_id-1]
                surface = face.Surface
                for index in triangle:
                    point = placement.multVec(App.Vector(*mesh.points[index]))
                    expected = inverse.Rotation.multVec(face.normalAt(*surface.parameter(point)))
                    self.assertLess((App.Vector(*mesh.normals[index])-expected).Length, 1e-7)

    def test_detached_meshing_retains_source_and_returns_real_triangles(self):
        shape = Part.makeSphere(20)
        with tempfile.TemporaryDirectory() as directory:
            before, after = Path(directory)/"before.bin", Path(directory)/"after.bin"
            shape.exportBinary(str(before))
            points, triangles = shape.tessellateDetached(0.1)
            shape.exportBinary(str(after))
            self.assertEqual(before.read_bytes(), after.read_bytes())
        self.assertGreater(len(triangles), 100)
        self.assertTrue(all(0 <= index < len(points) for triangle in triangles for index in triangle))
        self.assertTrue(all(abs(point.Length-20) < 1e-6 for point in points))

    def test_detached_meshing_rejects_invalid_deflection(self):
        shape = Part.makeBox(1, 2, 3)
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                shape.tessellateDetached(value)


class TestIncompleteRestore(unittest.TestCase):
    def test_missing_feature_proxy_does_not_publish_saved_geometry(self):
        import SheetMetalPresentation as Presentation
        obj = SimpleNamespace(Proxy=None, State=[], OutListRecursive=[],
                              Document=SimpleNamespace(Recomputing=False,
                                                       RecomputePending=False))
        view = Presentation.SheetViewProvider.__new__(Presentation.SheetViewProvider)
        view._object = obj
        with patch.object(view, "_alive", return_value=True), \
                patch.object(Presentation.Editable, "get_state_geometry",
                             side_effect=RuntimeError("The selected object is not a shared sheet state")):
            self.assertIsNone(view._capture())


class TestPresentation(unittest.TestCase):
    def setUp(self):
        import SheetMetalPresentation as Presentation
        self.presentation = Presentation
        self.fixture = fixtures.TestEditableSheet()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        fixture = self.fixture
        fixture.doc.removeObject(fixture.sheet.Name)
        fixture.sheet = fixture.edit(lambda: Presentation.create_presented_sheet(
            fixture.doc.BaseBend, f"Face{fixture.root}"))
        self.view = fixture.sheet.ViewObject.Proxy
        self.wait_for(lambda: self.view.ready)

    def wait_for(self, condition):
        for _ in range(10000):
            QtCore.QCoreApplication.processEvents()
            if condition():
                return
            time.sleep(.001)
        obj = self.view._object
        self.fail(f"Private presentation did not settle: pending={self.view.pending}, "
                  f"closed={self.view._closed}, error={self.view.error}; "
                  f"states={[(dep.Name, dep.State) for dep in (obj, *obj.OutListRecursive)] if obj else None}")

    def test_warmed_switch_uses_cached_nodes_without_document_changes(self):
        fixture = self.fixture
        sheet, doc = fixture.sheet, fixture.doc
        before = (doc.UndoCount, doc.isTouched(), sheet.Definition,
                  sheet.PreparedInputHash, list(sheet.State), sheet.ViewObject.DisplayMode,
                  [(obj.Name, obj.Visibility) for obj in doc.Objects if hasattr(obj, "Visibility")])
        folded, flat = sheet.Shape, sheet.FlatShape
        nodes = self.view.cached_nodes
        with patch.object(self.presentation, "prepare_pair", side_effect=AssertionError("remesh")), \
                patch.object(sheet.Proxy, "execute", side_effect=AssertionError("recompute")):
            timings = []
            for i in range(200):
                start = time.perf_counter()
                self.view.switch("flat" if i % 2 == 0 else "folded")
                timings.append(time.perf_counter()-start)
            QtCore.QCoreApplication.processEvents()
        after = (doc.UndoCount, doc.isTouched(), sheet.Definition,
                 sheet.PreparedInputHash, list(sheet.State), sheet.ViewObject.DisplayMode,
                 [(obj.Name, obj.Visibility) for obj in doc.Objects if hasattr(obj, "Visibility")])
        self.assertEqual(after, before)
        self.assertTrue(sheet.Shape.isEqual(folded))
        self.assertTrue(sheet.FlatShape.isEqual(flat))
        self.assertEqual(self.view.cached_nodes, nodes)
        self.assertEqual(self.view.mode, "folded")
        Path(os.environ["VIBECAD_TEST_OUTPUT"], "switch-timing.json").write_text(json.dumps({
            "count": len(timings), "mean_seconds": sum(timings)/len(timings),
            "maximum_seconds": max(timings), "scope": "cached switch call; excludes GPU frame time"}))

    def test_hidden_sheet_defers_meshing_until_shown(self):
        sheet = self.fixture.sheet
        sheet.ViewObject.Visibility = False
        old_nodes = self.view.cached_nodes
        with patch.object(self.presentation._workers, "submit",
                          wraps=self.presentation._workers.submit) as submit:
            self.fixture.doc.BaseBend.Length = 45
            self.fixture.recompute()
            self.fixture.settle()
            submit.assert_not_called()
            self.assertEqual(self.view.cached_nodes, old_nodes)
            sheet.ViewObject.Visibility = True
            self.wait_for(lambda: self.view.ready and self.view.cached_nodes != old_nodes)
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(self.view.current(), sheet.PreparedInputHash)

    def test_native_provider_does_not_fall_back_to_part_meshing(self):
        self.assertEqual(self.fixture.sheet.ViewObject.TypeId,
                         "PartGui::ViewProviderCachedPython")

    def test_hidden_link_source_still_refreshes_its_display(self):
        link = self.fixture.doc.addObject("App::Link", "SheetLink")
        link.setLink(self.fixture.sheet)
        self.fixture.sheet.ViewObject.Visibility = False
        old_nodes = self.view.cached_nodes
        self.fixture.doc.BaseBend.Length = 45
        self.fixture.recompute()
        self.wait_for(lambda: self.view.ready and self.view.cached_nodes != old_nodes)
        self.assertEqual(self.view.current(), self.fixture.sheet.PreparedInputHash)

    def test_hiding_sheet_cancels_inflight_mesh_and_keeps_warmed_cache(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.presentation.prepare_pair
        def delayed(*args):
            started.set()
            release.wait()
            try:
                return original(*args)
            finally:
                finished.set()
        old_nodes = self.view.cached_nodes
        with patch.object(self.presentation, "prepare_pair", side_effect=delayed):
            self.view.request(force=True)
            self.wait_for(started.is_set)
            cancelled = self.view._cancelled
            self.fixture.sheet.ViewObject.Visibility = False
            self.assertTrue(cancelled.is_set())
            self.assertFalse(self.view.pending)
            with patch.object(self.presentation, "_publish", wraps=self.presentation._publish) as publish:
                release.set()
                self.wait_for(lambda: finished.is_set() and publish.call_count > 0)
        self.assertEqual(self.view.cached_nodes, old_nodes)
        with patch.object(self.presentation._workers, "submit",
                          wraps=self.presentation._workers.submit) as submit:
            self.fixture.sheet.ViewObject.Visibility = True
            self.fixture.settle()
            submit.assert_not_called()
            self.assertEqual(self.view.current(), self.fixture.sheet.PreparedInputHash)

    def test_pending_display_has_status_until_worker_finishes(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.presentation.prepare_pair
        def delayed(*args):
            started.set()
            release.wait()
            return original(*args)
        with patch.object(self.presentation, "prepare_pair", side_effect=delayed):
            self.view.request(force=True)
            self.wait_for(started.is_set)
            label = Gui.getMainWindow().statusBar().findChild(QtWidgets.QLabel,
                                                             "SheetMetalDisplayStatus")
            self.assertIsNotNone(label)
            self.assertFalse(label.isHidden())
            self.assertIn("1", label.text())
            release.set()
            self.wait_for(lambda: not self.view.pending)
            self.assertTrue(label.isHidden())

    def test_restore_only_meshes_visible_history_and_preserves_camera(self):
        fixture = self.fixture
        hidden = fixture.edit(lambda: self.presentation.create_presented_sheet(
            fixture.doc.BaseBend, f"Face{fixture.root}", name="HiddenHistory"))
        hidden.ViewObject.Visibility = False
        active = Gui.activeDocument().activeView()
        active.setAnimationEnabled(False)
        active.viewAxonometric()
        active.fitAll()
        camera = active.getCameraNode()
        expected = (camera.position.getValue().getValue(), camera.height.getValue())
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"visible-history.FCStd")
            fixture.doc.saveAs(filename)
            fixture.settle()
            App.closeDocument(fixture.doc.Name)
            with patch.object(self.presentation._workers, "submit",
                              wraps=self.presentation._workers.submit) as submit:
                fixture.doc = App.openDocument(filename)
                fixture.settle()
                fixture.sheet = fixture.doc.getObject("EditableSheet")
                self.view = fixture.sheet.ViewObject.Proxy
                self.wait_for(lambda: self.view.ready)
                self.assertEqual(submit.call_count, 1)
            hidden = fixture.doc.getObject("HiddenHistory")
            self.assertFalse(hidden.ViewObject.Visibility)
            self.assertFalse(hidden.ViewObject.Proxy.cached_nodes)
            camera = Gui.activeDocument().activeView().getCameraNode()
            for a, b in zip(camera.position.getValue().getValue(), expected[0]):
                self.assertAlmostEqual(a, b, places=4)
            self.assertAlmostEqual(camera.height.getValue(), expected[1], places=4)
            hidden.ViewObject.Visibility = True
            self.wait_for(lambda: hidden.ViewObject.Proxy.ready)

    def test_appearance_updates_both_cached_modes_without_remeshing(self):
        native = self.fixture.sheet.ViewObject
        nodes = self.view.cached_nodes
        with patch.object(self.presentation, "prepare_pair", side_effect=AssertionError("remesh")):
            native.Transparency = 40
            appearances = native.ShapeAppearance
            appearances[0].DiffuseColor = (.2, .4, .8)
            native.ShapeAppearance = appearances
            QtCore.QCoreApplication.processEvents()
        self.assertEqual(self.view.cached_nodes, nodes)
        for node in nodes:
            material = node.getChild(0)
            self.assertAlmostEqual(material.transparency[0], .4, places=6)
            for actual, expected in zip(material.diffuseColor[0].getValue(), (.2, .4, .8)):
                self.assertAlmostEqual(actual, expected, places=6)

    def test_both_modes_render_their_cached_geometry(self):
        fixture = self.fixture
        for obj in fixture.doc.Objects:
            if hasattr(obj, "Visibility"):
                obj.Visibility = obj is fixture.sheet
        active = Gui.activeDocument().activeView()
        # Disable camera animation in this isolated capture: rendering a frame
        # mid-transition does not test the prepared sheet's final geometry.
        active.setAnimationEnabled(False)
        active.viewAxonometric()
        images = []
        for index, mode in enumerate(("folded", "flat")):
            self.view.switch(mode)
            active.fitAll()
            Gui.updateGui()
            active.redraw()
            count = coin.SoGetPrimitiveCountAction()
            count.apply(fixture.sheet.ViewObject.RootNode)
            self.assertEqual(count.getTriangleCount(), len(self.view._meshes[index].triangles))
            path = str(Path(os.environ["VIBECAD_TEST_OUTPUT"], f"{mode}.png"))
            active.saveImage(path, 800, 600, "White")
            action = coin.SoWriteAction()
            action.getOutput().openFile(str(Path(os.environ["VIBECAD_TEST_OUTPUT"], f"{mode}.iv")))
            action.apply(active.getSceneGraph())
            action.getOutput().closeFile()
            Path(os.environ["VIBECAD_TEST_OUTPUT"], f"{mode}-scene.txt").write_text(
                "\nCAMERA\n" + active.getCamera()
                + "\nVIEW\n" + str(fixture.sheet.ViewObject.Visibility)
                + " " + fixture.sheet.ViewObject.DisplayMode)
            image = QtGui.QImage(path)
            self.assertFalse(image.isNull())
            foreground = sum(image.pixelColor(x, y) != QtGui.QColor("white")
                             for x in range(100, 700, 4) for y in range(100, 500, 4))
            self.assertGreater(foreground, 500, "Sheet did not render")
            images.append(image)
        self.assertNotEqual(images[0], images[1])

    def test_saved_pair_restores_and_reprepares_without_recompute_on_switch(self):
        fixture = self.fixture
        input_hash = fixture.sheet.PreparedInputHash
        old_view = self.view
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory)/"presented.FCStd")
            fixture.doc.saveAs(filename)
            fixture.settle()
            App.closeDocument(fixture.doc.Name)
            fixture.doc = App.openDocument(filename)
            fixture.settle()
            fixture.sheet = fixture.doc.getObject("EditableSheet")
            self.view = fixture.sheet.ViewObject.Proxy
            self.wait_for(lambda: old_view._closed and self.view.ready)
            self.assertEqual(fixture.sheet.ViewObject.TypeId, "PartGui::ViewProviderCachedPython")
            with self.assertRaises(RuntimeError):
                self.view.current()
            self.view.switch("flat")
            fixture.sheet.touch()
            fixture.recompute()
            self.wait_for(lambda: self.view.ready and self.view._published[1] is not None)
            self.assertEqual(self.view.current(), input_hash)

    def test_deleting_sheet_cancels_its_inflight_mesh(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.presentation.prepare_pair
        def delayed(*args):
            started.set()
            release.wait()
            try:
                return original(*args)
            finally:
                finished.set()
        with patch.object(self.presentation, "prepare_pair", side_effect=delayed):
            self.view.request(force=True)
            self.wait_for(started.is_set)
            self.fixture.doc.removeObject(self.fixture.sheet.Name)
            self.wait_for(lambda: self.view._closed)
            release.set()
            self.wait_for(finished.is_set)
        self.assertEqual(self.view.cached_nodes, ())
        self.assertIsNone(self.view._switch)

    def test_cached_mesh_uses_the_native_object_placement_once(self):
        fixture = self.fixture
        old_nodes = self.view.cached_nodes
        fixture.doc.BaseBend.Placement = App.Placement(
            App.Vector(17, -29, 11), App.Rotation(App.Vector(1, 2, 3), 37))
        fixture.recompute()
        self.wait_for(lambda: self.view.ready and self.view.cached_nodes != old_nodes)
        for mode, shape in (("folded", fixture.sheet.Shape), ("flat", fixture.sheet.FlatShape)):
            self.view.switch(mode)
            bounds = coin.SoGetBoundingBoxAction(coin.SbViewportRegion(800, 600))
            bounds.apply(fixture.sheet.ViewObject.RootNode)
            actual = bounds.getBoundingBox()
            expected = shape.BoundBox
            for actual_point, expected_point in (
                    (actual.getMin().getValue(), (expected.XMin, expected.YMin, expected.ZMin)),
                    (actual.getMax().getValue(), (expected.XMax, expected.YMax, expected.ZMax))):
                for a, b in zip(actual_point, expected_point):
                    self.assertAlmostEqual(a, b, delta=.1)

    def test_late_worker_cannot_replace_a_newer_published_pair(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.presentation.prepare_pair
        def delayed(*args):
            started.set()
            release.wait()
            try:
                return original(*args)
            finally:
                finished.set()
        with patch.object(self.presentation, "prepare_pair", side_effect=delayed):
            self.view.request(force=True)
            self.wait_for(started.is_set)
        old_nodes = self.view.cached_nodes
        self.fixture.doc.BaseBend.Length = 45
        self.fixture.recompute()
        self.wait_for(lambda: self.view.ready and self.view.cached_nodes != old_nodes)
        new_nodes = self.view.cached_nodes
        with patch.object(self.presentation, "_publish", wraps=self.presentation._publish) as publish:
            release.set()
            self.wait_for(lambda: finished.is_set() and publish.call_count > 0)
        self.assertEqual(self.view.cached_nodes, new_nodes)
        self.assertEqual(self.view.current(), self.fixture.sheet.PreparedInputHash)

    def test_mesh_work_runs_off_gui_and_old_generation_is_rejected(self):
        fixture = self.fixture
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.presentation.prepare_pair
        threads = []
        def delayed(*args):
            threads.append(threading.get_ident())
            started.set()
            release.wait()
            return original(*args)
        old_nodes = self.view.cached_nodes
        with patch.object(self.presentation, "prepare_pair", side_effect=delayed):
            self.view.request(force=True)
            self.wait_for(started.is_set)
            fixture.doc.BaseBend.Length = 35
            with self.assertRaises(RuntimeError):
                self.view.current()
            release.set()
            self.wait_for(lambda: not self.view.pending)
        self.assertEqual(self.view.cached_nodes, old_nodes)
        self.assertTrue(all(thread != threading.get_ident() for thread in threads))
        fixture.recompute()
        self.wait_for(lambda: self.view.ready and self.view.cached_nodes != old_nodes)
        self.assertEqual(self.view.current(), fixture.sheet.PreparedInputHash)
