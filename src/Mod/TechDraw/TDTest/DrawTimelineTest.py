import os
import tempfile
import unittest

import FreeCAD
import Part
import TechDraw
from PySide import QtCore

from .TechDrawTestUtilities import createPageWithSVGTemplate


class DrawTimelineTest(unittest.TestCase):
    def setUp(self):
        self.document = FreeCAD.newDocument("TDDocumentTimeline")
        self.saved_file = None

    def tearDown(self):
        for name in list(FreeCAD.listDocuments().keys()):
            FreeCAD.closeDocument(name)
        if self.saved_file and os.path.exists(self.saved_file):
            os.remove(self.saved_file)

    def _timeline(self):
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        return timeline

    @staticmethod
    def _operation_index(timeline, operation):
        return list(timeline.Operations).index(operation)

    def _wait_until(self, predicate, failure_message, timeout_ms=10000):
        """Process queued TechDraw worker results until an exact condition holds."""
        elapsed = QtCore.QElapsedTimer()
        elapsed.start()
        while elapsed.elapsed() < timeout_ms:
            result = predicate()
            if result:
                return result
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(10, loop.quit)
            loop.exec()
        self.fail(failure_message)

    def test_page_keeps_future_views_but_excludes_them_from_active_render_set(self):
        page = createPageWithSVGTemplate(self.document)
        first = self.document.addObject("TechDraw::DrawViewAnnotation", "FirstAnnotation")
        second = self.document.addObject("TechDraw::DrawViewAnnotation", "SecondAnnotation")
        page.addView(first)
        page.addView(second)

        timeline = self._timeline()
        timeline.Position = self._operation_index(timeline, second)
        second.Visibility = True

        self.assertEqual({view.Name for view in page.getViews()},
                         {"FirstAnnotation", "SecondAnnotation"})
        self.assertEqual([view.Name for view in page.getActiveViews()],
                         ["FirstAnnotation"])

        timeline.Position = len(timeline.Operations)
        self.assertEqual({view.Name for view in page.getActiveViews()},
                         {"FirstAnnotation", "SecondAnnotation"})

    def test_projection_collection_children_follow_the_marker(self):
        page = createPageWithSVGTemplate(self.document)
        group = self.document.addObject("TechDraw::DrawProjGroup", "ProjectionGroup")
        page.addView(group)
        front = group.addProjection("Front")
        top = group.addProjection("Top")

        timeline = self._timeline()
        timeline.Position = self._operation_index(timeline, group)

        self.assertEqual({view.Name for view in group.Views}, {front.Name, top.Name})
        self.assertEqual(page.getAllActiveViews(), [])

        timeline.Position = self._operation_index(timeline, group) + 1
        self.assertEqual({view.Name for view in page.getAllActiveViews()},
                         {group.Name, front.Name, top.Name})

    def test_removing_projection_group_preserves_an_independent_anchor(self):
        page = createPageWithSVGTemplate(self.document)
        anchor = self.document.addObject(
            "TechDraw::DrawViewPart",
            "IndependentAnchor",
        )
        group = self.document.addObject(
            "TechDraw::DrawProjGroup",
            "TemporaryProjectionGroup",
        )
        page.addView(anchor)
        page.addView(group)
        group.addView(anchor)
        group.Anchor = anchor
        page.removeView(anchor)

        self.document.removeObject(group.Name)

        self.assertIs(self.document.getObject(anchor.Name), anchor)
        self.assertIn(anchor, page.Views)

    def test_nested_collections_are_flattened_once_at_the_active_marker(self):
        page = createPageWithSVGTemplate(self.document)
        outer = self.document.addObject(
            "TechDraw::DrawViewCollection", "OuterCollection"
        )
        inner = self.document.addObject(
            "TechDraw::DrawViewCollection", "InnerCollection"
        )
        annotation = self.document.addObject(
            "TechDraw::DrawViewAnnotation", "NestedAnnotation"
        )
        inner.Views = [annotation]
        outer.Views = [inner]
        page.addView(outer)

        timeline = self._timeline()
        timeline.Position = self._operation_index(timeline, annotation)
        self.assertEqual(
            [view.Name for view in page.getAllActiveViews()],
            ["OuterCollection", "InnerCollection"],
        )

        timeline.Position = len(timeline.Operations)
        self.assertEqual(
            [view.Name for view in page.getAllActiveViews()],
            ["OuterCollection", "InnerCollection", "NestedAnnotation"],
        )

    def test_generated_objects_are_owned_resources_of_durable_operations(self):
        page = self.document.addObject("TechDraw::DrawPage", "OwnedPage")
        template = self.document.addObject("TechDraw::DrawSVGTemplate", "OwnedTemplate")
        page.Template = template

        annotation = self.document.addObject(
            "TechDraw::DrawViewAnnotation", "UserAnnotation"
        )
        page.addView(annotation)

        group = self.document.addObject("TechDraw::DrawProjGroup", "OwnedProjectionGroup")
        page.addView(group)
        projection = group.addProjection("Front")

        weld = self.document.addObject("TechDraw::DrawWeldSymbol", "OwnedWeldSymbol")
        page.addView(weld)
        weld_tiles = [
            obj for obj in self.document.Objects
            if obj.TypeId == "TechDraw::DrawTileWeld"
        ]
        operations = list(self._timeline().Operations)
        weld_index = operations.index(weld)
        self.assertEqual(
            sorted(operations.index(tile) for tile in weld_tiles),
            list(
                range(
                    weld_index - len(weld_tiles),
                    weld_index,
                )
            ),
        )

        for operation in (page, annotation, group, weld):
            self.assertNotEqual(
                getattr(operation, "VibeCADTimelineRole", None),
                "resource",
            )
        for operation in (group, weld):
            self.assertEqual(
                operation.VibeCADTimelineRole,
                "operation",
            )
            self.assertIn(
                "Hidden",
                operation.getEditorMode("VibeCADTimelineRole"),
            )
            self.assertFalse(
                operation.removeProperty("VibeCADTimelineRole")
            )

        self.assertNotEqual(
            getattr(template, "VibeCADTimelineRole", None),
            "resource",
        )
        self.assertNotIn(
            "VibeCADTimelineOwner",
            template.PropertiesList,
        )
        self.assertEqual(projection.VibeCADTimelineRole, "resource")
        self.assertEqual(projection.VibeCADTimelineOwner, group)
        self.assertEqual(
            projection.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        self.assertNotIn(group, projection.OutList)
        self.assertEqual(len(weld_tiles), 2)
        for tile in weld_tiles:
            self.assertEqual(tile.VibeCADTimelineRole, "resource")
            self.assertEqual(tile.VibeCADTimelineOwner, weld)
            self.assertEqual(
                tile.getTypeIdOfProperty("VibeCADTimelineOwner"),
                "App::PropertyLinkHidden",
            )
            # VibeCADTimelineOwner is hidden from the dependency graph. The
            # tile's existing TileParent property is a legitimate modeling
            # dependency on the same weld and must remain in the OutList.
            self.assertEqual(
                [(edge.FromProp, edge.ToObj) for edge in tile.OutListProp],
                [("TileParent", weld)],
            )
            self.assertIn(
                "Hidden",
                tile.getEditorMode("VibeCADTimelineRole"),
            )
            self.assertIn(
                "Hidden",
                tile.getEditorMode("VibeCADTimelineOwner"),
            )
            self.assertFalse(
                tile.removeProperty("VibeCADTimelineRole")
            )
            self.assertFalse(
                tile.removeProperty("VibeCADTimelineOwner")
            )

    def test_assigning_an_existing_template_keeps_it_an_independent_operation(
        self,
    ):
        template = self.document.addObject(
            "TechDraw::DrawSVGTemplate",
            "ExistingTemplate",
        )
        page = self.document.addObject(
            "TechDraw::DrawPage",
            "ExistingTemplatePage",
        )
        timeline = self._timeline()
        operations_before = list(timeline.Operations)

        page.Template = template

        self.assertEqual(list(timeline.Operations), operations_before)
        self.assertIn(template, timeline.Operations)
        self.assertIn(page, timeline.Operations)
        self.assertNotEqual(
            getattr(template, "VibeCADTimelineRole", None),
            "resource",
        )
        self.assertNotIn(
            "VibeCADTimelineOwner",
            template.PropertiesList,
        )

    def test_removing_page_does_not_delete_an_independent_template(self):
        template = self.document.addObject(
            "TechDraw::DrawSVGTemplate",
            "SharedTemplate",
        )
        page = self.document.addObject(
            "TechDraw::DrawPage",
            "SharedTemplatePage",
        )
        page.Template = template

        self.document.removeObject(page.Name)

        self.assertIs(self.document.getObject(template.Name), template)

    def test_view_projection_excludes_future_model_sources(self):
        page = createPageWithSVGTemplate(self.document)
        view = self.document.addObject("TechDraw::DrawViewPart", "ModelView")
        page.addView(view)
        source = self.document.addObject("Part::Box", "FutureBox")
        source.Length = 10
        source.Width = 8
        source.Height = 6
        view.Source = [source]

        timeline = self._timeline()
        timeline.Position = self._operation_index(timeline, source)
        self.document.recompute()
        self.assertEqual(len(view.getVisibleEdges()), 0)

        timeline.Position = len(timeline.Operations)
        self.document.recompute()
        self._wait_until(
            lambda: len(view.getVisibleEdges()) or None,
            "The restored model projection did not finish HLR",
        )

    def test_multi_source_projection_recomputes_without_future_source_geometry(self):
        page = createPageWithSVGTemplate(self.document)
        first_source = self.document.addObject("Part::Box", "FirstProjectionSource")
        view = self.document.addObject("TechDraw::DrawViewPart", "MultiSourceView")
        page.addView(view)
        future_source = self.document.addObject("Part::Box", "FutureProjectionSource")
        future_source.Placement.Base = FreeCAD.Vector(20, 0, 0)
        view.Source = [first_source, future_source]
        self.document.recompute()
        full_edge_count = self._wait_until(
            lambda: len(view.getVisibleEdges()) or None,
            "The full multi-source projection did not finish HLR",
        )

        timeline = self._timeline()
        timeline.Position = self._operation_index(timeline, future_source)
        self.document.recompute()
        partial_edge_count = self._wait_until(
            lambda: (
                edge_count
                if 0 < (edge_count := len(view.getVisibleEdges()))
                < full_edge_count
                else None
            ),
            "The partial multi-source projection did not finish HLR",
        )

        timeline.Position = len(timeline.Operations)
        self.document.recompute()
        self._wait_until(
            lambda: len(view.getVisibleEdges()) == full_edge_count,
            "The restored multi-source projection did not finish HLR",
        )

    def test_same_body_source_cache_is_invalidated_when_the_marker_moves(self):
        page = createPageWithSVGTemplate(self.document)
        body = self.document.addObject("PartDesign::Body", "TimelineBody")
        first = body.newObject("PartDesign::Feature", "FirstBodyFeature")
        first.Shape = Part.makeBox(10, 8, 6)
        body.Tip = first

        view = self.document.addObject("TechDraw::DrawViewPart", "BodyView")
        page.addView(view)
        view.Source = [body]
        self.document.recompute()
        self._wait_until(
            lambda: len(view.getVisibleEdges()) or None,
            "The first body projection did not finish HLR",
        )

        second = body.newObject("PartDesign::Feature", "SecondBodyFeature")
        second.Shape = Part.makeBox(20, 8, 6)
        body.Tip = second
        self.document.recompute()
        full_edge_count = self._wait_until(
            lambda: len(view.getVisibleEdges()) or None,
            "The updated body projection did not finish HLR",
        )
        self.assertTrue(TechDraw.viewPartAsSvg(view))
        self.assertTrue(TechDraw.viewPartAsDxf(view))

        timeline = self._timeline()
        timeline.Position = self._operation_index(timeline, second)

        self.assertIn(
            view.Name,
            {active_view.Name for active_view in page.getActiveViews()},
        )
        self.assertEqual(len(view.getVisibleEdges()), 0)
        self.assertEqual(TechDraw.viewPartAsSvg(view), "")
        self.assertEqual(TechDraw.viewPartAsDxf(view), "")

        timeline.Position = len(timeline.Operations)
        self._wait_until(
            lambda: len(view.getVisibleEdges()) == full_edge_count,
            "The restored body projection did not finish HLR",
        )
        self.assertTrue(TechDraw.viewPartAsSvg(view))
        self.assertTrue(TechDraw.viewPartAsDxf(view))

    def test_view_owner_dependency_cannot_render_before_its_parent(self):
        page = createPageWithSVGTemplate(self.document)
        child = self.document.addObject(
            "TechDraw::DrawViewAnnotation", "DependentAnnotation"
        )
        page.addView(child)
        parent = self.document.addObject(
            "TechDraw::DrawViewAnnotation", "FutureAnnotationOwner"
        )
        page.addView(parent)
        child.Owner = parent

        timeline = self._timeline()
        timeline.Position = self._operation_index(timeline, parent)
        active_names = {
            view.Name for view in page.getActiveViews()
        }
        self.assertNotIn(child.Name, active_names)
        self.assertNotIn(parent.Name, active_names)

        timeline.Position = len(timeline.Operations)
        active_names = {
            view.Name for view in page.getActiveViews()
        }
        self.assertIn(child.Name, active_names)
        self.assertIn(parent.Name, active_names)

    def test_legacy_projection_feature_is_suppressible_and_marker_aware(self):
        source = self.document.addObject("Part::Box", "ProjectionSource")
        projection = self.document.addObject(
            "TechDraw::FeatureProjection", "LegacyProjection"
        )
        projection.Source = source
        self.document.recompute()
        self.assertFalse(projection.Shape.isNull())
        self.assertIn("Suppressed", projection.PropertiesList)

        timeline = self._timeline()
        timeline.Position = self._operation_index(timeline, projection)
        projection.touch()
        self.document.recompute()
        self.assertTrue(projection.Shape.isNull())

        timeline.Position = len(timeline.Operations)
        projection.touch()
        self.document.recompute()
        self.assertFalse(projection.Shape.isNull())

    def test_marker_and_active_views_survive_save_and_reopen(self):
        page = createPageWithSVGTemplate(self.document)
        first = self.document.addObject("TechDraw::DrawViewAnnotation", "SavedFirst")
        second = self.document.addObject("TechDraw::DrawViewAnnotation", "SavedFuture")
        page.addView(first)
        page.addView(second)

        timeline = self._timeline()
        timeline.Position = self._operation_index(timeline, second)
        expected_position = timeline.Position

        self.saved_file = os.path.join(
            tempfile.gettempdir(), "techdraw_document_timeline.FCStd"
        )
        self.document.saveAs(self.saved_file)
        FreeCAD.closeDocument(self.document.Name)

        self.document = FreeCAD.openDocument(self.saved_file)
        restored_page = self.document.getObject("Page")
        restored_timeline = self.document.getObject("VibeCADTimeline")
        restored_template = restored_page.Template
        self.assertEqual(restored_timeline.Position, expected_position)
        self.assertTrue(os.path.isfile(str(restored_template.PageResult)))
        self.assertGreater(os.path.getsize(str(restored_template.PageResult)), 0)
        self.assertNotEqual(
            getattr(restored_template, "VibeCADTimelineRole", None),
            "resource",
        )
        self.assertNotIn(
            "VibeCADTimelineOwner",
            restored_template.PropertiesList,
        )
        restored_operations = list(restored_timeline.Operations)
        self.assertLess(
            restored_operations.index(restored_template),
            restored_operations.index(restored_page),
        )
        self.assertEqual({view.Name for view in restored_page.getViews()},
                         {"SavedFirst", "SavedFuture"})
        self.assertEqual([view.Name for view in restored_page.getActiveViews()],
                         ["SavedFirst"])

        restored_timeline.Position = len(restored_timeline.Operations)
        self.assertEqual({view.Name for view in restored_page.getActiveViews()},
                         {"SavedFirst", "SavedFuture"})


if __name__ == "__main__":
    unittest.main()
