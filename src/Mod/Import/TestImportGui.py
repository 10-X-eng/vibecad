# SPDX-License-Identifier: LGPL-2.1-or-later

# **************************************************************************
#   Copyright (c) 2024 Werner Mayer <wmayer[at]users.sourceforge.net>     *
#                                                                         *
#   This file is part of FreeCAD.                                         *
#                                                                         *
#   FreeCAD is free software: you can redistribute it and/or modify it    *
#   under the terms of the GNU Lesser General Public License as           *
#   published by the Free Software Foundation, either version 2.1 of the  *
#   License, or (at your option) any later version.                       *
#                                                                         *
#   FreeCAD is distributed in the hope that it will be useful, but        *
#   WITHOUT ANY WARRANTY; without even the implied warranty of            *
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
#   Lesser General Public License for more details.                       *
#                                                                         *
#   You should have received a copy of the GNU Lesser General Public      *
#   License along with FreeCAD. If not, see                               *
#   <https://www.gnu.org/licenses/>.                                      *
#                                                                         *
# **************************************************************************

import os
import tempfile
import unittest
import FreeCAD as App
import ImportGui
from pivy import coin


class ExportImportTest(unittest.TestCase):
    def setUp(self):
        TempPath = tempfile.gettempdir()
        self.fileName = TempPath + os.sep + "ColorPerFaceTest.step"
        self.doc = App.newDocument()

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def testSaveLoadStepFile(self):
        """
        Create a STEP file with color per face
        """
        part = self.doc.addObject("App::Part", "Part")
        box = part.newObject("Part::Box", "Box")
        self.doc.recompute()

        box.ViewObject.DiffuseColor = [
            (1.0, 0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0, 1.0),
            (1.0, 0.0, 0.0, 1.0),
            (1.0, 1.0, 0.0, 1.0),
            (1.0, 1.0, 0.0, 1.0),
        ]

        ImportGui.export([part], self.fileName)

        self.doc.clearDocument()
        ImportGui.insert(
            name=self.fileName,
            docName=self.doc.Name,
            merge=False,
            useLinkGroup=True,
            importSolidBodies=True,
        )

        part_features = [obj for obj in self.doc.Objects if obj.TypeId == "Part::Feature"]
        self.assertEqual(len(part_features), 1)
        feature = part_features[0]
        bodies = self.doc.findObjects("PartDesign::Body")
        self.assertEqual(len(bodies), 1)
        self.assertIs(bodies[0].Tip, feature)
        self.assertIn(feature, bodies[0].Group)
        self.assertEqual(len(bodies[0].Shape.Solids), 1)
        self.assertTrue(feature.Label.startswith("Imported STEP:"))

        self.assertEqual(len(feature.ViewObject.DiffuseColor), 6)
        self.assertEqual(feature.ViewObject.DiffuseColor[0], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[1], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[2], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[3], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[4], (1.0, 1.0, 0.0, 1.0))
        self.assertEqual(feature.ViewObject.DiffuseColor[5], (1.0, 1.0, 0.0, 1.0))

        sa = coin.SoSearchAction()
        sa.setType(coin.SoMaterialBinding.getClassTypeId())
        # We need an easier way to access nodes of a display mode
        sa.setInterest(coin.SoSearchAction.ALL)
        sa.apply(feature.ViewObject.RootNode)
        paths = sa.getPaths()

        bind = paths.get(1).getTail()
        self.assertEqual(bind.value.getValue(), bind.PER_PART)

        sa = coin.SoSearchAction()
        sa.setType(coin.SoMaterial.getClassTypeId())
        # We need an easier way to access nodes of a display mode
        sa.setInterest(coin.SoSearchAction.ALL)
        sa.apply(feature.ViewObject.RootNode)
        paths = sa.getPaths()

        mat = paths.get(1).getTail()
        self.assertEqual(mat.diffuseColor.getNum(), 6)

    def testSolidBodyImportCanBeDisabled(self):
        box = self.doc.addObject("Part::Box", "LegacyBox")
        self.doc.recompute()
        ImportGui.export([box], self.fileName)

        self.doc.clearDocument()
        ImportGui.insert(
            name=self.fileName,
            docName=self.doc.Name,
            useLinkGroup=True,
            importSolidBodies=False,
        )

        self.assertEqual(self.doc.findObjects("PartDesign::Body"), [])
        features = [obj for obj in self.doc.Objects if obj.TypeId == "Part::Feature"]
        self.assertEqual(len(features), 1)
        self.assertEqual(len(features[0].Shape.Solids), 1)

    def testMultiSolidStepCreatesOneBodyPerConnectedSolid(self):
        first = self.doc.addObject("Part::Box", "FirstSolid")
        second = self.doc.addObject("Part::Box", "SecondSolid")
        second.Placement.Base.x = 20.0
        self.doc.recompute()
        ImportGui.export([first, second], self.fileName)

        self.doc.clearDocument()
        ImportGui.insert(
            name=self.fileName,
            docName=self.doc.Name,
            useLinkGroup=True,
            importSolidBodies=True,
        )

        bodies = self.doc.findObjects("PartDesign::Body")
        self.assertEqual(len(bodies), 2)
        self.assertTrue(all(len(body.Shape.Solids) == 1 for body in bodies))
        self.assertTrue(all(body.Tip in body.Group for body in bodies))

    def testSurfaceStepRemainsStandaloneGeometry(self):
        import Part

        surface = self.doc.addObject("Part::Feature", "Surface")
        surface.Shape = Part.makePlane(10.0, 8.0)
        self.doc.recompute()
        ImportGui.export([surface], self.fileName)

        self.doc.clearDocument()
        ImportGui.insert(
            name=self.fileName,
            docName=self.doc.Name,
            useLinkGroup=True,
            importSolidBodies=True,
        )

        self.assertEqual(self.doc.findObjects("PartDesign::Body"), [])
        features = [obj for obj in self.doc.Objects if obj.TypeId == "Part::Feature"]
        self.assertEqual(len(features), 1)
        self.assertEqual(len(features[0].Shape.Solids), 0)
        self.assertGreaterEqual(len(features[0].Shape.Faces), 1)

    def testAssemblyInstancesReuseOneImportedBodyDefinition(self):
        definition = self.doc.addObject("App::Part", "Definition")
        definition.newObject("Part::Box", "DefinitionSolid")
        assembly = self.doc.addObject("App::Part", "Assembly")
        first = assembly.newObject("App::Link", "FirstOccurrence")
        first.LinkedObject = definition
        second = assembly.newObject("App::Link", "SecondOccurrence")
        second.LinkedObject = definition
        second.Placement.Base.x = 20.0
        self.doc.recompute()
        ImportGui.export([assembly], self.fileName)

        self.doc.clearDocument()
        ImportGui.insert(
            name=self.fileName,
            docName=self.doc.Name,
            useLinkGroup=True,
            importSolidBodies=True,
        )

        bodies = self.doc.findObjects("PartDesign::Body")
        occurrences = [obj for obj in self.doc.Objects if obj.TypeId == "App::Link"]
        self.assertEqual(len(bodies), 1)
        definition_groups = [
            obj
            for obj in self.doc.Objects
            if obj.TypeId == "App::LinkGroup"
            and bodies[0] in list(getattr(obj, "OutListRecursive", []))
        ]
        occurrence_sets = [
            [link for link in occurrences if link.LinkedObject is definition]
            for definition in definition_groups
        ]
        top_occurrences = next(
            (matches for matches in occurrence_sets if len(matches) == 2),
            [],
        )
        self.assertEqual(
            len(top_occurrences),
            2,
            [
                (
                    obj.Name,
                    obj.TypeId,
                    obj.Label,
                    getattr(getattr(obj, "LinkedObject", None), "Name", None),
                    [parent.Name for parent in obj.InList],
                )
                for obj in self.doc.Objects
            ],
        )
        self.assertNotEqual(
            top_occurrences[0].Placement,
            top_occurrences[1].Placement,
        )
