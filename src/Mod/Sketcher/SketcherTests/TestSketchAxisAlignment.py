# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host coverage for exact, pure Remove Axes Alignment rewrites."""

import unittest

import FreeCAD
import Part
import Sketcher


App = FreeCAD


class TestSketchAxisAlignment(unittest.TestCase):
    def setUp(self):
        self.Doc = App.newDocument("TestSketchAxisAlignment")
        self.Doc.UndoMode = 1
        self.Sketch = self.Doc.addObject("Sketcher::SketchObject", "Sketch")

    def tearDown(self):
        if self.Doc.Name in App.listDocuments():
            App.closeDocument(self.Doc.Name)

    def line(self, start, end):
        return self.Sketch.addGeometry(
            Part.LineSegment(App.Vector(*start), App.Vector(*end)),
            False,
        )

    def point(self, coordinates):
        return self.Sketch.addGeometry(Part.Point(App.Vector(*coordinates)), False)

    @staticmethod
    def constraint_signature(sketch):
        return tuple(
            (
                constraint.Type,
                constraint.First,
                constraint.FirstPos,
                constraint.Second,
                constraint.SecondPos,
                constraint.Third,
                constraint.ThirdPos,
                constraint.Value,
                constraint.Tag,
            )
            for constraint in sketch.Constraints
        )

    @classmethod
    def sketch_signature(cls, sketch):
        geometry = []
        for item in sketch.Geometry:
            points = []
            for name in ("StartPoint", "EndPoint", "Center", "Location"):
                point = getattr(item, name, None)
                if point is not None:
                    points.append(
                        (name, round(point.x, 8), round(point.y, 8), round(point.z, 8))
                    )
            geometry.append((item.TypeId, tuple(points)))
        return (
            tuple(geometry),
            cls.constraint_signature(sketch),
            tuple(sketch.ExpressionEngine),
            sketch.GeometryCount,
            sketch.ConstraintCount,
        )

    def test_line_axis_diagnosis_is_pure_and_commit_matches(self):
        lines = (
            self.line((0, 0, 0), (5, 0, 0)),
            self.line((0, 2, 0), (4, 2, 0)),
            self.line((8, 0, 0), (8, 5, 0)),
            self.line((10, 0, 0), (10, 4, 0)),
        )
        self.Sketch.addConstraint(Sketcher.Constraint("Horizontal", lines[0]))
        self.Sketch.addConstraint(Sketcher.Constraint("Horizontal", lines[1]))
        self.Sketch.addConstraint(Sketcher.Constraint("Vertical", lines[2]))
        self.Sketch.addConstraint(Sketcher.Constraint("Vertical", lines[3]))
        self.Doc.recompute()
        before = self.sketch_signature(self.Sketch)

        diagnostic = self.Sketch.diagnoseRemoveAxesAlignment(list(lines))
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["input_geometry_indices"], list(lines))
        self.assertEqual(diagnostic["removed_horizontal_constraints"], 2)
        self.assertEqual(diagnostic["removed_vertical_constraints"], 2)
        self.assertEqual(diagnostic["created_parallel_constraints"], 2)
        self.assertEqual(diagnostic["removed_axis_symmetry_constraints"], 0)
        self.assertEqual(diagnostic["removed_point_on_axis_constraints"], 0)
        self.assertEqual(diagnostic["converted_distance_constraints"], 0)
        self.assertEqual([item.Type for item in diagnostic["constraints"]], [
            "Parallel",
            "Parallel",
        ])
        self.assertEqual(self.sketch_signature(self.Sketch), before)

        receipt = self.Sketch.removeAxesAlignmentExact(list(lines))
        self.assertEqual([item.Type for item in self.Sketch.Constraints], [
            "Parallel",
            "Parallel",
        ])
        diagnosed_receipt = diagnostic["mutation_receipt"]
        self.assertEqual(receipt["geometry"], diagnosed_receipt["geometry"])
        for field in ("old_to_new",):
            self.assertEqual(
                receipt["constraints"][field], diagnosed_receipt["constraints"][field]
            )
        for field in ("deleted", "created"):
            self.assertEqual(
                [item["index"] for item in receipt["constraints"][field]],
                [item["index"] for item in diagnosed_receipt["constraints"][field]],
            )

    def test_axis_symmetry_and_point_on_axis_are_removed(self):
        first = self.point((3, 4, 0))
        second = self.point((-3, 4, 0))
        on_axis = self.point((0, 2, 0))
        self.Sketch.addConstraint(
            Sketcher.Constraint("Symmetric", first, 1, second, 1, -2)
        )
        self.Sketch.addConstraint(
            Sketcher.Constraint("PointOnObject", on_axis, 1, -2)
        )
        self.Doc.recompute()
        before = self.sketch_signature(self.Sketch)

        diagnostic = self.Sketch.diagnoseRemoveAxesAlignment([first, on_axis])
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["removed_axis_symmetry_constraints"], 1)
        self.assertEqual(diagnostic["removed_point_on_axis_constraints"], 1)
        self.assertEqual(diagnostic["constraint_count"], 0)
        self.assertEqual(self.sketch_signature(self.Sketch), before)

        self.Sketch.removeAxesAlignmentExact([first, on_axis])
        self.assertEqual(self.Sketch.ConstraintCount, 0)

    def test_projected_distances_become_euclidean_and_keep_expressions(self):
        horizontal = self.line((0, 0, 0), (4, 0, 0))
        vertical = self.line((10, 0, 0), (10, 5, 0))
        first = self.Sketch.addConstraint(
            Sketcher.Constraint("DistanceX", horizontal, 1, horizontal, 2, 4.0)
        )
        second = self.Sketch.addConstraint(
            Sketcher.Constraint("DistanceY", vertical, 1, vertical, 2, 5.0)
        )
        self.Sketch.setExpression(f"Constraints[{first}]", "4 mm")
        self.Sketch.setExpression(f"Constraints[{second}]", "5 mm")
        self.Doc.recompute()
        before_tags = tuple(item.Tag for item in self.Sketch.Constraints)
        before_expressions = tuple(self.Sketch.ExpressionEngine)

        diagnostic = self.Sketch.diagnoseRemoveAxesAlignment(
            [horizontal, vertical]
        )
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["converted_distance_constraints"], 2)
        self.assertEqual([item.Type for item in diagnostic["constraints"]], [
            "Distance",
            "Distance",
        ])

        self.Sketch.removeAxesAlignmentExact([horizontal, vertical])
        self.assertEqual([item.Type for item in self.Sketch.Constraints], [
            "Distance",
            "Distance",
        ])
        self.assertEqual(tuple(item.Tag for item in self.Sketch.Constraints), before_tags)
        self.assertEqual(tuple(self.Sketch.ExpressionEngine), before_expressions)

    def test_point_specific_alignment_and_non_axis_relations_survive(self):
        first = self.line((0, 0, 0), (4, 0, 0))
        second = self.line((0, 2, 0), (4, 2, 0))
        whole = self.line((0, 6, 0), (4, 6, 0))
        point_alignment = self.Sketch.addConstraint(
            Sketcher.Constraint("Horizontal", first, 1, second, 1)
        )
        non_axis_relation = self.Sketch.addConstraint(
            Sketcher.Constraint("PointOnObject", first, 2, second)
        )
        self.Sketch.addConstraint(Sketcher.Constraint("Horizontal", whole))
        self.Doc.recompute()
        preserved_tags = tuple(item.Tag for item in self.Sketch.Constraints[:2])

        diagnostic = self.Sketch.diagnoseRemoveAxesAlignment([first, whole])
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["removed_horizontal_constraints"], 1)
        self.assertEqual(
            [item.Type for item in diagnostic["constraints"]],
            ["Horizontal", "PointOnObject"],
        )
        self.Sketch.removeAxesAlignmentExact([first, whole])
        self.assertEqual(
            tuple(item.Tag for item in self.Sketch.Constraints), preserved_tags
        )
        self.assertEqual(self.Sketch.Constraints[point_alignment].Type, "Horizontal")
        self.assertEqual(self.Sketch.Constraints[non_axis_relation].Type, "PointOnObject")

    def test_unselected_axis_alignment_is_untouched(self):
        selected = self.line((0, 0, 0), (4, 0, 0))
        untouched = self.line((0, 3, 0), (4, 3, 0))
        self.Sketch.addConstraint(Sketcher.Constraint("Horizontal", selected))
        untouched_constraint = self.Sketch.addConstraint(
            Sketcher.Constraint("Horizontal", untouched)
        )
        self.Doc.recompute()
        untouched_tag = self.Sketch.Constraints[untouched_constraint].Tag

        diagnostic = self.Sketch.diagnoseRemoveAxesAlignment([selected])
        self.assertTrue(diagnostic["accepted"], diagnostic)
        self.assertEqual(diagnostic["constraint_count"], 1)
        self.assertEqual(diagnostic["constraints"][0].Type, "Horizontal")
        self.Sketch.removeAxesAlignmentExact([selected])
        self.assertEqual(self.Sketch.Constraints[0].Type, "Horizontal")
        self.assertEqual(self.Sketch.Constraints[0].Tag, untouched_tag)

    def test_invalid_and_noop_exact_targets_are_pure(self):
        line = self.line((0, 0, 0), (4, 1, 0))
        self.Doc.recompute()
        before = self.sketch_signature(self.Sketch)
        for targets in ([], [line, line], [-1], [-3], [999]):
            with self.subTest(targets=targets):
                with self.assertRaises((TypeError, ValueError)):
                    self.Sketch.diagnoseRemoveAxesAlignment(targets)
                self.assertEqual(self.sketch_signature(self.Sketch), before)
        with self.assertRaises(TypeError):
            self.Sketch.diagnoseRemoveAxesAlignment([True])
        self.assertEqual(self.sketch_signature(self.Sketch), before)

        self.assertIsNone(self.Sketch.removeAxesAlignment([line]))
        self.assertEqual(self.sketch_signature(self.Sketch), before)

    def test_commit_is_one_undoable_and_redoable_document_change(self):
        line = self.line((0, 0, 0), (4, 0, 0))
        self.Sketch.addConstraint(Sketcher.Constraint("Horizontal", line))
        self.Doc.recompute()
        before = self.sketch_signature(self.Sketch)
        self.Doc.clearUndos()
        self.Doc.openTransaction("Remove Axes Alignment")
        self.Sketch.removeAxesAlignmentExact([line])
        self.Doc.commitTransaction()
        self.Doc.recompute()
        after = self.sketch_signature(self.Sketch)
        self.assertNotEqual(after, before)
        self.assertEqual(self.Doc.UndoCount, 1)

        self.Doc.undo()
        self.assertEqual(self.sketch_signature(self.Sketch), before)
        self.Doc.redo()
        self.assertEqual(self.sketch_signature(self.Sketch), after)
