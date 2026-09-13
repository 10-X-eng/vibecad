// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2011 Juergen Riegel <juergen.riegel@web.de>             *
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

#include <Bnd_Box.hxx>
#include <BRep_Tool.hxx>
#include <BRepTools.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_MakeVertex.hxx>
#include <BRepExtrema_DistShapeShape.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <gp_Trsf.hxx>
#include <Precision.hxx>
#include <Poly_Array1OfTriangle.hxx>
#include <Poly_Polygon3D.hxx>
#include <Poly_PolygonOnTriangulation.hxx>
#include <Poly_Triangulation.hxx>
#include <Standard_Version.hxx>
#include <TColgp_Array1OfDir.hxx>
#include <TColgp_Array1OfPnt.hxx>
#include <TColStd_Array1OfInteger.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Vertex.hxx>
#include <TopTools_IndexedMapOfShape.hxx>

#include <QAction>
#include <QApplication>
#include <QByteArray>
#include <QCoreApplication>
#include <QEventLoop>
#include <QMenu>
#include <QMetaObject>
#include <atomic>
#include <deque>
#include <map>
#include <memory>
#include <sstream>

#include <Inventor/SoPickedPoint.h>
#include <Inventor/details/SoFaceDetail.h>
#include <Inventor/details/SoLineDetail.h>
#include <Inventor/details/SoPointDetail.h>
#include <Inventor/errors/SoDebugError.h>
#include <Inventor/nodes/SoCoordinate3.h>
#include <Inventor/nodes/SoDrawStyle.h>
#include <Inventor/nodes/SoMaterial.h>
#include <Inventor/nodes/SoMaterialBinding.h>
#include <Inventor/nodes/SoNormal.h>
#include <Inventor/nodes/SoNormalBinding.h>
#include <Inventor/nodes/SoPolygonOffset.h>
#include <Inventor/nodes/SoSeparator.h>
#include <Inventor/nodes/SoShapeHints.h>

#include <boost/algorithm/string/predicate.hpp>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Parameter.h>
#include <Base/Sequencer.h>
#include <Base/TimeInfo.h>
#include <Base/Tools.h>

#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Control.h>
#include <Gui/FrameBudget.h>
#include <Gui/ProgressBar.h>
#include <Gui/Selection/SoFCSelectionAction.h>
#include <Gui/Selection/SoFCUnifiedSelection.h>
#include <Gui/ViewParams.h>
#include <Gui/Utilities.h>

#include <Mod/Part/App/ShapeMapHasher.h>
#include <Mod/Part/App/RenderMesh.h>
#include <Mod/Part/App/Tools.h>

#include "ViewProviderExt.h"
#include "ViewProviderPartExtPy.h"
#include "SoBrepEdgeSet.h"
#include "SoBrepFaceSet.h"
#include "SoBrepPointSet.h"
#include "TaskFaceAppearances.h"


FC_LOG_LEVEL_INIT("Part", true, true)

using namespace PartGui;

PROPERTY_SOURCE(PartGui::ViewProviderPartExt, Gui::ViewProviderGeometryObject)


//**************************************************************************
// Construction/Destruction

App::PropertyFloatConstraint::Constraints ViewProviderPartExt::sizeRange = {1.0, 64.0, 1.0};
App::PropertyFloatConstraint::Constraints ViewProviderPartExt::tessRange = {0.01, 100.0, 0.01};
App::PropertyQuantityConstraint::Constraints ViewProviderPartExt::angDeflectionRange
    = {1.0, 180.0, 0.05};
const char* ViewProviderPartExt::LightingEnums[] = {"One side", "Two side", nullptr};
const char* ViewProviderPartExt::DrawStyleEnums[] = {"Solid", "Dashed", "Dotted", "Dashdot", nullptr};

namespace PartGui
{
// Names locate documents; UIDs and provider instances authorize adoption.
// A queued request never owns a document or a view provider.
struct DeferredVisual
{
    std::string documentName;
    std::string documentUid;
    long objectId;
    std::uint64_t instanceId;

    explicit DeferredVisual(const ViewProviderPartExt& view)
        : documentName(view.getObject()->getDocument()->getName()),
          documentUid(view.getObject()->getDocument()->Uid.getValueStr()),
          objectId(view.getObject()->getID()), instanceId(view.visualInstanceId)
    {}

    bool matches(const ViewProviderPartExt* view) const
    {
        return view && view->visualInstanceId == instanceId;
    }
};
}

namespace
{
std::atomic_uint64_t nextVisualInstanceId {1};

std::pair<std::uint64_t, std::uint64_t> appearanceStamp(
    const SoMaterial* material, const SoMaterialBinding* binding)
{
    return {material->getNodeId(), binding->getNodeId()};
}

void copyRenderMesh(
    const Part::RenderMesh& mesh,
    SoCoordinate3* coords,
    SoBrepFaceSet* faceset,
    SoNormal* norm,
    SoBrepEdgeSet* lineset,
    SoBrepPointSet* nodeset
)
{
    const auto coinSize = [](std::size_t size) {
        if (size > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
            throw Base::MemoryException("Render mesh exceeds Coin field capacity");
        }
        return static_cast<int>(size);
    };

    coords->point.setNum(coinSize(mesh.vertexCount()));
    norm->vector.setNum(coinSize(mesh.normalCount()));
    faceset->coordIndex.setNum(coinSize(mesh.triangleIndices.size()));
    faceset->partIndex.setNum(coinSize(mesh.faceTriangleCounts.size()));
    lineset->coordIndex.setNum(coinSize(mesh.lineIndices.size()));

    auto* vertices = coords->point.startEditing();
    for (std::size_t index = 0; index < mesh.vertexCount(); ++index) {
        const std::size_t offset = index * 3;
        vertices[index].setValue(
            mesh.vertices[offset],
            mesh.vertices[offset + 1],
            mesh.vertices[offset + 2]
        );
    }
    auto* normals = norm->vector.startEditing();
    for (std::size_t index = 0; index < mesh.normalCount(); ++index) {
        const std::size_t offset = index * 3;
        normals[index].setValue(
            mesh.normals[offset],
            mesh.normals[offset + 1],
            mesh.normals[offset + 2]
        );
    }
    std::copy(
        mesh.triangleIndices.begin(),
        mesh.triangleIndices.end(),
        faceset->coordIndex.startEditing()
    );
    std::copy(
        mesh.faceTriangleCounts.begin(),
        mesh.faceTriangleCounts.end(),
        faceset->partIndex.startEditing()
    );
    std::copy(
        mesh.lineIndices.begin(),
        mesh.lineIndices.end(),
        lineset->coordIndex.startEditing()
    );
    nodeset->startIndex.setValue(mesh.vertexStart);

    coords->point.finishEditing();
    norm->vector.finishEditing();
    faceset->coordIndex.finishEditing();
    faceset->partIndex.finishEditing();
    lineset->coordIndex.finishEditing();
}

class VisualUpdateEnd
{
public:
    explicit VisualUpdateEnd(App::Document* document)
        : documentName(document ? document->getName() : ""),
          documentUid(document ? document->Uid.getValueStr() : "")
    {}

    ~VisualUpdateEnd()
    {
        auto* document = App::GetApplication().getDocument(documentName.c_str());
        if (document && document->Uid.getValueStr() == documentUid) {
            try {
                document->endVisualUpdate();
            }
            catch (const Base::Exception& failure) {
                failure.reportException();
            }
            catch (const std::exception& failure) {
                Base::Console().error("Render presentation release failed: %s\n", failure.what());
            }
            catch (...) {
                Base::Console().error("Render presentation release failed\n");
            }
        }
    }

