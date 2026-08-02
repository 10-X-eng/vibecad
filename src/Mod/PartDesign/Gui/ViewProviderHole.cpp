// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2011 Juergen Riegel <FreeCAD@juergen-riegel.net>        *
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
#include <QMessageBox>

#include <algorithm>
#include <map>
#include <string>
#include <string_view>

#include <gp_Ax1.hxx>
#include <gp_Dir.hxx>
#include <gp_Lin.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>
#include <Poly_Triangle.hxx>

#include <BRep_Tool.hxx>
#include <Geom_ConicalSurface.hxx>
#include <Geom_CylindricalSurface.hxx>
#include <Geom_RectangularTrimmedSurface.hxx>
#include <Geom_Surface.hxx>
#include <Precision.hxx>
#include <Standard_Failure.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/Material.h>
#include <Gui/Application.h>
#include <Gui/ViewProvider.h>
#include <Gui/ViewProviderDocumentObject.h>
#include <Mod/Part/App/Tools.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/DesignFeature.h>
#include <Mod/PartDesign/App/Feature.h>
#include <Mod/PartDesign/App/FeatureHole.h>
#include <Mod/PartDesign/Gui/ViewProviderHole.h>

#include <Base/Exception.h>
#include <Base/Placement.h>
#include <Base/Tools.h>
#include <App/Property.h>
#include <Utilities.h>

#include <Inventor/nodes/SoClipPlane.h>
#include <Inventor/nodes/SoCoordinate3.h>
#include <Inventor/nodes/SoGroup.h>
#include <Inventor/nodes/SoIndexedFaceSet.h>
#include <Inventor/nodes/SoMaterial.h>
#include <Inventor/nodes/SoNormal.h>
#include <Inventor/nodes/SoNormalBinding.h>
#include <Inventor/nodes/SoPickStyle.h>
#include <Inventor/nodes/SoSeparator.h>
#include <Inventor/nodes/SoSwitch.h>
#include <Inventor/nodes/SoTexture2.h>
#include <Inventor/nodes/SoTexture2Transform.h>
#include <Inventor/nodes/SoTextureCoordinate2.h>
#include <Inventor/nodes/SoTransparencyType.h>

#include "ViewProviderHole.h"
#include "ViewProviderBody.h"
#include "TaskHoleParameters.h"

using namespace PartDesignGui;

PROPERTY_SOURCE(PartDesignGui::ViewProviderHole, PartDesignGui::ViewProvider)

namespace
{

struct DesignThreadOverlay
{
    SoGroup* root {};
    SoSwitch* node {};
};

struct HolePresentationState
{
    fastsignals::scoped_connection recomputedConnection;
    fastsignals::scoped_connection stableConnection;
    fastsignals::scoped_connection restoredConnection;
    fastsignals::scoped_connection changedConnection;
    // A hole ViewProvider can outlive its body's ViewProvider while a GUI
    // document is being destroyed. Retain the exact scene root used for each
    // overlay so cleanup never has to rediscover an already-destroyed body.
    std::map<const PartDesign::Hole*, SoGroup*> threadRoots;
    std::map<std::string, DesignThreadOverlay> designOverlays;
    // signalFinishRestoreDocument is emitted immediately before the
    // document's Restoring status bit is cleared. The overlay refresh
    // triggered by that signal must not be rejected by the Restoring
    // guard in updateOverlay(), so the callback records completion here
    // first (same contract as ViewProviderBody's documentRestoreFinished).
    bool documentRestoreFinished {false};
};

std::map<const ViewProviderHole*, HolePresentationState>&
holePresentationStates()
{
    static auto* states =
        new std::map<const ViewProviderHole*, HolePresentationState>;
    return *states;
}

PartDesign::Body* bodyWithIdentity(
    const App::Document& document,
    const std::string& bodyId
)
{
    PartDesign::Body* result = nullptr;
    for (auto* body : document.getObjectsOfType<PartDesign::Body>()) {
        if (!body || body->VibeCADBodyId.getValueStr() != bodyId) {
            continue;
        }
        if (result) {
            return nullptr;
        }
        result = body;
    }
    return result;
}

bool operationIsInCurrentBodyState(
    const PartDesign::Hole& hole,
    const PartDesign::Body& body
)
{
    const auto* publication =
        PartDesign::findDesignBodyPublication(&body);
    auto* state = publication
        ? freecad_cast<PartDesign::DesignBodyState*>(
              publication->CurrentState.getValue()
          )
        : nullptr;
    while (state) {
        if (state->Operation.getValue() == &hole) {
            return state->Present.getValue();
        }
        state = freecad_cast<PartDesign::DesignBodyState*>(
            state->PreviousState.getValue()
        );
    }
    return false;
}

gp_Pnt transformedPoint(
    const gp_Pnt& point,
    const Base::Placement& placement
)
{
    Base::Vector3d source(point.X(), point.Y(), point.Z());
    Base::Vector3d target;
    placement.multVec(source, target);
    return gp_Pnt(target.x, target.y, target.z);
}

gp_Dir transformedDirection(
    const gp_Dir& direction,
    const Base::Placement& placement
)
{
    Base::Vector3d source(direction.X(), direction.Y(), direction.Z());
    const Base::Vector3d target =
        placement.getRotation().multVec(source);
    return gp_Dir(target.x, target.y, target.z);
}

void configureThreadClipper(
    const PartDesign::Hole& hole,
    const gp_Dir& normal,
    const gp_Pnt& origin,
    SoClipPlane& clipper
)
{
    if (hole.ThreadDepthType.getValue() == 0) {
        clipper.on = FALSE;
        return;
    }

    clipper.on = TRUE;
    const gp_Pnt endPoint = origin.Translated(
        gp_Vec(normal) * -hole.ThreadDepth.getValue()
    );
    clipper.plane.setValue(
        SbPlane(
            Base::convertTo<SbVec3f>(normal),
            Base::convertTo<SbVec3f>(endPoint)
        )
    );
}

void configureTextureTransform(
    const PartDesign::Hole& hole,
    SoTexture2Transform& transform
)
{
    transform.scaleFactor.setValue(
        hole.ThreadDirection.getValue() == 0
            ? SbVec2f(-1.0F, 1.0F)
            : SbVec2f(1.0F, 1.0F)
    );

    const std::string key = hole.getNameInDocument();
    const unsigned hash = std::hash<std::string> {}(key);
    constexpr float invMax =
        1.0F / static_cast<float>(
                   std::numeric_limits<unsigned>::max()
               );
    transform.translation.setValue(
        SbVec2f(static_cast<float>(hash) * invMax, 0.0F)
    );
}

}  // namespace


