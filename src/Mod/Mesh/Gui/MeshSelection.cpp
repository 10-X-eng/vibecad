// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2013 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <algorithm>
#include <QBitmap>

#include <Inventor/SbBox2s.h>
#include <Inventor/SoPickedPoint.h>
#include <Inventor/details/SoFaceDetail.h>
#include <Inventor/events/SoMouseButtonEvent.h>
#include <Inventor/nodes/SoCamera.h>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/MouseSelection.h>
#include <Gui/Navigation/NavigationStyle.h>
#include <Gui/Utilities.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/App/Core/MeshKernel.h>
#include <Mod/Mesh/App/Core/Iterator.h>
#include <Mod/Mesh/App/Core/TopoAlgorithm.h>

#include "MeshSelection.h"
#include "CommandGuard.h"
#include "ParametricMeshFilter.h"
#include "ViewProvider.h"


using namespace MeshGui;

#define CROSS_WIDTH 16
#define CROSS_HEIGHT 16
#define CROSS_HOT_X 7
#define CROSS_HOT_Y 7

// NOLINTBEGIN
// clang-format off
unsigned char MeshSelection::cross_bitmap[] = {
    0xc0, 0x03, 0x40, 0x02, 0x40, 0x02, 0x40, 0x02,
    0x40, 0x02, 0x40, 0x02, 0x7f, 0xfe, 0x01, 0x80,
    0x01, 0x80, 0x7f, 0xfe, 0x40, 0x02, 0x40, 0x02,
    0x40, 0x02, 0x40, 0x02, 0x40, 0x02, 0xc0, 0x03};

unsigned char MeshSelection::cross_mask_bitmap[] = {
    0xc0, 0x03, 0xc0, 0x03, 0xc0, 0x03, 0xc0, 0x03,
    0xc0, 0x03, 0xc0, 0x03, 0xff, 0xff, 0xff, 0xff,
    0xff, 0xff, 0xff, 0xff, 0xc0, 0x03, 0xc0, 0x03,
    0xc0, 0x03, 0xc0, 0x03, 0xc0, 0x03, 0xc0, 0x03};
// clang-format on
// NOLINTEND

MeshSelection::MeshSelection()
{
    setCallback(selectGLCallback);
}

MeshSelection::~MeshSelection()
{
    if (this->activeCB) {
        Gui::View3DInventorViewer* viewer = this->getViewer();
        if (viewer) {
            stopInteractiveCallback(viewer);
        }
    }
}

void MeshSelection::setEnabledViewerSelection(bool on)
{
    Gui::View3DInventorViewer* viewer = this->getViewer();
    if (viewer) {
        viewer->setSelectionEnabled(on);
    }
}

void MeshSelection::setCallback(SoEventCallbackCB* cb)
{
    selectionCB = cb;
}

void MeshSelection::setObjects(const std::vector<Gui::SelectionObject>& obj)
{
    meshObjects.clear();
    objectsBound = true;

    App::Document* targetDocument = nullptr;
    if (!obj.empty()) {
        meshObjects.reserve(obj.size());
        for (const auto& selection : obj) {
            const auto* selected = freecad_cast<const Mesh::Feature*>(selection.getObject());
            if (!selected || !MeshGui::isNativeMeshInputActive(selected)) {
                continue;
            }
            auto* object = const_cast<Mesh::Feature*>(selected);
            if (!targetDocument) {
                targetDocument = object->getDocument();
            }
            else if (object->getDocument() != targetDocument) {
                meshObjects.clear();
                return;
            }
            meshObjects.emplace_back(object);
        }
    }
    else {
        targetDocument = App::GetApplication().getActiveDocument();
        if (targetDocument) {
            auto objects = targetDocument->getObjectsOfType(Mesh::Feature::getClassTypeId());
            meshObjects.reserve(objects.size());
            for (auto* object : objects) {
                if (MeshGui::isNativeMeshInputActive(object)) {
                    meshObjects.emplace_back(object);
                }
            }
        }
    }

    if (!viewerBound && targetDocument) {
        auto* guiDocument = Gui::Application::Instance->getDocument(targetDocument);
        auto* view = guiDocument ? guiDocument->getActiveView() : nullptr;
        auto* view3d = dynamic_cast<Gui::View3DInventor*>(view);
        ivViewer = view3d ? view3d->getViewer() : nullptr;
        viewerBound = true;
    }
}