    VisualUpdateEnd(const VisualUpdateEnd&) = delete;
    VisualUpdateEnd& operator=(const VisualUpdateEnd&) = delete;

private:
    const std::string documentName;
    const std::string documentUid;
};

std::deque<DeferredVisual> deferredVisuals;
// false = queued, true = started. Cancelling a queued request must not consume
// another provider's outstanding completion.
std::map<std::uint64_t, bool> deferredVisualIdentities;
std::size_t deferredVisualOutstanding = 0;
bool deferredVisualRefreshScheduled = false;
bool deferredVisualDispatchPending = false;
bool deferredVisualShutdown = false;
bool deferredVisualShutdownConnected = false;
fastsignals::scoped_connection deferredRestoreIdleConnection;
fastsignals::scoped_connection deferredOpenFinishedConnection;
std::unique_ptr<Base::SequencerLauncher> deferredVisualProgress;
Gui::ProgressBar* deferredVisualProgressBar = nullptr;
int deferredVisualProgressMinimumDuration = -1;

void startDeferredVisualProgress()
{
    if (deferredVisualProgress || deferredVisuals.empty() || Base::Sequencer().isRunning()) {
        return;
    }

    deferredVisualProgressBar = static_cast<Gui::ProgressBar*>(
        Gui::SequencerBar::instance()->getProgressBar());
    deferredVisualProgressMinimumDuration = deferredVisualProgressBar->minimumDuration();
    deferredVisualProgressBar->setMinimumDuration(0);

    const QByteArray text = QApplication::translate(
                                "PartGui::ViewProviderPartExt",
                                "Loading model display\u2026")
                                .toUtf8();
    deferredVisualProgress =
        std::make_unique<Base::SequencerLauncher>(text.constData(), deferredVisuals.size());

}

void advanceDeferredVisualProgress()
{
    if (deferredVisualProgress) {
        deferredVisualProgress->next();
    }
}

void finishDeferredVisualProgress()
{
    deferredVisualProgress.reset();
    if (deferredVisualProgressBar) {
        deferredVisualProgressBar->setMinimumDuration(deferredVisualProgressMinimumDuration);
        deferredVisualProgressBar = nullptr;
        deferredVisualProgressMinimumDuration = -1;
    }
}

void shutdownDeferredVisualRestore()
{
    deferredVisualShutdown = true;
    deferredVisuals.clear();
    deferredVisualIdentities.clear();
    deferredVisualOutstanding = 0;
    deferredVisualRefreshScheduled = false;
    deferredVisualDispatchPending = false;
    deferredRestoreIdleConnection.disconnect();
    deferredOpenFinishedConnection.disconnect();
    finishDeferredVisualProgress();
}

void scheduleNextDeferredVisual();

void connectDeferredVisualShutdown()
{
    if (deferredVisualShutdownConnected || !qApp) {
        return;
    }

    deferredVisualShutdownConnected = true;
    QObject::connect(qApp,
                     &QCoreApplication::aboutToQuit,
                     qApp,
                     shutdownDeferredVisualRestore,
                     Qt::DirectConnection);
    const auto restoreIdle = [] {
        Gui::dispatchToGuiFrame(qApp, [] {
            if (!deferredVisuals.empty()) { scheduleNextDeferredVisual(); }
        });
    };
    deferredRestoreIdleConnection = App::GetApplication().signalRestoreActivityIdle.connect(restoreIdle);
    deferredOpenFinishedConnection = App::GetApplication().signalFinishOpenDocument.connect(restoreIdle);
}

void refreshNextDeferredVisual();

void completeDeferredVisualRestore(std::uint64_t instanceId)
{
    const auto found = deferredVisualIdentities.find(instanceId);
    if (deferredVisualShutdown || found == deferredVisualIdentities.end()) {
        return;
    }
    const bool started = found->second;
    deferredVisualIdentities.erase(found);
    if (started) {
        --deferredVisualOutstanding;
    }
    advanceDeferredVisualProgress();
    if (deferredVisuals.empty() && deferredVisualOutstanding == 0) {
        finishDeferredVisualProgress();
        deferredVisualRefreshScheduled = false;
    }
}

void scheduleNextDeferredVisual()
{
    if (deferredVisualShutdown || !qApp || deferredVisualDispatchPending) {
        return;
    }
    deferredVisualDispatchPending = true;
    if (!Gui::dispatchToGuiFrame(qApp, [] {
        deferredVisualDispatchPending = false;
        refreshNextDeferredVisual();
    })) {
        deferredVisualDispatchPending = false;
        deferredVisualRefreshScheduled = false;
        Base::Console().error("Cannot schedule restored model display: GUI dispatcher unavailable\n");
    }
}

void refreshNextDeferredVisual()
{
    if (deferredVisualShutdown) {
        return;
    }
    if (App::Document::isAnyRestoring()) {
        return; // The actual restore-idle transition schedules the next frame.
    }

    startDeferredVisualProgress();

    Gui::FrameBudget budget;
    while (!deferredVisuals.empty()) {
        if (budget.exhausted()) {
            scheduleNextDeferredVisual();
            return;
        }
        DeferredVisual identity = std::move(deferredVisuals.front());
        deferredVisuals.pop_front();
        if (!deferredVisualIdentities.contains(identity.instanceId)) {
            continue;
        }
        auto* document = App::GetApplication().getDocument(identity.documentName.c_str());
        if (document && document->Uid.getValueStr() != identity.documentUid) {
            document = nullptr;
        }
        if (document && document->testStatus(App::Document::Restoring)) {
            // A document can remain Restoring between worker phases even when
            // no archive scope is active. Do not consume its display request.
            deferredVisuals.push_front(std::move(identity));
            return;
        }
        auto* object = document ? document->getObjectByID(identity.objectId) : nullptr;
        auto* viewProvider = object && Gui::Application::Instance
            ? Gui::Application::Instance->getViewProvider<ViewProviderPartExt>(object)
            : nullptr;
        if (identity.matches(viewProvider)) {
            deferredVisualIdentities.at(identity.instanceId) = true;
            ++deferredVisualOutstanding;
            try {
                viewProvider->finishDeferredVisualRestore();
            }
            catch (const std::exception& error) {
                completeDeferredVisualRestore(identity.instanceId);
                Base::Console().error("Restored model display failed: %s\n", error.what());
            }
            catch (...) {
                completeDeferredVisualRestore(identity.instanceId);
                Base::Console().error("Restored model display failed\n");
            }
            scheduleNextDeferredVisual();
            return;
        }
        completeDeferredVisualRestore(identity.instanceId);
    }

    if (deferredVisualOutstanding == 0) {
        finishDeferredVisualProgress();
        deferredVisualRefreshScheduled = false;
    }
}

void deferVisualRestore(const ViewProviderPartExt& viewProvider)
{
    if (deferredVisualShutdown) {
        return;
    }
    const auto* object = viewProvider.getObject();
    const auto* document = object ? object->getDocument() : nullptr;
    if (!object || !document) {
        return;
    }
    connectDeferredVisualShutdown();
    DeferredVisual identity(viewProvider);
    if (!deferredVisualIdentities
             .emplace(identity.instanceId, false)
             .second) {
        return;
    }
    deferredVisuals.push_back(std::move(identity));
    if (!deferredVisualRefreshScheduled) {
        deferredVisualRefreshScheduled = true;
        scheduleNextDeferredVisual();
    }
}
}  // namespace