ViewProviderHole::ViewProviderHole()
    : textureExtension(std::make_unique<Gui::ViewProviderTextureExtension>())
{
    sPixmap = "PartDesign_Hole.svg";
}

ViewProviderHole::~ViewProviderHole()
{
    clearThreadTextures();
    holePresentationStates().erase(this);
}

void ViewProviderHole::attach(App::DocumentObject* object)
{
    ViewProvider::attach(object);
    if (!object || !object->getDocument()) {
        return;
    }

    auto& state = holePresentationStates()[this];
    App::Document* document = object->getDocument();
    state.recomputedConnection = document->signalRecomputed.connect(
        [this](
            const App::Document&,
            const std::vector<App::DocumentObject*>&
        ) {
            this->updateOverlay();
        }
    );
    state.stableConnection = document->signalBecameStable.connect(
        [this](const App::Document&) {
            this->updateOverlay();
        }
    );
    state.restoredConnection =
        App::GetApplication().signalFinishRestoreDocument.connect(
            [this, document](const App::Document& restored) {
                if (&restored == document) {
                    holePresentationStates()[this]
                        .documentRestoreFinished = true;
                    this->updateOverlay();
                }
            }
        );
    if (Gui::Application::Instance) {
        state.changedConnection =
            Gui::Application::Instance->signalChangedObject.connect(
                [this, document](
                    const Gui::ViewProvider& viewProvider,
                    const App::Property& property
                ) {
                    const char* name = property.getName();
                    if (!name
                        || std::string_view(name) != "Visibility") {
                        return;
                    }
                    const auto* objectView =
                        dynamic_cast<
                            const Gui::ViewProviderDocumentObject*>(
                            &viewProvider
                        );
                    const auto* changed = objectView
                        ? objectView->getObject()
                        : nullptr;
                    if (changed
                        && changed->getDocument() == document) {
                        this->updateOverlay();
                    }
                }
            );
    }
}

bool ViewProviderHole::onDelete(const std::vector<std::string>& arg)
{
    clearThreadTextures();
    return PartDesignGui::ViewProvider::onDelete(arg);
}

void ViewProviderHole::clearThreadTextures()
{
    auto stateIt = holePresentationStates().find(this);

    for (auto const& [hole, sw] : m_threadOverlays) {
        SoGroup* root = nullptr;
        if (stateIt != holePresentationStates().end()) {
            auto rootIt = stateIt->second.threadRoots.find(hole);
            if (rootIt != stateIt->second.threadRoots.end()) {
                root = rootIt->second;
            }
        }
        if (root && root->findChild(sw) >= 0) {
            root->removeChild(sw);
        }
        sw->unref();
        if (root) {
            root->unref();
        }
    }
    m_threadOverlays.clear();
    m_endThreadClipper = nullptr;
    m_textureTransform = nullptr;

    if (stateIt == holePresentationStates().end()) {
        return;
    }
    stateIt->second.threadRoots.clear();
    for (auto& [bodyId, overlay] :
         stateIt->second.designOverlays) {
        Q_UNUSED(bodyId);
        if (overlay.root && overlay.node
            && overlay.root->findChild(overlay.node) >= 0) {
            overlay.root->removeChild(overlay.node);
        }
        if (overlay.node) {
            overlay.node->unref();
        }
        if (overlay.root) {
            overlay.root->unref();
        }
    }
    stateIt->second.designOverlays.clear();
}

std::vector<App::DocumentObject*> ViewProviderHole::claimChildren() const
{
    std::vector<App::DocumentObject*> temp;

    if (App::DocumentObject* profile = getObject<PartDesign::Hole>()->Profile.getValue();
        profile && !profile->isDerivedFrom<PartDesign::Feature>()) {
        temp.push_back(profile);
    }

    return temp;
}

