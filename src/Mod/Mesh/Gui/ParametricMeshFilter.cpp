// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ParametricMeshFilter.h"

#include <algorithm>
#include <iterator>
#include <ranges>
#include <string>
#include <unordered_set>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/ExactTransaction.h>
#include <Gui/ViewProvider.h>

#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Mesh/App/FeatureMeshOperations.h>

#include "CommandGuard.h"

namespace
{

enum class OutputInputMode
{
    Replacement = 0,
    SourcePreserving = 1,
    Standalone = 2,
};

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

std::vector<App::DocumentObject*> uniqueSources(
    const std::vector<MeshGui::ParametricMeshFilterTarget>& targets
)
{
    std::vector<App::DocumentObject*> sources;
    std::unordered_set<App::DocumentObject*> seen;
    sources.reserve(targets.size());
    for (const auto& target : targets) {
        if (target.source && seen.insert(target.source).second) {
            sources.push_back(target.source);
        }
    }
    return sources;
}

std::vector<App::DocumentObject*> uniqueSources(
    const std::vector<MeshGui::StoredMeshEditTarget>& targets
)
{
    std::vector<App::DocumentObject*> sources;
    std::unordered_set<App::DocumentObject*> seen;
    sources.reserve(targets.size());
    for (const auto& target : targets) {
        if (target.source && seen.insert(target.source).second) {
            sources.push_back(target.source);
        }
    }
    return sources;
}

template<typename Target>
std::vector<App::DocumentObject*> visibleSourcesAtInvocation(const std::vector<Target>& targets)
{
    std::vector<App::DocumentObject*> sources;
    std::unordered_set<App::DocumentObject*> seen;
    sources.reserve(targets.size());
    for (const auto& target : targets) {
        if (target.source && target.source->Visibility.getValue()
            && seen.insert(target.source).second) {
            sources.push_back(target.source);
        }
    }
    return sources;
}

Mesh::OutputGroup* createOutputGroup(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const char* objectName,
    const char* label,
    const char* operationKind,
    OutputInputMode inputMode = OutputInputMode::Replacement
)
{
    if (inputMode != OutputInputMode::Standalone && sources.empty()) {
        throw Base::RuntimeError("A mesh output group requires at least one source");
    }
    const char* requestedName = objectName && objectName[0] != '\0' ? objectName : "MeshOutputGroup";
    const std::string uniqueName = document.getUniqueObjectName(requestedName);
    auto* group = document.addObject<Mesh::OutputGroup>(uniqueName.c_str());
    if (!group) {
        throw Base::RuntimeError("The mesh operation controller could not be created");
    }
    group->Label.setValue(label && label[0] != '\0' ? label : "Mesh Operation");
    group->Sources.setValues(sources);
    group->OperationKind.setValue(
        operationKind && operationKind[0] != '\0' ? operationKind : "Mesh operation"
    );
    group->InputMode.setValue(static_cast<long>(inputMode));
    return group;
}

void ownOutputResources(Mesh::OutputGroup& group, const std::vector<App::DocumentObject*>& results)
{
    std::vector<App::DocumentObject*> resources;
    resources.reserve(results.size());
    for (auto* result : results) {
        if (!result) {
            throw Base::RuntimeError("A mesh operation produced a missing result");
        }
        MeshGui::markMeshTimelineResource(*result, group);
        resources.push_back(result);
    }
    const auto added = group.addObjects(resources);
    if (added.size() != resources.size()
        || static_cast<std::size_t>(group.Group.getSize()) != resources.size()) {
        throw Base::RuntimeError("The mesh operation controller could not own every physical result");
    }
}

template<typename Object>
std::vector<App::DocumentObject*> asDocumentObjects(const std::vector<Object*>& objects)
{
    std::vector<App::DocumentObject*> result;
    result.reserve(objects.size());
    std::ranges::transform(objects, std::back_inserter(result), [](Object* object) {
        return static_cast<App::DocumentObject*>(object);
    });
    return result;
}

void storeExternalInputIdentities(
    App::DocumentObject& operation,
    const std::vector<std::string>& externalInputs
)
{
    constexpr auto propertyName = "VibeCADExternalInputs";
    auto* property = operation.getPropertyByName(propertyName);
    if (!property) {
        property = operation.addDynamicProperty(
            "App::PropertyStringList",
            propertyName,
            "Operation",
            "Saved external input identities; reopening never reads these paths",
            static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_NoRecompute),
            true,
            true
        );
    }
    auto* identities = dynamic_cast<App::PropertyStringList*>(property);
    if (!identities) {
        throw Base::TypeError("External-input metadata has an incompatible type");
    }
    identities->setValues(externalInputs);
}