ViewProviderPartExt::ViewProviderPartExt()
{
    visualInstanceId = nextVisualInstanceId.fetch_add(1, std::memory_order_relaxed);
    texture.initExtension(this);

    VisualTouched = true;
    forceUpdateCount = 0;
    NormalsFromUV = true;

    // get default line color
    unsigned long lcol = Gui::ViewParams::instance()->getDefaultShapeLineColor();  // dark grey
                                                                                   // (25,25,25)
    float lr, lg, lb;
    lr = ((lcol >> 24) & 0xff) / 255.0;
    lg = ((lcol >> 16) & 0xff) / 255.0;
    lb = ((lcol >> 8) & 0xff) / 255.0;
    // get default vertex color
    unsigned long vcol = Gui::ViewParams::instance()->getDefaultShapeVertexColor();
    float vr, vg, vb;
    vr = ((vcol >> 24) & 0xff) / 255.0;
    vg = ((vcol >> 16) & 0xff) / 255.0;
    vb = ((vcol >> 8) & 0xff) / 255.0;
    int lwidth = Gui::ViewParams::instance()->getDefaultShapeLineWidth();
    int psize = Gui::ViewParams::instance()->getDefaultShapePointSize();


    ParameterGrp::handle hPart = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Part"
    );
    NormalsFromUV = hPart->GetBool("NormalsFromUVNodes", NormalsFromUV);

    long twoside = hPart->GetBool("TwoSideRendering", true) ? 1 : 0;

    // Let the user define a custom lower limit but a value less than
    // OCCT's epsilon is not allowed
    double lowerLimit = hPart->GetFloat("MinimumDeviation", tessRange.LowerBound);
    lowerLimit = std::max(lowerLimit, Precision::Confusion());
    tessRange.LowerBound = lowerLimit;

    static const char* osgroup = "Object Style";

    App::Material lmat;
    lmat.ambientColor.set(0.2f, 0.2f, 0.2f);
    lmat.diffuseColor.set(lr, lg, lb);
    lmat.specularColor.set(0.0f, 0.0f, 0.0f);
    lmat.emissiveColor.set(0.0f, 0.0f, 0.0f);
    lmat.shininess = 1.0f;
    lmat.transparency = 0.0f;

    App::Material vmat;
    vmat.ambientColor.set(0.2f, 0.2f, 0.2f);
    vmat.diffuseColor.set(vr, vg, vb);
    vmat.specularColor.set(0.0f, 0.0f, 0.0f);
    vmat.emissiveColor.set(0.0f, 0.0f, 0.0f);
    vmat.shininess = 1.0f;
    vmat.transparency = 0.0f;

    ADD_PROPERTY_TYPE(LineMaterial, (lmat), osgroup, App::Prop_None, "Object line material.");
    ADD_PROPERTY_TYPE(PointMaterial, (vmat), osgroup, App::Prop_None, "Object point material.");
    ADD_PROPERTY_TYPE(LineColor, (lmat.diffuseColor), osgroup, App::Prop_None, "Set object line color.");
    ADD_PROPERTY_TYPE(PointColor, (vmat.diffuseColor), osgroup, App::Prop_None, "Set object point color");
    ADD_PROPERTY_TYPE(
        PointColorArray,
        (PointColor.getValue()),
        osgroup,
        App::Prop_None,
        "Object point color array."
    );
    ADD_PROPERTY_TYPE(
        LineColorArray,
        (LineColor.getValue()),
        osgroup,
        App::Prop_None,
        "Object line color array."
    );
    ADD_PROPERTY_TYPE(LineWidth, (lwidth), osgroup, App::Prop_None, "Set object line width.");
    LineWidth.setConstraints(&sizeRange);
    PointSize.setConstraints(&sizeRange);
    ADD_PROPERTY_TYPE(PointSize, (psize), osgroup, App::Prop_None, "Set object point size.");
    ADD_PROPERTY_TYPE(
        Deviation,
        (0.5f),
        osgroup,
        App::Prop_None,
        "Sets the accuracy of the polygonal representation of the model\n"
        "in the 3D view (tessellation). Lower values indicate better quality.\n"
        "The value is in percent of object's size."
    );
    Deviation.setConstraints(&tessRange);
    ADD_PROPERTY_TYPE(
        AngularDeflection,
        (28.5),
        osgroup,
        App::Prop_None,
        "Specify how finely to generate the mesh for rendering on screen or when exporting.\n"
        "The default value is 28.5 degrees, or 0.5 radians. The smaller the value\n"
        "the smoother the appearance in the 3D view, and the finer the mesh that will be exported."
    );
    AngularDeflection.setConstraints(&angDeflectionRange);
    ADD_PROPERTY_TYPE(Lighting, (twoside), osgroup, App::Prop_None, "Set object lighting.");
    Lighting.setEnums(LightingEnums);
    ADD_PROPERTY_TYPE(
        DrawStyle,
        ((long int)0),
        osgroup,
        App::Prop_None,
        "Defines the style of the edges in the 3D view."
    );
    DrawStyle.setEnums(DrawStyleEnums);
    coords = new SoCoordinate3();
    coords->ref();
    faceset = new SoBrepFaceSet();
    faceset->setViewProvider(this);
    faceset->ref();
    norm = new SoNormal;
    norm->ref();
    normb = new SoNormalBinding;
    normb->value = SoNormalBinding::PER_VERTEX_INDEXED;
    normb->ref();
    lineset = new SoBrepEdgeSet();
    lineset->setViewProvider(this);
    lineset->ref();
    nodeset = new SoBrepPointSet();
    nodeset->setViewProvider(this);
    nodeset->ref();

    pcFaceBind = new SoMaterialBinding();
    pcFaceBind->ref();
    pcFaceBind->setName("FaceBind");

    pcLineBind = new SoMaterialBinding();
    pcLineBind->ref();
    pcLineBind->setName("LineBind");
    pcLineMaterial = new SoMaterial;
    pcLineMaterial->ref();
    pcLineMaterial->setName("LineMaterial");
    LineMaterial.touch();

    pcPointBind = new SoMaterialBinding();
    pcPointBind->ref();
    pcPointBind->setName("PointBind");
    pcPointMaterial = new SoMaterial;
    pcPointMaterial->ref();
    pcPointMaterial->setName("PointMaterial");
    PointMaterial.touch();

    pcLineStyle = new SoDrawStyle();
    pcLineStyle->ref();
    pcLineStyle->style = SoDrawStyle::LINES;
    pcLineStyle->lineWidth = LineWidth.getValue();
    pcLineStyle->setName("LineStyle");

    pcPointStyle = new SoDrawStyle();
    pcPointStyle->ref();
    pcPointStyle->style = SoDrawStyle::POINTS;
    pcPointStyle->pointSize = PointSize.getValue();
    pcPointStyle->setName("PointStyle");

    pShapeHints = new SoShapeHints;
    pShapeHints->shapeType = SoShapeHints::UNKNOWN_SHAPE_TYPE;
    pShapeHints->ref();
    Lighting.touch();
    DrawStyle.touch();

    sPixmap = "Part_3D_object";
    loadParameter();
}

ViewProviderPartExt::~ViewProviderPartExt()
{
    visualMeshController.cancelAll();
    // The provider can disappear before its queued request even starts.
    completeDeferredVisualRestore(visualInstanceId);
    pcFaceBind->unref();
    pcLineBind->unref();
    pcPointBind->unref();
    pcLineMaterial->unref();
    pcPointMaterial->unref();
    pcLineStyle->unref();
    pcPointStyle->unref();
    pShapeHints->unref();
    coords->unref();
    faceset->unref();
    norm->unref();
    normb->unref();
    lineset->unref();
    nodeset->unref();
}