void ViewProviderHole::setupContextMenu(QMenu* menu, QObject* receiver, const char* member)
{
    addDefaultAction(menu, QObject::tr("Edit Hole"));
    PartDesignGui::ViewProvider::setupContextMenu(menu, receiver, member);
}

TaskDlgFeatureParameters* ViewProviderHole::getEditDialog()
{
    return new TaskDlgHoleParameters(this);
}

void ViewProviderHole::updateData(const App::Property* prop)
{
    PartDesignGui::ViewProvider::updateData(prop);

    auto* pcHole = getObject<PartDesign::Hole>();
    if (!pcHole || !prop) {
        return;
    }

    const bool designHole =
        pcHole->isDerivedFrom<PartDesign::DesignHole>();
    if (prop == &pcHole->Threaded || prop == &pcHole->CosmeticThread || prop == &pcHole->ModelThread) {
        if (!designHole && pcHole->getParents().empty()) {
            return;
        }
        updateOverlay();
        return;
    }
    if (prop == &pcHole->ThreadDepth || prop == &pcHole->ThreadDepthType) {
        if (designHole) {
            updateOverlay();
            return;
        }
        updateThreadClipper(pcHole);
        return;
    }
    if (prop == &pcHole->ThreadDirection) {
        if (designHole) {
            updateOverlay();
            return;
        }
        updateThreadDirection(pcHole);
        return;
    }

    if (designHole
        && (prop == &pcHole->Diameter
            || prop == &pcHole->Tapered
            || prop == &pcHole->TaperedAngle
            || prop == &pcHole->ThreadType
            || prop == &pcHole->ThreadSize
            || prop == &pcHole->Profile)) {
        updateOverlay();
    }
}

SoSeparator* ViewProviderHole::createThreadTextureSeparator()
{
    auto* pcHole = getObject<PartDesign::Hole>();
    if (!pcHole) {
        return nullptr;
    }

    gp_Pnt holeOriginPnt;
    auto holeOriginOpt = getHoleOrigin(pcHole);
    if (!holeOriginOpt.has_value()) {
        return nullptr;
    }
    holeOriginPnt = *holeOriginOpt;
    const auto holeNormal = getHoleNormal(pcHole);
    if (!holeNormal) {
        return nullptr;
    }

    return createThreadTextureSeparator(
        pcHole,
        getCurrentlyVisibleShape(pcHole),
        getHoleLocations(pcHole),
        *holeNormal,
        holeOriginPnt,
        getGlobalMaterial(),
        &m_endThreadClipper,
        &m_textureTransform
    );
}

SoSeparator* ViewProviderHole::createThreadTextureSeparator(
    const PartDesign::Hole* hole,
    const TopoDS_Shape& visibleShape,
    const std::vector<gp_Pnt>& holeLocations,
    const gp_Dir& holeNormal,
    const gp_Pnt& holeOrigin,
    const App::Material& material,
    SoClipPlane** threadClipper,
    SoTexture2Transform** textureTransform
)
{
    if (!hole || visibleShape.IsNull() || holeLocations.empty()) {
        return nullptr;
    }

    std::vector<SbVec3f> vertices;
    std::vector<SbVec3f> normals;
    std::vector<int> indices;
    std::vector<SbVec2f> uvs;

    if (!generateBoreMeshData(
            hole,
            holeOrigin,
            visibleShape,
            holeLocations,
            holeNormal,
            vertices,
            normals,
            indices,
            uvs
        )
        || vertices.empty() || normals.empty() || indices.empty() || uvs.empty()) {
        return nullptr;
    }

    // Create subtree
    auto* threadSep = new SoSeparator();

    // The face is selectable but not the texture
    auto* pickStyle = new SoPickStyle();
    pickStyle->style = SoPickStyle::UNPICKABLE;
    threadSep->addChild(pickStyle);

    // Avoid flicker on transparent objects
    auto* tt = new SoTransparencyType();
    tt->value = SoTransparencyType::DELAYED_BLEND;
    threadSep->addChild(tt);

    // End Clipping plane
    auto* clipper = new SoClipPlane();
    configureThreadClipper(*hole, holeNormal, holeOrigin, *clipper);
    threadSep->addChild(clipper);
    if (threadClipper) {
        *threadClipper = clipper;
    }

    // Material
    auto* mat = new SoMaterial();
    textureExtension->setCoinAppearance(mat, material);
    threadSep->addChild(mat);

    // Texture
    auto* threadTexture = new SoTexture2();
    threadTexture->filename.setValue(":/images/ThreadOverlay.png");
    threadTexture->wrapS = SoTexture2::REPEAT;
    threadTexture->wrapT = SoTexture2::REPEAT;
    threadSep->addChild(threadTexture);

    // --- Texture transform for flipping ---
    auto* transform = new SoTexture2Transform();
    configureTextureTransform(*hole, *transform);
    threadSep->addChild(transform);
    if (textureTransform) {
        *textureTransform = transform;
    }

    // Texcoords / normals / geometry
    auto* tc = new SoTextureCoordinate2();
    tc->point.setValues(0, (int)uvs.size(), uvs.data());
    threadSep->addChild(tc);

    auto* nb = new SoNormalBinding();
    nb->value = SoNormalBinding::PER_VERTEX_INDEXED;
    threadSep->addChild(nb);

    auto* ns = new SoNormal();
    ns->vector.setValues(0, (int)normals.size(), normals.data());
    threadSep->addChild(ns);

    auto* coords = new SoCoordinate3();
    coords->point.setValues(0, (int)vertices.size(), vertices.data());
    threadSep->addChild(coords);

    auto* faces = new SoIndexedFaceSet();
    faces->coordIndex.setValues(0, (int)indices.size(), indices.data());
    threadSep->addChild(faces);

    return threadSep;
}

