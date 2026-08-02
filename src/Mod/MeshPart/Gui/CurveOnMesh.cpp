// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2017 Werner Mayer <wmayer[at]users.sourceforge.net>     *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/


#include <QMenu>
#include <QPointer>
#include <QStatusBar>
#include <QTimer>

#include <algorithm>
#include <optional>

#include <Inventor/SoPickedPoint.h>
#include <Inventor/details/SoFaceDetail.h>
#include <Inventor/events/SoMouseButtonEvent.h>
#include <Inventor/nodes/SoBaseColor.h>
#include <Inventor/nodes/SoCoordinate3.h>
#include <Inventor/nodes/SoDrawStyle.h>
#include <Inventor/nodes/SoLineSet.h>
#include <Inventor/nodes/SoPointSet.h>
#include <Inventor/nodes/SoSeparator.h>

#include <App/Document.h>
#include <App/DocumentObserver.h>
#include <Base/Console.h>
#include <Base/Converter.h>
#include <Base/Matrix.h>
#include <Base/Tools.h>
#include <Gui/Document.h>
#include <Gui/DocumentObserver.h>
#include <Gui/ExactTransaction.h>
#include <Gui/MainWindow.h>
#include <Gui/Utilities.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Mod/Mesh/App/Core/Algorithm.h>
#include <Mod/Mesh/App/Core/Grid.h>
#include <Mod/Mesh/App/Core/MeshKernel.h>
#include <Mod/Mesh/App/Core/Projection.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/Gui/ViewProvider.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Mesh/Gui/ParametricMeshFilter.h>

#include "../App/FeatureMeshPartOperations.h"
#include "CurveOnMesh.h"


#ifndef HAVE_ACOSH
# define HAVE_ACOSH
#endif
#ifndef HAVE_ASINH
# define HAVE_ASINH
#endif
#ifndef HAVE_ATANH
# define HAVE_ATANH
#endif


/* XPM */
// clang-format off
static const char* cursor_curveonmesh[] = {
"32 32 3 1",
"+ c white",
"# c red",
". c None",
"......+.........................",
"......+.........................",
"......+.........................",
"......+.........................",
"......+.........................",
"................................",
"+++++...+++++...................",
"................................",
"......+...............###.......",
"......+...............#.#.......",
"......+...............###.......",
"......+..............#..#.......",
"......+.............#....#......",
"....................#.+..#......",
"..................+#+..+..#...+.",
"................++#.....+.#..+..",
"......+........+..#......++#+...",
".......+......+..#.........#....",
"........++..++..#..........###..",
"..........++....#..........#.#..",
"......#........#...........###..",
".......#......#.................",
"........#.....#.................",
".........#...#..................",
"..........###...................",
"..........#.#...................",
"..........###...................",
"................................",
"................................",
"................................",
"................................",
"................................"};
// clang-format on

using namespace MeshPartGui;