PyObject* ViewProviderPartExt::getPyObject()
{
    if (!pyViewObject) {
        pyViewObject = new ViewProviderPartExtPy(this);
    }
    pyViewObject->IncRef();
    return pyViewObject;
}

void ViewProviderPartExt::onChanged(const App::Property* prop)
{
    // The lower limit of the deviation has been increased to avoid
    // to freeze the GUI
    // https://forum.freecad.org/viewtopic.php?f=3&t=24912&p=195613
    if (prop == &Deviation) {
        lastRenderedShape = {};
        if (isUpdateForced() || Visibility.getValue()) {
            updateVisual();
        }
        else {
            VisualTouched = true;
        }
    }
    if (prop == &AngularDeflection) {
        lastRenderedShape = {};
        if (isUpdateForced() || Visibility.getValue()) {
            updateVisual();
        }
        else {
            VisualTouched = true;
        }
    }
    if (prop == &LineWidth) {
        pcLineStyle->lineWidth = LineWidth.getValue();
    }
    else if (prop == &PointSize) {
        pcPointStyle->pointSize = PointSize.getValue();
    }
    else if (prop == &LineColor) {
        const Base::Color& c = LineColor.getValue();
        pcLineMaterial->diffuseColor.setValue(c.r, c.g, c.b);
        if (c != LineMaterial.getValue().diffuseColor) {
            LineMaterial.setDiffuseColor(c);
        }
        LineColorArray.setValue(LineColor.getValue());
    }
    else if (prop == &PointColor) {
        const Base::Color& c = PointColor.getValue();
        pcPointMaterial->diffuseColor.setValue(c.r, c.g, c.b);
        if (c != PointMaterial.getValue().diffuseColor) {
            PointMaterial.setDiffuseColor(c);
        }
        PointColorArray.setValue(PointColor.getValue());
    }
    else if (prop == &LineMaterial) {
        const App::Material& Mat = LineMaterial.getValue();
        if (LineColor.getValue() != Mat.diffuseColor) {
            LineColor.setValue(Mat.diffuseColor);
        }
        pcLineMaterial->ambientColor
            .setValue(Mat.ambientColor.r, Mat.ambientColor.g, Mat.ambientColor.b);
        pcLineMaterial->diffuseColor
            .setValue(Mat.diffuseColor.r, Mat.diffuseColor.g, Mat.diffuseColor.b);
        pcLineMaterial->specularColor
            .setValue(Mat.specularColor.r, Mat.specularColor.g, Mat.specularColor.b);
        pcLineMaterial->emissiveColor
            .setValue(Mat.emissiveColor.r, Mat.emissiveColor.g, Mat.emissiveColor.b);
        pcLineMaterial->shininess.setValue(Mat.shininess);
        pcLineMaterial->transparency.setValue(Mat.transparency);
    }
    else if (prop == &PointMaterial) {
        const App::Material& Mat = PointMaterial.getValue();
        if (PointColor.getValue() != Mat.diffuseColor) {
            PointColor.setValue(Mat.diffuseColor);
        }
        pcPointMaterial->ambientColor
            .setValue(Mat.ambientColor.r, Mat.ambientColor.g, Mat.ambientColor.b);
        pcPointMaterial->diffuseColor
            .setValue(Mat.diffuseColor.r, Mat.diffuseColor.g, Mat.diffuseColor.b);
        pcPointMaterial->specularColor
            .setValue(Mat.specularColor.r, Mat.specularColor.g, Mat.specularColor.b);
        pcPointMaterial->emissiveColor
            .setValue(Mat.emissiveColor.r, Mat.emissiveColor.g, Mat.emissiveColor.b);
        pcPointMaterial->shininess.setValue(Mat.shininess);
        pcPointMaterial->transparency.setValue(Mat.transparency);
    }
    else if (prop == &PointColorArray) {
        pointAppearanceStamp = {};
        applyPointAppearance();
    }
    else if (prop == &LineColorArray) {
        edgeAppearanceStamp = {};
        applyEdgeAppearance();
    }
    else if (prop == &_diffuseColor) {
        // Used to load the old DiffuseColor values asynchronously
        std::vector<Base::Color> colors = _diffuseColor.getValues();
        std::vector<float> transparencies;
        transparencies.resize(static_cast<int>(colors.size()));
        for (int i = 0; i < static_cast<int>(colors.size()); i++) {
            transparencies[i] = colors[i].transparency();
            colors[i].a = 1.0F;
        }
        ShapeAppearance.setDiffuseColors(colors);
        ShapeAppearance.setTransparencies(transparencies);
    }
    else if (prop == &ShapeAppearance) {
        shapeAppearanceStamp = {};
        applyShapeAppearance();
        ViewProviderGeometryObject::onChanged(prop);
    }
    else if (prop == &Transparency) {
        const App::Material& Mat = ShapeAppearance[0];
        long value = Base::toPercent(Mat.transparency);
        if (value != Transparency.getValue()) {
            float trans = Base::fromPercent(Transparency.getValue());
            ShapeAppearance.setTransparency(trans);
        }
    }
    else if (prop == &Lighting) {
        if (Lighting.getValue() == 0) {
            pShapeHints->vertexOrdering = SoShapeHints::UNKNOWN_ORDERING;
        }
        else {
            pShapeHints->vertexOrdering = SoShapeHints::COUNTERCLOCKWISE;
        }
    }
    else if (prop == &DrawStyle) {
        if (DrawStyle.getValue() == 0) {
            pcLineStyle->linePattern = 0xffff;
        }
        else if (DrawStyle.getValue() == 1) {
            pcLineStyle->linePattern = 0xf00f;
        }
        else if (DrawStyle.getValue() == 2) {
            pcLineStyle->linePattern = 0x0f0f;
        }
        else {
            pcLineStyle->linePattern = 0xff88;
        }
    }
    else {
        // if the object was invisible and has been changed, recreate the visual
        if (prop == &Visibility && (isUpdateForced() || Visibility.getValue()) && VisualTouched) {
            auto* object = getObject();
            auto* document = object ? object->getDocument() : nullptr;
            if (!isRestoring() && document && document->testStatus(App::Document::Status::Restoring)) {
                // Another view provider can restore the logical visibility of
                // this object after its own finishRestoring() callback has
                // already run. Tessellation cannot execute until the App
                // document leaves restore, so put it on the same deferred
                // visual queue used by finishRestoring().
                deferVisualRestore(*this);
            }
            else {
                updateVisual();
            }
            // updateVisual() may not be triggered by any change (e.g.
            // triggered by an external object through forceUpdate()). And
            // since ShapeAppearance is not changed here either, do not falsely set
            // the document modified status
            Base::ObjectStatusLocker<App::Property::Status, App::Property> guard(
                App::Property::NoModify,
                &ShapeAppearance
            );
            // The material has to be checked again (#0001736)
            onChanged(&ShapeAppearance);
            onChanged(&ShowPlacement);
        }
    }

    ViewProviderGeometryObject::onChanged(prop);
}

bool ViewProviderPartExt::allowOverride(const App::DocumentObject&) const
{
    // Many derived view providers still uses static_cast to get object
    // pointer, so check for exact type here.
    return is<ViewProviderPartExt>();
}