void ViewProviderHole::updateThreadDirection(const PartDesign::Hole* pcHole)
{
    if (!pcHole || !m_textureTransform) {
        return;
    }
    if (pcHole->ThreadDirection.getValue() == 0) {
        m_textureTransform->scaleFactor.setValue(SbVec2f(-1.0F, 1.0F));
    }
    else {
        m_textureTransform->scaleFactor.setValue(SbVec2f(1.0F, 1.0F));
    }
}

void ViewProviderHole::applyThreadPhaseOffset(const PartDesign::Hole* pcHole)
{
    if (!pcHole || !m_textureTransform) {
        return;
    }
    // Applies a unique offset so overlapping threads can be shown as crossed
    // Uses a stable hash of the hole name so it's deterministic between runs
    const std::string key = pcHole->getNameInDocument();
    unsigned hash = std::hash<std::string> {}(key);
    // Map hash to 0..1 range for UV offset
    constexpr float invMax = 1.0F / static_cast<float>(std::numeric_limits<unsigned>::max());
    const float phase = static_cast<float>(hash) * invMax;
    // Apply only horizontal (U) offset
    m_textureTransform->translation.setValue(SbVec2f(phase, 0.0F));
}

void ViewProviderHole::updateThreadClipper(const PartDesign::Hole* pcHole)
{
    if (!pcHole || pcHole->isRecomputing() || !m_endThreadClipper) {
        return;
    }

    auto holeNormalOpt = getHoleNormal(pcHole);
    if (!holeNormalOpt.has_value()) {
        return;
    }
    gp_Dir holeNormalAxis = *holeNormalOpt;

    auto holeOriginOpt = getHoleOrigin(pcHole);
    if (!holeOriginOpt.has_value()) {
        return;
    }
    gp_Pnt holeOriginPnt = *holeOriginOpt;
    configureThreadClipper(
        *pcHole,
        holeNormalAxis,
        holeOriginPnt,
        *m_endThreadClipper
    );
}

std::optional<gp_Dir> ViewProviderHole::getHoleNormal(const PartDesign::Hole* pcHole) const
{
    if (!pcHole) {
        return std::nullopt;
    }

    // An empty or incomplete profile (e.g. mid-transaction, partially restored
    // links) is a normal transient presentation state: report "no overlay"
    // instead of letting Part::NullShapeException or an OCC failure escape
    // from presentation code. Mirrors Hole::getHoleLocations().
    try {
        Base::Vector3d normal = pcHole->guessNormalDirection(pcHole->getProfileShape());

        // Reject if direction is mathematically zero (invalid for gp_Dir)
        if (normal.IsNull()) {
            return std::nullopt;
        }

        return Base::convertTo<gp_Dir>(normal);
    }
    catch (const Base::Exception&) {
        return std::nullopt;
    }
    catch (const Standard_Failure&) {
        return std::nullopt;
    }
}

std::optional<gp_Pnt> ViewProviderHole::getHoleOrigin(const PartDesign::Hole* pcHole) const
{
    if (!pcHole) {
        return std::nullopt;
    }
    if (auto* profile = pcHole->Profile.getValue()) {
        const Base::Vector3d pos = profile->getPlacement().getPosition();
        return Base::convertTo<gp_Pnt>(pos);
    }
    return std::nullopt;
}

std::vector<gp_Pnt> ViewProviderHole::getHoleLocations(const PartDesign::Hole* pcHole) const
{
    if (!pcHole) {
        return {};
    }
    return pcHole->getHoleLocations();
}

std::vector<TopoDS_Face> ViewProviderHole::collectBoreFaces(const PartDesign::Hole* pcHole) const
{
    if (!pcHole) {
        return {};
    }

    const auto holeNormal = getHoleNormal(pcHole);
    if (!holeNormal) {
        return {};
    }
    return collectBoreFaces(
        pcHole,
        getCurrentlyVisibleShape(pcHole),
        getHoleLocations(pcHole),
        *holeNormal
    );
}