namespace
{

bool sameMeshState(const Mesh::MeshObject& first, const Mesh::MeshObject& second)
{
    if (first.getTransform() != second.getTransform()
        || first.countSegments() != second.countSegments()) {
        return false;
    }

    const auto& firstKernel = first.getKernel();
    const auto& secondKernel = second.getKernel();
    const auto& firstPoints = firstKernel.GetPoints();
    const auto& secondPoints = secondKernel.GetPoints();
    const auto& firstFacets = firstKernel.GetFacets();
    const auto& secondFacets = secondKernel.GetFacets();
    if (firstPoints.size() != secondPoints.size() || firstFacets.size() != secondFacets.size()
        || !std::ranges::equal(firstPoints, secondPoints)
        || !std::ranges::equal(
            firstFacets,
            secondFacets,
            [](const MeshCore::MeshFacet& left, const MeshCore::MeshFacet& right) {
                return left._aulPoints[0] == right._aulPoints[0]
                    && left._aulPoints[1] == right._aulPoints[1]
                    && left._aulPoints[2] == right._aulPoints[2];
            }
        )) {
        return false;
    }

    for (unsigned long index = 0; index < first.countSegments(); ++index) {
        if (first.getSegment(index).getIndices() != second.getSegment(index).getIndices()) {
            return false;
        }
    }
    return true;
}

std::optional<Base::Vector3d> sourceLocalDirection(
    const Mesh::MeshObject& source,
    const Base::Vector3d& worldDirection
)
{
    try {
        Base::Matrix4D inverse = source.getTransform();
        inverse.inverseGauss();
        Base::Vector3d local {
            inverse[0][0] * worldDirection.x + inverse[0][1] * worldDirection.y
                + inverse[0][2] * worldDirection.z,
            inverse[1][0] * worldDirection.x + inverse[1][1] * worldDirection.y
                + inverse[1][2] * worldDirection.z,
            inverse[2][0] * worldDirection.x + inverse[2][1] * worldDirection.y
                + inverse[2][2] * worldDirection.z,
        };
        if (local.Length() <= 0.0) {
            return std::nullopt;
        }
        local.Normalize();
        return local;
    }
    catch (const Base::Exception&) {
        return std::nullopt;
    }
}

long continuityIndex(GeomAbs_Shape continuity)
{
    switch (continuity) {
        case GeomAbs_C0:
            return 0;
        case GeomAbs_C1:
            return 1;
        case GeomAbs_C2:
            return 2;
        case GeomAbs_C3:
            return 3;
        default:
            throw Base::ValueError("Curve continuity must be C0, C1, C2, or C3");
    }
}

}  // namespace

PROPERTY_SOURCE(MeshPartGui::ViewProviderCurveOnMesh, Gui::ViewProviderDocumentObject)

ViewProviderCurveOnMesh::ViewProviderCurveOnMesh()
{
    // the lines
    pcCoords = new SoCoordinate3;
    pcCoords->ref();
    pcCoords->point.setNum(0);

    pcLinesStyle = new SoDrawStyle;
    pcLinesStyle->style = SoDrawStyle::LINES;
    pcLinesStyle->lineWidth = 3;
    pcLinesStyle->ref();

    SoGroup* pcLineRoot = new SoSeparator();
    pcLineRoot->addChild(pcLinesStyle);
    SoBaseColor* linecol = new SoBaseColor;
    linecol->rgb.setValue(1.0f, 1.0f, 0.0f);
    pcLineRoot->addChild(linecol);
    pcLineRoot->addChild(pcCoords);
    pcLineRoot->addChild(new SoLineSet);

    // the nodes
    pcNodes = new SoCoordinate3;
    pcNodes->ref();
    pcNodes->point.setNum(0);

    pcPointStyle = new SoDrawStyle;
    pcPointStyle->style = SoDrawStyle::POINTS;
    pcPointStyle->pointSize = 15;
    pcPointStyle->ref();

    SoGroup* pcPointRoot = new SoSeparator();
    pcPointRoot->addChild(pcPointStyle);
    SoBaseColor* pointcol = new SoBaseColor;
    pointcol->rgb.setValue(1.0f, 0.5f, 0.0f);
    pcPointRoot->addChild(pointcol);
    pcPointRoot->addChild(pcNodes);
    pcPointRoot->addChild(new SoPointSet);

    SoGroup* group = new SoGroup;
    group->addChild(pcLineRoot);
    group->addChild(pcPointRoot);
    addDisplayMaskMode(group, "Point");
}

ViewProviderCurveOnMesh::~ViewProviderCurveOnMesh()
{
    pcCoords->unref();
    pcLinesStyle->unref();
    pcNodes->unref();
    pcPointStyle->unref();
}

void ViewProviderCurveOnMesh::setDisplayMode(const char* ModeName)
{
    setDisplayMaskMode(ModeName);
    ViewProviderDocumentObject::setDisplayMode(ModeName);
}

void ViewProviderCurveOnMesh::addVertex(const SbVec3f& v)
{
    int num = pcNodes->point.getNum();
    pcNodes->point.set1Value(num, v);
}

