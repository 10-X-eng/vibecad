/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                               *
 *   Copyright (c) 2023 Peter McB                                          *
 *   Copyright (c) 2013 Jürgen Riegel <juergen.riegel@web.de>              *
 *                                                                         *
 *   This file is part of FreeCAD.                                         *
 *                                                                         *
 *   FreeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 ***************************************************************************/

#include <algorithm>
#include <cstdio>
#include <exception>
#include <memory>
#include <set>
#include <string>

#include <Inventor/events/SoMouseButtonEvent.h>
#include <Inventor/nodes/SoCamera.h>
#include <Inventor/nodes/SoEventCallback.h>

#include <QApplication>
#include <QMessageBox>

#include <SMDSAbs_ElementType.hxx>
#include <SMDS_MeshElement.hxx>
#include <SMDS_MeshNode.hxx>
#include <SMESHDS_Mesh.hxx>
#include <SMESH_Mesh.hxx>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/DocumentObject.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Tools2D.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Utilities.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Gui/WaitCursor.h>
#include <Mod/Fem/App/FemMesh.h>
#include <Mod/Fem/App/FemMeshObject.h>

#include "TaskCreateElementSet.h"
#include "ViewProviderFemMesh.h"
#include "ui_TaskCreateElementSet.h"


using namespace FemGui;


std::string TaskCreateElementSet::currentProject;