std::vector<App::DocumentObject*> MeshSelection::getObjects() const
{
    std::vector<App::DocumentObject*> objs;
    if (objectsBound) {
        objs.reserve(meshObjects.size());
        for (const auto& target : meshObjects) {
            auto* object = target.get<Mesh::Feature>();
            if (MeshGui::isNativeMeshInputActive(object)) {
                objs.push_back(object);
            }
        }
    }
    // get all objects of the active document
    else {
        App::Document* doc = App::GetApplication().getActiveDocument();
        if (doc) {
            objs = doc->getObjectsOfType(Mesh::Feature::getClassTypeId());
            std::erase_if(objs, [](const App::DocumentObject* object) {
                return !MeshGui::isNativeMeshInputActive(object);
            });
        }
    }

    return objs;
}

std::list<ViewProviderMesh*> MeshSelection::getViewProviders() const
{
    std::vector<App::DocumentObject*> objs = getObjects();
    std::list<ViewProviderMesh*> vps;
    for (auto obj : objs) {
        if (obj->isDerivedFrom<Mesh::Feature>()) {
            Gui::ViewProvider* vp = Gui::Application::Instance->getViewProvider(obj);
            auto* meshView = dynamic_cast<ViewProviderMesh*>(vp);
            if (meshView && meshView->isVisible()) {
                vps.push_back(meshView);
            }
        }
    }

    return vps;
}

void MeshSelection::setViewer(Gui::View3DInventorViewer* v)
{
    ivViewer = v;
    viewerBound = true;
}

Gui::View3DInventorViewer* MeshSelection::getViewer() const
{
    // Once a tool is bound to a viewer, never fall through to an unrelated
    // active document if that viewer is later destroyed.
    if (viewerBound) {
        return ivViewer;
    }

    Gui::Document* doc = Gui::Application::Instance->activeDocument();
    if (!doc) {
        return nullptr;
    }
    Gui::MDIView* view = doc->getActiveView();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return viewer;
    }

    return nullptr;
}

void MeshSelection::startInteractiveCallback(Gui::View3DInventorViewer* viewer, SoEventCallbackCB* cb)
{
    if (this->activeCB) {
        return;
    }
    viewer->setEditing(true);
    viewer->addEventCallback(SoMouseButtonEvent::getClassTypeId(), cb, this);
    this->activeCB = cb;
}

void MeshSelection::stopInteractiveCallback(Gui::View3DInventorViewer* viewer)
{
    if (!this->activeCB) {
        return;
    }
    viewer->setEditing(false);
    viewer->removeEventCallback(SoMouseButtonEvent::getClassTypeId(), this->activeCB, this);
    this->activeCB = nullptr;
}