void ViewProviderPartExt::attach(App::DocumentObject* pcFeat)
{
    // call parent attach method
    ViewProviderGeometryObject::attach(pcFeat);

    // Workaround for #0000433, i.e. use SoSeparator instead of SoGroup
    auto* pcNormalRoot = new SoSeparator();
    pcNormalRoot->setName("NormalRoot");
    auto* pcFlatRoot = new SoSeparator();
    pcFlatRoot->setName("FlatRoot");
    auto* pcWireframeRoot = new SoSeparator();
    pcWireframeRoot->setName("WireframeRoot");
    auto* pcPointsRoot = new SoSeparator();
    pcPointsRoot->setName("PointsRoot");
    auto* wireframe = new SoSeparator();

    // Must turn off all intermediate render caching, and let pcRoot to handle
    // cache without interference.
    pcNormalRoot->renderCaching = pcFlatRoot->renderCaching = pcWireframeRoot->renderCaching
        = pcPointsRoot->renderCaching = wireframe->renderCaching = SoSeparator::OFF;

    pcNormalRoot->boundingBoxCaching = pcFlatRoot->boundingBoxCaching = pcWireframeRoot->boundingBoxCaching
        = pcPointsRoot->boundingBoxCaching = wireframe->boundingBoxCaching = SoSeparator::OFF;

    // Avoid any Z-buffer artifacts, so that the lines always appear on top of the faces
    // The correct order is Edges, Polygon offset, Faces.
    SoPolygonOffset* offset = new SoPolygonOffset();

    // wireframe node
    wireframe->setName("Edge");
    wireframe->addChild(pcLineBind);
    wireframe->addChild(pcLineMaterial);
    wireframe->addChild(pcLineStyle);
    wireframe->addChild(lineset);

    // normal viewing with edges and points
    pcNormalRoot->addChild(pcPointsRoot);
    pcNormalRoot->addChild(offset);
    pcNormalRoot->addChild(pcFlatRoot);
    pcNormalRoot->addChild(wireframe);

    // just faces with no edges or points
    pcFlatRoot->addChild(pShapeHints);
    pcFlatRoot->addChild(pcFaceBind);
    pcFlatRoot->addChild(texture.getAppearance());
    texture.setup(pcShapeMaterial);
    SoDrawStyle* pcFaceStyle = new SoDrawStyle();
    pcFaceStyle->setName("FaceStyle");
    pcFaceStyle->style = SoDrawStyle::FILLED;
    pcFlatRoot->addChild(pcFaceStyle);
    pcFlatRoot->addChild(norm);
    pcFlatRoot->addChild(normb);
    pcFlatRoot->addChild(faceset);

    // edges and points
    pcWireframeRoot->addChild(wireframe);
    pcWireframeRoot->addChild(pcPointsRoot);

    // normal viewing with edges and points
    pcPointsRoot->addChild(pcPointBind);
    pcPointsRoot->addChild(pcPointMaterial);
    pcPointsRoot->addChild(pcPointStyle);
    pcPointsRoot->addChild(nodeset);

    // Move 'coords' before the switch
    pcRoot->insertChild(coords, pcRoot->findChild(pcModeSwitch));

    // putting all together with the switch
    addDisplayMaskMode(pcNormalRoot, "Flat Lines");
    addDisplayMaskMode(pcFlatRoot, "Shaded");
    addDisplayMaskMode(pcWireframeRoot, "Wireframe");
    addDisplayMaskMode(pcPointsRoot, "Point");
}

void ViewProviderPartExt::setDisplayMode(const char* ModeName)
{
    if (strcmp("Flat Lines", ModeName) == 0) {
        setDisplayMaskMode("Flat Lines");
    }
    else if (strcmp("Shaded", ModeName) == 0) {
        setDisplayMaskMode("Shaded");
    }
    else if (strcmp("Wireframe", ModeName) == 0) {
        setDisplayMaskMode("Wireframe");
    }
    else if (strcmp("Points", ModeName) == 0) {
        setDisplayMaskMode("Point");
    }

    ViewProviderGeometryObject::setDisplayMode(ModeName);
}

std::vector<std::string> ViewProviderPartExt::getDisplayModes() const
{
    // get the modes of the father
    std::vector<std::string> StrList = ViewProviderGeometryObject::getDisplayModes();

    // add your own modes
    StrList.emplace_back("Flat Lines");
    StrList.emplace_back("Shaded");
    StrList.emplace_back("Wireframe");
    StrList.emplace_back("Points");

    return StrList;
}

std::string ViewProviderPartExt::getElement(const SoDetail* detail) const
{
    std::stringstream str;
    if (detail) {
        if (detail->getTypeId() == SoFaceDetail::getClassTypeId()) {
            const SoFaceDetail* face_detail = static_cast<const SoFaceDetail*>(detail);
            int face = face_detail->getPartIndex() + 1;
            str << "Face" << face;
        }
        else if (detail->getTypeId() == SoLineDetail::getClassTypeId()) {
            const SoLineDetail* line_detail = static_cast<const SoLineDetail*>(detail);
            int edge = line_detail->getLineIndex() + 1;
            str << "Edge" << edge;
        }
        else if (detail->getTypeId() == SoPointDetail::getClassTypeId()) {
            const SoPointDetail* point_detail = static_cast<const SoPointDetail*>(detail);
            int vertex = point_detail->getCoordinateIndex() - nodeset->startIndex.getValue() + 1;
            str << "Vertex" << vertex;
        }
    }

    return str.str();
}

SoDetail* ViewProviderPartExt::getDetail(const char* subelement) const
{
    // 1. Try standard string parsing (FaceN, EdgeN...)
    auto type = Part::TopoShape::getElementTypeAndIndex(subelement);
    std::string element = type.first;
    int index = type.second;

    // 2. Create the Coin3D Detail
    if (element == "Face") {
        SoFaceDetail* detail = new SoFaceDetail();
        detail->setPartIndex(index - 1);
        return detail;
    }
    else if (element == "Edge") {
        SoLineDetail* detail = new SoLineDetail();
        detail->setLineIndex(index - 1);
        return detail;
    }
    else if (element == "Vertex") {
        SoPointDetail* detail = new SoPointDetail();
        static_cast<SoPointDetail*>(detail)->setCoordinateIndex(
            index + nodeset->startIndex.getValue() - 1
        );
        return detail;
    }

    return nullptr;
}

std::vector<Base::Vector3d> ViewProviderPartExt::getModelPoints(const SoPickedPoint* pp) const
{
    try {
        std::vector<Base::Vector3d> pts;
        std::string element = this->getElement(pp->getDetail());
        const auto& shape = getRenderedShape();

        TopoDS_Shape subShape = shape.getSubShape(element.c_str());

        // get the point of the vertex directly
        if (subShape.ShapeType() == TopAbs_VERTEX) {
            const TopoDS_Vertex& v = TopoDS::Vertex(subShape);
            gp_Pnt p = BRep_Tool::Pnt(v);
            pts.emplace_back(p.X(), p.Y(), p.Z());
        }
        // get the nearest point on the edge
        else if (subShape.ShapeType() == TopAbs_EDGE) {
            const SbVec3f& vec = pp->getPoint();
            BRepBuilderAPI_MakeVertex mkVert(gp_Pnt(vec[0], vec[1], vec[2]));
            BRepExtrema_DistShapeShape distSS(subShape, mkVert.Vertex(), 0.1);
            if (distSS.NbSolution() > 0) {
                gp_Pnt p = distSS.PointOnShape1(1);
                pts.emplace_back(p.X(), p.Y(), p.Z());
            }
        }
        // get the nearest point on the face
        else if (subShape.ShapeType() == TopAbs_FACE) {
            const SbVec3f& vec = pp->getPoint();
            BRepBuilderAPI_MakeVertex mkVert(gp_Pnt(vec[0], vec[1], vec[2]));
            BRepExtrema_DistShapeShape distSS(subShape, mkVert.Vertex(), 0.1);
            if (distSS.NbSolution() > 0) {
                gp_Pnt p = distSS.PointOnShape1(1);
                pts.emplace_back(p.X(), p.Y(), p.Z());
            }
        }

        return pts;
    }
    catch (...) {
    }

    // if something went wrong returns an empty array
    return {};
}

