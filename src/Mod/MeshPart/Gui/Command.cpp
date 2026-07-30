// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2008 Jürgen Riegel <juergen.riegel@web.de>              *
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
#include <optional>
#include <string>
#include <vector>

#include <QApplication>
#include <QCheckBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QFormLayout>
#include <QMessageBox>
#include <QPushButton>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObserver.h>
#include <Base/Converter.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/MainWindow.h>
#include <Gui/QuantitySpinBox.h>
#include <Gui/ViewProvider.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Mesh/Gui/ParametricMeshFilter.h>
#include <Mod/Part/App/PartFeature.h>

#include <TopAbs_ShapeEnum.hxx>

#include "../App/FeatureMeshPartOperations.h"
#include "CrossSections.h"
#include "TaskCurveOnMesh.h"
#include "Tessellation.h"


using namespace std;

namespace
{

App::Document* cleanActiveDocument()
{
    App::Document* document = App::GetApplication().getActiveDocument();
    return MeshGui::canStartNativeMeshCommand(document) ? document : nullptr;
}

template<typename Object>
bool allObjectsBelongTo(const std::vector<Object*>& objects, const App::Document* document)
{
    return document && std::ranges::all_of(objects, [document](const Object* object) {
               return object && object->getDocument() == document
                   && MeshGui::isNativeMeshInputActive(object);
           });
}

bool hasSelectedMeshingShape(const App::Document* document)
{
    if (!document) {
        return false;
    }
    for (const auto& selected : Gui::Selection().getSelection("*", Gui::ResolveMode::NoResolve)) {
        if (!selected.pObject || selected.pObject->getDocument() != document
            || !MeshGui::isNativeMeshInputActive(selected.pObject)) {
            continue;
        }
        const auto shape = Part::Feature::getTopoShape(
            selected.pObject,
            Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform,
            selected.SubName
        );
        if (shape.hasSubShape(TopAbs_FACE)) {
            return true;
        }
    }
    return false;
}

bool hasVisibleNonEmptyMesh(const App::Document* document)
{
    if (!document) {
        return false;
    }
    return std::ranges::any_of(
        document->getObjectsOfType(Mesh::Feature::getClassTypeId()),
        [](App::DocumentObject* object) {
            auto* mesh = freecad_cast<Mesh::Feature*>(object);
            auto* viewProvider = mesh ? Gui::Application::Instance->getViewProvider(mesh) : nullptr;
            return mesh && MeshGui::isNativeMeshInputActive(mesh)
                && mesh->Mesh.getValue().countFacets() > 0 && viewProvider
                && viewProvider->isVisible();
        }
    );
}

bool allMeshesNonEmpty(const std::vector<Mesh::Feature*>& meshes)
{
    return std::ranges::all_of(meshes, [](const Mesh::Feature* mesh) {
        return mesh && mesh->Mesh.getValue().countFacets() > 0;
    });
}

bool sameMeshGeometry(const Mesh::MeshObject& first, const Mesh::MeshObject& second)
{
    const auto& firstKernel = first.getKernel();
    const auto& secondKernel = second.getKernel();
    const auto& firstPoints = firstKernel.GetPoints();
    const auto& secondPoints = secondKernel.GetPoints();
    const auto& firstFacets = firstKernel.GetFacets();
    const auto& secondFacets = secondKernel.GetFacets();
    return firstPoints.size() == secondPoints.size() && firstFacets.size() == secondFacets.size()
        && std::ranges::equal(firstPoints, secondPoints)
        && std::ranges::equal(
               firstFacets,
               secondFacets,
               [](const MeshCore::MeshFacet& left, const MeshCore::MeshFacet& right) {
                   return left._aulPoints[0] == right._aulPoints[0]
                       && left._aulPoints[1] == right._aulPoints[1]
                       && left._aulPoints[2] == right._aulPoints[2];
               }
        );
}

}  // namespace

//===========================================================================
// MeshPart_Mesher
//===========================================================================
DEF_STD_CMD_A(CmdMeshPartMesher)

CmdMeshPartMesher::CmdMeshPartMesher()
    : Command("MeshPart_Mesher")
{
    sAppModule = "MeshPart";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Mesh From Shape");

    sToolTipText = QT_TR_NOOP("Tessellate shape");
    sWhatsThis = "MeshPart_Mesher";
    sStatusTip = sToolTipText;
}