void MeshSelection::prepareFreehandSelection(bool add, SoEventCallbackCB* cb)
{
    // a rubberband to select a rectangle area of the meshes
    Gui::View3DInventorViewer* viewer = this->getViewer();
    if (viewer) {
        // Note: It is possible that the mouse selection mode can be stopped
        // but then the callback function is still active.
        stopInteractiveCallback(viewer);
        startInteractiveCallback(viewer, cb);
        viewer->navigationStyle()->stopSelection();

        // set cross cursor
        Gui::FreehandSelection* freehand = new Gui::FreehandSelection();
        freehand->setClosed(true);
        freehand->setColor(1.0F, 0.0F, 0.0F);
        freehand->setLineWidth(3.0F);
        viewer->navigationStyle()->startSelection(freehand);

        auto setComponentCursor = [=]() {
            QBitmap cursor = QBitmap::fromData(QSize(CROSS_WIDTH, CROSS_HEIGHT), cross_bitmap);
            QBitmap mask = QBitmap::fromData(QSize(CROSS_WIDTH, CROSS_HEIGHT), cross_mask_bitmap);
#if defined(Q_OS_WIN32)
            double dpr = viewer->devicePixelRatio();
            cursor.setDevicePixelRatio(dpr);
            mask.setDevicePixelRatio(dpr);
#endif
            QCursor custom(cursor, mask, CROSS_HOT_X, CROSS_HOT_Y);
            viewer->setComponentCursor(custom);
        };

        QObject::connect(viewer, &Gui::View3DInventorViewer::devicePixelRatioChanged, setComponentCursor);
        setComponentCursor();
        this->addToSelection = add;
    }
}

void MeshSelection::startSelection()
{
    prepareFreehandSelection(true, selectionCB);
}

void MeshSelection::startDeselection()
{
    prepareFreehandSelection(false, selectionCB);
}

void MeshSelection::stopSelection()
{
    Gui::View3DInventorViewer* viewer = getViewer();
    if (viewer) {
        stopInteractiveCallback(viewer);
        viewer->navigationStyle()->stopSelection();
    }
}

void MeshSelection::fullSelection()
{
    // select the complete meshes
    std::list<ViewProviderMesh*> views = getViewProviders();
    for (auto view : views) {
        Mesh::Feature* mf = view->getObject<Mesh::Feature>();
        const Mesh::MeshObject* mo = mf->Mesh.getValuePtr();
        std::vector<Mesh::FacetIndex> faces(mo->countFacets());
        std::generate(faces.begin(), faces.end(), Base::iotaGen<Mesh::FacetIndex>(0));
        view->addSelection(faces);
    }
}

void MeshSelection::clearSelection()
{
    std::list<ViewProviderMesh*> views = getViewProviders();
    for (auto view : views) {
        view->clearSelection();
    }
}

bool MeshSelection::deleteSelection()
{
    struct Deletion
    {
        Deletion(ViewProviderMesh* view, Mesh::Feature* feature, std::vector<Mesh::FacetIndex> facets)
            : view(view)
            , feature(feature)
            , facets(std::move(facets))
        {}

        ViewProviderMesh* view;
        App::DocumentObjectWeakPtrT feature;
        std::vector<Mesh::FacetIndex> facets;
    };
    std::vector<Deletion> deletions;
    std::list<ViewProviderMesh*> views = getViewProviders();
    for (auto* view : views) {
        auto* feature = view ? view->getObject<Mesh::Feature>() : nullptr;
        if (!feature) {
            continue;
        }
        std::vector<Mesh::FacetIndex> facets;
        feature->Mesh.getValue().getFacetsFromSelection(facets);
        if (!facets.empty()) {
            deletions.emplace_back(view, feature, std::move(facets));
        }
    }
    if (deletions.empty()) {
        return false;  // nothing todo
    }

    App::Document* document = deletions.front().feature.get<Mesh::Feature>()->getDocument();
    if (!MeshGui::hasCleanNativeMutationBoundary(document)) {
        return false;
    }

    std::vector<MeshGui::ParametricMeshFilterTarget> operations;
    operations.reserve(deletions.size());
    for (auto& deletion : deletions) {
        auto* feature = deletion.feature.get<Mesh::Feature>();
        if (!feature || feature->getDocument() != document
            || !MeshGui::isNativeMeshInputActive(feature)) {
            return false;
        }
        std::vector<long> facets(deletion.facets.begin(), deletion.facets.end());
        if (deletion.view) {
            deletion.view->clearSelection();
        }
        operations.push_back(
            MeshGui::ParametricMeshFilterTarget {
                feature,
                [feature, facets = std::move(facets)](App::DocumentObject& object) {
                    auto& edit = static_cast<Mesh::FacetEdit&>(object);
                    edit.Action.setValue("Remove Facets");
                    edit.Indices.setValues(facets);
                    edit.AcceptedSource.setValue(feature->Mesh.getValue());
                },
            }
        );
    }

    try {
        MeshGui::createParametricMeshFilters(
            *document,
            operations,
            MeshGui::ParametricMeshFilterSpec {
                "Mesh::FacetEdit",
                "RemoveFacets",
                "Remove Mesh Facets",
                "Delete selection",
                true,
                false,
            }
        );
        return true;
    }
    catch (const Base::Exception& error) {
        Base::Console().error("Mesh facet removal failed: %s\n", error.what());
        return false;
    }
}