void ViewProviderCurveOnMesh::clearVertex()
{
    pcNodes->point.setNum(0);
}

void ViewProviderCurveOnMesh::setPoints(const std::vector<SbVec3f>& pts)
{
    pcCoords->point.setNum(pts.size());
    SbVec3f* coords = pcCoords->point.startEditing();
    int index = 0;
    for (auto it : pts) {
        coords[index] = it;
        index++;
    }
    pcCoords->point.finishEditing();
}

void ViewProviderCurveOnMesh::clearPoints()
{
    pcCoords->point.setNum(0);
}

// ------------------------------------------------------------------

class CurveOnMeshHandler::Private
{
public:
    struct PickedPoint
    {
        MeshCore::FacetIndex facet;
        SbVec3f point;
        SbVec3f normal;
    };

    struct ApproxPar
    {
        double weight1;
        double weight2;
        double weight3;
        double tol3d;
        int maxDegree;
        GeomAbs_Shape cont;

        ApproxPar()
        {
            weight1 = 0.2;
            weight2 = 0.4;
            weight3 = 0.2;
            tol3d = 1.0e-2;
            maxDegree = 5;
            cont = GeomAbs_C2;
        }
    };
    Private()
        : curve(new ViewProviderCurveOnMesh)
        , editcursor(QPixmap(cursor_curveonmesh), 7, 7)
    {}
    ~Private()
    {
        delete curve;
        delete grid;
    }
    void clearTarget()
    {
        curve->clearVertex();
        curve->clearPoints();
        pickedPoints.clear();
        projectionDirections.clear();
        cutLines.clear();
        wireClosed = false;
        mesh.reset();
        delete grid;
        grid = nullptr;
        kernel.Clear();
        sourceMesh = Mesh::MeshObject();
    }
    static void vertexCallback(void* ud, SoEventCallback* n);
    std::vector<SbVec3f> convert(const std::vector<Base::Vector3f>& points) const
    {
        std::vector<SbVec3f> pts;
        pts.reserve(points.size());
        for (const auto& it : points) {
            pts.push_back(Base::convertTo<SbVec3f>(it));
        }
        return pts;
    }
    bool createGrid()
    {
        auto* viewProvider = mesh ? mesh->get() : nullptr;
        Mesh::Feature* mf = viewProvider ? viewProvider->getObject<Mesh::Feature>() : nullptr;
        App::Document* targetDocument = document ? **document : nullptr;
        if (!mf || mf->getDocument() != targetDocument || !MeshGui::isNativeMeshInputActive(mf)
            || mf->Mesh.getValue().countFacets() == 0) {
            return false;
        }
        const Mesh::MeshObject& meshObject = mf->Mesh.getValue();
        sourceMesh = meshObject;
        kernel = meshObject.getKernel();
        kernel.Transform(meshObject.getTransform());

        MeshCore::MeshAlgorithm alg(kernel);
        float fAvgLen = alg.GetAverageEdgeLength();
        grid = new MeshCore::MeshFacetGrid(kernel, 5.0f * fAvgLen);
        return true;
    }
    bool projectLineOnMesh(const PickedPoint& pick)
    {
        PickedPoint last = pickedPoints.back();
        std::vector<Base::Vector3f> polyline;

        MeshCore::MeshProjection meshProjection(kernel);
        Base::Vector3f v1 = Base::convertTo<Base::Vector3f>(last.point);
        Base::Vector3f v2 = Base::convertTo<Base::Vector3f>(pick.point);
        const Base::Vector3f viewDirection = Base::convertTo<Base::Vector3f>(
            viewer->getViewer()->getViewDirection()
        );
        const Base::Vector3d worldViewDirection(viewDirection.x, viewDirection.y, viewDirection.z);
        if (meshProjection
                .projectLineOnMesh(*grid, v1, last.facet, v2, pick.facet, viewDirection, polyline)) {
            if (polyline.size() > 1) {
                const auto localDirection = sourceLocalDirection(sourceMesh, worldViewDirection);
                if (!localDirection) {
                    return false;
                }
                projectionDirections.push_back(*localDirection);
                if (cutLines.empty()) {
                    cutLines.push_back(polyline);
                }
                else {
                    SbVec3f dir1(0.0f, 0.0f, 0.0f);
                    SbVec3f dir2 = pick.point - last.point;
                    dir2.normalize();
                    std::size_t num = pickedPoints.size();
                    if (num >= 2) {
                        dir1 = pickedPoints[num - 1].point - pickedPoints[num - 2].point;
                        dir1.normalize();
                    }

                    // if the angle between two line segments is greater than the angle
                    // split the curve in this position
                    if (dir1.dot(dir2) < cosAngle) {
                        cutLines.push_back(polyline);
                    }
                    else {
                        std::vector<Base::Vector3f>& segm = cutLines.back();
                        segm.insert(segm.end(), polyline.begin() + 1, polyline.end());
                    }
                }

                return true;
            }
        }

        return false;
    }