std::vector<TopoDS_Face> ViewProviderHole::collectBoreFaces(
    const PartDesign::Hole* hole,
    const TopoDS_Shape& visibleShape,
    const std::vector<gp_Pnt>& holeLocations,
    const gp_Dir& holeNormal
) const
{
    std::vector<TopoDS_Face> boreFaces;
    if (!hole || visibleShape.IsNull() || holeLocations.empty()) {
        return boreFaces;
    }

    const double holeRadius = hole->Diameter.getValue() / 2.0;
    const double distTolerance = 2 * Precision::Confusion();
    const bool isTapered = hole->Tapered.getValue();
    const double taperSemiAngleRad = isTapered
        ? Base::toRadians(90 - hole->TaperedAngle.getValue())
        : 0.0;

    for (TopExp_Explorer expl(visibleShape, TopAbs_FACE);
         expl.More();
         expl.Next()) {
        const TopoDS_Face& face = TopoDS::Face(expl.Current());
        Handle(Geom_Surface) surf = BRep_Tool::Surface(face);
        if (surf.IsNull()) {
            continue;
        }

        // Unwrap trimmed surfaces
        if (surf->IsKind(STANDARD_TYPE(Geom_RectangularTrimmedSurface))) {
            surf = Handle(Geom_RectangularTrimmedSurface)::DownCast(surf)->BasisSurface();
        }

        gp_Ax1 axis;
        bool isMatch = false;

        if (!isTapered) {
            if (!surf->IsKind(STANDARD_TYPE(Geom_CylindricalSurface))) {
                continue;
            }
            auto cyl = Handle(Geom_CylindricalSurface)::DownCast(surf);
            if (std::abs(cyl->Radius() - holeRadius) >= Precision::Confusion()) {
                continue;
            }
            axis = cyl->Axis();
        }
        else {
            if (!surf->IsKind(STANDARD_TYPE(Geom_ConicalSurface))) {
                continue;
            }
            auto con = Handle(Geom_ConicalSurface)::DownCast(surf);
            double angle = std::abs(con->SemiAngle());
            if (std::abs(angle - taperSemiAngleRad) >= Precision::Angular()) {
                continue;
            }
            axis = con->Axis();
        }

        for (const auto& loc : holeLocations) {
            if (gp_Lin(axis).Distance(loc) < distTolerance) {
                isMatch = true;
                break;
            }
        }

        if (!isMatch) {
            continue;
        }

        if (!axis.Direction().IsParallel(
                holeNormal,
                Precision::Angular()
            )) {
            continue;
        }

        boreFaces.push_back(face);
    }

    return boreFaces;
}

App::Material ViewProviderHole::getGlobalMaterial()
{
    if (auto* materialProp = dynamic_cast<App::PropertyMaterial*>(getPropertyByName("Material"))) {
        return materialProp->getValue();
    }
    if (auto* bodyVp = getBodyViewProvider()) {
        if (auto* materialProp
            = freecad_cast<App::PropertyMaterial*>(bodyVp->getPropertyByName("Material"))) {
            return materialProp->getValue();
        }
    }

    return App::Material::getDefaultAppearance();
}

App::Material ViewProviderHole::getBodyMaterial(
    const PartDesign::Body* body
) const
{
    auto* document = getDocument();
    auto* viewProvider =
        body && document
        ? document->getViewProvider(
              const_cast<PartDesign::Body*>(body)
          )
        : nullptr;
    if (viewProvider) {
        if (auto* material = freecad_cast<App::PropertyMaterial*>(
                viewProvider->getPropertyByName("Material")
            )) {
            return material->getValue();
        }
    }
    return App::Material::getDefaultAppearance();
}

TopoDS_Shape ViewProviderHole::getCurrentlyVisibleShape(const PartDesign::Hole* pcHole) const
{
    auto* body = PartDesign::Body::findBodyOf(pcHole);
    if (!body) {
        return {};
    }
    const auto& features = body->Group.getValues();
    auto holeIt = std::ranges::find(features, pcHole);
    if (holeIt == features.end()) {
        return {};
    }
    for (auto it = holeIt; it != features.end(); ++it) {
        auto* posteriorFeature = dynamic_cast<PartDesign::Feature*>(*it);
        if (posteriorFeature && posteriorFeature->Visibility.getValue()) {
            return posteriorFeature->Shape.getValue();
        }
    }
    return body->Shape.getValue();
}

std::pair<gp_Dir, gp_Dir> ViewProviderHole::buildOrthonormalFrame(const gp_Dir& axis)
{
    gp_Dir ref(0, 0, 1);
    if (axis.IsParallel(ref, Precision::Angular())) {
        ref = gp_Dir(0, 1, 0);
    }
    gp_Vec x_vec = axis.Crossed(ref);
    if (x_vec.SquareMagnitude() < Precision::Confusion()) {
        ref = gp_Dir(1, 0, 0);
        x_vec = axis.Crossed(ref);
    }
    gp_Dir x_dir(x_vec);
    gp_Dir y_dir(axis.Crossed(x_dir));
    return {x_dir, y_dir};
}