bool MeshSelection::deleteSelectionBorder()
{
    // delete all selected faces
    bool deletion = false;
    std::list<ViewProviderMesh*> views = getViewProviders();
    for (auto view : views) {
        Mesh::Feature* mf = view->getObject<Mesh::Feature>();

        // mark the selected facet as visited
        std::vector<Mesh::FacetIndex> selection;
        std::vector<Mesh::FacetIndex> remove;
        std::set<Mesh::PointIndex> borderPoints;
        MeshCore::MeshAlgorithm meshAlg(mf->Mesh.getValue().getKernel());
        meshAlg.GetFacetsFlag(selection, MeshCore::MeshFacet::SELECTED);
        meshAlg.GetBorderPoints(selection, borderPoints);
        std::vector<Mesh::PointIndex> border;
        border.insert(border.begin(), borderPoints.begin(), borderPoints.end());

        meshAlg.ResetFacetFlag(MeshCore::MeshFacet::VISIT);
        meshAlg.SetFacetsFlag(selection, MeshCore::MeshFacet::VISIT);
        meshAlg.ResetPointFlag(MeshCore::MeshPoint::VISIT);
        meshAlg.SetPointsFlag(border, MeshCore::MeshPoint::VISIT);

        // collect neighbour facets that are not selected and that share a border point
        const MeshCore::MeshPointArray& points = mf->Mesh.getValue().getKernel().GetPoints();
        const MeshCore::MeshFacetArray& faces = mf->Mesh.getValue().getKernel().GetFacets();
        unsigned long numFaces = faces.size();
        for (unsigned long i = 0; i < numFaces; i++) {
            const MeshCore::MeshFacet& face = faces[i];
            if (!face.IsFlag(MeshCore::MeshFacet::VISIT)) {
                for (Mesh::PointIndex ptIndex : face._aulPoints) {
                    if (points[ptIndex].IsFlag(MeshCore::MeshPoint::VISIT)) {
                        remove.push_back(i);
                        break;
                    }
                }
            }
        }

        if (!remove.empty()) {
            deletion = true;
            // remove duplicates
            std::sort(remove.begin(), remove.end());
            remove.erase(std::unique(remove.begin(), remove.end()), remove.end());

            view->setSelection(remove);
        }
    }

    return deletion && deleteSelection();
}

void MeshSelection::invertSelection()
{
    std::list<ViewProviderMesh*> views = getViewProviders();
    for (auto view : views) {
        view->invertSelection();
    }
}

void MeshSelection::selectComponent(int size)
{
    std::list<ViewProviderMesh*> views = getViewProviders();
    for (auto view : views) {
        Mesh::Feature* mf = view->getObject<Mesh::Feature>();
        const Mesh::MeshObject* mo = mf->Mesh.getValuePtr();

        std::vector<std::vector<Mesh::FacetIndex>> segm;
        MeshCore::MeshComponents comp(mo->getKernel());
        comp.SearchForComponents(MeshCore::MeshComponents::OverEdge, segm);

        std::vector<Mesh::FacetIndex> faces;
        for (const auto& jt : segm) {
            if (jt.size() < (Mesh::FacetIndex)size) {
                faces.insert(faces.end(), jt.begin(), jt.end());
            }
        }

        view->addSelection(faces);
    }
}