    std::vector<PickedPoint> pickedPoints;
    std::vector<Base::Vector3d> projectionDirections;
    std::list<std::vector<Base::Vector3f>> cutLines;
    bool wireClosed {false};
    double distance {1};
    double cosAngle {0.7071};  // 45 degree
    double splitAngle {45.0};
    bool approximate {true};
    ViewProviderCurveOnMesh* curve;
    std::unique_ptr<Gui::WeakPtrT<MeshGui::ViewProviderMesh>> mesh;
    MeshCore::MeshFacetGrid* grid {nullptr};
    MeshCore::MeshKernel kernel;
    Mesh::MeshObject sourceMesh;
    QPointer<Gui::View3DInventor> viewer;
    std::unique_ptr<App::DocumentWeakPtrT> document;
    QCursor editcursor;
    ApproxPar par;
};

CurveOnMeshHandler::CurveOnMeshHandler(QObject* parent)
    : QObject(parent)
    , d_ptr(new Private)
{}

CurveOnMeshHandler::~CurveOnMeshHandler()
{
    disableCallback();
}

void CurveOnMeshHandler::enableApproximation(bool on)
{
    d_ptr->approximate = on;
}

void CurveOnMeshHandler::setParameters(int maxDegree, GeomAbs_Shape cont, double tol3d, double angle)
{
    d_ptr->par.maxDegree = maxDegree;
    d_ptr->par.cont = cont;
    d_ptr->par.tol3d = tol3d;
    d_ptr->splitAngle = angle;
    d_ptr->cosAngle = std::cos(Base::toRadians<double>(angle));
}

void CurveOnMeshHandler::onContextMenu()
{
    QMenu menu;
    menu.addAction(tr("Create"), this, &CurveOnMeshHandler::onCreate);
    if (!d_ptr->wireClosed && d_ptr->pickedPoints.size() >= 3) {
        menu.addAction(tr("Close wire"), this, &CurveOnMeshHandler::onCloseWire);
    }
    menu.addAction(tr("Clear"), this, &CurveOnMeshHandler::onClear);
    menu.addAction(tr("Cancel"), this, &CurveOnMeshHandler::onCancel);
    menu.exec(QCursor::pos());
}

