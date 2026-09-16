# SPDX-License-Identifier: LGPL-2.1-or-later

import FreeCAD as App
import Part

import unittest
import tempfile
from pathlib import Path


class BRepTests(unittest.TestCase):

    def testReadBrepShapesBatch(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(50):
                path = Path(directory) / f"shape-{index}.brep"
                Part.makeBox(index + 1, 2, 3).exportBrep(str(path))
                paths.append(str(path))
            before = set(App.listDocuments())
            shapes = Part.readBrepShapes(paths)
            self.assertEqual(len(shapes), 50)
            for index, shape in enumerate(shapes):
                self.assertTrue(shape.isValid())
                self.assertAlmostEqual(shape.Volume, (index + 1) * 6)
                self.assertEqual(len(shape.Solids), 1)
            self.assertEqual(set(App.listDocuments()), before)
            self.assertEqual(Part.readBrepShapes([]), [])
            bad = Path(directory) / "bad.brep"
            bad.write_text("not a BREP", encoding="utf-8")
            with self.assertRaises(ValueError):
                Part.readBrepShapes([paths[0], str(bad), paths[1]])

    def testProject(self):
        """
        This is a unit test for PR #13507
        """
        num = 18
        alt = [0, 1] * num
        pts = [App.Vector(i, alt[i], 0) for i in range(num)]

        bsc = Part.BSplineCurve()
        bsc.buildFromPoles(pts, False, 1)
        edge = bsc.toShape()

        rts = Part.RectangularTrimmedSurface(Part.Plane(), -50, 50, -50, 50)
        plane_shape = rts.toShape()

        proj = plane_shape.project([edge])
        self.assertFalse(proj.isNull())
        self.assertEqual(len(proj.Edges), 1)

    def testEdgeSplitFace(self):
        coords2d = [(0.5, -0.5), (1.0, -0.5), (1.0, 0.5), (0.5, 0.5)]
        pts2d = [App.Base.Vector2d(u, v) for u, v in coords2d]
        pts2d.append(pts2d[0])

        sphere = Part.Sphere()
        edges = []
        for i in range(1, len(pts2d)):
            ls = Part.Geom2d.Line2dSegment(pts2d[i - 1], pts2d[i])
            edges.append(ls.toShape(sphere))

        split = edges[0].split(0.25)
        new_edges = split.Edges + edges[1:]
        wire = Part.Wire(new_edges)
        face = Part.Face(wire, "Part::FaceMakerSimple")
        self.assertTrue(face.isValid())

    def testEdgeSplitReplace(self):
        cyl = Part.makeCylinder(2, 5)
        e1 = cyl.Edge3
        split = e1.split([1.0, 2.0])
        newcyl = cyl.replaceShape([(e1, split), (cyl.Vertex2, split.Vertex1)])
        self.assertTrue(newcyl.isValid())
