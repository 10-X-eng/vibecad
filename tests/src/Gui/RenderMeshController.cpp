// SPDX-License-Identifier: LGPL-2.1-or-later

#include <cmath>
#include <tuple>
#include <QThread>
#include <QTest>
#include <QElapsedTimer>

#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRep_Builder.hxx>
#include <TopoDS_Compound.hxx>
#include <BRepGProp.hxx>
#include <GProp_GProps.hxx>
#include <Inventor/nodes/SoMaterial.h>
#include <Inventor/nodes/SoSeparator.h>

#include <src/App/InitApplication.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/GeoFeature.h>
#include <App/HostRuntime.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/FrameBudget.h>
#include <Gui/MainWindow.h>
#include <Gui/Inventor/SoFCBoundingBox.h>
#include <Gui/ViewProviderGeometryObject.h>
#include <Mod/Part/App/PropertyTopoShape.h>
#include <Mod/Part/App/SectionGeometry.h>
#include <Mod/Part/Gui/RenderMeshController.h>
#include <Mod/Part/Gui/SectionFaceController.h>
#include <Mod/Part/Gui/SoBrepEdgeSet.h>
#include <Mod/Part/Gui/SoBrepFaceSet.h>
#include <Mod/Part/Gui/SoBrepPointSet.h>
#include <Mod/Part/Gui/ViewProviderExt.h>

namespace
{
struct ReleaseCallback
{
    std::function<void()> callback;
    ~ReleaseCallback() { callback(); }
};

class InspectablePartView: public PartGui::ViewProviderPartExt
{
public:
    SoMaterial* edgeMaterial() const { return pcLineMaterial; }
    SoMaterial* pointMaterial() const { return pcPointMaterial; }
    SoMaterial* faceMaterial() const { return pcShapeMaterial; }
    void setFaceCount(int count) { faceset->partIndex.setNum(count); }
    void clearLineGeometry() { lineset->coordIndex.setNum(0); }
    mutable unsigned shapeReads {0};
    Part::TopoShape renderedShape;
    Part::TopoShape getRenderedShape() const override { ++shapeReads; return renderedShape; }
    void refreshUnchangedGeometry() { VisualTouched = false; updateVisual(); }
};

class CountedShape: public Part::PropertyPartShape
{
public:
    struct Reads
    {
        std::atomic<unsigned> count {0};
        std::atomic<bool> usedGui {false};
    };
    std::shared_ptr<Reads> reads = std::make_shared<Reads>();
    App::Property* Copy() const override
    {
        auto copy = new CountedShape;
        copy->setValue(getValue());
        copy->reads = reads;
        return copy;
    }
    Base::BoundBox3d getBoundingBox() const override
    {
        ++reads->count;
        if (QThread::currentThread() == qApp->thread()) { reads->usedGui = true; }
        return Part::PropertyPartShape::getBoundingBox();
    }
};

class BoundsFeature: public App::GeoFeature
{
public:
    CountedShape shape;
    const App::PropertyComplexGeoData* getPropertyOfGeometry() const override { return &shape; }
};

class BoundsView: public Gui::ViewProviderGeometryObject
{
public:
    SbVec3f minimum() const { return pcBoundingBox->minBounds.getValue(); }
    SbVec3f maximum() const { return pcBoundingBox->maxBounds.getValue(); }
};
}

class RenderMeshControllerTest: public QObject
{
    Q_OBJECT

    std::unique_ptr<Gui::Application> application;
    std::unique_ptr<Gui::MainWindow> window;

private Q_SLOTS:
    void initTestCase()
    {
        tests::initApplication();
        Gui::Application::initApplication();
        Gui::Application::initOpenInventor();
        application = std::make_unique<Gui::Application>(true);
        window = std::make_unique<Gui::MainWindow>();
    }

    void cleanupTestCase()
    {
        App::GetApplication().closeAllDocuments();
        window.reset();
        application.reset();
    }