std::vector<Base::Vector3d> ViewProviderPartExt::getSelectionShape(const char* /*Element*/) const
{
    return {};
}

void ViewProviderPartExt::setHighlightedFaces(const std::vector<App::Material>& materials)
{
    if (getObject() && getObject()->testStatus(App::ObjectStatus::TouchOnColorChange)) {
        getObject()->touch(true);
    }

    Gui::SoUpdateVBOAction action;
    action.apply(this->faceset);

    int size = static_cast<int>(materials.size());
    if (size == 1) {
        // Uniform appearance is independent of topology. In particular, do
        // not traverse a restored BREP before its worker mesh is installed.
        pcFaceBind->value = SoMaterialBinding::OVERALL;
        setCoinAppearance(materials[0]);
        return;
    }
    if (size == 0) { return; }
    int faceCount = this->faceset->partIndex.getNum();
    if (faceCount == 0) {
        if (const auto* feature = getObject<Part::Feature>()) {
            faceCount = static_cast<int>(feature->Shape.getShape().countSubShapes(TopAbs_FACE));
        }
    }
    if (size == faceCount) {
        pcFaceBind->value = SoMaterialBinding::PER_PART;
        texture.activateMaterial();

        pcShapeMaterial->diffuseColor.setNum(size);
        pcShapeMaterial->ambientColor.setNum(size);
        pcShapeMaterial->specularColor.setNum(size);
        pcShapeMaterial->emissiveColor.setNum(size);
        pcShapeMaterial->shininess.setNum(size);
        pcShapeMaterial->transparency.setNum(size);

        SbColor* dc = pcShapeMaterial->diffuseColor.startEditing();
        SbColor* ac = pcShapeMaterial->ambientColor.startEditing();
        SbColor* sc = pcShapeMaterial->specularColor.startEditing();
        SbColor* ec = pcShapeMaterial->emissiveColor.startEditing();
        float* sh = pcShapeMaterial->shininess.startEditing();
        float* tr = pcShapeMaterial->transparency.startEditing();

        for (int i = 0; i < size; i++) {
            dc[i].setValue(
                materials[i].diffuseColor.r,
                materials[i].diffuseColor.g,
                materials[i].diffuseColor.b
            );
            ac[i].setValue(
                materials[i].ambientColor.r,
                materials[i].ambientColor.g,
                materials[i].ambientColor.b
            );
            sc[i].setValue(
                materials[i].specularColor.r,
                materials[i].specularColor.g,
                materials[i].specularColor.b
            );
            ec[i].setValue(
                materials[i].emissiveColor.r,
                materials[i].emissiveColor.g,
                materials[i].emissiveColor.b
            );
            sh[i] = materials[i].shininess;
            tr[i] = materials[i].transparency;
        }

        pcShapeMaterial->diffuseColor.finishEditing();
        pcShapeMaterial->ambientColor.finishEditing();
        pcShapeMaterial->specularColor.finishEditing();
        pcShapeMaterial->emissiveColor.finishEditing();
        pcShapeMaterial->shininess.finishEditing();
        pcShapeMaterial->transparency.finishEditing();
    }
}

void ViewProviderPartExt::setHighlightedFaces(const App::PropertyMaterialList& appearance)
{
    setHighlightedFaces(appearance.getValues());
}

void ViewProviderPartExt::applyShapeAppearance()
{
    const int faceCount = faceset->partIndex.getNum();
    if (appearanceFaceCount == faceCount
        && shapeAppearanceStamp == appearanceStamp(pcShapeMaterial, pcFaceBind)) {
        return;
    }
    // Multi-face appearance needs the worker's topology result, not a second
    // BREP traversal during restore. finishVisualBuild applies the latest
    // property values after installing that result. Uniform colour is immediate.
    if (ShapeAppearance.getSize() <= 1 || faceCount > 0) {
        setHighlightedFaces(ShapeAppearance);
        shapeAppearanceStamp = appearanceStamp(pcShapeMaterial, pcFaceBind);
        appearanceFaceCount = faceCount;
    }
}

void ViewProviderPartExt::applyEdgeAppearance()
{
    if (edgeAppearanceStamp != appearanceStamp(pcLineMaterial, pcLineBind)) {
        setHighlightedEdges(LineColorArray.getValues());
        edgeAppearanceStamp = appearanceStamp(pcLineMaterial, pcLineBind);
    }
}

void ViewProviderPartExt::applyPointAppearance()
{
    if (pointAppearanceStamp != appearanceStamp(pcPointMaterial, pcPointBind)) {
        setHighlightedPoints(PointColorArray.getValues());
        pointAppearanceStamp = appearanceStamp(pcPointMaterial, pcPointBind);
    }
}

std::map<std::string, Base::Color> ViewProviderPartExt::getElementColors(const char* element) const
{
    std::map<std::string, Base::Color> ret;

    if (!element || !element[0]) {
        auto color = ShapeAppearance.getDiffuseColor();
        color.setTransparency(ShapeAppearance.getTransparency());
        ret["Face"] = color;
        ret["Edge"] = LineColor.getValue();
        ret["Vertex"] = PointColor.getValue();
        return ret;
    }

    if (boost::starts_with(element, "Face")) {
        auto size = ShapeAppearance.getSize();
        if (element[4] == '*') {
            auto color = ShapeAppearance.getDiffuseColor();
            color.setTransparency(Base::fromPercent(Transparency.getValue()));
            bool singleColor = true;
            for (int i = 0; i < size; ++i) {
                auto color_i = ShapeAppearance.getDiffuseColor(i);
                color_i.setTransparency(ShapeAppearance.getTransparency(i));
                if (color_i != color) {
                    ret[std::string(element, 4) + std::to_string(i + 1)] = color_i;
                }
                singleColor = singleColor && color == color_i;
            }
            if (size > 0 && singleColor) {
                color = ShapeAppearance.getDiffuseColor(0);
                color.setTransparency(ShapeAppearance.getTransparency());
                ret.clear();
            }
            ret["Face"] = color;
        }
        else {
            int idx = atoi(element + 4);
            if (idx > 0 && idx <= size) {
                auto color_i = ShapeAppearance.getDiffuseColor(idx - 1);
                color_i.setTransparency(ShapeAppearance.getTransparency(idx - 1));
                ret[element] = color_i;
            }
            else {
                auto color_i = ShapeAppearance.getDiffuseColor();
                color_i.setTransparency(ShapeAppearance.getTransparency());
                ret[element] = color_i;
            }
            if (size == 1) {
                ret[element].setTransparency(ShapeAppearance.getTransparency());
            }
        }
    }
    else if (boost::starts_with(element, "Edge")) {
        auto size = LineColorArray.getSize();
        if (element[4] == '*') {
            auto color = LineColor.getValue();
            bool singleColor = true;
            for (int i = 0; i < size; ++i) {
                if (LineColorArray[i] != color) {
                    ret[std::string(element, 4) + std::to_string(i + 1)] = LineColorArray[i];
                }
                singleColor = singleColor && LineColorArray[0] == LineColorArray[i];
            }
            if (singleColor && size) {
                color = LineColorArray[0];
                ret.clear();
            }
            ret["Edge"] = color;
        }
        else {
            int idx = atoi(element + 4);
            if (idx > 0 && idx <= size) {
                ret[element] = LineColorArray[idx - 1];
            }
            else {
                ret[element] = LineColor.getValue();
            }
        }
    }
    else if (boost::starts_with(element, "Vertex")) {
        auto size = PointColorArray.getSize();
        if (element[5] == '*') {
            auto color = PointColor.getValue();
            bool singleColor = true;
            for (int i = 0; i < size; ++i) {
                if (PointColorArray[i] != color) {
                    ret[std::string(element, 5) + std::to_string(i + 1)] = PointColorArray[i];
                }
                singleColor = singleColor && PointColorArray[0] == PointColorArray[i];
            }
            if (singleColor && size) {
                color = PointColorArray[0];
                ret.clear();
            }
            ret["Vertex"] = color;
        }
        else {
            int idx = atoi(element + 5);
            if (idx > 0 && idx <= size) {
                ret[element] = PointColorArray[idx - 1];
            }
            else {
                ret[element] = PointColor.getValue();
            }
        }
    }
    return ret;
}