SbVec2f ViewProviderHole::addVertex(
    std::vector<SbVec3f>& vertices,
    std::vector<SbVec3f>& normals,
    const gp_Pnt& pt,
    const gp_Pnt& origin,
    const gp_Dir& axis,
    const gp_Dir& x_dir,
    const gp_Dir& y_dir,
    double minProj,
    double initialRadius,
    double threadPitch
)
{
    gp_Vec toPoint(origin, pt);
    gp_Vec radialComp = toPoint - (toPoint.Dot(axis) * axis);
    double axialDist = toPoint.Dot(axis) - minProj;
    double currentRadius = radialComp.Magnitude();
    double radialOffset = currentRadius - initialRadius;
    double lengthAlongTaper = std::sqrt((axialDist * axialDist) + (radialOffset * radialOffset));

    float vCoord = static_cast<float>(lengthAlongTaper / threadPitch);
    double angleRad = std::atan2(radialComp.Dot(y_dir), radialComp.Dot(x_dir));
    float uCoord = static_cast<float>(angleRad / (2 * M_PI));
    uCoord -= std::floor(uCoord);

    vertices.emplace_back(pt.X(), pt.Y(), pt.Z());
    gp_Dir normalDir = (radialComp.SquareMagnitude() > std::pow(Precision::Confusion(), 2))
        ? gp_Dir(radialComp)
        : axis;
    normals.emplace_back(normalDir.X(), normalDir.Y(), normalDir.Z());

    return SbVec2f(uCoord, vCoord);
}

namespace
{
Handle(Geom_Surface) unwrapSurface(const TopoDS_Face& face)
{
    Handle(Geom_Surface) surf = BRep_Tool::Surface(face);
    if (!surf.IsNull() && surf->IsKind(STANDARD_TYPE(Geom_RectangularTrimmedSurface))) {
        surf = Handle(Geom_RectangularTrimmedSurface)::DownCast(surf)->BasisSurface();
    }
    return surf;
}
}  // namespace

void ViewProviderHole::handleSeamTriangle(
    std::vector<SbVec3f>& vertices,
    std::vector<SbVec3f>& normals,
    std::vector<SbVec2f>& uvs,
    std::array<int, 3>& triIndices
)
{
    constexpr float seamThreshold = 0.5F;

    bool crossesSeam = std::abs(uvs[triIndices[0]][0] - uvs[triIndices[1]][0]) > seamThreshold
        || std::abs(uvs[triIndices[1]][0] - uvs[triIndices[2]][0]) > seamThreshold
        || std::abs(uvs[triIndices[2]][0] - uvs[triIndices[0]][0]) > seamThreshold;

    if (!crossesSeam) {
        return;
    }

    int idx0 = triIndices[0];
    int idx1 = triIndices[1];
    int idx2 = triIndices[2];

    if (uvs[idx0][0] < seamThreshold) {
        SbVec2f uv = uvs[idx0];
        uv[0] += 1.0F;
        int newIdx = static_cast<int>(vertices.size());
        vertices.push_back(vertices[idx0]);
        normals.push_back(normals[idx0]);
        uvs.push_back(uv);
        triIndices[0] = newIdx;
    }

    if (uvs[idx1][0] < seamThreshold) {
        SbVec2f uv = uvs[idx1];
        uv[0] += 1.0F;
        int newIdx = static_cast<int>(vertices.size());
        vertices.push_back(vertices[idx1]);
        normals.push_back(normals[idx1]);
        uvs.push_back(uv);
        triIndices[1] = newIdx;
    }

    if (uvs[idx2][0] < seamThreshold) {
        SbVec2f uv = uvs[idx2];
        uv[0] += 1.0F;
        int newIdx = static_cast<int>(vertices.size());
        vertices.push_back(vertices[idx2]);
        normals.push_back(normals[idx2]);
        uvs.push_back(uv);
        triIndices[2] = newIdx;
    }
}

bool ViewProviderHole::generateBoreMeshData(
    const PartDesign::Hole* pcHole,
    const gp_Pnt& holeOriginPnt,
    std::vector<SbVec3f>& vertices,
    std::vector<SbVec3f>& normals,
    std::vector<int>& indices,
    std::vector<SbVec2f>& uvs
)
{
    if (!pcHole) {
        return false;
    }
    const auto holeNormal = getHoleNormal(pcHole);
    if (!holeNormal) {
        return false;
    }
    return generateBoreMeshData(
        pcHole,
        holeOriginPnt,
        getCurrentlyVisibleShape(pcHole),
        getHoleLocations(pcHole),
        *holeNormal,
        vertices,
        normals,
        indices,
        uvs
    );
}