    void preparesLatestShapeOffTheGuiThread()
    {
        PartGui::RenderMeshController controller;
        int target = 0;
        int completions = 0;
        std::shared_ptr<const Part::RenderMesh> result;

        controller.request(
            &target,
            BRepPrimAPI_MakeCylinder(10.0, 20.0).Shape(),
            0.1,
            28.5,
            false,
            [&](PartGui::RenderMeshResult prepared) {
                ++completions;
                result = std::move(prepared.mesh);
            }
        );
        controller.request(
            &target,
            BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(),
            0.1,
            28.5,
            false,
            [&](PartGui::RenderMeshResult prepared) {
                QCOMPARE(QThread::currentThread(), qApp->thread());
                QVERIFY2(prepared.error.empty(), prepared.error.c_str());
                ++completions;
                result = std::move(prepared.mesh);
            }
        );

        QTRY_COMPARE_WITH_TIMEOUT(completions, 1, 5000);
        QVERIFY(result);
        QCOMPARE(result->faceTriangleCounts.size(), std::size_t {6});
    }

    void boundingOverlayDoesNotInspectGeometryUntilShown()
    {
        if (Part::PropertyPartShape::getClassTypeId().isBad()) {
            Part::PropertyPartShape::init();
        }
        BoundsFeature feature;
        BoundsView view;
        feature.shape.setValue(BRepPrimAPI_MakeBox(10, 20, 30).Shape());
        view.attach(&feature);
        view.addDisplayMaskMode(new SoSeparator, "Test");
        view.setDisplayMaskMode("Test");
        view.show();
        QVERIFY(view.isShow());
        for (int i = 0; i < 50; ++i) {
            view.updateData(&feature.shape);
            view.updateData(&feature.Placement);
        }
        QCOMPARE(feature.shape.reads->count.load(), 0U);

        view.BoundingBox.setValue(true);
        QCOMPARE(feature.shape.reads->count.load(), 0U);
        QTRY_VERIFY((view.maximum() - SbVec3f(10, 20, 30)).length() < 0.001F);
        QVERIFY(!feature.shape.reads->usedGui.load());
        view.show();
        QCOMPARE(feature.shape.reads->count.load(), 1U);

        view.hide();
        feature.shape.setValue(BRepPrimAPI_MakeBox(gp_Pnt(4, 5, 6), 7, 8, 9).Shape());
        view.updateData(&feature.shape);
        QCOMPARE(feature.shape.reads->count.load(), 1U);
        view.show();
        QTRY_VERIFY((view.minimum() - SbVec3f(4, 5, 6)).length() < 0.001F);
        QCOMPARE(feature.shape.reads->count.load(), 2U);
        QVERIFY((view.maximum() - SbVec3f(11, 13, 15)).length() < 0.001F);

        view.BoundingBox.setValue(false);
        view.updateData(&feature.shape);
        QCOMPARE(feature.shape.reads->count.load(), 2U);
        // The public method is also used independently of the property.
        view.showBoundingBox(true);
        QTRY_COMPARE(feature.shape.reads->count.load(), 3U);
        view.updateData(&feature.shape);
        QTRY_COMPARE(feature.shape.reads->count.load(), 4U);
        QVERIFY(!feature.shape.reads->usedGui.load());

        // A burst is captured once at the next owner dispatch, not once per
        // property notification. Only the newest immutable value is adopted.
        const auto beforeBurst = feature.shape.reads->count.load();
        for (int i = 1; i <= 50; ++i) {
            feature.shape.setValue(BRepPrimAPI_MakeBox(i, 20, 30).Shape());
            view.updateData(&feature.shape);
        }
        QTRY_VERIFY((view.maximum() - SbVec3f(50, 20, 30)).length() < 0.001F);
        QCOMPARE(feature.shape.reads->count.load(), beforeBurst + 1);
        QVERIFY(!feature.shape.reads->usedGui.load());

        auto reads = feature.shape.reads;
        {
            BoundsView discarded;
            discarded.attach(&feature);
            discarded.addDisplayMaskMode(new SoSeparator, "Test");
            discarded.setDisplayMaskMode("Test");
            discarded.show();
            discarded.BoundingBox.setValue(true);
        }
        bool ownerDrained = false;
        QVERIFY(Gui::dispatchToGuiFrame([&] { ownerDrained = true; }));
        QTRY_VERIFY(ownerDrained);
        QCOMPARE(reads->count.load(), beforeBurst + 1);
    }