namespace
{

SMDSAbs_ElementType primaryElementType(const Fem::FemMesh& mesh)
{
    const SMESHDS_Mesh* data = mesh.getSMesh()->GetMeshDS();
    constexpr SMDSAbs_ElementType orderedTypes[] {
        SMDSAbs_Volume,
        SMDSAbs_Face,
        SMDSAbs_Edge,
        SMDSAbs_0DElement,
        SMDSAbs_Ball,
    };

    for (SMDSAbs_ElementType type : orderedTypes) {
        if (data->GetMeshInfo().NbElements(type) > 0) {
            return type;
        }
    }
    return SMDSAbs_All;
}

std::set<int> elementIds(const Fem::FemMesh& mesh, SMDSAbs_ElementType type)
{
    std::set<int> ids;
    if (type == SMDSAbs_All) {
        return ids;
    }

    SMDS_ElemIteratorPtr elements = mesh.getSMesh()->GetMeshDS()->elementsIterator(type);
    while (elements->more()) {
        ids.insert(elements->next()->GetID());
    }
    return ids;
}

Base::Vector3f projectedCenter(const SMDS_MeshElement& element, const Gui::ViewVolumeProjection& projection)
{
    int nodeCount = element.NbCornerNodes();
    if (nodeCount <= 0) {
        nodeCount = element.NbNodes();
    }

    Base::Vector3f center;
    for (int index = 0; index < nodeCount; ++index) {
        const SMDS_MeshNode* node = element.GetNode(index);
        center.x += static_cast<float>(node->X());
        center.y += static_cast<float>(node->Y());
        center.z += static_cast<float>(node->Z());
    }

    if (nodeCount > 0) {
        center /= static_cast<float>(nodeCount);
    }
    return projection(center);
}

void markTimelineResource(App::DocumentObject* resource, App::DocumentObject* owner)
{
    if (!resource || !owner || resource == owner) {
        throw Base::ValueError("A filtered FEM mesh timeline resource requires a distinct owner");
    }
    if (!resource->getDocument() || resource->getDocument() != owner->getDocument()) {
        throw Base::ValueError("A filtered FEM mesh and its owner must share a document");
    }

    const auto ensureProperty =
        [](App::DocumentObject* object, const char* type, const char* name, const char* description) {
            auto* property = object->getPropertyByName(name);
            if (!property) {
                property = object->addDynamicProperty(
                    type,
                    name,
                    "Timeline",
                    description,
                    App::Prop_NoRecompute,
                    true,
                    true
                );
            }
            property->setStatus(App::Property::Hidden, true);
            property->setStatus(App::Property::LockDynamic, true);
            property->setStatus(App::Property::NoRecompute, true);
            return property;
        };
    auto* role = dynamic_cast<App::PropertyString*>(ensureProperty(
        resource,
        "App::PropertyString",
        App::DocumentTimeline::RolePropertyName,
        "Document timeline classification"
    ));
    auto* ownerProperty = dynamic_cast<App::PropertyLinkHidden*>(ensureProperty(
        resource,
        "App::PropertyLinkHidden",
        App::DocumentTimeline::OwnerPropertyName,
        "Erase Elements operation which owns this filtered mesh"
    ));
    if (!role || !ownerProperty) {
        throw Base::TypeError("Filtered FEM mesh timeline metadata properties have incompatible types");
    }

    ownerProperty->setValue(owner);
    role->setValue(App::DocumentTimeline::ResourceRole);
}

void markTimelineOperation(App::DocumentObject* operation)
{
    if (!operation || !operation->getDocument()
        || !operation->getDocument()->containsObject(operation)) {
        throw Base::ValueError("Erase Elements requires one live operation");
    }
    App::Property* property = operation->getPropertyByName(App::DocumentTimeline::RolePropertyName);
    if (!property) {
        property = operation->addDynamicProperty(
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName,
            "Timeline",
            "Document timeline classification",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    property->setStatus(App::Property::Hidden, true);
    property->setStatus(App::Property::LockDynamic, true);
    property->setStatus(App::Property::NoRecompute, true);
    auto* role = dynamic_cast<App::PropertyString*>(property);
    if (!role) {
        throw Base::TypeError("Erase Elements timeline role metadata has an incompatible type");
    }
    role->setValue(App::DocumentTimeline::OperationRole);
}

void markTimelineReplacedInput(App::DocumentObject* operation, App::DocumentObject* input)
{
    if (!operation || !input || operation == input) {
        throw Base::ValueError("Erase Elements requires a distinct replaced timeline input");
    }
    auto* document = operation->getDocument();
    if (!document || input->getDocument() != document || !document->containsObject(operation)
        || !document->containsObject(input)) {
        throw Base::ValueError(
            "An Erase Elements replaced input must be live in the operation document"
        );
    }

    App::Property* property = operation->getPropertyByName(
        App::DocumentTimeline::ReplacedInputsPropertyName
    );
    if (!property) {
        property = operation->addDynamicProperty(
            "App::PropertyLinkListHidden",
            App::DocumentTimeline::ReplacedInputsPropertyName,
            "Timeline",
            "Visible source mesh hidden by Erase Elements",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    property->setStatus(App::Property::Hidden, true);
    property->setStatus(App::Property::LockDynamic, true);
    property->setStatus(App::Property::NoRecompute, true);
    auto* replacedInputs = dynamic_cast<App::PropertyLinkListHidden*>(property);
    if (!replacedInputs) {
        throw Base::TypeError("Erase Elements replaced-input metadata has an incompatible type");
    }

    App::Property* roleProperty = operation->getPropertyByName(App::DocumentTimeline::RolePropertyName);
    if (!roleProperty) {
        roleProperty = operation->addDynamicProperty(
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName,
            "Timeline",
            "Document timeline classification",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    roleProperty->setStatus(App::Property::Hidden, true);
    roleProperty->setStatus(App::Property::LockDynamic, true);
    roleProperty->setStatus(App::Property::NoRecompute, true);
    auto* role = dynamic_cast<App::PropertyString*>(roleProperty);
    if (!role) {
        throw Base::TypeError("Erase Elements timeline role metadata has an incompatible type");
    }

    replacedInputs->setValues({input});
    role->setValue(App::DocumentTimeline::OperationRole);
}

}  // namespace


TaskCreateElementSet::TaskCreateElementSet(Fem::FemSetElementNodesObject* object, QWidget* parent)
    : TaskBox(Gui::BitmapFactory().pixmap("FEM_CreateElementsSet"), tr("Erase mesh elements"), true, parent)
    , MeshViewProvider(nullptr)
    , pcObject(object)
    , selectionMode(none)
    , proxy(new QWidget(this))
    , ui(std::make_unique<Ui_TaskCreateElementSet>())
    , document(object ? object->getDocument() : nullptr)
    , sourceMeshObject(nullptr)
    , previewMeshObject(nullptr)
    , sourceMeshViewProvider(nullptr)
    , polygonViewer(nullptr)
    , sourceWasVisible(false)
    , operationWasTimelineOperation(App::DocumentTimeline::hasTimelineOperationRole(object))
{
    if (!pcObject || !document || !pcObject->getNameInDocument()) {
        throw Base::RuntimeError("Erase Elements requires a document-owned element-set object");
    }

    operationObjectName = pcObject->getNameInDocument();
    sourceMeshObject = pcObject->FemMesh.getValue<Fem::FemMeshObject*>();
    if (!sourceMeshObject || sourceMeshObject->getDocument() != document
        || !sourceMeshObject->getNameInDocument()) {
        throw Base::RuntimeError("Erase Elements requires a FEM mesh in the same document");
    }
    sourceMeshName = sourceMeshObject->getNameInDocument();

    sourceMeshViewProvider = Gui::Application::Instance->getViewProvider<ViewProviderFemMesh>(
        sourceMeshObject
    );
    if (!sourceMeshViewProvider) {
        throw Base::RuntimeError("Erase Elements could not find the source mesh view");
    }

    MeshViewProvider = sourceMeshViewProvider;
    sourceWasVisible = sourceMeshViewProvider->Visibility.getValue();
    sourceMeshSnapshot = std::make_unique<Fem::FemMesh>(sourceMeshObject->FemMesh.getValue());
    workingMesh = std::make_unique<Fem::FemMesh>(*sourceMeshSnapshot);
    elementTempSet = pcObject->Elements.getValues();

    if (operationWasTimelineOperation) {
        auto* timeline = App::DocumentTimeline::get(document);
        if (!timeline) {
            throw Base::RuntimeError("Erase Elements has no native document timeline");
        }
        std::vector<App::DocumentObject*> directRoots;
        for (auto* candidate : timeline->Operations.getValues()) {
            if (!candidate || candidate == pcObject) {
                continue;
            }
            auto* current = candidate;
            std::set<App::DocumentObject*> visited;
            while (auto* owner = App::DocumentTimeline::timelineOwner(current)) {
                if (!visited.insert(current).second) {
                    throw Base::RuntimeError("Erase Elements has a cyclic timeline resource graph");
                }
                current = owner;
            }
            if (current != pcObject) {
                continue;
            }
            oldTimelineResources.emplace_back(candidate->getNameInDocument(), candidate->getID());
            if (App::DocumentTimeline::timelineOwner(candidate) == pcObject) {
                directRoots.push_back(candidate);
            }
        }
        document->stageTimelineOperationResourceReconciliation(pcObject, directRoots);
    }

    ui->setupUi(proxy);
    QMetaObject::connectSlotsByName(this);
    groupLayout()->addWidget(proxy);
    QObject::connect(ui->toolButton_Select, &QToolButton::clicked, this, &TaskCreateElementSet::Poly);
    QObject::connect(ui->toolButton_Restore, &QToolButton::clicked, this, &TaskCreateElementSet::Restore);
    QObject::connect(
        ui->toolButton_Copy,
        &QToolButton::clicked,
        this,
        &TaskCreateElementSet::CopyResultsMesh
    );
}

bool TaskCreateElementSet::ownsObject(const App::DocumentObject* object, const std::string& name) const
{
    return document && object && !name.empty() && document->getObject(name.c_str()) == object;
}

void TaskCreateElementSet::Poly()
{
    stopPolygonSelection();

    if (!ownsObject(pcObject, operationObjectName) || !ownsObject(sourceMeshObject, sourceMeshName)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The source mesh or operation is no longer available.")
        );
        return;
    }

    Gui::Document* guiDocument = Gui::Application::Instance->getDocument(document);
    if (!guiDocument) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The owning document no longer has a GUI view.")
        );
        return;
    }

    Gui::MDIView* mdiView = guiDocument->getActiveView();
    if (!mdiView || !mdiView->isDerivedFrom<Gui::View3DInventor>()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("Open a 3D view for this document before drawing a polygon.")
        );
        return;
    }

    polygonViewer = static_cast<Gui::View3DInventor*>(mdiView)->getViewer();
    if (!polygonViewer) {
        return;
    }

    polygonViewer->setEditing(true);
    polygonViewer->startSelection(Gui::View3DInventorViewer::Clip);
    polygonViewer->addEventCallback(SoMouseButtonEvent::getClassTypeId(), DefineElementsCallback, this);
}

void TaskCreateElementSet::stopPolygonSelection()
{
    if (!polygonViewer) {
        return;
    }

    polygonViewer->removeEventCallback(SoMouseButtonEvent::getClassTypeId(), DefineElementsCallback, this);
    polygonViewer->setEditing(false);
    polygonViewer = nullptr;
}

void TaskCreateElementSet::DefineElementsCallback(void* userData, SoEventCallback* callback)
{
    Gui::WaitCursor waitCursor;
    auto* task = static_cast<TaskCreateElementSet*>(userData);
    auto* viewer = static_cast<Gui::View3DInventorViewer*>(callback->getUserData());

    viewer->setEditing(false);
    viewer->removeEventCallback(SoMouseButtonEvent::getClassTypeId(), DefineElementsCallback, userData);
    if (task->polygonViewer == viewer) {
        task->polygonViewer = nullptr;
    }
    callback->setHandled();

    Gui::SelectionRole role;
    std::vector<SbVec2f> coordinates = viewer->getGLPolygon(&role);
    if (coordinates.size() < 3) {
        return;
    }
    if (coordinates.front() != coordinates.back()) {
        coordinates.push_back(coordinates.front());
    }

    SoCamera* camera = viewer->getSoRenderManager()->getCamera();
    Gui::ViewVolumeProjection projection(camera->getViewVolume());
    Base::Polygon2d polygon;
    for (const SbVec2f& coordinate : coordinates) {
        polygon.Add(Base::Vector2d(coordinate[0], coordinate[1]));
    }

    task->DefineNodes(polygon, projection, role == Gui::SelectionRole::Inner);
}

void TaskCreateElementSet::DefineNodes(
    const Base::Polygon2d& polygon,
    const Gui::ViewVolumeProjection& projection,
    bool inner
)
{
    if (!workingMesh || !ownsObject(pcObject, operationObjectName)
        || !ownsObject(sourceMeshObject, sourceMeshName)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The working mesh is no longer available.")
        );
        return;
    }

    const SMDSAbs_ElementType type = primaryElementType(*workingMesh);
    const std::set<int> allIds = elementIds(*workingMesh, type);
    if (allIds.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The working mesh has no erasable elements.")
        );
        return;
    }

