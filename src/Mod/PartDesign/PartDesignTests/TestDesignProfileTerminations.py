# SPDX-License-Identifier: LGPL-2.1-or-later

"""Global Design profile termination contracts."""

import unittest

import FreeCAD as App
import Part
import PartDesign
import Sketcher  # noqa: F401 - registers Sketcher document types


class TestDesignProfileTerminations(unittest.TestCase):
    def setUp(self):
        self.document = App.newDocument("DesignProfileTerminations")
        self.document.UndoMode = True

    def tearDown(self):
        if self.document is not None and App.getDocument(self.document.Name) is not None:
            App.closeDocument(self.document.Name)

    def _rectangle(self, name, x1, x2, y1, y2, z=0.0):
        sketch = self.document.addObject("Sketcher::SketchObject", name)
        PartDesign.initializeDesignDefinition(sketch)
        sketch.addGeometry(
            [
                Part.LineSegment(App.Vector(x1, y1, 0), App.Vector(x2, y1, 0)),
                Part.LineSegment(App.Vector(x2, y1, 0), App.Vector(x2, y2, 0)),
                Part.LineSegment(App.Vector(x2, y2, 0), App.Vector(x1, y2, 0)),
                Part.LineSegment(App.Vector(x1, y2, 0), App.Vector(x1, y1, 0)),
            ],
            False,
        )
        sketch.Placement.Base.z = z
        self.document.recompute([sketch], True, True)
        PartDesign.finalizeDesignDefinition(sketch)
        return sketch

    def _box_body(self, name, origin, lengths):
        body = self.document.addObject("PartDesign::Body", name)
        seed = body.newObject("PartDesign::Feature", f"{name}Seed")
        seed.Shape = Part.makeBox(*lengths, App.Vector(*origin))
        self.document.recompute()
        return body, seed

    @staticmethod
    def _face_name(shape, axis, coordinate):
        for index, face in enumerate(shape.Faces, 1):
            if abs(float(getattr(face.CenterOfMass, axis)) - coordinate) < 1.0e-7:
                return f"Face{index}"
        raise AssertionError(f"No face lies on {axis}={coordinate}")

    def _apply(self, type_id, name, profile, body, configure):
        self.document.openTransaction(f"Create {name}")
        operation = self.document.addObject(type_id, name)
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignOperationTargets(edit, "Join", [body])
        operation.Profile = profile
        configure(operation)
        self.document.recompute([operation], True, True)
        self.assertTrue(operation.isValid(), operation.getStatusString())
        outputs = PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()

        self.assertEqual(outputs, [body])
        self.assertIsNone(operation.BaseFeature)
        self.assertTrue(operation.Shape.isNull())
        self.assertFalse(operation.AddSubShape.isNull())
        self.assertEqual(len(body.Shape.Solids), 1)
        PartDesign.validateDesign(operation)
        return operation

    def test_extrude_first_and_last_use_one_exact_input_state(self):
        profile = self._rectangle("ExtrudeProfile", -3, 3, -2, 2, 2)
        for mode in ("UpToFirst", "UpToLast"):
            body, _seed = self._box_body(
                f"Extrude{mode}Body",
                (-5, -5, 0),
                (10, 10, 10),
            )
            operation = self._apply(
                "PartDesign::DesignExtrude",
                f"Extrude{mode}",
                profile,
                body,
                lambda feature, requested=mode: setattr(feature, "Type", requested),
            )
            self.assertEqual(str(operation.Type), mode)

    def test_revolve_target_dependent_modes_use_unsigned_tool_geometry(self):
        profile = self._rectangle("RevolveProfile", 2, 4, -3, 3)
        for mode in ("UpToFirst", "UpToLast", "UpToFace"):
            body, seed = self._box_body(
                f"Revolve{mode}Body",
                (1, -4, -4),
                (4, 8, 3),
            )

            def configure(feature, requested=mode, target=seed):
                feature.ReferenceAxis = (profile, ["V_Axis"])
                feature.Type = requested
                if requested == "UpToFace":
                    face = self._face_name(target.Shape, "z", -1.0)
                    feature.UpToFace = (target, [face])

            operation = self._apply(
                "PartDesign::DesignRevolve",
                f"Revolve{mode}",
                profile,
                body,
                configure,
            )
            self.assertEqual(str(operation.Type), mode)

    def test_target_dependent_extent_refuses_multiple_body_contexts(self):
        profile = self._rectangle("AmbiguousProfile", -3, 3, -2, 2, 2)
        first, _first_seed = self._box_body("FirstBody", (-5, -5, 0), (10, 10, 10))
        second, _second_seed = self._box_body("SecondBody", (-5, -5, 0), (10, 10, 10))

        self.document.openTransaction("Attempt ambiguous termination")
        operation = self.document.addObject("PartDesign::DesignExtrude", "AmbiguousExtrude")
        edit = PartDesign.beginDesignOperationEdit(operation)
        PartDesign.setDesignOperationTargets(edit, "Join", [first, second])
        operation.Profile = profile
        operation.Type = "UpToFirst"
        self.document.recompute([operation], True, True)

        self.assertFalse(operation.isValid())
        self.assertIn("exactly one explicit target Body", operation.getStatusString())
        self.document.abortTransaction()
        self.document.recompute()