    void deferredRestoreDoesNotTargetAReplacementDocument()
    {
        if (PartGui::SoBrepFaceSet::getClassTypeId().isBad()) {
            PartGui::SoBrepFaceSet::initClass();
            PartGui::SoBrepEdgeSet::initClass();
            PartGui::SoBrepPointSet::initClass();
        }
        if (PartGui::ViewProviderPartExt::getClassTypeId().isBad()) {
            PartGui::ViewProviderPartExt::init();
        }
        auto& guiApplication = *application;
        auto& app = App::GetApplication();
        const auto addView = [&](App::Document* document) {
            auto* object = document->addObject("App::DocumentObject", "RestoredShape");
            auto* view = new InspectablePartView;
            view->attach(object);
            guiApplication.getDocument(document)->addViewProvider(view);
            guiApplication.getDocument(document)->signalNewObject(*view);
            view->Visibility.setValue(true);
            return view;
        };
        auto* original = app.newDocument("DeferredRestoreIdentity");
        auto* originalView = addView(original);
        originalView->startRestoring();
        originalView->finishRestoring();
        // No Qt events have run: the old restore request is still queued.
        // Removing its target cancels presentation before closing the document.
        original->removeObject("RestoredShape");
        QVERIFY(app.closeDocument("DeferredRestoreIdentity"));
        auto* replacement = app.newDocument("DeferredRestoreIdentity");
        auto* replacementView = addView(replacement);
        replacementView->shapeReads = 0;
        bool dispatched = false;
        QVERIFY(Gui::dispatchToGuiFrame([&] { dispatched = true; }));
        QTRY_VERIFY_WITH_TIMEOUT(dispatched, 5000);
        // A queued restore must not inspect/render a different document just
        // because its name and its first object's name/ID were reused.
        QCOMPARE(replacementView->shapeReads, 0U);
        QVERIFY(app.closeDocument("DeferredRestoreIdentity"));
    }

    void renderedSnapshotDoesNotReadDocumentAndSurvivesViewRemoval()
    {
        if (PartGui::SoBrepFaceSet::getClassTypeId().isBad()) {
            PartGui::SoBrepFaceSet::initClass();
            PartGui::SoBrepEdgeSet::initClass();
            PartGui::SoBrepPointSet::initClass();
        }
        if (PartGui::ViewProviderPartExt::getClassTypeId().isBad()) {
            PartGui::ViewProviderPartExt::init();
        }
        auto& app = App::GetApplication();
        auto* document = app.newDocument("RenderedSectionSnapshot");
        auto* object = document->addObject("App::DocumentObject", "Shape");
        auto* view = new InspectablePartView;
        view->attach(object);
        application->getDocument(document)->addViewProvider(view);
        application->getDocument(document)->signalNewObject(*view);
        QVERIFY(view->getRenderedShapeSnapshot().IsNull());
        view->renderedShape = Part::TopoShape(BRepPrimAPI_MakeBox(2.0, 3.0, 4.0).Shape());
        QVERIFY(!view->renderedShape.isNull());
        QCOMPARE(application->getViewProvider<PartGui::ViewProviderPartExt>(object), view);
        view->refreshUnchangedGeometry();
        QVERIFY(document->isPresentationUpdateActive());
        QTRY_VERIFY(document->isClosable());
        view->shapeReads = 0;
        const auto snapshot = view->getRenderedShapeSnapshot();
        QVERIFY(!snapshot.IsNull());
        QVERIFY(snapshot.IsPartner(view->renderedShape.getShape()));
        QCOMPARE(view->shapeReads, 0U);
        // Capturing the displayed generation must not inspect a newer model
        // shape which has not yet been installed by the presentation worker.
        view->renderedShape = Part::TopoShape(BRepPrimAPI_MakeCylinder(5.0, 6.0).Shape());
        QVERIFY(view->getRenderedShapeSnapshot().IsPartner(snapshot));
        document->removeObject("Shape");
        QVERIFY(app.closeDocument("RenderedSectionSnapshot"));
        // The snapshot owns native geometry, not a view/document wrapper.
        const auto owner = std::this_thread::get_id();
        auto read = app.hostRuntime().submit([snapshot, owner](std::stop_token) {
            return std::pair {
                std::this_thread::get_id() != owner,
                Part::TopoShape(snapshot).countSubElements("Face")
            };
        });
        const auto [offOwner, faces] = read.get();
        QVERIFY(offOwner);
        QCOMPARE(faces, 6UL);
    }