void ViewProviderPartExt::unsetHighlightedFaces()
{
    ShapeAppearance.touch();
    Transparency.touch();
}

void ViewProviderPartExt::setHighlightedEdges(const std::vector<Base::Color>& colors)
{
    if (getObject() && getObject()->testStatus(App::ObjectStatus::TouchOnColorChange)) {
        getObject()->touch(true);
    }
    int size = static_cast<int>(colors.size());
    if (size > 1) {
        // Although indexed lineset is used the material binding must be PER_FACE!
        pcLineBind->value = SoMaterialBinding::PER_FACE;
        pcLineMaterial->diffuseColor.setNum(size);
        SbColor* ca = pcLineMaterial->diffuseColor.startEditing();
        // Colours already have one entry per edge, not per tessellation
        // vertex. Install the complete table even before geometry arrives.
        for (int i = 0; i < size; ++i) {
            ca[i].setValue(colors[i].r, colors[i].g, colors[i].b);
        }

        pcLineMaterial->diffuseColor.finishEditing();
    }
    else if (size == 1) {
        pcLineBind->value = SoMaterialBinding::OVERALL;
        pcLineMaterial->diffuseColor.setValue(colors[0].r, colors[0].g, colors[0].b);
    }
}

void ViewProviderPartExt::unsetHighlightedEdges()
{
    pcLineBind->value = SoMaterialBinding::OVERALL;
    LineMaterial.touch();
}

void ViewProviderPartExt::setHighlightedPoints(const std::vector<Base::Color>& colors)
{
    if (getObject() && getObject()->testStatus(App::ObjectStatus::TouchOnColorChange)) {
        getObject()->touch(true);
    }
    int size = static_cast<int>(colors.size());
    if (size > 1) {
        pcPointBind->value = SoMaterialBinding::PER_VERTEX;
        pcPointMaterial->diffuseColor.setNum(size);
        SbColor* ca = pcPointMaterial->diffuseColor.startEditing();
        for (int i = 0; i < size; ++i) {
            ca[i].setValue(colors[i].r, colors[i].g, colors[i].b);
        }
        pcPointMaterial->diffuseColor.finishEditing();
    }
    else if (size == 1) {
        pcPointBind->value = SoMaterialBinding::OVERALL;
        pcPointMaterial->diffuseColor.setValue(colors[0].r, colors[0].g, colors[0].b);
    }
}

void ViewProviderPartExt::unsetHighlightedPoints()
{
    PointColorArray.touch();
}

bool ViewProviderPartExt::loadParameter()
{
    bool changed = false;
    ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Part"
    );
    float deviation = hGrp->GetFloat("MeshDeviation", 0.2);
    float angularDeflection = hGrp->GetFloat("MeshAngularDeflection", 28.65);
    NormalsFromUV = hGrp->GetBool("NormalsFromUVNodes", NormalsFromUV);

    if (Deviation.getValue() != deviation) {
        Deviation.setValue(deviation);
        changed = true;
    }
    if (AngularDeflection.getValue() != angularDeflection) {
        AngularDeflection.setValue(angularDeflection);
    }

    return changed;
}

void ViewProviderPartExt::reload()
{
    if (loadParameter()) {
        updateVisual();
    }
}

void ViewProviderPartExt::updateData(const App::Property* prop)
{
    const char* propName = prop->getName();
    if (propName && (strcmp(propName, "Shape") == 0 || strstr(propName, "Touched"))) {
        // calculate the visual only if visible
        if (isUpdateForced() || Visibility.getValue()) {
            updateVisual();
        }
        else {
            VisualTouched = true;
        }

        if (!VisualTouched) {
            if (this->faceset->partIndex.getNum() > this->pcShapeMaterial->diffuseColor.getNum()) {
                this->pcFaceBind->value = SoMaterialBinding::OVERALL;
            }
        }
    }
    Gui::ViewProviderGeometryObject::updateData(prop);
}

void ViewProviderPartExt::startRestoring()
{
    Gui::ViewProviderGeometryObject::startRestoring();
}

void ViewProviderPartExt::finishRestoring()
{
    // The ShapeAppearance property is restored after DiffuseColor
    // and currently sets a single color.
    // In case DiffuseColor has defined multiple colors they will
    // be passed to the scene graph now.
    if (_diffuseColor.getSize() > 1) {
        onChanged(&_diffuseColor);
    }
    Gui::ViewProviderGeometryObject::finishRestoring();
    if (VisualTouched && (isUpdateForced() || Visibility.getValue())) {
        deferVisualRestore(*this);
    }
}

void ViewProviderPartExt::finishDeferredVisualRestore()
{
    deferredVisualRestorePending = true;
    try {
        if (VisualTouched && (isUpdateForced() || Visibility.getValue())) {
            updateVisual();
        }
    }
    catch (...) {
        deferredVisualRestorePending = false;
        completeDeferredVisualRestore(visualInstanceId);
        throw;
    }
    if (!visualBuildInFlight) {
        deferredVisualRestorePending = false;
        completeDeferredVisualRestore(visualInstanceId);
    }
}

void ViewProviderPartExt::setupContextMenu(QMenu* menu, QObject* receiver, const char* member)
{
    QIcon iconObject = mergeGreyableOverlayIcons(Gui::BitmapFactory().pixmap("Part_ColorFace.svg"));
    Gui::ViewProviderGeometryObject::setupContextMenu(menu, receiver, member);
    QAction* act = menu->addAction(iconObject, QObject::tr("Appearance per Face"), receiver, member);
    act->setData(QVariant((int)ViewProvider::Color));
}

bool ViewProviderPartExt::changeFaceAppearances()
{
    Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    if (dlg) {
        Gui::Control().showDialog(dlg);
        return false;
    }

    Gui::Selection().clearSelection();
    Gui::Control().showDialog(new TaskFaceAppearances(this));
    return true;
}

bool ViewProviderPartExt::setEdit(int ModNum)
{
    if (ModNum == ViewProvider::Color) {
        // When double-clicking on the item for this pad the
        // object unsets and sets its edit mode without closing
        // the task panel
        return changeFaceAppearances();
    }
    else {
        return Gui::ViewProviderGeometryObject::setEdit(ModNum);
    }
}