    std::set<int> removeIds;
    SMDS_ElemIteratorPtr elements = workingMesh->getSMesh()->GetMeshDS()->elementsIterator(type);
    while (elements->more()) {
        const SMDS_MeshElement* element = elements->next();
        const Base::Vector3f center = projectedCenter(*element, projection);
        const bool inside = polygon.Contains(Base::Vector2d(center.x, center.y));
        if (inside == inner) {
            removeIds.insert(element->GetID());
        }
    }

    if (removeIds.empty()) {
        QMessageBox::information(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The polygon did not select any mesh elements.")
        );
        return;
    }
    if (removeIds.size() == allIds.size()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("That polygon would erase every element. Draw the polygon in the opposite direction "
               "to keep its inside instead.")
        );
        return;
    }

    try {
        Fem::FemMesh filtered(*workingMesh);
        filtered.removeElements(removeIds, true);

        if (publishWorkingMesh(filtered)) {
            Base::Console().message(
                "Erase Elements removed %zu elements; %zu remain in the working mesh.\n",
                removeIds.size(),
                allIds.size() - removeIds.size()
            );
        }
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The mesh could not be filtered: %1").arg(QString::fromUtf8(error.what()))
        );
        return;
    }
    catch (const std::exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The mesh could not be filtered: %1").arg(QString::fromUtf8(error.what()))
        );
        return;
    }
}

