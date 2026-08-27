"""VibeCAD contracts for every native command shipped on the Mesh surface.

These tests intentionally specify the current VibeCAD human-tool behavior.
They do not inherit historical FreeCAD task/transaction assumptions.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import time

import FreeCAD as App
import FreeCADGui as Gui
import Mesh
import MeshGui
import MeshPart  # noqa: F401 - registers native MeshPart object types
import Part
import PartDesign  # noqa: F401 - registers native Body/Feature types
from PySide import QtCore, QtGui
from VibeCADCore import get_service


SHIPPED_COMMANDS = {
    "Tools": (
        "Mesh_Import",
        "Mesh_Export",
        "Mesh_BuildRegularSolid",
    ),
    "Convert": (
        "Mesh_FromPartShape",
        "MeshPart_ShapeFromMesh",
        "MeshPart_CurveOnMesh",
    ),
    "Modify": (
        "Mesh_HarmonizeNormals",
        "Mesh_FlipNormals",
        "Mesh_FillupHoles",
        "Mesh_FillInteractiveHole",
        "Mesh_AddFacet",
        "Mesh_RemoveComponents",
        "Mesh_Smoothing",
        "Mesh_RemeshGmsh",
        "Mesh_Decimating",
        "Mesh_Scale",
    ),
    "Boolean": (
        "Mesh_Union",
        "Mesh_Intersection",
        "Mesh_Difference",
    ),
    "Cut": (
        "Mesh_PolyCut",
        "Mesh_PolyTrim",
        "Mesh_TrimByPlane",
        "Mesh_SectionByPlane",
        "Mesh_CrossSections",
    ),
    "Segment": (
        "Mesh_Merge",
        "Mesh_SplitComponents",
        "Mesh_Segmentation",
        "Mesh_SegmentationBestFit",
    ),
    "Analyze": (
        "Mesh_Evaluation",
        "Mesh_EvaluateFacet",
        "Mesh_VertexCurvature",
        "Mesh_CurvatureInfo",
        "Mesh_EvaluateSolid",
        "Mesh_BoundingBox",
    ),
}

MESH_IMPLEMENTATION_TOOLBARS = {
    "Mesh Tools": SHIPPED_COMMANDS["Tools"],
    "Mesh Convert": SHIPPED_COMMANDS["Convert"],
    "Mesh Modify": SHIPPED_COMMANDS["Modify"],
    "Mesh Boolean": SHIPPED_COMMANDS["Boolean"],
    "Mesh Cutting": SHIPPED_COMMANDS["Cut"],
    "Mesh Segmentation": SHIPPED_COMMANDS["Segment"],
    "Mesh Analyze": SHIPPED_COMMANDS["Analyze"],
}

STANDARD_TOOLBAR_TITLES = {
    "File",
    "Edit",
    "Clipboard",
    "Workbench",
    "Macro",
    "View",
    "Individual Views",
    "Structure",
    "Help",
}

MENU_ONLY_COMMANDS = {
    "Mesh_RemoveCompByHand",
}

CONDITIONAL_MENU_COMMANDS = {
    "MeshPart_CreateFlatMesh",
    "MeshPart_CreateFlatFace",
}

READ_ONLY_COMMANDS = {
    "Mesh_Export",
    "Mesh_Evaluation",
    "Mesh_EvaluateFacet",
    "Mesh_CurvatureInfo",
    "Mesh_EvaluateSolid",
    "Mesh_BoundingBox",
}

STANDALONE_OPERATION_COMMANDS = {
    "Mesh_Import",
    "Mesh_BuildRegularSolid",
}

SOURCE_PRESERVING_OPERATION_COMMANDS = {
    "Mesh_FromPartShape",
    "MeshPart_ShapeFromMesh",
    "MeshPart_CurveOnMesh",
    "Mesh_SectionByPlane",
    "Mesh_CrossSections",
    "Mesh_VertexCurvature",
    "MeshPart_CreateFlatMesh",
    "MeshPart_CreateFlatFace",
}

REPLACEMENT_OPERATION_COMMANDS = {
    "Mesh_HarmonizeNormals",
    "Mesh_FlipNormals",
    "Mesh_FillupHoles",
    "Mesh_FillInteractiveHole",
    "Mesh_AddFacet",
    "Mesh_RemoveComponents",
    "Mesh_Smoothing",
    "Mesh_RemeshGmsh",
    "Mesh_Decimating",
    "Mesh_Scale",
    "Mesh_Union",
    "Mesh_Intersection",
    "Mesh_Difference",
    "Mesh_PolyCut",
    "Mesh_PolyTrim",
    "Mesh_TrimByPlane",
    "Mesh_Merge",
    "Mesh_SplitComponents",
    "Mesh_Segmentation",
    "Mesh_SegmentationBestFit",
    "Mesh_RemoveCompByHand",
}


def _tetrahedron(offset=0.0):
    a = App.Vector(offset + 0.0, 0.0, 0.0)
    b = App.Vector(offset + 8.0, 0.0, 0.0)
    c = App.Vector(offset + 0.0, 7.0, 0.0)
    d = App.Vector(offset + 0.0, 0.0, 6.0)
    return Mesh.Mesh(
        [
            (a, c, b),
            (a, b, d),
            (b, c, d),
            (c, a, d),
        ]
    )


def _open_tetrahedron(offset=0.0):
    a = App.Vector(offset + 0.0, 0.0, 0.0)
    b = App.Vector(offset + 8.0, 0.0, 0.0)
    c = App.Vector(offset + 0.0, 7.0, 0.0)
    d = App.Vector(offset + 0.0, 0.0, 6.0)
    return Mesh.Mesh(
        [
            (a, c, b),
            (a, b, d),
            (c, a, d),
        ]
    )


def _tetrahedron_with_duplicate_facet(offset=0.0):
    a = App.Vector(offset + 0.0, 0.0, 0.0)
    b = App.Vector(offset + 8.0, 0.0, 0.0)
    c = App.Vector(offset + 0.0, 7.0, 0.0)
    d = App.Vector(offset + 0.0, 0.0, 6.0)
    return Mesh.Mesh(
        [
            (a, c, b),
            (a, b, d),
            (b, c, d),
            (c, a, d),
            (a, c, b),
        ]
    )


def _planar_grid(
    columns=8,
    rows=8,
    spacing=2.0,
    x_offset=0.0,
    y_offset=0.0,
    z_offset=0.0,
):
    facets = []
    for row in range(rows):
        y0 = y_offset + row * spacing
        y1 = y_offset + (row + 1) * spacing
        for column in range(columns):
            x0 = x_offset + column * spacing
            x1 = x_offset + (column + 1) * spacing
            lower_left = App.Vector(x0, y0, z_offset)
            lower_right = App.Vector(x1, y0, z_offset)
            upper_left = App.Vector(x0, y1, z_offset)
            upper_right = App.Vector(x1, y1, z_offset)
            facets.append((lower_left, lower_right, upper_right))
            facets.append((lower_left, upper_right, upper_left))
    return Mesh.Mesh(facets)


def _box_mesh(length=8.0, width=7.0, height=6.0):
    return Mesh.createBox(length, width, height)


class TestVibeCADMeshSourceContracts(unittest.TestCase):
    @staticmethod
    def _native_source(relative_source):
        source_roots = (
            Path(__file__).resolve().parents[2],
            Path(App.getHomePath()).resolve().parents[1] / "src" / "Mod",
        )
        return next(
            (
                root / relative_source
                for root in source_roots
                if (root / relative_source).is_file()
            ),
            None,
        )

    def test_exported_group_helpers_only_finalize_owned_provisional_blocks(
        self,
    ):
        relative_source = Path("Mesh") / "Gui" / "ParametricMeshFilter.cpp"
        source_path = self._native_source(relative_source)
        if source_path is None:
            self.skipTest(
                "Native Mesh output-group source is not present in this installation"
            )

        source = source_path.read_text(encoding="utf-8")
        helper_start = source.index("void finalizeOutputTimelineBlock(")
        helper_end = source.index("\n}\n\n}  // namespace", helper_start)
        helper = source[helper_start:helper_end]
        proof = "isProvisionallyEnrolledByCurrentTransaction"
        legacy_return = "if (!ownsCompleteProvisionalBlock) {"
        finalization = "timeline->finalizeProvisionalOperationBlock("

        self.assertIn("DocumentTimeline::get(document)", helper)
        self.assertNotIn("DocumentTimeline::ensure", helper)
        self.assertGreaterEqual(helper.count(proof), 2)
        self.assertIn("std::ranges::all_of(", helper)
        self.assertIn("&& (!group", helper)
        self.assertIn(legacy_return, helper)
        self.assertIn(finalization, helper)
        self.assertLess(helper.index(legacy_return), helper.index(finalization))

        for exported_helper in (
            "MeshGui::createSourcePreservingOutputGroup(",
            "MeshGui::createStandaloneOutputGroup(",
        ):
            helper_start = source.index(exported_helper)
            helper_body = source[helper_start:]
            self.assertIn("finalizeOutputTimelineBlock(", helper_body)

    def test_import_retains_factory_results_and_tessellation_is_parametric(self):
        importer_header = self._native_source(
            Path("Mesh") / "App" / "Importer.h"
        )
        importer_source = self._native_source(
            Path("Mesh") / "App" / "Importer.cpp"
        )
        mesh_module_source = self._native_source(
            Path("Mesh") / "App" / "AppMeshPy.cpp"
        )
        mesh_command = self._native_source(
            Path("Mesh") / "Gui" / "Command.cpp"
        )
        tessellation_source = self._native_source(
            Path("MeshPart") / "Gui" / "Tessellation.cpp"
        )
        if not all(
            (
                importer_header,
                importer_source,
                mesh_module_source,
                mesh_command,
                tessellation_source,
            )
        ):
            self.skipTest(
                "Native Mesh identity sources are not present in this installation"
            )

        importer_header_text = importer_header.read_text(encoding="utf-8")
        importer_source_text = importer_source.read_text(encoding="utf-8")
        self.assertIn(
            "std::vector<Feature*> loadWithResults(",
            importer_header_text,
        )
        self.assertIn(
            "(void)loadWithResults(fileName);",
            importer_source_text,
        )
        self.assertIn("results.push_back(feature);", importer_source_text)
        mesh_module_text = mesh_module_source.read_text(encoding="utf-8")
        insert_start = mesh_module_text.index(
            "Py::Object importer(const Py::Tuple& args)"
        )
        insert_end = mesh_module_text.index(
            "\n    Py::Object exporter(",
            insert_start,
        )
        insert_body = mesh_module_text[insert_start:insert_end]
        self.assertIn("import.load(EncodedName);", insert_body)
        self.assertIn("return Py::None();", insert_body)

        mesh_text = mesh_command.read_text(encoding="utf-8")
        import_start = mesh_text.index("void CmdMeshImport::activated(")
        import_end = mesh_text.index(
            "\nbool CmdMeshImport::isActive()",
            import_start,
        )
        import_body = mesh_text[import_start:import_end]
        self.assertIn('PyImport_ImportModule("VibeCADMeshImportGui")', import_body)
        self.assertIn('callMemberFunction("start_mesh_imports"', import_body)
        self.assertIn('"*.3mf"', import_body)
        self.assertNotIn("importer.loadWithResults(", import_body)
        self.assertNotIn("Gui::ExactTransaction", import_body)
        self.assertIn("VibeCADMeshImportGui.start_mesh_imports", import_body)
        self.assertNotIn("document->getObjects()", import_body)
        self.assertNotIn("initialObjectIds", import_body)

        export_start = mesh_text.index("void CmdMeshExport::activated(")
        export_end = mesh_text.index(
            "\nbool CmdMeshExport::isActive()",
            export_start,
        )
        export_body = mesh_text[export_start:export_end]
        self.assertIn('PyImport_ImportModule("VibeCADMeshExportGui")', export_body)
        self.assertIn('callMemberFunction("start_mesh_export"', export_body)
        self.assertNotIn("vp->exportMesh(", export_body)

        tessellation_text = tessellation_source.read_text(encoding="utf-8")
        process_start = tessellation_text.index(
            "bool Tessellation::processAndCommit("
        )
        process_end = tessellation_text.index(
            "\nvoid Tessellation::saveParameters(",
            process_start,
        )
        process_body = tessellation_text[process_start:process_end]
        self.assertIn(
            'PyImport_ImportModule("VibeCADMeshTessellationGui")',
            process_body,
        )
        self.assertIn(
            'callMemberFunction("start_shape_tessellations"',
            process_body,
        )
        self.assertNotIn("doc->addObject<MeshPart::MeshFromShape>", process_body)
        self.assertNotIn("doc->recompute()", process_body)
        self.assertNotIn(
            "Gui::Command::runDocumentObjectCommand(",
            process_body,
        )
        self.assertNotIn("doc->getObjects()", process_body)
        self.assertNotIn("initialIds", process_body)
        self.assertNotIn("objectIds", process_body)

    def test_every_derived_mesh_geometry_command_persists_recompute_inputs(self):
        mesh_operations = self._native_source(
            Path("Mesh") / "App" / "FeatureMeshOperations.cpp"
        )
        mesh_command = self._native_source(
            Path("Mesh") / "Gui" / "Command.cpp"
        )
        meshpart_operations = self._native_source(
            Path("MeshPart") / "App" / "FeatureMeshPartOperations.cpp"
        )
        meshpart_command = self._native_source(
            Path("MeshPart") / "Gui" / "Command.cpp"
        )
        cross_sections = self._native_source(
            Path("MeshPart") / "Gui" / "CrossSections.cpp"
        )
        curve = self._native_source(
            Path("MeshPart") / "Gui" / "CurveOnMesh.cpp"
        )
        flattening = self._native_source(
            Path("MeshPart") / "Gui" / "MeshFlatteningCommand.py"
        )
        conversion_gui = self._native_source(
            Path("VibeCAD") / "VibeCADMeshConversionGui.py"
        )
        if not all(
            (
                mesh_operations,
                mesh_command,
                meshpart_operations,
                meshpart_command,
                cross_sections,
                curve,
                flattening,
                conversion_gui,
            )
        ):
            self.skipTest(
                "Native Mesh parametric-operation sources are not present"
            )

        mesh_operation_text = mesh_operations.read_text(encoding="utf-8")
        self.assertIn(
            "PROPERTY_SOURCE(Mesh::MeshFromGeometry, Mesh::Feature)",
            mesh_operation_text,
        )
        self.assertIn(
            "MeshFromGeometry::execute()",
            mesh_operation_text,
        )
        self.assertIn(
            "App::GeoFeature::getGlobalPlacement(source).toMatrix()",
            mesh_operation_text,
        )

        mesh_command_text = mesh_command.read_text(encoding="utf-8")
        geometry_start = mesh_command_text.index(
            "void CmdMeshFromGeometry::activated("
        )
        geometry_end = mesh_command_text.index(
            "\nbool CmdMeshFromGeometry::isActive()",
            geometry_start,
        )
        geometry_body = mesh_command_text[geometry_start:geometry_end]
        self.assertIn(
            "addObject<Mesh::MeshFromGeometry>",
            geometry_body,
        )
        self.assertIn("result->Source.setValue(source)", geometry_body)
        self.assertNotIn(
            "document->addObject<Mesh::Feature>",
            geometry_body,
        )

        operation_text = meshpart_operations.read_text(encoding="utf-8")
        for feature in (
            "MeshFromShape",
            "ShapeFromMesh",
            "SectionByPlane",
            "CrossSections",
            "CurveOnMesh",
        ):
            self.assertIn(
                f"PROPERTY_SOURCE(MeshPart::{feature},",
                operation_text,
            )
            self.assertIn(f"{feature}::execute()", operation_text)
        self.assertIn("continuityFromIndex(", operation_text)
        self.assertIn("transformDirection(", operation_text)

        meshpart_command_text = meshpart_command.read_text(encoding="utf-8")
        self.assertIn(
            'callMemberFunction(\n        "start_mesh_conversions"',
            meshpart_command_text,
        )
        conversion_gui_text = conversion_gui.read_text(encoding="utf-8")
        self.assertIn(
            "commit_mesh_conversion(document, prepared, publish=False)",
            conversion_gui_text,
        )
        self.assertIn(
            "MeshGui.publishSourcePreservingOutputs(",
            conversion_gui_text,
        )
        self.assertIn("run_mesh_conversion(", conversion_gui_text)
        self.assertIn("MeshGui::startBackgroundMeshCut(", meshpart_command_text)
        self.assertIn('"section_by_plane"', meshpart_command_text)
        cross_sections_text = cross_sections.read_text(encoding="utf-8")
        self.assertIn("MeshGui::startBackgroundMeshCut(", cross_sections_text)
        self.assertIn('"cross_sections"', cross_sections_text)
        curve_text = curve.read_text(encoding="utf-8")
        self.assertIn(
            "addObject<MeshPart::CurveOnMesh>",
            curve_text,
        )
        self.assertIn("continuityIndex(", curve_text)
        self.assertIn("sourceLocalDirection(", curve_text)

        flattening_text = flattening.read_text(encoding="utf-8")
        self.assertIn("class _FlatMeshBoundary:", flattening_text)
        self.assertIn("class _FlatFace:", flattening_text)
        self.assertGreaterEqual(
            flattening_text.count('"Part::FeaturePython"'),
            2,
        )
        self.assertIn("def execute(self, obj):", flattening_text)


class TestVibeCADRibbonTools(unittest.TestCase):
    def setUp(self):
        self.document = App.newDocument("VibeCADMeshRibbon")
        self.documents = [self.document.Name]
        Gui.activateWorkbench("MeshWorkbench")
        self._process_events()
        self.mesh = self.document.addObject("Mesh::Feature", "PrimaryMesh")
        self.mesh.Mesh = _tetrahedron()
        self.second_mesh = self.document.addObject("Mesh::Feature", "SecondaryMesh")
        self.second_mesh.Mesh = _tetrahedron(3.0)
        self.meshes_group = MeshGui.ensureMeshesGroup(self.document.Name)
        self.shape = self.document.addObject("Part::Feature", "SourceShape")
        self.shape.Shape = Part.makeBox(10.0, 8.0, 6.0)
        self.plane = self.document.addObject("Part::Plane", "CutPlane")
        self.plane.Length = 30.0
        self.plane.Width = 30.0
        self.curvature = self.document.addObject(
            "Mesh::Curvature",
            "Curvature",
        )
        self.curvature.Source = self.mesh
        self.document.recompute()

    def tearDown(self):
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
            self._process_events()
        for widget in QtGui.QApplication.topLevelWidgets():
            if isinstance(widget, QtGui.QDialog) and widget.isVisible():
                widget.close()
        Gui.Selection.clearSelection()
        for document_name in reversed(self.documents):
            if document_name not in App.listDocuments():
                continue
            document = App.getDocument(document_name)
            transaction = document.getBookedTransactionID()
            if transaction:
                App.closeActiveTransaction(True, transaction)
            App.closeDocument(document_name)
        self.document = None
        self.documents = []
        self._process_events()

    @staticmethod
    def _process_events(rounds=3):
        for _ in range(rounds):
            QtGui.QApplication.processEvents(
                QtCore.QEventLoop.AllEvents,
                25,
            )

    def test_compiled_projected_polygon_edit_preserves_both_exact_regions(self):
        source = Mesh.Mesh(
            [
                (App.Vector(-0.9, -0.8, 0.0), App.Vector(-0.1, -0.8, 0.0), App.Vector(-0.1, 0.8, 0.0)),
                (App.Vector(-0.9, -0.8, 0.0), App.Vector(-0.1, 0.8, 0.0), App.Vector(-0.9, 0.8, 0.0)),
                (App.Vector(0.1, -0.8, 0.0), App.Vector(0.9, -0.8, 0.0), App.Vector(0.9, 0.8, 0.0)),
                (App.Vector(0.1, -0.8, 0.0), App.Vector(0.9, 0.8, 0.0), App.Vector(0.1, 0.8, 0.0)),
            ]
        )
        projection = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        polygon = [(0.0, 0.0), (0.49, 0.0), (0.49, 1.0), (0.0, 1.0)]

        inside, outside = Mesh.projectedPolygonEdit(
            source,
            polygon,
            projection,
            "cut",
            ("inside", "outside"),
        )

        self.assertEqual(inside.CountFacets, 2)
        self.assertEqual(outside.CountFacets, 2)
        self.assertNotEqual(
            Mesh.geometrySha256(inside),
            Mesh.geometrySha256(outside),
        )

        trimmed_inside, trimmed_outside = Mesh.projectedPolygonEdit(
            source,
            polygon,
            projection,
            "trim",
            ("inside", "outside"),
        )
        self.assertEqual(trimmed_inside.CountFacets, 2)
        self.assertEqual(trimmed_outside.CountFacets, 2)
        self.assertNotEqual(
            Mesh.geometrySha256(trimmed_inside),
            Mesh.geometrySha256(source),
        )
        self.assertNotEqual(
            Mesh.geometrySha256(trimmed_outside),
            Mesh.geometrySha256(source),
        )
        self.assertNotEqual(
            Mesh.geometrySha256(trimmed_inside),
            Mesh.geometrySha256(trimmed_outside),
        )

    def test_compiled_mesh_snapshot_has_stable_geometry_and_revision(self):
        revision_before = Mesh.propertyRevision(self.mesh.Mesh)
        snapshot, digest = Mesh.snapshotWithSha256(self.mesh.Mesh)

        self.mesh.Mesh = _tetrahedron(40.0)
        revision_after = Mesh.propertyRevision(self.mesh.Mesh)

        self.assertGreater(revision_after, revision_before)
        self.assertEqual(digest, Mesh.geometrySha256(snapshot))
        self.assertNotEqual(digest, Mesh.geometrySha256(self.mesh.Mesh))
        self.assertEqual(snapshot.CountFacets, 4)

    def test_mesh_target_preflight_defers_geometry_snapshot_to_background(self):
        from VibeCADNativeMeshState import mesh_object_state
        from VibeCADNativeMeshTargets import prepare_mesh_target

        state = mesh_object_state(self.mesh)
        original_digest = Mesh.geometrySha256

        def reject_ui_digest(_mesh):
            raise AssertionError("Mesh preflight hashed geometry on the document thread")

        Mesh.geometrySha256 = reject_ui_digest
        try:
            target = prepare_mesh_target(
                self.document,
                str(self.document.Uid),
                {
                    "object_name": str(self.mesh.Name),
                    "expected_state_sha256": str(state["state_sha256"]),
                },
                require_label=False,
            )
        finally:
            Mesh.geometrySha256 = original_digest

        self.assertEqual(
            state["geometry_revision"],
            Mesh.propertyRevision(self.mesh.Mesh),
        )
        self.assertEqual(target.source_geometry_revision, state["geometry_revision"])
        self.assertEqual(target.source_geometry_sha256, "")
        self.assertIs(target.source_mesh, self.mesh.Mesh)

    def test_background_mesh_snapshot_rebinds_exact_prepared_targets(self):
        from VibeCADNativeMeshModify import PreparedMeshModification
        from VibeCADNativeMeshState import mesh_object_state
        from VibeCADNativeMeshTargets import (
            prepare_mesh_target,
            rebind_prepared_mesh_targets,
            snapshot_mesh_targets,
        )

        state = mesh_object_state(self.mesh)
        target = prepare_mesh_target(
            self.document,
            str(self.document.Uid),
            {
                "object_name": str(self.mesh.Name),
                "expected_state_sha256": str(state["state_sha256"]),
            },
            require_label=False,
        )
        prepared = PreparedMeshModification("harmonize_normals", (target,), {})

        exact_targets, snapshots = snapshot_mesh_targets(prepared.targets)
        exact_prepared = rebind_prepared_mesh_targets(prepared, exact_targets)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(
            exact_prepared.targets[0].source_geometry_sha256,
            Mesh.geometrySha256(snapshots[0]),
        )
        self.assertEqual(snapshots[0].CountFacets, target.topology["facets"])
        self.assertIs(exact_prepared.targets[0].source, target.source)
        self.assertIs(exact_prepared.targets[0].source_mesh, target.source_mesh)

    def test_mesh_job_request_creation_never_copies_on_document_thread(self):
        from dataclasses import replace
        from VibeCADMeshModificationJob import make_request
        from VibeCADNativeMeshModify import PreparedMeshModification
        from VibeCADNativeMeshState import mesh_object_state
        from VibeCADNativeMeshTargets import prepare_mesh_target

        class CopyTrap:
            @property
            def Mesh(self):
                raise AssertionError("A Mesh job copied geometry before background dispatch")

        state = mesh_object_state(self.mesh)
        target = prepare_mesh_target(
            self.document,
            str(self.document.Uid),
            {
                "object_name": str(self.mesh.Name),
                "expected_state_sha256": str(state["state_sha256"]),
            },
            require_label=False,
        )
        deferred = replace(target, source=CopyTrap())
        request = make_request(
            PreparedMeshModification("harmonize_normals", (deferred,), {})
        )

        self.assertEqual(request.detached_meshes, (target.source_mesh,))

    def test_viewport_polygon_cut_publishes_verified_split_in_background(self):
        self.mesh.Mesh = Mesh.Mesh(
            [
                (
                    App.Vector(-0.9, -0.8, 0.0),
                    App.Vector(-0.1, -0.8, 0.0),
                    App.Vector(-0.1, 0.8, 0.0),
                ),
                (
                    App.Vector(-0.9, -0.8, 0.0),
                    App.Vector(-0.1, 0.8, 0.0),
                    App.Vector(-0.9, 0.8, 0.0),
                ),
                (
                    App.Vector(0.1, -0.8, 0.0),
                    App.Vector(0.9, -0.8, 0.0),
                    App.Vector(0.9, 0.8, 0.0),
                ),
                (
                    App.Vector(0.1, -0.8, 0.0),
                    App.Vector(0.9, 0.8, 0.0),
                    App.Vector(0.1, 0.8, 0.0),
                ),
            ]
        )
        self.document.recompute()
        from VibeCADMeshCutGui import start_mesh_cut
        from VibeCADNativeMeshState import mesh_object_state

        before = tuple(self.document.Objects)
        started = time.monotonic()
        job_id = start_mesh_cut(
            "viewport_cut",
            json.dumps(
                {
                    "document": self.document.Name,
                    "targets": [self.mesh.Name],
                    "polygon": [[0.0, 0.0], [0.49, 0.0], [0.49, 1.0], [0.0, 1.0]],
                    "projection_matrix": [
                        1.0, 0.0, 0.0, 0.0,
                        0.0, 1.0, 0.0, 0.0,
                        0.0, 0.0, 1.0, 0.0,
                        0.0, 0.0, 0.0, 1.0,
                    ],
                    "mode": "split",
                    "expected_state_sha256": mesh_object_state(self.mesh)[
                        "state_sha256"
                    ],
                }
            ),
        )
        self.assertTrue(job_id)
        self.assertLess(time.monotonic() - started, 0.25)

        snapshot = self._wait_for_mesh_cut(capability="mesh.cut.viewport_cut.human")
        self.assertEqual(snapshot.job_id, job_id)
        created = [obj for obj in self.document.Objects if obj not in before]
        controller = next(obj for obj in created if obj.TypeId == "Mesh::OutputGroup")
        results = [obj for obj in created if obj.TypeId == "Mesh::StoredEdit"]
        self.assertEqual(len(results), 2)
        self.assertEqual(tuple(controller.Sources), (self.mesh,))
        self.assertEqual(tuple(controller.Group), tuple(results))
        self.assertEqual({result.Mesh.CountFacets for result in results}, {2})
        self.assertEqual({result.Source for result in results}, {self.mesh})
        self.assertEqual(
            {result.VibeCADTimelineOwner for result in results},
            {controller},
        )
        meshes = self.document.getObject("Meshes")
        self.assertIsNotNone(meshes)
        self.assertIn(controller, meshes.Group)
        self.assertTrue(all(result not in meshes.Group for result in results))
        self.assertFalse(self.mesh.Visibility)

    def _wait_for_mesh_conversion(self, timeout=30.0):
        manager = get_service().native_background_manager()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._process_events(2)
            snapshot = manager.latest_document_snapshot(
                str(self.document.Uid),
                capability_prefix="mesh.convert.human",
            )
            if snapshot is not None and snapshot.terminal:
                self.assertEqual(snapshot.phase, "completed", snapshot.error)
                return snapshot
            time.sleep(0.01)
        self.fail("Background Mesh conversion did not finish")

    def _wait_for_mesh_tessellation(self, timeout=60.0):
        manager = get_service().native_background_manager()
        consumed = getattr(self, "_last_mesh_tessellation_job_id", "")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._process_events(2)
            snapshot = manager.latest_document_snapshot(
                str(self.document.Uid),
                capability_prefix="mesh.convert.shape_to_mesh.human",
            )
            if (
                snapshot is not None
                and str(snapshot.job_id) != consumed
                and snapshot.terminal
            ):
                self.assertEqual(snapshot.phase, "completed", snapshot.error)
                self._last_mesh_tessellation_job_id = str(snapshot.job_id)
                return snapshot
            time.sleep(0.01)
        self.fail("Background Mesh From Shape did not finish")

    def _wait_for_mesh_import(self, timeout=60.0):
        manager = get_service().native_background_manager()
        consumed = getattr(self, "_last_mesh_import_job_id", "")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._process_events(2)
            snapshot = manager.latest_document_snapshot(
                str(self.document.Uid),
                capability_prefix="mesh.io.import_mesh.human",
            )
            if (
                snapshot is not None
                and str(snapshot.job_id) != consumed
                and snapshot.terminal
            ):
                self.assertEqual(snapshot.phase, "completed", snapshot.error)
                self._last_mesh_import_job_id = str(snapshot.job_id)
                return snapshot
            time.sleep(0.01)
        self.fail("Background Mesh import did not finish")

    def _wait_for_mesh_boolean(
        self,
        document=None,
        *,
        expected_phase="completed",
        timeout=60.0,
    ):
        target_document = document or self.document
        manager = get_service().native_background_manager()
        consumed = getattr(self, "_last_mesh_boolean_job_id", "")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._process_events(2)
            snapshot = manager.latest_document_snapshot(
                str(target_document.Uid),
                capability_prefix="mesh.boolean.human",
            )
            if (
                snapshot is not None
                and str(snapshot.job_id) != consumed
                and snapshot.terminal
            ):
                self.assertEqual(snapshot.phase, expected_phase, snapshot.error)
                self._last_mesh_boolean_job_id = str(snapshot.job_id)
                return snapshot
            time.sleep(0.01)
        self.fail("Background Mesh boolean did not finish")

    def _wait_for_mesh_cut(self, *, capability="mesh.cut.", timeout=60.0):
        manager = get_service().native_background_manager()
        consumed = getattr(self, "_last_mesh_cut_job_id", "")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._process_events(2)
            snapshot = manager.latest_document_snapshot(
                str(self.document.Uid),
                capability_prefix=capability,
            )
            if (
                snapshot is not None
                and str(snapshot.job_id) != consumed
                and snapshot.terminal
            ):
                self.assertEqual(snapshot.phase, "completed", snapshot.error)
                self._last_mesh_cut_job_id = str(snapshot.job_id)
                return snapshot
            time.sleep(0.01)
        self.fail("Background Mesh cut did not finish")

    def _wait_for_mesh_curvature(self, timeout=60.0):
        manager = get_service().native_background_manager()
        consumed = getattr(self, "_last_mesh_curvature_job_id", "")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._process_events(2)
            snapshot = manager.latest_document_snapshot(
                str(self.document.Uid),
                capability_prefix="mesh.curvature.vertex_curvature.human",
            )
            if (
                snapshot is not None
                and str(snapshot.job_id) != consumed
                and snapshot.terminal
            ):
                self.assertEqual(snapshot.phase, "completed", snapshot.error)
                self._last_mesh_curvature_job_id = str(snapshot.job_id)
                return snapshot
            time.sleep(0.01)
        self.fail("Background Mesh curvature did not finish")

    def _wait_for_mesh_job(
        self,
        capability_prefix,
        *,
        document=None,
        expected_phase="completed",
        timeout=60.0,
    ):
        target_document = document or self.document
        manager = get_service().native_background_manager()
        consumed = getattr(self, "_consumed_mesh_jobs", {})
        key = (str(target_document.Uid), capability_prefix)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._process_events(2)
            snapshot = manager.latest_document_snapshot(
                key[0],
                capability_prefix=capability_prefix,
            )
            if (
                snapshot is not None
                and str(snapshot.job_id) != consumed.get(key, "")
                and snapshot.terminal
            ):
                self.assertEqual(snapshot.phase, expected_phase, snapshot.error)
                consumed[key] = str(snapshot.job_id)
                self._consumed_mesh_jobs = consumed
                title = (
                    "Mesh Segmentation"
                    if capability_prefix.startswith("mesh.segment.")
                    else "Mesh"
                )
                while time.monotonic() < deadline:
                    self._process_events(2)
                    if not any(
                        isinstance(widget, QtGui.QProgressDialog)
                        and widget.isVisible()
                        and widget.windowTitle() == title
                        for widget in QtGui.QApplication.topLevelWidgets()
                    ):
                        break
                    time.sleep(0.01)
                else:
                    self.fail(f"Background {capability_prefix} UI did not close")
                return snapshot
            time.sleep(0.01)
        self.fail(f"Background {capability_prefix} job did not finish")

    def _select(self, *objects):
        Gui.Selection.clearSelection()
        for obj in objects:
            Gui.Selection.addSelection(obj)
        self._process_events()

    def _new_document(self, name):
        document = App.newDocument(name)
        self.documents.append(document.Name)
        return document

    @staticmethod
    def _mesh_points(feature):
        inverse_placement = feature.Placement.inverse()
        points = []
        for point in feature.Mesh.Points:
            local = inverse_placement.multVec(point.Vector)
            points.append((local.x, local.y, local.z))
        return tuple(points)

    @staticmethod
    def _placement_tuple(feature):
        placement = feature.Placement
        return (
            placement.Base.x,
            placement.Base.y,
            placement.Base.z,
            tuple(placement.Rotation.Q),
        )

    @staticmethod
    def _add_segment(feature, facets):
        mesh = Mesh.Mesh(feature.Mesh)
        mesh.addSegment(list(facets))
        feature.Mesh = mesh

    def _accept_input_dialog(self, value, integer=False):
        attempts = {"remaining": 20000}

        def accept():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QInputDialog):
                    continue
                if not widget.isVisible():
                    continue
                if integer:
                    widget.setIntValue(int(value))
                else:
                    widget.setDoubleValue(float(value))
                widget.accept()
                return
            attempts["remaining"] -= 1
            if attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, accept)

        QtCore.QTimer.singleShot(0, accept)

    def _accept_modal_dialog(self, _object_name, checked_texts=()):
        attempts = {"remaining": 1000}
        checked_texts = set(checked_texts)

        def accept():
            widget = QtGui.QApplication.activeModalWidget()
            if isinstance(widget, QtGui.QDialog) and widget.isVisible():
                for checkbox in widget.findChildren(QtGui.QCheckBox):
                    if checkbox.text() in checked_texts:
                        checkbox.setChecked(True)
                button_box = widget.findChild(QtGui.QDialogButtonBox)
                button = (
                    button_box.button(QtGui.QDialogButtonBox.Ok) if button_box else None
                )
                if button and button.isVisible() and button.isEnabled():
                    button.click()
                    return
            attempts["remaining"] -= 1
            if attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, accept)

        QtCore.QTimer.singleShot(0, accept)

    def _accept_open_files_dialog(self, paths):
        paths = tuple(Path(path) for path in paths)
        self.assertTrue(paths)
        self.assertEqual(len({path.parent for path in paths}), 1)
        attempts = {"remaining": 1000}

        def accept():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QFileDialog):
                    continue
                if not widget.isVisible():
                    continue
                widget.setDirectory(str(paths[0].parent))
                file_name = widget.findChild(
                    QtGui.QLineEdit,
                    "fileNameEdit",
                )
                if file_name is None:
                    continue
                file_name.setText(" ".join(f'"{path.name}"' for path in paths))
                widget.accept()
                return
            attempts["remaining"] -= 1
            if attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, accept)

        QtCore.QTimer.singleShot(0, accept)

    def _click_message_button(self, text):
        attempts = {"remaining": 100}

        def click():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QMessageBox):
                    continue
                if not widget.isVisible():
                    continue
                for button in widget.buttons():
                    if button.text().replace("&", "") == text:
                        button.click()
                        return
            attempts["remaining"] -= 1
            if attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, click)

        QtCore.QTimer.singleShot(0, click)

    def _task_button(self, standard_button):
        self._process_events()
        for box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not box.isVisible():
                continue
            parent = box.parentWidget()
            while parent is not None:
                if parent.metaObject().className() == "Gui::TaskView::TaskView":
                    break
                parent = parent.parentWidget()
            if parent is None:
                continue
            button = box.button(standard_button)
            if button and button.isVisible() and button.isEnabled():
                return button
        return None

    def _timeline_button(self, object_name):
        button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            object_name,
        )
        self.assertIsNotNone(button, object_name)
        self.assertTrue(button.isVisible(), object_name)
        self.assertTrue(button.isEnabled(), object_name)
        return button

    def _timeline_object_names(self):
        self._process_events()
        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline)
        return tuple(
            timeline.item(row).data(QtCore.Qt.UserRole)
            for row in range(timeline.count())
            if timeline.item(row).data(QtCore.Qt.UserRole)
        )

    def _assert_source_preserving_multi_result(
        self,
        created,
        sources,
        result_type,
        operation_kind,
        input_mode="Source preserving",
        controller_type="Mesh::OutputGroup",
    ):
        created = [obj for obj in created if obj.TypeId != "App::DocumentTimeline"]
        controllers = [obj for obj in created if obj.TypeId == controller_type]
        results = [obj for obj in created if obj.TypeId == result_type]
        self.assertEqual(len(controllers), 1)
        self.assertGreater(len(results), 1)
        self.assertEqual(len(created), len(results) + 1)
        controller = controllers[0]
        if input_mode is None:
            self.assertNotIn("InputMode", controller.PropertiesList)
        else:
            self.assertEqual(controller.InputMode, input_mode)
        self.assertEqual(controller.OperationKind, operation_kind)
        self.assertEqual(list(controller.Sources), list(sources))
        self.assertEqual(set(controller.Group), set(results))
        self.assertEqual(controller.VibeCADTimelineRole, "operation")
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            controller.PropertiesList,
        )
        self.assertNotIn(
            "VibeCADTimelineEditor",
            controller.PropertiesList,
        )
        if controller_type == "Mesh::OutputGroup":
            self.assertEqual(controller.Mesh.CountFacets, 0)
        else:
            self.assertNotIn("Mesh", controller.PropertiesList)
        for result in results:
            self.assertEqual(result.VibeCADTimelineRole, "resource")
            self.assertIs(result.VibeCADTimelineOwner, controller)
            self.assertTrue(result.ViewObject.ShowInTree)

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(controller, timeline.Operations)
        for result in results:
            self.assertIn(result, timeline.Operations)
        timeline_names = self._timeline_object_names()
        self.assertEqual(timeline_names.count(controller.Name), 1)
        self.assertTrue(all(result.Name not in timeline_names for result in results))
        self.assertTrue(all(source.Visibility for source in sources))
        return controller, results

    def _cancel_task(self, command_name):
        self.assertTrue(Gui.Control.activeDialog(), command_name)
        button = self._task_button(QtGui.QDialogButtonBox.Cancel)
        if button is None:
            button = self._task_button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(button, command_name)
        button.click()
        self._process_events(5)
        self.assertFalse(Gui.Control.activeDialog(), command_name)
        self.assertFalse(self.document.HasPendingTransaction, command_name)

    def _assert_refuses_caller_transaction(self, command_name):
        self.assertTrue(Gui.isCommandActive(command_name), command_name)
        undo_before = self.document.UndoCount
        objects_before = tuple(self.document.Objects)
        self.document.openTransaction("Caller owned")
        transaction = self.document.getBookedTransactionID()
        self.assertNotEqual(transaction, 0)
        self.assertFalse(Gui.isCommandActive(command_name), command_name)
        Gui.runCommand(command_name, 0)
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction,
        )
        App.closeActiveTransaction(True, transaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)

    def test_exact_shipped_inventory_is_registered(self):
        commands = set(Gui.listCommands())
        toolbar_commands = {
            command for group in SHIPPED_COMMANDS.values() for command in group
        }
        self.assertEqual(len(toolbar_commands), 34)
        self.assertEqual(
            set(SHIPPED_COMMANDS),
            {
                "Tools",
                "Convert",
                "Modify",
                "Boolean",
                "Cut",
                "Segment",
                "Analyze",
            },
        )
        self.assertFalse(toolbar_commands - commands)
        for command_name in sorted(toolbar_commands):
            actions = Gui.Command.get(command_name).getAction()
            self.assertTrue(actions, command_name)
            for action in actions:
                self.assertFalse(action.icon().isNull(), command_name)
                self.assertFalse(
                    action.icon().pixmap(24, 24).isNull(),
                    command_name,
                )

        toolbar_items = Gui.activeWorkbench().getToolbarItems()
        live_toolbars = {
            title: tuple(
                command
                for command in commands
                if command != "Separator"
            )
            for title, commands in toolbar_items.items()
            if title not in STANDARD_TOOLBAR_TITLES
        }
        self.assertEqual(live_toolbars, MESH_IMPLEMENTATION_TOOLBARS)

        def terminal_command_ids(menu):
            result = set()
            for action in menu.actions():
                if action.isSeparator():
                    continue
                submenu = action.menu()
                if submenu is not None:
                    result.update(terminal_command_ids(submenu))
                    continue
                command_id = action.objectName().strip()
                if command_id:
                    result.add(command_id)
            return result

        mesh_menu_commands = next(
            (
                terminal_command_ids(action.menu())
                for action in Gui.getMainWindow().menuBar().actions()
                if action.menu() is not None
                and {
                    "Mesh_Import",
                    "Mesh_Export",
                    "Mesh_Evaluation",
                }
                <= terminal_command_ids(action.menu())
            ),
            None,
        )
        self.assertIsNotNone(mesh_menu_commands)

        registered_conditional = CONDITIONAL_MENU_COMMANDS & commands
        expected_menu_commands = (
            toolbar_commands - {"MeshPart_CurveOnMesh"}
            | MENU_ONLY_COMMANDS
            | registered_conditional
        )
        self.assertEqual(
            mesh_menu_commands,
            expected_menu_commands,
        )
        self.assertEqual(
            toolbar_commands | MENU_ONLY_COMMANDS | registered_conditional,
            expected_menu_commands | {"MeshPart_CurveOnMesh"},
        )

    def test_every_shipped_command_has_one_explicit_history_contract(self):
        shipped = {command for group in SHIPPED_COMMANDS.values() for command in group}
        shipped.update(MENU_ONLY_COMMANDS)
        shipped.update(CONDITIONAL_MENU_COMMANDS)
        contracts = (
            STANDALONE_OPERATION_COMMANDS,
            SOURCE_PRESERVING_OPERATION_COMMANDS,
            REPLACEMENT_OPERATION_COMMANDS,
            READ_ONLY_COMMANDS,
        )
        classified = set().union(*contracts)
        self.assertEqual(classified, shipped)
        for index, contract in enumerate(contracts):
            for other in contracts[index + 1 :]:
                self.assertFalse(contract & other)

    def test_optional_unwrap_outputs_are_one_source_preserving_history_step(self):
        import MeshFlatteningCommand

        undo_before = self.document.UndoCount
        self.document.openTransaction("Tracked optional unwrap")
        first = self.document.addObject(
            "Part::Feature",
            "OptionalUnwrapBoundaryA",
        )
        first.Shape = Part.makePolygon([App.Vector(0, 0, 0), App.Vector(4, 0, 0)])
        second = self.document.addObject(
            "Part::Feature",
            "OptionalUnwrapBoundaryB",
        )
        second.Shape = Part.makePolygon([App.Vector(0, 2, 0), App.Vector(4, 2, 0)])
        operation = MeshFlatteningCommand._mark_source_preserving_outputs(
            [first, second],
            self.mesh,
        )
        self.document.commitTransaction()
        self._process_events()

        self.assertIs(operation, second)
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertIs(operation.Source, self.mesh)
        self.assertEqual(
            operation.getTypeIdOfProperty("Source"),
            "App::PropertyLinkHidden",
        )
        self.assertEqual(first.VibeCADTimelineRole, "resource")
        self.assertIs(first.VibeCADTimelineOwner, operation)
        self.assertEqual(
            first.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        self.assertIn(
            "Hidden",
            operation.getEditorMode("VibeCADTimelineRole"),
        )
        self.assertIn(
            "Hidden",
            first.getEditorMode("VibeCADTimelineOwner"),
        )
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIn(first, timeline.Operations)
        self.assertIn(operation, timeline.Operations)
        visible_names = self._timeline_object_names()
        self.assertNotIn(first.Name, visible_names)
        self.assertEqual(visible_names.count(operation.Name), 1)
        self.assertTrue(self.mesh.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        first_name = first.Name
        operation_name = operation.Name
        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(first_name))
        self.assertIsNone(self.document.getObject(operation_name))
        self.document.redo()
        self._process_events()
        restored_first = self.document.getObject(first_name)
        restored_operation = self.document.getObject(operation_name)
        self.assertIsNotNone(restored_first)
        self.assertIsNotNone(restored_operation)
        self.assertEqual(
            restored_first.VibeCADTimelineRole,
            "resource",
        )
        self.assertIs(
            restored_first.VibeCADTimelineOwner,
            restored_operation,
        )
        self.assertEqual(
            restored_operation.VibeCADTimelineRole,
            "operation",
        )
        self.assertIs(restored_operation.Source, self.mesh)

    def test_optional_unwrap_refuses_a_caller_owned_transaction(self):
        import MeshFlatteningCommand

        self.document.openTransaction("Caller-owned mesh edit")
        transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(transaction_id, 0)
        with self.assertRaisesRegex(
            RuntimeError,
            "transaction is already active",
        ):
            MeshFlatteningCommand._begin_unwrap(
                self.document,
                "Unwrap mesh",
            )
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction_id,
        )
        App.closeActiveTransaction(True, transaction_id)

    def test_optional_unwrap_closes_only_its_captured_document(self):
        import MeshFlatteningCommand

        background = self._new_document("VibeCADMeshUnwrapBackground")
        App.setActiveDocument(self.document.Name)
        label_before = self.mesh.Label
        transaction = MeshFlatteningCommand._begin_unwrap(
            self.document,
            "Unwrap mesh",
        )
        self.mesh.Label = "Provisional unwrap source"
        App.setActiveDocument(background.Name)

        MeshFlatteningCommand._abort_unwrap(transaction)

        self.assertEqual(self.mesh.Label, label_before)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(background.getBookedTransactionID(), 0)

    def test_every_mutating_or_interactive_family_refuses_caller_work(self):
        single_mesh = {
            "Mesh_HarmonizeNormals",
            "Mesh_FlipNormals",
            "Mesh_FillupHoles",
            "Mesh_AddFacet",
            "Mesh_RemeshGmsh",
            "Mesh_Smoothing",
            "Mesh_Decimating",
            "Mesh_Scale",
            "Mesh_VertexCurvature",
            "Mesh_SplitComponents",
            "Mesh_Segmentation",
            "Mesh_SegmentationBestFit",
            "Mesh_RemoveCompByHand",
        }
        document_mesh = {
            "Mesh_FillInteractiveHole",
            "Mesh_RemoveComponents",
            "MeshPart_CurveOnMesh",
        }
        multiple_mesh = {
            "Mesh_Union",
            "Mesh_Intersection",
            "Mesh_Difference",
            "Mesh_Merge",
            "Mesh_PolyCut",
            "Mesh_PolyTrim",
            "Mesh_CrossSections",
        }
        for command_name in sorted(single_mesh):
            self._select(self.mesh)
            self._assert_refuses_caller_transaction(command_name)
        for command_name in sorted(document_mesh):
            self._select(self.mesh)
            self._assert_refuses_caller_transaction(command_name)
        for command_name in sorted(multiple_mesh):
            self._select(self.mesh, self.second_mesh)
            self._assert_refuses_caller_transaction(command_name)

        self._select(self.mesh, self.plane)
        self._assert_refuses_caller_transaction("Mesh_TrimByPlane")
        self._select(self.mesh, self.plane)
        self._assert_refuses_caller_transaction("Mesh_SectionByPlane")
        self._select(self.shape)
        self._assert_refuses_caller_transaction("Mesh_FromPartShape")
        self._select(self.mesh)
        self._assert_refuses_caller_transaction("MeshPart_ShapeFromMesh")
        self._select(self.mesh)
        self._assert_refuses_caller_transaction("Mesh_BuildRegularSolid")
        self._select(self.mesh)
        self._assert_refuses_caller_transaction("Mesh_Import")

    def test_read_only_tools_remain_available_during_caller_work(self):
        self._select(self.mesh)
        for command_name in sorted(READ_ONLY_COMMANDS):
            self.assertTrue(Gui.isCommandActive(command_name), command_name)
        self.document.openTransaction("Caller owned")
        try:
            for command_name in sorted(READ_ONLY_COMMANDS):
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
        finally:
            self.document.abortTransaction()

    def test_flip_creates_one_parametric_operation_and_one_undo(self):
        self._select(self.mesh)
        normal_before = self.mesh.Mesh.Facets[0].Normal
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_FlipNormals", 0)
        self._wait_for_mesh_job("mesh.modify.flip_normals.human")
        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        result_name = result.Name
        self.assertEqual(result.TypeId, "Mesh::FlipNormals")
        self.assertIs(result.Source, self.mesh)
        self.assertFalse(result.Suppressed)
        self.assertFalse(self.mesh.Visibility)
        self.assertTrue(result.Visibility)
        self.assertAlmostEqual(
            self.mesh.Mesh.Facets[0].Normal.dot(normal_before),
            1.0,
            5,
        )
        self.assertAlmostEqual(
            result.Mesh.Facets[0].Normal.dot(normal_before),
            -1.0,
            5,
        )
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertFalse(self.document.HasPendingTransaction)
        self.document.undo()
        self.assertIsNone(self.document.getObject(result_name))
        self.assertTrue(self.mesh.Visibility)
        self.assertAlmostEqual(
            self.mesh.Mesh.Facets[0].Normal.dot(normal_before),
            1.0,
            5,
        )

    def test_parametric_flip_suppression_is_a_real_bypass(self):
        result = self.document.addObject(
            "Mesh::FlipNormals",
            "ParametricFlip",
        )
        result.Source = self.mesh
        self.document.recompute()
        normal_before = self.mesh.Mesh.Facets[0].Normal
        self.assertAlmostEqual(
            result.Mesh.Facets[0].Normal.dot(normal_before),
            -1.0,
            5,
        )

        result.Suppressed = True
        self.document.recompute()
        self.assertAlmostEqual(
            result.Mesh.Facets[0].Normal.dot(normal_before),
            1.0,
            5,
        )

        result.Suppressed = False
        self.document.recompute()
        self.assertAlmostEqual(
            result.Mesh.Facets[0].Normal.dot(normal_before),
            -1.0,
            5,
        )

    def test_fill_holes_creates_editable_linked_operation_and_one_undo(self):
        source = self.document.addObject(
            "Mesh::Feature",
            "OpenMesh",
        )
        source.Mesh = _open_tetrahedron()
        self.document.recompute()
        source_facets = source.Mesh.CountFacets
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount

        self._select(source)
        self._accept_input_dialog(3, integer=True)
        Gui.runCommand("Mesh_FillupHoles", 0)
        self._wait_for_mesh_job("mesh.modify.fill_holes.human")

        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "Mesh::FillHoles")
        result_name = result.Name
        self.assertIs(result.Source, source)
        self.assertEqual(result.FillupHolesOfLength, 3)
        self.assertEqual(result.Method, "Flat")
        self.assertEqual(source.Mesh.CountFacets, source_facets)
        self.assertGreater(result.Mesh.CountFacets, source_facets)
        self.assertFalse(source.Visibility)
        self.assertTrue(result.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertFalse(self.document.HasPendingTransaction)

        result.FillupHolesOfLength = 2
        self.document.recompute()
        self.assertEqual(result.Mesh.CountFacets, source_facets)
        result.FillupHolesOfLength = 3
        self.document.recompute()
        self.assertGreater(result.Mesh.CountFacets, source_facets)

        result.Suppressed = True
        self.document.recompute()
        self.assertEqual(result.Mesh.CountFacets, source_facets)
        result.Suppressed = False
        self.document.recompute()
        self.assertGreater(result.Mesh.CountFacets, source_facets)

        self.document.undo()
        self.assertIsNone(self.document.getObject(result_name))
        self.assertTrue(source.Visibility)
        self.assertEqual(source.Mesh.CountFacets, source_facets)

    def test_linked_fill_holes_survives_save_reopen_and_recomputes(self):
        persistence_document = self._new_document(
            "MeshFilterPersistence",
        )
        source = persistence_document.addObject(
            "Mesh::Feature",
            "PersistentOpenMesh",
        )
        source.Mesh = _open_tetrahedron()
        result = persistence_document.addObject(
            "Mesh::FillHoles",
            "PersistentFillHoles",
        )
        result.Source = source
        result.FillupHolesOfLength = 3
        result.Method = "Flat"
        repair = persistence_document.addObject(
            "Mesh::FixNonManifolds",
            "PersistentNonManifoldRepair",
        )
        repair.Source = source
        repair.RemoveNonManifoldPoints = True
        persistence_document.recompute()
        source.Visibility = False
        result.Visibility = True
        self.assertEqual(source.Mesh.CountFacets, 3)
        self.assertEqual(result.Mesh.CountFacets, 4)

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "native-mesh-filter.FCStd"
            persistence_document.saveAs(str(path))
            document_name = persistence_document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)

            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            reopened_source = reopened.getObject("PersistentOpenMesh")
            reopened_result = reopened.getObject("PersistentFillHoles")
            reopened_repair = reopened.getObject("PersistentNonManifoldRepair")
            self.assertEqual(
                reopened_result.TypeId,
                "Mesh::FillHoles",
            )
            self.assertIs(reopened_result.Source, reopened_source)
            self.assertEqual(reopened_result.Method, "Flat")
            self.assertEqual(
                reopened_result.FillupHolesOfLength,
                3,
            )
            self.assertEqual(reopened_source.Mesh.CountFacets, 3)
            self.assertEqual(reopened_result.Mesh.CountFacets, 4)
            self.assertFalse(reopened_source.Visibility)
            self.assertTrue(reopened_result.Visibility)
            self.assertIs(reopened_repair.Source, reopened_source)
            self.assertTrue(reopened_repair.RemoveNonManifoldPoints)

            reopened_result.FillupHolesOfLength = 2
            reopened.recompute()
            self.assertEqual(reopened_result.Mesh.CountFacets, 3)
            reopened_result.FillupHolesOfLength = 3
            reopened.recompute()
            self.assertEqual(reopened_result.Mesh.CountFacets, 4)

            reopened_result.Suppressed = True
            reopened.recompute()
            self.assertEqual(reopened_result.Mesh.CountFacets, 3)
            reopened_result.Suppressed = False
            reopened.recompute()
            self.assertEqual(reopened_result.Mesh.CountFacets, 4)

    def test_native_repair_options_and_suppression_are_computational(self):
        source = self.document.addObject(
            "Mesh::Feature",
            "RepairSource",
        )
        source.Mesh = _open_tetrahedron()
        repair = self.document.addObject(
            "Mesh::FixNonManifolds",
            "RepairNonManifolds",
        )
        repair.Source = source
        repair.RemoveNonManifoldPoints = True
        legacy_fill = self.document.addObject(
            "Mesh::FillHoles",
            "LegacyCompatibleFill",
        )
        legacy_fill.Source = source
        self.document.recompute()

        self.assertIs(repair.Source, source)
        self.assertTrue(repair.RemoveNonManifoldPoints)
        self.assertEqual(
            legacy_fill.Method,
            "Constrained Delaunay",
        )
        self.assertFalse(repair.Suppressed)
        repair.Suppressed = True
        self.document.recompute()
        self.assertEqual(
            repair.Mesh.CountFacets,
            source.Mesh.CountFacets,
        )

    def test_every_native_mesh_filter_has_a_real_suppression_bypass(self):
        self.mesh.Placement = App.Placement(
            App.Vector(11.0, -4.0, 3.0),
            App.Rotation(),
        )
        filter_types = (
            "Mesh::Repair",
            "Mesh::HarmonizeNormals",
            "Mesh::FlipNormals",
            "Mesh::FixNonManifolds",
            "Mesh::FixDuplicatedFaces",
            "Mesh::FixDuplicatedPoints",
            "Mesh::FixDegenerations",
            "Mesh::FixDeformations",
            "Mesh::FixIndices",
            "Mesh::FillHoles",
            "Mesh::RemoveComponents",
        )
        for index, type_name in enumerate(filter_types):
            result = self.document.addObject(
                type_name,
                f"SuppressedFilter{index}",
            )
            result.Source = self.mesh
            result.Suppressed = True
        self.document.recompute()

        source_points = self._mesh_points(self.mesh)
        source_placement = self._placement_tuple(self.mesh)
        for index, type_name in enumerate(filter_types):
            result = self.document.getObject(f"SuppressedFilter{index}")
            self.assertEqual(result.TypeId, type_name)
            self.assertTrue(result.hasExtension("App::SuppressibleExtension"))
            self.assertTrue(result.Suppressed)
            self.assertEqual(
                result.Mesh.CountFacets,
                self.mesh.Mesh.CountFacets,
            )
            self.assertEqual(
                self._mesh_points(result),
                source_points,
            )
            self.assertEqual(
                self._placement_tuple(result),
                source_placement,
            )

    def test_evaluate_repair_creates_linked_duplicate_face_filter(self):
        a = App.Vector(0.0, 0.0, 0.0)
        b = App.Vector(8.0, 0.0, 0.0)
        c = App.Vector(0.0, 7.0, 0.0)
        source = self.document.addObject(
            "Mesh::Feature",
            "DuplicatedFaceSource",
        )
        source.Mesh = Mesh.Mesh(
            [
                (a, b, c),
                (a, b, c),
            ]
        )
        self.document.recompute()
        self.assertEqual(source.Mesh.CountFacets, 2)
        self._select(source)
        Gui.runCommand("Mesh_Evaluation", 0)
        self._process_events(8)

        def visible_button(name):
            for widget in QtGui.QApplication.topLevelWidgets():
                button = widget.findChild(QtGui.QPushButton, name)
                if button and button.isVisible():
                    return button
            return None

        analyze = visible_button("analyzeDuplicatedFacesButton")
        self.assertIsNotNone(analyze)
        analyze.click()
        self._process_events(8)
        repair = visible_button("repairDuplicatedFacesButton")
        self.assertIsNotNone(repair)
        self.assertTrue(repair.isEnabled())

        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        repair.click()
        self._process_events(10)
        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(
            result.TypeId,
            "Mesh::FixDuplicatedFaces",
        )
        self.assertIs(result.Source, source)
        self.assertEqual(source.Mesh.CountFacets, 2)
        self.assertEqual(result.Mesh.CountFacets, 1)
        self.assertFalse(source.Visibility)
        self.assertTrue(result.Visibility)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            [source],
        )
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        result_name = result.Name
        self.document.undo()
        self._process_events(5)
        self.assertIsNone(self.document.getObject(result_name))
        self.assertTrue(source.Visibility)
        self.assertEqual(source.Mesh.CountFacets, 2)

    def test_merge_is_one_linked_replacement_operation(self):
        self.second_mesh.Placement = App.Placement(
            App.Vector(20.0, 0.0, 0.0),
            App.Rotation(),
        )
        self.document.recompute()
        primary_facets = self.mesh.Mesh.CountFacets
        secondary_facets = self.second_mesh.Mesh.CountFacets
        self._select(self.mesh, self.second_mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_Merge", 0)
        self._wait_for_mesh_job("mesh.segment.merge.human")
        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        result_name = result.Name
        primary_name = self.mesh.Name
        secondary_name = self.second_mesh.Name
        self.assertEqual(result.TypeId, "Mesh::Merge")
        self.assertEqual(
            list(result.Sources),
            [self.mesh, self.second_mesh],
        )
        self.assertEqual(
            result.Mesh.CountFacets,
            primary_facets + secondary_facets,
        )
        self.assertEqual(result.Mesh.countSegments(), 2)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            [self.mesh, self.second_mesh],
        )
        self.assertTrue(result.Visibility)
        self.assertFalse(self.mesh.Visibility)
        self.assertFalse(self.second_mesh.Visibility)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIn(result, timeline.Operations)
        self.assertEqual(
            self._timeline_object_names().count(result.Name),
            1,
        )
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.document.undo()
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(self.second_mesh.Visibility)
        self.document.redo()
        self._process_events(10)
        result = self.document.getObject(result_name)
        self.assertIsNotNone(result)
        self.assertEqual(
            list(result.Sources),
            [self.mesh, self.second_mesh],
        )
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            [self.mesh, self.second_mesh],
        )
        self.assertTrue(result.Visibility)
        self.assertFalse(self.mesh.Visibility)
        self.assertFalse(self.second_mesh.Visibility)

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(10)
        self.assertFalse(result.Visibility)
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(self.second_mesh.Visibility)
        self.assertTrue(result.Suppressed)
        self.assertEqual(result.Mesh.CountFacets, 0)

        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(10)
        self.assertTrue(result.Visibility)
        self.assertFalse(self.mesh.Visibility)
        self.assertFalse(self.second_mesh.Visibility)
        self.assertFalse(result.Suppressed)
        self.assertEqual(
            result.Mesh.CountFacets,
            primary_facets + secondary_facets,
        )

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "mesh-merge.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            self.document = reopened
            self.mesh = reopened.getObject(primary_name)
            self.second_mesh = reopened.getObject(secondary_name)
            reopened_result = reopened.getObject(result_name)
            self.assertIsNotNone(reopened_result)
            self.assertEqual(reopened_result.TypeId, "Mesh::Merge")
            self.assertEqual(
                list(reopened_result.Sources),
                [self.mesh, self.second_mesh],
            )
            self.assertEqual(
                list(reopened_result.VibeCADTimelineReplacedInputs),
                [self.mesh, self.second_mesh],
            )
            self.assertEqual(
                reopened_result.Mesh.CountFacets,
                primary_facets + secondary_facets,
            )
            self.assertTrue(reopened_result.Visibility)
            self.assertFalse(self.mesh.Visibility)
            self.assertFalse(self.second_mesh.Visibility)

            points_before = self._mesh_points(reopened_result)
            reopened_result.Placement = App.Placement(
                App.Vector(0.0, 12.0, 0.0),
                App.Rotation(),
            )
            reopened.recompute()
            self.assertTrue(reopened_result.isValid())
            self.assertEqual(
                self._mesh_points(reopened_result),
                points_before,
            )
            self.assertAlmostEqual(
                reopened_result.Placement.Base.y,
                12.0,
            )

            self.second_mesh.Placement = App.Placement(
                App.Vector(30.0, 0.0, 0.0),
                App.Rotation(),
            )
            reopened.recompute()
            self.assertFalse(reopened_result.isValid())
            self.assertTrue(reopened_result.AcceptedSourcesStale)
            self.assertEqual(reopened_result.Mesh.CountFacets, 0)
            self.assertIn("rerun the merge", reopened_result.getStatusString())

            self.mesh.Mesh = _open_tetrahedron()
            reopened.recompute()
            self.assertFalse(reopened_result.isValid())
            self.assertEqual(reopened_result.Mesh.CountFacets, 0)

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(reopened_result)
            Gui.runCommand("Std_Delete", 0)
            self._process_events(10)
            self.assertIsNone(reopened.getObject(result_name))
            self.assertTrue(self.mesh.Visibility)
            self.assertTrue(self.second_mesh.Visibility)

            reopened.undo()
            self._process_events(10)
            restored_result = reopened.getObject(result_name)
            self.assertIsNotNone(restored_result)
            self.assertEqual(
                list(restored_result.Sources),
                [self.mesh, self.second_mesh],
            )
            self.assertTrue(restored_result.Visibility)
            self.assertFalse(self.mesh.Visibility)
            self.assertFalse(self.second_mesh.Visibility)

            reopened.redo()
            self._process_events(10)
            self.assertIsNone(reopened.getObject(result_name))
            self.assertTrue(self.mesh.Visibility)
            self.assertTrue(self.second_mesh.Visibility)

    def test_merge_restores_only_sources_visible_when_the_command_started(self):
        self.mesh.Visibility = True
        self.second_mesh.Visibility = False
        self._select(self.mesh, self.second_mesh)
        objects_before = tuple(self.document.Objects)

        Gui.runCommand("Mesh_Merge", 0)
        self._wait_for_mesh_job("mesh.segment.merge.human")
        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "Mesh::Merge")
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            [self.mesh],
        )
        self.assertTrue(result.Visibility)
        self.assertFalse(self.mesh.Visibility)
        self.assertFalse(self.second_mesh.Visibility)

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(10)
        self.assertFalse(result.Visibility)
        self.assertTrue(self.mesh.Visibility)
        self.assertFalse(self.second_mesh.Visibility)

        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(10)
        self.assertTrue(result.Visibility)
        self.assertFalse(self.mesh.Visibility)
        self.assertFalse(self.second_mesh.Visibility)

    def test_parametric_merge_rejects_invalid_links_and_recovers(self):
        result = self.document.addObject("Mesh::Merge", "ParametricMerge")
        result.Sources = [self.mesh]
        self.document.recompute()
        self.assertFalse(result.isValid())
        self.assertEqual(result.Mesh.CountFacets, 0)

        result.Sources = [self.mesh, self.second_mesh]
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())
        self.assertEqual(
            result.Mesh.CountFacets,
            self.mesh.Mesh.CountFacets + self.second_mesh.Mesh.CountFacets,
        )

        result.Sources = [self.mesh, self.mesh]
        self.document.recompute()
        self.assertFalse(result.isValid())
        self.assertEqual(result.Mesh.CountFacets, 0)

        result.Sources = [self.mesh, self.second_mesh]
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())

    def test_native_parametric_booleans_have_exact_links_volumes_visibility_and_undo(
        self,
    ):
        first = self.document.addObject("Mesh::Feature", "BooleanFirst")
        first.Mesh = _box_mesh()
        second = self.document.addObject("Mesh::Feature", "BooleanSecond")
        second.Mesh = _box_mesh()
        second.Placement = App.Placement(
            App.Vector(3.0, 0.0, 0.0),
            App.Rotation(),
        )
        self.document.recompute()

        cases = (
            ("Mesh_Union", "Union", 462.0),
            ("Mesh_Intersection", "Intersection", 210.0),
            ("Mesh_Difference", "Difference", 126.0),
        )
        for command_name, operation, expected_volume in cases:
            first.ViewObject.Visibility = True
            second.ViewObject.Visibility = True
            self._select(first, second)
            objects_before = tuple(self.document.Objects)
            undo_before = self.document.UndoCount

            self.assertTrue(Gui.isCommandActive(command_name), command_name)
            Gui.runCommand(command_name, 0)
            self._wait_for_mesh_boolean()

            created = [
                obj for obj in self.document.Objects if obj not in objects_before
            ]
            self.assertEqual(len(created), 1, command_name)
            result = created[0]
            self.assertEqual(result.TypeId, "MeshPart::Boolean")
            self.assertFalse(result.UpdateFromSource)
            self.assertIs(result.Source1, first)
            self.assertIs(result.Source2, second)
            self.assertEqual(result.Operation, operation)
            self.assertGreater(result.Mesh.CountFacets, 0)
            self.assertTrue(result.Mesh.isSolid())
            self.assertAlmostEqual(
                abs(result.Mesh.Volume),
                expected_volume,
                delta=1.0e-4,
            )
            self.assertFalse(first.ViewObject.Visibility)
            self.assertFalse(second.ViewObject.Visibility)
            self.assertTrue(result.ViewObject.Visibility)
            self.assertEqual(result.VibeCADTimelineRole, "operation")
            self.assertEqual(
                list(result.VibeCADTimelineReplacedInputs),
                [first, second],
            )
            self.assertEqual(
                result.getTypeIdOfProperty("VibeCADTimelineReplacedInputs"),
                "App::PropertyLinkListHidden",
            )
            timeline = self.document.getObject("VibeCADTimeline")
            self.assertIn(result, timeline.Operations)
            timeline_names = self._timeline_object_names()
            self.assertEqual(timeline_names.count(result.Name), 1)
            self.assertEqual(self.document.UndoCount, undo_before + 1)
            self.assertFalse(self.document.HasPendingTransaction)

            result_name = result.Name
            self.document.undo()
            self._process_events(5)
            self.assertEqual(tuple(self.document.Objects), objects_before)
            self.assertTrue(first.ViewObject.Visibility)
            self.assertTrue(second.ViewObject.Visibility)

            self.document.redo()
            self._process_events(5)
            redone = self.document.getObject(result_name)
            self.assertIsNotNone(redone)
            self.assertIs(redone.Source1, first)
            self.assertIs(redone.Source2, second)
            self.assertEqual(redone.Operation, operation)
            self.assertEqual(redone.VibeCADTimelineRole, "operation")
            self.assertEqual(
                list(redone.VibeCADTimelineReplacedInputs),
                [first, second],
            )
            self.assertAlmostEqual(
                abs(redone.Mesh.Volume),
                expected_volume,
                delta=1.0e-4,
            )
            self.assertFalse(first.ViewObject.Visibility)
            self.assertFalse(second.ViewObject.Visibility)
            self.assertTrue(redone.ViewObject.Visibility)

            self._timeline_button("VibeCADFeatureTimelinePrevious").click()
            self._process_events(10)
            self.assertFalse(redone.ViewObject.Visibility)
            self.assertTrue(first.ViewObject.Visibility)
            self.assertTrue(second.ViewObject.Visibility)
            self.assertTrue(redone.Suppressed)
            self.assertGreater(redone.Mesh.CountFacets, 0)
            self.assertIs(redone.Source1, first)
            self.assertIs(redone.Source2, second)

            self._timeline_button("VibeCADFeatureTimelineEnd").click()
            self._process_events(10)
            self.assertTrue(redone.ViewObject.Visibility)
            self.assertFalse(first.ViewObject.Visibility)
            self.assertFalse(second.ViewObject.Visibility)
            self.assertFalse(redone.Suppressed)
            self.assertAlmostEqual(
                abs(redone.Mesh.Volume),
                expected_volume,
                delta=1.0e-4,
            )

            # The two marker moves are exact undoable transactions. Remove
            # them before undoing the original one-command boolean.
            self.document.undo()
            self.document.undo()
            self.document.undo()
            self._process_events(5)
            self.assertEqual(tuple(self.document.Objects), objects_before)
            self.assertTrue(first.ViewObject.Visibility)
            self.assertTrue(second.ViewObject.Visibility)

    def test_native_mesh_boolean_timeline_contract_survives_reopen(self):
        cases = (
            ("Mesh_Union", "Union", 462.0),
            ("Mesh_Intersection", "Intersection", 210.0),
            ("Mesh_Difference", "Difference", 126.0),
        )
        for command_name, operation, expected_volume in cases:
            document = self._new_document(f"Timeline{operation}MeshBoolean")
            App.setActiveDocument(document.Name)
            first = document.addObject(
                "Mesh::Feature",
                f"{operation}First",
            )
            first.Mesh = _box_mesh()
            second = document.addObject(
                "Mesh::Feature",
                f"{operation}Second",
            )
            second.Mesh = _box_mesh()
            second.Placement.Base.x = 3.0
            document.recompute()
            objects_before = tuple(document.Objects)
            undo_before = document.UndoCount

            self._select(first, second)
            Gui.runCommand(command_name, 0)
            self._wait_for_mesh_boolean(document)
            created = [
                obj
                for obj in document.Objects
                if obj not in objects_before and obj.TypeId == "MeshPart::Boolean"
            ]
            self.assertEqual(len(created), 1, command_name)
            result = created[0]
            meshes = document.getObject("Meshes")
            self.assertIsNotNone(meshes)
            self.assertIn(result, meshes.Group)
            result_name = result.Name
            self.assertEqual(result.TypeId, "MeshPart::Boolean")
            self.assertEqual(result.Operation, operation)
            self.assertEqual(result.VibeCADTimelineRole, "operation")
            self.assertEqual(
                list(result.VibeCADTimelineReplacedInputs),
                [first, second],
            )
            self.assertAlmostEqual(
                abs(result.Mesh.Volume),
                expected_volume,
                delta=1.0e-4,
            )
            self.assertEqual(document.UndoCount, undo_before + 1)

            document.undo()
            self._process_events()
            self.assertEqual(tuple(document.Objects), objects_before)
            self.assertTrue(first.Visibility)
            self.assertTrue(second.Visibility)
            document.redo()
            self._process_events()
            result = document.getObject(result_name)
            self.assertIsNotNone(result)
            self.assertEqual(
                list(result.VibeCADTimelineReplacedInputs),
                [first, second],
            )
            self.assertTrue(result.Visibility)
            self.assertFalse(first.Visibility)
            self.assertFalse(second.Visibility)

            first_name = first.Name
            second_name = second.Name
            with TemporaryDirectory() as temporary_directory:
                path = Path(temporary_directory) / f"{operation.lower()}-timeline.FCStd"
                document.saveAs(str(path))
                document_name = document.Name
                App.closeDocument(document_name)
                self.documents.remove(document_name)
                reopened = App.openDocument(str(path))
                self.documents.append(reopened.Name)
                App.setActiveDocument(reopened.Name)
                self._process_events(10)

                reopened_first = reopened.getObject(first_name)
                reopened_second = reopened.getObject(second_name)
                reopened_result = reopened.getObject(result_name)
                reopened_timeline = reopened.getObject("VibeCADTimeline")
                self.assertIsNotNone(reopened_result)
                self.assertIs(reopened_result.Source1, reopened_first)
                self.assertIs(reopened_result.Source2, reopened_second)
                self.assertEqual(
                    reopened_result.VibeCADTimelineRole,
                    "operation",
                )
                self.assertEqual(
                    list(reopened_result.VibeCADTimelineReplacedInputs),
                    [reopened_first, reopened_second],
                )
                self.assertIn(
                    reopened_result,
                    reopened_timeline.Operations,
                )
                self.assertTrue(reopened_result.Visibility)
                self.assertFalse(reopened_first.Visibility)
                self.assertFalse(reopened_second.Visibility)

                self._timeline_button("VibeCADFeatureTimelinePrevious").click()
                self._process_events(10)
                self.assertFalse(reopened_result.Visibility)
                self.assertTrue(reopened_first.Visibility)
                self.assertTrue(reopened_second.Visibility)

                self._timeline_button("VibeCADFeatureTimelineEnd").click()
                self._process_events(10)
                self.assertTrue(reopened_result.Visibility)
                self.assertFalse(reopened_first.Visibility)
                self.assertFalse(reopened_second.Visibility)

                reopened_name = reopened.Name
                App.closeDocument(reopened_name)
                self.documents.remove(reopened_name)

        App.setActiveDocument(self.document.Name)
        self._process_events()

    def test_native_mesh_boolean_recomputes_for_source_and_property_edits(self):
        first = self.document.addObject("Mesh::Feature", "ParametricFirst")
        first.Mesh = _box_mesh()
        second = self.document.addObject("Mesh::Feature", "ParametricSecond")
        second.Mesh = _box_mesh()
        second.Placement = App.Placement(
            App.Vector(3.0, 0.0, 0.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 90.0),
        )
        self.document.recompute()
        result = self.document.addObject(
            "MeshPart::Boolean",
            "EditableParametricUnion",
        )
        result.Source1 = first
        result.Source2 = second
        result.Operation = "Union"
        self.document.recompute()
        self.assertTrue(result.UpdateFromSource)
        self.assertAlmostEqual(
            abs(result.Mesh.Volume),
            483.0,
            delta=1.0e-4,
        )
        result.Placement = App.Placement(
            App.Vector(10.0, 2.0, -3.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 15.0),
        )

        def assert_result_placement_preserved():
            self.assertAlmostEqual(result.Placement.Base.x, 10.0)
            self.assertAlmostEqual(result.Placement.Base.y, 2.0)
            self.assertAlmostEqual(result.Placement.Base.z, -3.0)
            self.assertAlmostEqual(result.Mesh.Placement.Base.x, 10.0)
            self.assertAlmostEqual(result.Mesh.Placement.Base.y, 2.0)
            self.assertAlmostEqual(result.Mesh.Placement.Base.z, -3.0)
            self.assertTrue(
                result.Mesh.Placement.Rotation.isSame(
                    result.Placement.Rotation,
                    1.0e-12,
                )
            )

        result.Operation = "Intersection"
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())
        assert_result_placement_preserved()
        self.assertAlmostEqual(
            abs(result.Mesh.Volume),
            189.0,
            delta=1.0e-4,
        )

        result.Operation = "Difference"
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())
        assert_result_placement_preserved()
        self.assertAlmostEqual(
            abs(result.Mesh.Volume),
            147.0,
            delta=1.0e-4,
        )

        result.Operation = "Union"
        self.document.recompute()
        second.Placement = App.Placement(
            App.Vector(4.0, 0.0, 0.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 90.0),
        )
        self.document.recompute()
        self._process_events(5)
        self.assertTrue(result.isValid(), result.getStatusString())
        assert_result_placement_preserved()
        self.assertAlmostEqual(
            abs(result.Mesh.Volume),
            525.0,
            delta=1.0e-4,
        )

        second.Mesh = _box_mesh(10.0, 7.0, 6.0)
        second.Placement = App.Placement(
            App.Vector(4.0, 0.0, 0.0),
            App.Rotation(App.Vector(0.0, 0.0, 1.0), 90.0),
        )
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())
        assert_result_placement_preserved()
        self.assertAlmostEqual(
            abs(result.Mesh.Volume),
            609.0,
            delta=1.0e-4,
        )

        class MeshChangeObserver:
            def __init__(self, object_name):
                self.object_name = object_name
                self.changed_properties = []

            def slotChangedObject(self, obj, property_name):
                if obj.Name == self.object_name:
                    self.changed_properties.append(property_name)

        observer = MeshChangeObserver(result.Name)
        App.addDocumentObserver(observer)
        try:
            result.LinearDeflection = 2.0
            result.AngularDeflection = 2.0
            result.Relative = False
            observer.changed_properties.clear()
            self.document.recompute()
            self.assertIn("Mesh", observer.changed_properties)
            self.assertTrue(result.isValid(), result.getStatusString())
            assert_result_placement_preserved()
            coarse_volume = abs(result.Mesh.Volume)

            result.LinearDeflection = 0.02
            result.AngularDeflection = 0.1
            result.Relative = True
            observer.changed_properties.clear()
            self.document.recompute()
            self.assertIn("Mesh", observer.changed_properties)
            self.assertTrue(result.isValid(), result.getStatusString())
            assert_result_placement_preserved()
            self.assertAlmostEqual(
                abs(result.Mesh.Volume),
                coarse_volume,
                delta=1.0e-4,
            )
        finally:
            App.removeDocumentObserver(observer)

    def test_native_mesh_boolean_preserves_disconnected_solids_and_cavities(
        self,
    ):
        first = self.document.addObject(
            "Mesh::Feature",
            "DisconnectedFirst",
        )
        first.Mesh = _box_mesh()
        second = self.document.addObject(
            "Mesh::Feature",
            "DisconnectedSecond",
        )
        second.Mesh = _box_mesh()
        second.Placement.Base.x = 20.0
        result = self.document.addObject(
            "MeshPart::Boolean",
            "DisconnectedUnion",
        )
        result.Source1 = first
        result.Source2 = second
        result.Operation = "Union"
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())
        self.assertTrue(result.Mesh.isSolid())
        self.assertEqual(result.Mesh.countComponents(), 2)
        self.assertAlmostEqual(
            abs(result.Mesh.Volume),
            672.0,
            delta=1.0e-4,
        )

        multi_mesh = Mesh.createBox(2.0, 2.0, 2.0)
        far_component = Mesh.createBox(2.0, 2.0, 2.0)
        far_component.translate(10.0, 0.0, 0.0)
        multi_mesh.addMesh(far_component)
        multi_source = self.document.addObject(
            "Mesh::Feature",
            "DisconnectedSource",
        )
        multi_source.Mesh = multi_mesh
        local_source = self.document.addObject(
            "Mesh::Feature",
            "LocalSource",
        )
        local_source.Mesh = _box_mesh(3.0, 3.0, 3.0)
        multi_result = self.document.addObject(
            "MeshPart::Boolean",
            "DisconnectedSourceUnion",
        )
        multi_result.Source1 = multi_source
        multi_result.Source2 = local_source
        multi_result.Operation = "Union"
        self.document.recompute()
        self.assertTrue(
            multi_result.isValid(),
            multi_result.getStatusString(),
        )
        self.assertTrue(multi_result.Mesh.isSolid())
        self.assertEqual(multi_result.Mesh.countComponents(), 2)
        self.assertAlmostEqual(
            abs(multi_result.Mesh.Volume),
            35.0,
            delta=1.0e-4,
        )

        hollow_mesh = Mesh.createBox(10.0, 10.0, 10.0)
        cavity_boundary = Mesh.createBox(4.0, 4.0, 4.0)
        cavity_boundary.flipNormals()
        hollow_mesh.addMesh(cavity_boundary)
        hollow_source = self.document.addObject(
            "Mesh::Feature",
            "HollowSource",
        )
        hollow_source.Mesh = hollow_mesh
        inside_void = self.document.addObject(
            "Mesh::Feature",
            "InsideVoid",
        )
        inside_void.Mesh = _box_mesh(2.0, 2.0, 2.0)
        cavity_check = self.document.addObject(
            "MeshPart::Boolean",
            "CavityCheck",
        )
        cavity_check.Source1 = hollow_source
        cavity_check.Source2 = inside_void
        cavity_check.Operation = "Intersection"
        self.document.recompute()
        self.assertFalse(cavity_check.isValid())
        self.assertIn(
            "produced no solid volume",
            cavity_check.getStatusString(),
        )

        disjoint_addition = self.document.addObject(
            "Mesh::Feature",
            "DisjointAddition",
        )
        disjoint_addition.Mesh = _box_mesh(2.0, 2.0, 2.0)
        disjoint_addition.Placement.Base.x = 20.0
        cavity_check.Source2 = disjoint_addition
        cavity_check.Operation = "Union"
        self.document.recompute()
        self.assertTrue(
            cavity_check.isValid(),
            cavity_check.getStatusString(),
        )
        self.assertTrue(cavity_check.Mesh.isSolid())
        self.assertAlmostEqual(
            abs(cavity_check.Mesh.Volume),
            944.0,
            delta=1.0e-4,
        )

    def test_native_mesh_boolean_error_contract_and_selection_scope(self):
        empty = self.document.addObject("Mesh::Feature", "EmptyBoolean")
        solid = self.document.addObject("Mesh::Feature", "SolidBoolean")
        solid.Mesh = _box_mesh()
        invalid = self.document.addObject(
            "MeshPart::Boolean",
            "EmptyInputBoolean",
        )
        invalid.Source1 = empty
        invalid.Source2 = solid
        invalid.Operation = "Union"
        self.document.recompute()
        self.assertFalse(invalid.isValid())
        self.assertIn("is empty", invalid.getStatusString())

        invalid.Source1 = invalid
        self.document.recompute()
        self.assertFalse(invalid.isValid())
        self.assertIn(
            "cannot use itself as a source",
            invalid.getStatusString(),
        )
        invalid.Source1 = empty
        self.document.recompute()

        other_document = self._new_document("MeshBooleanOtherDocument")
        other_mesh = other_document.addObject(
            "Mesh::Feature",
            "OtherDocumentMesh",
        )
        other_mesh.Mesh = _box_mesh()
        other_document.recompute()
        with self.assertRaisesRegex(
            ValueError,
            "does not support external object",
        ):
            invalid.Source1 = other_mesh

        App.setActiveDocument(self.document.Name)
        self._select(solid, other_mesh)
        self._process_events()
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self.assertFalse(Gui.isCommandActive("Mesh_Union"))
        Gui.runCommand("Mesh_Union", 0)
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)

        far = self.document.addObject("Mesh::Feature", "FarBoolean")
        far.Mesh = _box_mesh()
        far.Placement.Base.x = 20.0
        self.document.recompute()
        solid.ViewObject.Visibility = True
        far.ViewObject.Visibility = True
        self._select(solid, far)
        self.assertTrue(Gui.isCommandActive("Mesh_Intersection"))
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._click_message_button("OK")
        Gui.runCommand("Mesh_Intersection", 0)
        self._wait_for_mesh_boolean(expected_phase="failed")
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertTrue(solid.ViewObject.Visibility)
        self.assertTrue(far.ViewObject.Visibility)

        self._select(solid, far)
        Gui.runCommand("Mesh_Difference", 0)
        self._wait_for_mesh_boolean()
        difference = next(
            obj for obj in self.document.Objects if obj not in objects_before
        )
        self.assertTrue(difference.isValid(), difference.getStatusString())
        self.assertAlmostEqual(
            abs(difference.Mesh.Volume),
            abs(solid.Mesh.Volume),
            delta=1.0e-4,
        )

    def test_mesh_difference_preserves_asymmetric_selection_order(self):
        large = self.document.addObject(
            "Mesh::Feature",
            "LargeDifferenceSource",
        )
        large.Mesh = _box_mesh(10.0, 10.0, 10.0)
        cutter = self.document.addObject(
            "Mesh::Feature",
            "SmallDifferenceCutter",
        )
        cutter.Mesh = _box_mesh(4.0, 4.0, 4.0)
        self.document.recompute()

        self._select(large, cutter)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_Difference", 0)
        self._wait_for_mesh_boolean()
        created = [obj for obj in self.document.Objects if obj not in objects_before]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertIs(result.Source1, large)
        self.assertIs(result.Source2, cutter)
        self.assertAlmostEqual(
            abs(result.Mesh.Volume),
            936.0,
            delta=1.0e-4,
        )
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.document.undo()
        self._process_events(5)
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertTrue(large.ViewObject.Visibility)
        self.assertTrue(cutter.ViewObject.Visibility)

        self._select(cutter, large)
        self._click_message_button("OK")
        Gui.runCommand("Mesh_Difference", 0)
        self._wait_for_mesh_boolean(expected_phase="failed")
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertTrue(large.ViewObject.Visibility)
        self.assertTrue(cutter.ViewObject.Visibility)

    def test_native_mesh_boolean_survives_save_reopen_and_recomputes(self):
        persistence_document = self._new_document(
            "MeshBooleanPersistence",
        )
        first = persistence_document.addObject(
            "Mesh::Feature",
            "PersistentFirst",
        )
        first.Mesh = _box_mesh()
        second = persistence_document.addObject(
            "Mesh::Feature",
            "PersistentSecond",
        )
        second.Mesh = _box_mesh()
        second.Placement.Base.x = 3.0
        result = persistence_document.addObject(
            "MeshPart::Boolean",
            "PersistentBoolean",
        )
        result.Source1 = first
        result.Source2 = second
        result.Operation = "Union"
        result.LinearDeflection = 0.25
        result.AngularDeflection = 0.3
        result.Relative = True
        persistence_document.recompute()
        self.assertAlmostEqual(
            abs(result.Mesh.Volume),
            462.0,
            delta=1.0e-4,
        )

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "native-mesh-boolean.FCStd"
            persistence_document.saveAs(str(path))
            document_name = persistence_document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)

            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            reopened_result = reopened.getObject("PersistentBoolean")
            reopened_first = reopened.getObject("PersistentFirst")
            reopened_second = reopened.getObject("PersistentSecond")
            self.assertEqual(
                reopened_result.TypeId,
                "MeshPart::Boolean",
            )
            self.assertIs(reopened_result.Source1, reopened_first)
            self.assertIs(reopened_result.Source2, reopened_second)
            self.assertEqual(reopened_result.Operation, "Union")
            self.assertAlmostEqual(
                reopened_result.LinearDeflection.Value,
                0.25,
            )
            self.assertAlmostEqual(
                reopened_result.AngularDeflection,
                0.3,
            )
            self.assertTrue(reopened_result.Relative)
            self.assertAlmostEqual(
                abs(reopened_result.Mesh.Volume),
                462.0,
                delta=1.0e-4,
            )

            reopened_second.Placement.Base.x = 4.0
            reopened.recompute()
            self.assertTrue(
                reopened_result.isValid(),
                reopened_result.getStatusString(),
            )
            self.assertAlmostEqual(
                abs(reopened_result.Mesh.Volume),
                504.0,
                delta=1.0e-4,
            )

    def test_parametric_mesh_boolean_reports_and_recovers_from_open_input(self):
        open_mesh = self.document.addObject(
            "Mesh::Feature",
            "OpenBooleanInput",
        )
        open_mesh.Mesh = Mesh.Mesh(
            [
                (
                    App.Vector(0.0, 0.0, 0.0),
                    App.Vector(8.0, 0.0, 0.0),
                    App.Vector(0.0, 7.0, 0.0),
                )
            ]
        )
        solid_mesh = self.document.addObject(
            "Mesh::Feature",
            "SolidBooleanInput",
        )
        solid_mesh.Mesh = _box_mesh()
        self.document.recompute()

        self._select(open_mesh, solid_mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._click_message_button("OK")
        Gui.runCommand("Mesh_Union", 0)
        self._wait_for_mesh_boolean(expected_phase="failed")
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertTrue(open_mesh.ViewObject.Visibility)
        self.assertTrue(solid_mesh.ViewObject.Visibility)

        result = self.document.addObject(
            "MeshPart::Boolean",
            "ValidatedBoolean",
        )
        result.Source1 = open_mesh
        result.Source2 = solid_mesh
        result.Operation = "Union"
        self.document.recompute()

        self.assertFalse(result.isValid())
        self.assertIn("open or non-manifold", result.getStatusString())

        open_mesh.Mesh = _box_mesh()
        self.document.recompute()
        self.assertTrue(result.isValid(), result.getStatusString())
        self.assertTrue(result.Mesh.isSolid())
        self.assertAlmostEqual(
            abs(result.Mesh.Volume),
            336.0,
            delta=1.0e-4,
        )

    def test_native_mesh_boolean_rejects_self_intersecting_closed_shells(self):
        overlapping = Mesh.createBox(6.0, 6.0, 6.0)
        crossing_shell = Mesh.createBox(6.0, 6.0, 6.0)
        crossing_shell.translate(2.0, 2.0, 2.0)
        overlapping.addMesh(crossing_shell)
        self.assertTrue(overlapping.isSolid())
        self.assertTrue(overlapping.hasSelfIntersections())

        invalid_source = self.document.addObject(
            "Mesh::Feature",
            "SelfIntersectingBooleanInput",
        )
        invalid_source.Mesh = overlapping
        valid_source = self.document.addObject(
            "Mesh::Feature",
            "SelfIntersectionBooleanTool",
        )
        valid_source.Mesh = _box_mesh(2.0, 2.0, 2.0)
        valid_source.Placement.Base.x = 20.0

        result = self.document.addObject(
            "MeshPart::Boolean",
            "RejectedSelfIntersection",
        )
        result.Source1 = invalid_source
        result.Source2 = valid_source
        result.Operation = "Union"
        self.document.recompute()
        self.assertFalse(result.isValid())
        self.assertEqual(result.Mesh.CountFacets, 0)
        self.assertIn("has self-intersections", result.getStatusString())

        invalid_source.ViewObject.Visibility = True
        valid_source.ViewObject.Visibility = True
        self._select(invalid_source, valid_source)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._click_message_button("OK")
        Gui.runCommand("Mesh_Union", 0)
        self._wait_for_mesh_boolean(expected_phase="failed")
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertTrue(invalid_source.ViewObject.Visibility)
        self.assertTrue(valid_source.ViewObject.Visibility)

    def test_mesh_boolean_commands_have_no_openscad_runtime_path(self):
        source_roots = (
            Path(__file__).resolve().parents[2],
            Path(App.getHomePath()).resolve().parents[1] / "src" / "Mod",
        )
        relative_sources = (
            Path("Mesh") / "Gui" / "Command.cpp",
            Path("MeshPart") / "App" / "MeshBoolean.cpp",
            Path("Mesh") / "Gui" / "CMakeLists.txt",
            Path("MeshPart") / "App" / "CMakeLists.txt",
        )
        source_root = next(
            (
                root
                for root in source_roots
                if all((root / path).is_file() for path in relative_sources)
            ),
            None,
        )
        if source_root is None:
            self.skipTest(
                "Native Mesh boolean sources are not present in this installation"
            )
        sources = tuple(
            source_root / relative_path for relative_path in relative_sources
        )
        native_sources = "\n".join(path.read_text(encoding="utf-8") for path in sources)
        forbidden_runtime_paths = (
            "Open" + "SCAD",
            "meshop" + "tempfile",
            "QProcess",
            "QTemporaryFile",
            "std::system",
            "popen(",
        )
        for forbidden in forbidden_runtime_paths:
            self.assertNotIn(forbidden, native_sources)

    def test_task_family_cancel_has_no_geometry_or_undo(self):
        cases = (
            ("Mesh_Smoothing", (self.mesh,)),
            ("Mesh_Decimating", (self.mesh,)),
            ("Mesh_RemeshGmsh", (self.mesh,)),
            ("Mesh_RemoveComponents", (self.mesh,)),
            ("Mesh_Segmentation", (self.mesh,)),
            ("Mesh_SegmentationBestFit", (self.mesh,)),
            ("Mesh_CrossSections", (self.mesh,)),
            ("Mesh_FromPartShape", (self.shape,)),
            ("MeshPart_CurveOnMesh", (self.mesh,)),
        )
        for command_name, selection in cases:
            self._select(*selection)
            self.assertTrue(Gui.isCommandActive(command_name), command_name)
            objects_before = tuple(self.document.Objects)
            undo_before = self.document.UndoCount
            Gui.runCommand(command_name, 0)
            self._process_events(5)
            self._cancel_task(command_name)
            self.assertEqual(tuple(self.document.Objects), objects_before)
            self.assertEqual(self.document.UndoCount, undo_before)

    def test_smoothing_accept_is_one_undo(self):
        self._select(self.mesh)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_Smoothing", 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        self._wait_for_mesh_job("mesh.modify.smooth.human")
        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.document.undo()
        self.assertFalse(self.document.HasPendingTransaction)

    def test_modeless_regular_solid_create_is_one_undo(self):
        self._select(self.mesh)
        undo_before = self.document.UndoCount
        objects_before = tuple(self.document.Objects)
        Gui.runCommand("Mesh_BuildRegularSolid", 0)
        self._process_events(5)
        dialog = next(
            (
                widget
                for widget in QtGui.QApplication.topLevelWidgets()
                if isinstance(widget, QtGui.QDialog)
                and widget.isVisible()
                and widget.findChild(
                    QtGui.QPushButton,
                    "createSolidButton",
                )
            ),
            None,
        )
        self.assertIsNotNone(dialog)
        create = dialog.findChild(
            QtGui.QPushButton,
            "createSolidButton",
        )
        create.click()
        self._process_events(8)
        created = [obj for obj in self.document.Objects if obj not in objects_before]
        meshes = self.document.getObject("Meshes")
        self.assertIs(meshes, self.meshes_group)
        self.assertEqual(meshes.TypeId, "App::DocumentObjectGroup")
        self.assertEqual(meshes.Label, "Meshes")
        results = [obj for obj in created if obj.TypeId.startswith("Mesh::")]
        self.assertEqual(len(results), 1)
        self.assertIn(results[0], meshes.Group)
        self.assertTrue(all(obj.TypeId.startswith("Mesh::") for obj in meshes.Group))
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertFalse(self.document.HasPendingTransaction)
        self.document.undo()
        self.assertIs(self.document.getObject("Meshes"), self.meshes_group)
        self.assertNotIn(results[0], self.meshes_group.Group)
        self.assertFalse(self.document.HasPendingTransaction)
        dialog.close()

    def test_modeless_regular_solid_keeps_its_launch_document(self):
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_BuildRegularSolid", 0)
        self._process_events(5)
        dialog = next(
            (
                widget
                for widget in QtGui.QApplication.topLevelWidgets()
                if isinstance(widget, QtGui.QDialog)
                and widget.isVisible()
                and widget.findChild(
                    QtGui.QPushButton,
                    "createSolidButton",
                )
            ),
            None,
        )
        self.assertIsNotNone(dialog)

        other = self._new_document("VibeCADMeshOther")
        App.setActiveDocument(other.Name)
        self._process_events()
        dialog.findChild(
            QtGui.QPushButton,
            "createSolidButton",
        ).click()
        self._process_events(8)

        created = [obj for obj in self.document.Objects if obj not in objects_before]
        self.assertEqual(len(created), 1)
        self.assertGreater(created[0].Mesh.CountFacets, 0)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertEqual(tuple(other.Objects), ())
        self.assertEqual(other.UndoCount, 0)
        dialog.close()
        App.setActiveDocument(self.document.Name)

    def test_mesh_import_does_not_adopt_an_unrelated_same_transaction_mesh(
        self,
    ):
        document = self.document

        class SameTransactionMeshObserver:
            def __init__(self):
                self.injected = False
                self.decoy = None

            def slotCreatedObject(self, obj):
                if (
                    self.injected
                    or obj.Document is not document
                    or obj.TypeId != "Mesh::Feature"
                ):
                    return
                self.injected = True
                self.decoy = document.addObject(
                    "Mesh::Feature",
                    "UnrelatedSameTransactionMesh",
                )
                self.decoy.Mesh = _tetrahedron(100.0)

        with TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "exact-import.stl"
            _tetrahedron(25.0).write(str(source_path))
            dialog_preferences = App.ParamGet(
                "User parameter:BaseApp/Preferences/Dialog"
            )
            native_dialog_before = dialog_preferences.GetBool(
                "DontUseNativeDialog",
                False,
            )
            observer = SameTransactionMeshObserver()
            App.addDocumentObserver(observer)
            try:
                dialog_preferences.SetBool("DontUseNativeDialog", True)
                self._accept_open_files_dialog((source_path,))
                Gui.runCommand("Mesh_Import", 0)
                self._wait_for_mesh_import()
            finally:
                App.removeDocumentObserver(observer)
                dialog_preferences.SetBool(
                    "DontUseNativeDialog",
                    native_dialog_before,
                )

        self.assertTrue(observer.injected)
        self.assertIsNotNone(observer.decoy)
        imported = self.document.getObject("exact_import")
        if imported is None:
            imported = next(
                obj
                for obj in self.document.Objects
                if obj.TypeId == "Mesh::Feature"
                and obj is not observer.decoy
                and "VibeCADExternalInputs" in obj.PropertiesList
                and list(obj.VibeCADExternalInputs) == [source_path.name]
            )
        self.assertIsNot(imported, observer.decoy)
        self.assertEqual(imported.VibeCADTimelineRole, "operation")
        if "VibeCADTimelineOwner" in imported.PropertiesList:
            self.assertIsNone(imported.VibeCADTimelineOwner)
        if "VibeCADTimelineOwner" in observer.decoy.PropertiesList:
            self.assertIsNone(observer.decoy.VibeCADTimelineOwner)
        self.assertFalse(
            any(
                obj.TypeId == "Mesh::OutputGroup"
                and observer.decoy in obj.Group
                for obj in self.document.Objects
            )
        )

    def test_mesh_import_persists_only_portable_basename_provenance(self):
        with TemporaryDirectory() as temporary_directory:
            import_directory = (
                Path(temporary_directory) / "local-machine-only" / "private"
            )
            import_directory.mkdir(parents=True)
            source_paths = (
                import_directory / "first-source.stl",
                import_directory / "second-source.stl",
            )
            _tetrahedron().write(str(source_paths[0]))
            _tetrahedron(20.0).write(str(source_paths[1]))

            dialog_preferences = App.ParamGet(
                "User parameter:BaseApp/Preferences/Dialog"
            )
            native_dialog_before = dialog_preferences.GetBool(
                "DontUseNativeDialog",
                False,
            )
            dialog_preferences.SetBool("DontUseNativeDialog", True)
            objects_before = tuple(self.document.Objects)
            undo_before = self.document.UndoCount
            try:
                self._accept_open_files_dialog(source_paths)
                Gui.runCommand("Mesh_Import", 0)
                self._wait_for_mesh_import()
            finally:
                dialog_preferences.SetBool(
                    "DontUseNativeDialog",
                    native_dialog_before,
                )

            created = [
                obj for obj in self.document.Objects if obj not in objects_before
            ]
            controller, results = self._assert_source_preserving_multi_result(
                created,
                (),
                "Mesh::Feature",
                "Import meshes",
                "Standalone",
            )
            self.assertEqual(list(controller.Sources), [])
            expected_provenance = [path.name for path in source_paths]
            self.assertEqual(
                list(controller.ExternalInputs),
                expected_provenance,
            )
            self.assertTrue(
                all(
                    "/" not in item and "\\" not in item and item == Path(item).name
                    for item in controller.ExternalInputs
                )
            )
            self.assertNotIn(
                str(import_directory),
                "\n".join(controller.ExternalInputs),
            )
            self.assertEqual(len(results), 2)
            self.assertTrue(all(result.Mesh.CountFacets > 0 for result in results))
            self.assertEqual(self.document.UndoCount, undo_before + 1)

            controller_name = controller.Name
            result_names = tuple(result.Name for result in results)
            document_path = Path(temporary_directory) / "portable-import.FCStd"
            self.document.saveAs(str(document_path))
            for source_path in source_paths:
                source_path.unlink()
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(document_path))
            self.documents.append(reopened.Name)
            self.document = reopened

            reopened_controller = reopened.getObject(controller_name)
            reopened_results = [
                reopened.getObject(result_name) for result_name in result_names
            ]
            self.assertIsNotNone(reopened_controller)
            self.assertEqual(
                list(reopened_controller.ExternalInputs),
                expected_provenance,
            )
            self.assertNotIn(
                str(import_directory),
                "\n".join(reopened_controller.ExternalInputs),
            )
            self.assertTrue(all(reopened_results))
            self.assertTrue(
                all(result.Mesh.CountFacets > 0 for result in reopened_results)
            )
            reopened.recompute()
            self.assertTrue(reopened_controller.isValid())

    def test_tessellation_does_not_adopt_an_unrelated_same_transaction_mesh(
        self,
    ):
        document = self.document

        class SameTransactionMeshObserver:
            def __init__(self):
                self.injected = False
                self.decoy = None

            def slotCreatedObject(self, obj):
                if (
                    self.injected
                    or obj.Document is not document
                    or obj.TypeId != "MeshPart::MeshFromShape"
                ):
                    return
                self.injected = True
                self.decoy = document.addObject(
                    "Mesh::Feature",
                    "UnrelatedTessellationMesh",
                )
                self.decoy.Mesh = _tetrahedron(150.0)

        self._select(self.shape)
        Gui.runCommand("Mesh_FromPartShape", 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())
        tabs = Gui.getMainWindow().findChild(
            QtGui.QTabWidget,
            "stackedWidget",
        )
        self.assertIsNotNone(tabs)
        tabs.setCurrentIndex(0)

        observer = SameTransactionMeshObserver()
        App.addDocumentObserver(observer)
        try:
            button = self._task_button(QtGui.QDialogButtonBox.Ok)
            self.assertIsNotNone(button)
            button.click()
            self._wait_for_mesh_tessellation()
        finally:
            App.removeDocumentObserver(observer)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertTrue(observer.injected)
        self.assertIsNotNone(observer.decoy)
        generated = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "MeshPart::MeshFromShape"
            and obj is not observer.decoy
            and obj not in (self.mesh, self.second_mesh)
        ]
        self.assertEqual(len(generated), 1)
        result = generated[0]
        self.assertGreater(result.Mesh.CountFacets, 0)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        if "VibeCADTimelineOwner" in result.PropertiesList:
            self.assertIsNone(result.VibeCADTimelineOwner)
        if "VibeCADTimelineOwner" in observer.decoy.PropertiesList:
            self.assertIsNone(observer.decoy.VibeCADTimelineOwner)
        self.assertFalse(
            any(
                obj.TypeId == "Mesh::OutputGroup"
                and observer.decoy in obj.Group
                for obj in self.document.Objects
            )
        )

    def test_tessellation_uses_the_shape_selected_at_launch(self):
        far_shape = self.document.addObject(
            "Part::Feature",
            "FarShape",
        )
        far_shape.Shape = Part.makeBox(
            4.0,
            4.0,
            4.0,
            App.Vector(100.0, 0.0, 0.0),
        )
        self.document.recompute()
        self._select(self.shape)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_FromPartShape", 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())

        self._select(far_shape)
        tabs = Gui.getMainWindow().findChild(
            QtGui.QTabWidget,
            "stackedWidget",
        )
        self.assertIsNotNone(tabs)
        tabs.setCurrentIndex(0)
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        self._wait_for_mesh_tessellation()

        self.assertFalse(Gui.Control.activeDialog())
        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before
            and obj.TypeId == "MeshPart::MeshFromShape"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertIs(result.Source[0], self.shape)
        self.assertEqual(result.Method, "Standard")
        self.assertGreater(result.Mesh.CountFacets, 0)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertFalse(
            any(
                obj not in objects_before and obj.TypeId == "Mesh::OutputGroup"
                for obj in self.document.Objects
            )
        )
        xs = [point.Vector.x for point in created[0].Mesh.Points]
        self.assertLess(max(xs), 20.0)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.shape.Shape = Part.makeBox(24.0, 8.0, 6.0)
        self.document.recompute()
        self.assertTrue(result.isValid())
        updated_xs = [point.Vector.x for point in result.Mesh.Points]
        self.assertEqual(updated_xs, xs)

    def test_tessellation_batch_is_one_source_preserving_history_step(self):
        far_shape = self.document.addObject(
            "Part::Feature",
            "FarShape",
        )
        far_shape.Shape = Part.makeBox(
            4.0,
            4.0,
            4.0,
            App.Vector(30.0, 0.0, 0.0),
        )
        self.document.recompute()
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.shape, far_shape)
        Gui.runCommand("Mesh_FromPartShape", 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())

        tabs = Gui.getMainWindow().findChild(
            QtGui.QTabWidget,
            "stackedWidget",
        )
        self.assertIsNotNone(tabs)
        tabs.setCurrentIndex(0)
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        self._wait_for_mesh_tessellation()

        self.assertFalse(Gui.Control.activeDialog())
        created = [obj for obj in self.document.Objects if obj not in objects_before]
        controller, results = self._assert_source_preserving_multi_result(
            created,
            (self.shape, far_shape),
            "MeshPart::MeshFromShape",
            "Mesh from shape",
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(
            {result.Source[0] for result in results},
            {self.shape, far_shape},
        )
        self.assertTrue(all(result.Mesh.CountFacets > 0 for result in results))
        self.assertTrue(all(result.Visibility for result in results))
        self.assertEqual(controller.Mesh.CountFacets, 0)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

    def test_generic_geometry_meshing_recomputes_from_its_linked_source(self):
        self._select(self.shape)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._accept_input_dialog(0.2)
        Gui.runCommand("Mesh_FromGeometry", 0)
        self._process_events(10)

        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before
            and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "Mesh::MeshFromGeometry")
        self.assertIs(result.Source, self.shape)
        self.assertAlmostEqual(result.Tolerance, 0.2)
        self.assertGreater(result.Mesh.CountFacets, 0)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        result.Placement = App.Placement(
            App.Vector(0.0, 15.0, 0.0),
            App.Rotation(),
        )
        self.shape.Placement = App.Placement(
            App.Vector(50.0, 0.0, 0.0),
            App.Rotation(),
        )
        self.document.recompute()
        self.assertTrue(result.isValid())
        self.assertGreater(
            min(point.Vector.x for point in result.Mesh.Points),
            49.0,
        )
        self.assertLess(
            max(point.Vector.x for point in result.Mesh.Points),
            61.0,
        )
        self.assertGreater(
            min(point.Vector.y for point in result.Mesh.Points),
            14.0,
        )
        self.assertAlmostEqual(result.Placement.Base.x, 0.0)
        self.assertAlmostEqual(result.Placement.Base.y, 15.0)
        self.assertAlmostEqual(result.Placement.Base.z, 0.0)

    def test_smoothing_keeps_exact_target_placement_and_segments(self):
        self._add_segment(self.mesh, (0, 1))
        self.mesh.Placement = App.Placement(
            App.Vector(11.0, 7.0, 3.0),
            App.Rotation(),
        )
        placement_before = self._placement_tuple(self.mesh)
        segments_before = self.mesh.Mesh.countSegments()
        points_before = self._mesh_points(self.mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.mesh)
        Gui.runCommand("Mesh_Smoothing", 0)
        self._process_events(5)

        other = self._new_document("VibeCADMeshSmoothingOther")
        other_mesh = other.addObject("Mesh::Feature", "PrimaryMesh")
        other_mesh.Mesh = _tetrahedron(50.0)
        other.recompute()
        other_points = self._mesh_points(other_mesh)
        App.setActiveDocument(other.Name)
        self._select(other_mesh)
        self._process_events(5)

        # Task controls belong to the launch document and are intentionally
        # hidden while another document is active. Return to that document
        # before accepting, without changing the captured target.
        App.setActiveDocument(self.document.Name)
        self._process_events(5)

        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        self._wait_for_mesh_job("mesh.modify.smooth.human")
        self.assertFalse(Gui.Control.activeDialog())

        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "Mesh::Smoothing")
        self.assertIs(result.Source, self.mesh)
        self.assertEqual(self._mesh_points(self.mesh), points_before)
        self.assertNotEqual(self._mesh_points(result), points_before)
        self.assertEqual(self._mesh_points(other_mesh), other_points)
        self.assertEqual(
            self._placement_tuple(result),
            placement_before,
        )
        self.assertEqual(
            result.Mesh.countSegments(),
            segments_before,
        )
        self.assertFalse(self.mesh.Visibility)
        self.assertTrue(result.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertEqual(other.UndoCount, 0)
        App.setActiveDocument(self.document.Name)

    def test_true_no_ops_create_no_objects_and_no_undo(self):
        self._select(self.mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_SplitComponents", 0)
        self._wait_for_mesh_job("mesh.segment.split_components.human")
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)

        Gui.runCommand("Mesh_HarmonizeNormals", 0)
        self._wait_for_mesh_job("mesh.modify.harmonize_normals.human")
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)

        self._accept_input_dialog(3, integer=True)
        Gui.runCommand("Mesh_FillupHoles", 0)
        self._wait_for_mesh_job("mesh.modify.fill_holes.human")
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)

        self._accept_input_dialog(1.0)
        Gui.runCommand("Mesh_Scale", 0)
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)

    def test_split_components_preserves_component_segment_metadata(self):
        combined = _tetrahedron()
        combined.addSegment([0, 1])
        combined.addMesh(_tetrahedron(30.0))
        combined.addSegment([4, 5])
        self.mesh.Mesh = combined
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.mesh)
        Gui.runCommand("Mesh_SplitComponents", 0)
        self._wait_for_mesh_job("mesh.segment.split_components.human")

        created = [obj for obj in self.document.Objects if obj not in objects_before]
        self.assertEqual(len(created), 3)
        controllers = [obj for obj in created if obj.TypeId == "Mesh::OutputGroup"]
        results = [obj for obj in created if obj.TypeId == "Mesh::FacetSubset"]
        self.assertEqual(len(controllers), 1)
        self.assertEqual(len(results), 2)
        controller = controllers[0]
        self.assertEqual(controller.OperationKind, "Split connected components")
        self.assertEqual(list(controller.Sources), [self.mesh])
        self.assertEqual(set(controller.Group), set(results))
        self.assertEqual(controller.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(controller.VibeCADTimelineReplacedInputs),
            [self.mesh],
        )
        for result in results:
            self.assertEqual(result.TypeId, "Mesh::FacetSubset")
            self.assertIs(result.Source, self.mesh)
            self.assertEqual(result.SelectionKind, "Connected component")
            self.assertEqual(len(result.FacetIndices), 4)
            self.assertEqual(
                result.AcceptedTopology.CountFacets,
                self.mesh.Mesh.CountFacets,
            )
            self.assertGreater(result.Mesh.CountFacets, 0)
            self.assertEqual(result.Mesh.countSegments(), 1)
            self.assertEqual(result.VibeCADTimelineRole, "resource")
            self.assertIs(result.VibeCADTimelineOwner, controller)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(controller, timeline.Operations)
        for result in results:
            self.assertIn(result, timeline.Operations)
        timeline_names = self._timeline_object_names()
        self.assertEqual(timeline_names.count(controller.Name), 1)
        self.assertTrue(all(result.Name not in timeline_names for result in results))
        self.assertFalse(self.mesh.Visibility)
        self.assertTrue(controller.Visibility)
        self.assertTrue(all(result.Visibility for result in results))
        self.assertEqual(controller.Mesh.CountFacets, 0)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        # Physical outputs remain independently hideable. Toggling the
        # semantic operation eye gates them as a group and restores each
        # child's own baseline instead of forcing all children visible.
        results[0].Visibility = False
        self._process_events()
        self.assertFalse(results[0].Visibility)
        self.assertTrue(results[1].Visibility)
        controller.Visibility = False
        self._process_events()
        self.assertFalse(controller.Visibility)
        self.assertTrue(all(not result.Visibility for result in results))
        controller.Visibility = True
        self._process_events()
        self.assertTrue(controller.Visibility)
        self.assertFalse(results[0].Visibility)
        self.assertTrue(results[1].Visibility)
        results[0].Visibility = True
        self._process_events()

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(10)
        self.assertTrue(controller.Suppressed)
        self.assertEqual(
            controller.Mesh.CountFacets,
            self.mesh.Mesh.CountFacets,
        )
        self.assertEqual(
            self._mesh_points(controller),
            self._mesh_points(self.mesh),
        )
        self.assertEqual(
            self._placement_tuple(controller),
            self._placement_tuple(self.mesh),
        )
        self.assertEqual(
            controller.Mesh.countSegments(),
            self.mesh.Mesh.countSegments(),
        )
        self.assertFalse(controller.Visibility)
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(all(not result.Visibility for result in results))

        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(10)
        self.assertFalse(controller.Suppressed)
        self.assertEqual(controller.Mesh.CountFacets, 0)
        self.assertTrue(controller.Visibility)
        self.assertTrue(all(result.Visibility for result in results))
        self.assertTrue(all(result.Mesh.CountFacets == 4 for result in results))
        self.assertFalse(self.mesh.Visibility)

        controller_name = controller.Name
        result_names = tuple(result.Name for result in results)
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "mesh-components.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            self.document = reopened
            self.mesh = reopened.getObject("PrimaryMesh")

            reopened_controller = reopened.getObject(controller_name)
            reopened_results = [
                reopened.getObject(result_name) for result_name in result_names
            ]
            reopened_timeline = reopened.getObject("VibeCADTimeline")
            self.assertIsNotNone(reopened_controller)
            self.assertTrue(all(reopened_results))
            self.assertEqual(list(reopened_controller.Sources), [self.mesh])
            self.assertEqual(
                list(reopened_controller.VibeCADTimelineReplacedInputs),
                [self.mesh],
            )
            self.assertEqual(
                set(reopened_controller.Group),
                set(reopened_results),
            )
            self.assertIn(
                reopened_controller,
                reopened_timeline.Operations,
            )
            for result in reopened_results:
                self.assertIs(
                    result.VibeCADTimelineOwner,
                    reopened_controller,
                )
                self.assertEqual(result.VibeCADTimelineRole, "resource")
                self.assertIn(result, reopened_timeline.Operations)
                self.assertIs(result.Source, self.mesh)
            reopened.recompute()
            self.assertTrue(reopened_controller.isValid())
            self.assertTrue(all(result.isValid() for result in reopened_results))

            self.mesh.Placement = App.Placement(
                App.Vector(42.0, -3.0, 8.0),
                App.Rotation(),
            )
            reopened.recompute()
            self.assertTrue(
                all(result.isValid() for result in reopened_results)
            )
            self.assertTrue(
                all(
                    self._placement_tuple(result)
                    == self._placement_tuple(self.mesh)
                    for result in reopened_results
                )
            )

            accepted_source = Mesh.Mesh(self.mesh.Mesh)
            changed_topology = Mesh.Mesh(self.mesh.Mesh)
            changed_topology.removeFacets([0])
            self.mesh.Mesh = changed_topology
            reopened.recompute()
            self.assertTrue(
                all(not result.isValid() for result in reopened_results)
            )
            self.assertTrue(
                all(result.Mesh.CountFacets == 0 for result in reopened_results)
            )
            self.mesh.Mesh = accepted_source
            reopened.recompute()
            self.assertTrue(
                all(not result.isValid() for result in reopened_results)
            )
            self.assertTrue(
                all(result.Mesh.CountFacets == 0 for result in reopened_results)
            )
            self.assertTrue(
                all(result.AcceptedSourceStale for result in reopened_results)
            )

            reopened_results[0].Visibility = False
            reopened_controller.Visibility = False
            self._process_events()
            self.assertTrue(all(not result.Visibility for result in reopened_results))
            reopened_controller.Visibility = True
            self._process_events()
            self.assertFalse(reopened_results[0].Visibility)
            self.assertTrue(reopened_results[1].Visibility)

    def test_scale_preserves_placement_and_valid_segments(self):
        self._add_segment(self.mesh, (0, 1))
        self.mesh.Placement = App.Placement(
            App.Vector(9.0, -4.0, 2.0),
            App.Rotation(),
        )
        placement_before = self._placement_tuple(self.mesh)
        segments_before = self.mesh.Mesh.countSegments()
        points_before = self._mesh_points(self.mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.mesh)
        self._accept_input_dialog(2.0)
        Gui.runCommand("Mesh_Scale", 0)
        self._wait_for_mesh_job("mesh.modify.scale.human")

        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "Mesh::Scale")
        self.assertIs(result.Source, self.mesh)
        self.assertEqual(self._mesh_points(self.mesh), points_before)
        points_after = self._mesh_points(result)
        self.assertEqual(len(points_after), len(points_before))
        for before, after in zip(points_before, points_after):
            for original, scaled in zip(before, after):
                self.assertAlmostEqual(scaled, original * 2.0, 6)
        self.assertEqual(
            self._placement_tuple(result),
            placement_before,
        )
        self.assertEqual(
            result.Mesh.countSegments(),
            segments_before,
        )
        self.assertFalse(self.mesh.Visibility)
        self.assertTrue(result.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

    def test_scale_marker_restores_the_exact_replaced_source(self):
        source_points = self._mesh_points(self.mesh)
        self._select(self.mesh)
        self._accept_input_dialog(2.0)
        Gui.runCommand("Mesh_Scale", 0)
        self._wait_for_mesh_job("mesh.modify.scale.human")
        result = next(
            obj for obj in self.document.Objects if obj.TypeId == "Mesh::Scale"
        )
        scaled_points = self._mesh_points(result)
        self.assertNotEqual(scaled_points, source_points)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(result, timeline.Operations)
        self.assertEqual(timeline.Position, len(timeline.Operations))
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            [self.mesh],
        )

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(10)
        self.assertTrue(result.Suppressed)
        self.assertEqual(self._mesh_points(result), source_points)
        self.assertFalse(result.Visibility)
        self.assertTrue(self.mesh.Visibility)

        while timeline.Position > 0:
            previous_position = int(timeline.Position)
            self._timeline_button("VibeCADFeatureTimelinePrevious").click()
            self._process_events(10)
            self.assertLess(timeline.Position, previous_position)
        self.assertEqual(timeline.Position, 0)
        self.assertTrue(result.Suppressed)
        self.assertFalse(
            result.Visibility,
            "A bypass result must not render before its linked source exists "
            "at the document-history boundary",
        )

        self._timeline_button("VibeCADFeatureTimelineNext").click()
        self._process_events(10)
        self.assertEqual(
            timeline.Position,
            list(timeline.Operations).index(self.mesh) + 1,
        )
        self.assertTrue(result.Suppressed)
        self.assertEqual(self._mesh_points(result), source_points)
        self.assertFalse(result.Visibility)
        self.assertTrue(
            self.mesh.Visibility,
            "A future replacement must present its exact source once that "
            "source is active.",
        )

        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(10)
        self.assertFalse(result.Suppressed)
        self.assertEqual(self._mesh_points(result), scaled_points)
        self.assertTrue(result.Visibility)
        self.assertFalse(self.mesh.Visibility)

    def test_every_new_mesh_operation_family_round_trips_through_marker(self):
        marker_document = self._new_document("MeshOperationMarkerFamilies")
        App.setActiveDocument(marker_document.Name)
        self._process_events()
        cases = []

        def source(name, mesh):
            feature = marker_document.addObject("Mesh::Feature", name)
            feature.Mesh = mesh
            return feature

        def record(label, operation, linked_source):
            marker_document.recompute()
            self.assertTrue(
                operation.isValid(),
                f"{label}: {operation.getStatusString()}",
            )
            linked_source.Visibility = False
            operation.Visibility = True
            self._process_events()
            cases.append(
                (
                    label,
                    operation,
                    linked_source,
                    operation.Mesh.CountFacets,
                    self._mesh_points(operation),
                )
            )

        smoothing_source = source(
            "MarkerSmoothingSource",
            _planar_grid(columns=3, rows=3),
        )
        smoothing = marker_document.addObject(
            "Mesh::Smoothing",
            "MarkerSmoothing",
        )
        smoothing.Source = smoothing_source
        smoothing.Method = "Laplace"
        smoothing.Iterations = 1
        smoothing.Lambda = 0.35
        record("smoothing", smoothing, smoothing_source)

        decimation_source = source(
            "MarkerDecimationSource",
            Mesh.createSphere(5.0, 20),
        )
        decimation = marker_document.addObject(
            "Mesh::Decimation",
            "MarkerDecimation",
        )
        decimation.Source = decimation_source
        decimation.Reduction = 30
        record("decimation", decimation, decimation_source)

        scale_source = source("MarkerScaleSource", _tetrahedron())
        scale = marker_document.addObject("Mesh::Scale", "MarkerScale")
        scale.Source = scale_source
        scale.Factor = 1.75
        record("scale", scale, scale_source)

        plane_source = source("MarkerPlaneSource", _tetrahedron())
        plane = marker_document.addObject("Part::Plane", "MarkerPlane")
        plane.Placement = App.Placement(
            App.Vector(0.0, 0.0, 2.0),
            App.Rotation(),
        )
        plane_trim = marker_document.addObject(
            "Mesh::TrimByPlane",
            "MarkerPlaneTrim",
        )
        plane_trim.Source = plane_source
        plane_trim.Plane = plane
        plane_trim.Side = "Below"
        record("plane trim", plane_trim, plane_source)

        add_source = source("MarkerAddFacetSource", _open_tetrahedron())
        add_facet = marker_document.addObject(
            "Mesh::FacetEdit",
            "MarkerAddFacet",
        )
        add_facet.Source = add_source
        add_facet.Action = "Add Triangle"
        add_facet.Indices = [2, 1, 3]
        add_facet.AcceptedSource = add_source.Mesh
        record("facet add", add_facet, add_source)

        remove_source = source("MarkerRemoveFacetSource", _tetrahedron())
        remove_facet = marker_document.addObject(
            "Mesh::FacetEdit",
            "MarkerRemoveFacet",
        )
        remove_facet.Source = remove_source
        remove_facet.Action = "Remove Facets"
        remove_facet.Indices = [0]
        remove_facet.AcceptedSource = remove_source.Mesh
        record("facet remove", remove_facet, remove_source)

        fill_source = source("MarkerFillHoleSource", _open_tetrahedron())
        fill_hole = marker_document.addObject(
            "Mesh::FacetEdit",
            "MarkerFillHole",
        )
        fill_hole.Source = fill_source
        fill_hole.Action = "Fill Hole"
        fill_hole.SeedFacet = 0
        fill_hole.Level = 2
        fill_hole.AcceptedSource = fill_source.Mesh
        record("facet fill", fill_hole, fill_source)

        subset_source_mesh = _tetrahedron()
        subset_source_mesh.addMesh(_tetrahedron(20.0))
        subset_source = source(
            "MarkerFacetSubsetSource",
            subset_source_mesh,
        )
        facet_subset = marker_document.addObject(
            "Mesh::FacetSubset",
            "MarkerFacetSubset",
        )
        facet_subset.Source = subset_source
        facet_subset.FacetIndices = [0, 1, 2, 3]
        facet_subset.AcceptedTopology = subset_source.Mesh
        facet_subset.SelectionKind = "Connected component"
        record("facet subset", facet_subset, subset_source)

        gmsh_source = source(
            "MarkerStoredGmshSource",
            Mesh.createSphere(5.0, 10),
        )
        gmsh_result = marker_document.addObject(
            "Mesh::StoredEdit",
            "MarkerStoredGmsh",
        )
        gmsh_result.Source = gmsh_source
        gmsh_result.AcceptedSource = gmsh_source.Mesh
        gmsh_result.AcceptedResult = Mesh.createSphere(5.0, 16)
        gmsh_result.EditKind = "Gmsh remesh"
        record("stored Gmsh remesh", gmsh_result, gmsh_source)

        polygon_source = source(
            "MarkerStoredPolygonSource",
            _tetrahedron(),
        )
        polygon_result_mesh = Mesh.Mesh(polygon_source.Mesh)
        polygon_result_mesh.removeFacets([0])
        polygon_result = marker_document.addObject(
            "Mesh::StoredEdit",
            "MarkerStoredPolygon",
        )
        polygon_result.Source = polygon_source
        polygon_result.AcceptedSource = polygon_source.Mesh
        polygon_result.AcceptedResult = polygon_result_mesh
        polygon_result.EditKind = "Polygon trim inside"
        record("stored polygon trim", polygon_result, polygon_source)

        empty_source = source("MarkerEmptyRemovalSource", _tetrahedron())
        empty_removal = marker_document.addObject(
            "Mesh::FacetEdit",
            "MarkerEmptyRemoval",
        )
        empty_removal.Source = empty_source
        empty_removal.Action = "Remove Facets"
        empty_removal.Indices = [0, 1, 2, 3]
        empty_removal.AcceptedSource = empty_source.Mesh
        record("empty remove-all", empty_removal, empty_source)
        self.assertEqual(empty_removal.Mesh.CountFacets, 0)

        timeline = marker_document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        operations = list(timeline.Operations)
        end_position = len(operations)
        for label, operation, linked_source, facets, points in cases:
            self.assertIn(operation, operations, label)
            operation_index = operations.index(operation)
            timeline.Position = operation_index + 1
            marker_document.recompute()
            self._process_events()
            self._timeline_button("VibeCADFeatureTimelinePrevious").click()
            self._process_events()
            self.assertEqual(timeline.Position, operation_index, label)
            self.assertTrue(operation.Suppressed, label)
            self.assertTrue(operation.isValid(), label)
            self.assertEqual(
                operation.Mesh.CountFacets,
                linked_source.Mesh.CountFacets,
                label,
            )
            self.assertEqual(
                self._mesh_points(operation),
                self._mesh_points(linked_source),
                label,
            )

            self._timeline_button("VibeCADFeatureTimelineEnd").click()
            self._process_events()
            self.assertEqual(timeline.Position, end_position, label)
            self.assertFalse(operation.Suppressed, label)
            self.assertTrue(operation.isValid(), label)
            self.assertEqual(operation.Mesh.CountFacets, facets, label)
            self.assertEqual(self._mesh_points(operation), points, label)

        App.setActiveDocument(self.document.Name)

    def test_polygon_split_result_groups_round_trip_marker_and_reopen(self):
        split_document = self._new_document("MeshPolygonSplitGroups")
        App.setActiveDocument(split_document.Name)
        self._process_events()

        def mark_resource(resource, owner):
            resource.addProperty(
                "App::PropertyLinkHidden",
                "VibeCADTimelineOwner",
                "Timeline",
            )
            resource.VibeCADTimelineOwner = owner
            resource.addProperty(
                "App::PropertyString",
                "VibeCADTimelineRole",
                "Timeline",
            )
            resource.VibeCADTimelineRole = "resource"
            resource.setEditorMode("VibeCADTimelineOwner", 2)
            resource.setEditorMode("VibeCADTimelineRole", 2)

        def output_group(name, label, kind, source):
            controller = split_document.addObject(
                "Mesh::OutputGroup",
                name,
            )
            controller.Label = label
            controller.OperationKind = kind
            controller.Sources = [source]
            return controller

        cut_source = split_document.addObject(
            "Mesh::Feature",
            "PolygonCutSource",
        )
        cut_source.Mesh = _tetrahedron()
        cut_primary = split_document.addObject(
            "Mesh::FacetEdit",
            "PolygonCutPrimary",
        )
        cut_primary.Source = cut_source
        cut_primary.Action = "Remove Facets"
        cut_primary.Indices = [0, 1]
        cut_primary.AcceptedSource = cut_source.Mesh
        cut_resource = split_document.addObject(
            "Mesh::FacetEdit",
            "PolygonCutResource",
        )
        cut_resource.Source = cut_source
        cut_resource.Action = "Remove Facets"
        cut_resource.Indices = [2, 3]
        cut_resource.AcceptedSource = cut_source.Mesh
        cut_controller = output_group(
            "PolygonCut",
            "Cut Mesh by Polygon",
            "Polygon cut",
            cut_source,
        )
        mark_resource(cut_primary, cut_controller)
        mark_resource(cut_resource, cut_controller)
        cut_controller.Group = [cut_primary, cut_resource]

        trim_source = split_document.addObject(
            "Mesh::Feature",
            "PolygonTrimSource",
        )
        trim_source.Mesh = _tetrahedron(20.0)
        outside = Mesh.Mesh(trim_source.Mesh)
        outside.removeFacets([0, 1])
        inside = Mesh.Mesh(trim_source.Mesh)
        inside.removeFacets([2, 3])
        trim_primary = split_document.addObject(
            "Mesh::StoredEdit",
            "PolygonTrimPrimary",
        )
        trim_primary.Source = trim_source
        trim_primary.AcceptedSource = trim_source.Mesh
        trim_primary.AcceptedResult = outside
        trim_primary.EditKind = "Polygon trim outside"
        trim_resource = split_document.addObject(
            "Mesh::StoredEdit",
            "PolygonTrimResource",
        )
        trim_resource.Source = trim_source
        trim_resource.AcceptedSource = trim_source.Mesh
        trim_resource.AcceptedResult = inside
        trim_resource.EditKind = "Polygon trim inside"
        trim_controller = output_group(
            "PolygonTrim",
            "Trim Mesh by Polygon",
            "Polygon trim",
            trim_source,
        )
        mark_resource(trim_primary, trim_controller)
        mark_resource(trim_resource, trim_controller)
        trim_controller.Group = [trim_primary, trim_resource]

        split_document.recompute()
        for source, controller, primary, resource in (
            (
                cut_source,
                cut_controller,
                cut_primary,
                cut_resource,
            ),
            (
                trim_source,
                trim_controller,
                trim_primary,
                trim_resource,
            ),
        ):
            self.assertTrue(
                controller.isValid(),
                controller.getStatusString(),
            )
            self.assertTrue(primary.isValid(), primary.getStatusString())
            self.assertTrue(resource.isValid(), resource.getStatusString())
            source.Visibility = False
            controller.Visibility = True
            primary.Visibility = True
            resource.Visibility = True
        self._process_events()

        timeline = split_document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        for controller, primary, resource in (
            (cut_controller, cut_primary, cut_resource),
            (trim_controller, trim_primary, trim_resource),
        ):
            self.assertIn(controller, operations)
            self.assertIn(primary, operations)
            self.assertIn(resource, operations)
            self.assertEqual(
                set(controller.Group),
                {primary, resource},
            )
            self.assertIs(primary.VibeCADTimelineOwner, controller)
            self.assertIs(resource.VibeCADTimelineOwner, controller)
        timeline_names = self._timeline_object_names()
        self.assertEqual(timeline_names.count(cut_controller.Name), 1)
        self.assertEqual(timeline_names.count(trim_controller.Name), 1)
        for resource in (
            cut_primary,
            cut_resource,
            trim_primary,
            trim_resource,
        ):
            self.assertNotIn(resource.Name, timeline_names)

        # The group is a timeline controller, not a second editor for one of
        # the physical result meshes.
        self.assertFalse(
            Gui.activeDocument().setEdit(cut_controller.Name),
        )
        self.assertIsNone(Gui.activeDocument().getInEdit())

        for label, source, controller, primary, resource in (
            (
                "polygon cut",
                cut_source,
                cut_controller,
                cut_primary,
                cut_resource,
            ),
            (
                "polygon trim",
                trim_source,
                trim_controller,
                trim_primary,
                trim_resource,
            ),
        ):
            output_facets = (
                primary.Mesh.CountFacets,
                resource.Mesh.CountFacets,
            )
            primary.Visibility = False
            controller.Visibility = False
            self._process_events()
            self.assertFalse(primary.Visibility, label)
            self.assertFalse(resource.Visibility, label)
            controller.Visibility = True
            self._process_events()
            self.assertFalse(primary.Visibility, label)
            self.assertTrue(resource.Visibility, label)
            primary.Visibility = True
            self._process_events()

            operation_index = operations.index(controller)
            timeline.Position = operation_index + 1
            split_document.recompute()
            self._process_events()
            self._timeline_button("VibeCADFeatureTimelinePrevious").click()
            self._process_events()
            self.assertLessEqual(
                timeline.Position,
                operation_index,
                label,
            )
            self.assertTrue(controller.Suppressed, label)
            self.assertEqual(
                controller.Mesh.CountFacets,
                source.Mesh.CountFacets,
                label,
            )
            self.assertEqual(
                self._mesh_points(controller),
                self._mesh_points(source),
                label,
            )
            self.assertEqual(
                self._placement_tuple(controller),
                self._placement_tuple(source),
                label,
            )
            self.assertTrue(controller.Visibility, label)
            self.assertFalse(primary.Visibility, label)
            self.assertFalse(resource.Visibility, label)

            self._timeline_button("VibeCADFeatureTimelineEnd").click()
            self._process_events()
            self.assertFalse(controller.Suppressed, label)
            self.assertEqual(controller.Mesh.CountFacets, 0, label)
            self.assertEqual(
                (
                    primary.Mesh.CountFacets,
                    resource.Mesh.CountFacets,
                ),
                output_facets,
                label,
            )
            self.assertTrue(controller.Visibility, label)
            self.assertTrue(primary.Visibility, label)
            self.assertTrue(resource.Visibility, label)
            self.assertFalse(source.Visibility, label)

        pairs = (
            (
                cut_controller.Name,
                cut_primary.Name,
                cut_resource.Name,
                cut_source.Name,
            ),
            (
                trim_controller.Name,
                trim_primary.Name,
                trim_resource.Name,
                trim_source.Name,
            ),
        )
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "polygon-splits.FCStd"
            split_document.saveAs(str(path))
            document_name = split_document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            reopened_timeline = reopened.getObject("VibeCADTimeline")
            for (
                controller_name,
                primary_name,
                resource_name,
                source_name,
            ) in pairs:
                controller = reopened.getObject(controller_name)
                primary = reopened.getObject(primary_name)
                resource = reopened.getObject(resource_name)
                source = reopened.getObject(source_name)
                self.assertEqual(
                    list(controller.Sources),
                    [source],
                )
                self.assertEqual(
                    set(controller.Group),
                    {primary, resource},
                )
                self.assertIs(primary.VibeCADTimelineOwner, controller)
                self.assertIs(resource.VibeCADTimelineOwner, controller)
                self.assertEqual(primary.VibeCADTimelineRole, "resource")
                self.assertEqual(resource.VibeCADTimelineRole, "resource")
                self.assertIn(controller, reopened_timeline.Operations)
                self.assertIn(primary, reopened_timeline.Operations)
                self.assertIn(resource, reopened_timeline.Operations)
                self.assertIs(primary.Source, source)
                self.assertIs(resource.Source, source)
                self.assertTrue(controller.isValid())
                self.assertTrue(primary.isValid())
                self.assertTrue(resource.isValid())

        App.setActiveDocument(self.document.Name)

    def test_indexed_edits_follow_geometry_but_reject_changed_topology(self):
        smoothing = self.document.addObject(
            "Mesh::Smoothing",
            "SelectedPointSmoothing",
        )
        smoothing.Source = self.mesh
        smoothing.Method = "Laplace"
        smoothing.PointIndices = [0]
        smoothing.SelectionSource = self.mesh.Mesh
        indexed = self.document.addObject(
            "Mesh::FacetEdit",
            "IndexedRemoval",
        )
        indexed.Source = self.mesh
        indexed.Action = "Remove Facets"
        indexed.Indices = [0]
        indexed.AcceptedSource = self.mesh.Mesh
        stored = self.document.addObject(
            "Mesh::StoredEdit",
            "StoredPolygonTrim",
        )
        accepted = Mesh.Mesh(self.mesh.Mesh)
        accepted.removeFacets([0])
        stored.Source = self.mesh
        stored.AcceptedSource = self.mesh.Mesh
        stored.AcceptedResult = accepted
        stored.EditKind = "Polygon trim inside"
        self.document.recompute()
        self.assertTrue(smoothing.isValid(), smoothing.getStatusString())
        self.assertTrue(indexed.isValid(), indexed.getStatusString())
        self.assertTrue(stored.isValid(), stored.getStatusString())
        self.assertEqual(indexed.Mesh.CountFacets, 3)
        self.assertEqual(stored.Mesh.CountFacets, 3)

        changed = Mesh.Mesh(self.mesh.Mesh)
        changed.translate(0.25, 0.0, 0.0)
        self.mesh.Mesh = changed
        self.document.recompute()
        self.assertTrue(smoothing.isValid(), smoothing.getStatusString())
        self.assertTrue(indexed.isValid(), indexed.getStatusString())
        self.assertFalse(stored.isValid())
        self.assertIn("source mesh changed", stored.getStatusString())
        self.assertEqual(indexed.Mesh.CountFacets, 3)
        self.assertAlmostEqual(
            indexed.Mesh.BoundBox.XMin,
            0.25,
            places=6,
        )
        self.assertEqual(stored.Mesh.CountFacets, 0)

        changed.removeFacets([0])
        self.mesh.Mesh = changed
        self.document.recompute()
        self.assertFalse(smoothing.isValid())
        self.assertFalse(indexed.isValid())
        self.assertFalse(stored.isValid())
        self.assertIn("topology changed", smoothing.getStatusString())
        self.assertIn("topology changed", indexed.getStatusString())
        self.assertIn("source mesh changed", stored.getStatusString())
        self.assertEqual(smoothing.Mesh.CountFacets, 0)
        self.assertEqual(indexed.Mesh.CountFacets, 0)
        self.assertEqual(stored.Mesh.CountFacets, 0)

        smoothing.Suppressed = True
        indexed.Suppressed = True
        stored.Suppressed = True
        self.document.recompute()
        self.assertTrue(smoothing.isValid(), smoothing.getStatusString())
        self.assertTrue(indexed.isValid(), indexed.getStatusString())
        self.assertTrue(stored.isValid(), stored.getStatusString())
        self.assertEqual(
            smoothing.Mesh.CountFacets,
            self.mesh.Mesh.CountFacets,
        )
        self.assertEqual(
            indexed.Mesh.CountFacets,
            self.mesh.Mesh.CountFacets,
        )
        self.assertEqual(
            stored.Mesh.CountFacets,
            self.mesh.Mesh.CountFacets,
        )

    def test_new_mesh_operations_survive_save_reopen_with_links_and_parameters(
        self,
    ):
        persistence_document = self._new_document(
            "MeshOperationPersistence",
        )
        source = persistence_document.addObject(
            "Mesh::Feature",
            "OperationSource",
        )
        source.Mesh = Mesh.createSphere(5.0, 20)
        scale = persistence_document.addObject(
            "Mesh::Scale",
            "PersistentScale",
        )
        scale.Source = source
        scale.Factor = 1.5
        decimation = persistence_document.addObject(
            "Mesh::Decimation",
            "PersistentDecimation",
        )
        decimation.Source = scale
        decimation.Reduction = 25
        persistence_document.recompute()
        self.assertTrue(scale.isValid(), scale.getStatusString())
        self.assertTrue(
            decimation.isValid(),
            decimation.getStatusString(),
        )

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "mesh-operations.FCStd"
            persistence_document.saveAs(str(path))
            document_name = persistence_document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)

            reopened_source = reopened.getObject("OperationSource")
            reopened_scale = reopened.getObject("PersistentScale")
            reopened_decimation = reopened.getObject("PersistentDecimation")
            self.assertIs(reopened_scale.Source, reopened_source)
            self.assertAlmostEqual(reopened_scale.Factor, 1.5)
            self.assertIs(
                reopened_decimation.Source,
                reopened_scale,
            )
            self.assertAlmostEqual(
                reopened_decimation.Reduction,
                25.0,
            )
            before = reopened_decimation.Mesh.CountFacets
            reopened_scale.Factor = 2.0
            reopened.recompute()
            self.assertTrue(
                reopened_decimation.isValid(),
                reopened_decimation.getStatusString(),
            )
            self.assertEqual(
                reopened_decimation.Mesh.CountFacets,
                before,
            )

    def test_decimation_replaces_topology_without_moving_feature(self):
        self.mesh.Mesh = Mesh.createSphere(5.0, 20)
        self._add_segment(self.mesh, (0, 1, 2))
        self.mesh.Placement = App.Placement(
            App.Vector(8.0, 6.0, 4.0),
            App.Rotation(),
        )
        placement_before = self._placement_tuple(self.mesh)
        facets_before = self.mesh.Mesh.CountFacets
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.mesh)
        Gui.runCommand("Mesh_Decimating", 0)
        self._process_events(5)
        reduction = Gui.getMainWindow().findChild(
            QtGui.QSlider,
            "sliderReduction",
        )
        self.assertIsNotNone(reduction)
        reduction.setValue(50)
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        self._wait_for_mesh_job("mesh.modify.decimate.human")

        self.assertFalse(Gui.Control.activeDialog())
        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "Mesh::Decimation")
        self.assertIs(result.Source, self.mesh)
        self.assertEqual(self.mesh.Mesh.CountFacets, facets_before)
        self.assertGreater(result.Mesh.CountFacets, 0)
        self.assertLess(result.Mesh.CountFacets, facets_before)
        self.assertEqual(
            self._placement_tuple(result),
            placement_before,
        )
        self.assertEqual(result.Mesh.countSegments(), 0)
        self.assertFalse(self.mesh.Visibility)
        self.assertTrue(result.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

    def test_vertex_curvature_creates_one_valid_result(self):
        self._select(self.mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        started = time.monotonic()
        Gui.runCommand("Mesh_VertexCurvature", 0)
        self.assertLess(time.monotonic() - started, 0.25)
        progress = [
            widget
            for widget in QtGui.QApplication.topLevelWidgets()
            if isinstance(widget, QtGui.QProgressDialog)
            and widget.isVisible()
            and widget.windowTitle() == "Mesh Curvature"
        ]
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0].windowModality(), QtCore.Qt.NonModal)
        self._wait_for_mesh_curvature()
        created = [obj for obj in self.document.Objects if obj not in objects_before]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].TypeId, "Mesh::Curvature")
        self.assertIs(created[0].Source, self.mesh)
        self.assertFalse(created[0].UpdateFromSource)
        self.assertEqual(created[0].VibeCADTimelineRole, "operation")
        self.assertFalse(any(obj.TypeId == "Mesh::OutputGroup" for obj in created))
        self.assertEqual(
            len(created[0].CurvInfo),
            self.mesh.Mesh.CountPoints,
        )
        self.assertEqual(self.document.UndoCount, undo_before + 1)

    def test_vertex_curvature_batch_is_one_source_preserving_history_step(self):
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.mesh, self.second_mesh)
        Gui.runCommand("Mesh_VertexCurvature", 0)
        self._wait_for_mesh_curvature()

        created = [obj for obj in self.document.Objects if obj not in objects_before]
        controller, results = self._assert_source_preserving_multi_result(
            created,
            (self.mesh, self.second_mesh),
            "Mesh::Curvature",
            "Calculate mesh curvature",
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.Visibility for result in results))
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(10)
        self.assertTrue(controller.Suppressed)
        self.assertEqual(controller.Mesh.CountFacets, 0)
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(self.second_mesh.Visibility)
        self.assertTrue(all(not result.Visibility for result in results))

        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(10)
        self.assertFalse(controller.Suppressed)
        self.assertEqual(controller.Mesh.CountFacets, 0)
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(self.second_mesh.Visibility)
        self.assertTrue(all(result.Visibility for result in results))

    def test_shape_from_mesh_creates_one_valid_shape_and_one_undo(self):
        self._select(self.mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._accept_modal_dialog("MeshPart_ShapeFromMesh")
        Gui.runCommand("MeshPart_ShapeFromMesh", 0)
        self._wait_for_mesh_conversion()

        created = [obj for obj in self.document.Objects if obj not in objects_before]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "MeshPart::ShapeFromMesh")
        self.assertIs(result.Source, self.mesh)
        self.assertAlmostEqual(result.Tolerance, 0.1)
        self.assertFalse(result.SewShape)
        self.assertFalse(result.MakeSolid)
        self.assertFalse(result.UpdateFromSource)
        self.assertFalse(result.Shape.isNull())
        self.assertTrue(result.Shape.isValid())
        self.assertFalse(result.Visibility)
        self.assertTrue(self.mesh.Visibility)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertFalse(any(obj.TypeId == "Mesh::OutputGroup" for obj in created))
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.mesh.Mesh = _tetrahedron(40.0)
        self.document.recompute()
        self.assertTrue(result.isValid())
        self.assertLess(result.Shape.BoundBox.XMin, 1.0)

    def test_shape_from_mesh_builds_one_valid_solid_when_requested(self):
        self.mesh.Placement = App.Placement(
            App.Vector(12.0, 18.0, 24.0),
            App.Rotation(),
        )
        self.document.recompute()
        self._select(self.mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._accept_modal_dialog(
            "MeshPart_ShapeFromMesh",
            checked_texts=("Build solid volumes",),
        )
        Gui.runCommand("MeshPart_ShapeFromMesh", 0)
        self._wait_for_mesh_conversion()

        created = [obj for obj in self.document.Objects if obj not in objects_before]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "MeshPart::ShapeFromMesh")
        self.assertIs(result.Source, self.mesh)
        self.assertTrue(result.SewShape)
        self.assertTrue(result.MakeSolid)
        self.assertFalse(result.UpdateFromSource)
        self.assertEqual(result.Shape.ShapeType, "Solid")
        self.assertEqual(len(result.Shape.Solids), 1)
        self.assertAlmostEqual(abs(result.Shape.Volume), 56.0, delta=1.0e-6)
        self.assertAlmostEqual(result.Shape.BoundBox.XMin, 12.0, delta=1.0e-7)
        self.assertAlmostEqual(result.Shape.BoundBox.YMin, 18.0, delta=1.0e-7)
        self.assertAlmostEqual(result.Shape.BoundBox.ZMin, 24.0, delta=1.0e-7)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

    def test_shape_from_mesh_batch_is_one_durable_history_step(self):
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.mesh, self.second_mesh)
        self._accept_modal_dialog("MeshPart_ShapeFromMesh")
        Gui.runCommand("MeshPart_ShapeFromMesh", 0)
        self._wait_for_mesh_conversion()

        created = [obj for obj in self.document.Objects if obj not in objects_before]
        controller, results = self._assert_source_preserving_multi_result(
            created,
            (self.mesh, self.second_mesh),
            "MeshPart::ShapeFromMesh",
            "Convert mesh to shape",
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(
            {result.Source for result in results},
            {self.mesh, self.second_mesh},
        )
        self.assertTrue(
            all(
                not result.Shape.isNull() and result.Shape.isValid()
                for result in results
            )
        )
        self.assertTrue(all(not result.Visibility for result in results))
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(self.second_mesh.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(10)
        self.assertTrue(controller.Suppressed)
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(self.second_mesh.Visibility)
        self.assertTrue(all(not result.Visibility for result in results))

        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(10)
        self.assertFalse(controller.Suppressed)
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(self.second_mesh.Visibility)
        self.assertTrue(all(not result.Visibility for result in results))

        controller_name = controller.Name
        result_names = tuple(result.Name for result in results)
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "shape-from-mesh.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            self.document = reopened
            self.mesh = reopened.getObject("PrimaryMesh")
            self.second_mesh = reopened.getObject("SecondaryMesh")

            reopened_controller = reopened.getObject(controller_name)
            reopened_results = [
                reopened.getObject(result_name) for result_name in result_names
            ]
            self.assertIsNotNone(reopened_controller)
            self.assertTrue(all(reopened_results))
            self.assertEqual(
                list(reopened_controller.Sources),
                [self.mesh, self.second_mesh],
            )
            self.assertEqual(
                set(reopened_controller.Group),
                set(reopened_results),
            )
            self.assertEqual(
                reopened_controller.TypeId,
                "Mesh::OutputGroup",
            )
            self.assertEqual(reopened_controller.InputMode, "Source preserving")
            self.assertEqual(reopened_controller.Mesh.CountFacets, 0)
            self.assertNotIn(
                "VibeCADTimelineEditor",
                reopened_controller.PropertiesList,
            )
            for result in reopened_results:
                self.assertIs(
                    result.VibeCADTimelineOwner,
                    reopened_controller,
                )
                self.assertEqual(result.VibeCADTimelineRole, "resource")
                self.assertIn(
                    result.Source,
                    (self.mesh, self.second_mesh),
                )
                self.assertFalse(result.Shape.isNull())
                self.assertTrue(result.Shape.isValid())
                self.assertFalse(result.Visibility)
            reopened.recompute()
            self.assertTrue(reopened_controller.isValid())

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(reopened_controller)
            Gui.runCommand("Std_Delete", 0)
            self._process_events(10)
            self.assertIsNone(reopened.getObject(controller_name))
            self.assertTrue(
                all(
                    reopened.getObject(result_name) is None
                    for result_name in result_names
                )
            )
            self.assertTrue(self.mesh.Visibility)
            self.assertTrue(self.second_mesh.Visibility)

            reopened.undo()
            self._process_events(10)
            restored_controller = reopened.getObject(controller_name)
            restored_results = [
                reopened.getObject(result_name) for result_name in result_names
            ]
            self.assertIsNotNone(restored_controller)
            self.assertTrue(all(restored_results))
            self.assertEqual(
                list(restored_controller.Sources),
                [self.mesh, self.second_mesh],
            )
            self.assertEqual(
                set(restored_controller.Group),
                set(restored_results),
            )
            for result in restored_results:
                self.assertIs(
                    result.VibeCADTimelineOwner,
                    restored_controller,
                )

    def test_hidden_meshes_do_not_start_viewport_editors(self):
        self.mesh.ViewObject.Visibility = False
        self.second_mesh.ViewObject.Visibility = False
        self.curvature.ViewObject.Visibility = False
        self._select(self.mesh)
        self._process_events(5)

        for command_name in (
            "Mesh_AddFacet",
            "Mesh_PolyCut",
            "Mesh_PolyTrim",
            "Mesh_FillInteractiveHole",
            "Mesh_RemoveComponents",
            "Mesh_EvaluateFacet",
            "Mesh_CurvatureInfo",
            "MeshPart_CurveOnMesh",
        ):
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )

    def test_hidden_member_rejects_entire_viewport_batch(self):
        self.second_mesh.ViewObject.Visibility = False
        self._select(self.mesh, self.second_mesh)
        for command_name in (
            "Mesh_PolyCut",
            "Mesh_PolyTrim",
        ):
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )

    def test_empty_mesh_is_not_a_valid_tool_target(self):
        self.mesh.Mesh = Mesh.Mesh()
        self.document.recompute()
        self._select(self.mesh)
        for command_name in (
            "Mesh_AddFacet",
            "Mesh_Smoothing",
            "Mesh_Decimating",
            "Mesh_Segmentation",
            "Mesh_SegmentationBestFit",
            "MeshPart_ShapeFromMesh",
        ):
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )

    def test_smoothing_rejects_if_any_captured_target_is_deleted(self):
        self._select(self.mesh, self.second_mesh)
        points_before = self._mesh_points(self.mesh)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_Smoothing", 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())

        self.document.removeObject(self.second_mesh.Name)
        self.document.recompute()
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        self._process_events(8)

        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(self._mesh_points(self.mesh), points_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self._cancel_task("Mesh_Smoothing")

    def test_add_facet_keeps_exact_target_and_survives_its_deletion(self):
        self._select(self.mesh)
        second_before = self._mesh_points(self.second_mesh)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_AddFacet", 0)
        self._process_events(5)
        self.assertFalse(Gui.isCommandActive("Mesh_AddFacet"))

        # Selection changes must never retarget a running viewport editor.
        self._select(self.second_mesh)
        self.document.removeObject(self.mesh.Name)
        self.document.recompute()
        self._process_events(8)

        self.assertEqual(
            self._mesh_points(self.second_mesh),
            second_before,
        )
        self.assertEqual(self.document.UndoCount, undo_before)
        self._select(self.second_mesh)
        self.assertTrue(Gui.isCommandActive("Mesh_AddFacet"))

    def test_curve_on_mesh_is_owned_by_its_launch_document(self):
        self._select(self.mesh)
        Gui.runCommand("MeshPart_CurveOnMesh", 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())

        start = Gui.getMainWindow().findChild(
            QtGui.QPushButton,
            "startButton",
        )
        self.assertIsNotNone(start)
        start.click()
        self._process_events(5)

        other = self._new_document("VibeCADCurveOther")
        other_mesh = other.addObject("Mesh::Feature", "OtherMesh")
        other_mesh.Mesh = _tetrahedron(50.0)
        other.recompute()
        other_before = self._mesh_points(other_mesh)
        App.setActiveDocument(other.Name)
        App.closeDocument(self.document.Name)
        self._process_events(10)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(self._mesh_points(other_mesh), other_before)
        self.assertEqual(other.UndoCount, 0)

    def test_trim_split_creates_two_nonempty_results_in_one_undo(self):
        self.plane.Placement = App.Placement(
            App.Vector(0.0, 0.0, 2.0),
            App.Rotation(),
        )
        self.document.recompute()
        self._select(self.mesh, self.plane)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._click_message_button("Split")
        Gui.runCommand("Mesh_TrimByPlane", 0)
        self._wait_for_mesh_cut(capability="mesh.cut.trim_by_plane.human")

        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 3)
        controllers = [obj for obj in created if obj.TypeId == "Mesh::OutputGroup"]
        results = [obj for obj in created if obj.TypeId == "Mesh::TrimByPlane"]
        self.assertEqual(len(controllers), 1)
        self.assertEqual(len(results), 2)
        controller = controllers[0]
        self.assertEqual(controller.OperationKind, "Plane split")
        self.assertEqual(list(controller.Sources), [self.mesh])
        self.assertEqual(set(controller.Group), set(results))
        self.assertEqual(controller.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(controller.VibeCADTimelineReplacedInputs),
            [self.mesh],
        )
        self.assertTrue(all(obj.TypeId == "Mesh::TrimByPlane" for obj in results))
        self.assertTrue(all(obj.Source is self.mesh for obj in results))
        self.assertTrue(all(obj.Plane is self.plane for obj in results))
        self.assertEqual({obj.Side for obj in results}, {"Below", "Above"})
        self.assertEqual(self.mesh.Mesh.CountFacets, 4)
        self.assertTrue(all(obj.Mesh.CountFacets > 0 for obj in results))
        self.assertFalse(self.mesh.Visibility)
        self.assertTrue(controller.Visibility)
        self.assertTrue(all(obj.Visibility for obj in results))
        self.assertEqual(controller.Mesh.CountFacets, 0)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIn(controller, timeline.Operations)
        for result in results:
            self.assertIn(result, timeline.Operations)
            self.assertEqual(result.VibeCADTimelineRole, "resource")
            self.assertIs(result.VibeCADTimelineOwner, controller)
        timeline_names = self._timeline_object_names()
        self.assertEqual(timeline_names.count(controller.Name), 1)
        self.assertTrue(all(result.Name not in timeline_names for result in results))
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        results[0].Visibility = False
        controller.Visibility = False
        self._process_events()
        self.assertTrue(all(not result.Visibility for result in results))
        controller.Visibility = True
        self._process_events()
        self.assertFalse(results[0].Visibility)
        self.assertTrue(results[1].Visibility)
        results[0].Visibility = True
        self._process_events()

        controller_name = controller.Name
        result_names = tuple(result.Name for result in results)
        self.document.undo()
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.document.redo()
        self._process_events(10)
        controller = self.document.getObject(controller_name)
        results = [self.document.getObject(result_name) for result_name in result_names]
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(controller)
        self.assertTrue(all(results))
        self.assertEqual(set(controller.Group), set(results))
        self.assertIn(controller, timeline.Operations)
        for result in results:
            self.assertIs(result.VibeCADTimelineOwner, controller)
            self.assertIn(result, timeline.Operations)
        self.assertEqual(
            list(controller.VibeCADTimelineReplacedInputs),
            [self.mesh],
        )

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(10)
        self.assertTrue(controller.Suppressed)
        self.assertEqual(
            controller.Mesh.CountFacets,
            self.mesh.Mesh.CountFacets,
        )
        self.assertEqual(
            self._mesh_points(controller),
            self._mesh_points(self.mesh),
        )
        self.assertEqual(
            self._placement_tuple(controller),
            self._placement_tuple(self.mesh),
        )
        self.assertFalse(controller.Visibility)
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(all(not result.Visibility for result in results))

        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(10)
        self.assertFalse(controller.Suppressed)
        self.assertEqual(controller.Mesh.CountFacets, 0)
        self.assertTrue(controller.Visibility)
        self.assertTrue(all(result.Visibility for result in results))
        self.assertTrue(all(result.Mesh.CountFacets > 0 for result in results))
        self.assertFalse(self.mesh.Visibility)

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "mesh-plane-split.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            self.document = reopened
            self.mesh = reopened.getObject("PrimaryMesh")
            self.plane = reopened.getObject("CutPlane")

            reopened_controller = reopened.getObject(controller_name)
            reopened_results = [
                reopened.getObject(result_name) for result_name in result_names
            ]
            reopened_timeline = reopened.getObject("VibeCADTimeline")
            self.assertEqual(
                list(reopened_controller.Sources),
                [self.mesh],
            )
            self.assertEqual(
                list(reopened_controller.VibeCADTimelineReplacedInputs),
                [self.mesh],
            )
            self.assertEqual(
                set(reopened_controller.Group),
                set(reopened_results),
            )
            self.assertIn(
                reopened_controller,
                reopened_timeline.Operations,
            )
            for result in reopened_results:
                self.assertIs(
                    result.VibeCADTimelineOwner,
                    reopened_controller,
                )
                self.assertEqual(result.VibeCADTimelineRole, "resource")
                self.assertIn(result, reopened_timeline.Operations)
                self.assertIs(result.Source, self.mesh)
                self.assertIs(result.Plane, self.plane)
            reopened.recompute()
            self.assertTrue(reopened_controller.isValid())
            self.assertTrue(all(result.isValid() for result in reopened_results))

    def test_plane_section_creates_valid_wire_in_one_undo(self):
        self.plane.Placement = App.Placement(
            App.Vector(0.0, 0.0, 2.0),
            App.Rotation(),
        )
        self.document.recompute()
        self._select(self.mesh, self.plane)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_SectionByPlane", 0)
        self._wait_for_mesh_cut(capability="mesh.cut.section_by_plane.human")

        created = [obj for obj in self.document.Objects if obj not in objects_before]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "MeshPart::SectionByPlane")
        self.assertIs(result.Source, self.mesh)
        self.assertIs(result.Plane, self.plane)
        self.assertTrue(result.ConnectEdges)
        self.assertFalse(result.Shape.isNull())
        self.assertTrue(result.Shape.isValid())
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertFalse(any(obj.TypeId == "Mesh::OutputGroup" for obj in created))
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.assertFalse(result.UpdateFromSource)
        result.UpdateFromSource = True
        self.plane.Placement = App.Placement(
            App.Vector(0.0, 0.0, 3.0),
            App.Rotation(),
        )
        self.document.recompute()
        self.assertTrue(result.isValid())
        self.assertTrue(result.Shape.Vertexes)
        self.assertTrue(
            all(
                abs(vertex.Point.z - 3.0) < 1.0e-5
                for vertex in result.Shape.Vertexes
            )
        )

    def test_plane_section_multi_wire_result_is_one_history_step(self):
        combined = _tetrahedron()
        combined.addMesh(_tetrahedron(30.0))
        self.mesh.Mesh = combined
        self.plane.Placement = App.Placement(
            App.Vector(0.0, 0.0, 2.0),
            App.Rotation(),
        )
        self.document.recompute()
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.mesh, self.plane)
        Gui.runCommand("Mesh_SectionByPlane", 0)
        self._wait_for_mesh_cut(capability="mesh.cut.section_by_plane.human")

        created = [obj for obj in self.document.Objects if obj not in objects_before]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "MeshPart::SectionByPlane")
        self.assertIs(result.Source, self.mesh)
        self.assertIs(result.Plane, self.plane)
        self.assertFalse(result.Shape.isNull())
        self.assertTrue(result.Shape.isValid())
        self.assertGreaterEqual(len(result.Shape.Wires), 2)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(self.document.UndoCount, undo_before + 1)

    def test_cross_sections_accept_creates_valid_shape_in_one_undo(self):
        self._select(self.mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_CrossSections", 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        self._wait_for_mesh_cut(capability="mesh.cut.cross_sections.human")

        self.assertFalse(Gui.Control.activeDialog())
        created = [obj for obj in self.document.Objects if obj not in objects_before]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "MeshPart::CrossSections")
        self.assertIs(result.Source, self.mesh)
        self.assertFalse(result.Shape.isNull())
        self.assertTrue(result.Shape.isValid())
        self.assertGreater(len(result.PlanePositions), 0)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertFalse(any(obj.TypeId == "Mesh::OutputGroup" for obj in created))
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        self.assertFalse(result.UpdateFromSource)
        result.UpdateFromSource = True
        result.PlanePositions = [1.0]
        self.document.recompute()
        self.assertTrue(result.isValid())
        self.assertTrue(result.Shape.Vertexes)
        self.assertTrue(
            all(
                abs(vertex.Point.z - 1.0) < 1.0e-5
                for vertex in result.Shape.Vertexes
            )
        )

    def test_cross_sections_batch_is_one_source_preserving_history_step(self):
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.mesh, self.second_mesh)
        Gui.runCommand("Mesh_CrossSections", 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        self._wait_for_mesh_cut(capability="mesh.cut.cross_sections.human")

        self.assertFalse(Gui.Control.activeDialog())
        created = [obj for obj in self.document.Objects if obj not in objects_before]
        controller, results = self._assert_source_preserving_multi_result(
            created,
            (self.mesh, self.second_mesh),
            "MeshPart::CrossSections",
            "Create mesh cross-sections",
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(
            {result.Source for result in results},
            {self.mesh, self.second_mesh},
        )
        self.assertTrue(all(not result.Shape.isNull() for result in results))
        self.assertEqual(controller.Mesh.CountFacets, 0)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

    def test_remove_all_components_creates_reversible_empty_timeline_result(self):
        other = self._new_document("VibeCADMeshRemove")
        target = other.addObject("Mesh::Feature", "Target")
        target.Mesh = _tetrahedron()
        other.recompute()
        App.setActiveDocument(other.Name)
        self._select(target)
        undo_before = other.UndoCount
        Gui.runCommand("Mesh_RemoveComponents", 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())

        select_all = Gui.getMainWindow().findChild(
            QtGui.QPushButton,
            "selectAll",
        )
        self.assertIsNotNone(select_all)
        select_all.click()
        self._process_events(5)
        delete = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(delete)
        delete.click()
        self._wait_for_mesh_job(
            "mesh.modify.remove_components.human",
            document=other,
        )

        source = other.getObject("Target")
        self.assertIsNotNone(source)
        results = [obj for obj in other.Objects if obj.TypeId == "Mesh::FacetEdit"]
        self.assertEqual(len(results), 1)
        result = results[0]
        result_name = result.Name
        self.assertIs(result.Source, source)
        self.assertEqual(result.Action, "Remove Facets")
        self.assertEqual(result.Mesh.CountFacets, 0)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            [source],
        )
        self.assertFalse(source.Visibility)
        self.assertEqual(other.UndoCount, undo_before + 1)
        close = self._task_button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(close)
        close.click()
        self._process_events(5)

        timeline = other.getObject("VibeCADTimeline")
        self.assertIn(result, timeline.Operations)
        result_name = result.Name
        other.undo()
        self.assertIsNone(other.getObject(result_name))
        self.assertGreater(
            other.getObject("Target").Mesh.CountFacets,
            0,
        )
        self.assertTrue(other.getObject("Target").Visibility)
        other.redo()
        self._process_events(10)
        result = other.getObject(result_name)
        source = other.getObject("Target")
        timeline = other.getObject("VibeCADTimeline")
        self.assertIsNotNone(result)
        self.assertIsNotNone(source)
        self.assertIn(result, timeline.Operations)
        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(10)
        self.assertTrue(result.Suppressed)
        self.assertEqual(
            result.Mesh.CountFacets,
            source.Mesh.CountFacets,
        )
        self.assertFalse(result.Visibility)
        self.assertTrue(source.Visibility)
        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(10)
        self.assertFalse(result.Suppressed)
        self.assertEqual(result.Mesh.CountFacets, 0)
        self.assertTrue(result.Visibility)
        self.assertFalse(source.Visibility)

        # The two marker moves are themselves undoable document changes.
        other.undo()
        other.undo()
        other.undo()
        self.assertIsNone(other.getObject(result_name))
        self.assertGreater(
            other.getObject("Target").Mesh.CountFacets,
            0,
        )
        self.assertTrue(other.getObject("Target").Visibility)
        App.setActiveDocument(self.document.Name)

    def _assert_planar_segmentation_contract(
        self,
        command_name,
    ):
        segmented_mesh = _planar_grid(columns=4, rows=4)
        segmented_mesh.addMesh(
            _planar_grid(
                columns=4,
                rows=4,
                x_offset=20.0,
                z_offset=5.0,
            )
        )
        self.mesh.Mesh = segmented_mesh
        self.mesh.Placement = App.Placement(
            App.Vector(13.0, 5.0, -2.0),
            App.Rotation(),
        )
        placement_before = self._placement_tuple(self.mesh)
        points_before = self._mesh_points(self.mesh)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        self._select(self.mesh)
        Gui.runCommand(command_name, 0)
        self._process_events(5)
        self.assertTrue(Gui.Control.activeDialog())

        plane = Gui.getMainWindow().findChild(
            QtGui.QGroupBox,
            "groupBoxPln",
        )
        self.assertIsNotNone(plane)
        plane.setChecked(True)
        for name in ("groupBoxCyl", "groupBoxSph", "groupBoxFree"):
            group = Gui.getMainWindow().findChild(
                QtGui.QGroupBox,
                name,
            )
            if group:
                group.setChecked(False)
        minimum = Gui.getMainWindow().findChild(
            QtGui.QSpinBox,
            "numPln",
        )
        self.assertIsNotNone(minimum)
        minimum.setValue(1)

        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        operation = (
            "mesh_segmentation"
            if command_name == "Mesh_Segmentation"
            else "segmentation_best_fit"
        )
        self._wait_for_mesh_job(f"mesh.segment.{operation}.human")
        self.assertFalse(Gui.Control.activeDialog())

        created = [obj for obj in self.document.Objects if obj not in objects_before]
        controllers = [obj for obj in created if obj.TypeId == "Mesh::OutputGroup"]
        results = [obj for obj in created if obj.TypeId == "Mesh::FacetSubset"]
        self.assertEqual(len(controllers), 1)
        self.assertGreater(len(results), 1)
        self.assertEqual(len(created), len(results) + 1)
        controller = controllers[0]
        expected_kind = (
            "Curvature segmentation"
            if command_name == "Mesh_Segmentation"
            else "Best-fit segmentation"
        )
        self.assertEqual(controller.OperationKind, expected_kind)
        self.assertEqual(list(controller.Sources), [self.mesh])
        self.assertEqual(set(controller.Group), set(results))
        self.assertEqual(controller.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(controller.VibeCADTimelineReplacedInputs),
            [self.mesh],
        )
        for result in results:
            self.assertGreater(result.Mesh.CountFacets, 0)
            self.assertEqual(
                self._placement_tuple(result),
                placement_before,
            )
            self.assertIs(result.Source, self.mesh)
            self.assertGreater(len(result.FacetIndices), 0)
            self.assertNotEqual(result.SelectionKind, "Facet subset")
            self.assertEqual(result.VibeCADTimelineRole, "resource")
            self.assertIs(result.VibeCADTimelineOwner, controller)
        self.assertEqual(self._mesh_points(self.mesh), points_before)
        self.assertEqual(self._placement_tuple(self.mesh), placement_before)
        self.assertFalse(self.mesh.Visibility)
        self.assertTrue(controller.Visibility)
        self.assertEqual(controller.Mesh.CountFacets, 0)
        self.assertTrue(all(result.Visibility for result in results))

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIn(controller, timeline.Operations)
        for result in results:
            self.assertIn(result, timeline.Operations)
        timeline_names = self._timeline_object_names()
        self.assertEqual(timeline_names.count(controller.Name), 1)
        self.assertTrue(all(result.Name not in timeline_names for result in results))
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        results[0].Visibility = False
        controller.Visibility = False
        self._process_events()
        self.assertTrue(all(not result.Visibility for result in results))
        controller.Visibility = True
        self._process_events()
        self.assertFalse(results[0].Visibility)
        self.assertTrue(all(result.Visibility for result in results[1:]))
        results[0].Visibility = True
        self._process_events()

        self._timeline_button("VibeCADFeatureTimelinePrevious").click()
        self._process_events(10)
        self.assertTrue(controller.Suppressed)
        self.assertEqual(
            controller.Mesh.CountFacets,
            self.mesh.Mesh.CountFacets,
        )
        self.assertEqual(
            self._mesh_points(controller),
            self._mesh_points(self.mesh),
        )
        self.assertEqual(
            self._placement_tuple(controller),
            self._placement_tuple(self.mesh),
        )
        self.assertFalse(controller.Visibility)
        self.assertTrue(self.mesh.Visibility)
        self.assertTrue(all(not result.Visibility for result in results))

        self._timeline_button("VibeCADFeatureTimelineEnd").click()
        self._process_events(10)
        self.assertFalse(controller.Suppressed)
        self.assertEqual(controller.Mesh.CountFacets, 0)
        self.assertFalse(self.mesh.Visibility)
        self.assertTrue(controller.Visibility)
        self.assertTrue(all(result.Visibility for result in results))

        controller_name = controller.Name
        result_names = tuple(result.Name for result in results)
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / f"{command_name}-segments.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            self.document = reopened
            self.mesh = reopened.getObject("PrimaryMesh")

            reopened_controller = reopened.getObject(controller_name)
            reopened_results = [
                reopened.getObject(result_name) for result_name in result_names
            ]
            reopened_timeline = reopened.getObject("VibeCADTimeline")
            self.assertEqual(
                list(reopened_controller.Sources),
                [self.mesh],
            )
            self.assertEqual(
                list(reopened_controller.VibeCADTimelineReplacedInputs),
                [self.mesh],
            )
            self.assertEqual(
                set(reopened_controller.Group),
                set(reopened_results),
            )
            self.assertIn(
                reopened_controller,
                reopened_timeline.Operations,
            )
            for result in reopened_results:
                self.assertIs(
                    result.VibeCADTimelineOwner,
                    reopened_controller,
                )
                self.assertEqual(result.VibeCADTimelineRole, "resource")
                self.assertIn(result, reopened_timeline.Operations)
                self.assertIs(result.Source, self.mesh)
                self.assertTrue(result.isValid())
            reopened.recompute()
            self.assertTrue(reopened_controller.isValid())

            self.mesh.Placement = App.Placement(
                App.Vector(-7.0, 9.0, 4.0),
                App.Rotation(),
            )
            reopened.recompute()
            self.assertTrue(
                all(result.isValid() for result in reopened_results)
            )
            self.assertTrue(
                all(
                    self._placement_tuple(result)
                    == self._placement_tuple(self.mesh)
                    for result in reopened_results
                )
            )

    def test_curvature_segmentation_creates_positioned_nonempty_results(self):
        self._assert_planar_segmentation_contract(
            "Mesh_Segmentation",
        )

    def test_best_fit_segmentation_creates_positioned_nonempty_results(self):
        self._assert_planar_segmentation_contract(
            "Mesh_SegmentationBestFit",
        )

    def test_body_selection_is_a_meshing_shape_and_cancel_is_clean(self):
        body = self.document.addObject("PartDesign::Body", "Body")
        feature = body.newObject("PartDesign::Feature", "BodyResult")
        feature.Shape = Part.makeBox(5.0, 4.0, 3.0)
        body.Tip = feature
        self.document.recompute()
        self._select(body)
        self.assertTrue(Gui.isCommandActive("Mesh_FromPartShape"))
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Mesh_FromPartShape", 0)
        self._process_events(5)
        self._cancel_task("Mesh_FromPartShape")
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)
