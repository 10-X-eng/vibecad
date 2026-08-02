# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native Design-wide sketch and multi-Body operation contracts."""

from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
import Sketcher


class TestDesignModeling(unittest.TestCase):
    def setUp(self):
        self.document = App.newDocument("DesignModeling")
        self.document.UndoMode = True
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="vibecad-design-modeling-"
        )

    def tearDown(self):
        if self.document is not None:
            document = App.getDocument(self.document.Name)
            if document is not None:
                App.closeDocument(document.Name)
        self._temporary_directory.cleanup()

    def _finalize_sketch(self, sketch):
        sketch.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        sketch.setPropertyStatus(
            "VibeCADTimelineRole",
            ("Hidden", "LockDynamic", "NoRecompute"),
        )
        sketch.VibeCADTimelineRole = "operation"
        self.document.finalizeProvisionalTimelineOperationBlock(
            sketch,
            [sketch],
        )

    def _rectangle_sketch(self, name, x_min, x_max, y_min, y_max):
        self.document.openTransaction(f"Create {name}")
        sketch = self.document.addObject("Sketcher::SketchObject", name)
        points = (
            ((x_min, y_min), (x_max, y_min)),
            ((x_max, y_min), (x_max, y_max)),
            ((x_max, y_max), (x_min, y_max)),
            ((x_min, y_max), (x_min, y_min)),
        )
        for start, end in points:
            sketch.addGeometry(
                Part.LineSegment(
                    App.Vector(*start, 0),
                    App.Vector(*end, 0),
                ),
                False,
            )
        self._finalize_sketch(sketch)
        self.document.commitTransaction()
        return sketch

    def _hole_sketch(self, name, centers, z):
        self.document.openTransaction(f"Create {name}")
        sketch = self.document.addObject("Sketcher::SketchObject", name)
        sketch.Placement.Base.z = z
        for x, y in centers:
            sketch.addGeometry(
                Part.Circle(
                    App.Vector(x, y, 0),
                    App.Vector(0, 0, 1),
                    1,
                ),
                False,
            )
        self._finalize_sketch(sketch)
        self.document.commitTransaction()
        return sketch

    def _circle_sketch(self, name, center, radius):
        self.document.openTransaction(f"Create {name}")
        sketch = self.document.addObject("Sketcher::SketchObject", name)
        sketch.addGeometry(
            Part.Circle(
                App.Vector(center[0], center[1], 0),
                App.Vector(0, 0, 1),
                radius,
            ),
            False,
        )
        self._finalize_sketch(sketch)
        self.document.commitTransaction()
        return sketch

    def _master_circle_sketch(self, name):
        self.document.openTransaction(f"Create {name}")
        sketch = self.document.addObject("Sketcher::SketchObject", name)
        first = sketch.addGeometry(
            Part.Circle(
                App.Vector(0, 5, 0),
                App.Vector(0, 0, 1),
                2,
            ),
            False,
        )
        second = sketch.addGeometry(
            Part.Circle(
                App.Vector(10, 5, 0),
                App.Vector(0, 0, 1),
                3,
            ),
            False,
        )
        first_radius = sketch.addConstraint(
            Sketcher.Constraint("Radius", first, 2),
        )
        sketch.addConstraint(Sketcher.Constraint("Radius", second, 3))
        self._finalize_sketch(sketch)
        self.document.commitTransaction()
        return sketch, first_radius

    def _component_body(self, name, x_offset):
        component = self.document.addObject(
            "PartDesign::Component",
            f"{name}Component",
        )
        component.Placement.Base.x = x_offset
        body = self.document.addObject("PartDesign::Body", f"{name}Body")
        component.addObject(body)
        initial = body.newObject(
            "PartDesign::Feature",
            f"{name}ImportedState",
        )
        initial.Shape = Part.makeBox(10, 10, 10)
        return component, body, initial

    def _new_body_operation(self, type_name, name, configure):
        self.document.openTransaction(f"Create {name}")
        operation = self.document.addObject(type_name, name)
        edit = PartDesign.beginDesignOperationEdit(operation)
        configure(operation)
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        self.assertEqual(len(bodies), 1)
        self.assertGreater(bodies[0].Shape.Volume, 0.0)
        PartDesign.validateDesign(operation)
        return operation, bodies[0]

    def test_generated_operation_owns_one_internal_parametric_source(self):
        self.document.openTransaction("Create generated operation")
        generator = self.document.addObject(
            "PartDesign::Feature",
            "NativeGenerator",
        )
        generator.Shape = Part.makeBox(10, 8, 6)
        self.document.classifyProvisionalTimelineInternalObject(generator)
        operation = self.document.addObject(
            "PartDesign::DesignGeneratedOperation",
            "GeneratedFeature",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Generator = generator
        operation.GeneratorKind = "test-native-generator"
        operation.OutputLabel = "Generated Body"
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        self.document.recompute()
        bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        state = body.Tip.CurrentState
        state_id = str(state.BodyStateId)
        body_id = str(body.VibeCADBodyId)
        self.assertEqual(body.Label, "Generated Body")
        self.assertEqual(generator.VibeCADTimelineRole, "internal")
        self.assertIs(operation.Generator, generator)
        self.assertIs(state.Operation, operation)
        self.assertTrue(operation.Shape.isNull())
        self.assertAlmostEqual(body.Shape.Volume, 480.0)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

        self.document.openTransaction("Edit generated operation")
        edit = PartDesign.beginDesignOperationEdit(operation)
        generator.Shape = Part.makeBox(12, 8, 6)
        operation.OutputLabel = "Edited Generated Body"
        self.document.recompute()
        edited = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        self.assertEqual(edited, [body])
        self.assertEqual(str(body.VibeCADBodyId), body_id)
        self.assertEqual(str(body.Tip.CurrentState.BodyStateId), state_id)
        self.assertEqual(body.Label, "Edited Generated Body")
        self.assertAlmostEqual(body.Shape.Volume, 576.0)
        PartDesign.validateDesign(operation)

        saved = (
            Path(self._temporary_directory.name)
            / "generated_operation.FCStd"
        )
        operation_name = operation.Name
        generator_name = generator.Name
        body_name = body.Name
        self.document.saveAs(str(saved))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(saved))
        operation = self.document.getObject(operation_name)
        generator = self.document.getObject(generator_name)
        body = self.document.getObject(body_name)
        self.assertIs(operation.Generator, generator)
        self.assertEqual(str(body.VibeCADBodyId), body_id)
        self.assertEqual(str(body.Tip.CurrentState.BodyStateId), state_id)
        self.assertAlmostEqual(body.Shape.Volume, 576.0)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

        self.document.openTransaction("Delete generated operation")
        removed = PartDesign.removeDesignOperation(operation)
        self.document.commitTransaction()
        self.assertEqual(removed, [body_name])
        self.assertIsNone(self.document.getObject(operation_name))
        self.assertIsNone(self.document.getObject(generator_name))
        self.assertIsNone(self.document.getObject(body_name))

    def test_one_master_sketch_drives_independent_closed_region_extrusions(self):
        sketch, first_radius = self._master_circle_sketch("MasterSketch")
        self.document.recompute()
        self.assertEqual(len(sketch.InternalShape.Faces), 2)

        first_operation, first_body = self._new_body_operation(
            "PartDesign::DesignExtrude",
            "FirstRegion",
            lambda operation: (
                setattr(operation, "Profile", (sketch, ["InternalFace1"])),
                setattr(operation, "Length", 5),
            ),
        )
        second_operation, second_body = self._new_body_operation(
            "PartDesign::DesignExtrude",
            "SecondRegion",
            lambda operation: (
                setattr(operation, "Profile", (sketch, ["InternalFace2"])),
                setattr(operation, "Length", 5),
            ),
        )

        resolved_sketch, resolved_regions = (
            PartDesign.resolveDesignDefinitionSubelementReference(
                first_operation,
                sketch,
                ["InternalFace1"],
            )
        )
        self.assertIs(resolved_sketch, sketch)
        self.assertEqual(list(resolved_regions), ["InternalFace1"])

        self.assertEqual(list(first_operation.Profile[1]), ["InternalFace1"])
        self.assertEqual(list(second_operation.Profile[1]), ["InternalFace2"])
        self.assertAlmostEqual(first_body.Shape.Volume, 20 * 3.14159265, places=4)
        self.assertAlmostEqual(second_body.Shape.Volume, 45 * 3.14159265, places=4)

        sketch.setDatum(first_radius, App.Units.Quantity("4 mm"))
        self.document.recompute()
        self.assertEqual(list(first_operation.Profile[1]), ["InternalFace1"])
        self.assertEqual(list(second_operation.Profile[1]), ["InternalFace2"])
        self.assertAlmostEqual(first_body.Shape.Volume, 80 * 3.14159265, places=4)
        self.assertAlmostEqual(second_body.Shape.Volume, 45 * 3.14159265, places=4)

        saved = Path(self._temporary_directory.name) / "master-sketch-regions.FCStd"
        first_operation_name = first_operation.Name
        second_operation_name = second_operation.Name
        first_body_name = first_body.Name
        second_body_name = second_body.Name
        sketch_name = sketch.Name
        self.document.saveAs(str(saved))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(saved))

        reopened_sketch = self.document.getObject(sketch_name)
        reopened_first = self.document.getObject(first_operation_name)
        reopened_second = self.document.getObject(second_operation_name)
        self.assertIs(reopened_first.Profile[0], reopened_sketch)
        self.assertIs(reopened_second.Profile[0], reopened_sketch)
        self.assertEqual(list(reopened_first.Profile[1]), ["InternalFace1"])
        self.assertEqual(list(reopened_second.Profile[1]), ["InternalFace2"])
        self.assertAlmostEqual(
            self.document.getObject(first_body_name).Shape.Volume,
            80 * 3.14159265,
            places=4,
        )
        self.assertAlmostEqual(
            self.document.getObject(second_body_name).Shape.Volume,
            45 * 3.14159265,
            places=4,
        )
        self._assert_dependency_graph_acyclic(self.document)

    def test_master_sketch_regions_cut_their_explicit_bodies_in_one_operation(self):
        _, first_body, _ = self._component_body("FirstRegionTarget", -3)
        _, second_body, _ = self._component_body("SecondRegionTarget", 7)
        sketch, _ = self._master_circle_sketch("SharedCutMasterSketch")

        self.document.openTransaction("Cut explicit Bodies from master sketch")
        operation = self.document.addObject(
            "PartDesign::DesignExtrude",
            "SharedRegionCut",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = (
            sketch,
            ["InternalFace1", "InternalFace2"],
        )
        operation.Length = 10
        PartDesign.setDesignOperationTargets(
            edit,
            "Cut",
            [first_body, second_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(
            list(operation.Profile[1]),
            ["InternalFace1", "InternalFace2"],
        )
        self.assertAlmostEqual(
            first_body.Shape.Volume,
            1000 - 40 * 3.14159265,
            places=4,
        )
        self.assertAlmostEqual(
            second_body.Shape.Volume,
            1000 - 90 * 3.14159265,
            places=4,
        )
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    @staticmethod
    def _assert_dependency_graph_acyclic(document):
        visiting = set()
        visited = set()

        def visit(obj):
            if obj.Name in visiting:
                raise AssertionError(f"dependency cycle reaches {obj.Name}")
            if obj.Name in visited:
                return
            visiting.add(obj.Name)
            for dependency in obj.OutList:
                visit(dependency)
            visiting.remove(obj.Name)
            visited.add(obj.Name)

        for obj in document.Objects:
            visit(obj)

    def test_design_primitives_share_one_global_result_contract(self):
        primitive_types = (
            "Box",
            "Cylinder",
            "Sphere",
            "Cone",
            "Ellipsoid",
            "Torus",
            "Prism",
            "Wedge",
            "Tube",
        )
        identities = {}
        created_bodies = {}
        for primitive_name in primitive_types:
            operation, body = self._new_body_operation(
                f"PartDesign::Design{primitive_name}",
                f"Design{primitive_name}",
                lambda primitive: None,
            )
            self.assertIsNone(operation.getParentGeoFeatureGroup())
            self.assertIsNone(operation.BaseFeature)
            self.assertTrue(operation.Shape.isNull())
            self.assertFalse(operation.AddSubShape.isNull())
            self.assertEqual(operation.ResultOperation, "New Body")
            self.assertEqual(operation.InputStates, [])
            self.assertEqual(operation.OutputPreviousInputIndices, [-1])
            self.assertEqual(operation.OutputPresence, (True,))
            self.assertEqual(body.Tip.CurrentState.Operation, operation)
            self.assertEqual(body.Shape.Solids.__len__(), 1)
            identities[operation.Name] = (
                str(operation.OperationId),
                str(body.VibeCADBodyId),
                str(body.Tip.CurrentState.BodyStateId),
            )
            created_bodies[operation.Name] = body
            self._assert_dependency_graph_acyclic(self.document)

        box = self.document.getObject("DesignBox")
        box_body = created_bodies["DesignBox"]
        box.Length = 12
        self.document.recompute()
        self.assertAlmostEqual(box_body.Shape.Volume, 1200.0)

        box.Suppressed = True
        self.document.recompute()
        self.assertEqual(box.OutputPresence, (False,))
        self.assertTrue(box_body.Shape.isNull())
        box.Suppressed = False
        self.document.recompute()
        self.assertEqual(box.OutputPresence, (True,))
        self.assertAlmostEqual(box_body.Shape.Volume, 1200.0)

        _, join_body, join_input = self._component_body("PrimitiveJoin", 0)
        self.document.openTransaction("Create primitive Join")
        join = self.document.addObject(
            "PartDesign::DesignBox",
            "PrimitiveJoinOperation",
        )
        join_edit = PartDesign.beginDesignOperationEdit(join)
        join.Placement.Base.x = 5
        PartDesign.setDesignOperationTargets(join_edit, "Join", [join_body])
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(join_edit)
        self.document.commitTransaction()
        self.assertEqual(join.InputStates, [join_input])
        self.assertAlmostEqual(join_body.Shape.Volume, 1500.0)
        PartDesign.validateDesign(join)

        _, first_cut_body, first_cut_input = self._component_body(
            "PrimitiveCutFirst",
            0,
        )
        _, second_cut_body, second_cut_input = self._component_body(
            "PrimitiveCutSecond",
            15,
        )
        self.document.openTransaction("Create primitive multi-Body Cut")
        cut = self.document.addObject(
            "PartDesign::DesignBox",
            "PrimitiveCutOperation",
        )
        cut_edit = PartDesign.beginDesignOperationEdit(cut)
        cut.Length = 25
        cut.Width = 5
        cut.Height = 5
        cut.Placement.Base.y = 2
        cut.Placement.Base.z = 2
        PartDesign.setDesignOperationTargets(
            cut_edit,
            "Cut",
            [first_cut_body, second_cut_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(cut_edit)
        self.document.commitTransaction()
        self.assertEqual(
            cut.InputStates,
            [first_cut_input, second_cut_input],
        )
        self.assertAlmostEqual(first_cut_body.Shape.Volume, 750.0)
        self.assertAlmostEqual(second_cut_body.Shape.Volume, 750.0)
        PartDesign.validateDesign(cut)

        _, intersect_body, intersect_input = self._component_body(
            "PrimitiveIntersect",
            0,
        )
        self.document.openTransaction("Create primitive Intersect")
        intersect = self.document.addObject(
            "PartDesign::DesignSphere",
            "PrimitiveIntersectOperation",
        )
        intersect_edit = PartDesign.beginDesignOperationEdit(intersect)
        PartDesign.setDesignOperationTargets(
            intersect_edit,
            "Intersect",
            [intersect_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(intersect_edit)
        self.document.commitTransaction()
        self.assertEqual(intersect.InputStates, [intersect_input])
        self.assertGreater(intersect_body.Shape.Volume, 0.0)
        self.assertLess(intersect_body.Shape.Volume, 1000.0)
        PartDesign.validateDesign(intersect)
        self._assert_dependency_graph_acyclic(self.document)

        path = (
            Path(self._temporary_directory.name)
            / "DesignPrimitives.FCStd"
        )
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()
        for operation_name, expected in identities.items():
            operation = self.document.getObject(operation_name)
            body = next(
                candidate
                for candidate in self.document.Objects
                if candidate.TypeId == "PartDesign::Body"
                and str(candidate.VibeCADBodyId) == expected[1]
            )
            self.assertEqual(str(operation.OperationId), expected[0])
            self.assertEqual(str(body.VibeCADBodyId), expected[1])
            self.assertEqual(
                str(body.Tip.CurrentState.BodyStateId),
                expected[2],
            )
            self.assertTrue(operation.isValid(), operation.getStatusString())
            self.assertTrue(body.isValid(), body.getStatusString())
            PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_clone_copies_one_exact_state_without_body_owned_history(self):
        component, source_body, source_state = self._component_body(
            "CloneSource",
            25,
        )
        source_body_id = str(source_body.VibeCADBodyId)

        self.document.openTransaction("Create Design Clone")
        clone = self.document.addObject(
            "PartDesign::DesignClone",
            "DesignClone",
        )
        edit = PartDesign.beginDesignOperationEdit(clone)
        PartDesign.setDesignCloneSource(edit, source_body)
        outputs = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(len(outputs), 1)
        output_body = outputs[0]
        self.assertIsNone(clone.getParentGeoFeatureGroup())
        self.assertIsNone(clone.BaseFeature)
        self.assertEqual(clone.ResultOperation, "New Bodies")
        self.assertEqual(clone.InputStates, [source_state])
        self.assertEqual(clone.InputBodyIds, [source_body_id])
        self.assertEqual(clone.OutputPreviousInputIndices, [-1])
        self.assertEqual(clone.OutputPresence, (True,))
        self.assertNotEqual(
            str(output_body.VibeCADBodyId),
            source_body_id,
        )
        self.assertEqual(output_body.ComponentId, component.ComponentId)
        self.assertEqual(output_body.getParentGeoFeatureGroup(), component)
        self.assertEqual(output_body.Tip.TypeId, "PartDesign::DesignBodyPublication")
        self.assertEqual(output_body.Group, [output_body.Tip])
        self.assertFalse(
            any(
                obj.TypeId == "PartDesign::FeatureBase"
                for obj in self.document.Objects
            )
        )
        self.assertAlmostEqual(output_body.Shape.Volume, 1000.0)
        self.assertEqual(output_body.Placement, source_body.Placement)
        PartDesign.validateDesign(clone)
        self._assert_dependency_graph_acyclic(self.document)

        clone.Suppressed = True
        self.document.recompute()
        self.assertEqual(clone.OutputPresence, (False,))
        self.assertTrue(output_body.Shape.isNull())
        self.assertAlmostEqual(source_body.Shape.Volume, 1000.0)
        clone.Suppressed = False
        self.document.recompute()
        self.assertEqual(clone.OutputPresence, (True,))
        self.assertAlmostEqual(output_body.Shape.Volume, 1000.0)

        identities = (
            str(clone.OperationId),
            str(output_body.VibeCADBodyId),
            str(output_body.Tip.CurrentState.BodyStateId),
        )
        path = Path(self._temporary_directory.name) / "DesignClone.FCStd"
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        clone = self.document.getObject("DesignClone")
        output_body = next(
            body
            for body in self.document.Objects
            if body.TypeId == "PartDesign::Body"
            and str(body.VibeCADBodyId) == identities[1]
        )
        self.assertEqual(str(clone.OperationId), identities[0])
        self.assertEqual(
            str(output_body.Tip.CurrentState.BodyStateId),
            identities[2],
        )
        self.assertAlmostEqual(output_body.Shape.Volume, 1000.0)
        PartDesign.validateDesign(clone)
        self._assert_dependency_graph_acyclic(self.document)

    def test_scale_modifies_multiple_bodies_atomically_in_design_space(self):
        _, first_body, first_input = self._component_body("ScaleFirst", 0)
        _, second_body, second_input = self._component_body(
            "ScaleSecond",
            30,
        )
        body_ids = (
            str(first_body.VibeCADBodyId),
            str(second_body.VibeCADBodyId),
        )

        self.document.openTransaction("Scale two Bodies")
        scale = self.document.addObject(
            "PartDesign::DesignScale",
            "SharedScale",
        )
        edit = PartDesign.beginDesignOperationEdit(scale)
        PartDesign.setDesignOperationTargets(
            edit,
            "Modify",
            [first_body, second_body],
        )
        scale.Uniform = True
        scale.UniformScale = 2.0
        scale.Center = App.Vector(5, 5, 5)
        self.document.recompute()
        modified = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(modified, [first_body, second_body])
        self.assertIsNone(scale.getParentGeoFeatureGroup())
        self.assertIsNone(scale.BaseFeature)
        self.assertEqual(scale.ResultOperation, "Modify")
        self.assertEqual(scale.InputStates, [first_input, second_input])
        self.assertEqual(list(scale.OutputBodyIds), list(body_ids))
        self.assertEqual(scale.OutputPreviousInputIndices, [0, 1])
        self.assertEqual(scale.OutputPresence, (True, True))
        self.assertTrue(scale.Shape.isNull())
        self.assertEqual(len(scale.OutputShapes), 2)
        self.assertAlmostEqual(first_body.Shape.Volume, 8000.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 8000.0)
        self.assertAlmostEqual(first_body.Shape.BoundBox.XLength, 20.0)
        self.assertAlmostEqual(second_body.Shape.BoundBox.XMin, 25.0)
        PartDesign.validateDesign(scale)
        self._assert_dependency_graph_acyclic(self.document)

        accepted_volumes = [shape.Volume for shape in scale.OutputShapes]
        scale.Uniform = False
        scale.XScale = 2.0
        scale.YScale = 3.0
        scale.ZScale = 4.0
        self.document.recompute()
        self.assertTrue(scale.isValid(), scale.getStatusString())
        self.assertAlmostEqual(first_body.Shape.Volume, 24000.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 24000.0)
        self.assertAlmostEqual(first_body.Shape.BoundBox.XLength, 20.0)
        self.assertAlmostEqual(first_body.Shape.BoundBox.YLength, 30.0)
        self.assertAlmostEqual(first_body.Shape.BoundBox.ZLength, 40.0)

        scale.ZScale = 0.0
        self.document.recompute()
        self.assertFalse(scale.isValid())
        self.assertEqual(
            [shape.Volume for shape in scale.OutputShapes],
            [24000.0, 24000.0],
            "an invalid factor must not publish partial Body outputs",
        )
        scale.Uniform = True
        scale.UniformScale = 2.0
        scale.ZScale = 4.0
        self.document.recompute()
        self.assertTrue(scale.isValid(), scale.getStatusString())
        self.assertEqual(
            [shape.Volume for shape in scale.OutputShapes],
            accepted_volumes,
        )

        scale.Suppressed = True
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 1000.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 1000.0)
        scale.Suppressed = False
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 8000.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 8000.0)

        path = Path(self._temporary_directory.name) / "DesignScale.FCStd"
        body_names = (first_body.Name, second_body.Name)
        operation_id = str(scale.OperationId)
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reopened = self.document.getObject("SharedScale")
        self.assertEqual(str(reopened.OperationId), operation_id)
        self.assertEqual(list(reopened.OutputBodyIds), list(body_ids))
        self.assertEqual(reopened.InputStates.__len__(), 2)
        self.assertAlmostEqual(
            self.document.getObject(body_names[0]).Shape.Volume,
            8000.0,
        )
        self.assertAlmostEqual(
            self.document.getObject(body_names[1]).Shape.Volume,
            8000.0,
        )
        PartDesign.validateDesign(reopened)
        self._assert_dependency_graph_acyclic(self.document)

    def test_reference_is_one_global_definition_with_exact_body_state(self):
        source_operation, source_body = self._new_body_operation(
            "PartDesign::DesignBox",
            "ReferenceSource",
            lambda primitive: None,
        )
        source_state = source_body.Tip.CurrentState

        self.document.openTransaction("Create global Reference")
        reference = self.document.addObject(
            "PartDesign::SubShapeBinder",
            "Reference",
        )
        PartDesign.initializeDesignDefinition(reference)
        resolved, subelements = (
            PartDesign.resolveDesignDefinitionSubelementReference(
                reference,
                source_body,
                ["Face1"],
            )
        )
        self.assertEqual(
            resolved,
            PartDesign.resolveDesignDefinitionReference(
                reference,
                source_body,
            ),
        )
        self.assertEqual(resolved, source_state)
        self.assertEqual(
            resolved.Shape.getElementIndexedName(subelements[0]),
            "Face1",
        )
        self.assertNotEqual(subelements[0], "?Face1")
        reference.Support = [(resolved, tuple(subelements))]
        self.document.recompute()
        self.assertTrue(reference.isValid(), reference.getStatusString())
        self.assertFalse(reference.Shape.isNull())
        PartDesign.finalizeDesignDefinition(reference)
        self.document.commitTransaction()

        self.assertIsNone(reference.getParentGeoFeatureGroup())
        self.assertEqual(reference.Support[0][0], source_state)
        self.assertNotEqual(str(reference.VibeCADDefinitionId), "")
        self.assertEqual(
            str(reference.DesignId),
            str(self.document.VibeCADTimeline.DesignId),
        )
        self.assertEqual(reference.VibeCADTimelineRole, "operation")
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(reference),
            1,
        )
        self.assertLess(
            self.document.VibeCADTimeline.Operations.index(
                source_operation
            ),
            self.document.VibeCADTimeline.Operations.index(reference),
        )
        PartDesign.validateDesign(reference)
        self._assert_dependency_graph_acyclic(self.document)

        identity = str(reference.VibeCADDefinitionId)
        source_body_name = source_body.Name
        path = (
            Path(self._temporary_directory.name)
            / "GlobalReference.FCStd"
        )
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reference = self.document.getObject("Reference")
        source_body = self.document.getObject(source_body_name)
        self.assertEqual(str(reference.VibeCADDefinitionId), identity)
        self.assertEqual(
            reference.Support[0][0],
            source_body.Tip.CurrentState,
        )
        self.assertIsNone(reference.getParentGeoFeatureGroup())
        PartDesign.validateDesign(reference)
        self._assert_dependency_graph_acyclic(self.document)

    def test_body_patterns_create_stable_independent_body_outputs(self):
        _, source_body = self._new_body_operation(
            "PartDesign::DesignBox",
            "PatternSource",
            lambda primitive: None,
        )
        source_body_id = str(source_body.VibeCADBodyId)

        self.document.openTransaction("Create body linear pattern")
        pattern = self.document.addObject(
            "PartDesign::DesignLinearPattern",
            "BodyLinearPattern",
        )
        edit = PartDesign.beginDesignOperationEdit(pattern)
        pattern.Occurrences = 3
        pattern.Spacing = 20
        PartDesign.setDesignBodyPatternSource(edit, source_body, 2)
        self.document.recompute()
        outputs = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(pattern.PatternSource, "Body")
        self.assertIsNone(pattern.SourceOperation)
        self.assertEqual(pattern.ResultOperation, "New Bodies")
        self.assertEqual(pattern.InputStates, [source_body.Tip.CurrentState])
        self.assertEqual(pattern.InputBodyIds, [source_body_id])
        self.assertEqual(pattern.OutputPreviousInputIndices, [-1, -1])
        self.assertEqual(pattern.OutputPresence, (True, True))
        self.assertEqual(len(outputs), 2)
        first_ids = [str(body.VibeCADBodyId) for body in outputs]
        self.assertEqual(len(set(first_ids + [source_body_id])), 3)
        self.assertEqual(
            [round(body.Shape.BoundBox.XMin, 6) for body in outputs],
            [20.0, 40.0],
        )
        self.assertTrue(all(body is not source_body for body in outputs))
        self.assertAlmostEqual(source_body.Shape.Volume, 1000.0)
        PartDesign.validateDesign(pattern)
        self._assert_dependency_graph_acyclic(self.document)

        self.document.openTransaction("Grow body linear pattern")
        edit = PartDesign.beginDesignOperationEdit(pattern)
        pattern.Occurrences = 4
        PartDesign.setDesignBodyPatternSource(edit, source_body, 3)
        self.document.recompute()
        grown = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        grown_ids = [str(body.VibeCADBodyId) for body in grown]
        self.assertEqual(grown_ids[:2], first_ids)
        self.assertNotIn(grown_ids[2], first_ids + [source_body_id])
        self.assertEqual(
            [round(body.Shape.BoundBox.XMin, 6) for body in grown],
            [20.0, 40.0, 60.0],
        )

        retired_names = [grown[1].Name, grown[2].Name]
        self.document.openTransaction("Shrink body linear pattern")
        edit = PartDesign.beginDesignOperationEdit(pattern)
        pattern.Occurrences = 2
        PartDesign.setDesignBodyPatternSource(edit, source_body, 1)
        self.document.recompute()
        shrunk = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        self.assertEqual(
            [str(body.VibeCADBodyId) for body in shrunk],
            [first_ids[0]],
        )
        self.assertTrue(
            all(self.document.getObject(name) is None for name in retired_names)
        )

        pattern.Suppressed = True
        self.document.recompute()
        self.assertEqual(pattern.OutputPresence, (False,))
        self.assertTrue(shrunk[0].Shape.isNull())
        self.assertAlmostEqual(source_body.Shape.Volume, 1000.0)
        pattern.Suppressed = False
        self.document.recompute()
        self.assertEqual(pattern.OutputPresence, (True,))
        self.assertAlmostEqual(shrunk[0].Shape.Volume, 1000.0)

        self.document.openTransaction("Mirror body")
        mirror = self.document.addObject(
            "PartDesign::DesignMirror",
            "BodyMirror",
        )
        mirror_edit = PartDesign.beginDesignOperationEdit(mirror)
        mirror.PlaneNormal = App.Vector(1, 0, 0)
        PartDesign.setDesignBodyPatternSource(
            mirror_edit,
            source_body,
            1,
        )
        self.document.recompute()
        mirrored = PartDesign.finalizeDesignOperationEdit(mirror_edit)
        self.document.commitTransaction()
        self.assertEqual(len(mirrored), 1)
        self.assertAlmostEqual(mirrored[0].Shape.BoundBox.XMin, -10.0)
        self.assertAlmostEqual(mirrored[0].Shape.BoundBox.XMax, 0.0)

        self.document.openTransaction("Circular body pattern")
        circular = self.document.addObject(
            "PartDesign::DesignCircularPattern",
            "BodyCircularPattern",
        )
        circular_edit = PartDesign.beginDesignOperationEdit(circular)
        circular.Occurrences = 4
        circular.Angle = 360
        PartDesign.setDesignBodyPatternSource(
            circular_edit,
            source_body,
            3,
        )
        self.document.recompute()
        circular_bodies = PartDesign.finalizeDesignOperationEdit(
            circular_edit
        )
        self.document.commitTransaction()
        self.assertEqual(len(circular_bodies), 3)
        self.assertEqual(circular.GeneratedOccurrenceCount, 3)
        PartDesign.validateDesign(circular)
        self._assert_dependency_graph_acyclic(self.document)

        identities = {
            operation.Name: (
                str(operation.OperationId),
                tuple(operation.OutputBodyIds),
            )
            for operation in (pattern, mirror, circular)
        }
        path = (
            Path(self._temporary_directory.name)
            / "DesignBodyPatterns.FCStd"
        )
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()
        for name, expected in identities.items():
            operation = self.document.getObject(name)
            self.assertEqual(str(operation.OperationId), expected[0])
            self.assertEqual(tuple(operation.OutputBodyIds), expected[1])
            self.assertTrue(operation.isValid(), operation.getStatusString())
            PartDesign.validateDesign(operation)

    def test_feature_pattern_repeats_parametric_additive_and_cut_tools(self):
        component = self.document.addObject(
            "PartDesign::Component",
            "FeaturePatternComponent",
        )
        additive_body = self.document.addObject(
            "PartDesign::Body",
            "AdditivePatternBody",
        )
        component.addObject(additive_body)
        additive_initial = additive_body.newObject(
            "PartDesign::Feature",
            "AdditiveInitial",
        )
        additive_initial.Shape = Part.makeBox(10, 10, 10)
        self.document.recompute()

        self.document.openTransaction("Create additive source")
        additive_source = self.document.addObject(
            "PartDesign::DesignBox",
            "AdditivePatternSource",
        )
        additive_source.Placement.Base.x = 8
        additive_edit = PartDesign.beginDesignOperationEdit(
            additive_source
        )
        PartDesign.setDesignOperationTargets(
            additive_edit,
            "Join",
            [additive_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(additive_edit)
        self.document.commitTransaction()
        self.assertAlmostEqual(additive_body.Shape.Volume, 1800.0)

        self.document.openTransaction("Create additive feature pattern")
        additive_pattern = self.document.addObject(
            "PartDesign::DesignLinearPattern",
            "AdditiveFeaturePattern",
        )
        pattern_edit = PartDesign.beginDesignOperationEdit(
            additive_pattern
        )
        additive_pattern.Occurrences = 2
        additive_pattern.Spacing = 8
        PartDesign.setDesignFeaturePatternTargets(
            pattern_edit,
            additive_source,
            [additive_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(pattern_edit)
        self.document.commitTransaction()
        self.assertEqual(additive_pattern.PatternSource, "Feature")
        self.assertEqual(
            additive_pattern.SourceOperation,
            additive_source,
        )
        self.assertEqual(additive_pattern.ResultOperation, "Join")
        self.assertAlmostEqual(additive_body.Shape.Volume, 2600.0)

        cut_body = self.document.addObject(
            "PartDesign::Body",
            "CutPatternBody",
        )
        component.addObject(cut_body)
        cut_initial = cut_body.newObject(
            "PartDesign::Feature",
            "CutInitial",
        )
        cut_initial.Shape = Part.makeBox(30, 10, 10)
        self.document.recompute()

        self.document.openTransaction("Create cut source")
        cut_source = self.document.addObject(
            "PartDesign::DesignBox",
            "CutPatternSource",
        )
        cut_source.Length = 2
        cut_source.Width = 4
        cut_source.Height = 4
        cut_source.Placement.Base.x = 2
        cut_source.Placement.Base.y = 3
        cut_source.Placement.Base.z = 3
        cut_edit = PartDesign.beginDesignOperationEdit(cut_source)
        PartDesign.setDesignOperationTargets(
            cut_edit,
            "Cut",
            [cut_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(cut_edit)
        self.document.commitTransaction()
        self.assertAlmostEqual(cut_body.Shape.Volume, 2968.0)

        self.document.openTransaction("Create cut feature pattern")
        cut_pattern = self.document.addObject(
            "PartDesign::DesignLinearPattern",
            "CutFeaturePattern",
        )
        cut_pattern_edit = PartDesign.beginDesignOperationEdit(cut_pattern)
        cut_pattern.Occurrences = 3
        cut_pattern.Spacing = 10
        PartDesign.setDesignFeaturePatternTargets(
            cut_pattern_edit,
            cut_source,
            [cut_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(cut_pattern_edit)
        self.document.commitTransaction()
        self.assertEqual(cut_pattern.ResultOperation, "Cut")
        self.assertAlmostEqual(cut_body.Shape.Volume, 2904.0)

        cut_source.Length = 3
        self.document.recompute()
        self.assertAlmostEqual(cut_body.Shape.Volume, 2856.0)
        self.assertEqual(
            cut_pattern.InputStates[0].Operation,
            cut_source,
        )
        PartDesign.validateDesign(additive_pattern)
        PartDesign.validateDesign(cut_pattern)
        self._assert_dependency_graph_acyclic(self.document)

        path = (
            Path(self._temporary_directory.name)
            / "DesignFeaturePatterns.FCStd"
        )
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()
        reopened_cut = self.document.getObject("CutPatternBody")
        reopened_pattern = self.document.getObject("CutFeaturePattern")
        self.assertAlmostEqual(reopened_cut.Shape.Volume, 2856.0)
        self.assertEqual(
            reopened_pattern.SourceOperation.Name,
            "CutPatternSource",
        )
        PartDesign.validateDesign(reopened_pattern)

    def test_one_design_cut_advances_bodies_across_components(self):
        _, first_body, first_input = self._component_body(
            "First",
            0,
        )
        _, second_body, second_input = self._component_body(
            "Second",
            15,
        )
        sketch = self._rectangle_sketch("SharedSketch", 5, 20, 2, 8)

        self.document.openTransaction("Create Shared Cut")
        operation = self.document.addObject(
            "PartDesign::DesignExtrude",
            "SharedCut",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = sketch
        operation.Length = 10
        PartDesign.setDesignOperationTargets(
            edit,
            "Cut",
            [first_body, second_body],
        )
        self.document.recompute()
        finalized_bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        first_publication = first_body.Tip
        second_publication = second_body.Tip
        first_result = first_publication.CurrentState
        second_result = second_publication.CurrentState
        first_publication_name = first_publication.Name
        second_publication_name = second_publication.Name
        first_result_name = first_result.Name
        second_result_name = second_result.Name

        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertTrue(first_result.isValid(), first_result.getStatusString())
        self.assertTrue(second_result.isValid(), second_result.getStatusString())
        self.assertEqual(len(operation.OutputShapes), 2)
        self.assertAlmostEqual(first_body.Shape.Volume, 700.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 700.0)
        self.assertIs(first_body.Tip, first_publication)
        self.assertIs(second_body.Tip, second_publication)
        self.assertIs(sketch.getParentGeoFeatureGroup(), None)
        self.assertEqual(operation.Profile[0], sketch)
        self.assertEqual(operation.InputStates, [first_input, second_input])
        self.assertEqual(
            operation.InputBodyIds,
            [
                first_body.VibeCADBodyId,
                second_body.VibeCADBodyId,
            ],
        )
        self.assertEqual(
            operation.OutputBodyIds,
            operation.InputBodyIds,
        )
        self.assertEqual(
            operation.OutputPreviousInputIndices,
            [0, 1],
        )
        self.assertEqual(operation.OutputPresence, (True, True))
        self.assertEqual(operation.OutputComponentIds, ["", ""])
        self.assertEqual(
            operation.TargetBodyIds,
            operation.OutputBodyIds,
            "the legacy target list must remain a compatibility mirror",
        )
        self.assertEqual(first_result.Shape.BoundBox.XMin, 0.0)
        self.assertEqual(second_result.Shape.BoundBox.XMin, 0.0)
        self.assertEqual(first_publication.Shape.BoundBox.XMin, 0.0)
        self.assertEqual(second_publication.Shape.BoundBox.XMin, 0.0)
        self.assertIs(first_result.getParentGeoFeatureGroup(), None)
        self.assertIs(second_result.getParentGeoFeatureGroup(), None)
        self.assertEqual(finalized_bodies, [first_body, second_body])
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

        operation.Length = 5
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 850.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 850.0)

        operation.Suppressed = True
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 1000.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 1000.0)

        operation.Suppressed = False
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 850.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 850.0)

        accepted_outputs = [shape.Volume for shape in operation.OutputShapes]
        sketch.Placement.Base.x = 100
        self.document.recompute()
        self.assertFalse(operation.isValid())
        self.assertEqual(
            [shape.Volume for shape in operation.OutputShapes],
            accepted_outputs,
            "a failed target must not publish a partial multi-Body output set",
        )
        sketch.Placement.Base.x = 0
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())

        identities = {
            "sketch": str(sketch.VibeCADSketchId),
            "first_body": str(first_body.VibeCADBodyId),
            "second_body": str(second_body.VibeCADBodyId),
            "operation": str(operation.OperationId),
            "first_state": str(first_result.BodyStateId),
            "second_state": str(second_result.BodyStateId),
        }
        path = Path(self._temporary_directory.name) / "DesignModeling.FCStd"
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reopened_sketch = self.document.getObject("SharedSketch")
        reopened_first_body = self.document.getObject("FirstBody")
        reopened_second_body = self.document.getObject("SecondBody")
        reopened_operation = self.document.getObject("SharedCut")
        reopened_first_result = self.document.getObject(first_result_name)
        reopened_second_result = self.document.getObject(second_result_name)

        self.assertEqual(str(reopened_sketch.VibeCADSketchId), identities["sketch"])
        self.assertEqual(
            str(reopened_first_body.VibeCADBodyId),
            identities["first_body"],
        )
        self.assertEqual(
            str(reopened_second_body.VibeCADBodyId),
            identities["second_body"],
        )
        self.assertEqual(
            str(reopened_operation.OperationId),
            identities["operation"],
        )
        self.assertEqual(
            str(reopened_first_result.BodyStateId),
            identities["first_state"],
        )
        self.assertEqual(
            str(reopened_second_result.BodyStateId),
            identities["second_state"],
        )
        self.assertIs(reopened_operation.Profile[0], reopened_sketch)
        self.assertEqual(
            reopened_operation.InputStates,
            [
                self.document.getObject("FirstImportedState"),
                self.document.getObject("SecondImportedState"),
            ],
        )
        self.assertIs(reopened_first_result.Operation, reopened_operation)
        self.assertIs(reopened_second_result.Operation, reopened_operation)
        self.assertIs(
            self.document.getObject(first_publication_name).CurrentState,
            reopened_first_result,
        )
        self.assertIs(
            self.document.getObject(second_publication_name).CurrentState,
            reopened_second_result,
        )
        self.assertAlmostEqual(reopened_first_body.Shape.Volume, 850.0)
        self.assertAlmostEqual(reopened_second_body.Shape.Volume, 850.0)
        PartDesign.validateDesign(reopened_operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_design_hole_cuts_exact_bodies_from_one_global_sketch(self):
        _, first_body, first_input = self._component_body("FirstHole", 0)
        second_component, second_body, second_input = self._component_body(
            "SecondHole",
            15,
        )
        sketch = self._hole_sketch(
            "SharedHoleLocations",
            [(5, 5), (20, 5)],
            10,
        )

        self.document.openTransaction("Create Shared Hole")
        operation = self.document.addObject(
            "PartDesign::DesignHole",
            "SharedHole",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = sketch
        operation.Diameter = 2
        operation.Depth = 10
        operation.DepthType = "Dimension"
        operation.DrillPoint = "Flat"
        PartDesign.setDesignOperationTargets(
            edit,
            "Cut",
            [first_body, second_body],
        )
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        affected = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        expected_volume = 1000.0 - 10.0 * 3.141592653589793
        self.assertEqual(affected, [first_body, second_body])
        self.assertEqual(operation.ResultOperation, "Cut")
        self.assertIsNone(operation.BaseFeature)
        self.assertIsNone(operation.getParentGeoFeatureGroup())
        self.assertIsNone(sketch.getParentGeoFeatureGroup())
        self.assertEqual(operation.Profile[0], sketch)
        self.assertEqual(operation.InputStates, [first_input, second_input])
        self.assertEqual(operation.OutputBodyIds, operation.InputBodyIds)
        self.assertEqual(operation.OutputPreviousInputIndices, [0, 1])
        self.assertAlmostEqual(first_body.Shape.Volume, expected_volume, 5)
        self.assertAlmostEqual(second_body.Shape.Volume, expected_volume, 5)
        self.assertEqual(len(operation.AddSubShape.Solids), 2)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

        operation.DepthType = "ThroughAll"
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertGreater(operation.Depth.Value, 10)
        self.assertAlmostEqual(first_body.Shape.Volume, expected_volume, 5)
        self.assertAlmostEqual(second_body.Shape.Volume, expected_volume, 5)
        def shape_signature(shape):
            bounds = shape.BoundBox
            center = shape.Solids[0].CenterOfMass
            return (
                shape.Volume,
                shape.Area,
                center.x,
                center.y,
                center.z,
                bounds.XMin,
                bounds.XMax,
                bounds.YMin,
                bounds.YMax,
                bounds.ZMin,
                bounds.ZMax,
            )

        first_shape = shape_signature(first_body.Shape)
        second_shape = shape_signature(second_body.Shape)

        second_component.Placement.Base.x = 40
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        for actual, expected in zip(
            shape_signature(first_body.Shape),
            first_shape,
        ):
            self.assertAlmostEqual(actual, expected, 7)
        for actual, expected in zip(
            shape_signature(second_body.Shape),
            second_shape,
        ):
            self.assertAlmostEqual(actual, expected, 7)

        operation.Suppressed = True
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 1000.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 1000.0)
        operation.Suppressed = False
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, expected_volume, 5)
        self.assertAlmostEqual(second_body.Shape.Volume, expected_volume, 5)

        self.document.openTransaction("Reject invalid Hole mode")
        invalid_edit = PartDesign.beginDesignOperationEdit(operation)
        with self.assertRaises(RuntimeError):
            PartDesign.setDesignOperationTargets(
                invalid_edit,
                "Join",
                [first_body],
            )
        self.document.abortTransaction()
        self.assertEqual(operation.ResultOperation, "Cut")

        path = (
            Path(self._temporary_directory.name)
            / "DesignHole.FCStd"
        )
        operation_id = str(operation.OperationId)
        first_state_id = str(first_body.Tip.CurrentState.BodyStateId)
        second_state_id = str(second_body.Tip.CurrentState.BodyStateId)
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reopened = self.document.getObject("SharedHole")
        reopened_first = self.document.getObject("FirstHoleBody")
        reopened_second = self.document.getObject("SecondHoleBody")
        self.assertEqual(str(reopened.OperationId), operation_id)
        self.assertEqual(
            str(reopened_first.Tip.CurrentState.BodyStateId),
            first_state_id,
        )
        self.assertEqual(
            str(reopened_second.Tip.CurrentState.BodyStateId),
            second_state_id,
        )
        self.assertAlmostEqual(reopened_first.Shape.Volume, expected_volume, 5)
        self.assertAlmostEqual(reopened_second.Shape.Volume, expected_volume, 5)
        PartDesign.validateDesign(reopened)
        self._assert_dependency_graph_acyclic(self.document)

    def test_design_hole_preserves_native_counter_forms_and_modeled_threads(self):
        _, body, _ = self._component_body("DetailedHole", 0)
        sketch = self._hole_sketch(
            "DetailedHoleLocation",
            [(5, 5)],
            10,
        )

        self.document.openTransaction("Create Detailed Hole")
        operation = self.document.addObject(
            "PartDesign::DesignHole",
            "DetailedHole",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = sketch
        operation.Diameter = 6
        operation.Depth = 10
        operation.DepthType = "Dimension"
        operation.DrillPoint = "Flat"
        operation.HoleCutType = "Counterbore"
        operation.HoleCutDiameter = 8
        operation.HoleCutDepth = 5
        PartDesign.setDesignOperationTargets(edit, "Cut", [body])
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        counterbore_volume = (
            1000.0
            - 5.0 * 3.141592653589793 * 4.0**2
            - 5.0 * 3.141592653589793 * 3.0**2
        )
        self.assertAlmostEqual(body.Shape.Volume, counterbore_volume, 5)

        operation.HoleCutType = "Countersink"
        operation.HoleCutDiameter = 9
        operation.HoleCutCountersinkAngle = 90
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertLess(body.Shape.Volume, 1000.0 - 10.0 * 3.141592653589793 * 3.0**2)

        operation.HoleCutType = "None"
        operation.ThreadType = "ISOMetricProfile"
        operation.ThreadSize = "M6x1.0"
        operation.Threaded = True
        operation.ThreadDepthType = "Dimension"
        operation.ThreadDepth = 8
        operation.ModelThread = False
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        smooth_thread_volume = body.Shape.Volume
        smooth_thread_faces = len(body.Shape.Faces)

        operation.ModelThread = True
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertNotAlmostEqual(body.Shape.Volume, smooth_thread_volume, 5)
        self.assertGreater(len(body.Shape.Faces), smooth_thread_faces)
        self.assertGreater(len(operation.AddSubShape.Faces), 3)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_suppressed_creation_preserves_identity_as_an_absent_body(self):
        sketch = self._rectangle_sketch("CreationSketch", 0, 8, 0, 6)
        operation, body = self._new_body_operation(
            "PartDesign::DesignExtrude",
            "CreatedBody",
            lambda feature: (
                setattr(feature, "Profile", sketch),
                setattr(feature, "Length", 4),
            ),
        )
        publication = body.Tip
        state = publication.CurrentState
        body_id = body.VibeCADBodyId
        state_id = state.BodyStateId
        volume = body.Shape.Volume

        operation.Suppressed = True
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertEqual(operation.OutputPresence, (False,))
        self.assertFalse(state.Present)
        self.assertTrue(state.Shape.isNull())
        self.assertTrue(publication.Shape.isNull())
        self.assertTrue(body.Shape.isNull())
        self.assertEqual(body.VibeCADBodyId, body_id)
        self.assertEqual(state.BodyStateId, state_id)
        PartDesign.validateDesign(operation)

        operation.Suppressed = False
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertEqual(operation.OutputPresence, (True,))
        self.assertTrue(state.Present)
        self.assertAlmostEqual(body.Shape.Volume, volume)
        self.assertIs(body.Tip, publication)
        self.assertIs(publication.CurrentState, state)
        self.assertEqual(body.VibeCADBodyId, body_id)
        self.assertEqual(state.BodyStateId, state_id)
        PartDesign.validateDesign(operation)

    def test_combine_consumes_and_restores_tool_body_identity(self):
        _, result_body, result_input = self._component_body("Result", 0)
        _, tool_body, tool_input = self._component_body("Tool", 5)

        self.document.openTransaction("Create Combine")
        combine = self.document.addObject(
            "PartDesign::DesignCombine",
            "Combine",
        )
        edit = PartDesign.beginDesignOperationEdit(combine)
        PartDesign.setDesignCombineBodies(
            edit,
            "Join",
            result_body,
            [tool_body],
            False,
        )
        affected = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        result_state = result_body.Tip.CurrentState
        consumed_state = tool_body.Tip.CurrentState
        result_state_id = str(result_state.BodyStateId)
        consumed_state_name = consumed_state.Name
        self.assertEqual(affected, [result_body, tool_body])
        self.assertEqual(combine.ResultOperation, "Join")
        self.assertEqual(
            str(combine.ResultBodyId),
            str(result_body.VibeCADBodyId),
        )
        self.assertFalse(combine.KeepTools)
        self.assertEqual(
            combine.InputStates,
            [result_input, tool_input],
        )
        self.assertEqual(
            combine.OutputBodyIds,
            [
                str(result_body.VibeCADBodyId),
                str(tool_body.VibeCADBodyId),
            ],
        )
        self.assertEqual(
            combine.OutputPreviousInputIndices,
            [0, 1],
        )
        self.assertEqual(combine.OutputPresence, (True, False))
        self.assertAlmostEqual(result_body.Shape.Volume, 1500.0)
        self.assertTrue(tool_body.Shape.isNull())
        self.assertTrue(result_state.Present)
        self.assertFalse(consumed_state.Present)
        PartDesign.validateDesign(combine)
        self._assert_dependency_graph_acyclic(self.document)

        combine.Suppressed = True
        self.document.recompute()
        self.assertEqual(combine.OutputPresence, (True, True))
        self.assertAlmostEqual(result_body.Shape.Volume, 1000.0)
        self.assertAlmostEqual(tool_body.Shape.Volume, 1000.0)
        self.assertTrue(consumed_state.Present)
        PartDesign.validateDesign(combine)

        combine.Suppressed = False
        self.document.recompute()
        self.assertEqual(combine.OutputPresence, (True, False))
        self.assertAlmostEqual(result_body.Shape.Volume, 1500.0)
        self.assertTrue(tool_body.Shape.isNull())

        self.document.openTransaction("Keep Combine Tools")
        edit = PartDesign.beginDesignOperationEdit(combine)
        PartDesign.setDesignCombineBodies(
            edit,
            "Join",
            result_body,
            [tool_body],
            True,
        )
        affected = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(affected, [result_body])
        self.assertTrue(combine.KeepTools)
        self.assertEqual(
            combine.OutputBodyIds,
            [str(result_body.VibeCADBodyId)],
        )
        self.assertEqual(combine.OutputPresence, (True,))
        self.assertIs(result_body.Tip.CurrentState, result_state)
        self.assertEqual(
            str(result_body.Tip.CurrentState.BodyStateId),
            result_state_id,
        )
        self.assertIs(tool_body.Tip.CurrentState, tool_input)
        self.assertIsNone(self.document.getObject(consumed_state_name))
        self.assertAlmostEqual(result_body.Shape.Volume, 1500.0)
        self.assertAlmostEqual(tool_body.Shape.Volume, 1000.0)
        PartDesign.validateDesign(combine)
        self._assert_dependency_graph_acyclic(self.document)

        self.document.undo()
        self.document.recompute()
        restored_combine = self.document.getObject("Combine")
        restored_tool = self.document.getObject("ToolBody")
        self.assertFalse(restored_combine.KeepTools)
        self.assertEqual(restored_combine.OutputPresence, (True, False))
        self.assertTrue(restored_tool.Shape.isNull())
        PartDesign.validateDesign(restored_combine)

        self.document.redo()
        self.document.recompute()
        redone_combine = self.document.getObject("Combine")
        redone_result = self.document.getObject("ResultBody")
        redone_tool = self.document.getObject("ToolBody")
        self.assertTrue(redone_combine.KeepTools)
        self.assertAlmostEqual(redone_result.Shape.Volume, 1500.0)
        self.assertAlmostEqual(redone_tool.Shape.Volume, 1000.0)
        PartDesign.validateDesign(redone_combine)

        path = Path(self._temporary_directory.name) / "DesignCombine.FCStd"
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()
        reopened_combine = self.document.getObject("Combine")
        reopened_result = self.document.getObject("ResultBody")
        reopened_tool = self.document.getObject("ToolBody")
        self.assertTrue(reopened_combine.KeepTools)
        self.assertAlmostEqual(reopened_result.Shape.Volume, 1500.0)
        self.assertAlmostEqual(reopened_tool.Shape.Volume, 1000.0)
        PartDesign.validateDesign(reopened_combine)
        self._assert_dependency_graph_acyclic(self.document)

    def test_combine_cut_uses_saved_cross_component_frames(self):
        _, result_body, _ = self._component_body("Result", 0)
        tool_component, tool_body, _ = self._component_body("Tool", 5)

        self.document.openTransaction("Create Combine Cut")
        combine = self.document.addObject(
            "PartDesign::DesignCombine",
            "CombineCut",
        )
        edit = PartDesign.beginDesignOperationEdit(combine)
        PartDesign.setDesignCombineBodies(
            edit,
            "Cut",
            result_body,
            [tool_body],
            True,
        )
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertAlmostEqual(result_body.Shape.Volume, 500.0)
        self.assertAlmostEqual(result_body.Shape.BoundBox.XMax, 5.0)
        self.assertAlmostEqual(tool_body.Shape.Volume, 1000.0)
        self.assertEqual(combine.OutputPresence, (True,))
        accepted_frame = combine.InputFrames[1]
        PartDesign.validateDesign(combine)

        tool_component.Placement.Base.x = 30
        self.document.recompute()
        self.assertEqual(combine.InputFrames[1], accepted_frame)
        self.assertAlmostEqual(result_body.Shape.Volume, 500.0)
        self.assertAlmostEqual(result_body.Shape.BoundBox.XMax, 5.0)
        self.assertAlmostEqual(tool_body.Shape.Volume, 1000.0)
        PartDesign.validateDesign(combine)
        self._assert_dependency_graph_acyclic(self.document)

    def test_split_persists_explicit_region_identity_across_lifecycle(self):
        component, source_body, source_input = self._component_body(
            "Source",
            0,
        )
        splitter = self.document.addObject(
            "PartDesign::Feature",
            "SplitterPlane",
        )
        splitter.Shape = Part.makePlane(
            30,
            30,
            App.Vector(5, 20, -10),
            App.Vector(1, 0, 0),
        )

        self.document.openTransaction("Create Split")
        operation = self.document.addObject(
            "PartDesign::DesignSplit",
            "Split",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        witnesses = PartDesign.setDesignSplitDefinition(
            edit,
            source_body,
            [splitter],
        )
        self.assertEqual(len(witnesses), 2)
        retained_region = min(
            range(len(witnesses)),
            key=lambda index: witnesses[index].x,
        )
        PartDesign.assignDesignSplitRegions(
            edit,
            source_body,
            witnesses,
            retained_region,
        )
        affected = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(len(affected), 2)
        retained_body, created_body = affected
        self.assertIs(retained_body, source_body)
        self.assertIs(
            created_body.getParentGeoFeatureGroup(),
            component,
        )
        self.assertEqual(operation.ResultOperation, "Split")
        self.assertEqual(operation.InputStates, [source_input])
        self.assertEqual(
            operation.OutputPreviousInputIndices,
            [0, -1],
        )
        self.assertEqual(operation.OutputPresence, (True, True))
        self.assertEqual(
            operation.OutputComponentIds,
            ["", str(component.ComponentId)],
        )
        self.assertEqual(
            str(operation.SourceBodyId),
            str(source_body.VibeCADBodyId),
        )
        self.assertTrue(operation.RetainedRegionChosen)
        self.assertAlmostEqual(source_body.Shape.Volume, 500.0)
        self.assertAlmostEqual(created_body.Shape.Volume, 500.0)
        self.assertAlmostEqual(
            source_body.Placement.Base.x,
            created_body.Placement.Base.x,
        )
        source_body_id = str(source_body.VibeCADBodyId)
        created_body_id = str(created_body.VibeCADBodyId)
        source_state_id = str(source_body.Tip.CurrentState.BodyStateId)
        created_state_id = str(created_body.Tip.CurrentState.BodyStateId)
        source_name = source_body.Name
        created_name = created_body.Name
        accepted_frames = list(operation.OutputFrames)
        accepted_witnesses = list(operation.RegionWitnesses)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

        self.document.undo()
        self.document.recompute()
        self.assertIsNone(self.document.getObject("Split"))
        self.assertIsNone(self.document.getObject(created_name))
        self.document.redo()
        self.document.recompute()
        operation = self.document.getObject("Split")
        source_body = self.document.getObject(source_name)
        created_body = self.document.getObject(created_name)
        component = self.document.getObject("SourceComponent")
        splitter = self.document.getObject("SplitterPlane")
        self.assertEqual(
            str(source_body.VibeCADBodyId),
            source_body_id,
        )
        self.assertEqual(
            str(created_body.VibeCADBodyId),
            created_body_id,
        )
        self.assertEqual(
            str(source_body.Tip.CurrentState.BodyStateId),
            source_state_id,
        )
        self.assertEqual(
            str(created_body.Tip.CurrentState.BodyStateId),
            created_state_id,
        )
        PartDesign.validateDesign(operation)

        splitter.Placement.Base.x = 1
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertAlmostEqual(source_body.Shape.Volume, 600.0)
        self.assertAlmostEqual(created_body.Shape.Volume, 400.0)
        self.assertEqual(
            str(source_body.VibeCADBodyId),
            source_body_id,
        )
        self.assertEqual(
            str(created_body.VibeCADBodyId),
            created_body_id,
        )
        self.assertEqual(
            list(operation.RegionWitnesses),
            accepted_witnesses,
        )

        component.Placement.Base.x = 25
        self.document.recompute()
        self.assertEqual(list(operation.OutputFrames), accepted_frames)
        self.assertAlmostEqual(source_body.Shape.Volume, 600.0)
        self.assertAlmostEqual(created_body.Shape.Volume, 400.0)

        operation.Suppressed = True
        self.document.recompute()
        self.assertEqual(operation.OutputPresence, (True, False))
        self.assertAlmostEqual(source_body.Shape.Volume, 1000.0)
        self.assertTrue(created_body.Shape.isNull())
        PartDesign.validateDesign(operation)

        operation.Suppressed = False
        self.document.recompute()
        self.assertEqual(operation.OutputPresence, (True, True))
        self.assertAlmostEqual(source_body.Shape.Volume, 600.0)
        self.assertAlmostEqual(created_body.Shape.Volume, 400.0)

        splitter.Placement.Base.x = 4
        self.document.recompute()
        self.assertFalse(operation.isValid())
        self.assertIn(
            "explicitly reassign",
            operation.getStatusString(),
        )
        self.assertAlmostEqual(source_body.Shape.Volume, 600.0)
        self.assertAlmostEqual(created_body.Shape.Volume, 400.0)
        splitter.Placement.Base.x = 1
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())

        path = Path(self._temporary_directory.name) / "DesignSplit.FCStd"
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reopened = self.document.getObject("Split")
        reopened_source = self.document.getObject(source_name)
        reopened_created = self.document.getObject(created_name)
        self.assertEqual(
            str(reopened_source.VibeCADBodyId),
            source_body_id,
        )
        self.assertEqual(
            str(reopened_created.VibeCADBodyId),
            created_body_id,
        )
        self.assertEqual(
            str(reopened_source.Tip.CurrentState.BodyStateId),
            source_state_id,
        )
        self.assertEqual(
            str(reopened_created.Tip.CurrentState.BodyStateId),
            created_state_id,
        )
        self.assertEqual(
            list(reopened.RegionWitnesses),
            accepted_witnesses,
        )
        self.assertAlmostEqual(reopened_source.Shape.Volume, 600.0)
        self.assertAlmostEqual(reopened_created.Shape.Volume, 400.0)
        PartDesign.validateDesign(reopened)
        self._assert_dependency_graph_acyclic(self.document)

    def test_separate_persists_one_body_identity_per_source_solid(self):
        self.document.openTransaction("Create reusable multi-solid definition")
        source = self.document.addObject(
            "Part::Feature",
            "MultiSolidDefinition",
        )
        source.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 10, 10),
                Part.makeBox(
                    8,
                    10,
                    10,
                    App.Vector(20, 0, 0),
                ),
            ]
        )
        PartDesign.finalizeDesignDefinition(source)
        self.document.commitTransaction()

        self.document.openTransaction("Separate solids")
        operation = self.document.addObject(
            "PartDesign::DesignSeparate",
            "Separate",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignSeparateDefinition(edit, source)
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(len(bodies), 2)
        self.assertIsNone(operation.getParentGeoFeatureGroup())
        self.assertEqual(operation.Source, source)
        self.assertEqual(operation.ResultOperation, "New Bodies")
        self.assertEqual(operation.InputStates, [])
        self.assertEqual(operation.InputBodyIds, [])
        self.assertEqual(operation.OutputPreviousInputIndices, [-1, -1])
        self.assertEqual(operation.OutputPresence, (True, True))
        self.assertEqual(len(operation.RegionWitnesses), 2)
        self.assertEqual(
            sorted(round(body.Shape.Volume) for body in bodies),
            [800, 1000],
        )
        self.assertTrue(
            all(body.Tip.CurrentState.Operation is operation for body in bodies)
        )
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(operation),
            1,
        )
        self.assertLess(
            self.document.VibeCADTimeline.Operations.index(source),
            self.document.VibeCADTimeline.Operations.index(operation),
        )
        body_names = [body.Name for body in bodies]
        body_ids = [str(body.VibeCADBodyId) for body in bodies]
        state_ids = [
            str(body.Tip.CurrentState.BodyStateId)
            for body in bodies
        ]
        accepted_witnesses = list(operation.RegionWitnesses)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

        source.Shape = Part.makeCompound(
            [
                Part.makeBox(12, 10, 10),
                Part.makeBox(
                    9,
                    10,
                    10,
                    App.Vector(20, 0, 0),
                ),
            ]
        )
        self.document.recompute()
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertEqual(
            [str(self.document.getObject(name).VibeCADBodyId)
             for name in body_names],
            body_ids,
        )
        self.assertEqual(
            [str(self.document.getObject(name).Tip.CurrentState.BodyStateId)
             for name in body_names],
            state_ids,
        )
        self.assertEqual(
            sorted(
                round(self.document.getObject(name).Shape.Volume)
                for name in body_names
            ),
            [900, 1200],
        )
        self.assertEqual(
            list(operation.RegionWitnesses),
            accepted_witnesses,
        )

        operation.Suppressed = True
        self.document.recompute()
        self.assertEqual(operation.OutputPresence, (False, False))
        self.assertTrue(
            all(
                self.document.getObject(name).Shape.isNull()
                for name in body_names
            )
        )
        PartDesign.validateDesign(operation)

        operation.Suppressed = False
        self.document.recompute()
        self.assertEqual(operation.OutputPresence, (True, True))
        self.assertEqual(
            sorted(
                round(self.document.getObject(name).Shape.Volume)
                for name in body_names
            ),
            [900, 1200],
        )

        path = (
            Path(self._temporary_directory.name)
            / "DesignSeparate.FCStd"
        )
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.UndoMode = True
        self.document.recompute()

        reopened = self.document.getObject("Separate")
        self.assertEqual(
            [str(self.document.getObject(name).VibeCADBodyId)
             for name in body_names],
            body_ids,
        )
        self.assertEqual(
            [str(self.document.getObject(name).Tip.CurrentState.BodyStateId)
             for name in body_names],
            state_ids,
        )
        self.assertEqual(
            list(reopened.RegionWitnesses),
            accepted_witnesses,
        )
        PartDesign.validateDesign(reopened)
        self._assert_dependency_graph_acyclic(self.document)

        self.document.openTransaction("Delete Separate")
        removed = PartDesign.removeDesignOperation(reopened)
        self.document.commitTransaction()
        self.assertEqual(removed, body_names)
        self.assertIsNone(self.document.getObject("Separate"))
        self.assertTrue(
            all(self.document.getObject(name) is None for name in body_names)
        )

        self.document.undo()
        self.document.recompute()
        restored = self.document.getObject("Separate")
        self.assertIsNotNone(restored)
        self.assertEqual(
            [str(self.document.getObject(name).VibeCADBodyId)
             for name in body_names],
            body_ids,
        )
        PartDesign.validateDesign(restored)

        self.document.redo()
        self.document.recompute()
        self.assertIsNone(self.document.getObject("Separate"))
        self.assertTrue(
            all(self.document.getObject(name) is None for name in body_names)
        )

    def test_separate_reconciles_added_and_removed_solids_without_guessing(self):
        self.document.openTransaction("Create changing definition")
        component = self.document.addObject(
            "PartDesign::Component",
            "SeparateComponent",
        )
        source = self.document.addObject(
            "Part::Feature",
            "ChangingMultiSolidDefinition",
        )
        source.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 10, 10),
                Part.makeBox(8, 10, 10, App.Vector(20, 0, 0)),
            ]
        )
        PartDesign.finalizeDesignDefinition(source)
        self.document.commitTransaction()

        self.document.openTransaction("Create changing Separate")
        operation = self.document.addObject(
            "PartDesign::DesignSeparate",
            "ChangingSeparate",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignSeparateDefinition(edit, source, component)
        initial_bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        initial_ids = [str(body.VibeCADBodyId) for body in initial_bodies]
        initial_state_ids = [
            str(body.Tip.CurrentState.BodyStateId)
            for body in initial_bodies
        ]
        self.assertEqual(len(initial_ids), 2)
        self.assertTrue(
            all(body.getParentGeoFeatureGroup() is component for body in initial_bodies)
        )

        self.document.openTransaction("Add one Separate source solid")
        source.Shape = Part.makeCompound(
            [
                Part.makeBox(12, 10, 10),
                Part.makeBox(9, 10, 10, App.Vector(20, 0, 0)),
                Part.makeBox(6, 10, 10, App.Vector(40, 0, 0)),
            ]
        )
        add_edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignSeparateDefinition(add_edit, source, component)
        added_bodies = PartDesign.finalizeDesignOperationEdit(add_edit)
        self.document.commitTransaction()

        added_ids = [str(body.VibeCADBodyId) for body in added_bodies]
        added_state_ids = [
            str(body.Tip.CurrentState.BodyStateId)
            for body in added_bodies
        ]
        self.assertEqual(added_ids[:2], initial_ids)
        self.assertEqual(added_state_ids[:2], initial_state_ids)
        self.assertNotIn(added_ids[2], initial_ids)
        self.assertEqual(
            sorted(round(body.Shape.Volume) for body in added_bodies),
            [600, 900, 1200],
        )
        self.assertEqual(operation.OutputPreviousInputIndices, [-1, -1, -1])
        self.assertEqual(
            operation.OutputComponentIds,
            [str(component.ComponentId)] * 3,
        )
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

        self.document.undo()
        self.document.recompute()
        self.assertEqual(
            [str(body.VibeCADBodyId) for body in initial_bodies],
            initial_ids,
        )
        self.assertIsNone(
            next(
                (
                    body
                    for body in self.document.findObjects("PartDesign::Body")
                    if str(body.VibeCADBodyId) == added_ids[2]
                ),
                None,
            )
        )
        PartDesign.validateDesign(operation)

        self.document.redo()
        self.document.recompute()
        operation = self.document.getObject("ChangingSeparate")
        component = self.document.getObject("SeparateComponent")
        source = self.document.getObject("ChangingMultiSolidDefinition")
        self.assertEqual(
            list(operation.OutputBodyIds),
            added_ids,
        )

        self.document.openTransaction("Remove one Separate source solid")
        source.Shape = Part.makeCompound(
            [
                Part.makeBox(12, 10, 10),
                Part.makeBox(6, 10, 10, App.Vector(40, 0, 0)),
            ]
        )
        remove_edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignSeparateDefinition(remove_edit, source, component)
        remaining_bodies = PartDesign.finalizeDesignOperationEdit(remove_edit)
        self.document.commitTransaction()

        self.assertEqual(
            [str(body.VibeCADBodyId) for body in remaining_bodies],
            [added_ids[0], added_ids[2]],
        )
        self.assertEqual(
            [
                str(body.Tip.CurrentState.BodyStateId)
                for body in remaining_bodies
            ],
            [added_state_ids[0], added_state_ids[2]],
        )
        self.assertIsNone(
            next(
                (
                    body
                    for body in self.document.findObjects("PartDesign::Body")
                    if str(body.VibeCADBodyId) == added_ids[1]
                ),
                None,
            )
        )
        self.assertEqual(
            sorted(round(body.Shape.Volume) for body in remaining_bodies),
            [600, 1200],
        )
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

        path = (
            Path(self._temporary_directory.name)
            / "ReconciledDesignSeparate.FCStd"
        )
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()
        reopened = self.document.getObject("ChangingSeparate")
        self.assertEqual(
            list(reopened.OutputBodyIds),
            [added_ids[0], added_ids[2]],
        )
        self.assertEqual(len(reopened.RegionWitnesses), 2)
        PartDesign.validateDesign(reopened)
        self._assert_dependency_graph_acyclic(self.document)

    def test_separate_refuses_to_retire_a_body_with_downstream_history(self):
        self.document.openTransaction("Create consumed definition")
        source = self.document.addObject(
            "Part::Feature",
            "ConsumedSeparateDefinition",
        )
        source.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 10, 10),
                Part.makeBox(8, 10, 10, App.Vector(20, 0, 0)),
                Part.makeBox(6, 10, 10, App.Vector(40, 0, 0)),
            ]
        )
        PartDesign.finalizeDesignDefinition(source)
        self.document.commitTransaction()

        self.document.openTransaction("Create consumed Separate")
        operation = self.document.addObject(
            "PartDesign::DesignSeparate",
            "ConsumedSeparate",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignSeparateDefinition(edit, source)
        bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        consumed = sorted(bodies, key=lambda body: body.Shape.BoundBox.XMin)[1]

        self.document.openTransaction("Create downstream operation")
        downstream = self.document.addObject(
            "PartDesign::DesignBox",
            "DownstreamJoin",
        )
        downstream.Length = 2
        downstream.Width = 2
        downstream.Height = 2
        downstream.Placement.Base = App.Vector(20, 0, 0)
        downstream_edit = PartDesign.beginDesignOperationEdit(downstream)
        PartDesign.setDesignOperationTargets(
            downstream_edit,
            "Join",
            [consumed],
        )
        PartDesign.finalizeDesignOperationEdit(downstream_edit)
        self.document.commitTransaction()

        accepted_source_volumes = sorted(
            round(solid.Volume)
            for solid in source.Shape.Solids
        )
        accepted_ids = list(operation.OutputBodyIds)
        self.document.openTransaction("Attempt unsafe Separate retirement")
        source.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 10, 10),
                Part.makeBox(6, 10, 10, App.Vector(40, 0, 0)),
            ]
        )
        unsafe_edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignSeparateDefinition(unsafe_edit, source)
        with self.assertRaisesRegex(
            RuntimeError,
            "downstream modeling history|cannot be retired",
        ):
            PartDesign.finalizeDesignOperationEdit(unsafe_edit)
        self.document.abortTransaction()
        self.document.recompute()

        self.assertEqual(
            sorted(round(solid.Volume) for solid in source.Shape.Solids),
            accepted_source_volumes,
        )
        self.assertEqual(list(operation.OutputBodyIds), accepted_ids)
        self.assertEqual(len(self.document.findObjects("PartDesign::Body")), 3)
        self.assertTrue(downstream.isValid(), downstream.getStatusString())
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_design_fillet_modifies_explicit_edges_across_bodies_atomically(self):
        _, first_body, first_input = self._component_body("First", 0)
        _, second_body, second_input = self._component_body("Second", 20)

        self.document.openTransaction("Create shared fillet")
        fillet = self.document.addObject(
            "PartDesign::DesignFillet",
            "SharedFillet",
        )
        edit = PartDesign.beginDesignOperationEdit(fillet)
        PartDesign.setDesignOperationTargets(
            edit,
            "Modify",
            [first_body, second_body],
        )
        fillet.TargetElementOffsets = [0, 1, 2]
        fillet.TargetElements = ["Edge1", "Edge1"]
        fillet.Radius = 1.0
        self.document.recompute()
        modified = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(modified, [first_body, second_body])
        self.assertEqual(fillet.ResultOperation, "Modify")
        self.assertEqual(fillet.InputStates, [first_input, second_input])
        self.assertEqual(fillet.TargetElementOffsets, [0, 1, 2])
        self.assertEqual(fillet.TargetElements, ["Edge1", "Edge1"])
        self.assertEqual(len(fillet.OutputShapes), 2)
        self.assertLess(first_body.Shape.Volume, 1000.0)
        self.assertAlmostEqual(
            first_body.Shape.Volume,
            second_body.Shape.Volume,
        )
        first_volume = first_body.Shape.Volume
        second_volume = second_body.Shape.Volume
        accepted_output_volumes = [
            shape.Volume for shape in fillet.OutputShapes
        ]
        PartDesign.validateDesign(fillet)
        self._assert_dependency_graph_acyclic(self.document)

        fillet.TargetElements = ["Edge1", "Edge999"]
        self.document.recompute()
        self.assertFalse(fillet.isValid())
        self.assertEqual(
            [shape.Volume for shape in fillet.OutputShapes],
            accepted_output_volumes,
            "one invalid target must not publish a partial dress-up result",
        )
        self.assertAlmostEqual(first_body.Shape.Volume, first_volume)
        self.assertAlmostEqual(second_body.Shape.Volume, second_volume)

        fillet.TargetElements = ["Edge1", "Edge1"]
        self.document.recompute()
        self.assertTrue(fillet.isValid(), fillet.getStatusString())
        fillet.Suppressed = True
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 1000.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 1000.0)
        fillet.Suppressed = False
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, first_volume)
        self.assertAlmostEqual(second_body.Shape.Volume, second_volume)

        path = Path(self._temporary_directory.name) / "DesignFillet.FCStd"
        body_names = (first_body.Name, second_body.Name)
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reopened = self.document.getObject("SharedFillet")
        self.assertEqual(reopened.ResultOperation, "Modify")
        self.assertEqual(reopened.TargetElementOffsets, [0, 1, 2])
        self.assertEqual(reopened.TargetElements, ["Edge1", "Edge1"])
        self.assertAlmostEqual(
            self.document.getObject(body_names[0]).Shape.Volume,
            first_volume,
        )
        self.assertAlmostEqual(
            self.document.getObject(body_names[1]).Shape.Volume,
            second_volume,
        )
        PartDesign.validateDesign(reopened)
        self._assert_dependency_graph_acyclic(self.document)

    def test_design_chamfer_cancel_and_accept_leave_no_body_tip_links(self):
        _, body, initial = self._component_body("Target", 0)

        self.document.openTransaction("Cancel chamfer")
        cancelled = self.document.addObject(
            "PartDesign::DesignChamfer",
            "CancelledChamfer",
        )
        cancelled_edit = PartDesign.beginDesignOperationEdit(cancelled)
        PartDesign.setDesignOperationTargets(
            cancelled_edit,
            "Modify",
            [body],
        )
        cancelled.TargetElementOffsets = [0, 1]
        cancelled.TargetElements = ["Edge1"]
        cancelled.Size = 1.0
        self.document.recompute()
        self.document.abortTransaction()
        self.document.recompute()

        self.assertIsNone(self.document.getObject("CancelledChamfer"))
        self.assertIs(body.Tip, initial)
        self.assertAlmostEqual(body.Shape.Volume, 1000.0)

        self.document.openTransaction("Create chamfer")
        chamfer = self.document.addObject(
            "PartDesign::DesignChamfer",
            "Chamfer",
        )
        edit = PartDesign.beginDesignOperationEdit(chamfer)
        PartDesign.setDesignOperationTargets(edit, "Modify", [body])
        chamfer.TargetElementOffsets = [0, 1]
        chamfer.TargetElements = ["Edge1"]
        chamfer.Size = 1.0
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertIsNone(chamfer.Base)
        self.assertIsNone(chamfer.BaseFeature)
        self.assertEqual(chamfer.InputStates, [initial])
        self.assertLess(body.Shape.Volume, 1000.0)
        PartDesign.validateDesign(chamfer)
        self._assert_dependency_graph_acyclic(self.document)

    def test_design_thickness_shells_multiple_bodies_atomically(self):
        _, first_body, first_input = self._component_body("First", 0)
        _, second_body, second_input = self._component_body("Second", 20)

        self.document.openTransaction("Create shared thickness")
        thickness = self.document.addObject(
            "PartDesign::DesignThickness",
            "SharedThickness",
        )
        edit = PartDesign.beginDesignOperationEdit(thickness)
        PartDesign.setDesignOperationTargets(
            edit,
            "Modify",
            [first_body, second_body],
        )
        thickness.TargetElementOffsets = [0, 1, 2]
        thickness.TargetElements = ["Face6", "Face6"]
        thickness.Value = 1.0
        thickness.Reversed = True
        self.document.recompute()
        modified = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(modified, [first_body, second_body])
        self.assertEqual(thickness.ResultOperation, "Modify")
        self.assertIsNone(thickness.Base)
        self.assertIsNone(thickness.BaseFeature)
        self.assertEqual(
            thickness.InputStates,
            [first_input, second_input],
        )
        self.assertEqual(thickness.TargetElementOffsets, [0, 1, 2])
        self.assertEqual(thickness.TargetElements, ["Face6", "Face6"])
        self.assertEqual(len(thickness.OutputShapes), 2)
        self.assertAlmostEqual(first_body.Shape.Volume, 424.0, places=6)
        self.assertAlmostEqual(second_body.Shape.Volume, 424.0, places=6)
        accepted_output_volumes = [
            shape.Volume for shape in thickness.OutputShapes
        ]
        PartDesign.validateDesign(thickness)
        self._assert_dependency_graph_acyclic(self.document)

        thickness.TargetElements = ["Face6", "Face999"]
        self.document.recompute()
        self.assertFalse(thickness.isValid())
        self.assertEqual(
            [shape.Volume for shape in thickness.OutputShapes],
            accepted_output_volumes,
            "one invalid target must not publish a partial shell result",
        )
        self.assertAlmostEqual(first_body.Shape.Volume, 424.0, places=6)
        self.assertAlmostEqual(second_body.Shape.Volume, 424.0, places=6)

        thickness.TargetElements = ["Face6", "Face6"]
        self.document.recompute()
        self.assertTrue(thickness.isValid(), thickness.getStatusString())
        thickness.Suppressed = True
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 1000.0, places=6)
        self.assertAlmostEqual(second_body.Shape.Volume, 1000.0, places=6)
        thickness.Suppressed = False
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 424.0, places=6)
        self.assertAlmostEqual(second_body.Shape.Volume, 424.0, places=6)

        path = Path(self._temporary_directory.name) / "DesignThickness.FCStd"
        body_names = (first_body.Name, second_body.Name)
        operation_id = str(thickness.OperationId)
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reopened = self.document.getObject("SharedThickness")
        self.assertEqual(str(reopened.OperationId), operation_id)
        self.assertEqual(reopened.ResultOperation, "Modify")
        self.assertEqual(reopened.TargetElementOffsets, [0, 1, 2])
        self.assertEqual(reopened.TargetElements, ["Face6", "Face6"])
        self.assertAlmostEqual(
            self.document.getObject(body_names[0]).Shape.Volume,
            424.0,
            places=6,
        )
        self.assertAlmostEqual(
            self.document.getObject(body_names[1]).Shape.Volume,
            424.0,
            places=6,
        )
        PartDesign.validateDesign(reopened)
        self._assert_dependency_graph_acyclic(self.document)

    def test_design_draft_tapers_multiple_bodies_with_exact_saved_references(self):
        first_component, first_body, first_input = self._component_body(
            "First",
            7,
        )
        _, second_body, second_input = self._component_body("Second", 27)

        self.document.openTransaction("Create shared draft")
        draft = self.document.addObject(
            "PartDesign::DesignDraft",
            "SharedDraft",
        )
        edit = PartDesign.beginDesignOperationEdit(draft)
        PartDesign.setDesignOperationTargets(
            edit,
            "Modify",
            [first_body, second_body],
        )
        draft.TargetElementOffsets = [0, 1, 2]
        draft.TargetElements = ["Face1", "Face1"]
        draft.NeutralPlane = (first_input, ["Face5"])
        draft.PullDirection = (first_input, ["Edge1"])
        draft.Angle = 5.0
        self.document.recompute()
        modified = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        expected_volume = 956.255668237038
        self.assertEqual(modified, [first_body, second_body])
        self.assertIsNone(draft.Base)
        self.assertIsNone(draft.BaseFeature)
        self.assertEqual(draft.InputStates, [first_input, second_input])
        self.assertEqual(draft.TargetElementOffsets, [0, 1, 2])
        self.assertEqual(draft.TargetElements, ["Face1", "Face1"])
        self.assertEqual(draft.NeutralPlane, (first_input, ["Face5"]))
        self.assertEqual(draft.PullDirection, (first_input, ["Edge1"]))
        self.assertAlmostEqual(draft.NeutralPlaneFrame.Base.x, 7.0)
        self.assertAlmostEqual(draft.PullDirectionFrame.Base.x, 7.0)
        self.assertEqual(len(draft.OutputShapes), 2)
        self.assertAlmostEqual(first_body.Shape.Volume, expected_volume)
        self.assertAlmostEqual(second_body.Shape.Volume, expected_volume)
        accepted_output_volumes = [
            shape.Volume for shape in draft.OutputShapes
        ]
        PartDesign.validateDesign(draft)
        self._assert_dependency_graph_acyclic(self.document)

        draft.TargetElements = ["Face1", "Face999"]
        self.document.recompute()
        self.assertFalse(draft.isValid())
        self.assertEqual(
            [shape.Volume for shape in draft.OutputShapes],
            accepted_output_volumes,
            "one invalid target must not publish a partial draft result",
        )
        self.assertAlmostEqual(first_body.Shape.Volume, expected_volume)
        self.assertAlmostEqual(second_body.Shape.Volume, expected_volume)

        draft.TargetElements = ["Face1", "Face1"]
        self.document.recompute()
        self.assertTrue(draft.isValid(), draft.getStatusString())
        draft.Suppressed = True
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, 1000.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 1000.0)
        draft.Suppressed = False
        self.document.recompute()
        self.assertAlmostEqual(first_body.Shape.Volume, expected_volume)
        self.assertAlmostEqual(second_body.Shape.Volume, expected_volume)

        first_component.Placement.Base.x = 57
        self.document.recompute()
        self.assertAlmostEqual(
            draft.NeutralPlaneFrame.Base.x,
            7.0,
            msg="moving a Component must not rewrite an accepted reference frame",
        )
        self.assertAlmostEqual(draft.PullDirectionFrame.Base.x, 7.0)
        self.assertAlmostEqual(first_body.Shape.Volume, expected_volume)
        self.assertAlmostEqual(second_body.Shape.Volume, expected_volume)

        path = Path(self._temporary_directory.name) / "DesignDraft.FCStd"
        body_names = (first_body.Name, second_body.Name)
        operation_id = str(draft.OperationId)
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reopened = self.document.getObject("SharedDraft")
        self.assertEqual(str(reopened.OperationId), operation_id)
        self.assertEqual(
            reopened.NeutralPlane,
            (self.document.getObject("FirstImportedState"), ["Face5"]),
        )
        self.assertEqual(
            reopened.PullDirection,
            (self.document.getObject("FirstImportedState"), ["Edge1"]),
        )
        self.assertAlmostEqual(reopened.NeutralPlaneFrame.Base.x, 7.0)
        self.assertAlmostEqual(reopened.PullDirectionFrame.Base.x, 7.0)
        self.assertAlmostEqual(
            self.document.getObject(body_names[0]).Shape.Volume,
            expected_volume,
        )
        self.assertAlmostEqual(
            self.document.getObject(body_names[1]).Shape.Volume,
            expected_volume,
        )
        PartDesign.validateDesign(reopened)
        self._assert_dependency_graph_acyclic(self.document)

    def test_design_loft_sweep_and_helix_use_the_same_body_contract(self):
        lower = self._rectangle_sketch("LoftLower", -2, 2, -2, 2)
        upper = self._rectangle_sketch("LoftUpper", -1, 1, -1, 1)
        upper.Placement.Base.z = 8

        loft, loft_body = self._new_body_operation(
            "PartDesign::DesignLoft",
            "Loft",
            lambda operation: (
                setattr(operation, "Profile", lower),
                setattr(operation, "Sections", [upper]),
            ),
        )
        self.assertEqual(loft.ResultOperation, "New Body")
        self.assertIs(loft.Profile[0], lower)
        self.assertEqual(loft.Sections[0][0], upper)
        self.assertIsNone(lower.getParentGeoFeatureGroup())
        self.assertIsNone(upper.getParentGeoFeatureGroup())

        sweep_profile = self._circle_sketch(
            "SweepProfile",
            (0, 0),
            1,
        )
        sweep_path = self.document.addObject("PartDesign::Feature", "SweepPath")
        sweep_path.Shape = Part.makeLine(
            App.Vector(0, 0, 0),
            App.Vector(0, 0, 6),
        )
        sweep, sweep_body = self._new_body_operation(
            "PartDesign::DesignSweep",
            "Sweep",
            lambda operation: (
                setattr(operation, "Profile", sweep_profile),
                setattr(operation, "Spine", (sweep_path, ["Edge1"])),
            ),
        )
        self.assertAlmostEqual(sweep_body.Shape.Volume, 6 * 3.14159265, places=4)

        helix_profile = self._circle_sketch(
            "HelixProfile",
            (2, 0),
            0.5,
        )

        def configure_helix(operation):
            operation.Profile = helix_profile
            operation.ReferenceAxis = (helix_profile, ["V_Axis"])
            operation.Mode = 0
            operation.Pitch = 3
            operation.Height = 9
            operation.Angle = 0

        helix, helix_body = self._new_body_operation(
            "PartDesign::DesignHelix",
            "Helix",
            configure_helix,
        )
        self.assertEqual(len(helix_body.Shape.Solids), 1)
        self.assertEqual(
            {
                str(loft.DesignId),
                str(sweep.DesignId),
                str(helix.DesignId),
                str(loft_body.DesignId),
                str(sweep_body.DesignId),
                str(helix_body.DesignId),
            },
            {str(self.document.VibeCADTimeline.DesignId)},
        )
        self._assert_dependency_graph_acyclic(self.document)

    def test_removing_a_body_target_rebases_its_downstream_history(self):
        _, first_body, first_input = self._component_body("First", 0)
        _, second_body, second_input = self._component_body("Second", 15)
        shared_profile = self._rectangle_sketch(
            "SharedProfile",
            5,
            20,
            2,
            8,
        )

        self.document.openTransaction("Create shared cut")
        shared_cut = self.document.addObject(
            "PartDesign::DesignExtrude",
            "SharedCut",
        )
        shared_edit = PartDesign.beginDesignOperationEdit(shared_cut)
        shared_cut.Profile = shared_profile
        shared_cut.Length = 10
        PartDesign.setDesignOperationTargets(
            shared_edit,
            "Cut",
            [first_body, second_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(shared_edit)
        self.document.commitTransaction()

        second_shared_state = second_body.Tip.CurrentState
        downstream_profile = self._rectangle_sketch(
            "DownstreamProfile",
            15,
            25,
            0,
            2,
        )
        self.document.openTransaction("Create downstream cut")
        downstream = self.document.addObject(
            "PartDesign::DesignExtrude",
            "DownstreamCut",
        )
        downstream_edit = PartDesign.beginDesignOperationEdit(downstream)
        downstream.Profile = downstream_profile
        downstream.Length = 10
        PartDesign.setDesignOperationTargets(
            downstream_edit,
            "Cut",
            [second_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(downstream_edit)
        self.document.commitTransaction()
        downstream_state = second_body.Tip.CurrentState
        second_shared_state_name = second_shared_state.Name

        self.assertIs(downstream.InputStates[0], second_shared_state)
        self.assertIs(downstream_state.PreviousState, second_shared_state)
        self.assertAlmostEqual(first_body.Shape.Volume, 700.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 500.0)

        self.document.openTransaction("Remove second target")
        edit = PartDesign.beginDesignOperationEdit(shared_cut)
        PartDesign.setDesignOperationTargets(edit, "Cut", [first_body])
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(shared_cut.TargetBodyIds, [str(first_body.VibeCADBodyId)])
        self.assertEqual(shared_cut.InputStates, [first_input])
        self.assertEqual(downstream.InputStates, [second_input])
        self.assertIs(downstream_state.PreviousState, second_input)
        self.assertIs(second_body.Tip.CurrentState, downstream_state)
        self.assertIsNone(self.document.getObject(second_shared_state_name))
        self.assertAlmostEqual(first_body.Shape.Volume, 700.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 800.0)
        PartDesign.validateDesign(shared_cut)
        self._assert_dependency_graph_acyclic(self.document)

    def test_editing_a_new_body_operation_preserves_body_and_state_identity(self):
        profile = self._rectangle_sketch("Profile", 0, 10, 0, 10)
        operation, body = self._new_body_operation(
            "PartDesign::DesignExtrude",
            "Extrude",
            lambda feature: (
                setattr(feature, "Profile", profile),
                setattr(feature, "Length", 10),
            ),
        )
        publication = body.Tip
        state = publication.CurrentState
        identities = (
            str(body.VibeCADBodyId),
            str(state.BodyStateId),
            body.Name,
            publication.Name,
            state.Name,
        )

        self.document.openTransaction("Edit new body extrusion")
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Length = 5
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        edited_bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(edited_bodies, [body])
        self.assertEqual(
            (
                str(body.VibeCADBodyId),
                str(state.BodyStateId),
                body.Name,
                publication.Name,
                state.Name,
            ),
            identities,
        )
        self.assertIs(body.Tip, publication)
        self.assertIs(publication.CurrentState, state)
        self.assertAlmostEqual(body.Shape.Volume, 500.0)
        PartDesign.validateDesign(operation)

    def test_modification_can_become_a_new_body_without_damaging_target(self):
        _, target, initial = self._component_body("Target", 0)
        profile = self._rectangle_sketch("Profile", 2, 8, 2, 8)

        self.document.openTransaction("Create target cut")
        operation = self.document.addObject(
            "PartDesign::DesignExtrude",
            "Extrude",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = profile
        operation.Length = 10
        PartDesign.setDesignOperationTargets(edit, "Cut", [target])
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        old_state = target.Tip.CurrentState
        old_state_name = old_state.Name

        self.assertAlmostEqual(target.Shape.Volume, 640.0)

        self.document.openTransaction("Convert cut to new body")
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        pending_body_id = operation.TargetBodyIds[0]
        PartDesign.setDesignOperationTargets(edit, "Cut", [target])
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        self.assertEqual(
            operation.TargetBodyIds,
            [pending_body_id],
            "changing task-panel modes must not allocate a different Body "
            "identity before Accept",
        )
        created = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(len(created), 1)
        new_body = created[0]
        self.assertIsNot(new_body, target)
        self.assertNotEqual(
            str(new_body.VibeCADBodyId),
            str(target.VibeCADBodyId),
        )
        self.assertEqual(operation.ResultOperation, "New Body")
        self.assertEqual(operation.InputStates, [])
        self.assertIs(target.Tip.CurrentState, initial)
        self.assertIsNone(self.document.getObject(old_state_name))
        self.assertAlmostEqual(target.Shape.Volume, 1000.0)
        self.assertAlmostEqual(new_body.Shape.Volume, 360.0)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_new_body_destination_component_move_preserves_identity(self):
        first_component = self.document.addObject(
            "PartDesign::Component",
            "FirstComponent",
        )
        second_component = self.document.addObject(
            "PartDesign::Component",
            "SecondComponent",
        )
        second_component.Placement.Base.x = 25
        profile = self._rectangle_sketch("Profile", 0, 10, 0, 10)

        self.document.openTransaction("Create component Body")
        operation = self.document.addObject(
            "PartDesign::DesignExtrude",
            "Extrude",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = profile
        operation.Length = 10
        PartDesign.setDesignOperationTargets(
            edit,
            "New Body",
            [],
            first_component,
        )
        body = PartDesign.finalizeDesignOperationEdit(edit)[0]
        self.document.commitTransaction()
        publication = body.Tip
        state = publication.CurrentState
        identities = (
            str(body.VibeCADBodyId),
            str(state.BodyStateId),
            body.Name,
            publication.Name,
            state.Name,
        )

        self.document.openTransaction("Move result to second component")
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignOperationTargets(
            edit,
            "New Body",
            [],
            second_component,
        )
        moved = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(moved, [body])
        self.assertEqual(
            (
                str(body.VibeCADBodyId),
                str(state.BodyStateId),
                body.Name,
                publication.Name,
                state.Name,
            ),
            identities,
        )
        self.assertNotIn(body, first_component.Group)
        self.assertIn(body, second_component.Group)
        self.assertEqual(str(body.ComponentId), str(second_component.ComponentId))
        self.assertAlmostEqual(operation.TargetFrames[0].Base.x, 25.0)
        self.assertAlmostEqual(body.Shape.BoundBox.XMin, -25.0)
        self.assertAlmostEqual(
            body.Shape.BoundBox.XMin + second_component.Placement.Base.x,
            0.0,
            msg="changing Component membership must preserve the result's "
            "Design-space position",
        )
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_recursive_component_copy_remaps_the_complete_design_graph(self):
        component = self.document.addObject(
            "PartDesign::Component",
            "SourceComponent",
        )
        profile = self._rectangle_sketch("SourceProfile", 0, 10, 0, 10)

        self.document.openTransaction("Create source Component")
        operation = self.document.addObject(
            "PartDesign::DesignExtrude",
            "SourceExtrude",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = profile
        operation.Length = 10
        PartDesign.setDesignOperationTargets(
            edit,
            "New Body",
            [],
            component,
        )
        body = PartDesign.finalizeDesignOperationEdit(edit)[0]
        self.document.commitTransaction()
        state = body.Tip.CurrentState

        source_ids = {
            str(component.ComponentId),
            str(profile.VibeCADSketchId),
            str(body.VibeCADBodyId),
            str(operation.OperationId),
            str(state.BodyStateId),
        }
        self.document.openTransaction("Duplicate Component")
        imported = self.document.copyObject(component, True, True)
        if not isinstance(imported, tuple):
            imported = (imported,)
        self.document.recompute()
        copied_component = next(
            obj
            for obj in imported
            if obj.TypeId == "PartDesign::Component"
        )
        copied_body = next(
            obj for obj in copied_component.Group
            if obj.TypeId == "PartDesign::Body"
        )
        copied_publication = copied_body.Tip
        copied_state = copied_publication.CurrentState
        copied_operation = copied_state.Operation
        copied_profile = copied_operation.Profile[0]
        PartDesign.validateDesign(copied_operation)
        self.document.commitTransaction()

        copied_ids = {
            str(copied_component.ComponentId),
            str(copied_profile.VibeCADSketchId),
            str(copied_body.VibeCADBodyId),
            str(copied_operation.OperationId),
            str(copied_state.BodyStateId),
        }
        self.assertEqual(len(copied_ids), 5)
        self.assertTrue(source_ids.isdisjoint(copied_ids))
        self.assertEqual(
            {
                str(copied_component.DesignId),
                str(copied_profile.DesignId),
                str(copied_body.DesignId),
                str(copied_operation.DesignId),
                str(copied_state.DesignId),
                str(copied_publication.DesignId),
            },
            {str(self.document.VibeCADTimeline.DesignId)},
        )
        self.assertEqual(
            copied_operation.TargetBodyIds,
            [str(copied_body.VibeCADBodyId)],
        )
        self.assertEqual(
            copied_operation.DestinationComponentId,
            str(copied_component.ComponentId),
        )
        self.assertIs(copied_operation.Profile[0], copied_profile)
        self.assertIs(copied_state.Operation, copied_operation)
        self.assertEqual(
            str(copied_state.OperationId),
            str(copied_operation.OperationId),
        )
        self.assertEqual(
            str(copied_state.BodyId),
            str(copied_body.VibeCADBodyId),
        )
        self.assertEqual(
            str(copied_publication.BodyId),
            str(copied_body.VibeCADBodyId),
        )
        self.assertIs(copied_publication.CurrentState, copied_state)
        self.assertAlmostEqual(copied_body.Shape.Volume, 1000.0)
        self._assert_dependency_graph_acyclic(self.document)

        copied_names = tuple(obj.Name for obj in imported)
        copied_component_name = copied_component.Name
        copied_profile_name = copied_profile.Name
        copied_body_name = copied_body.Name
        copied_operation_name = copied_operation.Name
        copied_state_name = copied_state.Name
        copied_property_by_name = {
            copied_component_name: "ComponentId",
            copied_profile_name: "VibeCADSketchId",
            copied_body_name: "VibeCADBodyId",
            copied_operation_name: "OperationId",
            copied_state_name: "BodyStateId",
        }
        copied_identity_by_name = {
            copied_component_name: str(copied_component.ComponentId),
            copied_profile_name: str(copied_profile.VibeCADSketchId),
            copied_body_name: str(copied_body.VibeCADBodyId),
            copied_operation_name: str(copied_operation.OperationId),
            copied_state_name: str(copied_state.BodyStateId),
        }
        self.document.undo()
        self.assertTrue(
            all(self.document.getObject(name) is None for name in copied_names)
        )
        self.document.redo()
        self.document.recompute()
        self.assertEqual(
            {
                name: str(
                    getattr(
                        self.document.getObject(name),
                        copied_property_by_name[name],
                    )
                )
                for name in copied_identity_by_name
            },
            copied_identity_by_name,
        )
        PartDesign.validateDesign(
            self.document.getObject(copied_operation_name)
        )
        self._assert_dependency_graph_acyclic(self.document)

        design_id = str(self.document.VibeCADTimeline.DesignId)
        path = Path(self._temporary_directory.name) / "CopiedComponent.FCStd"
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        self.assertEqual(str(self.document.VibeCADTimeline.DesignId), design_id)
        self.assertEqual(
            {
                name: str(
                    getattr(
                        self.document.getObject(name),
                        copied_property_by_name[name],
                    )
                )
                for name in copied_identity_by_name
            },
            copied_identity_by_name,
        )
        reopened_operation = self.document.getObject(copied_operation_name)
        reopened_component = self.document.getObject(copied_component_name)
        reopened_body = self.document.getObject(copied_body_name)
        self.assertEqual(
            str(reopened_component.DesignId),
            design_id,
        )
        self.assertEqual(
            str(reopened_body.ComponentId),
            str(reopened_component.ComponentId),
        )
        self.assertEqual(
            reopened_operation.TargetBodyIds,
            [str(reopened_body.VibeCADBodyId)],
        )
        PartDesign.validateDesign(reopened_operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_component_copy_rejects_a_partial_cross_component_operation(self):
        first_component, first_body, _ = self._component_body("First", 0)
        _, second_body, _ = self._component_body("Second", 15)
        profile = self._rectangle_sketch("SharedProfile", 5, 20, 2, 8)

        self.document.openTransaction("Create cross-Component cut")
        operation = self.document.addObject(
            "PartDesign::DesignExtrude",
            "SharedCut",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = profile
        operation.Length = 10
        PartDesign.setDesignOperationTargets(
            edit,
            "Cut",
            [first_body, second_body],
        )
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        original_names = tuple(obj.Name for obj in self.document.Objects)

        self.document.openTransaction("Attempt partial Component copy")
        try:
            self.document.copyObject(first_component, True, True)
        except RuntimeError as error:
            self.assertIn("partial Design graph", str(error))
        else:
            self.fail(
                "copying one side of a cross-Component operation must not "
                "produce a silently incomplete history"
            )
        self.document.abortTransaction()
        self.document.recompute()

        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertAlmostEqual(first_body.Shape.Volume, 700.0)
        self.assertAlmostEqual(second_body.Shape.Volume, 700.0)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_new_body_conversion_refuses_downstream_identity_loss(self):
        _, target, _ = self._component_body("Target", 0)
        creation_profile = self._rectangle_sketch(
            "CreationProfile",
            2,
            8,
            2,
            8,
        )
        creation, created_body = self._new_body_operation(
            "PartDesign::DesignExtrude",
            "Creation",
            lambda feature: (
                setattr(feature, "Profile", creation_profile),
                setattr(feature, "Length", 10),
            ),
        )
        creation_state = created_body.Tip.CurrentState
        created_body_name = created_body.Name
        creation_state_name = creation_state.Name

        downstream_profile = self._rectangle_sketch(
            "DownstreamProfile",
            3,
            5,
            3,
            5,
        )
        self.document.openTransaction("Create downstream cut")
        downstream = self.document.addObject(
            "PartDesign::DesignExtrude",
            "Downstream",
        )
        downstream_edit = PartDesign.beginDesignOperationEdit(downstream)
        downstream.Profile = downstream_profile
        downstream.Length = 10
        PartDesign.setDesignOperationTargets(
            downstream_edit,
            "Cut",
            [created_body],
        )
        PartDesign.finalizeDesignOperationEdit(downstream_edit)
        self.document.commitTransaction()
        downstream_state = created_body.Tip.CurrentState

        self.document.openTransaction("Attempt unsafe identity retirement")
        edit = PartDesign.beginDesignOperationEdit(creation)
        PartDesign.setDesignOperationTargets(edit, "Cut", [target])
        try:
            PartDesign.finalizeDesignOperationEdit(edit)
        except Exception as error:
            self.assertIn("downstream modeling history", str(error))
        else:
            self.fail("converting a Body with downstream history must fail")
        self.document.abortTransaction()
        self.document.recompute()

        restored_creation = self.document.getObject("Creation")
        restored_body = self.document.getObject(created_body_name)
        restored_creation_state = self.document.getObject(creation_state_name)
        restored_downstream = self.document.getObject("Downstream")
        self.assertEqual(restored_creation.ResultOperation, "New Body")
        self.assertEqual(restored_creation.InputStates, [])
        self.assertIsNotNone(restored_body)
        self.assertIs(restored_body.Tip.CurrentState, downstream_state)
        self.assertIs(restored_downstream.InputStates[0], restored_creation_state)
        self.assertIs(downstream_state.PreviousState, restored_creation_state)
        self.assertAlmostEqual(restored_body.Shape.Volume, 320.0)
        PartDesign.validateDesign(restored_creation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_result_mode_migration_survives_undo_redo_and_reopen(self):
        _, target, initial = self._component_body("Target", 0)
        profile = self._rectangle_sketch("Profile", 2, 8, 2, 8)

        self.document.openTransaction("Create target cut")
        operation = self.document.addObject(
            "PartDesign::DesignExtrude",
            "Extrude",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = profile
        operation.Length = 10
        PartDesign.setDesignOperationTargets(edit, "Cut", [target])
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        cut_state_name = target.Tip.CurrentState.Name

        self.document.openTransaction("Convert cut to new Body")
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignOperationTargets(edit, "New Body", [])
        created_body = PartDesign.finalizeDesignOperationEdit(edit)[0]
        self.document.commitTransaction()
        created_body_name = created_body.Name
        created_body_id = str(created_body.VibeCADBodyId)
        created_state_name = created_body.Tip.CurrentState.Name

        self.document.undo()
        self.document.recompute()
        restored_operation = self.document.getObject("Extrude")
        restored_target = self.document.getObject("TargetBody")
        self.assertEqual(restored_operation.ResultOperation, "Cut")
        self.assertIsNone(self.document.getObject(created_body_name))
        self.assertIsNotNone(self.document.getObject(cut_state_name))
        self.assertAlmostEqual(restored_target.Shape.Volume, 640.0)
        PartDesign.validateDesign(restored_operation)

        self.document.redo()
        self.document.recompute()
        redone_operation = self.document.getObject("Extrude")
        redone_target = self.document.getObject("TargetBody")
        redone_body = self.document.getObject(created_body_name)
        self.assertEqual(redone_operation.ResultOperation, "New Body")
        self.assertIsNotNone(redone_body)
        self.assertEqual(str(redone_body.VibeCADBodyId), created_body_id)
        self.assertIsNone(self.document.getObject(cut_state_name))
        self.assertIs(redone_target.Tip.CurrentState, initial)
        self.assertAlmostEqual(redone_target.Shape.Volume, 1000.0)
        self.assertAlmostEqual(redone_body.Shape.Volume, 360.0)
        PartDesign.validateDesign(redone_operation)

        path = Path(self._temporary_directory.name) / "ModeMigration.FCStd"
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reopened_operation = self.document.getObject("Extrude")
        reopened_body = self.document.getObject(created_body_name)
        reopened_state = self.document.getObject(created_state_name)
        self.assertEqual(reopened_operation.ResultOperation, "New Body")
        self.assertEqual(str(reopened_body.VibeCADBodyId), created_body_id)
        self.assertIs(reopened_body.Tip.CurrentState, reopened_state)
        self.assertAlmostEqual(reopened_body.Shape.Volume, 360.0)
        PartDesign.validateDesign(reopened_operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_new_body_can_become_a_modification_when_identity_is_unused(self):
        _, target, initial = self._component_body("Target", 0)
        profile = self._rectangle_sketch("Profile", 2, 8, 2, 8)
        operation, created_body = self._new_body_operation(
            "PartDesign::DesignExtrude",
            "Extrude",
            lambda feature: (
                setattr(feature, "Profile", profile),
                setattr(feature, "Length", 10),
            ),
        )
        created_body_name = created_body.Name
        created_body_id = str(created_body.VibeCADBodyId)
        created_state_name = created_body.Tip.CurrentState.Name

        self.document.openTransaction("Convert new body to cut")
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignOperationTargets(edit, "Cut", [target])
        modified = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(modified, [target])
        self.assertEqual(operation.ResultOperation, "Cut")
        self.assertEqual(operation.InputStates, [initial])
        self.assertIsNone(self.document.getObject(created_body_name))
        self.assertIsNone(self.document.getObject(created_state_name))
        self.assertNotIn(
            created_body_id,
            [str(body.VibeCADBodyId) for body in self.document.findObjects(
                "PartDesign::Body"
            )],
        )
        self.assertAlmostEqual(target.Shape.Volume, 640.0)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_vibescript_program_is_one_global_multi_body_operation(self):
        program = self.document.addObject("App::Part", "ScriptProgram")
        program.Label = "Parametric enclosure"

        self.document.openTransaction("Publish VibeScript program")
        operation = self.document.addObject(
            "PartDesign::DesignScriptOperation",
            "ScriptOperation",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            program.Name,
            "program-enclosure",
            "revision-1",
            ["Base", "Lid"],
            ["Enclosure Base", "Enclosure Lid"],
            [
                Part.makeBox(30, 20, 5),
                Part.makeBox(30, 20, 2),
            ],
            [None, None],
        )
        bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(operation.ResultOperation, "Program Outputs")
        self.assertEqual(operation.ProgramOutputKeys, ["Base", "Lid"])
        self.assertEqual(operation.ProgramOutputTypes, ["solid", "solid"])
        self.assertEqual(operation.ScriptOutputKeys, ["Base", "Lid"])
        self.assertEqual(operation.OutputPreviousInputIndices, [-1, -1])
        self.assertEqual(operation.InputStates, [])
        self.assertTrue(operation.Shape.isNull())
        self.assertEqual(
            [body.Label for body in bodies],
            ["Enclosure Base", "Enclosure Lid"],
        )
        self.assertEqual(
            [round(body.Shape.Volume) for body in bodies],
            [3000, 1200],
        )
        self.assertTrue(
            all(body.Tip.CurrentState.Operation is operation for body in bodies)
        )
        self.assertEqual(
            sum(
                item is operation
                for item in self.document.VibeCADTimeline.Operations
            ),
            1,
        )
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_vibescript_finalizer_leaves_unrelated_document_branch_deferred(self):
        class RecomputeCounter:
            def __init__(self):
                self.count = 0

            def execute(self, _object):
                self.count += 1

        unrelated = self.document.addObject("App::FeaturePython", "Unrelated")
        counter = RecomputeCounter()
        unrelated.Proxy = counter
        self.document.recompute()
        baseline_count = counter.count
        unrelated.touch()

        program = self.document.addObject("App::Part", "ScriptProgram")
        self.document.openTransaction("Publish targeted VibeScript program")
        operation = self.document.addObject(
            "PartDesign::DesignScriptOperation",
            "ScriptOperation",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            program.Name,
            "program-targeted-recompute",
            "revision-1",
            ["Housing"],
            ["Housing"],
            [Part.makeBox(12, 8, 3)],
            [None],
        )
        bodies = PartDesign.finalizeDesignScriptOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(counter.count, baseline_count)
        self.assertIn("Touched", [str(state) for state in unrelated.State])
        self.assertEqual(len(bodies), 1)
        self.assertAlmostEqual(bodies[0].Shape.Volume, 288.0)
        PartDesign.validateDesign(operation)

        self.document.recompute()
        self.assertGreater(counter.count, baseline_count)

    def test_vibescript_program_tracks_mixed_outputs_without_fake_bodies(self):
        program = self.document.addObject("App::Part", "ScriptProgram")

        self.document.openTransaction("Publish surface-only VibeScript program")
        operation = self.document.addObject(
            "PartDesign::DesignScriptOperation",
            "ScriptOperation",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            program.Name,
            "program-mixed",
            "revision-1",
            [],
            [],
            [],
            [],
            ["Section", "Outline"],
            ["face", "wire"],
        )
        self.assertEqual(
            PartDesign.finalizeDesignOperationEdit(edit),
            [],
        )
        self.document.commitTransaction()

        self.assertEqual(
            operation.ProgramOutputKeys,
            ["Section", "Outline"],
        )
        self.assertEqual(operation.ProgramOutputTypes, ["face", "wire"])
        self.assertEqual(operation.ScriptOutputKeys, [])
        self.assertEqual(operation.OutputBodyIds, [])
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(operation),
            1,
        )
        PartDesign.validateDesign(operation)

        self.document.openTransaction("Add physical VibeScript output")
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            program.Name,
            "program-mixed",
            "revision-2",
            ["Housing"],
            ["Housing"],
            [Part.makeBox(12, 8, 3)],
            [None],
            ["Housing", "Section"],
            ["solid", "face"],
        )
        bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        self.assertEqual(len(bodies), 1)
        body_name = bodies[0].Name
        body_id = str(bodies[0].VibeCADBodyId)
        self.assertAlmostEqual(bodies[0].Shape.Volume, 288.0)
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(operation),
            1,
        )
        PartDesign.validateDesign(operation)

        self.document.openTransaction("Remove physical VibeScript output")
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            program.Name,
            "program-mixed",
            "revision-3",
            [],
            [],
            [],
            [],
            ["Section"],
            ["face"],
        )
        self.assertEqual(
            PartDesign.finalizeDesignOperationEdit(edit),
            [],
        )
        self.document.commitTransaction()
        self.assertIsNone(self.document.getObject(body_name))
        self.assertEqual(operation.OutputBodyIds, [])
        PartDesign.validateDesign(operation)

        self.document.undo()
        self.document.recompute()
        restored = self.document.getObject(body_name)
        self.assertIsNotNone(restored)
        self.assertEqual(str(restored.VibeCADBodyId), body_id)
        self.assertEqual(operation.ProgramRevision, "revision-2")
        PartDesign.validateDesign(operation)

        self.document.redo()
        self.document.recompute()
        self.assertIsNone(self.document.getObject(body_name))
        self.assertEqual(operation.ProgramRevision, "revision-3")
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_vibescript_edit_retains_output_identity_by_key(self):
        program = self.document.addObject("App::Part", "ScriptProgram")
        self.document.openTransaction("Publish VibeScript program")
        operation = self.document.addObject(
            "PartDesign::DesignScriptOperation",
            "ScriptOperation",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            program.Name,
            "program-bracket",
            "revision-1",
            ["Bracket", "Pin"],
            ["Bracket", "Pin"],
            [Part.makeBox(20, 10, 4), Part.makeCylinder(2, 12)],
            [None, None],
        )
        original_bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        original = {
            key: (
                body.Name,
                str(body.VibeCADBodyId),
                body.Tip.CurrentState.Name,
            )
            for key, body in zip(operation.ScriptOutputKeys, original_bodies)
        }

        self.document.openTransaction("Edit VibeScript program")
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            program.Name,
            "program-bracket",
            "revision-2",
            ["Pin", "Bracket", "Washer"],
            ["Hinge Pin", "Mounting Bracket", "Washer"],
            [
                Part.makeCylinder(2.5, 12),
                Part.makeBox(24, 10, 4),
                Part.makeCylinder(4, 1).cut(Part.makeCylinder(2.6, 1)),
            ],
            [None, None, None],
        )
        edited_bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(operation.ScriptOutputKeys, ["Pin", "Bracket", "Washer"])
        self.assertEqual(
            [body.Name for body in edited_bodies[:2]],
            [original["Pin"][0], original["Bracket"][0]],
        )
        self.assertEqual(
            [str(body.VibeCADBodyId) for body in edited_bodies[:2]],
            [original["Pin"][1], original["Bracket"][1]],
        )
        self.assertEqual(
            [body.Tip.CurrentState.Name for body in edited_bodies[:2]],
            [original["Pin"][2], original["Bracket"][2]],
        )
        self.assertEqual(operation.OutputPreviousInputIndices, [-1, -1, -1])
        self.assertEqual(operation.ProgramRevision, "revision-2")
        self.assertEqual(
            [body.Label for body in edited_bodies],
            ["Hinge Pin", "Mounting Bracket", "Washer"],
        )
        PartDesign.validateDesign(operation)

        path = (
            Path(self._temporary_directory.name)
            / "VibeScriptDesignOperation.FCStd"
        )
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()

        reopened = self.document.getObject("ScriptOperation")
        self.assertEqual(
            reopened.ScriptOutputKeys,
            ["Pin", "Bracket", "Washer"],
        )
        self.assertEqual(reopened.ProgramRevision, "revision-2")
        self.assertEqual(
            [
                str(
                    self.document.getObject(original[key][0]).VibeCADBodyId
                )
                for key in ("Pin", "Bracket")
            ],
            [original["Pin"][1], original["Bracket"][1]],
        )
        PartDesign.validateDesign(reopened)
        self._assert_dependency_graph_acyclic(self.document)

    def test_vibescript_program_can_adopt_a_legacy_body_without_ownership(self):
        program = self.document.addObject("App::Part", "ScriptProgram")
        _component, existing_body, initial = self._component_body(
            "Existing",
            7,
        )

        self.document.openTransaction("Adopt VibeScript output")
        operation = self.document.addObject(
            "PartDesign::DesignScriptOperation",
            "ScriptOperation",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            program.Name,
            "program-adoption",
            "revision-1",
            ["Existing", "New"],
            ["Existing Housing", "New Cover"],
            [Part.makeBox(12, 10, 10), Part.makeBox(12, 10, 2)],
            [existing_body, None],
        )
        outputs = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertIs(outputs[0], existing_body)
        self.assertEqual(operation.InputStates, [initial])
        self.assertEqual(
            operation.OutputPreviousInputIndices,
            [0, -1],
        )
        self.assertIs(
            existing_body.Tip.CurrentState.PreviousState,
            initial,
        )
        self.assertAlmostEqual(existing_body.Shape.Volume, 1200.0)
        self.assertAlmostEqual(outputs[1].Shape.Volume, 240.0)
        PartDesign.validateDesign(operation)
        self._assert_dependency_graph_acyclic(self.document)

    def test_vibescript_operation_removal_deletes_created_bodies_atomically(self):
        program = self.document.addObject("App::Part", "ScriptProgram")
        self.document.openTransaction("Publish VibeScript program")
        operation = self.document.addObject(
            "PartDesign::DesignScriptOperation",
            "ScriptOperation",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignScriptOutputs(
            edit,
            program.Name,
            "program-removal",
            "revision-1",
            ["First", "Second"],
            ["First", "Second"],
            [Part.makeBox(4, 5, 6), Part.makeCylinder(2, 8)],
            [None, None],
        )
        bodies = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        operation_name = operation.Name
        body_names = [body.Name for body in bodies]
        body_ids = [str(body.VibeCADBodyId) for body in bodies]
        state_names = [body.Tip.CurrentState.Name for body in bodies]

        self.document.openTransaction("Delete VibeScript program operation")
        removed = PartDesign.removeDesignOperation(operation)
        self.document.commitTransaction()
        self.assertEqual(removed, body_names)
        self.assertIsNone(self.document.getObject(operation_name))
        self.assertTrue(
            all(self.document.getObject(name) is None for name in body_names)
        )
        self.assertTrue(
            all(self.document.getObject(name) is None for name in state_names)
        )

        self.document.undo()
        self.document.recompute()
        restored_operation = self.document.getObject(operation_name)
        self.assertIsNotNone(restored_operation)
        self.assertEqual(
            [
                str(self.document.getObject(name).VibeCADBodyId)
                for name in body_names
            ],
            body_ids,
        )
        PartDesign.validateDesign(restored_operation)

        self.document.redo()
        self.document.recompute()
        self.assertIsNone(self.document.getObject(operation_name))
        self.assertTrue(
            all(self.document.getObject(name) is None for name in body_names)
        )