void TaskCreateElementSet::ensurePreviewObject(const Fem::FemMesh& mesh)
{
    if (ownsObject(previewMeshObject, previewMeshName)) {
        return;
    }

    // An externally removed preview must never leave a stale public view
    // provider pointer behind.
    previewMeshObject = nullptr;
    previewMeshName.clear();
    MeshViewProvider = sourceMeshViewProvider;

    if (!ownsObject(pcObject, operationObjectName) || !ownsObject(sourceMeshObject, sourceMeshName)) {
        throw Base::RuntimeError("The source mesh or Erase Elements operation was removed");
    }

    const std::string uniqueName = document->getUniqueObjectName("FilteredMesh");
    auto* added = document->addObject("Fem::FemMeshObject", uniqueName.c_str());
    auto* preview = freecad_cast<Fem::FemMeshObject*>(added);
    if (!preview) {
        if (added && added->getNameInDocument()) {
            document->removeObject(added->getNameInDocument());
        }
        throw Base::RuntimeError("Could not create the filtered FEM mesh");
    }
    previewMeshObject = preview;
    previewMeshName = preview->getNameInDocument();

    try {
        markTimelineOperation(pcObject);
        markTimelineResource(previewMeshObject, pcObject);
        if (sourceWasVisible) {
            markTimelineReplacedInput(pcObject, sourceMeshObject);
        }
        const QString label
            = tr("%1 (filtered)").arg(QString::fromUtf8(sourceMeshObject->Label.getValue()));
        previewMeshObject->Label.setValue(label.toUtf8().constData());
        previewMeshObject->FemMesh.setValue(mesh);

        auto* previewViewProvider = Gui::Application::Instance->getViewProvider<ViewProviderFemMesh>(
            previewMeshObject
        );
        if (!previewViewProvider) {
            throw Base::RuntimeError("Could not create the filtered mesh view");
        }

        pcObject->FemMesh.setValue(previewMeshObject);
        sourceMeshViewProvider->Visibility.setValue(false);
        previewViewProvider->Visibility.setValue(true);
        MeshViewProvider = previewViewProvider;
    }
    catch (...) {
        const std::exception_ptr failure = std::current_exception();
        try {
            if (ownsObject(pcObject, operationObjectName)
                && ownsObject(sourceMeshObject, sourceMeshName)) {
                pcObject->FemMesh.setValue(sourceMeshObject);
            }
        }
        catch (...) {
        }
        try {
            if (ownsObject(sourceMeshObject, sourceMeshName)) {
                sourceMeshViewProvider->Visibility.setValue(sourceWasVisible);
            }
        }
        catch (...) {
        }
        try {
            if (ownsObject(previewMeshObject, previewMeshName)) {
                document->removeObject(previewMeshName.c_str());
            }
        }
        catch (...) {
        }
        previewMeshObject = nullptr;
        previewMeshName.clear();
        MeshViewProvider = sourceMeshViewProvider;
        std::rethrow_exception(failure);
    }
}