bool ViewProviderHole::generateBoreMeshData(
    const PartDesign::Hole* hole,
    const gp_Pnt& holeOrigin,
    const TopoDS_Shape& visibleShape,
    const std::vector<gp_Pnt>& holeLocations,
    const gp_Dir& holeNormal,
    std::vector<SbVec3f>& vertices,
    std::vector<SbVec3f>& normals,
    std::vector<int>& indices,
    std::vector<SbVec2f>& uvs
)
{
    if (!hole) {
        return false;
    }
    const double threadPitch = hole->getThreadPitch();
    if (threadPitch == 0.0) {
        return false;
    }

    vertices.clear();
    normals.clear();
    indices.clear();
    uvs.clear();

    const auto boreFaces = collectBoreFaces(
        hole,
        visibleShape,
        holeLocations,
        holeNormal
    );
    if (boreFaces.empty()) {
        return false;
    }

    double minProj = std::numeric_limits<double>::max();

    // --- Compute projection bounds ---
    for (const auto& face : boreFaces) {
        std::vector<gp_Pnt> meshPoints;
        std::vector<Poly_Triangle> meshFacets;
        if (Part::Tools::getTriangulation(face, meshPoints, meshFacets)) {
            for (const auto& p : meshPoints) {
                double proj = gp_Vec(holeOrigin, p).Dot(holeNormal);
                minProj = std::min(minProj, proj);
            }
        }
    }
    if (!std::isfinite(minProj)) {
        return false;
    }

    const double holeRadius = hole->Diameter.getValue() / 2.0;
    const double coneSemiAngleRad = hole->Tapered.getValue()
        ? Base::toRadians(hole->TaperedAngle.getValue() * 0.5)
        : 0.0;
    const double initialRadius = (minProj * std::tan(coneSemiAngleRad)) + holeRadius;

    bool success = false;

    for (const auto& face : boreFaces) {
        std::vector<gp_Pnt> meshPoints;
        std::vector<Poly_Triangle> meshFacets;
        if (!Part::Tools::getTriangulation(face, meshPoints, meshFacets)) {
            continue;
        }

        Handle(Geom_Surface) surf = unwrapSurface(face);
        gp_Ax3 surfPos;
        if (auto cyl = Handle(Geom_CylindricalSurface)::DownCast(surf)) {
            surfPos = cyl->Position();
        }
        else if (auto cone = Handle(Geom_ConicalSurface)::DownCast(surf)) {
            surfPos = cone->Position();
        }
        else {
            continue;
        }

        auto [x_dir, y_dir] = buildOrthonormalFrame(surfPos.Direction());
        gp_Pnt localOrigin = surfPos.Location();

        std::vector<int> localToGlobalIndex(meshPoints.size());
        for (size_t i = 0; i < meshPoints.size(); ++i) {
            localToGlobalIndex[i] = static_cast<int>(vertices.size()),
            uvs.push_back(addVertex(
                vertices,
                normals,
                meshPoints[i],
                localOrigin,
                surfPos.Direction(),
                x_dir,
                y_dir,
                minProj,
                initialRadius,
                threadPitch
            ));
        }
        // --- Build indices ---
        for (const auto& facet : meshFacets) {
            std::array<int, 3> n = {1, 1, 1};
            facet.Get(n[0], n[1], n[2]);
            std::array<int, 3> triIndices
                = {localToGlobalIndex[n[0]], localToGlobalIndex[n[1]], localToGlobalIndex[n[2]]};
            handleSeamTriangle(vertices, normals, uvs, triIndices);

            indices.insert(indices.end(), {triIndices[0], triIndices[1], triIndices[2], -1});
        }
        success = true;
    }

    return success;
}

bool ViewProviderHole::isHoleThreadVisible() const
{
    auto* hole = getObject<PartDesign::Hole>();
    if (!hole) {
        return false;
    }
    if (hole->isDerivedFrom<PartDesign::DesignHole>()) {
        auto* document = hole->getDocument();
        const auto* operation =
            dynamic_cast<
                const PartDesign::DesignOperationProperties*>(hole);
        if (!document || !operation) {
            return false;
        }
        for (const auto& bodyId :
             operation->OutputBodyIds.getValues()) {
            if (const auto* body =
                    bodyWithIdentity(*document, bodyId);
                body && isDesignHoleThreadVisible(hole, body)) {
                return true;
            }
        }
        return false;
    }

    auto* body = PartDesign::Body::findBodyOf(hole);
    if (!body || !body->Visibility.getValue() || hole->Suppressed.getValue()
        || !hole->Threaded.getValue() || !hole->CosmeticThread.getValue()
        || hole->ModelThread.getValue()) {
        return false;
    }
    const auto& features = body->Group.getValues();
    auto holeIt = std::ranges::find(features, hole);
    if (holeIt == features.end()) {
        return false;
    }
    for (auto it = holeIt; it != features.end(); ++it) {
        auto* posteriorFeature = dynamic_cast<PartDesign::Feature*>(*it);
        if (posteriorFeature && posteriorFeature->Visibility.getValue()) {
            return true;
        }
    }
    // We've reached the end and no posterior feature is visible,
    return false;
}

bool ViewProviderHole::isDesignHoleThreadVisible(
    const PartDesign::Hole* hole,
    const PartDesign::Body* body
) const
{
    if (!hole || !body || hole->getDocument() != body->getDocument()
        || hole->Suppressed.getValue() || !hole->Threaded.getValue()
        || !hole->CosmeticThread.getValue()
        || hole->ModelThread.getValue()
        || !operationIsInCurrentBodyState(*hole, *body)) {
        return false;
    }

    const auto* publication =
        PartDesign::findDesignBodyPublication(body);
    auto* guiDocument = getDocument();
    auto* bodyView =
        guiDocument
        ? dynamic_cast<ViewProviderBody*>(
              guiDocument->getViewProvider(
                  const_cast<PartDesign::Body*>(body)
              )
          )
        : nullptr;
    auto* publicationView =
        publication && guiDocument
        ? guiDocument->getViewProvider(
              const_cast<
                  PartDesign::DesignBodyPublication*>(publication)
          )
        : nullptr;
    return publication && bodyView && publicationView
        && bodyView->isShow() && publicationView->isShow()
        && !publication->Shape.getShape().isNull();
}