void CurveOnMeshHandler::onCreate()
{
    App::Document* document = d_ptr->document ? **d_ptr->document : nullptr;
    auto* viewProvider = d_ptr->mesh ? d_ptr->mesh->get() : nullptr;
    auto* source = viewProvider ? viewProvider->getObject<Mesh::Feature>() : nullptr;
    if (!source || source->getDocument() != document || !MeshGui::isNativeMeshInputActive(source)
        || !sameMeshState(source->Mesh.getValue(), d_ptr->sourceMesh)) {
        Base::Console().warning("Curve on mesh was cancelled because its source mesh "
                                "changed\n");
        d_ptr->clearTarget();
        return;
    }

    if (d_ptr->pickedPoints.size() < 2
        || d_ptr->projectionDirections.size()
            != (d_ptr->wireClosed ? d_ptr->pickedPoints.size() : d_ptr->pickedPoints.size() - 1)
        || !MeshGui::hasCleanNativeMutationBoundary(document)) {
        return;
    }

    try {
        std::vector<long> facets;
        std::vector<Base::Vector3d> weights;
        facets.reserve(d_ptr->pickedPoints.size());
        weights.reserve(d_ptr->pickedPoints.size());
        for (const auto& picked : d_ptr->pickedPoints) {
            if (picked.facet >= d_ptr->kernel.CountFacets()) {
                throw Base::ValueError("A curve anchor facet no longer exists");
            }
            const MeshCore::MeshGeomFacet triangle = d_ptr->kernel.GetFacet(picked.facet);
            float weight0 = 0.0F;
            float weight1 = 0.0F;
            float weight2 = 0.0F;
            Base::Vector3f projected;
            triangle.ProjectPointToPlane(Base::convertTo<Base::Vector3f>(picked.point), projected);
            if (!triangle.Weights(projected, weight0, weight1, weight2)) {
                throw Base::ValueError("A curve anchor is no longer on its source facet");
            }
            facets.push_back(static_cast<long>(picked.facet));
            weights.emplace_back(weight0, weight1, weight2);
        }

        Gui::ExactTransaction transaction(*document, "Curve on mesh");
        auto* feature = document->addObject<MeshPart::CurveOnMesh>("CurveOnMesh");
        feature->Label.setValue("Curve on Mesh");
        feature->Source.setValue(source);
        feature->AnchorFacets.setValues(facets);
        feature->AnchorWeights.setValues(weights);
        feature->ProjectionDirections.setValues(d_ptr->projectionDirections);
        feature->Closed.setValue(d_ptr->wireClosed);
        feature->Approximate.setValue(d_ptr->approximate);
        feature->MaximumDegree.setValue(d_ptr->par.maxDegree);
        feature->Continuity.setValue(continuityIndex(d_ptr->par.cont));
        feature->Tolerance.setValue(d_ptr->par.tol3d);
        feature->SplitAngle.setValue(d_ptr->splitAngle);
        document->recompute();
        if (feature->Shape.getShape().isNull() || !feature->Shape.getShape().isValid()
            || feature->isError()) {
            throw Base::RuntimeError(
                feature->isError() ? feature->getStatusString() : "Curve projection produced no geometry"
            );
        }
        MeshGui::createSourcePreservingOutputGroup(
            *document,
            {source},
            {feature},
            "CurvesOnMesh",
            "Curves on Mesh",
            "Create curves on mesh"
        );
        if (!transaction.commit()) {
            return;
        }
    }
    catch (const Base::Exception& error) {
        Base::Console().error("Curve on mesh failed: %s\n", error.what());
        return;
    }
    catch (...) {
        Base::Console().error("Curve on mesh failed because of an unknown error\n");
        return;
    }

    d_ptr->curve->clearVertex();
    d_ptr->curve->clearPoints();

    d_ptr->pickedPoints.clear();
    d_ptr->projectionDirections.clear();
    d_ptr->cutLines.clear();
    d_ptr->wireClosed = false;

    disableCallback();
    d_ptr->clearTarget();
}

void CurveOnMeshHandler::onCloseWire()
{
    if (d_ptr->wireClosed || d_ptr->pickedPoints.size() < 3) {
        return;
    }

    closeWire();
}

void CurveOnMeshHandler::onClear()
{
    d_ptr->curve->clearVertex();
    d_ptr->curve->clearPoints();

    d_ptr->pickedPoints.clear();
    d_ptr->projectionDirections.clear();
    d_ptr->cutLines.clear();
    d_ptr->wireClosed = false;
    d_ptr->clearTarget();
}

void CurveOnMeshHandler::onCancel()
{
    d_ptr->curve->clearVertex();
    d_ptr->curve->clearPoints();

    d_ptr->pickedPoints.clear();
    d_ptr->cutLines.clear();
    d_ptr->wireClosed = false;

    disableCallback();
    d_ptr->clearTarget();
}