void TaskCreateElementSet::finalizeTimelineBlock()
{
    if (!ownsObject(pcObject, operationObjectName) || !ownsObject(sourceMeshObject, sourceMeshName)
        || !ownsObject(previewMeshObject, previewMeshName)) {
        throw Base::RuntimeError("Erase Elements has no complete operation result to finalize");
    }
    const auto resolveMeshViewProvider = [this](Fem::FemMeshObject* object, const std::string& name) {
        return ownsObject(object, name)
            ? Gui::Application::Instance->getViewProvider<ViewProviderFemMesh>(object)
            : nullptr;
    };
    auto* sourceViewProvider = resolveMeshViewProvider(sourceMeshObject, sourceMeshName);
    auto* previewViewProvider = resolveMeshViewProvider(previewMeshObject, previewMeshName);
    if (!sourceViewProvider || !previewViewProvider || !App::DocumentTimeline::get(document)) {
        throw Base::RuntimeError("Erase Elements has no complete live result presentation");
    }

    // A new operation's semantic publication is allowed to classify its
    // provisional outputs, but it must not observe a pre-existing operation
    // in a state changed by this transaction.  The source mesh is hidden
    // during the interactive preview, so briefly restore its exact launch
    // visibility for publication and apply the accepted replacement state
    // immediately afterward in the same transaction.
    const bool restoreVisibleSource = sourceWasVisible && !sourceViewProvider->Visibility.getValue();
    if (restoreVisibleSource) {
        sourceViewProvider->Visibility.setValue(true);
    }

    try {
        if (!ownsObject(pcObject, operationObjectName)
            || !ownsObject(sourceMeshObject, sourceMeshName)
            || !ownsObject(previewMeshObject, previewMeshName)) {
            throw Base::RuntimeError("Erase Elements changed exact identity before publication");
        }
        auto* timeline = App::DocumentTimeline::get(document);
        if (!timeline) {
            throw Base::RuntimeError("Erase Elements has no native document timeline");
        }
        if (!operationWasTimelineOperation) {
            timeline->publishProvisionalOperationBlock(pcObject, {previewMeshObject});
        }
        else {
            App::TimelineResourceReconciliationMapping mapping;
            mapping.owner = pcObject;
            mapping.orderedFinalResources.reserve(oldTimelineResources.size() + 1);
            mapping.stateSourceIndices.reserve(oldTimelineResources.size() + 1);
            mapping.consumerReplacementIndices.reserve(oldTimelineResources.size());
            for (std::size_t index = 0; index < oldTimelineResources.size(); ++index) {
                const auto& [name, objectId] = oldTimelineResources[index];
                auto* resource = document->getObject(name.c_str());
                if (!resource || resource->getID() != objectId) {
                    throw Base::RuntimeError(
                        "An existing Erase Elements resource changed exact identity"
                    );
                }
                mapping.orderedFinalResources.push_back(resource);
                mapping.stateSourceIndices.push_back(static_cast<long>(index));
                mapping.consumerReplacementIndices.push_back(static_cast<long>(index));
            }
            if (std::ranges::find(mapping.orderedFinalResources, previewMeshObject)
                == mapping.orderedFinalResources.end()) {
                mapping.orderedFinalResources.push_back(previewMeshObject);
                mapping.stateSourceIndices.push_back(-1);
            }
            document->finalizeProvisionalTimelineOperationResourceReconciliation(mapping);
        }
    }
    catch (...) {
        sourceViewProvider = resolveMeshViewProvider(sourceMeshObject, sourceMeshName);
        if (restoreVisibleSource && sourceViewProvider) {
            sourceViewProvider->Visibility.setValue(false);
        }
        throw;
    }

    sourceViewProvider = resolveMeshViewProvider(sourceMeshObject, sourceMeshName);
    previewViewProvider = resolveMeshViewProvider(previewMeshObject, previewMeshName);
    if (!sourceViewProvider || !previewViewProvider) {
        throw Base::RuntimeError("Erase Elements result presentation changed during publication");
    }
    if (sourceWasVisible) {
        sourceViewProvider->Visibility.setValue(false);
    }
    previewViewProvider->Visibility.setValue(true);
    sourceMeshViewProvider = sourceViewProvider;
    MeshViewProvider = previewViewProvider;
}