void ViewProviderHole::updateDesignHoleOverlays(
    PartDesign::Hole* hole
)
{
    clearThreadTextures();
    if (!hole || hole->isRecomputing()
        || !hole->isDerivedFrom<PartDesign::DesignHole>()) {
        return;
    }

    auto* document = hole->getDocument();
    auto* operation =
        dynamic_cast<PartDesign::DesignOperationProperties*>(hole);
    if (!document || !operation || !getDocument()) {
        return;
    }
    const auto& bodyIds = operation->OutputBodyIds.getValues();
    const auto& frames = operation->OutputFrames.getValues();
    if (bodyIds.size() != frames.size()) {
        return;
    }

    const auto normal = getHoleNormal(hole);
    const auto origin = getHoleOrigin(hole);
    const auto locations = getHoleLocations(hole);
    if (!normal || !origin || locations.empty()) {
        return;
    }

    auto& overlays =
        holePresentationStates()[this].designOverlays;
    for (std::size_t index = 0; index < bodyIds.size(); ++index) {
        auto* body = bodyWithIdentity(*document, bodyIds[index]);
        if (!body || !isDesignHoleThreadVisible(hole, body)) {
            continue;
        }
        auto* publication =
            PartDesign::findDesignBodyPublication(body);
        auto* bodyView = dynamic_cast<ViewProviderBody*>(
            getDocument()->getViewProvider(body)
        );
        if (!publication || !bodyView) {
            continue;
        }

        const Base::Placement designToBody =
            frames[index].inverse();
        std::vector<gp_Pnt> bodyLocations;
        bodyLocations.reserve(locations.size());
        std::ranges::transform(
            locations,
            std::back_inserter(bodyLocations),
            [&designToBody](const gp_Pnt& location) {
                return transformedPoint(location, designToBody);
            }
        );
        const gp_Dir bodyNormal =
            transformedDirection(*normal, designToBody);
        const gp_Pnt bodyOrigin =
            transformedPoint(*origin, designToBody);

        SoSeparator* separator = createThreadTextureSeparator(
            hole,
            publication->Shape.getValue(),
            bodyLocations,
            bodyNormal,
            bodyOrigin,
            getBodyMaterial(body),
            nullptr,
            nullptr
        );
        if (!separator) {
            continue;
        }

        auto* threadSwitch = new SoSwitch();
        threadSwitch->ref();
        threadSwitch->addChild(separator);
        threadSwitch->whichChild = SO_SWITCH_ALL;

        SoGroup* root = bodyView->getRoot();
        root->ref();
        root->addChild(threadSwitch);
        overlays.emplace(
            bodyIds[index],
            DesignThreadOverlay {root, threadSwitch}
        );
    }
}

void ViewProviderHole::updateOverlay()
{
    auto* hole = getObject<PartDesign::Hole>();
    if (!hole) {
        clearThreadTextures();
        return;
    }

    // During undo/redo replay, Cancel rollback, restore, and recompute,
    // property replay owns the object graph: links such as Profile may be
    // temporarily incomplete. Presentation code must not observe that
    // state. Leave the existing overlay untouched and let the
    // signalBecameStable / signalFinishRestoreDocument connections rebuild
    // it once the document is coherent again — the same lifecycle boundary
    // as ViewProviderBody::normalizeResultPresentation().
    if (const auto* document = hole->getDocument()) {
        const auto stateIt = holePresentationStates().find(this);
        const bool documentRestoreFinished =
            stateIt != holePresentationStates().end()
            && stateIt->second.documentRestoreFinished;
        if (document->isPerformingTransaction()
            || document->testStatus(App::Document::Recomputing)
            || (!documentRestoreFinished
                && document->testStatus(App::Document::Restoring))) {
            return;
        }
    }

    if (hole->isDerivedFrom<PartDesign::DesignHole>()) {
        updateDesignHoleOverlays(hole);
        return;
    }

    bool isThreadVisible = isHoleThreadVisible();
    auto* bodyVp = getBodyViewProvider();
    auto& state = holePresentationStates()[this];
    // Cleanup
    auto it = m_threadOverlays.find(hole);
    if (it != m_threadOverlays.end()) {
        SoSwitch* existingSwitch = it->second;
        auto rootIt = state.threadRoots.find(hole);
        SoGroup* root = rootIt != state.threadRoots.end()
            ? rootIt->second
            : nullptr;
        if (root && root->findChild(existingSwitch) >= 0) {
            root->removeChild(existingSwitch);
        }
        existingSwitch->unref();
        if (root) {
            root->unref();
        }
        if (rootIt != state.threadRoots.end()) {
            state.threadRoots.erase(rootIt);
        }
        m_threadOverlays.erase(it);
    }
    // Add the thread
    if (isThreadVisible && bodyVp) {
        if (SoSeparator* newSep = createThreadTextureSeparator()) {
            auto* threadSwitch = new SoSwitch();
            threadSwitch->ref();
            threadSwitch->addChild(newSep);
            SoGroup* root = bodyVp->getRoot();
            root->ref();
            root->addChild(threadSwitch);
            threadSwitch->whichChild = SO_SWITCH_ALL;
            m_threadOverlays[hole] = threadSwitch;
            state.threadRoots[hole] = root;
        }
    }
}