void finalizeOutputTimelineBlock(
    App::Document& document,
    const std::vector<App::DocumentObject*>& outputs,
    App::DocumentObject* group
)
{
    auto* timeline = App::DocumentTimeline::get(document);
    const bool ownsCompleteProvisionalBlock =
        timeline
        && std::ranges::all_of(
            outputs,
            [timeline](const App::DocumentObject* output) {
                return timeline->isProvisionallyEnrolledByCurrentTransaction(
                    output
                );
            }
        )
        && (!group
            || timeline->isProvisionallyEnrolledByCurrentTransaction(group));
    if (!ownsCompleteProvisionalBlock) {
        // These helpers predate the timeline and are exported for external
        // callers. Preserve their legacy ability to classify already-existing
        // outputs, including outside a transaction. Only a command which owns
        // every member as a same-transaction provisional object may finalize
        // and reorder a semantic block.
        return;
    }

    std::vector<App::DocumentObject*> orderedNewObjects = outputs;
    auto* operation = group;
    if (operation) {
        orderedNewObjects.push_back(operation);
    }
    else if (outputs.size() == 1) {
        operation = outputs.front();
    }
    else {
        throw Base::RuntimeError(
            "Multiple mesh outputs require one semantic operation controller"
        );
    }
    timeline->finalizeProvisionalOperationBlock(
        operation,
        orderedNewObjects
    );
}

}  // namespace