bool TaskCreateElementSet::publishWorkingMesh(const Fem::FemMesh& mesh)
{
    const bool alreadyHadPreview = ownsObject(previewMeshObject, previewMeshName);
    const auto rollbackFailedUpdate = [this, alreadyHadPreview]() {
        try {
            if (alreadyHadPreview && ownsObject(previewMeshObject, previewMeshName) && workingMesh) {
                previewMeshObject->FemMesh.setValue(*workingMesh);
                document->recompute();
            }
            else {
                if (ownsObject(pcObject, operationObjectName)
                    && ownsObject(sourceMeshObject, sourceMeshName)) {
                    pcObject->FemMesh.setValue(sourceMeshObject);
                    sourceMeshViewProvider->Visibility.setValue(sourceWasVisible);
                }
                if (ownsObject(previewMeshObject, previewMeshName)) {
                    const std::string failedPreviewName = previewMeshName;
                    document->removeObject(failedPreviewName.c_str());
                }
                previewMeshObject = nullptr;
                previewMeshName.clear();
                MeshViewProvider = sourceMeshViewProvider;
            }
        }
        catch (const Base::Exception& rollbackError) {
            Base::Console().warning(
                "Erase Elements could not restore its failed preview update: %s\n",
                rollbackError.what()
            );
        }
        catch (const std::exception& rollbackError) {
            Base::Console().warning(
                "Erase Elements could not restore its failed preview update: %s\n",
                rollbackError.what()
            );
        }
        catch (...) {
            Base::Console().warning("Erase Elements could not restore its failed preview update.\n");
        }
    };

    try {
        // Stage every allocation before touching the document.  This keeps
        // the previous preview and element set intact if cloning fails.
        auto nextWorkingMesh = std::make_unique<Fem::FemMesh>(mesh);
        std::set<long> nextElementSet = elementSetForMesh(*nextWorkingMesh);

        if (!ownsObject(pcObject, operationObjectName)
            || !ownsObject(sourceMeshObject, sourceMeshName)) {
            throw Base::RuntimeError("The source mesh or Erase Elements operation was removed");
        }
        ensurePreviewObject(mesh);
        if (alreadyHadPreview) {
            previewMeshObject->FemMesh.setValue(mesh);
        }

        pcObject->FemMesh.setValue(previewMeshObject);
        sourceMeshViewProvider->Visibility.setValue(false);
        MeshViewProvider->Visibility.setValue(true);
        document->recompute();

        workingMesh = std::move(nextWorkingMesh);
        elementTempSet = std::move(nextElementSet);
        return true;
    }
    catch (const Base::Exception& error) {
        rollbackFailedUpdate();
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The mesh preview could not be updated: %1").arg(QString::fromUtf8(error.what()))
        );
    }
    catch (const std::exception& error) {
        rollbackFailedUpdate();
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The mesh preview could not be updated: %1").arg(QString::fromUtf8(error.what()))
        );
    }
    catch (...) {
        rollbackFailedUpdate();
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The mesh preview could not be updated.")
        );
    }

    return false;
}