void ViewProviderPartExt::unsetEdit(int ModNum)
{
    if (ModNum == ViewProvider::Color) {
        // Do nothing here
    }
    else {
        Gui::ViewProviderGeometryObject::unsetEdit(ModNum);
    }
}

void ViewProviderPartExt::setupCoinGeometry(
    TopoDS_Shape shape,
    SoCoordinate3* coords,
    SoBrepFaceSet* faceset,
    SoNormal* norm,
    SoBrepEdgeSet* lineset,
    SoBrepPointSet* nodeset,
    double deviation,
    double angularDeflection,
    bool normalsFromUV
)
{
    const Part::RenderMesh mesh =
        Part::prepareRenderMesh(shape, deviation, angularDeflection, normalsFromUV);
    copyRenderMesh(mesh, coords, faceset, norm, lineset, nodeset);
}

void ViewProviderPartExt::bindRenderMesh(std::shared_ptr<const Part::RenderMesh> mesh)
{
    bindPreparedRenderMesh(
        std::move(mesh),
        installedRenderMesh,
        coords,
        faceset,
        norm,
        lineset,
        nodeset
    );
}

void ViewProviderPartExt::setupCoinGeometry(
    TopoDS_Shape shape,
    SoFCShape* node,
    double deviation,
    double angularDeflection,
    bool normalsFromUV
)
{
    setupCoinGeometry(
        shape,
        node->coords,
        node->faceset,
        node->norm,
        node->lineset,
        node->nodeset,
        deviation,
        angularDeflection,
        normalsFromUV
    );
}

void ViewProviderPartExt::startVisualBuild(
    TopoDS_Shape shape,
    std::uint64_t generation
)
{
    auto* object = getObject();
    auto* document = object ? object->getDocument() : nullptr;
    if (!object || !document || !qApp) {
        return;
    }

    const std::string documentName = document->getName();
    const std::string documentUid = document->Uid.getValueStr();
    const long objectId = object->getID();
    const std::uint64_t instanceId = visualInstanceId;
    visualBuildInFlight = true;
    // The controller owns callbacks on the GUI thread. Completion, cancellation
    // and provider destruction all release this lease there; workers never
    // retain or dereference a document or view provider.
    auto presentationFinished = std::make_shared<VisualUpdateEnd>(document);
    document->beginVisualUpdate();

    try {
        visualMeshController.request(
            this,
            shape,
            Deviation.getValue(),
            AngularDeflection.getValue(),
            NormalsFromUV,
            [documentName, documentUid, objectId, instanceId, generation,
             shape,
             presentationFinished](RenderMeshResult result) mutable {
                auto* document = App::GetApplication().getDocument(documentName.c_str());
                if (document && document->Uid.getValueStr() != documentUid) {
                    document = nullptr;
                }
                auto* object = document ? document->getObjectByID(objectId) : nullptr;
                auto* viewProvider = object && Gui::Application::Instance
                    ? Gui::Application::Instance->getViewProvider<ViewProviderPartExt>(object)
                    : nullptr;
                if (viewProvider) {
                    viewProvider->finishVisualBuild(
                        instanceId, generation, std::move(shape),
                        std::move(result.mesh), std::move(result.error)
                    );
                }
                else {
                    completeDeferredVisualRestore(instanceId);
                }
            }
        );
    }
    catch (const std::exception& failure) {
        visualBuildInFlight = false;
        FC_ERR(
            "Cannot schedule render mesh preparation for "
            << object->getFullName() << ": " << failure.what()
        );
        if (deferredVisualRestorePending) {
            deferredVisualRestorePending = false;
            completeDeferredVisualRestore(instanceId);
        }
    }
}

void ViewProviderPartExt::finishVisualBuild(
    std::uint64_t instanceId,
    std::uint64_t generation,
    TopoDS_Shape shape,
    std::shared_ptr<const Part::RenderMesh> mesh,
    std::string error
)
{
    if (instanceId != visualInstanceId) {
        return;
    }

    visualBuildInFlight = false;
    const bool currentRequest = generation == visualRequestGeneration;
    bool applied = false;
    if (currentRequest && !error.empty()) {
        const auto* object = getObject();
        FC_ERR(
            "Cannot compute Inventor representation for the shape of "
            << (object ? object->getFullName() : "<detached object>")
            << ": " << error
        );
    }
    else if (currentRequest && mesh) {
        Gui::SoUpdateVBOAction action;
        action.apply(this->faceset);

        Gui::SoSelectionElementAction selectionAction(
            Gui::SoSelectionElementAction::None
        );
        selectionAction.apply(this->faceset);
        selectionAction.apply(this->lineset);
        selectionAction.apply(this->nodeset);

        Gui::SoHighlightElementAction highlightAction;
        highlightAction.apply(this->faceset);
        highlightAction.apply(this->lineset);
        highlightAction.apply(this->nodeset);

        try {
            bindRenderMesh(std::move(mesh));
            lastRenderedShape = shape;
            VisualTouched = false;
            applied = true;
        }
        catch (const Base::Exception& failure) {
            failure.reportException();
        }
        catch (const std::exception& failure) {
            const auto* object = getObject();
            FC_ERR(
                "Cannot install Inventor representation for the shape of "
                << (object ? object->getFullName() : "<detached object>")
                << ": " << failure.what()
            );
        }
    }

    if (applied) {
        applyShapeAppearance();
        applyEdgeAppearance();
        applyPointAppearance();
    }

    if (!visualBuildInFlight && deferredVisualRestorePending) {
        deferredVisualRestorePending = false;
        completeDeferredVisualRestore(visualInstanceId);
    }
}

void ViewProviderPartExt::updateVisual()
{
    auto* object = getObject();
    auto* document = object ? object->getDocument() : nullptr;
    if (isRestoring()
        || (document && document->testStatus(App::Document::Status::Restoring))) {
        VisualTouched = true;
        return;
    }

    TopoDS_Shape shape = getRenderedShape().getShape();
    if (!VisualTouched && lastRenderedShape.IsPartner(shape)) {
        Gui::SoHighlightElementAction highlightAction;
        highlightAction.apply(this->faceset);
        highlightAction.apply(this->lineset);
        highlightAction.apply(this->nodeset);
        applyShapeAppearance();
        applyEdgeAppearance();
        applyPointAppearance();
        return;
    }

    VisualTouched = true;
    const std::uint64_t generation = ++visualRequestGeneration;
    startVisualBuild(std::move(shape), generation);
}

void ViewProviderPartExt::forceUpdate(bool enable)
{
    if (enable) {
        if (++forceUpdateCount == 1) {
            if (!isShow() && VisualTouched) {
                auto* object = getObject();
                auto* document = object ? object->getDocument() : nullptr;
                if (document
                    && document->testStatus(App::Document::Status::Restoring)) {
                    deferVisualRestore(*this);
                }
                else {
                    updateVisual();
                }
            }
        }
    }
    else if (forceUpdateCount) {
        --forceUpdateCount;
    }
}


void ViewProviderPartExt::handleChangedPropertyName(
    Base::XMLReader& reader,
    const char* TypeName,
    const char* PropName
)
{
    if (strcmp(PropName, "DiffuseColor") == 0
        && TypeName == App::PropertyColorList::getClassTypeId().getName()) {

        // PropertyColorLists are loaded asynchronously as they're stored in separate files
        _diffuseColor.Restore(reader);
    }
    else {
        Gui::ViewProviderGeometryObject::handleChangedPropertyName(reader, TypeName, PropName);
    }
}
