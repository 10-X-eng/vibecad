// SPDX-License-Identifier: LGPL-2.1-or-later

#include "OperationSupport.h"

#include <algorithm>
#include <iterator>
#include <ranges>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentObserver.h>
#include <App/DocumentTimeline.h>
#include <App/PropertyLinks.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/ExactTransaction.h>
#include <Gui/ViewProvider.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>
#include <Mod/Mesh/Gui/CommandGuard.h>
#include <Mod/Mesh/Gui/ParametricMeshFilter.h>

using namespace ReverseEngineeringGui;

App::Document* OperationSupport::cleanActiveDocument()
{
    auto* document = App::GetApplication().getActiveDocument();
    return MeshGui::canStartNativeMeshCommand(document) ? document : nullptr;
}

App::DocumentObject* OperationSupport::usableTaskSource(const App::DocumentObjectT& source) noexcept
{
    try {
        auto* document = source.getDocument();
        auto* object = source.getObject();
        return document && App::GetApplication().getActiveDocument() == document
                && MeshGui::hasCleanNativeMutationBoundary(document)
                && isUsableSource(object, document)
            ? object
            : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

bool OperationSupport::isUsableSource(
    const App::DocumentObject* object,
    const App::Document* document
) noexcept
{
    return object && document && object->getDocument() == document
        && MeshGui::isNativeMeshInputActive(object);
}

bool OperationSupport::areUsableSources(
    const std::vector<App::DocumentObject*>& objects,
    const App::Document* document
) noexcept
{
    return document && !objects.empty()
        && std::ranges::all_of(objects, [document](const App::DocumentObject* object) {
               return isUsableSource(object, document);
           });
}

std::unordered_set<long> OperationSupport::objectIds(const App::Document& document)
{
    std::unordered_set<long> result;
    for (const auto* object : document.getObjects()) {
        if (object) {
            result.insert(object->getID());
        }
    }
    return result;
}

std::vector<App::DocumentObject*> OperationSupport::createdObjects(
    App::Document& document,
    const std::unordered_set<long>& previousIds
)
{
    std::vector<App::DocumentObject*> result;
    for (auto* object : document.getObjects()) {
        if (object && !previousIds.contains(object->getID())
            && !object->isDerivedFrom<App::DocumentTimeline>()) {
            result.push_back(object);
        }
    }
    return result;
}

void OperationSupport::setSource(App::DocumentObject& output, App::DocumentObject& source)
{
    auto* property = output.getPropertyByName("Source");
    if (!property) {
        property = output.addDynamicProperty(
            "App::PropertyLink",
            "Source",
            "Operation",
            "Geometry used to create this reconstruction",
            App::Prop_ReadOnly,
            true,
            true
        );
    }
    auto* link = dynamic_cast<App::PropertyLink*>(property);
    if (!link) {
        throw Base::TypeError("The reconstruction Source property has an incompatible type");
    }
    link->setValue(&source);
}

void OperationSupport::setSources(
    App::DocumentObject& output,
    const std::vector<App::DocumentObject*>& sources
)
{
    auto* property = output.getPropertyByName("Sources");
    if (!property) {
        property = output.addDynamicProperty(
            "App::PropertyLinkList",
            "Sources",
            "Operation",
            "Geometry used to create this reconstruction",
            App::Prop_ReadOnly,
            true,
            true
        );
    }
    auto* links = dynamic_cast<App::PropertyLinkList*>(property);
    if (!links) {
        throw Base::TypeError("The reconstruction Sources property has an incompatible type");
    }
    links->setValues(sources);
}

void OperationSupport::publishSourcePreserving(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const std::vector<App::DocumentObject*>& outputs,
    const char* objectName,
    const char* label,
    const char* operationKind
)
{
    MeshGui::createSourcePreservingOutputGroup(document, sources, outputs, objectName, label, operationKind);
}

void OperationSupport::publishGroupedOperation(
    App::Document& document,
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& sources,
    const std::vector<App::DocumentObject*>& resources
)
{
    if (sources.size() == 1) {
        setSource(operation, *sources.front());
    }
    else {
        setSources(operation, sources);
    }
    auto* timeline = App::DocumentTimeline::ensure(document);
    if (!timeline) {
        throw Base::RuntimeError("The reconstruction operation could not create document History");
    }
    timeline->publishProvisionalOperationBlock(&operation, resources);
}

Mesh::OutputGroup* OperationSupport::publishOutputGroup(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const std::vector<App::DocumentObject*>& outputs,
    const char* objectName,
    const char* label,
    const char* operationKind,
    bool replacesVisibleSources
)
{
    if (sources.empty() || outputs.empty()) {
        throw Base::ValueError("A reconstruction output group requires sources and results");
    }

    std::vector<App::DocumentObject*> exactSources;
    std::unordered_set<App::DocumentObject*> seenSources;
    exactSources.reserve(sources.size());
    for (auto* source : sources) {
        if (!isUsableSource(source, &document) || !seenSources.insert(source).second) {
            throw Base::ValueError("Every reconstruction source must be distinct and usable");
        }
        exactSources.push_back(source);
    }

    std::unordered_set<App::DocumentObject*> seenOutputs;
    for (auto* output : outputs) {
        if (!output || output->getDocument() != &document || !output->getNameInDocument()
            || !document.containsObject(output) || seenSources.contains(output)
            || !seenOutputs.insert(output).second) {
            throw Base::ValueError("Every reconstruction result must be a distinct live object");
        }
    }

    const char* requestedName = objectName && objectName[0] != '\0' ? objectName : "Reconstruction";
    const std::string uniqueName = document.getUniqueObjectName(requestedName);
    auto* group = document.addObject<Mesh::OutputGroup>(uniqueName.c_str());
    if (!group) {
        throw Base::RuntimeError("The reconstruction operation controller could not be created");
    }
    group->Label.setValue(label && label[0] != '\0' ? label : "Reconstruction");
    group->Sources.setValues(exactSources);
    group->OperationKind.setValue(
        operationKind && operationKind[0] != '\0' ? operationKind : "Reverse engineering"
    );
    group->InputMode.setValue(replacesVisibleSources ? 0L : 1L);

    for (auto* output : outputs) {
        MeshGui::markMeshTimelineResource(*output, *group);
    }
    const auto added = group->addObjects(outputs);
    if (added.size() != outputs.size()
        || static_cast<std::size_t>(group->Group.getSize()) != outputs.size()) {
        throw Base::RuntimeError("The reconstruction operation could not own every result");
    }

    std::vector<App::DocumentObject*> replacedInputs;
    if (replacesVisibleSources) {
        std::ranges::copy_if(
            exactSources,
            std::back_inserter(replacedInputs),
            [](const App::DocumentObject* source) { return source->Visibility.getValue(); }
        );
    }
    MeshGui::markMeshTimelineReplacement(*group, replacedInputs);

    auto* timeline = App::DocumentTimeline::ensure(document);
    if (!timeline) {
        throw Base::RuntimeError("The reconstruction operation could not create document History");
    }
    timeline->publishProvisionalOperationBlock(group, outputs);

    if (replacesVisibleSources) {
        for (auto* source : exactSources) {
            if (auto* view = Gui::Application::Instance->getViewProvider(source)) {
                view->setVisible(false);
            }
            else {
                source->Visibility.setValue(false);
            }
        }
    }
    return group;
}

void OperationSupport::commit(Gui::ExactTransaction& transaction)
{
    if (!transaction.commit()) {
        throw Base::RuntimeError("The reconstruction operation could not be committed");
    }
}