std::set<long> TaskCreateElementSet::elementSetForMesh(const Fem::FemMesh& mesh) const
{
    std::set<long> elements;
    const SMDSAbs_ElementType type = primaryElementType(mesh);
    if (type == SMDSAbs_All) {
        return elements;
    }

    // The negative type marker is part of the existing ElementsSet document
    // contract.  Keep it while replacing the old file-based implementation.
    elements.insert(-static_cast<long>(type));
    const std::set<int> ids = elementIds(mesh, type);
    elements.insert(ids.begin(), ids.end());
    return elements;
}

void TaskCreateElementSet::Restore()
{
    stopPolygonSelection();
    if (!sourceMeshSnapshot) {
        return;
    }

    if (ownsObject(previewMeshObject, previewMeshName)) {
        publishWorkingMesh(*sourceMeshSnapshot);
        return;
    }

    try {
        auto restoredMesh = std::make_unique<Fem::FemMesh>(*sourceMeshSnapshot);
        std::set<long> restoredElementSet = elementSetForMesh(*restoredMesh);
        workingMesh = std::move(restoredMesh);
        elementTempSet = std::move(restoredElementSet);
        MeshViewProvider = sourceMeshViewProvider;
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The source mesh could not be restored: %1").arg(QString::fromUtf8(error.what()))
        );
    }
    catch (const std::exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Erase Elements"),
            tr("The source mesh could not be restored: %1").arg(QString::fromUtf8(error.what()))
        );
    }
}

void TaskCreateElementSet::CopyResultsMesh()
{
    stopPolygonSelection();
    if (!document) {
        return;
    }

    const auto selection
        = Gui::Selection().getSelection(document->getName(), Gui::ResolveMode::NoResolve, true);
    if (selection.size() != 1) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Copy FEM Mesh"),
            tr("Select exactly one FEM mesh in this document.")
        );
        return;
    }

    auto* selectedMesh = freecad_cast<Fem::FemMeshObject*>(selection.front().pObject);
    if (!selectedMesh || selectedMesh->getDocument() != document) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Copy FEM Mesh"),
            tr("The selected object is not a FEM mesh from this document.")
        );
        return;
    }

    if (selectedMesh == previewMeshObject) {
        QMessageBox::information(
            Gui::getMainWindow(),
            tr("Copy FEM Mesh"),
            tr("The selected mesh is already the working preview.")
        );
        return;
    }

    try {
        Fem::FemMesh copiedMesh(selectedMesh->FemMesh.getValue());
        if (primaryElementType(copiedMesh) == SMDSAbs_All) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                tr("Copy FEM Mesh"),
                tr("The selected FEM mesh contains no elements.")
            );
            return;
        }

        publishWorkingMesh(copiedMesh);
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Copy FEM Mesh"),
            tr("The selected FEM mesh could not be copied: %1").arg(QString::fromUtf8(error.what()))
        );
    }
    catch (const std::exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Copy FEM Mesh"),
            tr("The selected FEM mesh could not be copied: %1").arg(QString::fromUtf8(error.what()))
        );
    }
}

void TaskCreateElementSet::onSelectionChanged(const Gui::SelectionChanges& message)
{
    if (selectionMode != PickElement || !document || !message.pDocName
        || std::string(message.pDocName) != document->getName() || !message.pObjectName
        || !message.pSubName || message.Type != Gui::SelectionChanges::AddSelection) {
        return;
    }

    if (!ownsObject(pcObject, operationObjectName)) {
        return;
    }

    Fem::FemMeshObject* currentMesh = pcObject->FemMesh.getValue<Fem::FemMeshObject*>();
    if (!currentMesh || document->getObject(message.pObjectName) != currentMesh) {
        return;
    }

    long elementId = 0;
    int faceId = 0;
    if (std::sscanf(message.pSubName, "Elem%ldF%d", &elementId, &faceId) != 2 || elementId <= 0) {
        return;
    }

    elementTempSet.clear();
    elementTempSet.insert(elementId);
    selectionMode = none;
    Gui::Selection().rmvSelectionGate();
}

TaskCreateElementSet::~TaskCreateElementSet()
{
    stopPolygonSelection();
    Gui::Selection().rmvSelectionGate();
}

#include "moc_TaskCreateElementSet.cpp"