void CmdMeshPartMesher::activated(int)
{
    if (!isActive()) {
        return;
    }
    Gui::Control().showDialog(new MeshPartGui::TaskTessellation());
}

bool CmdMeshPartMesher::isActive()
{
    App::Document* document = cleanActiveDocument();
    return hasSelectedMeshingShape(document);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshPartShapeFromMesh)

CmdMeshPartShapeFromMesh::CmdMeshPartShapeFromMesh()
    : Command("MeshPart_ShapeFromMesh")
{
    sAppModule = "MeshPart";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Shape From Mesh");
    sToolTipText = QT_TR_NOOP("Create an editable shape linked to the selected mesh");
    sWhatsThis = "MeshPart_ShapeFromMesh";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Shape_from_Mesh";
}

void CmdMeshPartShapeFromMesh::activated(int)
{
    App::Document* document = cleanActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    if (meshes.empty() || !allObjectsBelongTo(meshes, document) || !allMeshesNonEmpty(meshes)) {
        return;
    }
    App::DocumentWeakPtrT documentTarget(document);
    std::vector<App::DocumentObjectWeakPtrT> meshTargets;
    meshTargets.reserve(meshes.size());
    for (auto* mesh : meshes) {
        meshTargets.emplace_back(mesh);
    }

    QDialog dialog(Gui::getMainWindow());
    dialog.setWindowTitle(qApp->translate("MeshPart_ShapeFromMesh", "Shape From Mesh"));
    QFormLayout layout(&dialog);
    Gui::QuantitySpinBox tolerance(&dialog);
    tolerance.setUnit(Base::Unit::Length);
    tolerance.setMinimum(1.0e-6);
    tolerance.setMaximum(10.0);
    tolerance.setSingleStep(0.1);
    tolerance.setValue(0.1);
    QCheckBox sew(qApp->translate("MeshPart_ShapeFromMesh", "Sew adjacent faces"), &dialog);
    layout.addRow(qApp->translate("MeshPart_ShapeFromMesh", "Tolerance"), &tolerance);
    layout.addRow(&sew);
    QDialogButtonBox buttons(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    layout.addRow(&buttons);
    QObject::connect(&buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    QObject::connect(&buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    document = *documentTarget;
    meshes.clear();
    meshes.reserve(meshTargets.size());
    for (const auto& target : meshTargets) {
        auto* mesh = target.get<Mesh::Feature>();
        if (!mesh) {
            return;
        }
        meshes.push_back(mesh);
    }
    if (!allObjectsBelongTo(meshes, document) || !allMeshesNonEmpty(meshes)
        || !MeshGui::hasCleanNativeMutationBoundary(document)) {
        return;
    }

    Gui::ExactTransaction transaction(*document, QT_TRANSLATE_NOOP("Command", "Convert mesh to shape"));
    std::vector<App::DocumentObject*> sources(meshes.begin(), meshes.end());
    std::vector<App::DocumentObject*> outputs;
    outputs.reserve(meshes.size());
    for (auto* source : meshes) {
        std::string name = source->getNameInDocument();
        name += "_shape";
        auto* result = document->addObject<MeshPart::ShapeFromMesh>(name.c_str());
        result->Label.setValue(source->Label.getStrValue() + " (Shape)");
        result->Source.setValue(source);
        result->Tolerance.setValue(tolerance.value().getValue());
        result->SewShape.setValue(sew.isChecked());
        outputs.push_back(result);
    }
    document->recompute();
    if (std::ranges::any_of(outputs, [](const App::DocumentObject* output) {
            const auto* shape = freecad_cast<const MeshPart::ShapeFromMesh*>(output);
            return !shape || shape->Shape.getShape().isNull() || !shape->Shape.getShape().isValid()
                || shape->isError();
        })) {
        QMessageBox::critical(
            Gui::getMainWindow(),
            qApp->translate("MeshPart_ShapeFromMesh", "Shape From Mesh"),
            qApp->translate(
                "MeshPart_ShapeFromMesh",
                "The selected mesh could not be converted with these settings."
            )
        );
        return;
    }
    MeshGui::createSourcePreservingOutputGroup(
        *document,
        sources,
        outputs,
        "ConvertedMeshShapes",
        "Converted Mesh Shapes",
        "Convert mesh to shape"
    );
    if (!transaction.commit()) {
        throw Base::RuntimeError("Mesh-to-shape conversion could not be committed");
    }
}

bool CmdMeshPartShapeFromMesh::isActive()
{
    App::Document* document = cleanActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    return !meshes.empty() && allObjectsBelongTo(meshes, document) && allMeshesNonEmpty(meshes);
}

//--------------------------------------------------------------------------------------

DEF_STD_CMD_A(CmdMeshPartTrimByPlane)

CmdMeshPartTrimByPlane::CmdMeshPartTrimByPlane()
    : Command("MeshPart_TrimByPlane")
{
    sAppModule = "Mesh";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Trim Mesh");
    sToolTipText = QT_TR_NOOP("Trims a mesh with a plane");
    sStatusTip = sToolTipText;
}

void CmdMeshPartTrimByPlane::activated(int)
{
    App::Document* document = cleanActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    Base::Type partType = Base::Type::fromName("Part::Plane");
    std::vector<App::DocumentObject*> plane = getSelection().getObjectsOfType(partType);
    if (meshes.size() != 1 || plane.size() != 1 || !allObjectsBelongTo(meshes, document)
        || !allObjectsBelongTo(plane, document) || !allMeshesNonEmpty(meshes)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("MeshPart_TrimByPlane", "Select plane"),
            qApp->translate("MeshPart_TrimByPlane", "Select a plane to trim the mesh with.")
        );
        return;
    }
    App::DocumentWeakPtrT targetDocument(document);
    App::DocumentObjectWeakPtrT targetMesh(meshes.front());
    App::DocumentObjectWeakPtrT targetPlane(plane.front());

    QMessageBox msgBox(Gui::getMainWindow());
    msgBox.setIcon(QMessageBox::Question);
    msgBox.setWindowTitle(qApp->translate("MeshPart_TrimByPlane", "Trim With Plane"));
    msgBox.setText(qApp->translate("MeshPart_TrimByPlane", "Select the side to keep"));
    QPushButton* inner
        = msgBox.addButton(qApp->translate("MeshPart_TrimByPlane", "Below"), QMessageBox::ActionRole);
    QPushButton* outer
        = msgBox.addButton(qApp->translate("MeshPart_TrimByPlane", "Above"), QMessageBox::ActionRole);
    QPushButton* split
        = msgBox.addButton(qApp->translate("MeshPart_TrimByPlane", "Split"), QMessageBox::ActionRole);
    msgBox.addButton(QMessageBox::Cancel);
    msgBox.setDefaultButton(inner);
    msgBox.exec();
    QAbstractButton* click = msgBox.clickedButton();

    Gui::SelectionRole role;
    if (inner == click) {
        role = Gui::SelectionRole::Inner;
    }
    else if (outer == click) {
        role = Gui::SelectionRole::Outer;
    }
    else if (split == click) {
        role = Gui::SelectionRole::Split;
    }
    else {
        // abort
        return;
    }

    document = *targetDocument;
    auto* feature = targetMesh.get<Mesh::Feature>();
    auto* planeFeature = targetPlane.get<App::GeoFeature>();
    if (!MeshGui::hasCleanNativeMutationBoundary(document) || !feature
        || feature->getDocument() != document || !MeshGui::isNativeMeshInputActive(feature)
        || feature->Mesh.getValue().countFacets() == 0 || !planeFeature
        || planeFeature->getDocument() != document
        || !MeshGui::isNativeMeshInputActive(planeFeature)) {
        return;
    }
    Base::Placement plnPlacement = planeFeature->Placement.getValue();
    Base::Vector3d normal(0, 0, 1);
    plnPlacement.getRotation().multVec(normal, normal);
    Base::Vector3d base = plnPlacement.getPosition();
    Base::Vector3f plnBase = Base::convertTo<Base::Vector3f>(base);
    Base::Vector3f plnNormal = Base::convertTo<Base::Vector3f>(normal);

    const Mesh::MeshObject original = feature->Mesh.getValue();
    Mesh::MeshObject primary = original;
    std::optional<Mesh::MeshObject> secondary;
    if (role == Gui::SelectionRole::Inner) {
        primary.trimByPlane(plnBase, plnNormal);
    }
    else if (role == Gui::SelectionRole::Outer) {
        primary.trimByPlane(plnBase, -plnNormal);
    }
    else {
        secondary.emplace(primary);
        primary.trimByPlane(plnBase, plnNormal);
        secondary->trimByPlane(plnBase, -plnNormal);
    }
    if (primary.countFacets() == 0 || (secondary && secondary->countFacets() == 0)) {
        QMessageBox::information(
            Gui::getMainWindow(),
            qApp->translate("MeshPart_TrimByPlane", "Trim With Plane"),
            qApp->translate(
                "MeshPart_TrimByPlane",
                "The plane does not leave usable mesh geometry on every requested side."
            )
        );
        return;
    }
    if (sameMeshGeometry(primary, original) || (secondary && sameMeshGeometry(*secondary, original))) {
        QMessageBox::information(
            Gui::getMainWindow(),
            qApp->translate("MeshPart_TrimByPlane", "Trim With Plane"),
            qApp->translate("MeshPart_TrimByPlane", "The plane does not split the mesh.")
        );
        return;
    }

    std::vector<MeshGui::ParametricMeshFilterTarget> operations;
    const auto makeOperation = [feature, planeFeature](int side) {
        return MeshGui::ParametricMeshFilterTarget {
            feature,
            [planeFeature, side](App::DocumentObject& object) {
                auto& trim = static_cast<Mesh::TrimByPlane&>(object);
                trim.Plane.setValue(planeFeature);
                trim.Side.setValue(side);
            },
        };
    };
    if (role == Gui::SelectionRole::Inner) {
        operations.push_back(makeOperation(0));
    }
    else if (role == Gui::SelectionRole::Outer) {
        operations.push_back(makeOperation(1));
    }
    else {
        operations.push_back(makeOperation(0));
        operations.push_back(makeOperation(1));
    }
    MeshGui::createParametricMeshFilters(
        *document,
        operations,
        MeshGui::ParametricMeshFilterSpec {
            "Mesh::TrimByPlane",
            "TrimByPlane",
            role == Gui::SelectionRole::Split ? "Mesh Plane Split" : "Mesh Plane Trim",
            QT_TRANSLATE_NOOP("Command", "Trim with plane"),
            true,
            true,
            role == Gui::SelectionRole::Split,
            "PlaneSplit",
            "Split Mesh by Plane",
            "Plane split",
        }
    );
}

bool CmdMeshPartTrimByPlane::isActive()
{
    App::Document* document = cleanActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    Base::Type planeType = Base::Type::fromName("Part::Plane");
    auto planes = getSelection().getObjectsOfType(planeType);
    return meshes.size() == 1 && planes.size() == 1 && allObjectsBelongTo(meshes, document)
        && allObjectsBelongTo(planes, document) && allMeshesNonEmpty(meshes);
}

//===========================================================================
// MeshPart_Section
//===========================================================================
DEF_STD_CMD_A(CmdMeshPartSection)

CmdMeshPartSection::CmdMeshPartSection()
    : Command("MeshPart_SectionByPlane")
{
    sAppModule = "MeshPart";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Section");
    sToolTipText = QT_TR_NOOP("Creates a section from a mesh and plane");
    sWhatsThis = "MeshPart_Section";
    sStatusTip = sToolTipText;
}

void CmdMeshPartSection::activated(int)
{
    App::Document* document = cleanActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    Base::Type partType = Base::Type::fromName("Part::Plane");
    std::vector<App::DocumentObject*> plane = getSelection().getObjectsOfType(partType);
    if (meshes.size() != 1 || plane.size() != 1 || !allObjectsBelongTo(meshes, document)
        || !allObjectsBelongTo(plane, document) || !allMeshesNonEmpty(meshes)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            qApp->translate("MeshPart_Section", "Select plane"),
            qApp->translate("MeshPart_Section", "Select a plane to section the mesh with.")
        );
        return;
    }

    auto* meshFeature = meshes.front();
    auto* planeFeature = static_cast<App::GeoFeature*>(plane.front());
    if (!MeshGui::hasCleanNativeMutationBoundary(document)
        || !MeshGui::isNativeMeshInputActive(meshFeature)
        || !MeshGui::isNativeMeshInputActive(planeFeature)) {
        return;
    }

    Gui::ExactTransaction transaction(*document, QT_TRANSLATE_NOOP("Command", "Section with plane"));
    auto* result = document->addObject<MeshPart::SectionByPlane>("Section");
    result->Label.setValue("Mesh Plane Section");
    result->Source.setValue(meshFeature);
    result->Plane.setValue(planeFeature);
    result->MinimumLength.setValue(1.0e-7);
    result->ConnectEdges.setValue(true);
    document->recompute();
    if (result->Shape.getShape().isNull() || !result->Shape.getShape().isValid()) {
        QMessageBox::information(
            Gui::getMainWindow(),
            qApp->translate("MeshPart_Section", "Section"),
            qApp->translate("MeshPart_Section", "The plane does not intersect the mesh.")
        );
        return;
    }
    MeshGui::createSourcePreservingOutputGroup(
        *document,
        {meshFeature, planeFeature},
        {result},
        "MeshPlaneSections",
        "Mesh Plane Sections",
        "Section mesh with plane"
    );
    document->recompute();
    if (!transaction.commit()) {
        throw Base::RuntimeError("The mesh section could not be committed");
    }
}

bool CmdMeshPartSection::isActive()
{
    App::Document* document = cleanActiveDocument();
    auto meshes = getSelection().getObjectsOfType<Mesh::Feature>();
    Base::Type planeType = Base::Type::fromName("Part::Plane");
    auto planes = getSelection().getObjectsOfType(planeType);
    return meshes.size() == 1 && planes.size() == 1 && allObjectsBelongTo(meshes, document)
        && allObjectsBelongTo(planes, document) && allMeshesNonEmpty(meshes);
}

//===========================================================================
// MeshPart_CrossSections
//===========================================================================
DEF_STD_CMD_A(CmdMeshPartCrossSections)

CmdMeshPartCrossSections::CmdMeshPartCrossSections()
    : Command("MeshPart_CrossSections")
{
    sAppModule = "MeshPart";
    sGroup = QT_TR_NOOP("MeshPart");
    sMenuText = QT_TR_NOOP("Cross-Sections");
    sToolTipText = QT_TR_NOOP("Applies cross-sections to the mesh");
    sWhatsThis = "MeshPart_CrossSections";
    sStatusTip = sToolTipText;
    // sPixmap       = "MeshPart_CrossSections";
}

void CmdMeshPartCrossSections::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    if (!isActive()) {
        return;
    }
    auto meshes = Gui::Selection().getObjectsOfType<Mesh::Feature>();
    if (meshes.empty() || !allMeshesNonEmpty(meshes)) {
        return;
    }
    Base::BoundBox3d bbox;
    for (auto* mesh : meshes) {
        bbox.Add(mesh->Mesh.getBoundingBox());
    }
    auto* dlg = new MeshPartGui::TaskCrossSections(bbox);
    Gui::Control().showDialog(dlg);
}