void CurveOnMeshHandler::enableCallback(Gui::View3DInventor* v)
{
    if (v && !d_ptr->viewer) {
        d_ptr->viewer = v;
        Gui::View3DInventorViewer* view3d = d_ptr->viewer->getViewer();
        Gui::Document* guiDocument = view3d->getDocument();
        d_ptr->document = guiDocument
            ? std::make_unique<App::DocumentWeakPtrT>(guiDocument->getDocument())
            : nullptr;
        if (!d_ptr->document || !**d_ptr->document) {
            d_ptr->viewer = nullptr;
            return;
        }
        view3d->addEventCallback(SoEvent::getClassTypeId(), Private::vertexCallback, this);
        view3d->addViewProvider(d_ptr->curve);
        view3d->setEditing(true);

        view3d->setEditingCursor(d_ptr->editcursor);

        d_ptr->curve->setDisplayMode("Point");
    }
}

void CurveOnMeshHandler::disableCallback()
{
    if (d_ptr->viewer) {
        Gui::View3DInventorViewer* view3d = d_ptr->viewer->getViewer();
        view3d->setEditing(false);
        view3d->removeViewProvider(d_ptr->curve);
        view3d->removeEventCallback(SoEvent::getClassTypeId(), Private::vertexCallback, this);
    }
    d_ptr->viewer = nullptr;
}

std::vector<SbVec3f> CurveOnMeshHandler::getVertexes() const
{
    std::vector<SbVec3f> pts;
    pts.reserve(d_ptr->pickedPoints.size());
    for (const auto& it : d_ptr->pickedPoints) {
        pts.push_back(it.point);
    }
    return pts;
}

std::vector<SbVec3f> CurveOnMeshHandler::getPoints() const
{
    std::vector<SbVec3f> pts;
    for (auto it = d_ptr->cutLines.begin(); it != d_ptr->cutLines.end(); ++it) {
        std::vector<SbVec3f> segm = d_ptr->convert(*it);
        pts.insert(pts.end(), segm.begin(), segm.end());
    }
    return pts;
}

bool CurveOnMeshHandler::tryCloseWire(const SbVec3f& p) const
{
    if (d_ptr->pickedPoints.size() >= 3) {
        Private::PickedPoint first = d_ptr->pickedPoints.front();
        // if the distance of the first and last points is small enough (~1mm)
        // the curve can be closed.
        float len = (first.point - p).length();
        if (len < d_ptr->distance) {
            return true;
        }
    }

    return false;
}

void CurveOnMeshHandler::closeWire()
{
    Private::PickedPoint pick = d_ptr->pickedPoints.front();
    if (d_ptr->projectLineOnMesh(pick)) {
        d_ptr->curve->setPoints(getPoints());
        d_ptr->wireClosed = true;
    }
}