void MeshSelection::deselectComponent(int size)
{
    std::list<ViewProviderMesh*> views = getViewProviders();
    for (auto view : views) {
        Mesh::Feature* mf = view->getObject<Mesh::Feature>();
        const Mesh::MeshObject* mo = mf->Mesh.getValuePtr();

        std::vector<std::vector<Mesh::FacetIndex>> segm;
        MeshCore::MeshComponents comp(mo->getKernel());
        comp.SearchForComponents(MeshCore::MeshComponents::OverEdge, segm);

        std::vector<Mesh::FacetIndex> faces;
        for (const auto& jt : segm) {
            if (jt.size() > (Mesh::FacetIndex)size) {
                faces.insert(faces.end(), jt.begin(), jt.end());
            }
        }

        view->removeSelection(faces);
    }
}

void MeshSelection::selectTriangle()
{
    this->addToSelection = true;

    Gui::View3DInventorViewer* viewer = this->getViewer();
    if (viewer) {
        stopInteractiveCallback(viewer);
        viewer->navigationStyle()->stopSelection();
        startInteractiveCallback(viewer, pickFaceCallback);
        viewer->setEditingCursor(QCursor(Qt::PointingHandCursor));
    }
}

void MeshSelection::deselectTriangle()
{
    this->addToSelection = false;

    Gui::View3DInventorViewer* viewer = this->getViewer();
    if (viewer) {
        stopInteractiveCallback(viewer);
        viewer->navigationStyle()->stopSelection();
        startInteractiveCallback(viewer, pickFaceCallback);
        viewer->setEditingCursor(QCursor(Qt::PointingHandCursor));
    }
}

void MeshSelection::setCheckOnlyPointToUserTriangles(bool on)
{
    onlyPointToUserTriangles = on;
}

bool MeshSelection::isCheckedOnlyPointToUserTriangles() const
{
    return onlyPointToUserTriangles;
}

void MeshSelection::setCheckOnlyVisibleTriangles(bool on)
{
    onlyVisibleTriangles = on;
}

bool MeshSelection::isCheckedOnlyVisibleTriangles() const
{
    return onlyVisibleTriangles;
}

void MeshSelection::setAddComponentOnClick(bool on)
{
    addComponent = on;
}

void MeshSelection::setRemoveComponentOnClick(bool on)
{
    removeComponent = on;
}