void MeshGui::markMeshTimelineResource(App::DocumentObject& resource, App::DocumentObject& owner)
{
    if (&resource == &owner || !resource.getDocument()
        || resource.getDocument() != owner.getDocument()) {
        throw Base::ValueError(
            "A mesh timeline resource requires a distinct owner in the same document"
        );
    }

    const auto ensureProperty = [](App::DocumentObject& object,
                                   const char* type,
                                   const char* name,
                                   const char* description) {
        auto* property = object.getPropertyByName(name);
        if (!property) {
            property = object.addDynamicProperty(
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
        "Mesh operation which owns this generated result"
    ));
    if (!role || !ownerProperty) {
        throw Base::TypeError("Mesh timeline metadata properties have incompatible types");
    }

    ownerProperty->setValue(&owner);
    role->setValue(App::DocumentTimeline::ResourceRole);
}

void MeshGui::markMeshTimelineReplacement(
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& replacedInputs
)
{
    auto* document = operation.getDocument();
    if (!document || !operation.getNameInDocument() || !document->containsObject(&operation)) {
        throw Base::ValueError("A mesh timeline operation must be live in its document");
    }

    std::vector<App::DocumentObject*> exactInputs;
    exactInputs.reserve(replacedInputs.size());
    for (auto* input : replacedInputs) {
        if (!input || input == &operation || input->getDocument() != document
            || !input->getNameInDocument() || !document->containsObject(input)) {
            throw Base::ValueError(
                "A replaced mesh input must be a distinct live object in the operation document"
            );
        }
        if (std::ranges::find(exactInputs, input) == exactInputs.end()) {
            exactInputs.push_back(input);
        }
    }

    const auto ensureProperty = [](App::DocumentObject& object,
                                   const char* type,
                                   const char* name,
                                   const char* description) {
        auto* property = object.getPropertyByName(name);
        if (!property) {
            property = object.addDynamicProperty(
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
        operation,
        "App::PropertyString",
        App::DocumentTimeline::RolePropertyName,
        "Document timeline classification"
    ));
    if (!role) {
        throw Base::TypeError("Mesh timeline role metadata has an incompatible type");
    }
    if (auto* ownerProperty = operation.getPropertyByName(App::DocumentTimeline::OwnerPropertyName)) {
        ownerProperty->setStatus(App::Property::Hidden, true);
        ownerProperty->setStatus(App::Property::LockDynamic, true);
        ownerProperty->setStatus(App::Property::NoRecompute, true);
        auto* owner = dynamic_cast<App::PropertyLinkHidden*>(ownerProperty);
        if (!owner || owner->getValue()) {
            throw Base::TypeError(
                "A root mesh timeline operation cannot retain resource-owner metadata"
            );
        }
    }

    if (!exactInputs.empty()
        || operation.getPropertyByName(App::DocumentTimeline::ReplacedInputsPropertyName)) {
        auto* inputs = dynamic_cast<App::PropertyLinkListHidden*>(ensureProperty(
            operation,
            "App::PropertyLinkListHidden",
            App::DocumentTimeline::ReplacedInputsPropertyName,
            "Visible input objects hidden by this mesh operation"
        ));
        if (!inputs) {
            throw Base::TypeError("Mesh replaced-input metadata has an incompatible type");
        }
        inputs->setValues(exactInputs);
    }
    role->setValue(App::DocumentTimeline::OperationRole);
}

std::vector<Mesh::Feature*> MeshGui::createParametricMeshFilters(
    App::Document& document,
    const std::vector<ParametricMeshFilterTarget>& targets,
    const ParametricMeshFilterSpec& spec
)
{
    if (targets.empty() || !spec.typeName || !spec.objectName || !spec.objectLabel
        || !spec.transactionName) {
        throw Base::RuntimeError("The parametric mesh filter request is incomplete");
    }
    for (const auto& target : targets) {
        if (!target.source || target.source->getDocument() != &document
            || !MeshGui::isNativeMeshInputActive(target.source)
            || target.source->Mesh.getValue().countFacets() == 0) {
            throw Base::RuntimeError("A parametric mesh filter source is unavailable");
        }
    }
    const auto visibleSources = visibleSourcesAtInvocation(targets);

    Gui::ExactTransaction transaction(document, spec.transactionName);
    std::vector<Mesh::Feature*> results;
    results.reserve(targets.size());
    for (const auto& target : targets) {
        const std::string uniqueName = document.getUniqueObjectName(spec.objectName);
        auto* resultObject = document.addObject(spec.typeName, uniqueName.c_str());
        auto* result = freecad_cast<Mesh::Feature*>(resultObject);
        auto* sourceProperty = resultObject
            ? freecad_cast<App::PropertyLink*>(resultObject->getPropertyByName("Source"))
            : nullptr;
        if (!result || !sourceProperty) {
            throw Base::RuntimeError("The native mesh filter has an invalid property contract");
        }

        result->Label.setValue(spec.objectLabel);
        sourceProperty->setValue(target.source);
        if (target.configure) {
            target.configure(*resultObject);
        }
        if (target.acceptedInitialResult) {
            result->Mesh.setValue(*target.acceptedInitialResult);
            result->purgeTouched();
        }
        else {
            if (!result->recomputeFeature() || result->isError()) {
                throw Base::RuntimeError(
                    result->getStatusString()[0] != '\0'
                        ? result->getStatusString()
                        : "The native mesh filter could not be recomputed"
                );
            }
        }
        if (spec.requireNonEmptyResult && result->Mesh.getValue().countFacets() == 0) {
            throw Base::RuntimeError("The native mesh filter produced an empty result");
        }
        if (spec.requireMeshChange
            && sameMeshState(target.source->Mesh.getValue(), result->Mesh.getValue())) {
            throw Base::RuntimeError("The native mesh filter did not change the source");
        }
        results.push_back(result);
    }
    Mesh::OutputGroup* group = nullptr;
    if (spec.groupOutputsUnderOperation && targets.size() > 1) {
        group = createOutputGroup(
            document,
            uniqueSources(targets),
            spec.groupObjectName,
            spec.groupLabel ? spec.groupLabel : spec.objectLabel,
            spec.operationKind ? spec.operationKind : spec.transactionName
        );
        ownOutputResources(*group, asDocumentObjects(results));
    }
    if (group) {
        markMeshTimelineReplacement(*group, visibleSources);
    }
    else {
        for (std::size_t index = 0; index < results.size(); ++index) {
            std::vector<App::DocumentObject*> replacedInput;
            if (std::ranges::find(visibleSources, targets[index].source) != visibleSources.end()) {
                replacedInput.push_back(targets[index].source);
            }
            markMeshTimelineReplacement(*results[index], replacedInput);
        }
    }
    if (group) {
        finalizeOutputTimelineBlock(
            document,
            asDocumentObjects(results),
            group
        );
    }
    else {
        for (auto* result : results) {
            finalizeOutputTimelineBlock(document, {result}, nullptr);
        }
    }

    for (const auto& target : targets) {
        if (auto* sourceView = Gui::Application::Instance->getViewProvider(target.source)) {
            sourceView->setVisible(false);
        }
    }
    document.recompute();
    if (!transaction.commit()) {
        throw Base::RuntimeError("The parametric mesh operation could not be committed");
    }
    return results;
}

std::vector<Mesh::Feature*> MeshGui::createStoredMeshEdits(
    App::Document& document,
    const std::vector<StoredMeshEditTarget>& targets,
    const char* transactionName,
    bool groupOutputsUnderOperation,
    const char* groupObjectName,
    const char* groupLabel,
    const char* operationKind
)
{
    if (targets.empty() || !transactionName) {
        throw Base::RuntimeError("The stored mesh edit request is incomplete");
    }
    for (const auto& target : targets) {
        if (!target.source || target.source->getDocument() != &document
            || !MeshGui::isNativeMeshInputActive(target.source)
            || target.source->Mesh.getValue().countFacets() == 0 || !target.objectName
            || !target.objectLabel || !target.editKind
            || (!target.allowEmptyResult && target.acceptedResult.countFacets() == 0)
            || sameMeshState(target.source->Mesh.getValue(), target.acceptedResult)) {
            throw Base::RuntimeError("A stored mesh edit target is invalid");
        }
    }
    const auto visibleSources = visibleSourcesAtInvocation(targets);

    Gui::ExactTransaction transaction(document, transactionName);
    std::vector<Mesh::Feature*> results;
    results.reserve(targets.size());
    for (const auto& target : targets) {
        const std::string uniqueName = document.getUniqueObjectName(target.objectName);
        auto* result = document.addObject<Mesh::StoredEdit>(uniqueName.c_str());
        if (!result) {
            throw Base::RuntimeError("The stored mesh edit object could not be created");
        }
        result->Label.setValue(target.objectLabel);
        result->Source.setValue(target.source);
        result->AcceptedSource.setValue(target.source->Mesh.getValue());
        result->AcceptedResult.setValue(target.acceptedResult);
        result->EditKind.setValue(target.editKind);
        if (!result->recomputeFeature() || result->isError()) {
            throw Base::RuntimeError(
                result->getStatusString()[0] != '\0'
                    ? result->getStatusString()
                    : "The stored mesh edit could not be recomputed"
            );
        }
        if (!target.allowEmptyResult && result->Mesh.getValue().countFacets() == 0) {
            throw Base::RuntimeError("The stored mesh edit produced an empty result");
        }
        results.push_back(result);
    }
    Mesh::OutputGroup* group = nullptr;
    if (groupOutputsUnderOperation && targets.size() > 1) {
        group = createOutputGroup(
            document,
            uniqueSources(targets),
            groupObjectName,
            groupLabel ? groupLabel : targets.front().objectLabel,
            operationKind ? operationKind : transactionName
        );
        ownOutputResources(*group, asDocumentObjects(results));
    }
    if (group) {
        markMeshTimelineReplacement(*group, visibleSources);
    }
    else {
        for (std::size_t index = 0; index < results.size(); ++index) {
            std::vector<App::DocumentObject*> replacedInput;
            if (std::ranges::find(visibleSources, targets[index].source) != visibleSources.end()) {
                replacedInput.push_back(targets[index].source);
            }
            markMeshTimelineReplacement(*results[index], replacedInput);
        }
    }
    if (group) {
        finalizeOutputTimelineBlock(
            document,
            asDocumentObjects(results),
            group
        );
    }
    else {
        for (auto* result : results) {
            finalizeOutputTimelineBlock(document, {result}, nullptr);
        }
    }

    for (const auto& target : targets) {
        if (auto* sourceView = Gui::Application::Instance->getViewProvider(target.source)) {
            sourceView->setVisible(false);
        }
    }
    document.recompute();
    if (!transaction.commit()) {
        throw Base::RuntimeError("The stored mesh edit could not be committed");
    }
    return results;
}

Mesh::OutputGroup* MeshGui::createSourcePreservingOutputGroup(
    App::Document& document,
    const std::vector<App::DocumentObject*>& sources,
    const std::vector<App::DocumentObject*>& outputs,
    const char* objectName,
    const char* label,
    const char* operationKind
)
{
    if (outputs.empty()) {
        throw Base::RuntimeError("A source-preserving mesh operation requires at least one output");
    }
    std::vector<App::DocumentObject*> uniqueSourceObjects;
    std::unordered_set<App::DocumentObject*> seenSources;
    uniqueSourceObjects.reserve(sources.size());
    for (auto* source : sources) {
        if (!source || source->getDocument() != &document || !source->getNameInDocument()
            || !document.containsObject(source)
            || !MeshGui::isNativeMeshInputActive(source)) {
            throw Base::ValueError(
                "A source-preserving mesh operation source must be live in its document"
            );
        }
        if (seenSources.insert(source).second) {
            uniqueSourceObjects.push_back(source);
        }
    }
    if (uniqueSourceObjects.empty()) {
        throw Base::ValueError("A source-preserving mesh operation requires a live source");
    }
    std::unordered_set<App::DocumentObject*> seenOutputs;
    std::unordered_set<App::DocumentObject*> persistedDependencies;
    for (auto* output : outputs) {
        if (!output || output->getDocument() != &document || !output->getNameInDocument()
            || !document.containsObject(output)) {
            throw Base::ValueError(
                "A source-preserving mesh operation output must be live in its document"
            );
        }
        if (seenSources.contains(output) || !seenOutputs.insert(output).second) {
            throw Base::ValueError(
                "Source-preserving mesh operation outputs must be distinct from every source"
            );
        }
        for (auto* dependency : output->getOutListRecursive()) {
            persistedDependencies.insert(dependency);
        }
    }
    for (auto* source : uniqueSourceObjects) {
        if (!persistedDependencies.contains(source)) {
            throw Base::ValueError(
                "A source-preserving geometry output must persist a native "
                "dependency on every declared source"
            );
        }
    }

    if (outputs.size() == 1) {
        markMeshTimelineReplacement(*outputs.front(), {});
        finalizeOutputTimelineBlock(
            document,
            outputs,
            nullptr
        );
        return nullptr;
    }

    auto* group = createOutputGroup(
        document,
        uniqueSourceObjects,
        objectName,
        label,
        operationKind,
        OutputInputMode::SourcePreserving
    );
    ownOutputResources(*group, outputs);
    markMeshTimelineReplacement(*group, {});
    finalizeOutputTimelineBlock(document, outputs, group);
    if (group->Mesh.getValue().countFacets() != 0) {
        throw Base::RuntimeError("A source-preserving output group must not publish bypass geometry");
    }
    return group;
}

Mesh::OutputGroup* MeshGui::createStandaloneOutputGroup(
    App::Document& document,
    const std::vector<App::DocumentObject*>& outputs,
    const std::vector<std::string>& externalInputs,
    const char* objectName,
    const char* label,
    const char* operationKind
)
{
    if (outputs.empty()) {
        throw Base::RuntimeError("A standalone mesh operation requires at least one output");
    }
    std::unordered_set<App::DocumentObject*> seenOutputs;
    for (auto* output : outputs) {
        if (!output || output->getDocument() != &document || !output->getNameInDocument()
            || !document.containsObject(output) || !seenOutputs.insert(output).second) {
            throw Base::ValueError(
                "Standalone mesh operation outputs must be distinct live document objects"
            );
        }
    }

    if (outputs.size() == 1) {
        storeExternalInputIdentities(*outputs.front(), externalInputs);
        markMeshTimelineReplacement(*outputs.front(), {});
        finalizeOutputTimelineBlock(
            document,
            outputs,
            nullptr
        );
        return nullptr;
    }

    auto* group
        = createOutputGroup(document, {}, objectName, label, operationKind, OutputInputMode::Standalone);
    group->ExternalInputs.setValues(externalInputs);
    ownOutputResources(*group, outputs);
    markMeshTimelineReplacement(*group, {});
    finalizeOutputTimelineBlock(document, outputs, group);
    if (group->Mesh.getValue().countFacets() != 0) {
        throw Base::RuntimeError("A standalone output group must not publish bypass geometry");
    }
    return group;
}