void CurveOnMeshHandler::Private::vertexCallback(void* ud, SoEventCallback* cb)
{
    Gui::View3DInventorViewer* view = static_cast<Gui::View3DInventorViewer*>(cb->getUserData());
    const SoEvent* ev = cb->getEvent();
    if (ev->getTypeId() == SoMouseButtonEvent::getClassTypeId()) {
        // set as handled
        cb->setHandled();

        const SoMouseButtonEvent* mbe = static_cast<const SoMouseButtonEvent*>(ev);
        if (mbe->getButton() == SoMouseButtonEvent::BUTTON1
            && mbe->getState() == SoButtonEvent::DOWN) {
            const SoPickedPoint* pp = cb->getPickedPoint();
            if (pp) {
                CurveOnMeshHandler* self = static_cast<CurveOnMeshHandler*>(ud);
                if (!self->d_ptr->wireClosed) {
                    Gui::ViewProvider* vp = view->getViewProviderByPathFromTail(pp->getPath());
                    if (vp && vp->isDerivedFrom<MeshGui::ViewProviderMesh>()) {
                        MeshGui::ViewProviderMesh* mesh = static_cast<MeshGui::ViewProviderMesh*>(vp);
                        App::Document* targetDocument = self->d_ptr->document
                            ? **self->d_ptr->document
                            : nullptr;
                        auto* pickedSource = mesh->getObject<Mesh::Feature>();
                        if (!pickedSource || pickedSource->getDocument() != targetDocument
                            || !MeshGui::isNativeMeshInputActive(pickedSource)) {
                            self->d_ptr->clearTarget();
                            return;
                        }
                        const SoDetail* detail = pp->getDetail();
                        if (detail && detail->getTypeId() == SoFaceDetail::getClassTypeId()) {
                            // get the mesh and build a grid
                            auto* target = self->d_ptr->mesh ? self->d_ptr->mesh->get() : nullptr;
                            auto* source = target ? target->getObject<Mesh::Feature>() : nullptr;
                            if (source
                                && (source->getDocument() != targetDocument
                                    || !MeshGui::isNativeMeshInputActive(source)
                                    || !sameMeshState(source->Mesh.getValue(), self->d_ptr->sourceMesh)
                                )) {
                                self->d_ptr->clearTarget();
                                target = nullptr;
                            }
                            if (!target) {
                                self->d_ptr->clearTarget();
                                self->d_ptr->mesh
                                    = std::make_unique<Gui::WeakPtrT<MeshGui::ViewProviderMesh>>(mesh);
                                if (!self->d_ptr->createGrid()) {
                                    self->d_ptr->clearTarget();
                                    return;
                                }
                            }
                            else if (target != mesh) {
                                Gui::getMainWindow()->statusBar()->showMessage(
                                    tr("Wrong mesh selected")
                                );
                                return;
                            }

                            const SbVec3f& p = pp->getPoint();
                            const SbVec3f& n = pp->getNormal();

                            Private::PickedPoint pick;
                            pick.facet = static_cast<const SoFaceDetail*>(detail)->getFaceIndex();
                            pick.point = p;
                            pick.normal = n;

                            if (self->d_ptr->pickedPoints.empty()) {
                                self->d_ptr->pickedPoints.push_back(pick);
                                self->d_ptr->curve->addVertex(p);
                            }
                            else {
                                // check to auto-complete the curve
                                if (self->tryCloseWire(p)) {
                                    self->closeWire();
                                }
                                else if (self->d_ptr->projectLineOnMesh(pick)) {
                                    self->d_ptr->curve->setPoints(self->getPoints());
                                    self->d_ptr->pickedPoints.push_back(pick);
                                    self->d_ptr->curve->addVertex(p);
                                }
                            }
                        }
                    }
                    // try to 'complete' the curve
                    else if (vp && vp->isDerivedFrom<ViewProviderCurveOnMesh>()) {
                        const SbVec3f& p = pp->getPoint();
                        if (self->tryCloseWire(p)) {
                            self->closeWire();
                        }
                    }
                }
            }
            else {
                Gui::getMainWindow()->statusBar()->showMessage(tr("No point was selected"));
            }
        }
        else if (mbe->getButton() == SoMouseButtonEvent::BUTTON2
                 && mbe->getState() == SoButtonEvent::UP) {
            CurveOnMeshHandler* self = static_cast<CurveOnMeshHandler*>(ud);
            QTimer::singleShot(100, self, &CurveOnMeshHandler::onContextMenu);
        }
    }
}

void CurveOnMeshHandler::recomputeDocument()
{
    if (!d_ptr->viewer) {
        return;
    }

    Gui::View3DInventorViewer* view3d = d_ptr->viewer->getViewer();
    Gui::Document* guiDocument = view3d ? view3d->getDocument() : nullptr;
    App::Document* document = guiDocument ? guiDocument->getDocument() : nullptr;
    App::Document* targetDocument = d_ptr->document ? **d_ptr->document : nullptr;
    if (document && document == targetDocument) {
        document->recompute();
    }
}

#include "moc_CurveOnMesh.cpp"