void MeshSelection::selectGLCallback(void* ud, SoEventCallback* n)
{
    // When this callback function is invoked we must leave the edit mode
    Gui::View3DInventorViewer* view = static_cast<Gui::View3DInventorViewer*>(n->getUserData());
    MeshSelection* self = static_cast<MeshSelection*>(ud);
    self->stopInteractiveCallback(view);
    n->setHandled();
    std::vector<SbVec2f> polygon = view->getGLPolygon();
    if (polygon.size() < 3) {
        return;
    }
    if (polygon.front() != polygon.back()) {
        polygon.push_back(polygon.front());
    }

    SbVec3f pnt;
    SbVec3f dir;
    view->getNearPlane(pnt, dir);
    Base::Vector3f normal(dir[0], dir[1], dir[2]);

    std::list<ViewProviderMesh*> views = self->getViewProviders();
    for (auto vp : views) {
        std::vector<Mesh::FacetIndex> faces;
        const Mesh::MeshObject& mesh = vp->getObject<Mesh::Feature>()->Mesh.getValue();
        const MeshCore::MeshKernel& kernel = mesh.getKernel();

        // simply get all triangles under the polygon
        SoCamera* cam = view->getSoRenderManager()->getCamera();
        SbViewVolume vv = cam->getViewVolume();
        Gui::ViewVolumeProjection proj(vv);

        Base::Placement plm = vp->getObject<Mesh::Feature>()->Placement.getValue();
        proj.setTransform(plm.toMatrix());
        vp->getFacetsFromPolygon(polygon, proj, true, faces);

        if (self->onlyVisibleTriangles) {
            const SbVec2s& sz = view->getSoRenderManager()->getViewportRegion().getWindowSize();
            short width {};
            short height {};
            sz.getValue(width, height);
            std::vector<SbVec2s> pixelPoly = view->getPolygon();
            SbBox2s rect;
            for (const auto& p : pixelPoly) {
                rect.extendBy(SbVec2s(p[0], height - p[1]));
            }
            std::vector<Mesh::FacetIndex> rf;
            rf.swap(faces);
            std::vector<Mesh::FacetIndex> vf = vp->getVisibleFacetsAfterZoom(
                rect,
                view->getSoRenderManager()->getViewportRegion(),
                view->getSoRenderManager()->getCamera()
            );

            // get common facets of the viewport and the visible one
            std::sort(vf.begin(), vf.end());
            std::sort(rf.begin(), rf.end());
            std::back_insert_iterator<std::vector<Mesh::FacetIndex>> biit(faces);
            std::set_intersection(vf.begin(), vf.end(), rf.begin(), rf.end(), biit);
        }

        // if set filter out all triangles which do not point into user direction
        if (self->onlyPointToUserTriangles) {
            std::vector<Mesh::FacetIndex> screen;
            screen.reserve(faces.size());
            MeshCore::MeshFacetIterator it_f(kernel);
            for (Mesh::FacetIndex face : faces) {
                it_f.Set(face);
                if (it_f->GetNormal() * normal > 0.0F) {
                    screen.push_back(face);
                }
            }

            faces.swap(screen);
        }

        if (self->addToSelection) {
            vp->addSelection(faces);
        }
        else {
            vp->removeSelection(faces);
        }
    }

    view->redraw();
}

void MeshSelection::pickFaceCallback(void* ud, SoEventCallback* n)
{
    // handle only mouse button events
    if (n->getEvent()->isOfType(SoMouseButtonEvent::getClassTypeId())) {
        const SoMouseButtonEvent* mbe = static_cast<const SoMouseButtonEvent*>(n->getEvent());
        Gui::View3DInventorViewer* view = static_cast<Gui::View3DInventorViewer*>(n->getUserData());

        // Mark all incoming mouse button events as handled, especially, to deactivate the selection
        // node
        n->getAction()->setHandled();
        if (mbe->getButton() == SoMouseButtonEvent::BUTTON1
            && mbe->getState() == SoButtonEvent::DOWN) {
            const SoPickedPoint* point = n->getPickedPoint();
            if (!point) {
                Base::Console().message("No facet picked.\n");
                return;
            }

            n->setHandled();

            // By specifying the indexed mesh node 'pcFaceSet' we make sure that the picked point is
            // really from the mesh we render and not from any other geometry
            Gui::ViewProvider* vp = view->getViewProviderByPathFromTail(point->getPath());
            if (!vp || !vp->isDerivedFrom<ViewProviderMesh>()) {
                return;
            }
            const auto mesh = static_cast<ViewProviderMesh*>(vp);
            const auto self = static_cast<MeshSelection*>(ud);
            if (std::list<ViewProviderMesh*> views = self->getViewProviders();
                std::ranges::find(views, mesh) == views.end()) {
                return;
            }
            const SoDetail* detail = point->getDetail(/*mesh->getShapeNode()*/);
            if (detail && detail->getTypeId() == SoFaceDetail::getClassTypeId()) {
                // get the boundary to the picked facet
                Mesh::FacetIndex uFacet = static_cast<const SoFaceDetail*>(detail)->getFaceIndex();
                if (self->addToSelection) {
                    if (self->addComponent) {
                        mesh->selectComponent(uFacet);
                    }
                    else {
                        mesh->selectFacet(uFacet);
                    }
                }
                else {
                    if (self->removeComponent) {
                        mesh->deselectComponent(uFacet);
                    }
                    else {
                        mesh->deselectFacet(uFacet);
                    }
                }
            }
        }
    }
}
