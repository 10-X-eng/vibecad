# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI coverage for the native VibeCAD Section View command."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui
try:
    from PySide import QtWidgets
except ImportError:  # pragma: no cover - PySide1 compatibility
    QtWidgets = QtGui
import VibeCADSectionView


class TestVibeCADSectionViewCommand(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        self.document = App.newDocument("VibeCADSectionViewCommand")
        Gui.activateView("Gui::View3DInventor", True)
        box = self.document.addObject("Part::Box", "SectionBox")
        box.Length = 40.0
        box.Width = 20.0
        box.Height = 10.0
        self.assertTrue(self._wait_until(self.document.isClosable))
        self.document.recompute()
        view = Gui.ActiveDocument.ActiveView
        if VibeCADSectionView.is_section_view_active(view):
            VibeCADSectionView.set_section_view(False, view=view, document=self.document)
        self._wait_until(lambda: not VibeCADSectionView.is_section_view_active(view))

    def tearDown(self):
        view = None
        try:
            view = Gui.ActiveDocument.ActiveView
        except Exception:
            view = None
        try:
            import VibeCADSectionViewGui

            VibeCADSectionViewGui.close_section_view_dialog()
        except Exception:
            pass
        if view is not None and VibeCADSectionView.is_section_view_active(view):
            VibeCADSectionView.set_section_view(False, view=view, document=self.document)
        self._process_events()
        if "VibeCADSectionViewCommand" in App.listDocuments():
            self.assertTrue(self._wait_until(self.document.isClosable))
            App.closeDocument("VibeCADSectionViewCommand")
        self._process_events()

    @staticmethod
    def _process_events(wait_ms=20):
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(wait_ms, loop.quit)
        loop.exec()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def _wait_until(self, predicate, timeout_ms=5000):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            self._process_events()
            if predicate():
                return True
        return False

    @staticmethod
    def _command_action():
        actions = Gui.Command.get("VibeCAD_SectionView").getAction()
        if not actions:
            return None
        return actions[0]

    @staticmethod
    def _named_scene_nodes(view, name):
        scene = view.getSceneGraph()
        children = tuple(scene.getChildren()) if scene is not None else ()
        found = []
        for node in children:
            getter = getattr(node, "getName", None)
            if not callable(getter):
                continue
            try:
                node_name = str(getter())
            except Exception:
                continue
            if node_name == name:
                found.append(node)
        return tuple(found)

    @staticmethod
    def _scene_clip_nodes(view):
        scene = view.getSceneGraph()
        children = tuple(scene.getChildren()) if scene is not None else ()
        return tuple(
            node
            for node in children
            if type(node).__name__ in {"SoClipPlane", "SoClipPlaneManip"}
        )

    @staticmethod
    def _section_dialog():
        main = Gui.getMainWindow()
        if main is not None:
            found = main.findChild(QtWidgets.QWidget, "VibeCADSectionViewDialog")
            if found is not None:
                return found
        application = QtGui.QApplication.instance()
        if application is None:
            return None
        for widget in application.allWidgets():
            if widget.objectName() == "VibeCADSectionViewDialog":
                return widget
        return None

    def test_display_bounds_include_visible_meshes_but_exclude_grid(self):
        import Mesh
        import MeshGui
        from pivy import coin

        mesh = self.document.addObject("Mesh::Feature", "SectionMesh")
        mesh.Mesh = Mesh.createBox(20, 20, 20)
        mesh.Placement.Base = App.Vector(100, 0, 0)
        self.document.recompute()
        self.assertTrue(self._wait_until(self.document.isClosable))
        view = Gui.ActiveDocument.ActiveView
        scene = view.getSceneGraph()
        helper = coin.SoCube()
        helper.width = helper.height = helper.depth = 45000
        scene.addChild(helper)
        try:
            self._process_events()
            bounds = VibeCADSectionView._render_bounds(view)
            self.assertIsNotNone(bounds)
            self.assertAlmostEqual(bounds.xmin, 0, places=4)
            self.assertAlmostEqual(bounds.xmax, 110, places=4)
            mesh.ViewObject.Visibility = False
            self._process_events()
            hidden = VibeCADSectionView._render_bounds(view)
            self.assertAlmostEqual(hidden.xmax, 40, places=4)
        finally:
            scene.removeChild(helper)

    def test_native_command_toggles_clip_plane_action_and_scene(self):
        command_name = "VibeCAD_SectionView"
        self.assertTrue(Gui.isCommandActive(command_name))
        action = self._command_action()
        self.assertIsNotNone(action)
        self.assertTrue(action.isCheckable())
        self.assertFalse(action.isChecked())

        view = Gui.ActiveDocument.ActiveView
        Gui.runCommand(command_name, 0)
        self.assertTrue(
            self._wait_until(lambda: VibeCADSectionView.is_section_view_active(view)),
            "Section View did not enable a clipping plane in the active 3D view.",
        )
        self.assertTrue(Gui.isCommandActive(command_name))
        Gui.Command.get(command_name).getAction()[0]
        self.assertTrue(view.hasClippingPlane())
        clip_nodes = self._scene_clip_nodes(view)
        self.assertTrue(clip_nodes or view.hasClippingPlane())
        self.assertFalse(
            any(type(node).__name__ == "SoClipPlaneManip" for node in clip_nodes),
            "Section View must not use the Coin clip manipulator.",
        )
        self.assertTrue(
            self._wait_until(lambda: self._named_scene_nodes(view, "VibeCADSectionCapOverlay")),
            "Section View did not add hatched solid cap faces.",
        )
        cap_nodes = self._named_scene_nodes(view, "VibeCADSectionCapOverlay")
        self.assertTrue(cap_nodes)
        cap_children = tuple(cap_nodes[0].getChildren()) if hasattr(cap_nodes[0], "getChildren") else ()
        child_types = {type(child).__name__ for child in cap_children}
        self.assertIn("SoIndexedFaceSet", child_types)
        self.assertIn("SoIndexedLineSet", child_types)
        self.assertTrue(
            self._wait_until(
                lambda: self._named_scene_nodes(view, "VibeCADSectionDragger")
            ),
            "Section View did not add a datum-origin drag gizmo.",
        )
        dialog = self._section_dialog()
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        dock = Gui.getMainWindow().findChild(
            QtWidgets.QDockWidget, "VibeCADSectionViewDock"
        )
        self.assertIsNotNone(dock)
        self.assertTrue(dock.isVisible())
        self.assertEqual(
            Gui.getMainWindow().dockWidgetArea(dock), QtCore.Qt.RightDockWidgetArea
        )
        self.assertIsNotNone(dialog.findChild(QtWidgets.QLabel, "sectionPlaneLabel"))
        self.assertIsNotNone(dialog.findChild(QtWidgets.QDoubleSpinBox, "sectionOffset"))
        self.assertIsNotNone(dialog.findChild(QtWidgets.QPushButton, "sectionFlip"))
        self.assertIsNone(dialog.findChild(QtWidgets.QRadioButton, "planeFront"))
        self.assertIsNone(dialog.findChild(QtWidgets.QDoubleSpinBox, "sectionPitch"))

        VibeCADSectionView.configure_section_view(plane="top")
        import VibeCADSectionViewGui

        VibeCADSectionViewGui.refresh_section_view_dialog()
        self._process_events()
        self.assertEqual(VibeCADSectionView.current_section_view_settings().plane, "top")
        label = dialog.findChild(QtWidgets.QLabel, "sectionPlaneLabel")
        self.assertIn("Top", label.text())
        self.assertTrue(view.hasClippingPlane())

        Gui.runCommand(command_name, 0)
        self.assertTrue(
            self._wait_until(
                lambda: not VibeCADSectionView.is_section_view_active(view)
            ),
            "Section View did not remove the clipping plane.",
        )
        self.assertTrue(Gui.isCommandActive(command_name))
        self.assertFalse(view.hasClippingPlane())
        self.assertTrue(
            self._wait_until(lambda: self._section_dialog() is None),
            "Section View did not close its editor dialog.",
        )

    def test_native_command_chooses_the_plane_from_the_live_camera(self):
        command_name = "VibeCAD_SectionView"
        view = Gui.ActiveDocument.ActiveView

        for orient, expected in (
            (view.viewFront, "front"),
            (view.viewTop, "top"),
            (view.viewRight, "right"),
        ):
            orient()
            self._process_events()
            look = VibeCADSectionView._view_look_direction(view)
            self.assertIsNotNone(
                look,
                f"Section View could not read the live {expected} camera direction.",
            )
            Gui.runCommand(command_name, 0)
            self.assertTrue(
                self._wait_until(lambda: VibeCADSectionView.is_section_view_active(view)),
                f"Section View did not enable from the {expected} camera.",
            )
            self.assertEqual(
                VibeCADSectionView.current_section_view_settings().plane,
                expected,
                f"Live camera direction was {look!r}.",
            )
            self.assertTrue(
                self._wait_until(
                    lambda: self._named_scene_nodes(view, "VibeCADSectionCapOverlay")
                ),
                f"Section View did not publish its {expected} cut cap.",
            )
            Gui.runCommand(command_name, 0)
            self.assertTrue(
                self._wait_until(
                    lambda: not VibeCADSectionView.is_section_view_active(view)
                )
            )

    def test_close_with_section_enabled_releases_poll_and_scene(self):
        view = Gui.ActiveDocument.ActiveView
        VibeCADSectionView.set_section_view(True, view=view, document=self.document)
        self.assertIsNotNone(VibeCADSectionView._poll_timer)
        self.assertIsNotNone(VibeCADSectionView._dragger_node)
        self.assertTrue(self._wait_until(self.document.isClosable))
        App.closeDocument(self.document.Name)
        self._process_events()
        self.assertIsNone(VibeCADSectionView._poll_timer)
        self.assertIsNone(VibeCADSectionView._dragger_node)
        self.assertIsNone(VibeCADSectionView._overlay_node)
        self.assertIsNone(VibeCADSectionView._cap_node)
        self.assertIsNone(VibeCADSectionView._dragger_document)
        VibeCADSectionView._poll_dragger()

    def test_section_updates_preserve_document_geometry_and_undo_history(self):
        self.assertTrue(self._wait_until(self.document.isClosable))
        box = self.document.getObject("SectionBox")
        before = (box.Placement.toMatrix().A, box.Shape.Volume, self.document.UndoCount)
        view = Gui.ActiveDocument.ActiveView
        VibeCADSectionView.set_section_view(True, view=view, document=self.document)
        for offset in range(6):
            VibeCADSectionView.configure_section_view(
                offset=float(offset), view=view, document=self.document,
                preview=True, sync_dragger=False,
            )
        VibeCADSectionView.configure_section_view(view=view, document=self.document)
        self.assertTrue(self._wait_until(
            lambda: VibeCADSectionView._cap_worker is not None
            and not VibeCADSectionView._cap_worker._running
        ))
        after = (box.Placement.toMatrix().A, box.Shape.Volume, self.document.UndoCount)
        self.assertEqual(after, before)

    def test_native_rendered_snapshot_survives_document_close(self):
        self.assertTrue(self._wait_until(self.document.isClosable))
        box = self.document.getObject("SectionBox")
        snapshot = box.ViewObject.getRenderedShapeSnapshot()
        self.assertFalse(snapshot.isNull())
        self.assertAlmostEqual(snapshot.Volume, 8000.0)
        App.closeDocument(self.document.Name)
        self._process_events()
        self.assertAlmostEqual(snapshot.Volume, 8000.0)

    def test_rendered_instances_follow_link_display_transform(self):
        self.assertTrue(self._wait_until(self.document.isClosable))
        box = self.document.getObject("SectionBox")
        link = self.document.addObject("App::Link", "SectionInstance")
        link.setLink(box)
        link.Placement = App.Placement(App.Vector(100, 20, 30), App.Rotation())
        box.Visibility = False
        self.document.recompute()
        self.assertTrue(self._wait_until(self.document.isClosable))
        displayed = App.Placement(App.Vector(200, 40, 60), App.Rotation())
        link.ViewObject.setTransformation(displayed)
        view = Gui.ActiveDocument.ActiveView
        instances = [item for item in VibeCADSectionView._rendered_section_snapshot_steps(view)
                     if item is not None]
        self.assertEqual(len(instances), 1)
        shape, transform = instances[0]
        self.assertAlmostEqual(shape.Volume, 8000.0)
        self.assertEqual(transform.multVec(App.Vector()), displayed.Base)
        self.assertEqual(link.Placement.Base, App.Vector(100, 20, 30))
        bounds = VibeCADSectionView._render_bounds(view)
        self.assertAlmostEqual(bounds.xmin, 200, places=3)
        self.assertAlmostEqual(bounds.xmax, 240, places=3)
        self.assertAlmostEqual(bounds.ymin, 40, places=3)
        self.assertAlmostEqual(bounds.zmin, 60, places=3)

    def test_native_section_controller_delivers_faces_on_gui(self):
        import PartGui

        self.assertTrue(self._wait_until(self.document.isClosable))
        snapshot = self.document.getObject("SectionBox").ViewObject.getRenderedShapeSnapshot()
        controller = PartGui.createSectionFaceController()
        received = []

        def completed(faces, error):
            self.assertEqual(QtCore.QThread.currentThread(), Gui.getMainWindow().thread())
            received.append((faces, error))

        PartGui.requestSectionFaces(controller, [(snapshot, App.Matrix())],
                                    App.Vector(0, 0, 5), App.Vector(0, 0, 1), completed)
        self.assertTrue(self._wait_until(lambda: bool(received)))
        faces, error = received[0]
        self.assertEqual(error, "")
        self.assertEqual(len(faces), 1)
        self.assertAlmostEqual(faces[0].Area, 800.0)
        PartGui.cancelSectionFaces(controller)
        from threading import Thread

        errors = []

        def cancel_off_owner():
            try:
                PartGui.cancelSectionFaces(controller)
            except RuntimeError as error:
                errors.append(str(error))

        thread = Thread(target=cancel_off_owner)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIn("GUI owner", errors[0])

    def test_native_section_display_is_read_in_bounded_chunks(self):
        import PartGui

        self.assertTrue(self._wait_until(self.document.isClosable))
        snapshot = self.document.getObject("SectionBox").ViewObject.getRenderedShapeSnapshot()
        controller = PartGui.createSectionFaceController()
        received = []
        PartGui.requestSectionDisplay(controller, [(snapshot, App.Matrix())],
                                      App.Vector(0, 0, 5), App.Vector(0, 0, 1), 0.01,
                                      lambda geometry, error: received.append((geometry, error)))
        self.assertTrue(self._wait_until(lambda: bool(received)))
        geometry, error = received[0]
        self.assertEqual(error, "")
        triangles, hatch, outlines = PartGui.sectionDisplaySizes(geometry)
        self.assertGreater(triangles, 0)
        self.assertGreater(hatch, 256)
        self.assertGreater(outlines, 0)
        chunk = PartGui.readSectionDisplay(geometry, "hatch", 0, 10000)
        self.assertEqual(len(chunk), 256)
        following = PartGui.readSectionDisplay(geometry, "hatch", 256, 256)
        self.assertTrue(following)
        self.assertNotEqual(chunk, following)
        self.assertAlmostEqual(chunk[0][0][2], 4.95)
        deferred = []
        self.assertTrue(PartGui._deferSectionDisplay(lambda: deferred.append(True)))
        self.assertFalse(deferred)
        self.assertTrue(self._wait_until(lambda: bool(deferred)))

    def test_close_during_cap_computation_discards_worker_result(self):
        import PartGui
        from unittest.mock import patch

        submitted = []
        original = PartGui.requestSectionMeshDisplay

        def close_after_submit(*args):
            original(*args)
            submitted.append(True)
            # The native completion is queued to the owner, so close before
            # its delivery without blocking the GUI or any worker.
            App.closeDocument(self.document.Name)

        self.assertTrue(self._wait_until(self.document.isClosable))
        with patch.object(PartGui, "requestSectionMeshDisplay", close_after_submit):
            view = Gui.ActiveDocument.ActiveView
            VibeCADSectionView.set_section_view(True, view=view, document=self.document)
            self.assertTrue(self._wait_until(lambda: bool(submitted)))
            self.assertTrue(self._wait_until(
                lambda: not VibeCADSectionView._cap_worker._running
            ))
        self.assertIsNone(VibeCADSectionView._cap_node)
        self.assertIsNone(VibeCADSectionView._dragger_node)
        self.assertIsNone(VibeCADSectionView._poll_timer)

    def test_hidden_infinite_datum_does_not_expand_offset_slider(self):
        import VibeCADSectionViewGui as panel
        plane = self.document.addObject("App::Plane", "InfiniteDatum")
        plane.Placement = App.Placement(App.Vector(), App.Rotation(App.Vector(1, 0, 0), 90))
        plane.Visibility = False
        self.assertTrue(self._wait_until(self.document.isClosable))
        self.document.recompute()
        self.assertTrue(self._wait_until(self.document.isClosable))
        self.assertGreater(abs(plane.Shape.BoundBox.ZMax), 1e90)
        view = Gui.ActiveDocument.ActiveView
        VibeCADSectionView.reset_section_view_settings()
        VibeCADSectionView.set_section_view(True, view=view, document=self.document)
        dialog = panel.show_section_view_dialog()
        self.assertIsNotNone(dialog)
        self.assertAlmostEqual(dialog.offset_spin.minimum(), -5., places=3)
        self.assertAlmostEqual(dialog.offset_spin.maximum(), 5., places=3)
        dialog.offset_slider.setValue(500)
        self.assertFalse(dialog._updating)
        self.assertAlmostEqual(VibeCADSectionView.current_section_view_settings().offset, 2.5, places=3)

    def test_mesh_snapshot_keeps_caps_available_after_document_close(self):
        import PartGui

        snapshot = self.document.getObject("SectionBox").ViewObject.getRenderedMeshSnapshot()
        self.assertIsNotNone(snapshot)
        controller = PartGui.createSectionFaceController()
        received = []
        App.closeDocument(self.document.Name)
        PartGui.requestSectionMeshDisplay(controller, [(snapshot, App.Matrix())],
                                          App.Vector(0, 0, 5), App.Vector(0, 0, 1), 1.0,
                                          lambda geometry, error: received.append((geometry, error)))
        self.assertTrue(self._wait_until(lambda: bool(received)))
        geometry, error = received[0]
        self.assertEqual(error, "")
        triangles, hatch, outlines = PartGui.sectionDisplaySizes(geometry)
        self.assertGreater(triangles, 0)
        self.assertGreater(hatch, 0)
        self.assertGreater(outlines, 0)

    def test_panel_can_hide_plane_without_rebuilding_cut(self):
        import VibeCADSectionViewGui as panel
        from unittest.mock import patch

        view = Gui.ActiveDocument.ActiveView
        VibeCADSectionView.reset_section_view_settings()
        VibeCADSectionView.set_section_view(True, view=view, document=self.document)
        self.assertTrue(self._wait_until(lambda: not VibeCADSectionView._cap_worker._running))
        cap = VibeCADSectionView._cap_node
        dialog = panel.show_section_view_dialog()
        checkbox = dialog.findChild(QtWidgets.QCheckBox, "sectionShowPlane")
        self.assertIsNotNone(checkbox)
        with patch.object(VibeCADSectionView, "_schedule_cap_build", side_effect=AssertionError("Rebuilt cut")):
            checkbox.setChecked(False)
            self.assertIsNone(VibeCADSectionView._overlay_node)
            self.assertIs(VibeCADSectionView._cap_node, cap)
            self.assertTrue(view.hasClippingPlane())
            checkbox.setChecked(True)
            self.assertIsNotNone(VibeCADSectionView._overlay_node)
            self.assertIs(VibeCADSectionView._cap_node, cap)

    def test_panel_can_hide_handles_without_rebuilding_cut(self):
        import VibeCADSectionViewGui as panel
        from unittest.mock import patch

        view = Gui.ActiveDocument.ActiveView
        VibeCADSectionView.reset_section_view_settings()
        VibeCADSectionView.set_section_view(True, view=view, document=self.document)
        self.assertTrue(self._wait_until(lambda: not VibeCADSectionView._cap_worker._running))
        cap, guide = VibeCADSectionView._cap_node, VibeCADSectionView._overlay_node
        dragger = VibeCADSectionView._dragger_node
        checkbox = panel.show_section_view_dialog().findChild(QtWidgets.QCheckBox, "sectionShowHandles")
        self.assertIsNotNone(checkbox)
        with patch.object(VibeCADSectionView, "_schedule_cap_build", side_effect=AssertionError("Rebuilt cut")):
            checkbox.setChecked(False)
            self.assertEqual(view.getSceneGraph().findChild(dragger), -1)
            self.assertIs(VibeCADSectionView._cap_node, cap)
            self.assertIs(VibeCADSectionView._overlay_node, guide)
            self.assertTrue(view.hasClippingPlane())
            checkbox.setChecked(True)
            self.assertGreaterEqual(view.getSceneGraph().findChild(dragger), 0)
            self.assertIs(VibeCADSectionView._cap_node, cap)

    def test_drag_plane_in_canvas_preserves_document(self):
        view = Gui.ActiveDocument.ActiveView
        view.viewAxonometric()
        view.fitAll()
        VibeCADSectionView.reset_section_view_settings()
        VibeCADSectionView.set_section_view(True, view=view, document=self.document)
        self._process_events()
        widget = view.graphicsView().viewport()
        # Just inside the guide's padded corner, outside the model and gizmo.
        point = view.getPointOnScreen(App.Vector(-0.8, -0.4, 5))
        start = QtCore.QPoint(int(point[0]), widget.height() - 1 - int(point[1]))
        finish = start + QtCore.QPoint(0, -40)
        bounds = VibeCADSectionView._bounds_for_view(view, self.document)
        before_origin, _ = VibeCADSectionView.clip_plane_from_settings(
            VibeCADSectionView.current_section_view_settings(), bounds.center)
        before_screen = view.getPointOnScreen(App.Vector(*before_origin))
        box = self.document.getObject("SectionBox")
        placement = box.Placement
        undo = self.document.UndoCount
        for kind, position, button, buttons in (
            (QtCore.QEvent.MouseButtonPress, start, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton),
            (QtCore.QEvent.MouseMove, finish, QtCore.Qt.NoButton, QtCore.Qt.LeftButton),
            (QtCore.QEvent.MouseButtonRelease, finish, QtCore.Qt.LeftButton, QtCore.Qt.NoButton),
        ):
            event = QtGui.QMouseEvent(kind, QtCore.QPointF(position),
                                     QtCore.QPointF(widget.mapToGlobal(position)),
                                     button, buttons, QtCore.Qt.NoModifier)
            QtCore.QCoreApplication.sendEvent(widget, event)
        self._process_events()
        self.assertNotAlmostEqual(VibeCADSectionView.current_section_view_settings().offset, 0)
        after_origin, _ = VibeCADSectionView.clip_plane_from_settings(
            VibeCADSectionView.current_section_view_settings(), bounds.center)
        after_screen = view.getPointOnScreen(App.Vector(*after_origin))
        plane_motion = QtCore.QPointF(
            after_screen[0] - before_screen[0],
            before_screen[1] - after_screen[1],
        )
        mouse_motion = QtCore.QPointF(finish - start)
        self.assertGreater(
            plane_motion.x() * mouse_motion.x() + plane_motion.y() * mouse_motion.y(),
            0.0,
            "Section plane moved away from the pointer during its drag",
        )
        self.assertEqual(box.Placement, placement)
        self.assertEqual(self.document.UndoCount, undo)

    def test_face_on_plane_pull_follows_active_normal_when_flipped(self):
        view = Gui.ActiveDocument.ActiveView
        view.viewTop()
        view.fitAll()
        widget = view.graphicsView().viewport()
        for flipped in (False, True):
            VibeCADSectionView.reset_section_view_settings()
            VibeCADSectionView.configure_section_view(plane="top", flipped=flipped)
            VibeCADSectionView.set_section_view(True, view=view, document=self.document)
            self._process_events()
            before = VibeCADSectionView.current_section_view_settings()
            point = view.getPointOnScreen(App.Vector(-0.8, -0.4, 5))
            start = QtCore.QPoint(int(point[0]), widget.height() - 1 - int(point[1]))
            finish = start + QtCore.QPoint(0, -10)
            for kind, position, button, buttons in (
                (QtCore.QEvent.MouseButtonPress, start, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton),
                (QtCore.QEvent.MouseMove, finish, QtCore.Qt.NoButton, QtCore.Qt.LeftButton),
                (QtCore.QEvent.MouseButtonRelease, finish, QtCore.Qt.LeftButton, QtCore.Qt.NoButton),
            ):
                event = QtGui.QMouseEvent(kind, QtCore.QPointF(position),
                                         QtCore.QPointF(widget.mapToGlobal(position)),
                                         button, buttons, QtCore.Qt.NoModifier)
                QtCore.QCoreApplication.sendEvent(widget, event)
            after = VibeCADSectionView.current_section_view_settings()
            self.assertGreater(
                after.offset,
                before.offset,
                f"Pull opposed the active section normal with flipped={flipped}",
            )
            VibeCADSectionView.set_section_view(False, view=view, document=self.document)
            self._process_events()

    def test_scene_helpers_do_not_expand_section_bounds(self):
        from pivy import coin

        view = Gui.ActiveDocument.ActiveView
        scene = view.getSceneGraph()
        helper = coin.SoSeparator()
        # A visible grid/datum is scene geometry, but is not model geometry.
        cube = coin.SoCube()
        cube.width = 45000
        cube.height = 45000
        cube.depth = 1
        helper.addChild(cube)
        scene.addChild(helper)
        try:
            bounds = VibeCADSectionView._render_bounds(view)
            self.assertIsNotNone(bounds)
            self.assertAlmostEqual(bounds.xmin, 0, places=3)
            self.assertAlmostEqual(bounds.xmax, 40, places=3)
            self.assertAlmostEqual(bounds.ymax, 20, places=3)
            self.assertAlmostEqual(bounds.zmax, 10, places=3)
            import VibeCADSectionViewGui as panel
            VibeCADSectionView.reset_section_view_settings()
            VibeCADSectionView.configure_section_view(plane="right")
            VibeCADSectionView.set_section_view(True, view=view, document=self.document)
            dialog = panel.show_section_view_dialog()
            self.assertAlmostEqual(dialog.offset_spin.minimum(), -20, places=3)
            self.assertAlmostEqual(dialog.offset_spin.maximum(), 20, places=3)
        finally:
            scene.removeChild(helper)

    def test_section_queries_are_safe_after_native_view_deletion(self):
        view = Gui.ActiveDocument.ActiveView
        self.assertTrue(self._wait_until(self.document.isClosable))
        App.closeDocument(self.document.Name)
        self._process_events()
        self.assertIsNone(VibeCADSectionView._scene_from_view(view))
        self.assertFalse(VibeCADSectionView.is_section_view_active(view))
