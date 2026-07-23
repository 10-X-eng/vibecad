# SPDX-License-Identifier: LGPL-2.1-or-later

"""Functional GUI coverage for Part tools hosted by Part Design."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui


class TestConsolidatedPartTools(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("ConsolidatedPartTools")
        Gui.activateView("Gui::View3DInventor", True)
        self.body = self.document.addObject("PartDesign::Body", "Body")
        Gui.activeView().setActiveObject("pdbody", self.body)

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        if App.getDocument("ConsolidatedPartTools") is not None:
            App.closeDocument("ConsolidatedPartTools")

    def _select(self, *objects):
        Gui.Selection.clearSelection()
        for obj in objects:
            Gui.Selection.addSelection(obj)

    def _box(self, name, x=0.0, size=10.0):
        import PartDesignGui

        box = self.document.addObject("Part::Box", name)
        box.Length = size
        box.Width = size
        box.Height = size
        box.Placement.Base.x = x
        self.document.recompute()
        PartDesignGui.adoptPartResult(box)
        return box

    def _wire(self, name, x=0.0, z=0.0):
        import PartDesignGui

        wire = self.document.addObject("Part::Feature", name)
        wire.Shape = Part.makePolygon(
            [
                App.Vector(x + 2, 0, z),
                App.Vector(x + 7, 0, z),
                App.Vector(x + 7, 0, z + 5),
                App.Vector(x + 2, 0, z + 5),
                App.Vector(x + 2, 0, z),
            ]
        )
        self.document.recompute()
        PartDesignGui.adoptPartResult(wire)
        return wire

    def _assert_body_result(self, result):
        self._process_events()
        self.document.recompute()
        self.assertIsNotNone(result)
        self.assertIn(result, self.body.Group, result.Name)
        self.assertEqual(result.getParentGeoFeatureGroup(), self.body)
        self.assertTrue(result.isDerivedFrom("Part::Feature"), result.TypeId)
        self.assertFalse(result.Shape.isNull(), result.Name)
        self.assertEqual(result.getStatusString(), "Valid", result.getStatusString())

    def _accept_task_dialog(self):
        self.assertTrue(Gui.Control.activeDialog())
        Gui.updateGui()
        for button_box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not button_box.isVisible():
                continue
            button = button_box.button(QtGui.QDialogButtonBox.Ok)
            if button is not None and button.isEnabled():
                button.click()
                QtGui.QApplication.processEvents()
                self.assertFalse(Gui.Control.activeDialog())
                return
        self.fail("Active task dialog has no enabled OK button")

    def _run_modal_command(
        self, command_name, standard_button=QtGui.QDialogButtonBox.Ok
    ):
        clicked = []

        def click_dialog():
            for dialog in QtGui.QApplication.topLevelWidgets():
                if not isinstance(dialog, QtGui.QDialog) or not dialog.isVisible():
                    continue
                for button_box in dialog.findChildren(QtGui.QDialogButtonBox):
                    button = button_box.button(standard_button)
                    if button is not None and button.isEnabled():
                        clicked.append(dialog.windowTitle())
                        button.click()
                        return
            QtCore.QTimer.singleShot(20, click_dialog)

        QtCore.QTimer.singleShot(0, click_dialog)
        Gui.runCommand(command_name, 0)
        self.assertTrue(clicked, command_name)

    def _choose_action_selector_items(self, labels):
        main_window = Gui.getMainWindow()
        available = next(
            (
                tree
                for tree in main_window.findChildren(QtGui.QTreeWidget)
                if tree.objectName() == "availableTreeWidget" and tree.isVisible()
            ),
            None,
        )
        add_button = next(
            (
                button
                for button in main_window.findChildren(QtGui.QPushButton)
                if button.objectName() == "addButton" and button.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(available)
        self.assertIsNotNone(add_button)
        for label in labels:
            matches = available.findItems(label, QtCore.Qt.MatchExactly)
            self.assertEqual(len(matches), 1, label)
            available.setCurrentItem(matches[0])
            add_button.click()
            QtGui.QApplication.processEvents()

    def _open_and_close_task_command(self, command_name):
        Gui.runCommand(command_name, 0)
        self.assertTrue(Gui.Control.activeDialog(), command_name)
        for button_box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not button_box.isVisible():
                continue
            button = button_box.button(QtGui.QDialogButtonBox.Cancel)
            if button is None:
                button = button_box.button(QtGui.QDialogButtonBox.Close)
            if button is not None and button.isEnabled():
                button.click()
                QtGui.QApplication.processEvents()
                if not Gui.Control.activeDialog():
                    return
        Gui.Control.closeDialog()
        QtGui.QApplication.processEvents()
        self.assertFalse(Gui.Control.activeDialog(), command_name)

    @staticmethod
    def _process_events(wait_ms=20):
        Gui.updateGui()
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(wait_ms, loop.quit)
        loop.exec()

    def _send_mouse_event(self, viewport, event_type, pos, button, buttons):
        global_pos = viewport.mapToGlobal(pos)
        QtGui.QCursor.setPos(global_pos)
        event = QtGui.QMouseEvent(
            event_type,
            pos,
            global_pos,
            button,
            buttons,
            QtCore.Qt.NoModifier,
        )
        QtGui.QApplication.sendEvent(viewport, event)

    def test_parametric_primitive_commands_create_valid_body_results(self):
        results = []
        for command_name in (
            "Part_Box",
            "Part_Cylinder",
            "Part_Sphere",
            "Part_Cone",
            "Part_Torus",
        ):
            Gui.runCommand(command_name, 0)
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertGreater(len(result.Shape.Solids), 0, command_name)
            results.append(result)

        self.assertEqual(list(self.body.Group)[-len(results) :], results)
        self.assertEqual(self.body.Tip, results[-1])

    def test_tube_command_accepts_defaults_and_creates_valid_body_result(self):
        Gui.runCommand("Part_Tube", 0)
        tube = self.document.ActiveObject
        self._accept_task_dialog()
        self._assert_body_result(tube)
        self.assertGreater(len(tube.Shape.Solids), 0)

    def test_extrude_revolve_mirror_and_scale_task_commands(self):
        extrusion_profile = self._wire("ExtrusionProfile")
        self._select(extrusion_profile)
        Gui.runCommand("Part_Extrude", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

        revolution_profile = self._wire("RevolutionProfile", x=15.0)
        self._select(revolution_profile)
        Gui.runCommand("Part_Revolve", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

        mirror_source = self._box("MirrorSource", x=30.0)
        self._select(mirror_source)
        Gui.runCommand("Part_Mirror", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

        scale_source = self._box("ScaleSource", x=50.0)
        self._select(scale_source)
        Gui.runCommand("Part_Scale", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

    def _exercise_edge_task_command(self, command_name):
        source = self._box(f"{command_name}Source")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Edge1")
        self.assertEqual(Gui.Selection.getSelectionEx()[0].SubElementNames, ("Edge1",))
        Gui.runCommand(command_name, 0)
        edge_tree = next(
            (
                tree
                for tree in Gui.getMainWindow().findChildren(QtGui.QTreeView)
                if tree.objectName() == "treeView" and tree.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(edge_tree)
        model = edge_tree.model()
        self.assertGreater(model.rowCount(), 0)
        checked_rows = [
            model.index(row, 0).data(QtCore.Qt.CheckStateRole)
            for row in range(model.rowCount())
        ]
        self.assertTrue(
            any(state == QtCore.Qt.CheckState.Checked.value for state in checked_rows),
            (
                f"{command_name} did not preserve the selected edge: "
                f"selection={[item.SubElementNames for item in Gui.Selection.getSelectionEx()]}, "
                f"rows={checked_rows}"
            ),
        )
        self._accept_task_dialog()
        result = self.document.ActiveObject
        self.assertIsNot(result, source)
        self._assert_body_result(result)

    def test_fillet_task_command(self):
        self._exercise_edge_task_command("Part_Fillet")

    def test_chamfer_task_command(self):
        self._exercise_edge_task_command("Part_Chamfer")

    def test_body_native_dressup_winners_accept_ordinary_part_results(self):
        for index, (command_name, subelement) in enumerate(
            (
                ("PartDesign_Fillet", "Edge1"),
                ("PartDesign_Chamfer", "Edge1"),
                ("PartDesign_Thickness", "Face1"),
            )
        ):
            source = self._box(f"BodyDressupSource{index}", x=index * 25.0)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(source, subelement)
            Gui.runCommand(command_name, 0)
            self.assertTrue(Gui.Control.activeDialog(), command_name)
            self._accept_task_dialog()
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertTrue(
                result.isDerivedFrom("PartDesign::Feature"),
                (command_name, result.TypeId),
            )
            self.assertEqual(result.BaseFeature, source)

    def test_add_material_winner_continues_from_ordinary_part_result(self):
        source = self._box("PartBaseForAddMaterial")
        Gui.Selection.clearSelection()
        Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
        self.assertTrue(Gui.Control.activeDialog())
        self._accept_task_dialog()

        result = self.document.ActiveObject
        self._assert_body_result(result)
        self.assertEqual(result.TypeId, "PartDesign::AdditiveBox")
        self.assertEqual(result.BaseFeature, source)
        self.assertGreater(len(result.Shape.Solids), 0)

    def test_mesh_conversion_and_point_sampling_commands(self):
        import Mesh

        mesh = self.document.addObject("Mesh::Feature", "SourceMesh")
        mesh.Mesh = Mesh.createBox(10.0, 10.0, 10.0)
        self._select(mesh)
        self._run_modal_command("Part_ShapeFromMesh")
        converted = self.document.ActiveObject
        self._assert_body_result(converted)

        source = self._box("PointSource", x=20.0)
        self._select(source)
        self._run_modal_command("Part_PointsFromMesh")
        sampled = self.document.ActiveObject
        self._assert_body_result(sampled)
        self.assertGreater(len(sampled.Shape.Vertexes), 0)

    def test_offset_thickness_and_defeaturing_commands(self):
        offset_source = self._box("OffsetSource")
        self._select(offset_source)
        Gui.runCommand("Part_Offset", 0)
        offset = self.document.ActiveObject
        self._accept_task_dialog()
        self._assert_body_result(offset)

        wire_source = self._wire("Offset2DSource", x=20.0)
        self._select(wire_source)
        Gui.runCommand("Part_Offset2D", 0)
        offset_2d = self.document.ActiveObject
        self._accept_task_dialog()
        self._assert_body_result(offset_2d)

        thickness_source = self._box("ThicknessSource", x=40.0)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(thickness_source, "Face1")
        Gui.runCommand("Part_Thickness", 0)
        thickness = self.document.ActiveObject
        self._accept_task_dialog()
        self._assert_body_result(thickness)

        defeature_source = self.document.addObject("Part::Feature", "DefeatureSource")
        defeature_source.Shape = Part.makeBox(10, 10, 10, App.Vector(60, 0, 0)).cut(
            Part.makeCylinder(2, 10, App.Vector(65, 5, 0))
        )
        self.document.recompute()
        cylindrical_face = next(
            index
            for index, face in enumerate(defeature_source.Shape.Faces, start=1)
            if isinstance(face.Surface, Part.Cylinder)
        )
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(defeature_source, f"Face{cylindrical_face}")
        before = set(self.body.Group)
        Gui.runCommand("Part_Defeaturing", 0)
        created = [obj for obj in self.body.Group if obj not in before]
        self.assertEqual(len(created), 1)
        self._assert_body_result(created[0])

    def test_loft_and_sweep_commands_create_valid_body_results(self):
        loft_lower = self._wire("LoftLower")
        loft_lower.Label = "Loft Lower Profile"
        loft_upper = self._wire("LoftUpper")
        loft_upper.Label = "Loft Upper Profile"
        loft_upper.Placement.Base.y = 10.0
        self.document.recompute()

        Gui.runCommand("Part_Loft", 0)
        self._choose_action_selector_items([loft_lower.Label, loft_upper.Label])
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

        sweep_profile = self._wire("SweepProfile", x=20.0)
        sweep_profile.Label = "Sweep Profile"
        sweep_path = self.document.addObject("Part::Feature", "SweepPath")
        sweep_path.Label = "Sweep Path"
        sweep_path.Shape = Part.makeLine(App.Vector(22, 0, 0), App.Vector(22, 15, 0))
        self.document.recompute()

        Gui.runCommand("Part_Sweep", 0)
        self._choose_action_selector_items([sweep_profile.Label])
        path_button = next(
            (
                button
                for button in Gui.getMainWindow().findChildren(QtGui.QPushButton)
                if button.objectName() == "buttonPath" and button.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(path_button)
        path_button.click()
        Gui.Selection.addSelection(sweep_path)
        path_button.click()
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

    def test_canonical_model_loft_and_sweep_create_standalone_solids(self):
        from tool_impl.service import model_loft, model_sweep

        class Service:
            def __init__(self, document):
                self.document = document

            def _active_document(self):
                return self.document

        service = Service(self.document)
        loft_lower = self._wire("CanonicalLoftLower", x=60.0)
        loft_upper = self._wire("CanonicalLoftUpper", x=60.0)
        loft_upper.Placement.Base.y = 10.0
        self.document.recompute()

        loft_result = model_loft.run(
            service,
            operation="new_solid",
            profile_names=[loft_lower.Name, loft_upper.Name],
            label="Canonical Standalone Loft",
            closed=False,
            ruled=False,
            reversed=False,
            midplane=False,
            refine=False,
        )
        self.assertTrue(loft_result["ok"], loft_result)
        loft = self.document.getObject(loft_result["mutation"]["feature"])
        self._assert_body_result(loft)
        self.assertGreater(len(loft.Shape.Solids), 0)

        sweep_profile = self._wire("CanonicalSweepProfile", x=80.0)
        sweep_path = self.document.addObject("Part::Feature", "CanonicalSweepPath")
        sweep_path.Shape = Part.makeLine(
            App.Vector(82, 0, 0),
            App.Vector(82, 15, 0),
        )
        self.document.recompute()

        sweep_result = model_sweep.run(
            service,
            operation="new_solid",
            profile_name=sweep_profile.Name,
            spine_name=sweep_path.Name,
            section_names=[],
            label="Canonical Standalone Sweep",
            orientation="standard",
            transformation="constant",
            transition="right_corner",
            spine_tangent=False,
            auxiliary_spine_tangent=False,
            auxiliary_curvilinear=False,
            reversed=False,
            midplane=False,
            refine=False,
        )
        self.assertTrue(sweep_result["ok"], sweep_result)
        sweep = self.document.getObject(sweep_result["mutation"]["feature"])
        self._assert_body_result(sweep)
        self.assertGreater(len(sweep.Shape.Solids), 0)

    def test_canonical_model_revolve_uses_native_symmetric_part_feature(self):
        from tool_impl.service import model_revolve

        class Service:
            def __init__(self, document):
                self.document = document

            def _active_document(self):
                return self.document

        profile = self._wire("CanonicalRevolveProfile")
        result = model_revolve.run(
            Service(self.document),
            profile_name=profile.Name,
            operation="new_solid",
            axis={
                "source": "global",
                "point": {"x": 0.0, "y": 0.0, "z": 0.0},
                "direction": {"x": 0.0, "y": 0.0, "z": 1.0},
            },
            extent={"type": "angle", "angle_degrees": 180.0},
            midplane=True,
            reversed=False,
            label="Canonical Symmetric Revolution",
        )

        self.assertTrue(result["ok"], result)
        revolution = self.document.getObject(result["mutation"]["feature"])
        self._assert_body_result(revolution)
        self.assertTrue(revolution.Symmetric)
        self.assertGreater(len(revolution.Shape.Solids), 0)

    def test_body_mirror_winner_transforms_whole_ordinary_part_result(self):
        from tool_impl.service import model_mirror

        class Service:
            def __init__(self, document):
                self.document = document

            def _active_document(self):
                return self.document

            def _get_partdesign_body(self, body_name):
                candidate = self.document.getObject(body_name)
                return (
                    candidate
                    if getattr(candidate, "TypeId", "") == "PartDesign::Body"
                    else None
                )

            @staticmethod
            def _partdesign_body_header(body):
                return {
                    "name": body.Name,
                    "tip": getattr(getattr(body, "Tip", None), "Name", None),
                    "group": [item.Name for item in body.Group],
                }

            @staticmethod
            def _partdesign_body_for_feature(feature):
                parent = feature.getParentGeoFeatureGroup()
                return (
                    parent
                    if getattr(parent, "TypeId", "") == "PartDesign::Body"
                    else None
                )

            @staticmethod
            def _partdesign_origin_feature(body, role):
                return next(
                    (
                        item
                        for item in body.Origin.OriginFeatures
                        if getattr(item, "Role", "") == role
                        or getattr(item, "Name", "") == role
                        or getattr(item, "Label", "").replace("-", "_") == role
                    ),
                    None,
                )

        source = self._box("WholeShapeMirrorSource")
        service = Service(self.document)
        feature_mode = model_mirror.run(
            service,
            result_mode="body_features",
            feature_names=[source.Name],
            body_plane={"source": "body_origin", "plane": "YZ_Plane"},
            transform_mode="features",
            refine=True,
            label="Invalid Feature-Delta Mirror",
        )
        self.assertFalse(feature_mode["ok"])
        self.assertIn("whole_shape", feature_mode["error"])

        result = model_mirror.run(
            service,
            result_mode="body_features",
            feature_names=[source.Name],
            body_plane={"source": "body_origin", "plane": "YZ_Plane"},
            transform_mode="whole_shape",
            refine=True,
            label="Whole Shape Mirror",
        )
        self.assertTrue(result["ok"], result)
        mirrored = self.document.getObject(result["mutation"]["feature"])
        self._assert_body_result(mirrored)
        self.assertEqual(mirrored.TransformMode, "Whole shape")
        self.assertGreater(len(mirrored.Shape.Solids), 0)

    def test_cross_sections_command_creates_valid_body_result(self):
        source = self._box("CrossSectionSource")
        self._select(source)
        Gui.runCommand("Part_CrossSections", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

    def test_specialized_task_tools_open_cleanly_in_part_design(self):
        left = self._box("TaskLeft")
        right = self._box("TaskRight", x=5.0)

        Gui.runCommand("Part_Primitives", 0)
        primitive_selector = next(
            (
                combo
                for combo in Gui.getMainWindow().findChildren(QtGui.QComboBox)
                if combo.objectName() == "PrimitiveTypeCB" and combo.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(primitive_selector)
        primitive_names = {
            primitive_selector.itemText(index)
            for index in range(primitive_selector.count())
        }
        self.assertEqual(
            primitive_names
            & {
                "Box",
                "Cylinder",
                "Cone",
                "Sphere",
                "Ellipsoid",
                "Torus",
                "Prism",
                "Wedge",
            },
            set(),
        )
        before_primitives = set(self.body.Group)
        primitive_button_box = next(
            (
                box
                for box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox)
                if box.isVisible()
                and box.button(QtGui.QDialogButtonBox.Ok) is not None
                and box.button(QtGui.QDialogButtonBox.Ok).isEnabled()
            ),
            None,
        )
        self.assertIsNotNone(primitive_button_box)
        primitive_button_box.button(QtGui.QDialogButtonBox.Ok).click()
        self._process_events()
        created_primitives = [
            obj for obj in self.body.Group if obj not in before_primitives
        ]
        self.assertEqual(len(created_primitives), 1)
        self._assert_body_result(created_primitives[0])
        self.assertEqual(created_primitives[0].TypeId, "Part::Plane")
        close_button = primitive_button_box.button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(close_button)
        close_button.click()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())

        self._open_and_close_task_command("Part_Builder")

        self._select(left, right)
        self._open_and_close_task_command("Part_Boolean")

        self._select(left)
        self._open_and_close_task_command("Part_CheckGeometry")
        self._select(left)
        self._open_and_close_task_command("Part_ColorPerFace")
        self._select(left)
        self._open_and_close_task_command("Part_EditAttachment")

        for command_name in (
            "Materials_InspectAppearance",
            "Materials_InspectMaterial",
        ):
            self._select(left)
            self._open_and_close_task_command(command_name)

        self._select(left)
        self._open_and_close_task_command("Part_ProjectionOnSurface")

    def test_new_sketch_command_creates_sketch_in_active_body(self):
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/PartDesign")
        previous = preferences.GetBool("NewSketchUseAttachmentDialog", False)
        preferences.SetBool("NewSketchUseAttachmentDialog", False)
        try:
            support = self._box("SketchSupport")
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(support, "Face6")
            Gui.runCommand("PartDesign_NewSketch", 0)
            sketch = self.document.ActiveObject
            self.assertIsNotNone(sketch)
            self.assertTrue(sketch.isDerivedFrom("Sketcher::SketchObject"))
            self.assertIn(sketch, self.body.Group)
            if Gui.Control.activeDialog():
                Gui.Control.closeDialog()
            if Gui.activeDocument().getInEdit() is not None:
                Gui.activeDocument().resetEdit()
        finally:
            preferences.SetBool("NewSketchUseAttachmentDialog", previous)

    def test_persistent_section_cut_opens_its_dock(self):
        self._box("SectionCutSource")
        Gui.runCommand("Part_SectionCut", 0)
        docks = [
            dock
            for dock in Gui.getMainWindow().findChildren(QtGui.QDockWidget)
            if dock.windowTitle() == "Persistent Section Cut"
        ]
        self.assertEqual(len(docks), 1)
        self.assertTrue(docks[0].isVisible())
        docks[0].close()

    def test_projection_on_surface_command_creates_valid_body_result(self):
        support = self._box("ProjectionSupport")
        projected = self.document.addObject("Part::Feature", "ProjectedEdge")
        projected.Shape = Part.makeLine(App.Vector(2, 2, 15), App.Vector(8, 2, 15))
        self.document.recompute()

        Gui.runCommand("Part_ProjectionOnSurface", 0)
        result = self.document.ActiveObject
        result.SupportFace = (support, ["Face6"])
        result.Projection = [(projected, ["Edge1"])]
        result.Direction = App.Vector(0, 0, -1)
        result.Mode = "Edges"
        self.document.recompute()
        self._accept_task_dialog()
        self._assert_body_result(result)

    def test_box_selection_command_selects_faces_in_view(self):
        target = self._box("BoxSelectionTarget")
        view = Gui.activeDocument().activeView()
        view.viewIsometric()
        view.fitAll()
        self._process_events(50)

        graphics_view = view.graphicsView()
        viewport = graphics_view.viewport()
        _, height = view.getSize()
        scale = (
            viewport.devicePixelRatioF()
            if hasattr(viewport, "devicePixelRatioF")
            else float(viewport.devicePixelRatio())
        )
        projected = []
        for x in (target.Shape.BoundBox.XMin, target.Shape.BoundBox.XMax):
            for y in (target.Shape.BoundBox.YMin, target.Shape.BoundBox.YMax):
                for z in (target.Shape.BoundBox.ZMin, target.Shape.BoundBox.ZMax):
                    point = view.getPointOnViewport(App.Vector(x, y, z))
                    projected.append(
                        QtCore.QPoint(
                            int(round(point[0] / scale)),
                            int(round((height - point[1] - 1) / scale)),
                        )
                    )
        bounds = viewport.rect().adjusted(2, 2, -3, -3)
        rect = QtCore.QRect(
            QtCore.QPoint(
                max(bounds.left(), min(point.x() for point in projected) - 15),
                max(bounds.top(), min(point.y() for point in projected) - 15),
            ),
            QtCore.QPoint(
                min(bounds.right(), max(point.x() for point in projected) + 15),
                min(bounds.bottom(), max(point.y() for point in projected) + 15),
            ),
        )

        Gui.Selection.clearSelection()
        Gui.runCommand("Part_BoxSelection", 0)
        start = rect.topLeft()
        middle = rect.center()
        end = rect.bottomRight()
        for event_type, pos, button, buttons in (
            (QtCore.QEvent.MouseMove, start, QtCore.Qt.NoButton, QtCore.Qt.NoButton),
            (
                QtCore.QEvent.MouseButtonPress,
                start,
                QtCore.Qt.LeftButton,
                QtCore.Qt.LeftButton,
            ),
            (QtCore.QEvent.MouseMove, middle, QtCore.Qt.NoButton, QtCore.Qt.LeftButton),
            (QtCore.QEvent.MouseMove, end, QtCore.Qt.NoButton, QtCore.Qt.LeftButton),
            (
                QtCore.QEvent.MouseButtonRelease,
                end,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoButton,
            ),
        ):
            self._send_mouse_event(viewport, event_type, pos, button, buttons)
            self._process_events(10)

        selected = next(
            (item for item in Gui.Selection.getSelectionEx() if item.Object == target),
            None,
        )
        self.assertIsNotNone(selected)
        self.assertTrue(selected.SubElementNames)
        self.assertTrue(
            all(name.startswith("Face") for name in selected.SubElementNames)
        )

    def test_copy_refine_and_reverse_commands_create_valid_body_results(self):
        source = self._box("CopySource")

        for command_name in (
            "Part_SimpleCopy",
            "Part_TransformedCopy",
            "Part_RefineShape",
            "Part_ReverseShape",
        ):
            self._select(source)
            Gui.runCommand(command_name, 0)
            self._assert_body_result(self.document.ActiveObject)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1")
        Gui.runCommand("Part_ElementCopy", 0)
        element = self.document.ActiveObject
        self._assert_body_result(element)
        self.assertEqual(element.Shape.ShapeType, "Face")

    def test_core_boolean_commands_preserve_inputs_and_create_valid_results(self):
        for index, command_name in enumerate(("Part_Cut", "Part_Fuse", "Part_Common")):
            left = self._box(f"Left{index}", x=index * 30.0)
            right = self._box(f"Right{index}", x=index * 30.0 + 5.0)
            self._select(left, right)
            Gui.runCommand(command_name, 0)
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertIn(left, self.body.Group, command_name)
            self.assertIn(right, self.body.Group, command_name)
            self.assertEqual(self.body.Tip, result)

    def test_join_commands_preserve_inputs_and_create_valid_results(self):
        for index, command_name in enumerate(
            ("Part_JoinConnect", "Part_JoinEmbed", "Part_JoinCutout")
        ):
            base_x = index * 30.0
            base = self._box(f"JoinBase{index}", x=base_x)
            tool = self._box(f"JoinTool{index}", x=base_x + 3.0, size=4.0)
            self._select(base, tool)
            Gui.runCommand(command_name, 0)
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertIn(base, self.body.Group, command_name)
            self.assertIn(tool, self.body.Group, command_name)

    def test_split_commands_preserve_inputs_and_create_valid_results(self):
        for index, command_name in enumerate(
            ("Part_BooleanFragments", "Part_Slice", "Part_XOR")
        ):
            base_x = index * 30.0
            base = self._box(f"SplitBase{index}", x=base_x)
            tool = self._box(f"SplitTool{index}", x=base_x + 5.0)
            self._select(base, tool)
            Gui.runCommand(command_name, 0)
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertIn(base, self.body.Group, command_name)
            self.assertIn(tool, self.body.Group, command_name)

    def test_slice_apart_keeps_exploded_results_directly_in_body(self):
        base = self._box("SliceApartBase")
        tool = self._box("SliceApartTool", x=5.0)
        before = set(self.body.Group)
        self._select(base, tool)
        Gui.runCommand("Part_SliceApart", 0)
        self.document.recompute()

        created = [obj for obj in self.body.Group if obj not in before]
        self.assertGreaterEqual(len(created), 2)
        for obj in created:
            self._assert_body_result(obj)
        self.assertFalse(
            any(
                obj.TypeId == "App::DocumentObjectGroup"
                and obj.Name.startswith("GrExplode")
                for obj in self.document.Objects
            )
        )

    def test_compound_filter_and_explode_commands_create_valid_results(self):
        left = self._box("CompoundToolLeft")
        right = self._box("CompoundToolRight", x=15.0)
        self._select(left, right)
        Gui.runCommand("Part_Compound", 0)
        compound = self.document.ActiveObject
        self._assert_body_result(compound)

        self._select(compound)
        Gui.runCommand("Part_CompoundFilter", 0)
        filtered = self.document.ActiveObject
        self._assert_body_result(filtered)

        self._select(compound)
        before = set(self.body.Group)
        Gui.runCommand("Part_ExplodeCompound", 0)
        self.document.recompute()
        created = [obj for obj in self.body.Group if obj not in before]
        self.assertTrue(created)
        for obj in created:
            self._assert_body_result(obj)

    def test_tolerance_command_preserves_input_and_creates_valid_result(self):
        source = self._box("ToleranceSource")
        self._select(source)
        Gui.runCommand("Part_ToleranceSet", 0)
        result = self.document.ActiveObject
        self._assert_body_result(result)
        self.assertIn(source, self.body.Group)

    def test_compound_section_face_surface_and_solid_commands(self):
        left = self._box("CompoundLeft")
        right = self._box("CompoundRight", x=5.0)

        self._select(left, right)
        Gui.runCommand("Part_Compound", 0)
        compound = self.document.ActiveObject
        self._assert_body_result(compound)

        self._select(left, right)
        Gui.runCommand("Part_Section", 0)
        self._assert_body_result(self.document.ActiveObject)

        wire = self.document.addObject("Part::Feature", "ClosedWire")
        wire.Shape = Part.makePolygon(
            [
                App.Vector(0, 0, 0),
                App.Vector(5, 0, 0),
                App.Vector(5, 5, 0),
                App.Vector(0, 5, 0),
                App.Vector(0, 0, 0),
            ]
        )
        self._select(wire)
        Gui.runCommand("Part_MakeFace", 0)
        face = self.document.ActiveObject
        self._assert_body_result(face)
        self.assertEqual(face.Shape.ShapeType, "Face")

        upper_wire = self.document.addObject("Part::Feature", "UpperWire")
        upper_wire.Shape = wire.Shape.copy()
        upper_wire.Placement.Base.z = 5.0
        self._select(wire, upper_wire)
        Gui.runCommand("Part_RuledSurface", 0)
        self._assert_body_result(self.document.ActiveObject)

        shell = self.document.addObject("Part::Feature", "BoxShell")
        shell.Shape = Part.makeShell(left.Shape.Faces)
        self._select(shell)
        Gui.runCommand("Part_MakeSolid", 0)
        solid = self.document.ActiveObject
        self._assert_body_result(solid)
        self.assertEqual(solid.Shape.ShapeType, "Solid")