bool CmdMeshPartCrossSections::isActive()
{
    App::Document* document = cleanActiveDocument();
    auto objects = getSelection().getObjectsOfType<Mesh::Feature>();
    return !objects.empty() && allObjectsBelongTo(objects, document) && allMeshesNonEmpty(objects);
}

DEF_STD_CMD_A(CmdMeshPartCurveOnMesh)

CmdMeshPartCurveOnMesh::CmdMeshPartCurveOnMesh()
    : Command("MeshPart_CurveOnMesh")
{
    sAppModule = "MeshPart";
    sGroup = QT_TR_NOOP("Mesh");
    sMenuText = QT_TR_NOOP("Curve on Mesh");
    sToolTipText = QT_TR_NOOP("Creates an approximated curve on top of a mesh object");
    sWhatsThis = "MeshPart_CurveOnMesh";
    sStatusTip = sToolTipText;
    sPixmap = "MeshPart_CurveOnMesh";
}

void CmdMeshPartCurveOnMesh::activated(int)
{
    if (!isActive()) {
        return;
    }
    Gui::Document* doc = getActiveGuiDocument();
    auto* view = doc ? dynamic_cast<Gui::View3DInventor*>(doc->getActiveView()) : nullptr;
    if (!view) {
        return;
    }

    Gui::Control().showDialog(new MeshPartGui::TaskCurveOnMesh(view));
}

bool CmdMeshPartCurveOnMesh::isActive()
{
    App::Document* document = cleanActiveDocument();
    return hasVisibleNonEmptyMesh(document);
}


void CreateMeshPartCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
    rcCmdMgr.addCommand(new CmdMeshPartMesher());
    rcCmdMgr.addCommand(new CmdMeshPartShapeFromMesh());
    rcCmdMgr.addCommand(new CmdMeshPartTrimByPlane());
    rcCmdMgr.addCommand(new CmdMeshPartSection());
    rcCmdMgr.addCommand(new CmdMeshPartCrossSections());
    rcCmdMgr.addCommand(new CmdMeshPartCurveOnMesh());
}