    void meshSectionPreservesHolesAndDisplayedPlacement()
    {
        const auto tube = BRepAlgoAPI_Cut(
            BRepPrimAPI_MakeCylinder(5.0, 6.0).Shape(),
            BRepPrimAPI_MakeCylinder(2.0, 6.0).Shape()).Shape();
        auto mesh = Part::prepareRenderMesh(tube, 0.05, 5.0);
        const auto originalVertices = mesh.vertices;
        Base::Matrix4D displayed;
        displayed.move(Base::Vector3d(200.0, 40.0, 60.0));
        const auto result = Part::prepareSectionMeshFaces(
            mesh, displayed, Base::Vector3d(200.0, 40.0, 63.0), Base::Vector3d(0.0, 0.0, 1.0));
        QVERIFY(!result.IsNull());
        GProp_GProps area;
        BRepGProp::SurfaceProperties(result, area);
        QVERIFY(std::abs(area.Mass() - 21.0 * std::acos(-1.0)) < 0.2);
        QVERIFY(std::abs(area.CentreOfMass().Z() - 63.0) < 1e-7);
        QVERIFY(mesh.vertices == originalVertices);
        QVERIFY(Part::prepareSectionMeshFaces(mesh, displayed, Base::Vector3d(0, 0, 100), Base::Vector3d(0, 0, 1)).IsNull());
        std::stop_source cancelled;
        cancelled.request_stop();
        QVERIFY_EXCEPTION_THROWN(Part::prepareSectionMeshFaces(
            mesh, displayed, Base::Vector3d(0, 0, 63), Base::Vector3d(0, 0, 1), cancelled.get_token()), std::runtime_error);
    }

    void meshSectionHandlesVerticesAndCoplanarBoundary()
    {
        const auto mesh = Part::prepareRenderMesh(BRepPrimAPI_MakeBox(2.0, 2.0, 2.0).Shape(), 0.5, 10.0);
        for (const auto& [origin, normal, expected] : {
                 std::tuple {Base::Vector3d(1, 1, 0), Base::Vector3d(1, 1, 0), 4 * std::sqrt(2.0)},
                 std::tuple {Base::Vector3d(), Base::Vector3d(1, 0, 0), 4.0}}) {
            const auto faces = Part::prepareSectionMeshFaces(mesh, {}, origin, normal);
            QVERIFY(!faces.IsNull());
            GProp_GProps area;
            BRepGProp::SurfaceProperties(faces, area);
            QVERIFY(std::abs(area.Mass() - expected) < 1e-6);
        }
    }

