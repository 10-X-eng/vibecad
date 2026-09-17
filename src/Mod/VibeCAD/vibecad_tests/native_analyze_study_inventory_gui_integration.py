# SPDX-License-Identifier: LGPL-2.1-or-later
"""Study-local inventory follows real FEM mesh ownership links."""

import unittest

import FreeCAD as App
import ObjectsFem
import Part
from PySide import QtCore, QtWidgets

from VibeCADNativeAnalyzeStudyState import study_inventory


class TestStudyMeshInventory(unittest.TestCase):
    def close_document(self, document):
        deadline = QtCore.QDeadlineTimer(10000)
        while not document.isClosable():
            self.assertFalse(deadline.hasExpired(), "Document update did not settle")
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 20)
        App.closeDocument(document.Name)

    def test_owned_refinements_are_counted_once_and_do_not_leak_between_studies(self):
        document = App.newDocument("StudyMeshInventory")
        self.addCleanup(self.close_document, document)
        geometry = document.addObject("Part::Feature", "Geometry")
        geometry.Shape = Part.makeBox(10, 10, 10)
        studies = [ObjectsFem.makeAnalysis(document, name) for name in ("First", "Second")]
        meshes = []
        for index, study in enumerate(studies):
            mesh = ObjectsFem.makeMeshGmsh(document, f"Mesh{index}")
            mesh.Shape = geometry
            study.addObject(mesh)
            meshes.append(mesh)
        region = ObjectsFem.makeMeshRegion(document, meshes[0], 1.0)
        region.References = [(geometry, ("Face1",))]
        first = study_inventory(studies[0])
        self.assertEqual(first["mesh_refinement_count"], 1)
        self.assertEqual(first["mesh_refinement_kinds"], ["region"])
        self.assertEqual(study_inventory(studies[1])["mesh_refinement_count"], 0)

        # Older documents may also list the resource directly in the study.
        studies[0].addObject(region)
        self.assertEqual(study_inventory(studies[0])["mesh_refinement_count"], 1)
        self.assertEqual(study_inventory(studies[1])["mesh_refinement_count"], 0)

    def test_invalid_nested_refinement_remains_a_validation_issue(self):
        document = App.newDocument("InvalidStudyMeshInventory")
        self.addCleanup(self.close_document, document)
        geometry = document.addObject("Part::Feature", "Geometry")
        geometry.Shape = Part.makeBox(10, 10, 10)
        study = ObjectsFem.makeAnalysis(document)
        mesh = ObjectsFem.makeMeshGmsh(document)
        mesh.Shape = geometry
        study.addObject(mesh)
        invalid = document.addObject("App::FeaturePython", "InvalidRefinement")
        mesh.MeshRefinementList = [invalid]
        inventory = study_inventory(study)
        self.assertEqual(inventory["mesh_refinement_count"], 0)
        self.assertGreater(inventory["assignment_validation_issue_count"], 0)