    void meshSectionAtStepUsesTheKeptSideContour()
    {
        const auto step = BRepAlgoAPI_Fuse(
            BRepPrimAPI_MakeBox(2.0, 2.0, 1.0).Shape(),
            BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 1), 1.0, 2.0, 1.0).Shape()).Shape();
        const auto mesh = Part::prepareRenderMesh(step, 0.1, 5.0);
        for (double sign : {1.0, -1.0}) {
            const auto faces = Part::prepareSectionMeshFaces(
                mesh, {}, Base::Vector3d(0, 0, 1), Base::Vector3d(0, 0, sign));
            QVERIFY(!faces.IsNull());
            GProp_GProps area;
            BRepGProp::SurfaceProperties(faces, area);
            QVERIFY(std::abs(area.Mass() - (sign > 0 ? 2.0 : 4.0)) < 1e-6);
        }
    }

    void meshSectionKeepsCompoundSolidsSeparate()
    {
        for (const auto& position : {gp_Pnt(2, 2, 0), gp_Pnt(2, 0, 0), gp_Pnt(1, 1, 0)}) {
            BRep_Builder builder;
            TopoDS_Compound compound;
            builder.MakeCompound(compound);
            builder.Add(compound, BRepPrimAPI_MakeBox(2.0, 2.0, 2.0).Shape());
            builder.Add(compound, BRepPrimAPI_MakeBox(position, 2.0, 2.0, 2.0).Shape());
            const auto mesh = Part::prepareRenderMesh(compound, 0.1, 5.0);
            for (double sign : {1.0, -1.0}) {
                const auto faces = Part::prepareSectionMeshFaces(
                    mesh, {}, Base::Vector3d(0, 0, 1), Base::Vector3d(0, 0, sign));
                QVERIFY(!faces.IsNull());
                GProp_GProps area;
                BRepGProp::SurfaceProperties(faces, area);
                // A compound retains its independent solids, including overlaps.
                QVERIFY(std::abs(area.Mass() - 8.0) < 1e-6);
            }
        }
    }

    void meshSectionRejectsOpenContours()
    {
        Part::RenderMesh mesh;
        mesh.vertices = {0, 0, -1, 1, 0, 1, 0, 1, 1};
        mesh.triangleIndices = {0, 1, 2, -1};
        QVERIFY_EXCEPTION_THROWN(Part::prepareSectionMeshFaces(
            mesh, {}, Base::Vector3d(), Base::Vector3d(0, 0, 1)), std::runtime_error);
    }

    void sectionFacesUseDisplayedPlacementAndPreserveHoles()
    {
        auto source = BRepPrimAPI_MakeBox(2.0, 3.0, 4.0).Shape();
        gp_Trsf oldPlacement;
        oldPlacement.SetTranslation(gp_Vec(700.0, 800.0, 900.0));
        source.Location(TopLoc_Location(oldPlacement));
        Base::Matrix4D displayed;
        displayed.move(Base::Vector3d(200.0, 40.0, 60.0));
        auto result = App::GetApplication().hostRuntime().submit(
            [source, displayed](std::stop_token stop) {
                return Part::prepareSectionFaces(
                    source, displayed, Base::Vector3d(201.0, 40.0, 60.0),
                    Base::Vector3d(1.0, 0.0, 0.0), stop);
            }).get();
        QVERIFY(!result.IsNull());
        GProp_GProps area;
        BRepGProp::SurfaceProperties(result, area);
        QVERIFY(std::abs(area.Mass() - 12.0) < 1e-7);
        QCOMPARE(source.Location().Transformation().TranslationPart().X(), 700.0);

        const auto tube = BRepAlgoAPI_Cut(
            BRepPrimAPI_MakeCylinder(5.0, 6.0).Shape(),
            BRepPrimAPI_MakeCylinder(2.0, 6.0).Shape()).Shape();
        result = App::GetApplication().hostRuntime().submit([tube](std::stop_token stop) {
            return Part::prepareSectionFaces(tube, {}, Base::Vector3d(0.0, 0.0, 3.0),
                                             Base::Vector3d(0.0, 0.0, 1.0), stop);
        }).get();
        QVERIFY(!result.IsNull());
        GProp_GProps tubeArea;
        BRepGProp::SurfaceProperties(result, tubeArea);
        QVERIFY2(std::abs(tubeArea.Mass() - 21.0 * std::acos(-1.0)) < 1e-7,
                 qPrintable(QString::number(tubeArea.Mass(), 'g', 17)));

        Base::Matrix4D scaled;
        scaled.scale(-2.0, 3.0, 0.5);
        result = App::GetApplication().hostRuntime().submit([source, scaled](std::stop_token stop) {
            return Part::prepareSectionFaces(source, scaled, Base::Vector3d(0.0, 0.0, 1.0),
                                             Base::Vector3d(0.0, 0.0, 1.0), stop);
        }).get();
        QVERIFY(!result.IsNull());
        GProp_GProps scaledArea;
        BRepGProp::SurfaceProperties(result, scaledArea);
        QVERIFY(std::abs(scaledArea.Mass() - 36.0) < 1e-7);

        std::stop_source cancelled;
        cancelled.request_stop();
        auto cancellation = App::GetApplication().hostRuntime().submit(
            [source, token = cancelled.get_token()](std::stop_token) {
                return Part::prepareSectionFaces(source, {}, Base::Vector3d(),
                                                 Base::Vector3d(0.0, 0.0, 1.0), token);
            });
        QVERIFY_EXCEPTION_THROWN(cancellation.get(), std::runtime_error);
    }

    void sectionControllerDeliversOnlyLatestOnOwner()
    {
        PartGui::SectionFaceController controller;
        const auto source = BRepPrimAPI_MakeBox(2.0, 3.0, 4.0).Shape();
        int deliveries = 0;
        for (int index = 0; index < 3; ++index) {
            controller.request({{source, {}}}, Base::Vector3d(0.0, 0.0, 1.0 + index),
                               Base::Vector3d(0.0, 0.0, 1.0),
                [&, index](PartGui::SectionFaceResult result) {
                    QCOMPARE(QThread::currentThread(), qApp->thread());
                    QCOMPARE(index, 2);
                    QVERIFY2(result.error.empty(), result.error.c_str());
                    QCOMPARE(result.faces.size(), std::size_t(1));
                    ++deliveries;
                });
        }
        QTRY_COMPARE(deliveries, 1);
    }

    void sectionDisplayKeepsTrianglesAndHatchOutOfHoles()
    {
        const auto tube = BRepAlgoAPI_Cut(
            BRepPrimAPI_MakeCylinder(5.0, 6.0).Shape(),
            BRepPrimAPI_MakeCylinder(2.0, 6.0).Shape()).Shape();
        auto geometry = App::GetApplication().hostRuntime().submit([tube](std::stop_token stop) {
            const Base::Vector3d origin(0.0, 0.0, 3.0), normal(0.0, 0.0, 1.0);
            const auto faces = Part::prepareSectionFaces(tube, {}, origin, normal, stop);
            return Part::prepareSectionDisplay(faces, origin, normal, 0.5, stop);
        }).get();
        QVERIFY(!geometry.triangles.empty());
        QVERIFY(!geometry.hatch.empty());
        QVERIFY(!geometry.outlines.empty());
        double area = 0.0;
        for (const auto& triangle : geometry.triangles) {
            area += (triangle[1] - triangle[0]).Cross(triangle[2] - triangle[0]).Length() * 0.5;
            const auto center = (triangle[0] + triangle[1] + triangle[2]) / 3.0;
            QVERIFY(std::hypot(center.x, center.y) >= 1.75);
            for (const auto& point : triangle) {
                QVERIFY(std::abs(point.z - 2.95) < 1e-7);
            }
        }
        QVERIFY(std::abs(area - 21.0 * std::acos(-1.0)) < 1.0);
        for (const auto& line : geometry.hatch) {
            const auto delta = line[1] - line[0];
            const double lengthSquared = delta.Dot(delta);
            QVERIFY(lengthSquared > 0);
            const double t = std::clamp(-line[0].Dot(delta) / lengthSquared, 0.0, 1.0);
            const auto nearest = line[0] + delta * t;
            QVERIFY(std::hypot(nearest.x, nearest.y) >= 1.75);
            QVERIFY(std::abs(line[0].z - 2.95) < 1e-7);
            QVERIFY(std::abs(line[1].z - 2.95) < 1e-7);
        }
    }

    void sectionControllerDeliversPreparedDisplay()
    {
        PartGui::SectionFaceController controller;
        bool completed = false;
        controller.requestDisplay({{BRepPrimAPI_MakeBox(2.0, 3.0, 4.0).Shape(), {}}},
                                  Base::Vector3d(0.0, 0.0, 1.0), Base::Vector3d(0.0, 0.0, 1.0), 0.5,
            [&](PartGui::SectionFaceResult result) {
                QCOMPARE(QThread::currentThread(), qApp->thread());
                QVERIFY(result.error.empty());
                QVERIFY(result.geometry);
                QVERIFY(!result.geometry->triangles.empty());
                QVERIFY(!result.geometry->hatch.empty());
                QVERIFY(!result.geometry->outlines.empty());
                completed = true;
            });
        QTRY_VERIFY(completed);
    }

    void sectionCancellationReleasesCapturesOnOwner()
    {
        PartGui::SectionFaceController controller;
        bool released = false;
        auto capture = std::make_shared<ReleaseCallback>();
        capture->callback = [&] {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            released = true;
        };
        controller.request({{BRepPrimAPI_MakeBox(2.0, 3.0, 4.0).Shape(), {}}},
                           Base::Vector3d(0.0, 0.0, 1.0), Base::Vector3d(0.0, 0.0, 1.0),
                           [capture](PartGui::SectionFaceResult) { QFAIL("Cancelled result delivered"); });
        capture.reset();
        QVERIFY(!released);
        controller.cancel();
        QVERIFY(released);
    }

    void sectionCaptureReleaseCanSubmitANewerRequest()
    {
        PartGui::SectionFaceController controller;
        const auto shape = BRepPrimAPI_MakeBox(2.0, 3.0, 4.0).Shape();
        const Base::Vector3d origin(0.0, 0.0, 1.0), normal(0.0, 0.0, 1.0);
        int delivered = 0;
        auto capture = std::make_shared<ReleaseCallback>();
        capture->callback = [&] {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            controller.request({{shape, {}}}, origin, normal, [&](PartGui::SectionFaceResult result) {
                QVERIFY(result.error.empty());
                QCOMPARE(result.faces.size(), std::size_t(1));
                ++delivered;
            });
        };
        controller.request({{shape, {}}}, origin, normal,
                           [capture](PartGui::SectionFaceResult) { QFAIL("Obsolete first result"); });
        capture.reset();
        controller.request({{shape, {}}}, origin, normal,
                           [](PartGui::SectionFaceResult) { QFAIL("Obsolete pending result"); });
        QTRY_COMPARE(delivered, 1);
    }

    void cancelledCompletionCannotAdoptIntoReusedTarget()
    {
        PartGui::RenderMeshController controller;
        auto& runtime = App::GetApplication().hostRuntime();
        int target = 0;
        int obsoleteCompletions = 0;
        int currentCompletions = 0;
        std::shared_ptr<const Part::RenderMesh> result;
        controller.request(
            &target, BRepPrimAPI_MakeCylinder(10.0, 20.0).Shape(), 0.1, 28.5, false,
            [&](PartGui::RenderMeshResult) { ++obsoleteCompletions; }
        );

        // Let the real worker enqueue its completion without dispatching GUI
        // events. Reusing the target then deterministically tests a late result
        // from before cancelAll(), rather than depending on worker timing.
        QElapsedTimer deadline;
        deadline.start();
        while ((runtime.queuedTaskCount() || runtime.activeTaskCount())
               && deadline.elapsed() < 5000) {
            QThread::msleep(1);
        }
        QCOMPARE(runtime.queuedTaskCount(), std::size_t {0});
        QCOMPARE(runtime.activeTaskCount(), std::size_t {0});
        controller.cancelAll();
        controller.request(
            &target, BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(), 0.1, 28.5, false,
            [&](PartGui::RenderMeshResult prepared) {
                ++currentCompletions;
                result = std::move(prepared.mesh);
            }
        );

        QTRY_COMPARE_WITH_TIMEOUT(currentCompletions, 1, 5000);
        QCOMPARE(obsoleteCompletions, 0);
        QVERIFY(result);
        QCOMPARE(result->faceTriangleCounts.size(), std::size_t {6});
    }

    void cancellationReleasesOwnerCapturesBeforeWorkerDelivery()
    {
        PartGui::RenderMeshController controller;
        int target = 0;
        bool released = false;
        bool delivered = false;
        auto lease = std::make_shared<ReleaseCallback>();
        lease->callback = [&] {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            released = true;
        };
        controller.request(&target, BRepPrimAPI_MakeBox(10, 20, 30).Shape(), 0.1, 28.5, false,
            [lease, &delivered](PartGui::RenderMeshResult) { delivered = true; });
        lease.reset();
        controller.cancel(&target);
        // No GUI event processing: cancellation must not retain a document's
        // presentation lease until the obsolete worker delivers its result.
        QVERIFY(released);
        QVERIFY(!delivered);
    }

    void pendingCaptureReleaseMayReenterController()
    {
        PartGui::RenderMeshController controller;
        int target = 0;
        int obsoleteCompletions = 0;
        int currentCompletions = 0;
        bool released = false;
        controller.request(&target, BRepPrimAPI_MakeCylinder(10, 20).Shape(), 0.1, 28.5, false,
            [&](PartGui::RenderMeshResult) { ++obsoleteCompletions; });
        auto lease = std::make_shared<ReleaseCallback>();
        lease->callback = [&] {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            released = true;
            controller.cancelAll();
            controller.request(&target, BRepPrimAPI_MakeBox(10, 20, 30).Shape(), 0.1, 28.5, false,
                [&](PartGui::RenderMeshResult result) {
                    QVERIFY2(result.error.empty(), result.error.c_str());
                    QVERIFY(result.mesh);
                    QCOMPARE(result.mesh->faceTriangleCounts.size(), std::size_t {6});
                    ++currentCompletions;
                });
        };
        controller.request(&target, BRepPrimAPI_MakeCylinder(12, 20).Shape(), 0.1, 28.5, false,
            [lease, &obsoleteCompletions](PartGui::RenderMeshResult) { ++obsoleteCompletions; });
        lease.reset();
        // Replacing the pending request releases the old capture. Its callback
        // clears/reuses the same target before this outer request returns.
        controller.request(&target, BRepPrimAPI_MakeCylinder(14, 20).Shape(), 0.1, 28.5, false,
            [&](PartGui::RenderMeshResult) { ++obsoleteCompletions; });
        QVERIFY(released);
        QTRY_COMPARE_WITH_TIMEOUT(currentCompletions, 1, 5000);
        QCOMPARE(obsoleteCompletions, 0);
    }

    void edgeColoursAreCompleteBeforeMeshAdoption()
    {
        if (PartGui::SoBrepFaceSet::getClassTypeId().isBad()) {
            PartGui::SoBrepFaceSet::initClass();
            PartGui::SoBrepEdgeSet::initClass();
            PartGui::SoBrepPointSet::initClass();
        }
        if (PartGui::ViewProviderPartExt::getClassTypeId().isBad()) {
            PartGui::ViewProviderPartExt::init();
        }
        InspectablePartView provider;
        provider.clearLineGeometry();
        std::vector<Base::Color> colours;
        for (int index = 0; index < 50; ++index) {
            colours.emplace_back(0.1F + index * 0.01F, 0.3F, 0.7F);
        }
        // Exercise the actual property callback, not a conversion substitute.
        // Restore can supply materials while geometry is still on a worker.
        provider.LineColorArray.setValues(colours);
        const auto& actual = provider.edgeMaterial()->diffuseColor;
        QCOMPARE(actual.getNum(), static_cast<int>(colours.size()));
        for (int index = 0; index < actual.getNum(); ++index) {
            QCOMPARE(actual[index][0], colours[index].r);
            QCOMPARE(actual[index][1], colours[index].g);
            QCOMPARE(actual[index][2], colours[index].b);
        }

        provider.PointColorArray.setValues(colours);
        provider.setFaceCount(static_cast<int>(colours.size()));
        std::vector<App::Material> materials(colours.size());
        for (std::size_t index = 0; index < colours.size(); ++index) {
            materials[index].diffuseColor = colours[index];
        }
        provider.ShapeAppearance.setValues(materials);
        provider.refreshUnchangedGeometry();
        const auto edgeStamp = provider.edgeMaterial()->getNodeId();
        const auto pointStamp = provider.pointMaterial()->getNodeId();
        const auto faceStamp = provider.faceMaterial()->getNodeId();
        for (int index = 0; index < 50; ++index) {
            provider.refreshUnchangedGeometry();
        }
        QCOMPARE(provider.edgeMaterial()->getNodeId(), edgeStamp);
        QCOMPARE(provider.pointMaterial()->getNodeId(), pointStamp);
        QCOMPARE(provider.faceMaterial()->getNodeId(), faceStamp);

        provider.setHighlightedEdges({Base::Color(1.0F, 0.0F, 0.0F)});
        provider.refreshUnchangedGeometry();
        QCOMPARE(actual.getNum(), static_cast<int>(colours.size()));
        QCOMPARE(actual[0][0], colours[0].r);
        // Derived providers can also change Coin fields directly. A cache
        // cannot assume that only property notifications alter appearance.
        provider.pointMaterial()->diffuseColor.setValue(1.0F, 0.0F, 0.0F);
        provider.refreshUnchangedGeometry();
        QCOMPARE(provider.pointMaterial()->diffuseColor.getNum(), static_cast<int>(colours.size()));
        QCOMPARE(provider.pointMaterial()->diffuseColor[0][0], colours[0].r);

        provider.setHighlightedFaces(std::vector<App::Material> {App::Material()});
        provider.refreshUnchangedGeometry();
        QCOMPARE(provider.faceMaterial()->diffuseColor.getNum(), static_cast<int>(colours.size()));
        QCOMPARE(provider.faceMaterial()->diffuseColor[0][0], colours[0].r);

        colours[0] = Base::Color(0.2F, 0.5F, 0.8F);
        provider.LineColorArray.setValues(colours);
        QCOMPARE(actual[0][0], colours[0].r);
        QCOMPARE(actual[0][1], colours[0].g);
        QCOMPARE(actual[0][2], colours[0].b);
    }
};

QTEST_MAIN(RenderMeshControllerTest)

#include "RenderMeshController.moc"
