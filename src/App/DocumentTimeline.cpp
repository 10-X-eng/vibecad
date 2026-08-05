// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public License   *
 *   as published by the Free Software Foundation; either version 2 of     *
 *   the License, or (at your option) any later version.                    *
 *                                                                         *
 ***************************************************************************/

#include "DocumentTimeline.h"

#include <algorithm>
#include <cstring>
#include <exception>
#include <iterator>
#include <limits>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Type.h>
#include <Base/Uuid.h>

#include "Application.h"
#include "Datums.h"
#include "Document.h"
#include "DocumentObjectGroup.h"
#include "GeoFeatureGroupExtension.h"
#include "Origin.h"
#include "SuppressibleExtension.h"

using namespace App;

PROPERTY_SOURCE(App::DocumentTimeline, App::DocumentObject)

namespace
{

Property* localTimelineMetadataProperty(DocumentObject* object, const char* name) noexcept
{
    if (!object || !name) {
        return nullptr;
    }

    // Timeline metadata belongs to this exact document-object occurrence.
    // In particular, App::Link may forward an ordinary getPropertyByName()
    // lookup to its linked source through LinkBaseExtension. Calling the
    // PropertyContainer implementation explicitly bypasses extension
    // forwarding while still recognizing both native and dynamic properties
    // declared on the occurrence itself.
    return object->PropertyContainer::getPropertyByName(name);
}

const Property* localTimelineMetadataProperty(const DocumentObject* object, const char* name) noexcept
{
    if (!object || !name) {
        return nullptr;
    }
    return object->PropertyContainer::getPropertyByName(name);
}

PropertyUUID& ensureDesignDefinitionUuid(
    DocumentObject& definition,
    const char* name,
    const char* description
)
{
    auto* property =
        definition.PropertyContainer::getPropertyByName(name);
    if (!property) {
        property = definition.addDynamicProperty(
            "App::PropertyUUID",
            name,
            "VibeCAD Design",
            description,
            Prop_NoRecompute,
            true,
            true
        );
    }
    auto* uuid = dynamic_cast<PropertyUUID*>(property);
    if (!uuid) {
        throw Base::TypeError(
            std::string("Design definition property '") + name
            + "' has an incompatible type"
        );
    }
    uuid->setStatus(Property::ReadOnly, true);
    uuid->setStatus(Property::Hidden, true);
    uuid->setStatus(Property::LockDynamic, true);
    uuid->setStatus(Property::NoRecompute, true);
    return *uuid;
}

class ApplyingScope
{
public:
    explicit ApplyingScope(DocumentTimeline& timeline)
        : _timeline(timeline)
    {
        _timeline.beginApplying();
    }

    ~ApplyingScope()
    {
        _timeline.endApplying();
    }

    ApplyingScope(const ApplyingScope&) = delete;
    ApplyingScope& operator=(const ApplyingScope&) = delete;

private:
    DocumentTimeline& _timeline;
};

boost::dynamic_bitset<> makeVisibilityBits(const std::vector<DocumentObject*>& operations)
{
    boost::dynamic_bitset<> result(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        const auto* operation = operations[index];
        result.set(index, operation && operation->Visibility.getValue());
    }
    return result;
}

bool operationSuppressed(const DocumentObject* operation)
{
    if (!operation) {
        return false;
    }
    const auto* suppressible = operation->getExtensionByType<SuppressibleExtension>(true);
    return suppressible && suppressible->Suppressed.getValue();
}

bool hasValidTimelineOwnerChain(const DocumentObject* object) noexcept
{
    if (!DocumentTimeline::hasTimelineResourceRole(object)) {
        return true;
    }

    const DocumentObject* slow = object;
    const DocumentObject* fast = object;
    while (true) {
        slow = DocumentTimeline::timelineOwner(slow);
        fast = DocumentTimeline::timelineOwner(fast);
        fast = DocumentTimeline::timelineOwner(fast);
        if (!slow || !fast) {
            break;
        }
        if (slow == fast) {
            return false;
        }
    }

    for (auto* current = object; DocumentTimeline::hasTimelineResourceRole(current);) {
        current = DocumentTimeline::timelineOwner(current);
        if (!current) {
            return false;
        }
    }
    return true;
}

const DocumentObject* semanticOperationRoot(const DocumentObject* object, const Document* document) noexcept
{
    if (!object || !document || !document->containsObject(object)
        || object->getDocument() != document) {
        return nullptr;
    }

    std::unordered_set<const DocumentObject*> visited;
    auto* current = object;
    while (DocumentTimeline::hasTimelineResourceRole(current)) {
        if (!visited.insert(current).second) {
            return nullptr;
        }
        current = DocumentTimeline::timelineOwner(current);
        if (!current || !document->containsObject(current) || current->getDocument() != document) {
            return nullptr;
        }
    }
    return current;
}

bool isStructuralTimelineLink(const DocumentObject* object, const DocumentObject* dependency) noexcept
{
    return object && object->isTimelineStructuralChild(dependency);
}

bool stableTopologicallyOrderSemanticBlocks(
    const Document* document,
    std::vector<DocumentObject*>& operations,
    std::vector<bool>& visibility,
    std::vector<bool>& suppression,
    const long position
)
{
    if (!document || operations.size() != visibility.size()
        || operations.size() != suppression.size()) {
        throw Base::RuntimeError(
            "Semantic History dependency ordering received mismatched state"
        );
    }

    std::vector<const DocumentObject*> roots;
    std::vector<std::vector<std::size_t>> blocks;
    std::unordered_map<const DocumentObject*, std::size_t> rootIndices;
    roots.reserve(operations.size());
    blocks.reserve(operations.size());
    rootIndices.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        const auto* root = semanticOperationRoot(operations[index], document);
        if (!root) {
            throw Base::RuntimeError(
                "Semantic History dependency ordering found a malformed operation"
            );
        }
        const auto existing = rootIndices.find(root);
        if (existing == rootIndices.end()) {
            rootIndices.emplace(root, roots.size());
            roots.push_back(root);
            blocks.push_back({index});
            continue;
        }
        if (existing->second + 1 != roots.size()) {
            throw Base::RuntimeError(
                "Semantic History dependency ordering found a crossing block"
            );
        }
        blocks[existing->second].push_back(index);
    }

    std::vector<std::vector<std::size_t>> consumers(roots.size());
    std::vector<std::size_t> indegree(roots.size(), 0);
    std::unordered_set<std::string> edges;
    edges.reserve(operations.size());
    for (std::size_t consumerIndex = 0; consumerIndex < roots.size(); ++consumerIndex) {
        for (const auto operationIndex : blocks[consumerIndex]) {
            const auto* operation = operations[operationIndex];
            std::vector<const DocumentObject*> pending {operation};
            std::unordered_set<const DocumentObject*> visited {operation};
            while (!pending.empty()) {
                const auto* current = pending.back();
                pending.pop_back();
                for (const auto* dependency : current->getOutList()) {
                    if (!dependency || !document->containsObject(dependency)
                        || dependency->getDocument() != document
                        || isStructuralTimelineLink(current, dependency)
                        || !visited.insert(dependency).second) {
                        continue;
                    }
                    const auto* dependencyRoot = semanticOperationRoot(dependency, document);
                    if (!dependencyRoot) {
                        throw Base::RuntimeError(
                            "Semantic History dependency ordering found a malformed dependency"
                        );
                    }
                    const auto dependencyPosition = rootIndices.find(dependencyRoot);
                    if (dependencyRoot != roots[consumerIndex]
                        && dependencyPosition != rootIndices.end()) {
                        const std::string edge = std::to_string(dependencyPosition->second) + ":"
                            + std::to_string(consumerIndex);
                        if (edges.insert(edge).second) {
                            consumers[dependencyPosition->second].push_back(consumerIndex);
                            ++indegree[consumerIndex];
                        }
                    }
                    pending.push_back(dependency);
                }
            }
        }
    }

    std::vector<std::size_t> orderedRoots;
    std::vector<bool> emitted(roots.size(), false);
    orderedRoots.reserve(roots.size());
    while (orderedRoots.size() != roots.size()) {
        std::size_t next = roots.size();
        for (std::size_t index = 0; index < roots.size(); ++index) {
            if (!emitted[index] && indegree[index] == 0) {
                next = index;
                break;
            }
        }
        if (next == roots.size()) {
            throw Base::RuntimeError(
                "Semantic History dependency ordering detected a cycle"
            );
        }
        emitted[next] = true;
        orderedRoots.push_back(next);
        for (const auto consumer : consumers[next]) {
            if (indegree[consumer] == 0) {
                throw Base::RuntimeError(
                    "Semantic History dependency ordering has inconsistent edges"
                );
            }
            --indegree[consumer];
        }
    }

    bool changed = false;
    for (std::size_t index = 0; index < orderedRoots.size(); ++index) {
        changed = changed || orderedRoots[index] != index;
    }
    if (!changed) {
        return false;
    }
    if (position != static_cast<long>(operations.size())) {
        throw Base::RuntimeError(
            "Semantic History dependencies cannot be rebased across the active marker"
        );
    }

    std::vector<DocumentObject*> reorderedOperations;
    std::vector<bool> reorderedVisibility;
    std::vector<bool> reorderedSuppression;
    reorderedOperations.reserve(operations.size());
    reorderedVisibility.reserve(visibility.size());
    reorderedSuppression.reserve(suppression.size());
    for (const auto rootIndex : orderedRoots) {
        for (const auto operationIndex : blocks[rootIndex]) {
            reorderedOperations.push_back(operations[operationIndex]);
            reorderedVisibility.push_back(visibility[operationIndex]);
            reorderedSuppression.push_back(suppression[operationIndex]);
        }
    }
    operations = std::move(reorderedOperations);
    visibility = std::move(reorderedVisibility);
    suppression = std::move(reorderedSuppression);
    return true;
}

bool ownerChainContains(const DocumentObject* object, const DocumentObject* candidate) noexcept
{
    std::unordered_set<const DocumentObject*> visited;
    for (auto* owner = DocumentTimeline::timelineOwner(object); owner;
         owner = DocumentTimeline::timelineOwner(owner)) {
        if (!visited.insert(owner).second) {
            return false;
        }
        if (owner == candidate) {
            return true;
        }
    }
    return false;
}

bool validateReplacementInputGraph(
    const DocumentObject* object,
    const Document* document,
    std::unordered_set<const DocumentObject*>& visiting,
    std::unordered_set<const DocumentObject*>& validated
)
{
    if (!object || !document->containsObject(object) || object->getDocument() != document
        || !hasValidTimelineOwnerChain(object)) {
        return false;
    }

    const auto* property
        = localTimelineMetadataProperty(object, DocumentTimeline::ReplacedInputsPropertyName);
    if (!property) {
        return true;
    }
    const auto* links = dynamic_cast<const PropertyLinkListHidden*>(property);
    if (!links || !DocumentTimeline::hasTimelineOperationRole(object)) {
        return false;
    }
    if (validated.contains(object)) {
        return true;
    }
    if (!visiting.insert(object).second) {
        return false;
    }

    std::unordered_set<const DocumentObject*> directInputs;
    for (const auto* input : links->getValues()) {
        if (!input || input == object || !document->containsObject(input)
            || input->getDocument() != document || !directInputs.insert(input).second
            || ownerChainContains(input, object)
            || !validateReplacementInputGraph(input, document, visiting, validated)) {
            visiting.erase(object);
            return false;
        }
    }
    visiting.erase(object);
    validated.insert(object);
    return true;
}

std::size_t resourceOwnershipDepth(const DocumentObject* resource, const DocumentObject* operation) noexcept
{
    if (!DocumentTimeline::hasTimelineResourceRole(resource)) {
        return 0;
    }

    std::unordered_set<const DocumentObject*> visited;
    std::size_t depth = 0;
    for (auto* current = resource; DocumentTimeline::hasTimelineResourceRole(current);) {
        if (!visited.insert(current).second) {
            return 0;
        }
        current = DocumentTimeline::timelineOwner(current);
        if (!current) {
            return 0;
        }
        ++depth;
        if (current == operation) {
            return depth;
        }
    }
    return 0;
}

boost::dynamic_bitset<> makeSuppressionBits(const std::vector<DocumentObject*>& operations)
{
    boost::dynamic_bitset<> result(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        result.set(index, operationSuppressed(operations[index]));
    }
    return result;
}

bool bitAt(const boost::dynamic_bitset<>& values, std::size_t index, bool fallback)
{
    return index < values.size() ? values.test(index) : fallback;
}

bool ownersPresentedAtEnd(
    const DocumentObject* object,
    const std::vector<DocumentObject*>& operations,
    const boost::dynamic_bitset<>& visibility,
    const boost::dynamic_bitset<>& suppression
)
{
    if (!hasValidTimelineOwnerChain(object)) {
        return false;
    }

    for (const auto* owner = DocumentTimeline::timelineOwner(object); owner;
         owner = DocumentTimeline::timelineOwner(owner)) {
        const auto found = std::find(operations.begin(), operations.end(), owner);
        if (found == operations.end()) {
            if (!owner->Visibility.getValue() || operationSuppressed(owner)) {
                return false;
            }
            continue;
        }
        const auto index = static_cast<std::size_t>(std::distance(operations.begin(), found));
        if (!bitAt(visibility, index, owner->Visibility.getValue())
            || bitAt(suppression, index, operationSuppressed(owner))) {
            return false;
        }
    }
    return true;
}

void appendStableMerge(
    std::vector<std::vector<DocumentObject*>>& sequences,
    std::vector<DocumentObject*>& result
)
{
    std::vector<std::size_t> positions(sequences.size(), 0);
    while (true) {
        std::size_t selected = sequences.size();
        DocumentObject* selectedObject = nullptr;
        for (std::size_t sequenceIndex = 0; sequenceIndex < sequences.size(); ++sequenceIndex) {
            const auto position = positions[sequenceIndex];
            if (position >= sequences[sequenceIndex].size()) {
                continue;
            }

            auto* candidate = sequences[sequenceIndex][position];
            if (!selectedObject || candidate->getID() < selectedObject->getID()
                || (candidate->getID() == selectedObject->getID()
                    && std::strcmp(candidate->getNameInDocument(), selectedObject->getNameInDocument())
                        < 0)) {
                selected = sequenceIndex;
                selectedObject = candidate;
            }
        }

        if (!selectedObject) {
            return;
        }

        result.push_back(selectedObject);
        ++positions[selected];
    }
}

DocumentObject* resolveExactTimelineIdentity(
    Document* document,
    const long objectId,
    const std::string& objectName
) noexcept
{
    if (!document || objectId <= 0 || objectName.empty()) {
        return nullptr;
    }
    auto* object = document->getObjectByID(objectId);
    if (!object || !document->containsObject(object)) {
        return nullptr;
    }
    const char* currentName = object->getNameInDocument();
    return currentName && objectName == currentName && object->getDocument() == document ? object
                                                                                         : nullptr;
}

template<typename IsDescendant>
void validateCanonicalNestedResourceOrder(
    const std::vector<DocumentObject*>& orderedResources,
    IsDescendant&& isDescendant,
    const char* error
)
{
    std::unordered_map<DocumentObject*, std::size_t> indices;
    indices.reserve(orderedResources.size());
    for (std::size_t index = 0; index < orderedResources.size(); ++index) {
        if (!orderedResources[index] || !indices.emplace(orderedResources[index], index).second) {
            throw Base::RuntimeError(error);
        }
    }

    for (auto* resource : orderedResources) {
        std::size_t subtreeBegin = std::numeric_limits<std::size_t>::max();
        std::size_t subtreeEnd = 0;
        std::size_t subtreeCount = 0;
        for (auto* candidate : orderedResources) {
            if (candidate != resource && !isDescendant(candidate, resource)) {
                continue;
            }
            const auto index = indices.at(candidate);
            subtreeBegin = std::min(subtreeBegin, index);
            subtreeEnd = std::max(subtreeEnd, index + 1);
            ++subtreeCount;
        }
        if (subtreeBegin == std::numeric_limits<std::size_t>::max()
            || subtreeEnd - subtreeBegin != subtreeCount
            || orderedResources[subtreeEnd - 1] != resource) {
            throw Base::RuntimeError(error);
        }
    }
}

void validateCanonicalSemanticBlockOrder(
    const std::vector<DocumentObject*>& block,
    const DocumentObject* root,
    const char* error
)
{
    if (!root || block.empty() || block.back() != root) {
        throw Base::RuntimeError(error);
    }
    std::vector<DocumentObject*> resources(block.begin(), block.end() - 1);
    validateCanonicalNestedResourceOrder(
        resources,
        [](const DocumentObject* candidate, const DocumentObject* resource) {
            return ownerChainContains(candidate, resource);
        },
        error
    );
}

void validateCanonicalTimelineMetadataStatus(const Property* property, const char* error)
{
    if (!property || !property->testStatus(Property::Hidden)
        || !property->testStatus(Property::LockDynamic)
        || !property->testStatus(Property::NoRecompute)) {
        throw Base::RuntimeError(error);
    }
}

}  // namespace

DocumentTimeline::DocumentTimeline()
{
    constexpr auto flags = static_cast<PropertyType>(Prop_Hidden | Prop_NoRecompute);
    Base::Uuid designId;
    ADD_PROPERTY_TYPE(
        DesignId,
        (designId),
        "Design",
        static_cast<PropertyType>(flags | Prop_ReadOnly),
        "Persistent identity of this saved Design"
    );
    ADD_PROPERTY_TYPE(
        DesignSchemaVersion,
        (CurrentDesignSchemaVersion),
        "Design",
        static_cast<PropertyType>(flags | Prop_ReadOnly),
        "Persistent Design model schema version"
    );
    ADD_PROPERTY_TYPE(
        Operations,
        (),
        "Timeline",
        flags,
        "Document-wide modeling operations in execution order"
    );
    ADD_PROPERTY_TYPE(
        Position,
        (0L),
        "Timeline",
        flags,
        "Active history boundary from zero through the operation count"
    );
    ADD_PROPERTY_TYPE(
        VisibilityAtEnd,
        (),
        "Timeline",
        flags,
        "Full-history visibility baseline parallel to Operations"
    );
    ADD_PROPERTY_TYPE(
        SuppressionAtEnd,
        (),
        "Timeline",
        flags,
        "Full-history suppression baseline parallel to Operations"
    );
    ADD_PROPERTY_TYPE(
        SchemaVersion,
        (CurrentSchemaVersion),
        "Timeline",
        flags,
        "Persistent document timeline schema version"
    );

    setStatus(ObjectStatus::NoTouch, true);
}

DocumentTimeline::~DocumentTimeline() = default;

DocumentTimeline* DocumentTimeline::get(Document* document) noexcept
{
    if (!document) {
        return nullptr;
    }

    if (auto* named = document->getObject(ObjectName);
        named && named->isDerivedFrom<DocumentTimeline>()) {
        return static_cast<DocumentTimeline*>(named);
    }

    DocumentTimeline* result = nullptr;
    for (auto* object : document->getObjects()) {
        if (!object || !object->isDerivedFrom<DocumentTimeline>()) {
            continue;
        }
        auto* timeline = static_cast<DocumentTimeline*>(object);
        if (!result || timeline->getID() < result->getID()) {
            result = timeline;
        }
    }
    return result;
}

const DocumentTimeline* DocumentTimeline::get(const Document* document) noexcept
{
    return get(const_cast<Document*>(document));
}

DocumentTimeline* DocumentTimeline::ensure(Document* document)
{
    if (!document) {
        throw Base::ValueError("Cannot create a document timeline without a document");
    }
    if (auto* timeline = get(document)) {
        return timeline;
    }

    // addObject() emits synchronous callbacks.  Preserve only the stable ID
    // across that boundary: an observer is allowed to remove the previously
    // active object, so retaining and then inspecting its pointer is unsafe.
    const auto* activeObject = document->getActiveObject();
    const long activeObjectId = activeObject ? activeObject->getID() : 0;
    document->addObject<DocumentTimeline>(ObjectName);

    document->setActiveObject(activeObjectId > 0 ? document->getObjectByID(activeObjectId) : nullptr);
    auto* timeline = get(document);
    if (!timeline) {
        throw Base::RuntimeError(
            "The internal document timeline was removed while it was being created"
        );
    }
    return timeline;
}

void DocumentTimeline::initializeDesignDefinition(
    DocumentObject& definition
)
{
    auto* document = definition.getDocument();
    if (!document || !document->containsObject(&definition)
        || definition.testStatus(ObjectStatus::Remove)
        || definition.testStatus(ObjectStatus::Destroy)
        || definition.isDerivedFrom<DocumentTimeline>()) {
        throw Base::ValueError(
            "A Design definition must be a live document object"
        );
    }
    if (GeoFeatureGroupExtension::getGroupOfObject(&definition)
        || GroupExtension::getGroupOfObject(&definition)) {
        throw Base::ValueError(
            "A reusable Design definition must remain at Design scope"
        );
    }

    if (const auto* role = dynamic_cast<const PropertyString*>(
            localTimelineMetadataProperty(
                &definition,
                RolePropertyName
            )
        );
        role && std::string_view(role->getValue()) != OperationRole
        && !std::string_view(role->getValue()).empty()) {
        throw Base::ValueError(
            "A reusable Design definition cannot be a History resource or "
            "internal object"
        );
    }
    if (const auto* ownerProperty =
            localTimelineMetadataProperty(
                &definition,
                OwnerPropertyName
            )) {
        const auto* owner =
            dynamic_cast<const PropertyLinkHidden*>(ownerProperty);
        if (!owner || owner->getValue()) {
            throw Base::ValueError(
                "A reusable Design definition cannot belong to another "
                "History operation"
            );
        }
    }

    auto& definitionId = ensureDesignDefinitionUuid(
        definition,
        DefinitionIdPropertyName,
        "Persistent identity of this reusable Design definition"
    );
    if (definitionId.getValueStr().empty()) {
        definitionId.setValue(Base::Uuid::createUuid());
    }

    auto* timeline = ensure(document);
    const bool hadDesignId =
        definition.PropertyContainer::getPropertyByName(
            DesignIdPropertyName
        );
    auto& designId = ensureDesignDefinitionUuid(
        definition,
        DesignIdPropertyName,
        "Persistent identity of the owning Design"
    );
    if (!hadDesignId || designId.getValueStr().empty()) {
        designId.setValue(timeline->DesignId.getValue());
    }
    else if (designId.getValueStr()
             != timeline->DesignId.getValueStr()) {
        throw Base::ValueError(
            "A reusable definition belongs to a different Design"
        );
    }
}

void DocumentTimeline::finalizeDesignDefinition(
    DocumentObject& definition
)
{
    initializeDesignDefinition(definition);
    auto* document = definition.getDocument();
    if (!definition.isValid()) {
        throw Base::RuntimeError(definition.getStatusString());
    }

    auto* roleProperty = localTimelineMetadataProperty(
        &definition,
        RolePropertyName
    );
    if (!roleProperty) {
        roleProperty = definition.addDynamicProperty(
            "App::PropertyString",
            RolePropertyName,
            "Timeline",
            "Document timeline classification",
            Prop_NoRecompute,
            true,
            true
        );
    }
    auto* role = dynamic_cast<PropertyString*>(roleProperty);
    if (!role) {
        throw Base::TypeError(
            "A Design definition has incompatible History metadata"
        );
    }
    role->setStatus(Property::Hidden, true);
    role->setStatus(Property::LockDynamic, true);
    role->setStatus(Property::NoRecompute, true);
    if (std::string_view(role->getValue()) != OperationRole
        && !std::string_view(role->getValue()).empty()) {
        throw Base::ValueError(
            "A reusable Design definition cannot change another History role"
        );
    }
    role->setValue(OperationRole);

    auto* timeline = ensure(document);
    const auto& history = timeline->Operations.getValues();
    const auto occurrences =
        std::ranges::count(history, &definition);
    if (occurrences > 1) {
        throw Base::RuntimeError(
            "A reusable Design definition occurs more than once in History"
        );
    }

    const auto definitionPosition =
        std::ranges::find(history, &definition);
    std::vector<Property*> properties;
    definition.getPropertyList(properties);
    for (auto* property : properties) {
        auto* links = freecad_cast<PropertyLinkBase*>(property);
        if (!links) {
            continue;
        }
        std::vector<DocumentObject*> targets;
        links->getLinks(targets, true);
        for (auto* target : targets) {
            if (!target) {
                continue;
            }
            if (target == &definition) {
                throw Base::ValueError(
                    "A reusable Design definition cannot reference itself"
                );
            }

            auto* root = target;
            std::unordered_set<DocumentObject*> owners;
            while (hasTimelineResourceRole(root)) {
                if (!owners.insert(root).second) {
                    throw Base::RuntimeError(
                        "A Design definition input has a cyclic History owner"
                    );
                }
                root = timelineOwner(root);
                if (!root) {
                    throw Base::RuntimeError(
                        "A Design definition input has no History root"
                    );
                }
            }
            if (root == &definition) {
                continue;
            }

            const bool requiresHistory =
                hasTimelineOperationRole(root)
                || target->PropertyContainer::getPropertyByName(
                    DefinitionIdPropertyName
                );
            if (!requiresHistory) {
                continue;
            }
            const auto targetPosition =
                std::ranges::find(history, root);
            if (targetPosition == history.end()
                || (definitionPosition != history.end()
                    && targetPosition >= definitionPosition)) {
                throw Base::ValueError(
                    "A reusable Design definition can reference only an "
                    "earlier History state"
                );
            }
        }
    }

    if (occurrences == 1) {
        return;
    }

    if (document
            ->isProvisionallyEnrolledInTimelineByCurrentTransaction(
                &definition
            )) {
        timeline->finalizeProvisionalOperationBlock(
            &definition,
            {&definition}
        );
    }
    else {
        timeline->publishProvisionalOperationBlock(
            &definition,
            {}
        );
    }
}

bool DocumentTimeline::isOperationCandidate(const DocumentObject* operation) noexcept
{
    if (!operation || operation->isDerivedFrom<DocumentTimeline>()
        || operation->isDerivedFrom<Origin>() || operation->testStatus(ObjectStatus::Remove)
        || operation->testStatus(ObjectStatus::Destroy)) {
        return false;
    }

    // Every axis, plane, and point owned by a coordinate system is controlled
    // infrastructure.  This includes both the document Origin and a
    // user-created LocalCoordinateSystem; only standalone datum elements are
    // modeling operations in their own right.
    if (operation->isDerivedFrom<DatumElement>()
        && static_cast<const DatumElement*>(operation)->getLCS()) {
        return false;
    }

    // Objects without a ViewProvider are internal document state rather than
    // user-visible modeling operations.
    const char* viewProvider = operation->getViewProviderNameStored();
    if (!viewProvider || viewProvider[0] == '\0') {
        return false;
    }

    // A domain may deliberately use a native container as the one durable
    // operation for a multi-output command, or as a resource owned by that
    // operation. Its explicit persisted role is more precise than the generic
    // rule that ordinary groups are organizational rather than modeling
    // history. Resources remain in the raw sequence so their accepted display
    // state can follow the owner, but the GUI never presents them as steps.
    const auto* role = dynamic_cast<const PropertyString*>(
        localTimelineMetadataProperty(operation, RolePropertyName)
    );
    if (role && std::string_view(role->getValue()) == InternalRole) {
        return false;
    }
    if (role
        && (std::string_view(role->getValue()) == OperationRole
            || std::string_view(role->getValue()) == ResourceRole)) {
        return true;
    }
    return !operation->isDerivedFrom<DocumentObjectGroup>();
}

const DocumentObject* DocumentTimeline::timelineOwner(const DocumentObject* object) noexcept
{
    if (!hasTimelineResourceRole(object)) {
        return nullptr;
    }

    const auto* ownerProperty = dynamic_cast<const PropertyLinkHidden*>(
        localTimelineMetadataProperty(object, OwnerPropertyName)
    );
    const auto* owner = ownerProperty ? ownerProperty->getValue() : nullptr;
    const auto* document = object->getDocument();
    if (!owner || owner == object || !document || !document->containsObject(owner)
        || owner->getDocument() != document) {
        return nullptr;
    }
    return owner;
}

const DocumentObject* DocumentTimeline::timelineEditor(const DocumentObject* object) noexcept
{
    if (!object) {
        return nullptr;
    }
    const auto* editorProperty = dynamic_cast<const PropertyLinkHidden*>(
        localTimelineMetadataProperty(object, EditorPropertyName)
    );
    const auto* editor = editorProperty ? editorProperty->getValue() : nullptr;
    const auto* document = object->getDocument();
    if (!editor || editor == object || !document || !document->containsObject(editor)
        || editor->getDocument() != document || !hasTimelineResourceRole(editor)) {
        return nullptr;
    }

    std::unordered_set<const DocumentObject*> visited;
    for (const auto* owner = timelineOwner(editor); owner; owner = timelineOwner(owner)) {
        if (!visited.insert(owner).second) {
            return nullptr;
        }
        if (owner == object) {
            return editor;
        }
    }
    return nullptr;
}

bool DocumentTimeline::hasTimelineResourceRole(const DocumentObject* object) noexcept
{
    if (!object) {
        return false;
    }
    const auto* role = dynamic_cast<const PropertyString*>(
        localTimelineMetadataProperty(object, RolePropertyName)
    );
    return role && std::string_view(role->getValue()) == ResourceRole;
}

bool DocumentTimeline::hasTimelineOperationRole(const DocumentObject* object) noexcept
{
    if (!object) {
        return false;
    }
    const auto* role = dynamic_cast<const PropertyString*>(
        localTimelineMetadataProperty(object, RolePropertyName)
    );
    return role && std::string_view(role->getValue()) == OperationRole;
}

bool DocumentTimeline::hasTimelineInternalRole(const DocumentObject* object) noexcept
{
    if (!object) {
        return false;
    }
    const auto* role = dynamic_cast<const PropertyString*>(
        localTimelineMetadataProperty(object, RolePropertyName)
    );
    return role && std::string_view(role->getValue()) == InternalRole;
}

bool DocumentTimeline::isTimelineResourceOwnedBy(
    const DocumentObject* resource,
    const DocumentObject* operation
) noexcept
{
    if (!resource || !operation || resource == operation) {
        return false;
    }

    const auto* document = resource->getDocument();
    if (!document || operation->getDocument() != document || !document->containsObject(resource)
        || !document->containsObject(operation)) {
        return false;
    }

    return semanticOperationRoot(resource, document) == operation;
}

DocumentTimeline::ReplacementInputContract DocumentTimeline::replacementInputContract(
    DocumentObject* operation
)
{
    ReplacementInputContract result;
    if (!operation) {
        return result;
    }

    const auto* property = localTimelineMetadataProperty(operation, ReplacedInputsPropertyName);
    if (!property) {
        return result;
    }
    result.declared = true;

    auto* document = operation->getDocument();
    const auto* links = dynamic_cast<const PropertyLinkListHidden*>(property);
    std::unordered_set<const DocumentObject*> visiting;
    std::unordered_set<const DocumentObject*> validated;
    if (!document || !document->containsObject(operation) || !links
        || !validateReplacementInputGraph(operation, document, visiting, validated)) {
        result.valid = false;
        return result;
    }

    result.inputs = links->getValues();
    return result;
}

DocumentTimeline::TimelineDeletionPlan DocumentTimeline::timelineDeletionPlan(DocumentObject* operation)
{
    TimelineDeletionPlan result;
    if (!operation || hasTimelineResourceRole(operation)) {
        return result;
    }

    auto* document = operation->getDocument();
    if (!document || !document->containsObject(operation)) {
        return result;
    }

    const auto replacement = replacementInputContract(operation);
    result.applicable = hasTimelineOperationRole(operation) || replacement.declared;
    if (!replacement.valid) {
        result.valid = false;
        return result;
    }
    result.replacedInputs = replacement.inputs;

    std::unordered_set<DocumentObject*> revealSet;
    for (auto* input : replacement.inputs) {
        if (revealSet.insert(input).second) {
            result.objectsToReveal.push_back(input);
        }
        for (auto* owner = timelineOwner(input); owner; owner = timelineOwner(owner)) {
            if (revealSet.insert(owner).second) {
                result.objectsToReveal.push_back(owner);
            }
        }
    }

    std::vector<std::pair<std::size_t, DocumentObject*>> resources;
    for (auto* object : document->getObjects()) {
        if (!object || object == operation || !hasTimelineResourceRole(object)) {
            continue;
        }
        const auto depth = resourceOwnershipDepth(object, operation);
        if (depth != 0) {
            resources.emplace_back(depth, object);
        }
    }
    std::sort(resources.begin(), resources.end(), [](const auto& left, const auto& right) {
        if (left.first != right.first) {
            return left.first > right.first;
        }
        if (left.second->getID() != right.second->getID()) {
            return left.second->getID() > right.second->getID();
        }
        return std::strcmp(left.second->getNameInDocument(), right.second->getNameInDocument()) > 0;
    });
    result.ownedResources.reserve(resources.size());
    for (const auto& [depth, resource] : resources) {
        (void)depth;
        result.ownedResources.push_back(resource);
    }
    result.applicable = result.applicable || !result.ownedResources.empty();
    return result;
}

bool DocumentTimeline::isOperationActive(const DocumentObject* operation) const noexcept
{
    if (!operation) {
        return false;
    }

    auto* document = getDocument();
    if (!document || !document->containsObject(operation) || operation->getDocument() != document) {
        return false;
    }

    // Owned setup/cache/representation objects share their durable operation's
    // history state. An explicitly internal object with no valid acyclic
    // owner remains inactive instead of becoming an independent operation.
    if (!hasValidTimelineOwnerChain(operation)) {
        return false;
    }

    const DocumentObject* effectiveOperation = operation;
    while (const auto* owner = timelineOwner(effectiveOperation)) {
        effectiveOperation = owner;
    }

    const auto& operations = Operations.getValues();
    const auto found = std::find(operations.begin(), operations.end(), effectiveOperation);
    if (found != operations.end()) {
        const auto index = static_cast<long>(std::distance(operations.begin(), found));
        const auto boundary = std::clamp(Position.getValue(), 0L, static_cast<long>(operations.size()));
        if (index >= boundary) {
            return false;
        }
    }

    // A resource is never independently active: a user-selected suppression
    // of any owning operation also deactivates it. Consult the saved
    // full-history baseline rather than the live Suppressed property, because
    // the latter is temporarily true for every operation beyond the marker
    // while the marker is being advanced.
    const auto& suppression = SuppressionAtEnd.getValues();
    for (const auto* owner = timelineOwner(operation); owner; owner = timelineOwner(owner)) {
        const auto ownerPosition = std::find(operations.begin(), operations.end(), owner);
        if (ownerPosition == operations.end()) {
            if (operationSuppressed(owner)) {
                return false;
            }
            continue;
        }
        const auto ownerIndex = static_cast<std::size_t>(
            std::distance(operations.begin(), ownerPosition)
        );
        if (bitAt(suppression, ownerIndex, false)) {
            return false;
        }
    }
    return true;
}

bool DocumentTimeline::isObjectUsableAtCurrentPosition(const DocumentObject* object) noexcept
{
    try {
        const auto* document = object ? object->getDocument() : nullptr;
        if (!document || !document->containsObject(object) || hasTimelineInternalRole(object)
            || !hasValidTimelineOwnerChain(object)) {
            return false;
        }

        const auto* timeline = get(document);
        if (timeline && !timeline->isOperationActive(object)) {
            return false;
        }

        const auto* current = object;
        std::unordered_set<const DocumentObject*> visited;
        while (current) {
            if (!visited.insert(current).second || hasTimelineInternalRole(current)) {
                return false;
            }

            bool suppressed = operationSuppressed(current);
            if (timeline) {
                const auto& operations = timeline->Operations.getValues();
                const auto found = std::find(operations.begin(), operations.end(), current);
                if (found != operations.end()) {
                    const auto index = static_cast<std::size_t>(
                        std::distance(operations.begin(), found)
                    );
                    suppressed = bitAt(timeline->SuppressionAtEnd.getValues(), index, suppressed);
                }
            }
            if (suppressed) {
                return false;
            }

            if (!hasTimelineResourceRole(current)) {
                break;
            }
            current = timelineOwner(current);
            if (!current) {
                return false;
            }
        }
        return true;
    }
    catch (...) {
        return false;
    }
}

bool DocumentTimeline::isOperationVisibleAtEnd(const DocumentObject* operation) const noexcept
{
    if (!operation) {
        return false;
    }

    const auto* document = getDocument();
    if (!document || !document->containsObject(operation) || operation->getDocument() != document) {
        return false;
    }

    const auto& operations = Operations.getValues();
    const auto& visibility = VisibilityAtEnd.getValues();
    const auto& suppression = SuppressionAtEnd.getValues();
    if (!hasValidTimelineOwnerChain(operation)) {
        return false;
    }
    const auto found = std::find(operations.begin(), operations.end(), operation);
    const bool selfVisible = found == operations.end()
        ? operation->Visibility.getValue()
        : bitAt(
              visibility,
              static_cast<std::size_t>(std::distance(operations.begin(), found)),
              operation->Visibility.getValue()
          );
    return selfVisible && ownersPresentedAtEnd(operation, operations, visibility, suppression);
}

void DocumentTimeline::pruneProvisionalEnrollments()
{
    auto* document = getDocument();
    const int currentTransaction = document ? document->getBookedTransactionID()
                                            : App::NullTransaction;
    const bool transactionIsLive = currentTransaction != App::NullTransaction
        && App::GetApplication().transactionIsActive(currentTransaction);
    const auto& operations = Operations.getValues();

    std::erase_if(
        _provisionalEnrollments,
        [document, currentTransaction, transactionIsLive, &operations](
            const ProvisionalEnrollment& enrollment
        ) {
            if (!document || !transactionIsLive || enrollment.transactionId != currentTransaction) {
                return true;
            }
            const auto* operation = document->getObjectByID(enrollment.objectId);
            return !operation || enrollment.objectName != operation->getNameInDocument()
                || std::find(operations.begin(), operations.end(), operation) == operations.end();
        }
    );
}

void DocumentTimeline::pruneProvisionalTransactionCreations()
{
    auto* document = getDocument();
    const int currentTransaction = document ? document->getBookedTransactionID()
                                            : App::NullTransaction;
    const bool transactionIsLive = currentTransaction != App::NullTransaction
        && App::GetApplication().transactionIsActive(currentTransaction);

    std::erase_if(
        _provisionalTransactionCreations,
        [document,
         currentTransaction,
         transactionIsLive](const ProvisionalTransactionCreations& provenance) {
            return !document || !transactionIsLive || provenance.transactionId != currentTransaction
                || provenance.documentName != document->getName()
                || provenance.documentUid != document->Uid.getValueStr();
        }
    );
}

void DocumentTimeline::pruneProvisionalPublications()
{
    std::erase_if(_provisionalPublications, [this](const ProvisionalPublication& publication) {
        return !publicationMatchesLiveState(publication, nullptr);
    });
}

void DocumentTimeline::discardTransactionProvenance(const int transactionId) noexcept
{
    if (transactionId == App::NullTransaction) {
        return;
    }
    std::erase_if(_provisionalEnrollments, [transactionId](const ProvisionalEnrollment& enrollment) {
        return enrollment.transactionId == transactionId;
    });
    std::erase_if(
        _provisionalTransactionCreations,
        [transactionId](const ProvisionalTransactionCreations& provenance) {
            return provenance.transactionId == transactionId;
        }
    );
    std::erase_if(_provisionalPublications, [transactionId](const ProvisionalPublication& publication) {
        return publication.transactionId == transactionId;
    });
    std::erase_if(_stagedResourceAdoptions, [transactionId](const StagedResourceAdoption& adoption) {
        return adoption.transactionId == transactionId;
    });
    std::erase_if(
        _provisionalInternalObjects,
        [transactionId](const ProvisionalInternalObject& internal) {
            return internal.transactionId == transactionId;
        }
    );
    std::erase_if(
        _stagedSegmentReplacements,
        [transactionId](const StagedSegmentReplacement& replacement) {
            return replacement.transactionId == transactionId;
        }
    );
    std::erase_if(
        _stagedResourceReconciliations,
        [transactionId](const StagedResourceReconciliation& reconciliation) {
            return reconciliation.transactionId == transactionId;
        }
    );
}

void DocumentTimeline::pruneStagedResourceAdoptions()
{
    auto* document = getDocument();
    const int currentTransaction = document ? document->getBookedTransactionID()
                                            : App::NullTransaction;
    const bool transactionIsLive = currentTransaction != App::NullTransaction
        && App::GetApplication().transactionIsActive(currentTransaction);

    std::erase_if(
        _stagedResourceAdoptions,
        [document, currentTransaction, transactionIsLive](const StagedResourceAdoption& adoption) {
            if (!document || !transactionIsLive || adoption.transactionId != currentTransaction) {
                return true;
            }
            const auto* operation = document->getObjectByID(adoption.operationId);
            if (!operation || adoption.operationName != operation->getNameInDocument()) {
                return true;
            }
            return std::ranges::any_of(
                adoption.resources,
                [document](const StagedExistingResource& resource) {
                    const auto* object = document->getObjectByID(resource.objectId);
                    return !object || resource.objectName != object->getNameInDocument();
                }
            );
        }
    );
}

void DocumentTimeline::pruneProvisionalInternalObjects()
{
    auto* document = getDocument();
    const int currentTransaction = document ? document->getBookedTransactionID()
                                            : App::NullTransaction;
    const bool transactionIsLive = currentTransaction != App::NullTransaction
        && App::GetApplication().transactionIsActive(currentTransaction);

    std::erase_if(
        _provisionalInternalObjects,
        [document, currentTransaction, transactionIsLive](const ProvisionalInternalObject& internal) {
            if (!document || !transactionIsLive || internal.transactionId != currentTransaction) {
                return true;
            }
            const auto* object = document->getObjectByID(internal.object.objectId);
            return !object || internal.object.objectName != object->getNameInDocument();
        }
    );
}

void DocumentTimeline::pruneStagedSegmentReplacement()
{
    auto* document = getDocument();
    const int currentTransaction = document ? document->getBookedTransactionID()
                                            : App::NullTransaction;
    const bool transactionIsLive = currentTransaction != App::NullTransaction
        && App::GetApplication().transactionIsActive(currentTransaction);

    std::erase_if(
        _stagedSegmentReplacements,
        [document, currentTransaction, transactionIsLive](const StagedSegmentReplacement& replacement) {
            return !document || !transactionIsLive || replacement.transactionId != currentTransaction
                || replacement.documentName != document->getName()
                || replacement.documentUid != document->Uid.getValueStr();
        }
    );
}

void DocumentTimeline::pruneStagedResourceReconciliation()
{
    auto* document = getDocument();
    const int currentTransaction = document ? document->getBookedTransactionID()
                                            : App::NullTransaction;
    const bool transactionIsLive = currentTransaction != App::NullTransaction
        && App::GetApplication().transactionIsActive(currentTransaction);

    std::erase_if(
        _stagedResourceReconciliations,
        [document,
         currentTransaction,
         transactionIsLive](const StagedResourceReconciliation& reconciliation) {
            if (!document || !transactionIsLive || reconciliation.transactionId != currentTransaction
                || reconciliation.documentName != document->getName()
                || reconciliation.documentUid != document->Uid.getValueStr()) {
                return true;
            }
            const auto* owner = document->getObjectByID(reconciliation.owner.objectId);
            return !owner || reconciliation.owner.objectName != owner->getNameInDocument();
        }
    );
}

void DocumentTimeline::rememberProvisionalEnrollment(const DocumentObject* operation, long insertionMarker)
{
    auto* document = getDocument();
    if (!document || !operation || !document->containsObject(operation)
        || operation->getDocument() != document) {
        return;
    }

    const int transactionId = document->getBookedTransactionID();
    if (transactionId == App::NullTransaction
        || !App::GetApplication().transactionIsActive(transactionId)) {
        return;
    }
    pruneProvisionalEnrollments();
    const auto alreadyRemembered = std::find_if(
        _provisionalEnrollments.begin(),
        _provisionalEnrollments.end(),
        [transactionId, operation](const ProvisionalEnrollment& enrollment) {
            return enrollment.transactionId == transactionId
                && enrollment.objectId == operation->getID()
                && enrollment.objectName == operation->getNameInDocument();
        }
    );
    if (alreadyRemembered != _provisionalEnrollments.end()) {
        return;
    }
    _provisionalEnrollments.push_back(
        ProvisionalEnrollment {
            .transactionId = transactionId,
            .objectId = operation->getID(),
            .objectName = operation->getNameInDocument(),
            .insertionMarker = insertionMarker,
        }
    );
}

void DocumentTimeline::rememberProvisionalCreation(const DocumentObject* object)
{
    auto* document = getDocument();
    if (!document || !object || !document->containsObject(object)
        || object->getDocument() != document || !object->getNameInDocument()) {
        return;
    }
    const int transactionId = document->getBookedTransactionID();
    if (transactionId == App::NullTransaction
        || !App::GetApplication().transactionIsActive(transactionId)) {
        return;
    }

    pruneProvisionalTransactionCreations();
    auto provenance = std::find_if(
        _provisionalTransactionCreations.begin(),
        _provisionalTransactionCreations.end(),
        [transactionId](const ProvisionalTransactionCreations& candidate) {
            return candidate.transactionId == transactionId;
        }
    );
    const auto captureBaseline = [this, document](ProvisionalTransactionCreations& target) {
        target.documentName = document->getName();
        target.documentUid = document->Uid.getValueStr();
        target.position = Position.getValue();
        target.operations.clear();
        const auto operations = Operations.getValues();
        const auto visibility = VisibilityAtEnd.getValues();
        const auto suppression = SuppressionAtEnd.getValues();
        target.operations.reserve(operations.size());
        for (std::size_t index = 0; index < operations.size(); ++index) {
            auto* operation = operations[index];
            if (!operation || !document->containsObject(operation)
                || operation->getDocument() != document || !operation->getNameInDocument()) {
                continue;
            }
            target.operations.push_back(
                CreationSnapshotOperation {
                    .object = {
                        .objectId = operation->getID(),
                        .objectName =
                            operation->getNameInDocument(),
                    },
                    .visibility = bitAt(
                        visibility,
                        index,
                        operation->Visibility.getValue()
                    ),
                    .suppression = bitAt(
                        suppression,
                        index,
                        operationSuppressed(operation)
                    ),
                }
            );
        }
    };

    if (provenance == _provisionalTransactionCreations.end()) {
        ProvisionalTransactionCreations created {
            .transactionId = transactionId,
            .documentName = {},
            .documentUid = {},
            .position = 0,
            .operations = {},
            .objects = {},
        };
        captureBaseline(created);
        _provisionalTransactionCreations.push_back(std::move(created));
        provenance = std::prev(_provisionalTransactionCreations.end());
    }
    else if (provenance->objects.empty()) {
        // A transaction may publish or classify one complete creation
        // generation and then continue editing before it creates the next.
        // Once no unconsumed creation identities remain, the next creation
        // starts a new generation and must snapshot that new accepted baseline.
        captureBaseline(*provenance);
    }

    if (std::ranges::none_of(provenance->objects, [object](const TimelineObjectIdentity& identity) {
            return identity.objectId == object->getID()
                && identity.objectName == object->getNameInDocument();
        })) {
        provenance->objects.push_back(
            TimelineObjectIdentity {
                .objectId = object->getID(),
                .objectName = object->getNameInDocument(),
            }
        );
    }
}

bool DocumentTimeline::isCreatedByCurrentTransaction(const DocumentObject* object) const noexcept
{
    const auto* document = getDocument();
    if (!document || !object || !document->containsObject(object)
        || object->getDocument() != document) {
        return false;
    }
    const int transactionId = document->getBookedTransactionID();
    if (transactionId == App::NullTransaction
        || !App::GetApplication().transactionIsActive(transactionId)) {
        return false;
    }
    return std::ranges::any_of(
        _provisionalTransactionCreations,
        [document, transactionId, object](const ProvisionalTransactionCreations& provenance) {
            return provenance.transactionId == transactionId
                && provenance.documentName == document->getName()
                && provenance.documentUid == document->Uid.getValueStr()
                && std::ranges::any_of(
                       provenance.objects,
                       [object](const TimelineObjectIdentity& identity) {
                           return identity.objectId == object->getID()
                               && identity.objectName == object->getNameInDocument();
                       }
                );
        }
    );
}

bool DocumentTimeline::isProvisionallyEnrolledByCurrentTransaction(
    const DocumentObject* object
) const noexcept
{
    const auto* document = getDocument();
    if (!document || !object || !document->containsObject(object)
        || object->getDocument() != document) {
        return false;
    }
    const int transactionId = document->getBookedTransactionID();
    if (transactionId == App::NullTransaction
        || !App::GetApplication().transactionIsActive(transactionId)) {
        return false;
    }
    return std::ranges::any_of(
        _provisionalEnrollments,
        [transactionId, object](const ProvisionalEnrollment& enrollment) {
            return enrollment.transactionId == transactionId
                && enrollment.objectId == object->getID()
                && enrollment.objectName == object->getNameInDocument();
        }
    );
}

bool DocumentTimeline::publicationMatchesLiveState(
    const ProvisionalPublication& publication,
    const std::vector<DocumentObject*>* expectedBlock
) const noexcept
{
    try {
        const auto* document = getDocument();
        if (!document || publication.orderedMembers.empty()) {
            return false;
        }
        const int transactionId = document->getBookedTransactionID();
        if (transactionId == App::NullTransaction || publication.transactionId != transactionId
            || !App::GetApplication().transactionIsActive(transactionId)
            || publication.documentName != document->getName()
            || publication.documentUid != document->Uid.getValueStr()
            || (expectedBlock && expectedBlock->size() != publication.orderedMembers.size())) {
            return false;
        }

        const auto& rootMember = publication.orderedMembers.back();
        if (rootMember.object.objectId != publication.operation.objectId
            || rootMember.object.objectName != publication.operation.objectName
            || rootMember.owner.objectId != 0 || !rootMember.owner.objectName.empty()) {
            return false;
        }

        std::vector<DocumentObject*> block;
        block.reserve(publication.orderedMembers.size());
        std::unordered_map<DocumentObject*, std::size_t> indices;
        indices.reserve(publication.orderedMembers.size());
        for (std::size_t index = 0; index < publication.orderedMembers.size(); ++index) {
            const auto& member = publication.orderedMembers[index];
            auto* object = resolveExactTimelineIdentity(
                const_cast<Document*>(document),
                member.object.objectId,
                member.object.objectName
            );
            if (!object || (expectedBlock && (*expectedBlock)[index] != object)
                || !indices.emplace(object, index).second) {
                return false;
            }
            block.push_back(object);
        }

        auto* operation = block.back();
        if (!hasTimelineOperationRole(operation) || timelineOwner(operation)) {
            return false;
        }
        const auto* operationRole = localTimelineMetadataProperty(operation, RolePropertyName);
        if (!operationRole || !operationRole->testStatus(Property::Hidden)
            || !operationRole->testStatus(Property::LockDynamic)
            || !operationRole->testStatus(Property::NoRecompute)) {
            return false;
        }
        if (const auto* ownerProperty = localTimelineMetadataProperty(operation, OwnerPropertyName)) {
            const auto* owner = dynamic_cast<const PropertyLinkHidden*>(ownerProperty);
            if (!owner || owner->getValue() || !ownerProperty->testStatus(Property::Hidden)
                || !ownerProperty->testStatus(Property::LockDynamic)
                || !ownerProperty->testStatus(Property::NoRecompute)) {
                return false;
            }
        }

        for (std::size_t index = 0; index + 1 < block.size(); ++index) {
            auto* resource = block[index];
            const auto& ownerIdentity = publication.orderedMembers[index].owner;
            auto* owner = resolveExactTimelineIdentity(
                const_cast<Document*>(document),
                ownerIdentity.objectId,
                ownerIdentity.objectName
            );
            const auto ownerIndex = indices.find(owner);
            const auto* roleProperty = localTimelineMetadataProperty(resource, RolePropertyName);
            const auto* ownerProperty = localTimelineMetadataProperty(resource, OwnerPropertyName);
            const auto* ownerLink = dynamic_cast<const PropertyLinkHidden*>(ownerProperty);
            if (!owner || ownerIndex == indices.end() || ownerIndex->second <= index
                || !hasTimelineResourceRole(resource) || timelineOwner(resource) != owner
                || !roleProperty || !roleProperty->testStatus(Property::Hidden)
                || !roleProperty->testStatus(Property::LockDynamic)
                || !roleProperty->testStatus(Property::NoRecompute) || !ownerLink
                || ownerLink->getValue() != owner || !ownerProperty->testStatus(Property::Hidden)
                || !ownerProperty->testStatus(Property::LockDynamic)
                || !ownerProperty->testStatus(Property::NoRecompute)) {
                return false;
            }
        }

        validateCanonicalSemanticBlockOrder(
            block,
            operation,
            "A current-transaction publication lost canonical semantic order"
        );

        const auto& operations = Operations.getValues();
        if (VisibilityAtEnd.getValues().size() != operations.size()
            || SuppressionAtEnd.getValues().size() != operations.size() || Position.getValue() < 0
            || Position.getValue() > static_cast<long>(operations.size())) {
            return false;
        }
        const auto blockBegin = std::ranges::find(operations, block.front());
        if (blockBegin == operations.end()
            || static_cast<std::size_t>(std::distance(blockBegin, operations.end())) < block.size()
            || !std::equal(block.begin(), block.end(), blockBegin)) {
            return false;
        }

        std::size_t semanticMemberCount = 0;
        for (auto* candidate : operations) {
            if (semanticOperationRoot(candidate, document) != operation) {
                continue;
            }
            ++semanticMemberCount;
            if (!indices.contains(candidate)) {
                return false;
            }
        }
        return semanticMemberCount == block.size();
    }
    catch (...) {
        return false;
    }
}

bool DocumentTimeline::isSemanticallyPublishedByCurrentTransaction(const DocumentObject* object
) const noexcept
{
    const auto* document = getDocument();
    if (!document || !object || !document->containsObject(object)
        || object->getDocument() != document) {
        return false;
    }
    return std::ranges::any_of(
        _provisionalPublications,
        [this, object](const ProvisionalPublication& publication) {
            const bool containsObject = std::ranges::any_of(
                publication.orderedMembers,
                [object](const ProvisionalPublicationMember& member) {
                    return member.object.objectId == object->getID()
                        && member.object.objectName == object->getNameInDocument();
                }
            );
            return containsObject && publicationMatchesLiveState(publication, nullptr);
        }
    );
}

bool DocumentTimeline::isExactSemanticBlockPublishedByCurrentTransaction(
    const DocumentObject* operation,
    const std::vector<DocumentObject*>& orderedBlock
) const noexcept
{
    const auto* document = getDocument();
    if (!document || !operation || !document->containsObject(operation)
        || operation->getDocument() != document || orderedBlock.empty()
        || orderedBlock.back() != operation) {
        return false;
    }
    return std::ranges::any_of(
        _provisionalPublications,
        [this, operation, &orderedBlock](const ProvisionalPublication& publication) {
            return publication.operation.objectId == operation->getID()
                && publication.operation.objectName == operation->getNameInDocument()
                && publicationMatchesLiveState(publication, &orderedBlock);
        }
    );
}

void DocumentTimeline::recordCreatedObject(DocumentObject* object)
{
    rememberProvisionalCreation(object);
    recordOperation(object);
}

void DocumentTimeline::classifyProvisionalInternalObject(DocumentObject* object)
{
    classifyTimelineLeafInternalObject(object, true);
}

void DocumentTimeline::classifyExistingLeafInternalObject(DocumentObject* object)
{
    classifyTimelineLeafInternalObject(object, false);
}

void DocumentTimeline::classifyTimelineLeafInternalObject(
    DocumentObject* object,
    const bool requireProvisional
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "An internal timeline classification requires one normal "
            "document and one caller-owned transaction"
        );
    }
    if (!object || !document->containsObject(object) || object->getDocument() != document
        || !object->getNameInDocument()) {
        throw Base::ValueError("The internal timeline leaf must be live in this document");
    }

    pruneProvisionalEnrollments();
    pruneProvisionalInternalObjects();
    pruneStagedSegmentReplacement();
    pruneStagedResourceReconciliation();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The internal-object transaction is no longer active");
    }

    const auto operations = Operations.getValues();
    const auto visibility = VisibilityAtEnd.getValues();
    const auto suppression = SuppressionAtEnd.getValues();
    const long position = Position.getValue();
    if (visibility.size() != operations.size() || suppression.size() != operations.size()
        || position < 0 || position > static_cast<long>(operations.size())) {
        throw Base::RuntimeError("The timeline state is inconsistent");
    }
    std::unordered_set<DocumentObject*> seenOperations;
    seenOperations.reserve(operations.size());
    for (auto* candidate : operations) {
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !isOperationCandidate(candidate) || !seenOperations.insert(candidate).second
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The timeline contains a missing, duplicate, internal, or "
                "malformed operation"
            );
        }
    }

    const auto enrollment = std::find_if(
        _provisionalEnrollments.begin(),
        _provisionalEnrollments.end(),
        [transactionId, object](const ProvisionalEnrollment& candidate) {
            return candidate.transactionId == transactionId && candidate.objectId == object->getID()
                && candidate.objectName == object->getNameInDocument();
        }
    );
    const bool isProvisional = enrollment != _provisionalEnrollments.end();
    if (isProvisional != requireProvisional) {
        throw Base::RuntimeError(
            requireProvisional ? "The object was not provisionally enrolled by this exact "
                                 "transaction"
                               : "A current-transaction provisional object cannot use the "
                                 "existing-leaf migration path"
        );
    }
    std::size_t rawIndex = operations.size();
    if (requireProvisional) {
        if (enrollment->insertionMarker < 0
            || enrollment->insertionMarker >= static_cast<long>(operations.size())
            || operations[static_cast<std::size_t>(enrollment->insertionMarker)] != object) {
            throw Base::RuntimeError(
                "The provisional object's exact timeline identity or order "
                "changed before classification"
            );
        }
        rawIndex = static_cast<std::size_t>(enrollment->insertionMarker);
    }
    else {
        const auto found = std::find(operations.begin(), operations.end(), object);
        if (found == operations.end()) {
            throw Base::RuntimeError(
                "The existing internal migration target is not one tracked "
                "timeline operation"
            );
        }
        rawIndex = static_cast<std::size_t>(std::distance(operations.begin(), found));
    }
    if (std::ranges::any_of(
            _provisionalInternalObjects,
            [transactionId, object](const ProvisionalInternalObject& internal) {
                return internal.transactionId == transactionId
                    && internal.object.objectId == object->getID()
                    && internal.object.objectName == object->getNameInDocument();
            }
        )) {
        throw Base::RuntimeError("The object is already classified as provisional internal state");
    }

    const auto* roleProperty = localTimelineMetadataProperty(object, RolePropertyName);
    const auto* role = dynamic_cast<const PropertyString*>(roleProperty);
    if (roleProperty
        && (!role
            || (std::string_view(role->getValue()) != OperationRole
                && std::string_view(role->getValue()).empty() == false))) {
        throw Base::RuntimeError("The internal leaf has incompatible role metadata");
    }
    if (roleProperty) {
        validateCanonicalTimelineMetadataStatus(
            roleProperty,
            "Existing internal role metadata is not hidden, locked, and non-recomputing"
        );
    }
    const auto* ownerProperty = localTimelineMetadataProperty(object, OwnerPropertyName);
    const auto* owner = dynamic_cast<const PropertyLinkHidden*>(ownerProperty);
    if (ownerProperty && (!owner || owner->getValue())) {
        throw Base::RuntimeError("An internal object cannot retain timeline ownership metadata");
    }
    if (ownerProperty) {
        validateCanonicalTimelineMetadataStatus(
            ownerProperty,
            "Existing internal owner metadata is not hidden, locked, and non-recomputing"
        );
    }
    if (localTimelineMetadataProperty(object, ReplacedInputsPropertyName)
        || localTimelineMetadataProperty(object, EditorPropertyName)
        || localTimelineMetadataProperty(object, EditCommandPropertyName)
        || localTimelineMetadataProperty(object, DeleteCommandPropertyName)) {
        throw Base::RuntimeError("An internal leaf cannot retain replacement or editor contracts");
    }
    for (auto* candidate : operations) {
        if (candidate != object && semanticOperationRoot(candidate, document) == object) {
            throw Base::RuntimeError(
                "Only a standalone semantic leaf can be classified as one "
                "internal object"
            );
        }
    }

    std::vector<DocumentObject*> finalOperations;
    finalOperations.reserve(operations.size() - 1);
    finalOperations.insert(finalOperations.end(), operations.begin(), operations.begin() + rawIndex);
    finalOperations.insert(finalOperations.end(), operations.begin() + rawIndex + 1, operations.end());
    std::vector<TimelineObjectIdentity> finalOperationIdentities;
    finalOperationIdentities.reserve(finalOperations.size());
    for (auto* operation : finalOperations) {
        finalOperationIdentities.push_back(
            TimelineObjectIdentity {
                .objectId = operation->getID(),
                .objectName = operation->getNameInDocument(),
            }
        );
    }
    boost::dynamic_bitset<> finalVisibility(finalOperations.size());
    boost::dynamic_bitset<> finalSuppression(finalOperations.size());
    for (std::size_t oldIndex = 0, newIndex = 0; oldIndex < operations.size(); ++oldIndex) {
        if (oldIndex == rawIndex) {
            continue;
        }
        finalVisibility.set(newIndex, visibility.test(oldIndex));
        finalSuppression.set(newIndex, suppression.test(oldIndex));
        ++newIndex;
    }
    const long finalPosition = position - (static_cast<long>(rawIndex) < position ? 1 : 0);

    // Validation is complete. Property and timeline mutation now occur under
    // one ApplyingScope so synchronous role callbacks cannot re-enroll the
    // object before its exact provisional entry has been removed.
    const TimelineObjectIdentity classifiedIdentity {
        .objectId = object->getID(),
        .objectName = object->getNameInDocument(),
    };
    ApplyingScope applying(*this);
    const bool createsRole = roleProperty == nullptr;
    auto* currentObject = resolveExactTimelineIdentity(
        document,
        classifiedIdentity.objectId,
        classifiedIdentity.objectName
    );
    auto* mutableRole = currentObject
        ? dynamic_cast<PropertyString*>(localTimelineMetadataProperty(currentObject, RolePropertyName))
        : nullptr;
    if (!mutableRole) {
        if (!currentObject) {
            throw Base::RuntimeError("The internal timeline object changed before classification");
        }
        currentObject->addDynamicProperty(
            "App::PropertyString",
            RolePropertyName,
            "Timeline",
            "Document timeline classification",
            Prop_NoRecompute,
            true,
            true
        );
        currentObject = resolveExactTimelineIdentity(
            document,
            classifiedIdentity.objectId,
            classifiedIdentity.objectName
        );
        mutableRole = currentObject
            ? dynamic_cast<PropertyString*>(
                  localTimelineMetadataProperty(currentObject, RolePropertyName)
              )
            : nullptr;
    }
    if (!currentObject || !mutableRole) {
        throw Base::TypeError("The internal timeline role property has an incompatible type");
    }
    mutableRole->setValue(InternalRole);
    currentObject = resolveExactTimelineIdentity(
        document,
        classifiedIdentity.objectId,
        classifiedIdentity.objectName
    );
    mutableRole = currentObject
        ? dynamic_cast<PropertyString*>(localTimelineMetadataProperty(currentObject, RolePropertyName))
        : nullptr;
    if (!currentObject || !mutableRole || std::string_view(mutableRole->getValue()) != InternalRole) {
        throw Base::RuntimeError("The internal timeline role could not be applied");
    }
    if (createsRole) {
        mutableRole->setStatus(Property::Hidden, true);
        currentObject = resolveExactTimelineIdentity(
            document,
            classifiedIdentity.objectId,
            classifiedIdentity.objectName
        );
        mutableRole = currentObject
            ? dynamic_cast<PropertyString*>(
                  localTimelineMetadataProperty(currentObject, RolePropertyName)
              )
            : nullptr;
        if (!mutableRole) {
            throw Base::RuntimeError("The internal timeline role changed while it was hidden");
        }
        mutableRole->setStatus(Property::LockDynamic, true);
        currentObject = resolveExactTimelineIdentity(
            document,
            classifiedIdentity.objectId,
            classifiedIdentity.objectName
        );
        mutableRole = currentObject
            ? dynamic_cast<PropertyString*>(
                  localTimelineMetadataProperty(currentObject, RolePropertyName)
              )
            : nullptr;
        if (!mutableRole) {
            throw Base::RuntimeError("The internal timeline role changed while it was locked");
        }
        mutableRole->setStatus(Property::NoRecompute, true);
    }

    std::vector<DocumentObject*> resolvedFinalOperations;
    resolvedFinalOperations.reserve(finalOperationIdentities.size());
    for (const auto& identity : finalOperationIdentities) {
        auto* operation
            = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
        if (!operation) {
            throw Base::RuntimeError(
                "A retained timeline identity changed during internal classification"
            );
        }
        resolvedFinalOperations.push_back(operation);
    }
    Operations.setValues(resolvedFinalOperations);
    VisibilityAtEnd.setValues(finalVisibility);
    SuppressionAtEnd.setValues(finalSuppression);
    Position.setValue(finalPosition);
    SchemaVersion.setValue(CurrentSchemaVersion);
    for (std::size_t index = 0; index < finalOperationIdentities.size(); ++index) {
        const auto& identity = finalOperationIdentities[index];
        if (resolveExactTimelineIdentity(document, identity.objectId, identity.objectName)
            != resolvedFinalOperations[index]) {
            throw Base::RuntimeError(
                "A retained timeline identity changed after internal classification"
            );
        }
    }
    currentObject = resolveExactTimelineIdentity(
        document,
        classifiedIdentity.objectId,
        classifiedIdentity.objectName
    );
    mutableRole = currentObject
        ? dynamic_cast<PropertyString*>(localTimelineMetadataProperty(currentObject, RolePropertyName))
        : nullptr;
    if (Operations.getValues() != resolvedFinalOperations
        || VisibilityAtEnd.getValues() != finalVisibility
        || SuppressionAtEnd.getValues() != finalSuppression || Position.getValue() != finalPosition
        || !mutableRole || std::string_view(mutableRole->getValue()) != InternalRole
        || !mutableRole->testStatus(Property::Hidden)
        || !mutableRole->testStatus(Property::LockDynamic)
        || !mutableRole->testStatus(Property::NoRecompute)) {
        throw Base::RuntimeError("The exact internal timeline classification did not apply");
    }

    if (requireProvisional) {
        _provisionalInternalObjects.push_back(
            ProvisionalInternalObject {
                .transactionId = transactionId,
                .object = classifiedIdentity,
            }
        );
        _provisionalEnrollments.erase(enrollment);
        for (auto& provenance : _provisionalTransactionCreations) {
            if (provenance.transactionId != transactionId) {
                continue;
            }
            std::erase_if(
                provenance.objects,
                [&classifiedIdentity](const TimelineObjectIdentity& identity) {
                    return identity.objectId == classifiedIdentity.objectId
                        && identity.objectName == classifiedIdentity.objectName;
                }
            );
        }
    }
}

void DocumentTimeline::classifyExistingSemanticBlockInternal(DocumentObject* operation)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Retiring a legacy semantic block requires one normal document "
            "and one caller-owned transaction"
        );
    }
    if (!operation || !document->containsObject(operation) || operation->getDocument() != document
        || !operation->getNameInDocument()) {
        throw Base::ValueError("The legacy semantic root must be live in this document");
    }

    pruneProvisionalEnrollments();
    pruneStagedResourceAdoptions();
    pruneProvisionalInternalObjects();
    pruneStagedSegmentReplacement();
    pruneStagedResourceReconciliation();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The legacy-block migration transaction is no longer active");
    }
    if (!_stagedResourceAdoptions.empty() || !_stagedSegmentReplacements.empty()
        || !_stagedResourceReconciliations.empty()) {
        throw Base::RuntimeError(
            "Another exact History graph rewrite is already staged by this transaction"
        );
    }
    if (isProvisionallyEnrolledByCurrentTransaction(operation)) {
        throw Base::ValueError(
            "A current-transaction provisional object is not a legacy semantic root"
        );
    }

    const auto operations = Operations.getValues();
    const auto visibility = VisibilityAtEnd.getValues();
    const auto suppression = SuppressionAtEnd.getValues();
    const long position = Position.getValue();
    if (visibility.size() != operations.size() || suppression.size() != operations.size()
        || position < 0 || position > static_cast<long>(operations.size())) {
        throw Base::RuntimeError("The timeline state is inconsistent");
    }
    std::unordered_map<DocumentObject*, std::size_t> indices;
    indices.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* candidate = operations[index];
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !indices.emplace(candidate, index).second || !isOperationCandidate(candidate)
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The timeline contains a missing, duplicate, internal, or malformed operation"
            );
        }
    }
    const auto rootIndex = indices.find(operation);
    if (rootIndex == indices.end() || !hasTimelineOperationRole(operation)
        || hasTimelineResourceRole(operation) || timelineOwner(operation)
        || semanticOperationRoot(operation, document) != operation) {
        throw Base::ValueError("The migration target must be one explicit legacy semantic root");
    }

    std::size_t blockBegin = operations.size();
    std::size_t blockEnd = 0;
    std::size_t blockCount = 0;
    for (std::size_t index = 0; index < operations.size(); ++index) {
        if (semanticOperationRoot(operations[index], document) != operation) {
            continue;
        }
        blockBegin = std::min(blockBegin, index);
        blockEnd = std::max(blockEnd, index + 1);
        ++blockCount;
    }
    if (blockCount == 0 || blockEnd - blockBegin != blockCount || blockEnd == 0
        || operations[blockEnd - 1] != operation) {
        throw Base::RuntimeError(
            "The legacy semantic block is not one contiguous resource-first/root-last block"
        );
    }
    std::vector<DocumentObject*> block(
        operations.begin() + static_cast<std::ptrdiff_t>(blockBegin),
        operations.begin() + static_cast<std::ptrdiff_t>(blockEnd)
    );
    validateCanonicalSemanticBlockOrder(
        block,
        operation,
        "The legacy semantic block is not in canonical resource-first/root-last order"
    );
    if (position > static_cast<long>(blockBegin) && position < static_cast<long>(blockEnd)) {
        throw Base::ValueError(
            "Move History to a semantic operation boundary before converting this legacy object"
        );
    }
    for (auto* candidate : block) {
        if (isProvisionallyEnrolledByCurrentTransaction(candidate)) {
            throw Base::ValueError(
                "A legacy semantic block cannot contain current-transaction identities"
            );
        }
        auto* role = dynamic_cast<PropertyString*>(
            localTimelineMetadataProperty(candidate, RolePropertyName)
        );
        if (!role
            || (candidate == operation ? std::string_view(role->getValue()) != OperationRole
                                       : std::string_view(role->getValue()) != ResourceRole)) {
            throw Base::RuntimeError("The legacy semantic block has malformed role metadata");
        }
        validateCanonicalTimelineMetadataStatus(
            role,
            "Legacy semantic role metadata is not hidden, locked, and non-recomputing"
        );
        if (auto* ownerProperty = localTimelineMetadataProperty(candidate, OwnerPropertyName)) {
            auto* owner = dynamic_cast<PropertyLinkHidden*>(ownerProperty);
            if (!owner
                || (candidate == operation ? owner->getValue() != nullptr
                                           : owner->getValue() == nullptr)) {
                throw Base::RuntimeError("The legacy semantic block has malformed ownership metadata");
            }
            validateCanonicalTimelineMetadataStatus(
                owner,
                "Legacy semantic owner metadata is not hidden, locked, and non-recomputing"
            );
        }
        for (const char* propertyName : {
                 EditorPropertyName,
                 EditCommandPropertyName,
                 DeleteCommandPropertyName,
                 ReplacedInputsPropertyName,
             }) {
            if (auto* property = localTimelineMetadataProperty(candidate, propertyName)) {
                validateCanonicalTimelineMetadataStatus(
                    property,
                    "Legacy semantic metadata is not hidden, locked, and non-recomputing"
                );
            }
        }
    }

    std::vector<DocumentObject*> finalOperations;
    finalOperations.reserve(operations.size() - blockCount);
    boost::dynamic_bitset<> finalVisibility(operations.size() - blockCount);
    boost::dynamic_bitset<> finalSuppression(operations.size() - blockCount);
    for (std::size_t oldIndex = 0, newIndex = 0; oldIndex < operations.size(); ++oldIndex) {
        if (oldIndex >= blockBegin && oldIndex < blockEnd) {
            continue;
        }
        finalOperations.push_back(operations[oldIndex]);
        finalVisibility.set(newIndex, visibility.test(oldIndex));
        finalSuppression.set(newIndex, suppression.test(oldIndex));
        ++newIndex;
    }
    const long finalPosition = position <= static_cast<long>(blockBegin)
        ? position
        : position - static_cast<long>(blockCount);
    std::vector<TimelineObjectIdentity> blockIdentities;
    blockIdentities.reserve(block.size());
    for (auto* candidate : block) {
        blockIdentities.push_back(
            TimelineObjectIdentity {
                .objectId = candidate->getID(),
                .objectName = candidate->getNameInDocument(),
            }
        );
    }

    ApplyingScope applying(*this);
    for (const auto& identity : blockIdentities) {
        auto* candidate
            = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
        if (!candidate) {
            throw Base::RuntimeError(
                "A legacy semantic identity changed before internal classification"
            );
        }
        if (auto* owner = dynamic_cast<PropertyLinkHidden*>(
                localTimelineMetadataProperty(candidate, OwnerPropertyName)
            )) {
            owner->setValue(nullptr);
        }
        if (auto* editor = dynamic_cast<PropertyLinkHidden*>(
                localTimelineMetadataProperty(candidate, EditorPropertyName)
            )) {
            editor->setValue(nullptr);
        }
        if (auto* command = dynamic_cast<PropertyString*>(
                localTimelineMetadataProperty(candidate, EditCommandPropertyName)
            )) {
            command->setValue("");
        }
        if (auto* command = dynamic_cast<PropertyString*>(
                localTimelineMetadataProperty(candidate, DeleteCommandPropertyName)
            )) {
            command->setValue("");
        }
        if (auto* replacements = dynamic_cast<PropertyLinkListHidden*>(
                localTimelineMetadataProperty(candidate, ReplacedInputsPropertyName)
            )) {
            replacements->setValues({});
        }
        auto* role = dynamic_cast<PropertyString*>(
            localTimelineMetadataProperty(candidate, RolePropertyName)
        );
        if (!role) {
            throw Base::RuntimeError(
                "A legacy semantic identity lost its role during internal classification"
            );
        }
        role->setValue(InternalRole);
    }

    Operations.setValues(finalOperations);
    VisibilityAtEnd.setValues(finalVisibility);
    SuppressionAtEnd.setValues(finalSuppression);
    Position.setValue(finalPosition);
    SchemaVersion.setValue(CurrentSchemaVersion);

    for (const auto& identity : blockIdentities) {
        auto* candidate
            = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
        const auto* role = candidate ? dynamic_cast<const PropertyString*>(
                                           localTimelineMetadataProperty(candidate, RolePropertyName)
                                       )
                                     : nullptr;
        const auto* owner = candidate
            ? dynamic_cast<const PropertyLinkHidden*>(
                  localTimelineMetadataProperty(candidate, OwnerPropertyName)
              )
            : nullptr;
        if (!candidate || !role || std::string_view(role->getValue()) != InternalRole
            || (owner && owner->getValue())) {
            throw Base::RuntimeError(
                "The legacy semantic block did not become retained internal state"
            );
        }
    }
    if (Operations.getValues() != finalOperations || VisibilityAtEnd.getValues() != finalVisibility
        || SuppressionAtEnd.getValues() != finalSuppression || Position.getValue() != finalPosition) {
        throw Base::RuntimeError(
            "The validated legacy semantic block migration changed while it was applied"
        );
    }
}

void DocumentTimeline::recordOperation(DocumentObject* operation)
{
    auto* document = getDocument();
    pruneProvisionalEnrollments();
    pruneProvisionalTransactionCreations();
    pruneProvisionalInternalObjects();
    pruneStagedSegmentReplacement();
    pruneStagedResourceReconciliation();
    const int currentTransaction = document ? document->getBookedTransactionID()
                                            : App::NullTransaction;
    const bool reclassifiesProvisionalInternal = document && operation
        && std::ranges::any_of(_provisionalInternalObjects,
                               [currentTransaction,
                                operation](const ProvisionalInternalObject& internal) {
                                   return internal.transactionId == currentTransaction
                                       && internal.object.objectId == operation->getID()
                                       && internal.object.objectName
                                       == operation->getNameInDocument();
                               });
    if (reclassifiesProvisionalInternal) {
        const auto* role = dynamic_cast<const PropertyString*>(
            localTimelineMetadataProperty(operation, RolePropertyName)
        );
        if (!role || std::string_view(role->getValue()) != OperationRole) {
            return;
        }
    }
    if (!document || !document->containsObject(operation) || !isOperationCandidate(operation)
        || operation->getDocument() != document || isApplying()
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()) {
        return;
    }

    const auto storedOperations = Operations.getValues();
    const auto storedVisibility = VisibilityAtEnd.getValues();
    const auto storedSuppression = SuppressionAtEnd.getValues();
    const auto storedBoundary
        = std::clamp(Position.getValue(), 0L, static_cast<long>(storedOperations.size()));

    // A document callback may request a new operation before generic link
    // cleanup has removed an already-detached address from the persisted
    // sequence.  Compact all parallel state by address-only liveness before
    // dereferencing any saved entry.  This is also the exact place to discard
    // duplicate or no-longer-present operations without shifting the current
    // state marker across surviving history.
    std::vector<DocumentObject*> operations;
    operations.reserve(storedOperations.size());
    boost::dynamic_bitset<> visibility;
    boost::dynamic_bitset<> suppression;
    std::unordered_set<DocumentObject*> seen;
    long boundary = 0;
    for (std::size_t index = 0; index < storedOperations.size(); ++index) {
        auto* candidate = storedOperations[index];
        if (!document->containsObject(candidate) || candidate->getDocument() != document
            || !isOperationCandidate(candidate) || !seen.insert(candidate).second) {
            continue;
        }

        if (static_cast<long>(index) < storedBoundary) {
            ++boundary;
        }
        operations.push_back(candidate);
        visibility.resize(operations.size());
        visibility.set(
            operations.size() - 1,
            bitAt(storedVisibility, index, candidate->Visibility.getValue())
        );
        suppression.resize(operations.size());
        suppression.set(
            operations.size() - 1,
            bitAt(storedSuppression, index, operationSuppressed(candidate))
        );
    }

    if (std::find(operations.begin(), operations.end(), operation) != operations.end()) {
        if (operations != storedOperations || visibility != storedVisibility
            || suppression != storedSuppression || boundary != Position.getValue()) {
            ApplyingScope applying(*this);
            Operations.setValues(operations);
            VisibilityAtEnd.setValues(visibility);
            SuppressionAtEnd.setValues(suppression);
            Position.setValue(boundary);
            SchemaVersion.setValue(CurrentSchemaVersion);
        }
        return;
    }

    const auto oldSize = operations.size();
    const auto insertion = static_cast<std::size_t>(boundary);

    operations.insert(operations.begin() + boundary, operation);

    boost::dynamic_bitset<> newVisibility(oldSize + 1);
    boost::dynamic_bitset<> newSuppression(oldSize + 1);
    for (std::size_t oldIndex = 0; oldIndex < oldSize; ++oldIndex) {
        const auto newIndex = oldIndex < insertion ? oldIndex : oldIndex + 1;
        newVisibility.set(
            newIndex,
            bitAt(visibility, oldIndex, operations[newIndex]->Visibility.getValue())
        );
        newSuppression.set(
            newIndex,
            bitAt(suppression, oldIndex, operationSuppressed(operations[newIndex]))
        );
    }
    newVisibility.set(insertion, operation->Visibility.getValue());
    newSuppression.set(insertion, operationSuppressed(operation));

    ApplyingScope applying(*this);
    Operations.setValues(operations);
    VisibilityAtEnd.setValues(newVisibility);
    SuppressionAtEnd.setValues(newSuppression);
    Position.setValue(boundary + 1);
    SchemaVersion.setValue(CurrentSchemaVersion);
    rememberProvisionalEnrollment(operation, boundary);
    std::erase_if(
        _provisionalInternalObjects,
        [currentTransaction, operation](const ProvisionalInternalObject& internal) {
            return internal.transactionId == currentTransaction
                && internal.object.objectId == operation->getID()
                && internal.object.objectName == operation->getNameInDocument();
        }
    );
}

void DocumentTimeline::publishProvisionalOperationBlock(
    DocumentObject* operation,
    const std::vector<DocumentObject*>& orderedResources,
    const std::vector<DocumentObject*>& resourceOwners
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError("Publishing a semantic timeline block requires one normal "
                                 "document and one caller-owned transaction");
    }
    if (!operation || !document->containsObject(operation) || operation->getDocument() != document
        || !operation->getNameInDocument()) {
        throw Base::ValueError("The published operation must be live in this document");
    }
    if (!resourceOwners.empty() && resourceOwners.size() != orderedResources.size()) {
        throw Base::ValueError("Explicit resource owners must exactly parallel ordered resources");
    }

    pruneProvisionalEnrollments();
    pruneProvisionalTransactionCreations();
    pruneProvisionalPublications();
    pruneStagedResourceAdoptions();
    pruneProvisionalInternalObjects();
    pruneStagedSegmentReplacement();
    pruneStagedResourceReconciliation();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The semantic-publication transaction is no longer active");
    }
    auto provenance = std::find_if(
        _provisionalTransactionCreations.begin(),
        _provisionalTransactionCreations.end(),
        [document, transactionId](const ProvisionalTransactionCreations& candidate) {
            return candidate.transactionId == transactionId
                && candidate.documentName == document->getName()
                && candidate.documentUid == document->Uid.getValueStr();
        }
    );
    if (provenance == _provisionalTransactionCreations.end()) {
        throw Base::RuntimeError("No exact current-transaction creation provenance is available");
    }

    std::vector<DocumentObject*> block = orderedResources;
    block.push_back(operation);
    const TimelineObjectIdentity publishedOperationIdentity {
        .objectId = operation->getID(),
        .objectName = operation->getNameInDocument(),
    };
    std::unordered_set<DocumentObject*> blockSet;
    blockSet.reserve(block.size());
    const auto isPublishableObject = [](const DocumentObject* object) {
        if (!object || object->isDerivedFrom<DocumentTimeline>() || object->isDerivedFrom<Origin>()) {
            return false;
        }
        if (object->isDerivedFrom<DatumElement>()
            && static_cast<const DatumElement*>(object)->getLCS()) {
            return false;
        }
        const char* viewProvider = object->getViewProviderNameStored();
        return viewProvider && viewProvider[0] != '\0';
    };
    for (auto* candidate : block) {
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !candidate->getNameInDocument() || !isPublishableObject(candidate)
            || !blockSet.insert(candidate).second || !isCreatedByCurrentTransaction(candidate)) {
            throw Base::ValueError("Every published block identity must be distinct, live, "
                                   "publishable, and created by this exact transaction");
        }
    }

    std::unordered_set<DocumentObject*> resourceSet(orderedResources.begin(), orderedResources.end());
    std::unordered_map<DocumentObject*, DocumentObject*> declaredOwners;
    declaredOwners.reserve(orderedResources.size());
    for (std::size_t index = 0; index < orderedResources.size(); ++index) {
        auto* resource = orderedResources[index];
        auto* declaredOwner = resourceOwners.empty() ? operation : resourceOwners[index];
        if (!declaredOwner || declaredOwner == resource
            || (declaredOwner != operation && !resourceSet.contains(declaredOwner))) {
            throw Base::ValueError("Every explicit resource owner must be the operation or one "
                                   "resource in the same published block");
        }
        declaredOwners.emplace(resource, declaredOwner);
    }

    const auto declaredOwnerChainContains =
        [&declaredOwners, operation](const DocumentObject* object, const DocumentObject* ancestor) {
            std::unordered_set<const DocumentObject*> visited;
            auto* current = object;
            while (current && current != operation) {
                const auto found = declaredOwners.find(const_cast<DocumentObject*>(current));
                if (found == declaredOwners.end() || !visited.insert(current).second) {
                    return false;
                }
                current = found->second;
                if (current == ancestor) {
                    return true;
                }
            }
            return false;
        };
    for (auto* resource : orderedResources) {
        std::unordered_set<DocumentObject*> visited;
        auto* current = resource;
        while (current != operation) {
            if (!visited.insert(current).second) {
                throw Base::RuntimeError("The declared publication ownership graph is cyclic");
            }
            const auto found = declaredOwners.find(current);
            if (found == declaredOwners.end()) {
                throw Base::RuntimeError("A published resource does not resolve to the declared "
                                         "operation");
            }
            current = found->second;
        }
    }

    validateCanonicalNestedResourceOrder(
        orderedResources,
        declaredOwnerChainContains,
        "Published resources must be in canonical nested resource-first, owner-last order"
    );

    ProvisionalPublication publication {
        .transactionId = transactionId,
        .documentName = document->getName(),
        .documentUid = document->Uid.getValueStr(),
        .operation = publishedOperationIdentity,
        .orderedMembers = {},
    };
    publication.orderedMembers.reserve(block.size());
    for (auto* resource : orderedResources) {
        auto* owner = declaredOwners.at(resource);
        publication.orderedMembers.push_back(
            ProvisionalPublicationMember {
                .object = {
                    .objectId = resource->getID(),
                    .objectName = resource->getNameInDocument(),
                },
                .owner = {
                    .objectId = owner->getID(),
                    .objectName = owner->getNameInDocument(),
                },
            }
        );
    }
    publication.orderedMembers.push_back(ProvisionalPublicationMember {
        .object = publishedOperationIdentity,
        .owner = {},
    });
    _provisionalPublications.reserve(_provisionalPublications.size() + 1);

    struct MetadataPlan
    {
        TimelineObjectIdentity object;
        TimelineObjectIdentity declaredOwner;
        bool isRoot {false};
        bool roleExists {false};
        bool ownerExists {false};
        std::string originalRole;
        TimelineObjectIdentity originalOwner;
    };
    std::vector<MetadataPlan> metadataPlans;
    metadataPlans.reserve(block.size());
    for (auto* candidate : block) {
        const bool isRoot = candidate == operation;
        auto* role = dynamic_cast<PropertyString*>(
            localTimelineMetadataProperty(candidate, RolePropertyName)
        );
        if (localTimelineMetadataProperty(candidate, RolePropertyName) && !role) {
            throw Base::RuntimeError("A published role property has the wrong type");
        }
        if (role) {
            validateCanonicalTimelineMetadataStatus(
                role,
                "Existing published role metadata is not hidden, locked, and non-recomputing"
            );
        }
        const std::string_view roleValue = role ? std::string_view(role->getValue())
                                                : std::string_view {};
        const std::string_view expectedRole = isRoot ? std::string_view(OperationRole)
                                                     : std::string_view(ResourceRole);
        if (!roleValue.empty() && roleValue != expectedRole) {
            throw Base::RuntimeError("A published object has incompatible existing role metadata");
        }

        auto* ownerLink = dynamic_cast<PropertyLinkHidden*>(
            localTimelineMetadataProperty(candidate, OwnerPropertyName)
        );
        if (localTimelineMetadataProperty(candidate, OwnerPropertyName) && !ownerLink) {
            throw Base::RuntimeError("A published owner property has the wrong type");
        }
        if (ownerLink) {
            validateCanonicalTimelineMetadataStatus(
                ownerLink,
                "Existing published owner metadata is not hidden, locked, and non-recomputing"
            );
        }
        auto* declaredOwner = isRoot ? nullptr : declaredOwners.at(candidate);
        if (ownerLink && ownerLink->getValue() && ownerLink->getValue() != declaredOwner) {
            throw Base::RuntimeError("A published object has incompatible existing ownership");
        }

        const auto* editorProperty = localTimelineMetadataProperty(candidate, EditorPropertyName);
        const auto* editor = dynamic_cast<const PropertyLinkHidden*>(editorProperty);
        if (editorProperty && !editor) {
            throw Base::RuntimeError("Published editor metadata has the wrong property type");
        }
        if (editor) {
            validateCanonicalTimelineMetadataStatus(
                editor,
                "Published editor metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (editor && editor->getValue()) {
            auto* editorValue = editor->getValue();
            if (!resourceSet.contains(editorValue)
                || !declaredOwnerChainContains(editorValue, candidate)) {
                throw Base::RuntimeError(
                    "A published editor is not an owned descendant of its "
                    "controller"
                );
            }
        }

        const auto* editCommandProperty
            = localTimelineMetadataProperty(candidate, EditCommandPropertyName);
        const auto* editCommand = dynamic_cast<const PropertyString*>(editCommandProperty);
        if (editCommandProperty && !editCommand) {
            throw Base::RuntimeError("Published edit-command metadata has the wrong property type");
        }
        if (editCommand) {
            validateCanonicalTimelineMetadataStatus(
                editCommand,
                "Published edit-command metadata is not hidden, locked, and non-recomputing"
            );
        }

        const auto* deleteCommandProperty =
            localTimelineMetadataProperty(candidate, DeleteCommandPropertyName);
        const auto* deleteCommand =
            dynamic_cast<const PropertyString*>(deleteCommandProperty);
        if (deleteCommandProperty && !deleteCommand) {
            throw Base::RuntimeError(
                "Published delete-command metadata has the wrong property type"
            );
        }
        if (deleteCommand) {
            validateCanonicalTimelineMetadataStatus(
                deleteCommand,
                "Published delete-command metadata is not hidden, locked, and non-recomputing"
            );
        }

        const auto* replacementProperty
            = localTimelineMetadataProperty(candidate, ReplacedInputsPropertyName);
        const auto* replacements = dynamic_cast<const PropertyLinkListHidden*>(replacementProperty);
        if (replacementProperty && !replacements) {
            throw Base::RuntimeError("Published replacement metadata has the wrong property type");
        }
        if (replacements) {
            validateCanonicalTimelineMetadataStatus(
                replacements,
                "Published replacement metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (!isRoot && replacementProperty) {
            throw Base::RuntimeError("Only the published operation may declare replacement inputs");
        }
        if (replacements) {
            std::unordered_set<DocumentObject*> directInputs;
            for (auto* input : replacements->getValues()) {
                if (!input || input == operation || !document->containsObject(input)
                    || input->getDocument() != document || blockSet.contains(input)
                    || !directInputs.insert(input).second || !hasValidTimelineOwnerChain(input)
                    || !replacementInputContract(input).valid) {
                    throw Base::RuntimeError(
                        "Published replacement inputs are malformed, "
                        "duplicate, or part of the new block"
                    );
                }
            }
        }

        metadataPlans.push_back(MetadataPlan {
            .object = {
                .objectId = candidate->getID(),
                .objectName = candidate->getNameInDocument(),
            },
            .declaredOwner = declaredOwner
                ? TimelineObjectIdentity {
                      .objectId = declaredOwner->getID(),
                      .objectName = declaredOwner->getNameInDocument(),
                  }
                : TimelineObjectIdentity {},
            .isRoot = isRoot,
            .roleExists = role != nullptr,
            .ownerExists = ownerLink != nullptr,
            .originalRole = role ? role->getValue() : "",
            .originalOwner = ownerLink && ownerLink->getValue()
                ? TimelineObjectIdentity {
                      .objectId = ownerLink->getValue()->getID(),
                      .objectName = ownerLink->getValue()->getNameInDocument(),
                  }
                : TimelineObjectIdentity {},
        });
    }

    const auto currentOperations = Operations.getValues();
    const auto currentVisibility = VisibilityAtEnd.getValues();
    const auto currentSuppression = SuppressionAtEnd.getValues();
    const long currentPosition = Position.getValue();
    std::vector<TimelineObjectIdentity> currentOperationIdentities;
    currentOperationIdentities.reserve(currentOperations.size());
    if (currentVisibility.size() != currentOperations.size()
        || currentSuppression.size() != currentOperations.size() || currentPosition < 0
        || currentPosition > static_cast<long>(currentOperations.size()) || provenance->position < 0
        || provenance->position > static_cast<long>(provenance->operations.size())) {
        throw Base::RuntimeError("The current or pre-creation timeline state is inconsistent");
    }

    std::unordered_map<DocumentObject*, std::size_t> currentIndices;
    currentIndices.reserve(currentOperations.size());
    for (std::size_t index = 0; index < currentOperations.size(); ++index) {
        auto* candidate = currentOperations[index];
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !currentIndices.emplace(candidate, index).second) {
            throw Base::RuntimeError(
                "The current timeline contains a missing, duplicate, or "
                "cross-document identity"
            );
        }
        if (!blockSet.contains(candidate)
            && (!isOperationCandidate(candidate) || !hasValidTimelineOwnerChain(candidate)
                || !replacementInputContract(candidate).valid)) {
            throw Base::RuntimeError("The current timeline contains a malformed retained operation");
        }
        currentOperationIdentities.push_back(
            TimelineObjectIdentity {
                .objectId = candidate->getID(),
                .objectName = candidate->getNameInDocument(),
            }
        );
    }

    std::unordered_set<DocumentObject*> currentGenerationObjects;
    currentGenerationObjects.reserve(provenance->objects.size());
    for (const auto& identity : provenance->objects) {
        if (auto* created
            = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName)) {
            currentGenerationObjects.insert(created);
        }
    }

    std::unordered_map<DocumentObject*, long> provisionalMarkers;
    for (const auto& enrollment : _provisionalEnrollments) {
        if (enrollment.transactionId != transactionId) {
            continue;
        }
        auto* candidate
            = resolveExactTimelineIdentity(document, enrollment.objectId, enrollment.objectName);
        if (!candidate || enrollment.insertionMarker < 0
            || enrollment.insertionMarker >= static_cast<long>(currentOperations.size())
            || currentOperations[static_cast<std::size_t>(enrollment.insertionMarker)] != candidate) {
            throw Base::RuntimeError(
                "A provisional operation moved after its exact creation "
                "proof was captured"
            );
        }
        provisionalMarkers.emplace(candidate, enrollment.insertionMarker);
    }

    std::unordered_set<DocumentObject*> pendingTrackedSet;
    std::vector<DocumentObject*> pendingTrackedOperations;
    pendingTrackedSet.reserve(provisionalMarkers.size());
    pendingTrackedOperations.reserve(provisionalMarkers.size());
    for (auto* candidate : currentOperations) {
        if (currentGenerationObjects.contains(candidate) && provisionalMarkers.contains(candidate)) {
            pendingTrackedSet.insert(candidate);
            pendingTrackedOperations.push_back(candidate);
        }
    }

    std::unordered_set<DocumentObject*> trackedBlockSet;
    for (auto* candidate : block) {
        if (!currentIndices.contains(candidate)) {
            continue;
        }
        if (!provisionalMarkers.contains(candidate) || !trackedBlockSet.insert(candidate).second) {
            throw Base::RuntimeError(
                "A tracked published identity lacks exact provisional "
                "enrollment proof"
            );
        }
    }
    bool remainingPendingSeen = false;
    for (auto* candidate : pendingTrackedOperations) {
        if (!trackedBlockSet.contains(candidate)) {
            remainingPendingSeen = true;
        }
        else if (remainingPendingSeen) {
            throw Base::RuntimeError(
                "Published blocks must consume pending creation generations "
                "in their original block order"
            );
        }
    }

    std::vector<DocumentObject*> baselineOperations;
    baselineOperations.reserve(provenance->operations.size());
    for (const auto& snapshot : provenance->operations) {
        auto* retained = resolveExactTimelineIdentity(
            document,
            snapshot.object.objectId,
            snapshot.object.objectName
        );
        if (!retained) {
            throw Base::RuntimeError(
                "A pre-creation timeline identity changed during semantic "
                "publication"
            );
        }
        baselineOperations.push_back(retained);
    }
    std::vector<DocumentObject*> currentBase;
    currentBase.reserve(baselineOperations.size());
    for (auto* candidate : currentOperations) {
        if (!pendingTrackedSet.contains(candidate)) {
            currentBase.push_back(candidate);
        }
    }
    if (currentBase != baselineOperations
        || currentOperations.size() != baselineOperations.size() + pendingTrackedSet.size()
        || currentPosition != provenance->position + static_cast<long>(pendingTrackedSet.size())) {
        throw Base::RuntimeError(
            "The timeline changed outside the exact current-transaction "
            "publication block"
        );
    }

    std::unordered_map<DocumentObject*, std::size_t> baselineIndices;
    baselineIndices.reserve(baselineOperations.size());
    for (std::size_t index = 0; index < baselineOperations.size(); ++index) {
        baselineIndices.emplace(baselineOperations[index], index);
        const auto current = currentIndices.find(baselineOperations[index]);
        const auto& snapshot = provenance->operations[index];
        if (current == currentIndices.end()
            || currentVisibility.test(current->second) != snapshot.visibility
            || currentSuppression.test(current->second) != snapshot.suppression) {
            const bool currentVisible = current != currentIndices.end()
                && currentVisibility.test(current->second);
            const bool currentSuppressed = current != currentIndices.end()
                && currentSuppression.test(current->second);
            throw Base::RuntimeError(
                "Pre-creation operation '" + snapshot.object.objectName
                + "' changed accepted state during semantic publication "
                  "(visibility "
                + (snapshot.visibility ? "visible" : "hidden") + " -> "
                + (currentVisible ? "visible" : "hidden") + ", suppression "
                + (snapshot.suppression ? "on" : "off") + " -> "
                + (currentSuppressed ? "on" : "off") + ")"
            );
        }
    }

    // No already-declared resource may be silently omitted from the exact
    // publication block.
    for (auto* candidate : document->getObjects()) {
        if (!candidate || !hasTimelineResourceRole(candidate) || blockSet.contains(candidate)) {
            continue;
        }
        const auto* root = semanticOperationRoot(candidate, document);
        if (root == operation) {
            throw Base::RuntimeError(
                "The new operation already owns a resource absent from its "
                "declared publication block"
            );
        }
    }

    const auto insertion = static_cast<std::size_t>(provenance->position);
    std::vector<DocumentObject*> remainingPendingOperations;
    remainingPendingOperations.reserve(pendingTrackedOperations.size());
    for (auto* candidate : pendingTrackedOperations) {
        if (!blockSet.contains(candidate)) {
            remainingPendingOperations.push_back(candidate);
        }
    }
    std::vector<DocumentObject*> finalOperations;
    finalOperations.reserve(
        baselineOperations.size() + block.size() + remainingPendingOperations.size()
    );
    finalOperations.insert(
        finalOperations.end(),
        baselineOperations.begin(),
        baselineOperations.begin() + insertion
    );
    finalOperations.insert(finalOperations.end(), block.begin(), block.end());
    finalOperations.insert(
        finalOperations.end(),
        remainingPendingOperations.begin(),
        remainingPendingOperations.end()
    );
    finalOperations.insert(
        finalOperations.end(),
        baselineOperations.begin() + insertion,
        baselineOperations.end()
    );

    std::vector<bool> finalVisibilityValues;
    std::vector<bool> finalSuppressionValues;
    finalVisibilityValues.reserve(finalOperations.size());
    finalSuppressionValues.reserve(finalOperations.size());
    for (auto* candidate : finalOperations) {
        const auto baseline = baselineIndices.find(candidate);
        if (baseline != baselineIndices.end()) {
            const auto& snapshot = provenance->operations[baseline->second];
            finalVisibilityValues.push_back(snapshot.visibility);
            finalSuppressionValues.push_back(snapshot.suppression);
            continue;
        }
        const auto current = currentIndices.find(candidate);
        finalVisibilityValues.push_back(
            current == currentIndices.end() ? candidate->Visibility.getValue()
                                            : currentVisibility.test(current->second)
        );
        finalSuppressionValues.push_back(
            current == currentIndices.end() ? operationSuppressed(candidate)
                                            : currentSuppression.test(current->second)
        );
    }
    const long finalPosition = provenance->position
        + static_cast<long>(block.size() + remainingPendingOperations.size());

    const auto declaredSemanticRoot =
        [document, operation, &blockSet](const DocumentObject* candidate) -> const DocumentObject* {
        if (blockSet.contains(const_cast<DocumentObject*>(candidate))) {
            return operation;
        }
        return semanticOperationRoot(candidate, document);
    };
    struct FinalBlock
    {
        std::size_t begin {std::numeric_limits<std::size_t>::max()};
        std::size_t end {0};
        std::size_t count {0};
        std::size_t rootIndex {std::numeric_limits<std::size_t>::max()};
    };
    std::unordered_map<DocumentObject*, std::size_t> finalIndices;
    std::unordered_map<const DocumentObject*, FinalBlock> finalBlocks;
    finalIndices.reserve(finalOperations.size());
    finalBlocks.reserve(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        auto* candidate = finalOperations[index];
        if (!finalIndices.emplace(candidate, index).second) {
            throw Base::RuntimeError("The published final timeline contains a duplicate identity");
        }
        const auto* root = declaredSemanticRoot(candidate);
        if (!root || (root != operation && hasTimelineResourceRole(root))) {
            throw Base::RuntimeError(
                "The published final timeline has an incomplete semantic "
                "ownership graph"
            );
        }
        auto& finalBlock = finalBlocks[root];
        finalBlock.begin = std::min(finalBlock.begin, index);
        finalBlock.end = std::max(finalBlock.end, index + 1);
        ++finalBlock.count;
        if (candidate == root) {
            finalBlock.rootIndex = index;
        }
    }

    std::vector<std::pair<std::size_t, const DocumentObject*>> rootsInOrder;
    rootsInOrder.reserve(finalBlocks.size());
    for (const auto& [root, finalBlock] : finalBlocks) {
        if (!finalIndices.contains(const_cast<DocumentObject*>(root))
            || finalBlock.rootIndex == std::numeric_limits<std::size_t>::max()
            || finalBlock.rootIndex + 1 != finalBlock.end
            || finalBlock.end - finalBlock.begin != finalBlock.count
            || (static_cast<long>(finalBlock.begin) < finalPosition
                && finalPosition < static_cast<long>(finalBlock.end))) {
            throw Base::RuntimeError(
                "The published final timeline is not canonical at one block "
                "or history boundary"
            );
        }
        rootsInOrder.emplace_back(finalBlock.begin, root);
    }
    std::ranges::sort(rootsInOrder);
    std::unordered_map<const DocumentObject*, std::size_t> rootOrder;
    rootOrder.reserve(rootsInOrder.size());
    for (std::size_t index = 0; index < rootsInOrder.size(); ++index) {
        rootOrder.emplace(rootsInOrder[index].second, index);
    }

    const auto dependencyRoot = [&declaredSemanticRoot](const DocumentObject* dependency) {
        return declaredSemanticRoot(dependency);
    };
    for (const auto* candidate : finalOperations) {
        const auto* candidateRoot = declaredSemanticRoot(candidate);
        const auto candidateOrder = rootOrder.find(candidateRoot);
        if (candidateOrder == rootOrder.end()) {
            throw Base::RuntimeError("A published identity has no semantic history position");
        }

        if (candidate == operation) {
            const auto* replacements = dynamic_cast<const PropertyLinkListHidden*>(
                localTimelineMetadataProperty(candidate, ReplacedInputsPropertyName)
            );
            if (replacements) {
                for (const auto* input : replacements->getValues()) {
                    const auto* inputRoot = dependencyRoot(input);
                    const auto inputOrder = rootOrder.find(inputRoot);
                    if (!inputRoot || inputRoot == candidateRoot || inputOrder == rootOrder.end()
                        || inputOrder->second >= candidateOrder->second) {
                        throw Base::RuntimeError(
                            "Published replacement input '"
                            + std::string(
                                input && input->getNameInDocument() ? input->getNameInDocument()
                                                                    : "<missing>"
                            )
                            + "' resolves to root '"
                            + std::string(
                                inputRoot && inputRoot->getNameInDocument()
                                    ? inputRoot->getNameInDocument()
                                    : "<untracked>"
                            )
                            + "' at "
                            + (inputOrder == rootOrder.end() ? std::string("<no history position>")
                                                             : std::to_string(inputOrder->second))
                            + ", but published operation '"
                            + std::string(operation->getNameInDocument()) + "' is at "
                            + std::to_string(candidateOrder->second)
                        );
                    }
                }
            }
        }

        std::vector<const DocumentObject*> pending {candidate};
        std::unordered_set<const DocumentObject*> visited {
            candidate,
        };
        while (!pending.empty()) {
            const auto* current = pending.back();
            pending.pop_back();
            for (const auto* dependency : current->getOutList()) {
                if (!dependency || !document->containsObject(dependency)
                    || dependency->getDocument() != document
                    || isStructuralTimelineLink(current, dependency)
                    || !visited.insert(dependency).second) {
                    continue;
                }
                const auto* resolvedRoot = dependencyRoot(dependency);
                if (!resolvedRoot) {
                    throw Base::RuntimeError("Published history encountered a malformed dependency");
                }
                const auto dependencyOrder = rootOrder.find(resolvedRoot);
                if (resolvedRoot != candidateRoot && dependencyOrder != rootOrder.end()
                    && dependencyOrder->second > candidateOrder->second) {
                    throw Base::RuntimeError(
                        "Publishing operation '"
                        + std::string(
                            candidateRoot->getNameInDocument() ? candidateRoot->getNameInDocument()
                                                               : "<unnamed>"
                        )
                        + "' at history position " + std::to_string(candidateOrder->second)
                        + " would leave object '"
                        + std::string(
                            current->getNameInDocument() ? current->getNameInDocument() : "<unnamed>"
                        )
                        + "' dependent on '"
                        + std::string(
                            dependency->getNameInDocument() ? dependency->getNameInDocument() : "<unnamed>"
                        )
                        + "', whose operation '"
                        + std::string(
                            resolvedRoot->getNameInDocument() ? resolvedRoot->getNameInDocument()
                                                              : "<unnamed>"
                        )
                        + "' is later at history position " + std::to_string(dependencyOrder->second)
                    );
                }
                pending.push_back(dependency);
            }
        }
    }

    boost::dynamic_bitset<> finalVisibility(finalOperations.size());
    boost::dynamic_bitset<> finalSuppression(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        finalVisibility.set(index, finalVisibilityValues[index]);
        finalSuppression.set(index, finalSuppressionValues[index]);
    }
    std::vector<TimelineObjectIdentity> finalOperationIdentities;
    finalOperationIdentities.reserve(finalOperations.size());
    for (auto* candidate : finalOperations) {
        finalOperationIdentities.push_back(
            TimelineObjectIdentity {
                .objectId = candidate->getID(),
                .objectName = candidate->getNameInDocument(),
            }
        );
    }

    // Dynamic-property and property-value notifications are synchronous and
    // may run arbitrary document callbacks. Retain only stable identities
    // across every such boundary, re-resolve before each access, and restore
    // both timeline state and all live metadata on any failure.
    const long originalSchemaVersion = SchemaVersion.getValue();
    ApplyingScope applying(*this);
    try {
        for (const auto& plan : metadataPlans) {
            auto* candidate
                = resolveExactTimelineIdentity(document, plan.object.objectId, plan.object.objectName);
            if (!candidate) {
                throw Base::RuntimeError("A published identity was removed by a metadata callback");
            }

            auto* role = dynamic_cast<PropertyString*>(
                localTimelineMetadataProperty(candidate, RolePropertyName)
            );
            if (!role) {
                candidate->addDynamicProperty(
                    "App::PropertyString",
                    RolePropertyName,
                    "Timeline",
                    "Document timeline classification",
                    Prop_NoRecompute,
                    false,
                    true
                );
                candidate = resolveExactTimelineIdentity(
                    document,
                    plan.object.objectId,
                    plan.object.objectName
                );
                role = candidate ? dynamic_cast<PropertyString*>(
                                       localTimelineMetadataProperty(candidate, RolePropertyName)
                                   )
                                 : nullptr;
            }
            if (!candidate || !role) {
                throw Base::TypeError("The published role property could not be created");
            }

            PropertyLinkHidden* owner = nullptr;
            DocumentObject* declaredOwner = nullptr;
            if (!plan.isRoot) {
                owner = dynamic_cast<PropertyLinkHidden*>(
                    localTimelineMetadataProperty(candidate, OwnerPropertyName)
                );
                if (!owner) {
                    candidate->addDynamicProperty(
                        "App::PropertyLinkHidden",
                        OwnerPropertyName,
                        "Timeline",
                        "Owning semantic timeline operation",
                        Prop_NoRecompute,
                        false,
                        true
                    );
                    candidate = resolveExactTimelineIdentity(
                        document,
                        plan.object.objectId,
                        plan.object.objectName
                    );
                    owner = candidate
                        ? dynamic_cast<PropertyLinkHidden*>(
                              localTimelineMetadataProperty(candidate, OwnerPropertyName)
                          )
                        : nullptr;
                }
                declaredOwner = resolveExactTimelineIdentity(
                    document,
                    plan.declaredOwner.objectId,
                    plan.declaredOwner.objectName
                );
                if (!candidate || !owner || !declaredOwner) {
                    throw Base::TypeError("The published owner property could not be created");
                }
                owner->setValue(declaredOwner);
                candidate = resolveExactTimelineIdentity(
                    document,
                    plan.object.objectId,
                    plan.object.objectName
                );
                if (!candidate) {
                    throw Base::RuntimeError("A published identity was removed by an owner callback");
                }
                role = dynamic_cast<PropertyString*>(
                    localTimelineMetadataProperty(candidate, RolePropertyName)
                );
                if (!role) {
                    throw Base::RuntimeError("A published role disappeared during ownership setup");
                }
            }

            role->setValue(plan.isRoot ? OperationRole : ResourceRole);
            if (!resolveExactTimelineIdentity(document, plan.object.objectId, plan.object.objectName)) {
                throw Base::RuntimeError("A published identity was removed by a role callback");
            }
        }

        std::vector<DocumentObject*> resolvedFinalOperations;
        resolvedFinalOperations.reserve(finalOperationIdentities.size());
        for (const auto& identity : finalOperationIdentities) {
            auto* candidate
                = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
            if (!candidate) {
                throw Base::RuntimeError("A timeline identity changed during publication callbacks");
            }
            resolvedFinalOperations.push_back(candidate);
        }
        Operations.setValues(resolvedFinalOperations);
        VisibilityAtEnd.setValues(finalVisibility);
        SuppressionAtEnd.setValues(finalSuppression);
        Position.setValue(finalPosition);
        SchemaVersion.setValue(CurrentSchemaVersion);

        const auto setMetadataStatus = [document](
                                           const TimelineObjectIdentity& identity,
                                           const char* propertyName,
                                           const Property::Status status,
                                           const char* error
                                       ) {
            auto* candidate
                = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
            auto* property = candidate ? localTimelineMetadataProperty(candidate, propertyName)
                                       : nullptr;
            if (!candidate || !property) {
                throw Base::RuntimeError(error);
            }
            property->setStatus(status, true);
            candidate = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
            property = candidate ? localTimelineMetadataProperty(candidate, propertyName) : nullptr;
            if (!candidate || !property || !property->testStatus(status)) {
                throw Base::RuntimeError(error);
            }
        };
        for (const auto& plan : metadataPlans) {
            if (!plan.roleExists) {
                setMetadataStatus(
                    plan.object,
                    RolePropertyName,
                    Property::Hidden,
                    "A newly published role property changed while it was hidden"
                );
                setMetadataStatus(
                    plan.object,
                    RolePropertyName,
                    Property::NoRecompute,
                    "A newly published role property changed while recompute was disabled"
                );
                setMetadataStatus(
                    plan.object,
                    RolePropertyName,
                    Property::LockDynamic,
                    "A newly published role property changed while it was locked"
                );
            }
            if (!plan.isRoot && !plan.ownerExists) {
                setMetadataStatus(
                    plan.object,
                    OwnerPropertyName,
                    Property::Hidden,
                    "A newly published owner property changed while it was hidden"
                );
                setMetadataStatus(
                    plan.object,
                    OwnerPropertyName,
                    Property::NoRecompute,
                    "A newly published owner property changed while recompute was disabled"
                );
                setMetadataStatus(
                    plan.object,
                    OwnerPropertyName,
                    Property::LockDynamic,
                    "A newly published owner property changed while it was locked"
                );
            }
        }

        for (std::size_t index = 0; index < finalOperationIdentities.size(); ++index) {
            const auto& identity = finalOperationIdentities[index];
            if (resolveExactTimelineIdentity(document, identity.objectId, identity.objectName)
                != resolvedFinalOperations[index]) {
                throw Base::RuntimeError(
                    "A final timeline identity changed after semantic publication"
                );
            }
        }
        auto* liveOperation = resolveExactTimelineIdentity(
            document,
            publishedOperationIdentity.objectId,
            publishedOperationIdentity.objectName
        );
        if (!liveOperation || Operations.getValues() != resolvedFinalOperations
            || VisibilityAtEnd.getValues() != finalVisibility
            || SuppressionAtEnd.getValues() != finalSuppression
            || Position.getValue() != finalPosition || !hasTimelineOperationRole(liveOperation)
            || std::ranges::any_of(metadataPlans, [document, liveOperation](const MetadataPlan& plan) {
                   if (plan.isRoot) {
                       return false;
                   }
                   auto* resource = resolveExactTimelineIdentity(
                       document,
                       plan.object.objectId,
                       plan.object.objectName
                   );
                   return !resource || !hasTimelineResourceRole(resource)
                       || semanticOperationRoot(resource, document) != liveOperation;
               })) {
            throw Base::RuntimeError("The validated semantic publication changed while it was applied");
        }
    }
    catch (...) {
        const auto failure = std::current_exception();
        try {
            std::vector<DocumentObject*> restoredOperations;
            boost::dynamic_bitset<> restoredVisibility;
            boost::dynamic_bitset<> restoredSuppression;
            long restoredPosition = 0;
            restoredOperations.reserve(currentOperationIdentities.size());
            for (std::size_t index = 0; index < currentOperationIdentities.size(); ++index) {
                const auto& identity = currentOperationIdentities[index];
                auto* restored
                    = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
                if (!restored) {
                    continue;
                }
                if (static_cast<long>(index) < currentPosition) {
                    ++restoredPosition;
                }
                restoredOperations.push_back(restored);
                restoredVisibility.resize(restoredOperations.size());
                restoredSuppression.resize(restoredOperations.size());
                restoredVisibility.set(restoredOperations.size() - 1, currentVisibility.test(index));
                restoredSuppression.set(restoredOperations.size() - 1, currentSuppression.test(index));
            }
            Operations.setValues(restoredOperations);
            VisibilityAtEnd.setValues(restoredVisibility);
            SuppressionAtEnd.setValues(restoredSuppression);
            Position.setValue(restoredPosition);
            SchemaVersion.setValue(originalSchemaVersion);
        }
        catch (...) {
            // Continue restoring independently addressable metadata. The
            // original callback failure remains the deterministic result.
        }

        for (auto plan = metadataPlans.rbegin(); plan != metadataPlans.rend(); ++plan) {
            try {
                auto* candidate = resolveExactTimelineIdentity(
                    document,
                    plan->object.objectId,
                    plan->object.objectName
                );
                if (!candidate) {
                    throw Base::RuntimeError("The published identity no longer exists");
                }
                if (!plan->ownerExists) {
                    if (auto* owner = localTimelineMetadataProperty(candidate, OwnerPropertyName)) {
                        owner->setStatus(Property::LockDynamic, false);
                        candidate = resolveExactTimelineIdentity(
                            document,
                            plan->object.objectId,
                            plan->object.objectName
                        );
                        if (candidate) {
                            candidate->removeDynamicProperty(OwnerPropertyName);
                        }
                    }
                }
                else if (
                    auto* owner = dynamic_cast<PropertyLinkHidden*>(
                        localTimelineMetadataProperty(candidate, OwnerPropertyName)
                    )
                ) {
                    auto* originalOwner = resolveExactTimelineIdentity(
                        document,
                        plan->originalOwner.objectId,
                        plan->originalOwner.objectName
                    );
                    owner->setValue(originalOwner);
                }
            }
            catch (...) {
            }
            try {
                auto* candidate = resolveExactTimelineIdentity(
                    document,
                    plan->object.objectId,
                    plan->object.objectName
                );
                if (!candidate) {
                    throw Base::RuntimeError("The published identity no longer exists");
                }
                if (!plan->roleExists) {
                    if (auto* role = localTimelineMetadataProperty(candidate, RolePropertyName)) {
                        role->setStatus(Property::LockDynamic, false);
                        candidate = resolveExactTimelineIdentity(
                            document,
                            plan->object.objectId,
                            plan->object.objectName
                        );
                        if (candidate) {
                            candidate->removeDynamicProperty(RolePropertyName);
                        }
                    }
                }
                else if (
                    auto* role = dynamic_cast<PropertyString*>(
                        localTimelineMetadataProperty(candidate, RolePropertyName)
                    )
                ) {
                    role->setValue(plan->originalRole);
                }
            }
            catch (...) {
            }
        }
        std::rethrow_exception(failure);
    }

    std::erase_if(
        _provisionalEnrollments,
        [transactionId, &blockSet](const ProvisionalEnrollment& enrollment) {
            if (enrollment.transactionId != transactionId) {
                return false;
            }
            return std::ranges::any_of(blockSet, [&enrollment](const DocumentObject* candidate) {
                return candidate && enrollment.objectId == candidate->getID()
                    && enrollment.objectName == candidate->getNameInDocument();
            });
        }
    );
    std::erase_if(provenance->objects, [&blockSet](const TimelineObjectIdentity& identity) {
        return std::ranges::any_of(blockSet, [&identity](const DocumentObject* candidate) {
            return candidate && identity.objectId == candidate->getID()
                && identity.objectName == candidate->getNameInDocument();
        });
    });
    std::vector<DocumentObject*> publishedBaseline;
    publishedBaseline.reserve(baselineOperations.size() + block.size());
    publishedBaseline.insert(
        publishedBaseline.end(),
        baselineOperations.begin(),
        baselineOperations.begin() + insertion
    );
    publishedBaseline.insert(publishedBaseline.end(), block.begin(), block.end());
    publishedBaseline.insert(
        publishedBaseline.end(),
        baselineOperations.begin() + insertion,
        baselineOperations.end()
    );

    const std::unordered_map<DocumentObject*, std::size_t> publishedIndices(
        finalIndices.begin(),
        finalIndices.end()
    );
    provenance->operations.clear();
    provenance->operations.reserve(publishedBaseline.size());
    for (auto* published : publishedBaseline) {
        const auto index = publishedIndices.at(published);
        provenance->operations.push_back(
            CreationSnapshotOperation {
                .object = {
                    .objectId = published->getID(),
                    .objectName = published->getNameInDocument(),
                },
                .visibility = finalVisibility.test(index),
                .suppression = finalSuppression.test(index),
            }
        );
    }
    provenance->position += static_cast<long>(block.size());

    for (auto& enrollment : _provisionalEnrollments) {
        if (enrollment.transactionId != transactionId) {
            continue;
        }
        auto* candidate
            = resolveExactTimelineIdentity(document, enrollment.objectId, enrollment.objectName);
        const auto found = candidate ? finalIndices.find(candidate) : finalIndices.end();
        if (found != finalIndices.end()) {
            enrollment.insertionMarker = static_cast<long>(found->second);
        }
    }
    _provisionalPublications.push_back(std::move(publication));
}

void DocumentTimeline::adoptExistingOperationBlock(
    DocumentObject* operation,
    const std::vector<DocumentObject*>& orderedResources,
    const std::vector<DocumentObject*>& resourceOwners
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Adopting an existing semantic block requires one normal document "
            "and one caller-owned transaction"
        );
    }
    if (!operation || orderedResources.empty() || !document->containsObject(operation)
        || operation->getDocument() != document || !operation->getNameInDocument()) {
        throw Base::ValueError(
            "Existing block adoption requires one live operation and an explicit "
            "non-empty resource graph"
        );
    }
    if (!resourceOwners.empty() && resourceOwners.size() != orderedResources.size()) {
        throw Base::ValueError("Explicit adopted resource owners must exactly parallel resources");
    }

    pruneProvisionalEnrollments();
    pruneProvisionalTransactionCreations();
    pruneStagedResourceAdoptions();
    pruneProvisionalInternalObjects();
    pruneStagedSegmentReplacement();
    pruneStagedResourceReconciliation();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The existing-block adoption transaction is no longer active");
    }
    if (!_provisionalEnrollments.empty() || !_provisionalTransactionCreations.empty()
        || !_stagedResourceAdoptions.empty() || !_provisionalInternalObjects.empty()
        || !_stagedSegmentReplacements.empty() || !_stagedResourceReconciliations.empty()) {
        throw Base::RuntimeError(
            "Existing block adoption cannot overlap another staged or "
            "provisional timeline change"
        );
    }

    const auto currentOperations = Operations.getValues();
    const auto currentVisibility = VisibilityAtEnd.getValues();
    const auto currentSuppression = SuppressionAtEnd.getValues();
    const long currentPosition = Position.getValue();
    if (currentOperations.empty() || currentVisibility.size() != currentOperations.size()
        || currentSuppression.size() != currentOperations.size() || currentPosition < 0
        || currentPosition > static_cast<long>(currentOperations.size())) {
        throw Base::RuntimeError("The timeline state is inconsistent");
    }

    std::unordered_map<DocumentObject*, std::size_t> currentIndices;
    currentIndices.reserve(currentOperations.size());
    std::vector<TimelineObjectIdentity> currentIdentities;
    currentIdentities.reserve(currentOperations.size());
    for (std::size_t index = 0; index < currentOperations.size(); ++index) {
        auto* candidate = currentOperations[index];
        if (!candidate || !document->containsObject(candidate)
            || candidate->getDocument() != document || !candidate->getNameInDocument()
            || !isOperationCandidate(candidate) || !currentIndices.emplace(candidate, index).second
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The timeline contains a missing, duplicate, cross-document, "
                "or malformed operation"
            );
        }
        currentIdentities.push_back(
            TimelineObjectIdentity {
                .objectId = candidate->getID(),
                .objectName = candidate->getNameInDocument(),
            }
        );
    }

    std::vector<DocumentObject*> block = orderedResources;
    block.push_back(operation);
    std::unordered_set<DocumentObject*> blockSet;
    blockSet.reserve(block.size());
    std::size_t segmentBegin = currentOperations.size();
    std::size_t segmentEnd = 0;
    for (auto* candidate : block) {
        const auto found = currentIndices.find(candidate);
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !candidate->getNameInDocument() || found == currentIndices.end()
            || !blockSet.insert(candidate).second || isCreatedByCurrentTransaction(candidate)
            || isProvisionallyEnrolledByCurrentTransaction(candidate)) {
            throw Base::ValueError(
                "Every adopted block identity must be distinct, live, tracked, "
                "and predate this transaction"
            );
        }
        segmentBegin = std::min(segmentBegin, found->second);
        segmentEnd = std::max(segmentEnd, found->second + 1);
    }
    if (segmentEnd - segmentBegin != block.size()) {
        throw Base::RuntimeError(
            "Adopted identities must exactly occupy one contiguous History "
            "segment"
        );
    }
    if (static_cast<long>(segmentBegin) < currentPosition
        && currentPosition < static_cast<long>(segmentEnd)) {
        throw Base::RuntimeError("The History marker cannot split an adopted semantic block");
    }

    std::unordered_set<DocumentObject*> resourceSet(orderedResources.begin(), orderedResources.end());
    std::unordered_map<DocumentObject*, DocumentObject*> declaredOwners;
    declaredOwners.reserve(orderedResources.size());
    for (std::size_t index = 0; index < orderedResources.size(); ++index) {
        auto* resource = orderedResources[index];
        auto* declaredOwner = resourceOwners.empty() ? operation : resourceOwners[index];
        if (!declaredOwner || declaredOwner == resource
            || (declaredOwner != operation && !resourceSet.contains(declaredOwner))) {
            throw Base::ValueError(
                "Every adopted resource owner must be the operation or one "
                "resource in the same block"
            );
        }
        declaredOwners.emplace(resource, declaredOwner);
    }

    const auto declaredOwnerChainContains =
        [&declaredOwners, operation](const DocumentObject* object, const DocumentObject* ancestor) {
            std::unordered_set<const DocumentObject*> visited;
            auto* current = object;
            while (current && current != operation) {
                const auto found = declaredOwners.find(const_cast<DocumentObject*>(current));
                if (found == declaredOwners.end() || !visited.insert(current).second) {
                    return false;
                }
                current = found->second;
                if (current == ancestor) {
                    return true;
                }
            }
            return false;
        };
    for (auto* resource : orderedResources) {
        std::unordered_set<DocumentObject*> visited;
        auto* current = resource;
        while (current != operation) {
            if (!visited.insert(current).second) {
                throw Base::RuntimeError("The adopted ownership graph is cyclic");
            }
            const auto found = declaredOwners.find(current);
            if (found == declaredOwners.end()) {
                throw Base::RuntimeError("An adopted resource does not resolve to the operation");
            }
            current = found->second;
        }
    }
    validateCanonicalNestedResourceOrder(
        orderedResources,
        declaredOwnerChainContains,
        "Adopted resources must use canonical nested resource-first, owner-last order"
    );

    struct MetadataPlan
    {
        TimelineObjectIdentity object;
        TimelineObjectIdentity declaredOwner;
        bool isRoot {false};
        bool roleExists {false};
        bool ownerExists {false};
        std::string originalRole;
    };
    std::vector<MetadataPlan> metadataPlans;
    metadataPlans.reserve(block.size());
    for (auto* candidate : block) {
        const bool isRoot = candidate == operation;
        const auto* roleProperty = localTimelineMetadataProperty(candidate, RolePropertyName);
        auto* role = dynamic_cast<PropertyString*>(
            localTimelineMetadataProperty(candidate, RolePropertyName)
        );
        if (roleProperty && !role) {
            throw Base::RuntimeError("An adopted role property has the wrong type");
        }
        if (role) {
            validateCanonicalTimelineMetadataStatus(
                role,
                "Existing adopted role metadata is not hidden, locked, and non-recomputing"
            );
            const std::string_view roleValue(role->getValue());
            if (!roleValue.empty() && roleValue != OperationRole) {
                throw Base::RuntimeError("An adopted identity is not an independent operation");
            }
        }

        const auto* ownerProperty = localTimelineMetadataProperty(candidate, OwnerPropertyName);
        auto* ownerLink = dynamic_cast<PropertyLinkHidden*>(
            localTimelineMetadataProperty(candidate, OwnerPropertyName)
        );
        if (ownerProperty && !ownerLink) {
            throw Base::RuntimeError("An adopted owner property has the wrong type");
        }
        if (ownerLink) {
            validateCanonicalTimelineMetadataStatus(
                ownerLink,
                "Existing adopted owner metadata is not hidden, locked, and non-recomputing"
            );
            if (ownerLink->getValue()) {
                throw Base::RuntimeError("An adopted identity already has a semantic owner");
            }
        }

        const auto* editorProperty = localTimelineMetadataProperty(candidate, EditorPropertyName);
        const auto* editor = dynamic_cast<const PropertyLinkHidden*>(editorProperty);
        if (editorProperty && !editor) {
            throw Base::RuntimeError("Adopted editor metadata has the wrong property type");
        }
        if (editor) {
            validateCanonicalTimelineMetadataStatus(
                editor,
                "Adopted editor metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (editor && editor->getValue()
            && (!resourceSet.contains(editor->getValue())
                || !declaredOwnerChainContains(editor->getValue(), candidate))) {
            throw Base::RuntimeError("An adopted editor is not a declared owned descendant");
        }

        const auto* editCommandProperty
            = localTimelineMetadataProperty(candidate, EditCommandPropertyName);
        const auto* editCommand = dynamic_cast<const PropertyString*>(editCommandProperty);
        if (editCommandProperty && !editCommand) {
            throw Base::RuntimeError("Adopted edit-command metadata has the wrong property type");
        }
        if (editCommand) {
            validateCanonicalTimelineMetadataStatus(
                editCommand,
                "Adopted edit-command metadata is not hidden, locked, and non-recomputing"
            );
        }

        const auto* deleteCommandProperty =
            localTimelineMetadataProperty(candidate, DeleteCommandPropertyName);
        const auto* deleteCommand =
            dynamic_cast<const PropertyString*>(deleteCommandProperty);
        if (deleteCommandProperty && !deleteCommand) {
            throw Base::RuntimeError(
                "Adopted delete-command metadata has the wrong property type"
            );
        }
        if (deleteCommand) {
            validateCanonicalTimelineMetadataStatus(
                deleteCommand,
                "Adopted delete-command metadata is not hidden, locked, and non-recomputing"
            );
        }

        const auto* replacementProperty
            = localTimelineMetadataProperty(candidate, ReplacedInputsPropertyName);
        const auto* replacements = dynamic_cast<const PropertyLinkListHidden*>(replacementProperty);
        if (replacementProperty && !replacements) {
            throw Base::RuntimeError("Adopted replacement metadata has the wrong property type");
        }
        if (replacements) {
            validateCanonicalTimelineMetadataStatus(
                replacements,
                "Adopted replacement metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (!isRoot && replacementProperty) {
            throw Base::RuntimeError("Only the adopted operation may declare replacement inputs");
        }
        if (replacements) {
            std::unordered_set<DocumentObject*> directInputs;
            for (auto* input : replacements->getValues()) {
                if (!input || input == operation || !document->containsObject(input)
                    || input->getDocument() != document || blockSet.contains(input)
                    || !directInputs.insert(input).second || !hasValidTimelineOwnerChain(input)
                    || !replacementInputContract(input).valid) {
                    throw Base::RuntimeError(
                        "Adopted replacement inputs are malformed, duplicate, "
                        "or part of the adopted block"
                    );
                }
            }
        }

        for (auto* possibleResource : document->getObjects()) {
            if (possibleResource != candidate && hasTimelineResourceRole(possibleResource)
                && semanticOperationRoot(possibleResource, document) == candidate) {
                throw Base::RuntimeError("An adopted identity already owns a semantic resource");
            }
        }

        auto* declaredOwner = isRoot ? nullptr : declaredOwners.at(candidate);
        metadataPlans.push_back(MetadataPlan {
            .object = {
                .objectId = candidate->getID(),
                .objectName = candidate->getNameInDocument(),
            },
            .declaredOwner = declaredOwner
                ? TimelineObjectIdentity {
                      .objectId = declaredOwner->getID(),
                      .objectName = declaredOwner->getNameInDocument(),
                  }
                : TimelineObjectIdentity {},
            .isRoot = isRoot,
            .roleExists = role != nullptr,
            .ownerExists = ownerLink != nullptr,
            .originalRole = role ? role->getValue() : "",
        });
    }

    std::vector<DocumentObject*> finalOperations;
    finalOperations.reserve(currentOperations.size());
    finalOperations.insert(
        finalOperations.end(),
        currentOperations.begin(),
        currentOperations.begin() + static_cast<std::ptrdiff_t>(segmentBegin)
    );
    finalOperations.insert(finalOperations.end(), block.begin(), block.end());
    finalOperations.insert(
        finalOperations.end(),
        currentOperations.begin() + static_cast<std::ptrdiff_t>(segmentEnd),
        currentOperations.end()
    );
    boost::dynamic_bitset<> finalVisibility(finalOperations.size());
    boost::dynamic_bitset<> finalSuppression(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        const auto original = currentIndices.at(finalOperations[index]);
        finalVisibility.set(index, currentVisibility.test(original));
        finalSuppression.set(index, currentSuppression.test(original));
    }

    const auto declaredSemanticRoot =
        [document, operation, &blockSet](const DocumentObject* candidate) -> const DocumentObject* {
        if (blockSet.contains(const_cast<DocumentObject*>(candidate))) {
            return operation;
        }
        return semanticOperationRoot(candidate, document);
    };
    struct FinalBlock
    {
        std::size_t begin {std::numeric_limits<std::size_t>::max()};
        std::size_t end {0};
        std::size_t count {0};
        std::size_t rootIndex {std::numeric_limits<std::size_t>::max()};
    };
    std::unordered_map<const DocumentObject*, FinalBlock> finalBlocks;
    std::unordered_map<DocumentObject*, std::size_t> finalIndices;
    finalBlocks.reserve(finalOperations.size());
    finalIndices.reserve(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        auto* candidate = finalOperations[index];
        if (!finalIndices.emplace(candidate, index).second) {
            throw Base::RuntimeError("The adopted final timeline contains a duplicate identity");
        }
        const auto* root = declaredSemanticRoot(candidate);
        if (!root || (root != operation && hasTimelineResourceRole(root))) {
            throw Base::RuntimeError("The adopted final timeline has an incomplete ownership graph");
        }
        auto& finalBlock = finalBlocks[root];
        finalBlock.begin = std::min(finalBlock.begin, index);
        finalBlock.end = std::max(finalBlock.end, index + 1);
        ++finalBlock.count;
        if (candidate == root) {
            finalBlock.rootIndex = index;
        }
    }

    std::vector<std::pair<std::size_t, const DocumentObject*>> rootsInOrder;
    rootsInOrder.reserve(finalBlocks.size());
    for (const auto& [root, finalBlock] : finalBlocks) {
        if (!finalIndices.contains(const_cast<DocumentObject*>(root))
            || finalBlock.rootIndex == std::numeric_limits<std::size_t>::max()
            || finalBlock.rootIndex + 1 != finalBlock.end
            || finalBlock.end - finalBlock.begin != finalBlock.count
            || (static_cast<long>(finalBlock.begin) < currentPosition
                && currentPosition < static_cast<long>(finalBlock.end))) {
            throw Base::RuntimeError(
                "The adopted final timeline is not canonical at one block or "
                "History boundary"
            );
        }
        rootsInOrder.emplace_back(finalBlock.begin, root);
    }
    std::ranges::sort(rootsInOrder);
    std::unordered_map<const DocumentObject*, std::size_t> rootOrder;
    rootOrder.reserve(rootsInOrder.size());
    for (std::size_t index = 0; index < rootsInOrder.size(); ++index) {
        rootOrder.emplace(rootsInOrder[index].second, index);
    }

    for (const auto* candidate : finalOperations) {
        const auto* candidateRoot = declaredSemanticRoot(candidate);
        const auto candidateOrder = rootOrder.find(candidateRoot);
        if (candidateOrder == rootOrder.end()) {
            throw Base::RuntimeError("An adopted identity has no semantic History position");
        }
        if (candidate == operation) {
            const auto* replacements = dynamic_cast<const PropertyLinkListHidden*>(
                localTimelineMetadataProperty(candidate, ReplacedInputsPropertyName)
            );
            if (replacements) {
                for (const auto* input : replacements->getValues()) {
                    const auto* inputRoot = declaredSemanticRoot(input);
                    const auto inputOrder = rootOrder.find(inputRoot);
                    if (!inputRoot || inputRoot == candidateRoot || inputOrder == rootOrder.end()
                        || inputOrder->second >= candidateOrder->second) {
                        throw Base::RuntimeError(
                            "An adopted replacement input is not an earlier "
                            "semantic operation"
                        );
                    }
                }
            }
        }

        std::vector<const DocumentObject*> pending {candidate};
        std::unordered_set<const DocumentObject*> visited {candidate};
        while (!pending.empty()) {
            const auto* current = pending.back();
            pending.pop_back();
            for (const auto* dependency : current->getOutList()) {
                if (!dependency || !document->containsObject(dependency)
                    || dependency->getDocument() != document
                    || isStructuralTimelineLink(current, dependency)
                    || !visited.insert(dependency).second) {
                    continue;
                }
                const auto* dependencyRoot = declaredSemanticRoot(dependency);
                if (!dependencyRoot) {
                    throw Base::RuntimeError("Adopted History encountered a malformed dependency");
                }
                const auto dependencyOrder = rootOrder.find(dependencyRoot);
                if (dependencyRoot != candidateRoot && dependencyOrder != rootOrder.end()
                    && dependencyOrder->second > candidateOrder->second) {
                    throw Base::RuntimeError(
                        "Adopting the block would place a dependency after "
                        "its consumer"
                    );
                }
                pending.push_back(dependency);
            }
        }
    }

    std::vector<TimelineObjectIdentity> finalOperationIdentities;
    finalOperationIdentities.reserve(finalOperations.size());
    for (auto* candidate : finalOperations) {
        finalOperationIdentities.push_back(
            TimelineObjectIdentity {
                .objectId = candidate->getID(),
                .objectName = candidate->getNameInDocument(),
            }
        );
    }
    const long originalSchemaVersion = SchemaVersion.getValue();
    ApplyingScope applying(*this);
    try {
        for (const auto& plan : metadataPlans) {
            auto* candidate
                = resolveExactTimelineIdentity(document, plan.object.objectId, plan.object.objectName);
            if (!candidate) {
                throw Base::RuntimeError("An adopted identity was removed by a metadata callback");
            }

            auto* role = dynamic_cast<PropertyString*>(
                localTimelineMetadataProperty(candidate, RolePropertyName)
            );
            if (!role) {
                candidate->addDynamicProperty(
                    "App::PropertyString",
                    RolePropertyName,
                    "Timeline",
                    "Document timeline classification",
                    Prop_NoRecompute,
                    false,
                    true
                );
                candidate = resolveExactTimelineIdentity(
                    document,
                    plan.object.objectId,
                    plan.object.objectName
                );
                role = candidate ? dynamic_cast<PropertyString*>(
                                       localTimelineMetadataProperty(candidate, RolePropertyName)
                                   )
                                 : nullptr;
            }
            if (!candidate || !role) {
                throw Base::TypeError("The adopted role property could not be created");
            }

            if (!plan.isRoot) {
                auto* owner = dynamic_cast<PropertyLinkHidden*>(
                    localTimelineMetadataProperty(candidate, OwnerPropertyName)
                );
                if (!owner) {
                    candidate->addDynamicProperty(
                        "App::PropertyLinkHidden",
                        OwnerPropertyName,
                        "Timeline",
                        "Owning semantic timeline operation",
                        Prop_NoRecompute,
                        false,
                        true
                    );
                    candidate = resolveExactTimelineIdentity(
                        document,
                        plan.object.objectId,
                        plan.object.objectName
                    );
                    owner = candidate
                        ? dynamic_cast<PropertyLinkHidden*>(
                              localTimelineMetadataProperty(candidate, OwnerPropertyName)
                          )
                        : nullptr;
                }
                auto* declaredOwner = resolveExactTimelineIdentity(
                    document,
                    plan.declaredOwner.objectId,
                    plan.declaredOwner.objectName
                );
                if (!candidate || !owner || !declaredOwner) {
                    throw Base::TypeError("The adopted owner property could not be created");
                }
                owner->setValue(declaredOwner);
                candidate = resolveExactTimelineIdentity(
                    document,
                    plan.object.objectId,
                    plan.object.objectName
                );
                role = candidate ? dynamic_cast<PropertyString*>(
                                       localTimelineMetadataProperty(candidate, RolePropertyName)
                                   )
                                 : nullptr;
                if (!candidate || !role) {
                    throw Base::RuntimeError("An adopted identity changed during ownership setup");
                }
            }

            role->setValue(plan.isRoot ? OperationRole : ResourceRole);
            if (!resolveExactTimelineIdentity(document, plan.object.objectId, plan.object.objectName)) {
                throw Base::RuntimeError("An adopted identity was removed by a role callback");
            }
        }

        std::vector<DocumentObject*> resolvedFinalOperations;
        resolvedFinalOperations.reserve(finalOperationIdentities.size());
        for (const auto& identity : finalOperationIdentities) {
            auto* candidate
                = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
            if (!candidate) {
                throw Base::RuntimeError("A timeline identity changed during adoption callbacks");
            }
            resolvedFinalOperations.push_back(candidate);
        }
        Operations.setValues(resolvedFinalOperations);
        VisibilityAtEnd.setValues(finalVisibility);
        SuppressionAtEnd.setValues(finalSuppression);
        Position.setValue(currentPosition);
        SchemaVersion.setValue(CurrentSchemaVersion);

        const auto setMetadataStatus = [document](
                                           const TimelineObjectIdentity& identity,
                                           const char* propertyName,
                                           const Property::Status status,
                                           const char* error
                                       ) {
            auto* candidate
                = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
            auto* property = candidate ? localTimelineMetadataProperty(candidate, propertyName)
                                       : nullptr;
            if (!candidate || !property) {
                throw Base::RuntimeError(error);
            }
            property->setStatus(status, true);
            candidate = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
            property = candidate ? localTimelineMetadataProperty(candidate, propertyName) : nullptr;
            if (!candidate || !property || !property->testStatus(status)) {
                throw Base::RuntimeError(error);
            }
        };
        for (const auto& plan : metadataPlans) {
            if (!plan.roleExists) {
                setMetadataStatus(
                    plan.object,
                    RolePropertyName,
                    Property::Hidden,
                    "A newly adopted role property changed while it was hidden"
                );
                setMetadataStatus(
                    plan.object,
                    RolePropertyName,
                    Property::NoRecompute,
                    "A newly adopted role property changed while recompute was disabled"
                );
                setMetadataStatus(
                    plan.object,
                    RolePropertyName,
                    Property::LockDynamic,
                    "A newly adopted role property changed while it was locked"
                );
            }
            if (!plan.isRoot && !plan.ownerExists) {
                setMetadataStatus(
                    plan.object,
                    OwnerPropertyName,
                    Property::Hidden,
                    "A newly adopted owner property changed while it was hidden"
                );
                setMetadataStatus(
                    plan.object,
                    OwnerPropertyName,
                    Property::NoRecompute,
                    "A newly adopted owner property changed while recompute was disabled"
                );
                setMetadataStatus(
                    plan.object,
                    OwnerPropertyName,
                    Property::LockDynamic,
                    "A newly adopted owner property changed while it was locked"
                );
            }
        }

        for (std::size_t index = 0; index < finalOperationIdentities.size(); ++index) {
            const auto& identity = finalOperationIdentities[index];
            if (resolveExactTimelineIdentity(document, identity.objectId, identity.objectName)
                != resolvedFinalOperations[index]) {
                throw Base::RuntimeError("A final timeline identity changed after semantic adoption");
            }
        }
        const auto& operationIdentity = metadataPlans.back().object;
        auto* liveOperation = resolveExactTimelineIdentity(
            document,
            operationIdentity.objectId,
            operationIdentity.objectName
        );
        if (!liveOperation || Operations.getValues() != resolvedFinalOperations
            || VisibilityAtEnd.getValues() != finalVisibility
            || SuppressionAtEnd.getValues() != finalSuppression
            || Position.getValue() != currentPosition || !hasTimelineOperationRole(liveOperation)
            || std::ranges::any_of(metadataPlans, [document, liveOperation](const MetadataPlan& plan) {
                   if (plan.isRoot) {
                       return false;
                   }
                   auto* resource = resolveExactTimelineIdentity(
                       document,
                       plan.object.objectId,
                       plan.object.objectName
                   );
                   return !resource || !hasTimelineResourceRole(resource)
                       || semanticOperationRoot(resource, document) != liveOperation;
               })) {
            throw Base::RuntimeError("The validated semantic adoption changed while it was applied");
        }
    }
    catch (...) {
        const auto failure = std::current_exception();
        try {
            std::vector<DocumentObject*> restoredOperations;
            restoredOperations.reserve(currentIdentities.size());
            for (const auto& identity : currentIdentities) {
                auto* restored
                    = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
                if (restored) {
                    restoredOperations.push_back(restored);
                }
            }
            if (restoredOperations.size() == currentIdentities.size()) {
                Operations.setValues(restoredOperations);
                VisibilityAtEnd.setValues(currentVisibility);
                SuppressionAtEnd.setValues(currentSuppression);
                Position.setValue(currentPosition);
            }
            SchemaVersion.setValue(originalSchemaVersion);
        }
        catch (...) {
        }

        for (auto plan = metadataPlans.rbegin(); plan != metadataPlans.rend(); ++plan) {
            try {
                auto* candidate = resolveExactTimelineIdentity(
                    document,
                    plan->object.objectId,
                    plan->object.objectName
                );
                if (!candidate) {
                    throw Base::RuntimeError("The adopted identity no longer exists");
                }
                if (!plan->ownerExists) {
                    if (auto* owner = localTimelineMetadataProperty(candidate, OwnerPropertyName)) {
                        owner->setStatus(Property::LockDynamic, false);
                        candidate = resolveExactTimelineIdentity(
                            document,
                            plan->object.objectId,
                            plan->object.objectName
                        );
                        if (candidate) {
                            candidate->removeDynamicProperty(OwnerPropertyName);
                        }
                    }
                }
                else if (
                    auto* owner = dynamic_cast<PropertyLinkHidden*>(
                        localTimelineMetadataProperty(candidate, OwnerPropertyName)
                    )
                ) {
                    owner->setValue(nullptr);
                }
            }
            catch (...) {
            }
            try {
                auto* candidate = resolveExactTimelineIdentity(
                    document,
                    plan->object.objectId,
                    plan->object.objectName
                );
                if (!candidate) {
                    throw Base::RuntimeError("The adopted identity no longer exists");
                }
                if (!plan->roleExists) {
                    if (auto* role = localTimelineMetadataProperty(candidate, RolePropertyName)) {
                        role->setStatus(Property::LockDynamic, false);
                        candidate = resolveExactTimelineIdentity(
                            document,
                            plan->object.objectId,
                            plan->object.objectName
                        );
                        if (candidate) {
                            candidate->removeDynamicProperty(RolePropertyName);
                        }
                    }
                }
                else if (
                    auto* role = dynamic_cast<PropertyString*>(
                        localTimelineMetadataProperty(candidate, RolePropertyName)
                    )
                ) {
                    role->setValue(plan->originalRole);
                }
            }
            catch (...) {
            }
        }
        std::rethrow_exception(failure);
    }
}

void DocumentTimeline::forgetOperation(DocumentObject* operation)
{
    auto* document = getDocument();
    if (!operation || !document || isApplying() || document->isPerformingTransaction()
        || operation->getDocument() != document) {
        return;
    }

    auto operations = Operations.getValues();
    const auto newEnd = std::remove_if(
        operations.begin(),
        operations.end(),
        [document, operation](const DocumentObject* candidate) {
            // containsObject() compares addresses without dereferencing the
            // candidate.  A legacy document or an interrupted rollback may
            // leave an already-detached address in a restored link list; do
            // not pass that address back through PropertyLinkList validation.
            return candidate == operation || !document->containsObject(candidate);
        }
    );
    if (newEnd == operations.end()) {
        return;
    }
    operations.erase(newEnd, operations.end());
    if (operation) {
        const long operationId = operation->getID();
        const std::string operationName = operation->getNameInDocument();
        std::erase_if(
            _provisionalEnrollments,
            [operationId, &operationName](const ProvisionalEnrollment& enrollment) {
                return enrollment.objectId == operationId && enrollment.objectName == operationName;
            }
        );
    }

    // Leave reconciliation enabled. onBeforeChange() snapshots the parallel
    // arrays while every linked object is still valid, and onChanged() then
    // remaps the marker and accepted visibility/suppression state by identity.
    Operations.setValues(operations);
}

bool DocumentTimeline::reorderOperationDependentClosureAfter(
    DocumentObject* operation,
    DocumentObject* target
)
{
    auto* document = getDocument();
    const auto operations = Operations.getValues();
    if (!document || !operation || !target || operation == target || operations.empty()) {
        throw Base::ValueError(
            "Timeline dependency rebase requires one operation and one "
            "distinct target"
        );
    }

    std::unordered_map<const DocumentObject*, std::vector<DocumentObject*>> blocks;
    std::vector<DocumentObject*> orderedRoots;
    std::unordered_set<const DocumentObject*> seenRoots;
    blocks.reserve(operations.size());
    orderedRoots.reserve(operations.size());
    seenRoots.reserve(operations.size());
    for (auto* member : operations) {
        auto* root = const_cast<DocumentObject*>(semanticOperationRoot(member, document));
        if (!root) {
            throw Base::RuntimeError("The timeline contains a malformed semantic operation block");
        }
        blocks[root].push_back(member);
        if (seenRoots.insert(root).second) {
            orderedRoots.push_back(root);
        }
    }

    auto* operationRoot = const_cast<DocumentObject*>(semanticOperationRoot(operation, document));
    auto* targetRoot = const_cast<DocumentObject*>(semanticOperationRoot(target, document));
    if (!operationRoot || !targetRoot || operationRoot == targetRoot
        || !blocks.contains(operationRoot) || !blocks.contains(targetRoot)) {
        throw Base::ValueError(
            "Timeline dependency rebase inputs must identify two distinct "
            "tracked semantic operations"
        );
    }

    std::unordered_set<const DocumentObject*> movingRoots {operationRoot};
    const auto blockDependsOnMovingRoot = [&](const DocumentObject* candidateRoot) {
        std::vector<const DocumentObject*> pending;
        std::unordered_set<const DocumentObject*> visited;
        for (auto* member : blocks.at(candidateRoot)) {
            pending.push_back(member);
            visited.insert(member);
        }

        while (!pending.empty()) {
            const auto* current = pending.back();
            pending.pop_back();
            for (const auto* dependency : current->getOutList()) {
                if (!dependency || !document->containsObject(dependency)
                    || dependency->getDocument() != document
                    || isStructuralTimelineLink(current, dependency)
                    || !visited.insert(dependency).second) {
                    continue;
                }

                const auto* dependencyRoot = semanticOperationRoot(dependency, document);
                if (!dependencyRoot) {
                    throw Base::RuntimeError(
                        "Timeline dependency rebase encountered a "
                        "malformed dependency"
                    );
                }
                if (dependencyRoot != candidateRoot && movingRoots.contains(dependencyRoot)) {
                    return true;
                }
                pending.push_back(dependency);
            }
        }
        return false;
    };

    bool changed = true;
    while (changed) {
        changed = false;
        for (const auto* candidateRoot : orderedRoots) {
            if (movingRoots.contains(candidateRoot)) {
                continue;
            }
            if (blockDependsOnMovingRoot(candidateRoot)) {
                if (candidateRoot == targetRoot) {
                    throw Base::RuntimeError(
                        "The requested timeline dependency rebase would "
                        "create a dependency cycle"
                    );
                }
                movingRoots.insert(candidateRoot);
                changed = true;
            }
        }
    }

    std::vector<DocumentObject*> movingOperations;
    movingOperations.reserve(movingRoots.size());
    for (auto* root : orderedRoots) {
        if (movingRoots.contains(root)) {
            movingOperations.push_back(root);
        }
    }
    return reorderOperationBlocksAfter(movingOperations, targetRoot);
}

bool DocumentTimeline::reorderOperationBlocksAfter(
    const std::vector<DocumentObject*>& requestedOperations,
    DocumentObject* target
)
{
    return reorderOperationBlocks(requestedOperations, target, false);
}

bool DocumentTimeline::reorderOperationBlocksBefore(
    const std::vector<DocumentObject*>& requestedOperations,
    DocumentObject* target
)
{
    return reorderOperationBlocks(requestedOperations, target, true);
}

bool DocumentTimeline::reorderOperationBlocks(
    const std::vector<DocumentObject*>& requestedOperations,
    DocumentObject* target,
    const bool insertBefore
)
{
    auto* document = getDocument();
    if (!document || isApplying() || document->testStatus(Document::Restoring)
        || document->isPerformingTransaction()
        || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Timeline blocks can only be reordered inside one clean owned transaction"
        );
    }

    const auto operations = Operations.getValues();
    if (operations.empty() || requestedOperations.empty() || !target) {
        throw Base::ValueError("Timeline block reorder requires operations and one target");
    }
    if (Position.getValue() != static_cast<long>(operations.size())) {
        throw Base::RuntimeError(
            "Timeline blocks can only be reordered at the current end of history"
        );
    }
    if (VisibilityAtEnd.getSize() != static_cast<int>(operations.size())
        || SuppressionAtEnd.getSize() != static_cast<int>(operations.size())) {
        throw Base::RuntimeError("The timeline state arrays do not match its operation sequence");
    }

    std::unordered_map<const DocumentObject*, std::size_t> originalIndices;
    std::unordered_set<long> operationIds;
    originalIndices.reserve(operations.size());
    operationIds.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        const auto* operation = operations[index];
        if (!operation || !document->containsObject(operation) || operation->getDocument() != document
            || !originalIndices.emplace(operation, index).second
            || !operationIds.insert(operation->getID()).second
            || !hasValidTimelineOwnerChain(operation)) {
            throw Base::RuntimeError(
                "The timeline contains a missing, duplicate, cross-document, or malformed operation"
            );
        }
        const auto replacement = replacementInputContract(const_cast<DocumentObject*>(operation));
        if (!replacement.valid) {
            throw Base::RuntimeError("The timeline contains malformed replacement metadata");
        }
    }

    // Every explicitly internal object must participate in one complete,
    // acyclic semantic block. Otherwise moving the visible operation could
    // strand an implementation object at its old chronological position.
    for (const auto* object : document->getObjects()) {
        if (!hasTimelineResourceRole(object)) {
            continue;
        }
        const auto* root = semanticOperationRoot(object, document);
        if (!root || !originalIndices.contains(object) || !originalIndices.contains(root)) {
            throw Base::RuntimeError("The document contains an incomplete timeline ownership block");
        }
    }

    std::unordered_set<const DocumentObject*> requestedIdentities;
    std::unordered_set<long> requestedIds;
    std::unordered_set<const DocumentObject*> movingRoots;
    requestedIdentities.reserve(requestedOperations.size());
    requestedIds.reserve(requestedOperations.size());
    movingRoots.reserve(requestedOperations.size());
    for (const auto* requested : requestedOperations) {
        const auto* root = semanticOperationRoot(requested, document);
        if (!requested || !root || !originalIndices.contains(requested)
            || !originalIndices.contains(root) || !requestedIdentities.insert(requested).second
            || !requestedIds.insert(requested->getID()).second || !movingRoots.insert(root).second) {
            throw Base::ValueError(
                "Timeline reorder inputs contain a missing, duplicate, "
                "cross-document, or overlapping operation"
            );
        }
    }

    const auto* targetRoot = semanticOperationRoot(target, document);
    if (!targetRoot || !originalIndices.contains(target) || !originalIndices.contains(targetRoot)
        || movingRoots.contains(targetRoot)) {
        throw Base::ValueError("The timeline reorder target is missing or belongs to a moved block");
    }

    std::vector<DocumentObject*> movedBlock;
    std::vector<DocumentObject*> remaining;
    movedBlock.reserve(operations.size());
    remaining.reserve(operations.size());
    for (auto* operation : operations) {
        const auto* root = semanticOperationRoot(operation, document);
        if (!root || !originalIndices.contains(root)) {
            throw Base::RuntimeError(
                "The timeline ownership graph changed while planning its reorder"
            );
        }
        if (movingRoots.contains(root)) {
            movedBlock.push_back(operation);
        }
        else {
            remaining.push_back(operation);
        }
    }
    if (movedBlock.empty()) {
        throw Base::RuntimeError("No complete semantic block was found for the requested operations");
    }

    auto insertion = remaining.end();
    bool foundTarget = false;
    for (auto iterator = remaining.begin(); iterator != remaining.end(); ++iterator) {
        if (semanticOperationRoot(*iterator, document) == targetRoot) {
            foundTarget = true;
            insertion = insertBefore ? iterator : std::next(iterator);
            if (insertBefore) {
                break;
            }
        }
    }
    if (!foundTarget) {
        throw Base::RuntimeError("The target semantic block is incomplete in the timeline");
    }

    std::vector<DocumentObject*> reordered;
    reordered.reserve(operations.size());
    reordered.insert(reordered.end(), remaining.begin(), insertion);
    reordered.insert(reordered.end(), movedBlock.begin(), movedBlock.end());
    reordered.insert(reordered.end(), insertion, remaining.end());
    if (reordered == operations) {
        return false;
    }

    std::unordered_map<const DocumentObject*, std::size_t> rootIndices;
    rootIndices.reserve(reordered.size());
    for (std::size_t index = 0; index < reordered.size(); ++index) {
        const auto* root = semanticOperationRoot(reordered[index], document);
        if (!root) {
            throw Base::RuntimeError(
                "The timeline ownership graph changed while validating its reorder"
            );
        }
        rootIndices.try_emplace(root, index);
    }

    // Reject a chronology where an operation depends, directly or through an
    // internal helper, on a later semantic block. Links within one block and
    // structural Group membership do not express execution order and are
    // deliberately ignored at every level of the traversal.
    for (const auto* operation : reordered) {
        const auto* operationRoot = semanticOperationRoot(operation, document);
        const auto operationIndex = rootIndices.find(operationRoot);
        if (operationIndex == rootIndices.end()) {
            throw Base::RuntimeError("A reordered operation has no semantic timeline position");
        }

        std::vector<const DocumentObject*> pending {operation};
        std::unordered_set<const DocumentObject*> visited {operation};
        while (!pending.empty()) {
            const auto* current = pending.back();
            pending.pop_back();
            for (const auto* dependency : current->getOutList()) {
                if (!dependency || !document->containsObject(dependency)
                    || dependency->getDocument() != document
                    || isStructuralTimelineLink(current, dependency)
                    || !visited.insert(dependency).second) {
                    continue;
                }

                const auto* dependencyRoot = semanticOperationRoot(dependency, document);
                if (!dependencyRoot) {
                    throw Base::RuntimeError("Timeline reorder encountered a malformed dependency");
                }
                const auto dependencyIndex = rootIndices.find(dependencyRoot);
                if (dependencyRoot != operationRoot && dependencyIndex != rootIndices.end()
                    && dependencyIndex->second > operationIndex->second) {
                    throw Base::RuntimeError(
                        std::string("Timeline reorder would place dependency '")
                        + dependency->getNameInDocument() + "' after consumer '"
                        + current->getNameInDocument() + "'"
                    );
                }
                pending.push_back(dependency);
            }
        }
    }

    Operations.setValues(reordered);
    if (Operations.getValues() != reordered
        || Position.getValue() != static_cast<long>(reordered.size())
        || VisibilityAtEnd.getSize() != static_cast<int>(reordered.size())
        || SuppressionAtEnd.getSize() != static_cast<int>(reordered.size())) {
        throw Base::RuntimeError("The timeline changed while applying its validated block reorder");
    }
    return true;
}

std::vector<DocumentObject*> DocumentTimeline::semanticCopyClosure(
    const std::vector<DocumentObject*>& selectedObjects
) const
{
    const auto* document = getDocument();
    if (!document) {
        throw Base::RuntimeError("A semantic copy closure requires a live document timeline");
    }
    if (selectedObjects.empty()) {
        return {};
    }

    const auto& operations = Operations.getValues();
    std::unordered_map<const DocumentObject*, std::size_t> operationIndices;
    operationIndices.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        const auto* operation = operations[index];
        if (!operation || !document->containsObject(operation) || operation->getDocument() != document
            || !operationIndices.emplace(operation, index).second
            || !hasValidTimelineOwnerChain(operation)) {
            throw Base::RuntimeError(
                "The timeline contains a missing, duplicate, cross-document, or malformed operation"
            );
        }
    }

    std::unordered_map<const DocumentObject*, std::vector<DocumentObject*>> resourcesByRoot;
    for (auto* object : document->getObjects()) {
        if (!hasTimelineResourceRole(object)) {
            continue;
        }
        const auto* root = semanticOperationRoot(object, document);
        if (!root) {
            throw Base::RuntimeError("The document contains an orphaned or cyclic timeline resource");
        }
        resourcesByRoot[root].push_back(object);
    }

    const auto validateMetadata = [document](const DocumentObject* object) {
        const auto* roleProperty = localTimelineMetadataProperty(object, RolePropertyName);
        const auto* role = dynamic_cast<const PropertyString*>(roleProperty);
        if (roleProperty && !role) {
            throw Base::RuntimeError("Timeline role metadata has the wrong property type");
        }
        const std::string_view roleValue = role ? std::string_view(role->getValue())
                                                : std::string_view();
        if (role && roleValue != OperationRole && roleValue != ResourceRole) {
            throw Base::RuntimeError("Timeline role metadata has an unknown value");
        }

        const auto* ownerProperty = localTimelineMetadataProperty(object, OwnerPropertyName);
        const auto* owner = dynamic_cast<const PropertyLinkHidden*>(ownerProperty);
        if (ownerProperty && !owner) {
            throw Base::RuntimeError("Timeline owner metadata has the wrong property type");
        }
        if (roleValue == ResourceRole) {
            const auto* value = owner ? owner->getValue() : nullptr;
            if (!value || value == object || !document->containsObject(value)
                || value->getDocument() != document) {
                throw Base::RuntimeError("A timeline resource has no live same-document owner");
            }
        }
        else if (owner && owner->getValue()) {
            throw Base::RuntimeError("A timeline operation carries a stale owner link");
        }

        const auto* editorProperty = localTimelineMetadataProperty(object, EditorPropertyName);
        const auto* editor = dynamic_cast<const PropertyLinkHidden*>(editorProperty);
        if (editorProperty && !editor) {
            throw Base::RuntimeError("Timeline editor metadata has the wrong property type");
        }
        if (editor && editor->getValue()
            && DocumentTimeline::timelineEditor(object) != editor->getValue()) {
            throw Base::RuntimeError("A timeline operation carries a stale editor link");
        }

        const auto replacement = DocumentTimeline::replacementInputContract(
            const_cast<DocumentObject*>(object)
        );
        if (!replacement.valid) {
            throw Base::RuntimeError("Timeline replacement metadata is malformed");
        }
        return replacement;
    };

    std::vector<const DocumentObject*> pendingRoots;
    pendingRoots.reserve(selectedObjects.size());
    for (const auto* selected : selectedObjects) {
        const auto* root = semanticOperationRoot(selected, document);
        if (!selected || !root || !operationIndices.contains(selected)
            || !operationIndices.contains(root)) {
            throw Base::ValueError("A selected object is not part of this complete document timeline");
        }
        pendingRoots.push_back(root);
    }

    std::unordered_set<const DocumentObject*> includedRoots;
    std::unordered_set<const DocumentObject*> includedObjects;
    while (!pendingRoots.empty()) {
        const auto* root = pendingRoots.back();
        pendingRoots.pop_back();
        if (!includedRoots.insert(root).second) {
            continue;
        }
        if (!operationIndices.contains(root) || hasTimelineResourceRole(root)) {
            throw Base::RuntimeError("A semantic copy block has no tracked operation root");
        }

        const auto replacement = validateMetadata(root);
        includedObjects.insert(root);
        if (const auto resources = resourcesByRoot.find(root); resources != resourcesByRoot.end()) {
            for (auto* resource : resources->second) {
                if (!operationIndices.contains(resource)
                    || semanticOperationRoot(resource, document) != root) {
                    throw Base::RuntimeError("A semantic copy block contains an untracked resource");
                }
                validateMetadata(resource);
                includedObjects.insert(resource);
            }
        }

        for (const auto* input : replacement.inputs) {
            const auto* inputRoot = semanticOperationRoot(input, document);
            if (!inputRoot || !operationIndices.contains(input)
                || !operationIndices.contains(inputRoot)) {
                throw Base::RuntimeError(
                    "A replacement input is outside the complete document timeline"
                );
            }
            pendingRoots.push_back(inputRoot);
        }
    }

    std::vector<DocumentObject*> result;
    result.reserve(includedObjects.size());
    for (auto* operation : operations) {
        if (includedObjects.contains(operation)) {
            result.push_back(operation);
        }
    }
    if (result.size() != includedObjects.size()) {
        throw Base::RuntimeError("A semantic copy block is incomplete in the document timeline");
    }
    return result;
}

void DocumentTimeline::adoptImportedOperations(
    const std::vector<DocumentObject*>& importedObjects,
    const std::vector<DocumentObject*>& sourceOrder,
    const std::vector<bool>& sourceVisibility,
    const std::vector<bool>& sourceSuppression
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || document->getBookedTransactionID() == App::NullTransaction || isApplying()) {
        throw Base::RuntimeError(
            "Imported timeline operations require one normal document and "
            "one caller-owned transaction"
        );
    }
    if (importedObjects.empty()) {
        return;
    }
    pruneProvisionalEnrollments();
    pruneProvisionalTransactionCreations();
    pruneStagedResourceAdoptions();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The timeline import transaction is no longer active");
    }
    const bool hasExplicitSourceState = !sourceVisibility.empty() || !sourceSuppression.empty();
    if ((sourceVisibility.empty() != sourceSuppression.empty())
        || (hasExplicitSourceState
            && (sourceVisibility.size() != sourceOrder.size()
                || sourceSuppression.size() != sourceOrder.size()))) {
        throw Base::ValueError(
            "Imported source visibility and suppression must both match "
            "the explicit source chronology"
        );
    }

    std::unordered_set<DocumentObject*> importedSet;
    importedSet.reserve(importedObjects.size());
    std::vector<TimelineObjectIdentity> importedIdentities;
    importedIdentities.reserve(importedObjects.size());
    std::vector<DocumentTimeline*> importedTimelines;
    for (auto* object : importedObjects) {
        if (!object || !document->containsObject(object) || object->getDocument() != document
            || !importedSet.insert(object).second) {
            throw Base::ValueError(
                "Imported timeline identities contain a missing, duplicate, "
                "or cross-document object"
            );
        }
        if (object == this) {
            throw Base::RuntimeError(
                "The target timeline must exist before importing a source "
                "timeline"
            );
        }
        if (object != this && object->isDerivedFrom<DocumentTimeline>()) {
            importedTimelines.push_back(static_cast<DocumentTimeline*>(object));
        }
        else {
            importedIdentities.push_back(
                TimelineObjectIdentity {
                    .objectId = object->getID(),
                    .objectName = object->getNameInDocument(),
                }
            );
        }
    }

    const auto storedOperations = Operations.getValues();
    const auto storedVisibility = VisibilityAtEnd.getValues();
    const auto storedSuppression = SuppressionAtEnd.getValues();
    const auto storedPosition = Position.getValue();
    if (storedVisibility.size() != storedOperations.size()
        || storedSuppression.size() != storedOperations.size() || storedPosition < 0
        || storedPosition > static_cast<long>(storedOperations.size())) {
        throw Base::RuntimeError("The target timeline state is inconsistent");
    }

    std::unordered_map<DocumentObject*, long> provisionalMarkers;
    provisionalMarkers.reserve(_provisionalEnrollments.size());
    DocumentObject* firstProvisionalOverlap = nullptr;
    long firstProvisionalMarker = 0;
    for (const auto& enrollment : _provisionalEnrollments) {
        if (enrollment.transactionId != transactionId) {
            continue;
        }
        auto* operation = document->getObjectByID(enrollment.objectId);
        if (!operation || enrollment.objectName != operation->getNameInDocument()
            || !importedSet.contains(operation)) {
            continue;
        }
        provisionalMarkers.emplace(operation, enrollment.insertionMarker);
        if (!firstProvisionalOverlap) {
            firstProvisionalOverlap = operation;
            firstProvisionalMarker = enrollment.insertionMarker;
        }
    }

    std::unordered_set<DocumentObject*> existingSet;
    existingSet.reserve(storedOperations.size());
    std::unordered_set<DocumentObject*> acceptedProvisionalOverlaps;
    acceptedProvisionalOverlaps.reserve(provisionalMarkers.size());
    for (auto* operation : storedOperations) {
        if (!operation || !document->containsObject(operation) || operation->getDocument() != document
            || !isOperationCandidate(operation) || !existingSet.insert(operation).second
            || !hasValidTimelineOwnerChain(operation) || !replacementInputContract(operation).valid) {
            throw Base::RuntimeError(
                "The target timeline contains a missing, duplicate, "
                "cross-document, or malformed operation"
            );
        }
        if (!importedSet.contains(operation)) {
            continue;
        }
        if (!provisionalMarkers.contains(operation)) {
            throw Base::RuntimeError(
                "An imported object was already present in history before "
                "the current import transaction"
            );
        }
        acceptedProvisionalOverlaps.insert(operation);
    }
    if (!acceptedProvisionalOverlaps.empty()) {
        if (!firstProvisionalOverlap || !acceptedProvisionalOverlaps.contains(firstProvisionalOverlap)
            || firstProvisionalMarker < 0
            || firstProvisionalMarker >= static_cast<long>(storedOperations.size())
            || storedOperations[static_cast<std::size_t>(firstProvisionalMarker)]
                != firstProvisionalOverlap) {
            throw Base::RuntimeError("The provisional import history changed before adoption");
        }
        for (auto* operation : acceptedProvisionalOverlaps) {
            const auto marker = provisionalMarkers.at(operation);
            if (marker < 0 || marker >= static_cast<long>(storedOperations.size())
                || storedOperations[static_cast<std::size_t>(marker)] != operation) {
                throw Base::RuntimeError("A provisional import operation moved before adoption");
            }
        }
    }

    std::vector<DocumentObject*> baseOperations;
    baseOperations.reserve(storedOperations.size() - acceptedProvisionalOverlaps.size());
    boost::dynamic_bitset<> baseVisibility;
    boost::dynamic_bitset<> baseSuppression;
    long basePosition = 0;
    std::size_t insertion = static_cast<std::size_t>(storedPosition);
    if (!acceptedProvisionalOverlaps.empty()) {
        insertion = 0;
    }
    for (std::size_t index = 0; index < storedOperations.size(); ++index) {
        auto* operation = storedOperations[index];
        if (acceptedProvisionalOverlaps.contains(operation)) {
            continue;
        }
        if (static_cast<long>(index) < storedPosition) {
            ++basePosition;
        }
        if (!acceptedProvisionalOverlaps.empty()
            && static_cast<long>(index) < firstProvisionalMarker) {
            ++insertion;
        }
        baseOperations.push_back(operation);
        baseVisibility.resize(baseOperations.size());
        baseVisibility.set(
            baseOperations.size() - 1,
            bitAt(storedVisibility, index, operation->Visibility.getValue())
        );
        baseSuppression.resize(baseOperations.size());
        baseSuppression.set(
            baseOperations.size() - 1,
            bitAt(storedSuppression, index, operationSuppressed(operation))
        );
    }
    if (acceptedProvisionalOverlaps.empty()) {
        baseVisibility = storedVisibility;
        baseSuppression = storedSuppression;
        basePosition = storedPosition;
    }
    else if (basePosition != static_cast<long>(insertion)) {
        throw Base::RuntimeError("The history marker changed during provisional import");
    }

    // If a complete source document was merged, its imported timeline is the
    // authoritative source chronology. Selected-object copy paths supply the
    // already-mapped source sequence explicitly because the source timeline
    // itself is intentionally not exported.
    std::vector<DocumentObject*> transportedOrder;
    std::vector<bool> transportedVisibility;
    std::vector<bool> transportedSuppression;
    if (!importedTimelines.empty()) {
        std::sort(
            importedTimelines.begin(),
            importedTimelines.end(),
            [](const auto* left, const auto* right) { return left->getID() < right->getID(); }
        );
        for (const auto* importedTimeline : importedTimelines) {
            const auto& importedOperations = importedTimeline->Operations.getValues();
            const auto& importedVisibility = importedTimeline->VisibilityAtEnd.getValues();
            const auto& importedSuppression = importedTimeline->SuppressionAtEnd.getValues();
            const auto importedPosition = importedTimeline->Position.getValue();
            const auto importedSchema = importedTimeline->SchemaVersion.getValue();
            if (importedVisibility.size() != importedOperations.size()
                || importedSuppression.size() != importedOperations.size() || importedPosition < 0
                || importedPosition > static_cast<long>(importedOperations.size())
                || importedSchema <= 0 || importedSchema > CurrentSchemaVersion) {
                throw Base::RuntimeError("An imported source timeline has inconsistent state");
            }
            std::unordered_set<DocumentObject*> transportIdentities;
            transportIdentities.reserve(importedOperations.size());
            for (auto* operation : importedOperations) {
                if (!operation || !importedSet.contains(operation)
                    || operation->isDerivedFrom<DocumentTimeline>()
                    || !isOperationCandidate(operation)
                    || !transportIdentities.insert(operation).second) {
                    throw Base::RuntimeError(
                        "An imported source timeline references a missing, "
                        "duplicate, internal, or non-operation object"
                    );
                }
            }
            const auto& importedOrder = importedTimeline->Operations.getValues();
            transportedOrder.insert(transportedOrder.end(), importedOrder.begin(), importedOrder.end());
            for (std::size_t index = 0; index < importedOrder.size(); ++index) {
                transportedVisibility.push_back(importedVisibility.test(index));
                transportedSuppression.push_back(importedSuppression.test(index));
            }
        }
    }
    std::vector<DocumentObject*> orderedImported = sourceOrder;
    std::vector<bool> orderedVisibility = sourceVisibility;
    std::vector<bool> orderedSuppression = sourceSuppression;
    if (orderedImported.empty()) {
        orderedImported = transportedOrder;
        orderedVisibility = transportedVisibility;
        orderedSuppression = transportedSuppression;
    }
    else if (!transportedOrder.empty() && orderedImported != transportedOrder) {
        throw Base::RuntimeError(
            "The explicit source chronology disagrees with the imported "
            "timeline"
        );
    }
    else if (!transportedOrder.empty()) {
        if (hasExplicitSourceState
            && (orderedVisibility != transportedVisibility
                || orderedSuppression != transportedSuppression)) {
            throw Base::RuntimeError(
                "The explicit source state disagrees with the imported "
                "timeline"
            );
        }
        if (!hasExplicitSourceState) {
            orderedVisibility = transportedVisibility;
            orderedSuppression = transportedSuppression;
        }
    }
    const bool hasAcceptedSourceState = !orderedVisibility.empty() || !orderedSuppression.empty();
    if ((orderedVisibility.empty() != orderedSuppression.empty())
        || (hasAcceptedSourceState
            && (orderedVisibility.size() != orderedImported.size()
                || orderedSuppression.size() != orderedImported.size()))) {
        throw Base::RuntimeError("The accepted source timeline state is inconsistent");
    }

    std::unordered_set<DocumentObject*> orderedSet;
    orderedSet.reserve(orderedImported.size());
    for (auto* operation : orderedImported) {
        if (!operation || !importedSet.contains(operation)
            || operation->isDerivedFrom<DocumentTimeline>() || !isOperationCandidate(operation)
            || !orderedSet.insert(operation).second) {
            throw Base::ValueError(
                "The imported source chronology contains a missing, "
                "duplicate, internal, or non-operation object"
            );
        }
    }
    std::unordered_map<DocumentObject*, std::pair<bool, bool>> acceptedSourceState;
    if (hasAcceptedSourceState) {
        acceptedSourceState.reserve(orderedImported.size());
        for (std::size_t index = 0; index < orderedImported.size(); ++index) {
            acceptedSourceState.emplace(
                orderedImported[index],
                std::pair {
                    orderedVisibility[index],
                    orderedSuppression[index],
                }
            );
        }
    }
    for (const auto& [operation, state] : acceptedSourceState) {
        if (state.second && !operation->getExtensionByType<SuppressibleExtension>(true)) {
            throw Base::ValueError(
                "An imported source marks a non-suppressible operation as "
                "suppressed"
            );
        }
    }

    std::unordered_set<DocumentObject*> semanticRoots;
    std::unordered_set<DocumentObject*> semanticMembers;
    for (auto* object : importedObjects) {
        if (object->isDerivedFrom<DocumentTimeline>()) {
            continue;
        }

        const auto* roleProperty = localTimelineMetadataProperty(object, RolePropertyName);
        const auto* role = dynamic_cast<const PropertyString*>(roleProperty);
        if (roleProperty && !role) {
            throw Base::RuntimeError("Imported timeline role metadata has the wrong property type");
        }
        if (roleProperty) {
            validateCanonicalTimelineMetadataStatus(
                roleProperty,
                "Imported role metadata is not hidden, locked, and non-recomputing"
            );
        }
        const std::string_view roleValue = role ? std::string_view(role->getValue())
                                                : std::string_view();
        if (role && roleValue != OperationRole
            && roleValue != ResourceRole
            && roleValue != InternalRole) {
            throw Base::RuntimeError("Imported timeline role metadata has an unknown value");
        }

        const auto* ownerProperty = localTimelineMetadataProperty(object, OwnerPropertyName);
        const auto* owner = dynamic_cast<const PropertyLinkHidden*>(ownerProperty);
        if (ownerProperty && !owner) {
            throw Base::RuntimeError("Imported timeline owner metadata has the wrong property type");
        }
        if (ownerProperty) {
            validateCanonicalTimelineMetadataStatus(
                ownerProperty,
                "Imported owner metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (roleValue == ResourceRole) {
            const auto* ownerValue = owner ? owner->getValue() : nullptr;
            if (!ownerValue || ownerValue == object
                || !importedSet.contains(const_cast<DocumentObject*>(ownerValue))) {
                throw Base::RuntimeError("An imported timeline resource has no imported owner");
            }
        }
        else if (owner && owner->getValue()) {
            throw Base::RuntimeError("An imported timeline operation carries a stale owner link");
        }

        const auto* editorProperty = localTimelineMetadataProperty(object, EditorPropertyName);
        const auto* editor = dynamic_cast<const PropertyLinkHidden*>(editorProperty);
        if (editorProperty && !editor) {
            throw Base::RuntimeError("Imported timeline editor metadata has the wrong property type");
        }
        if (editorProperty) {
            validateCanonicalTimelineMetadataStatus(
                editorProperty,
                "Imported editor metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (editor && editor->getValue()) {
            if (!importedSet.contains(editor->getValue())
                || timelineEditor(object) != editor->getValue()) {
                throw Base::RuntimeError("An imported timeline operation carries a stale editor link");
            }
        }

        const auto* editCommandProperty
            = localTimelineMetadataProperty(object, EditCommandPropertyName);
        if (editCommandProperty && !dynamic_cast<const PropertyString*>(editCommandProperty)) {
            throw Base::RuntimeError(
                "Imported timeline edit-command metadata has the wrong "
                "property type"
            );
        }
        if (editCommandProperty) {
            validateCanonicalTimelineMetadataStatus(
                editCommandProperty,
                "Imported edit-command metadata is not hidden, locked, and non-recomputing"
            );
        }

        const auto* deleteCommandProperty =
            localTimelineMetadataProperty(object, DeleteCommandPropertyName);
        if (deleteCommandProperty
            && !dynamic_cast<const PropertyString*>(
                deleteCommandProperty
            )) {
            throw Base::RuntimeError(
                "Imported timeline delete-command metadata has the wrong "
                "property type"
            );
        }
        if (deleteCommandProperty) {
            validateCanonicalTimelineMetadataStatus(
                deleteCommandProperty,
                "Imported delete-command metadata is not hidden, locked, and non-recomputing"
            );
        }

        const auto replacement = replacementInputContract(object);
        if (!replacement.valid) {
            throw Base::RuntimeError("Imported timeline replacement metadata is malformed");
        }
        for (auto* input : replacement.inputs) {
            if (!importedSet.contains(input)) {
                throw Base::RuntimeError(
                    "An imported replacement operation is missing one of its "
                    "declared inputs"
                );
            }
        }

        if (const auto* replacementProperty
            = localTimelineMetadataProperty(object, ReplacedInputsPropertyName)) {
            if (!dynamic_cast<const PropertyLinkListHidden*>(replacementProperty)) {
                throw Base::RuntimeError("Imported replacement metadata has the wrong property type");
            }
            validateCanonicalTimelineMetadataStatus(
                replacementProperty,
                "Imported replacement metadata is not hidden, locked, and non-recomputing"
            );
        }

        if (roleValue == ResourceRole) {
            auto* root = const_cast<DocumentObject*>(semanticOperationRoot(object, document));
            if (!root || !importedSet.contains(root) || !isOperationCandidate(root)) {
                throw Base::RuntimeError(
                    "An imported timeline resource has an incomplete owner "
                    "block"
                );
            }
            semanticRoots.insert(root);
            semanticMembers.insert(object);
            semanticMembers.insert(root);
        }
        else if (roleValue == OperationRole) {
            if (!isOperationCandidate(object)) {
                throw Base::RuntimeError(
                    "Imported operation metadata is attached to an internal "
                    "object"
                );
            }
            semanticRoots.insert(object);
            semanticMembers.insert(object);
        }
    }

    // A block is either wholly imported or rejected. This also catches a
    // caller which imported a root and then attached a pre-existing resource,
    // because that resource would otherwise disappear when the operation is
    // copied or moved through History.
    for (auto* object : document->getObjects()) {
        if (!hasTimelineResourceRole(object)) {
            continue;
        }
        auto* root = const_cast<DocumentObject*>(semanticOperationRoot(object, document));
        if (!root || !semanticRoots.contains(root)) {
            continue;
        }
        if (!importedSet.contains(object)) {
            throw Base::RuntimeError(
                "An imported semantic operation does not contain all of its "
                "owned resources"
            );
        }
        semanticMembers.insert(object);
    }
    for (auto* object : semanticMembers) {
        if (!orderedSet.contains(object)) {
            throw Base::RuntimeError(
                "An imported semantic block is missing from its mapped source "
                "chronology"
            );
        }
    }

    // Objects from documents created before the global timeline have no
    // source chronology. Include only real operation candidates and order
    // those legacy additions by stable target identity.
    std::vector<DocumentObject*> legacyOperations;
    for (auto* object : importedObjects) {
        if (object->isDerivedFrom<DocumentTimeline>() || orderedSet.contains(object)
            || !isOperationCandidate(object)) {
            continue;
        }
        legacyOperations.push_back(object);
    }
    std::sort(legacyOperations.begin(), legacyOperations.end(), [](const auto* left, const auto* right) {
        if (left->getID() != right->getID()) {
            return left->getID() < right->getID();
        }
        return std::strcmp(left->getNameInDocument(), right->getNameInDocument()) < 0;
    });
    orderedImported.insert(orderedImported.end(), legacyOperations.begin(), legacyOperations.end());
    const auto consumeImportedTimelines = [document, &importedTimelines]() {
        for (auto* importedTimeline : importedTimelines) {
            const std::string name = importedTimeline->getNameInDocument();
            document->removeObject(name.c_str());
            if (document->getObject(name.c_str())) {
                throw Base::RuntimeError("The imported source timeline survived adoption");
            }
        }
    };
    if (orderedImported.empty()) {
        consumeImportedTimelines();
        return;
    }

    std::vector<DocumentObject*> finalOperations;
    finalOperations.reserve(baseOperations.size() + orderedImported.size());
    finalOperations
        .insert(finalOperations.end(), baseOperations.begin(), baseOperations.begin() + insertion);
    finalOperations.insert(finalOperations.end(), orderedImported.begin(), orderedImported.end());
    finalOperations
        .insert(finalOperations.end(), baseOperations.begin() + insertion, baseOperations.end());

    boost::dynamic_bitset<> finalVisibility(finalOperations.size());
    boost::dynamic_bitset<> finalSuppression(finalOperations.size());
    for (std::size_t index = 0; index < baseOperations.size(); ++index) {
        const auto finalIndex = index < insertion ? index : index + orderedImported.size();
        finalVisibility.set(
            finalIndex,
            bitAt(baseVisibility, index, baseOperations[index]->Visibility.getValue())
        );
        finalSuppression.set(
            finalIndex,
            bitAt(baseSuppression, index, operationSuppressed(baseOperations[index]))
        );
    }
    for (std::size_t index = 0; index < orderedImported.size(); ++index) {
        auto* operation = orderedImported[index];
        const auto acceptedState = acceptedSourceState.find(operation);
        finalVisibility.set(
            insertion + index,
            acceptedState != acceptedSourceState.end() ? acceptedState->second.first
                                                       : operation->Visibility.getValue()
        );
        finalSuppression.set(
            insertion + index,
            acceptedState != acceptedSourceState.end() ? acceptedState->second.second
                                                       : operationSuppressed(operation)
        );
    }
    const auto finalPosition = static_cast<long>(insertion)
        + static_cast<long>(orderedImported.size());

    struct FinalBlock
    {
        std::size_t begin {std::numeric_limits<std::size_t>::max()};
        std::size_t end {0};
        std::size_t count {0};
        std::size_t rootIndex {std::numeric_limits<std::size_t>::max()};
    };
    std::unordered_set<DocumentObject*> finalIdentities;
    std::unordered_map<const DocumentObject*, FinalBlock> finalBlocks;
    finalIdentities.reserve(finalOperations.size());
    finalBlocks.reserve(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        auto* candidate = finalOperations[index];
        const auto* root = semanticOperationRoot(candidate, document);
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !finalIdentities.insert(candidate).second || !root || hasTimelineResourceRole(root)) {
            throw Base::RuntimeError(
                "The imported final timeline has a missing, duplicate, or incomplete identity"
            );
        }
        auto& block = finalBlocks[root];
        block.begin = std::min(block.begin, index);
        block.end = std::max(block.end, index + 1);
        ++block.count;
        if (candidate == root) {
            block.rootIndex = index;
        }
    }
    for (const auto& [root, block] : finalBlocks) {
        if (block.rootIndex == std::numeric_limits<std::size_t>::max()
            || block.rootIndex + 1 != block.end || block.end - block.begin != block.count
            || (static_cast<long>(block.begin) < finalPosition
                && finalPosition < static_cast<long>(block.end))) {
            throw Base::RuntimeError(
                "The imported final timeline is not canonical at one semantic block"
            );
        }
        validateCanonicalSemanticBlockOrder(
            std::vector<DocumentObject*>(
                finalOperations.begin() + static_cast<std::ptrdiff_t>(block.begin),
                finalOperations.begin() + static_cast<std::ptrdiff_t>(block.end)
            ),
            root,
            "Imported semantic resources must be contiguous and nested resource-first, owner-last"
        );
    }

    struct ImportedStateTarget
    {
        TimelineObjectIdentity object;
        bool visibility {false};
        bool suppression {false};
    };
    std::vector<ImportedStateTarget> importedStateTargets;
    importedStateTargets.reserve(acceptedSourceState.size());
    for (auto* operation : orderedImported) {
        const auto state = acceptedSourceState.find(operation);
        if (state == acceptedSourceState.end()) {
            continue;
        }
        importedStateTargets.push_back(
            ImportedStateTarget {
                .object = {
                    .objectId = operation->getID(),
                    .objectName = operation->getNameInDocument(),
                },
                .visibility = state->second.first,
                .suppression = state->second.second,
            }
        );
    }
    std::vector<TimelineObjectIdentity> finalOperationIdentities;
    finalOperationIdentities.reserve(finalOperations.size());
    for (auto* operation : finalOperations) {
        finalOperationIdentities.push_back(
            TimelineObjectIdentity {
                .objectId = operation->getID(),
                .objectName = operation->getNameInDocument(),
            }
        );
    }

    ApplyingScope applying(*this);
    for (const auto& target : importedStateTargets) {
        auto* operation
            = resolveExactTimelineIdentity(document, target.object.objectId, target.object.objectName);
        if (!operation) {
            throw Base::RuntimeError("An imported operation lost its document identity");
        }
        operation->Visibility.setValue(target.visibility);
        operation
            = resolveExactTimelineIdentity(document, target.object.objectId, target.object.objectName);
        if (!operation || operation->Visibility.getValue() != target.visibility) {
            throw Base::RuntimeError("An imported operation rejected its accepted visibility");
        }
        if (auto* suppressible = operation->getExtensionByType<SuppressibleExtension>(true)) {
            suppressible->Suppressed.setValue(target.suppression);
            operation = resolveExactTimelineIdentity(
                document,
                target.object.objectId,
                target.object.objectName
            );
            suppressible = operation ? operation->getExtensionByType<SuppressibleExtension>(true)
                                     : nullptr;
            if (!operation || !suppressible
                || suppressible->Suppressed.getValue() != target.suppression) {
                throw Base::RuntimeError(
                    "An imported operation rejected its accepted "
                    "suppression state"
                );
            }
        }
    }
    std::vector<DocumentObject*> resolvedFinalOperations;
    resolvedFinalOperations.reserve(finalOperationIdentities.size());
    for (const auto& identity : finalOperationIdentities) {
        auto* operation
            = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
        if (!operation) {
            throw Base::RuntimeError(
                "An imported timeline identity was removed while applying accepted state"
            );
        }
        resolvedFinalOperations.push_back(operation);
    }
    Operations.setValues(resolvedFinalOperations);
    VisibilityAtEnd.setValues(finalVisibility);
    SuppressionAtEnd.setValues(finalSuppression);
    Position.setValue(finalPosition);
    SchemaVersion.setValue(CurrentSchemaVersion);
    if (Operations.getValues() != resolvedFinalOperations
        || VisibilityAtEnd.getValues() != finalVisibility
        || SuppressionAtEnd.getValues() != finalSuppression || Position.getValue() != finalPosition) {
        throw Base::RuntimeError("The imported timeline changed while applying its validated state");
    }

    // An imported DocumentTimeline is transport metadata, not a second
    // controller. Consume it only after the canonical target state has been
    // applied, while the caller can still roll the entire import back.
    consumeImportedTimelines();

    const auto isImportedIdentity = [&importedIdentities](const TimelineObjectIdentity& identity) {
        return std::ranges::any_of(
            importedIdentities,
            [&identity](const TimelineObjectIdentity& imported) {
                return imported.objectId == identity.objectId
                    && imported.objectName == identity.objectName;
            }
        );
    };
    auto creationProvenance = std::find_if(
        _provisionalTransactionCreations.begin(),
        _provisionalTransactionCreations.end(),
        [document, transactionId](const ProvisionalTransactionCreations& candidate) {
            return candidate.transactionId == transactionId
                && candidate.documentName == document->getName()
                && candidate.documentUid == document->Uid.getValueStr();
        }
    );
    const bool hasImportedCreationProof = creationProvenance != _provisionalTransactionCreations.end()
        && std::ranges::any_of(creationProvenance->objects, isImportedIdentity);
    const bool hasUnrelatedCreationProof = creationProvenance
            != _provisionalTransactionCreations.end()
        && std::ranges::any_of(creationProvenance->objects,
                               [&isImportedIdentity](const TimelineObjectIdentity& identity) {
                                   return !isImportedIdentity(identity);
                               });

    if (hasImportedCreationProof && hasUnrelatedCreationProof) {
        // One provenance generation has one pre-creation timeline snapshot.
        // Consuming only the adopted identities would put the newly adopted
        // History block outside that snapshot and invalidate every unrelated
        // object which is still waiting to publish. Keep the adopted
        // operations as exact pending members of the same generation instead.
        // A preceding publication can then consume its own identities while
        // preserving this adopted block and the original creation order.
        std::unordered_map<DocumentObject*, long> finalMarkers;
        finalMarkers.reserve(resolvedFinalOperations.size());
        for (std::size_t index = 0; index < resolvedFinalOperations.size(); ++index) {
            finalMarkers.emplace(resolvedFinalOperations[index], static_cast<long>(index));
        }
        for (auto& enrollment : _provisionalEnrollments) {
            if (enrollment.transactionId != transactionId) {
                continue;
            }
            auto* operation
                = resolveExactTimelineIdentity(document, enrollment.objectId, enrollment.objectName);
            const auto marker = operation ? finalMarkers.find(operation) : finalMarkers.end();
            if (marker != finalMarkers.end()) {
                enrollment.insertionMarker = marker->second;
            }
        }
        for (auto* operation : resolvedFinalOperations) {
            const TimelineObjectIdentity identity {
                .objectId = operation->getID(),
                .objectName = operation->getNameInDocument(),
            };
            if (isImportedIdentity(identity) && isCreatedByCurrentTransaction(operation)) {
                rememberProvisionalEnrollment(operation, finalMarkers.at(operation));
            }
        }
    }
    else {
        std::erase_if(
            _provisionalEnrollments,
            [transactionId, &acceptedProvisionalOverlaps](const ProvisionalEnrollment& enrollment) {
                if (enrollment.transactionId != transactionId) {
                    return false;
                }
                return std::any_of(
                    acceptedProvisionalOverlaps.begin(),
                    acceptedProvisionalOverlaps.end(),
                    [&enrollment](const DocumentObject* operation) {
                        return operation && enrollment.objectId == operation->getID()
                            && enrollment.objectName == operation->getNameInDocument();
                    }
                );
            }
        );
        if (hasImportedCreationProof) {
            std::erase_if(creationProvenance->objects, isImportedIdentity);
        }
    }
}

void DocumentTimeline::stageExistingOperationResources(
    DocumentObject* provisionalOperation,
    const std::vector<DocumentObject*>& selectedOperations
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Existing timeline resources can only be staged inside one clean "
            "caller-owned transaction"
        );
    }
    if (!provisionalOperation || selectedOperations.empty()) {
        throw Base::ValueError(
            "Staging existing timeline resources requires a provisional "
            "operation and an explicit non-empty selection"
        );
    }

    pruneProvisionalEnrollments();
    pruneStagedResourceAdoptions();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)
        || !isProvisionallyEnrolledByCurrentTransaction(provisionalOperation)) {
        throw Base::RuntimeError("The resource owner was not created by the current transaction");
    }
    if (!document->containsObject(provisionalOperation)
        || provisionalOperation->getDocument() != document
        || !isOperationCandidate(provisionalOperation)
        || hasTimelineResourceRole(provisionalOperation) || timelineOwner(provisionalOperation)) {
        throw Base::ValueError(
            "The staged resource owner is not one independent provisional "
            "operation"
        );
    }
    if (std::ranges::any_of(
            _stagedResourceAdoptions,
            [transactionId, provisionalOperation](const StagedResourceAdoption& adoption) {
                return adoption.transactionId == transactionId
                    && adoption.operationId == provisionalOperation->getID()
                    && adoption.operationName == provisionalOperation->getNameInDocument();
            }
        )) {
        throw Base::RuntimeError(
            "Existing timeline resources were already staged for this "
            "operation"
        );
    }

    const auto operations = Operations.getValues();
    const auto visibility = VisibilityAtEnd.getValues();
    const auto suppression = SuppressionAtEnd.getValues();
    const long position = Position.getValue();
    if (operations.empty() || visibility.size() != operations.size()
        || suppression.size() != operations.size() || position < 0
        || position > static_cast<long>(operations.size())) {
        throw Base::RuntimeError("The timeline state is inconsistent");
    }

    std::unordered_map<const DocumentObject*, std::size_t> indices;
    indices.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        const auto* candidate = operations[index];
        if (!candidate || !document->containsObject(candidate)
            || candidate->getDocument() != document || !isOperationCandidate(candidate)
            || !indices.emplace(candidate, index).second || !hasValidTimelineOwnerChain(candidate)
            || !replacementInputContract(const_cast<DocumentObject*>(candidate)).valid) {
            throw Base::RuntimeError(
                "The timeline contains a missing, duplicate, or malformed "
                "operation"
            );
        }
    }

    const auto ownerIndex = indices.find(provisionalOperation);
    if (ownerIndex == indices.end() || static_cast<long>(ownerIndex->second) >= position) {
        throw Base::RuntimeError(
            "The provisional resource owner is not active at the current "
            "history boundary"
        );
    }

    std::unordered_set<const DocumentObject*> selectedSet;
    selectedSet.reserve(selectedOperations.size());
    StagedResourceAdoption adoption {
        .transactionId = transactionId,
        .operationId = provisionalOperation->getID(),
        .operationName = provisionalOperation->getNameInDocument(),
        .resources = {},
    };
    adoption.resources.reserve(selectedOperations.size());

    for (const auto* selected : selectedOperations) {
        const auto found = indices.find(selected);
        if (!selected || selected == provisionalOperation || !document->containsObject(selected)
            || selected->getDocument() != document || found == indices.end()
            || !selectedSet.insert(selected).second
            || isProvisionallyEnrolledByCurrentTransaction(selected)
            || static_cast<long>(found->second) >= position || hasTimelineResourceRole(selected)
            || timelineOwner(selected) || semanticOperationRoot(selected, document) != selected) {
            throw Base::ValueError(
                "Every staged resource must be a distinct active existing "
                "independent operation"
            );
        }

        const auto* roleProperty = localTimelineMetadataProperty(selected, RolePropertyName);
        const auto* role = dynamic_cast<const PropertyString*>(roleProperty);
        if (roleProperty && (!role || std::string_view(role->getValue()) != OperationRole)) {
            throw Base::RuntimeError("A staged resource has incompatible existing role metadata");
        }
        const auto* ownerProperty = localTimelineMetadataProperty(selected, OwnerPropertyName);
        const auto* owner = dynamic_cast<const PropertyLinkHidden*>(ownerProperty);
        if (ownerProperty && (!owner || owner->getValue())) {
            throw Base::RuntimeError("A staged resource has incompatible existing owner metadata");
        }
        const auto replacement = replacementInputContract(const_cast<DocumentObject*>(selected));
        if (!replacement.valid || replacement.declared) {
            throw Base::RuntimeError(
                "An operation with a replacement-input contract must retain "
                "its independent history step"
            );
        }

        for (const auto* candidate : document->getObjects()) {
            if (candidate != selected && semanticOperationRoot(candidate, document) == selected) {
                throw Base::RuntimeError(
                    "An operation which already owns timeline resources must "
                    "retain its complete independent block"
                );
            }
        }

        for (const auto* dependent : selected->getInList()) {
            if (!dependent || dependent == provisionalOperation
                || !document->containsObject(dependent) || dependent->getDocument() != document
                || isStructuralTimelineLink(dependent, selected)) {
                continue;
            }
            const auto* dependentRoot = semanticOperationRoot(dependent, document);
            const auto dependentIndex = indices.find(dependentRoot);
            if (!dependentRoot
                || (dependentIndex != indices.end() && dependentRoot != selected
                    && static_cast<long>(dependentIndex->second) <= static_cast<long>(found->second))) {
                throw Base::RuntimeError(
                    "A staged resource has an earlier semantic consumer which "
                    "requires its independent history position"
                );
            }
        }

        adoption.resources.push_back(
            StagedExistingResource {
                .objectId = selected->getID(),
                .objectName = selected->getNameInDocument(),
                .timelineIndex = static_cast<long>(found->second),
            }
        );
    }

    _stagedResourceAdoptions.push_back(std::move(adoption));
}

void DocumentTimeline::stageOperationSegmentReplacement(
    const std::vector<std::vector<DocumentObject*>>& oldRootSegments
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Staging timeline replacement segments requires one normal "
            "document and one caller-owned transaction"
        );
    }
    if (oldRootSegments.empty()
        || std::ranges::any_of(oldRootSegments, [](const auto& roots) { return roots.empty(); })) {
        throw Base::ValueError("At least one non-empty old-root segment must be staged");
    }

    pruneProvisionalEnrollments();
    pruneStagedResourceAdoptions();
    pruneProvisionalInternalObjects();
    pruneStagedSegmentReplacement();
    pruneStagedResourceReconciliation();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The segment-replacement transaction is no longer active");
    }
    if (!_stagedSegmentReplacements.empty()) {
        throw Base::RuntimeError(
            "Timeline replacement segments were already staged by this "
            "transaction"
        );
    }

    const auto operations = Operations.getValues();
    const auto visibility = VisibilityAtEnd.getValues();
    const auto suppression = SuppressionAtEnd.getValues();
    const long position = Position.getValue();
    if (operations.empty() || visibility.size() != operations.size()
        || suppression.size() != operations.size() || position < 0
        || position > static_cast<long>(operations.size())) {
        throw Base::RuntimeError("The timeline state is inconsistent");
    }

    std::unordered_map<DocumentObject*, std::size_t> operationIndices;
    operationIndices.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* candidate = operations[index];
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !isOperationCandidate(candidate) || !operationIndices.emplace(candidate, index).second
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The timeline contains a missing, duplicate, internal, or "
                "malformed operation"
            );
        }
    }

    struct LiveBlock
    {
        DocumentObject* root {nullptr};
        std::size_t begin {std::numeric_limits<std::size_t>::max()};
        std::size_t end {0};
        std::size_t count {0};
        std::size_t rootIndex {std::numeric_limits<std::size_t>::max()};
    };
    std::unordered_map<DocumentObject*, LiveBlock> blocks;
    blocks.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* candidate = operations[index];
        auto* root = const_cast<DocumentObject*>(semanticOperationRoot(candidate, document));
        if (!root || !operationIndices.contains(root) || hasTimelineResourceRole(root)) {
            throw Base::RuntimeError("The timeline contains an incomplete semantic ownership graph");
        }
        auto& block = blocks[root];
        block.root = root;
        block.begin = std::min(block.begin, index);
        block.end = std::max(block.end, index + 1);
        ++block.count;
        if (candidate == root) {
            block.rootIndex = index;
        }
    }
    for (const auto& [root, block] : blocks) {
        if (block.rootIndex == std::numeric_limits<std::size_t>::max()
            || block.rootIndex + 1 != block.end || block.end - block.begin != block.count) {
            throw Base::RuntimeError(
                "The timeline does not contain canonical resource-first, "
                "root-last semantic blocks"
            );
        }
        validateCanonicalSemanticBlockOrder(
            std::vector<DocumentObject*>(
                operations.begin() + static_cast<std::ptrdiff_t>(block.begin),
                operations.begin() + static_cast<std::ptrdiff_t>(block.end)
            ),
            root,
            "The timeline does not contain canonical nested resource-first, owner-last blocks"
        );
        if (static_cast<long>(block.begin) < position && position < static_cast<long>(block.end)) {
            throw Base::RuntimeError("The current history marker cuts through a semantic block");
        }
    }

    StagedSegmentReplacement staged {
        .transactionId = transactionId,
        .documentName = document->getName(),
        .documentUid = document->Uid.getValueStr(),
        .position = position,
        .operations = {},
        .segments = {},
    };
    staged.operations.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        staged.operations.push_back(
            TimelineSnapshotOperation {
                .object = {
                    .objectId = operations[index]->getID(),
                    .objectName =
                        operations[index]->getNameInDocument(),
                },
                .visibility = visibility.test(index),
                .suppression = suppression.test(index),
            }
        );
    }

    std::unordered_set<DocumentObject*> selectedRoots;
    std::unordered_set<DocumentObject*> stagedObjects;
    std::size_t previousSegmentEnd = 0;
    bool firstSegment = true;
    staged.segments.reserve(oldRootSegments.size());
    for (const auto& requestedRoots : oldRootSegments) {
        SegmentSnapshot segment;
        segment.roots.reserve(requestedRoots.size());
        std::size_t segmentBegin = std::numeric_limits<std::size_t>::max();
        std::size_t segmentEnd = 0;
        std::size_t previousBlockEnd = 0;
        bool firstRoot = true;
        bool futureRootSeen = false;

        for (auto* root : requestedRoots) {
            const auto rootIndex = operationIndices.find(root);
            const auto block = blocks.find(root);
            if (!root || !document->containsObject(root) || root->getDocument() != document
                || rootIndex == operationIndices.end() || block == blocks.end()
                || isProvisionallyEnrolledByCurrentTransaction(root)
                || semanticOperationRoot(root, document) != root || hasTimelineResourceRole(root)
                || !hasTimelineOperationRole(root) || !selectedRoots.insert(root).second) {
                throw Base::ValueError(
                    "Every staged root must be one distinct explicit "
                    "canonical semantic root"
                );
            }
            if (!firstRoot && previousBlockEnd != block->second.begin) {
                throw Base::RuntimeError(
                    "Each staged segment must contain adjacent semantic "
                    "blocks in history order"
                );
            }
            if (firstRoot) {
                segmentBegin = block->second.begin;
                firstRoot = false;
            }
            segmentEnd = block->second.end;
            previousBlockEnd = block->second.end;

            const bool active = static_cast<long>(block->second.end) <= position;
            const bool future = static_cast<long>(block->second.begin) >= position;
            if (!active && !future) {
                throw Base::RuntimeError("The history marker cuts through a staged semantic root");
            }
            if (active) {
                if (futureRootSeen) {
                    throw Base::RuntimeError("A staged segment has active roots after future roots");
                }
                ++segment.activeRootCount;
            }
            else {
                futureRootSeen = true;
            }
            segment.roots.push_back(
                TimelineObjectIdentity {
                    .objectId = root->getID(),
                    .objectName = root->getNameInDocument(),
                }
            );
        }

        if (!firstSegment && segmentBegin < previousSegmentEnd) {
            throw Base::RuntimeError(
                "Staged semantic segments must be disjoint and supplied in "
                "history order"
            );
        }
        firstSegment = false;
        previousSegmentEnd = segmentEnd;
        segment.members.reserve(segmentEnd - segmentBegin);
        for (std::size_t index = segmentBegin; index < segmentEnd; ++index) {
            auto* member = operations[index];
            if (isProvisionallyEnrolledByCurrentTransaction(member)
                || !stagedObjects.insert(member).second) {
                throw Base::RuntimeError(
                    "A staged old identity must be pre-existing and belong "
                    "to exactly one segment"
                );
            }
            segment.members.push_back(
                SegmentSnapshotMember {
                    .object = {
                        .objectId = member->getID(),
                        .objectName = member->getNameInDocument(),
                    },
                    .timelineIndex = static_cast<long>(index),
                    .visibility = visibility.test(index),
                    .suppression = suppression.test(index),
                    .retainedConsumers = {},
                    .retainedHiddenConsumers = {},
                }
            );
        }
        staged.segments.push_back(std::move(segment));
    }

    // Consumer evidence must be captured only after every segment identity is
    // known, so consumers inside any staged segment are never misclassified
    // as retained.
    for (auto& segment : staged.segments) {
        for (auto& member : segment.members) {
            auto* object = document->getObjectByID(member.object.objectId);
            if (!object || member.object.objectName != object->getNameInDocument()) {
                throw Base::RuntimeError(
                    "A staged identity changed while its consumer graph was "
                    "being captured"
                );
            }
            std::unordered_set<DocumentObject*> seenConsumers;
            for (auto* consumer : object->getInList()) {
                if (!consumer || stagedObjects.contains(consumer)
                    || !document->containsObject(consumer) || consumer->getDocument() != document
                    || isStructuralTimelineLink(consumer, object)) {
                    continue;
                }
                if (!seenConsumers.insert(consumer).second) {
                    continue;
                }
                member.retainedConsumers.push_back(
                    TimelineObjectIdentity {
                        .objectId = consumer->getID(),
                        .objectName = consumer->getNameInDocument(),
                    }
                );
            }
            for (auto* consumer : document->getObjects()) {
                if (!consumer || stagedObjects.contains(consumer)) {
                    continue;
                }
                if (const auto* editorProperty
                    = localTimelineMetadataProperty(consumer, EditorPropertyName)) {
                    const auto* editor = dynamic_cast<const PropertyLinkHidden*>(editorProperty);
                    if (!editor) {
                        throw Base::RuntimeError(
                            "A retained timeline editor property has the wrong type"
                        );
                    }
                    if (editor->getValue() == object) {
                        member.retainedHiddenConsumers.push_back(
                            SegmentSnapshotMember::HiddenConsumer {
                                .consumer = {
                                    .objectId = consumer->getID(),
                                    .objectName = consumer->getNameInDocument(),
                                },
                                .kind = SegmentSnapshotMember::HiddenConsumerKind::Editor,
                            }
                        );
                    }
                }
                if (const auto* replacementProperty
                    = localTimelineMetadataProperty(consumer, ReplacedInputsPropertyName)) {
                    const auto* replacements = dynamic_cast<const PropertyLinkListHidden*>(
                        replacementProperty
                    );
                    if (!replacements) {
                        throw Base::RuntimeError(
                            "A retained replacement-input property has the wrong type"
                        );
                    }
                    const auto replacementValues = replacements->getValues();
                    if (std::ranges::find(replacementValues, object) != replacementValues.end()) {
                        member.retainedHiddenConsumers.push_back(
                            SegmentSnapshotMember::HiddenConsumer {
                                .consumer = {
                                    .objectId = consumer->getID(),
                                    .objectName = consumer->getNameInDocument(),
                                },
                                .kind =
                                    SegmentSnapshotMember::HiddenConsumerKind::ReplacedInput,
                            }
                        );
                    }
                }
            }
        }
    }

    _stagedSegmentReplacements.push_back(std::move(staged));
}

void DocumentTimeline::finalizeProvisionalOperationBlock(
    DocumentObject* operation,
    const std::vector<DocumentObject*>& orderedNewObjects
)
{
    finalizeProvisionalOperationBlock(operation, orderedNewObjects, {});
}

void DocumentTimeline::finalizeProvisionalOperationBlock(
    DocumentObject* operation,
    const std::vector<DocumentObject*>& orderedNewObjects,
    const std::vector<DocumentObject*>& orderedStagedResources
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "A provisional timeline block requires one normal document and "
            "one caller-owned transaction"
        );
    }
    if (!operation || orderedNewObjects.empty()) {
        throw Base::ValueError(
            "A provisional timeline block requires an operation and newly "
            "enrolled objects"
        );
    }

    pruneProvisionalEnrollments();
    pruneStagedResourceAdoptions();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The provisional timeline transaction is no longer active");
    }

    const auto storedOperations = Operations.getValues();
    const auto storedVisibility = VisibilityAtEnd.getValues();
    const auto storedSuppression = SuppressionAtEnd.getValues();
    const long storedPosition = Position.getValue();
    if (storedOperations.empty() || storedVisibility.size() != storedOperations.size()
        || storedSuppression.size() != storedOperations.size() || storedPosition < 0
        || storedPosition > static_cast<long>(storedOperations.size())) {
        throw Base::RuntimeError("The target timeline state is inconsistent");
    }

    std::unordered_map<DocumentObject*, std::size_t> storedIndices;
    storedIndices.reserve(storedOperations.size());
    for (std::size_t index = 0; index < storedOperations.size(); ++index) {
        auto* candidate = storedOperations[index];
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !isOperationCandidate(candidate) || !storedIndices.emplace(candidate, index).second
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The target timeline contains a missing, duplicate, "
                "cross-document, or malformed operation"
            );
        }
    }
    if (!document->containsObject(operation) || operation->getDocument() != document
        || !storedIndices.contains(operation) || !isOperationCandidate(operation)
        || hasTimelineResourceRole(operation) || !hasTimelineOperationRole(operation)
        || semanticOperationRoot(operation, document) != operation) {
        throw Base::ValueError("The provisional block root is not one explicit tracked operation");
    }

    std::unordered_map<DocumentObject*, long> provisionalMarkers;
    provisionalMarkers.reserve(_provisionalEnrollments.size());
    for (const auto& enrollment : _provisionalEnrollments) {
        if (enrollment.transactionId != transactionId) {
            continue;
        }
        auto* candidate = document->getObjectByID(enrollment.objectId);
        if (!candidate || enrollment.objectName != candidate->getNameInDocument()
            || enrollment.insertionMarker < 0
            || enrollment.insertionMarker >= static_cast<long>(storedOperations.size())
            || storedOperations[static_cast<std::size_t>(enrollment.insertionMarker)] != candidate) {
            throw Base::RuntimeError("A provisional operation moved before its block was finalized");
        }
        provisionalMarkers.emplace(candidate, enrollment.insertionMarker);
    }

    std::unordered_set<DocumentObject*> orderedSet;
    orderedSet.reserve(orderedNewObjects.size());
    for (auto* candidate : orderedNewObjects) {
        if (!candidate) {
            throw Base::ValueError("A provisional timeline block contains a null object");
        }
        if (!document->containsObject(candidate) || candidate->getDocument() != document) {
            throw Base::ValueError(
                "A provisional timeline block object is not live in the target document"
            );
        }
        if (!storedIndices.contains(candidate)) {
            throw Base::ValueError(
                "A provisional timeline block object is absent from the stored history"
            );
        }
        if (!isOperationCandidate(candidate)) {
            throw Base::ValueError(
                "A provisional timeline block object is not a modeling-operation candidate"
            );
        }
        if (!orderedSet.insert(candidate).second) {
            throw Base::ValueError("A provisional timeline block contains a duplicate object");
        }
        if (!provisionalMarkers.contains(candidate)) {
            throw Base::ValueError(
                std::string("Provisional timeline block object '") + candidate->getNameInDocument()
                + "' was not enrolled by the current transaction"
            );
        }
    }

    const bool newRoot = orderedSet.contains(operation);
    if (newRoot) {
        if (orderedNewObjects.back() != operation) {
            throw Base::ValueError(
                "A new semantic operation must be the final object in its "
                "canonical block"
            );
        }
    }
    else {
        if (!orderedStagedResources.empty()) {
            throw Base::RuntimeError(
                "Existing resources can only be adopted by a newly created "
                "provisional operation"
            );
        }
        if (provisionalMarkers.contains(operation)
            || static_cast<long>(storedIndices.at(operation)) >= storedPosition) {
            throw Base::RuntimeError(
                "Resources can only be added to a pre-existing active "
                "semantic operation"
            );
        }
    }

    const auto stagedAdoption = std::find_if(
        _stagedResourceAdoptions.begin(),
        _stagedResourceAdoptions.end(),
        [transactionId, operation](const StagedResourceAdoption& adoption) {
            return adoption.transactionId == transactionId
                && adoption.operationId == operation->getID()
                && adoption.operationName == operation->getNameInDocument();
        }
    );
    if ((stagedAdoption == _stagedResourceAdoptions.end()) != orderedStagedResources.empty()) {
        throw Base::RuntimeError(
            "The finalized existing-resource selection does not match the "
            "selection staged by this task"
        );
    }

    std::unordered_set<DocumentObject*> stagedSet;
    stagedSet.reserve(orderedStagedResources.size());
    if (stagedAdoption != _stagedResourceAdoptions.end()) {
        if (!newRoot || stagedAdoption->resources.size() != orderedStagedResources.size()) {
            throw Base::RuntimeError("The staged existing-resource selection is incomplete");
        }
        for (std::size_t index = 0; index < orderedStagedResources.size(); ++index) {
            auto* candidate = orderedStagedResources[index];
            const auto& proof = stagedAdoption->resources[index];
            const auto storedIndex = storedIndices.find(candidate);
            if (!candidate || candidate == operation || !document->containsObject(candidate)
                || candidate->getDocument() != document || storedIndex == storedIndices.end()
                || !stagedSet.insert(candidate).second || orderedSet.contains(candidate)
                || provisionalMarkers.contains(candidate) || candidate->getID() != proof.objectId
                || candidate->getNameInDocument() != proof.objectName
                || static_cast<long>(storedIndex->second) != proof.timelineIndex
                || static_cast<long>(storedIndex->second) >= storedPosition
                || !hasTimelineResourceRole(candidate) || timelineOwner(candidate) != operation
                || semanticOperationRoot(candidate, document) != operation) {
                throw Base::RuntimeError(
                    "A staged existing resource changed identity, history "
                    "order, or ownership before task acceptance"
                );
            }
        }
    }

    const auto validateMetadata = [document](DocumentObject* candidate) {
        const auto* roleProperty = localTimelineMetadataProperty(candidate, RolePropertyName);
        const auto* role = dynamic_cast<const PropertyString*>(roleProperty);
        if (!roleProperty || !role) {
            throw Base::RuntimeError("A semantic timeline object has no correctly typed role");
        }
        validateCanonicalTimelineMetadataStatus(
            roleProperty,
            "Timeline role metadata is not hidden, locked, and non-recomputing"
        );
        const std::string_view roleValue(role->getValue());
        if (roleValue != OperationRole && roleValue != ResourceRole) {
            throw Base::RuntimeError("A semantic timeline object has an unknown role");
        }

        const auto* ownerProperty = localTimelineMetadataProperty(candidate, OwnerPropertyName);
        const auto* owner = dynamic_cast<const PropertyLinkHidden*>(ownerProperty);
        if (ownerProperty && !owner) {
            throw Base::RuntimeError("Timeline owner metadata has the wrong property type");
        }
        if (ownerProperty) {
            validateCanonicalTimelineMetadataStatus(
                ownerProperty,
                "Timeline owner metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (roleValue == ResourceRole) {
            const auto* ownerValue = owner ? owner->getValue() : nullptr;
            if (!ownerValue || ownerValue == candidate || !document->containsObject(ownerValue)
                || ownerValue->getDocument() != document) {
                throw Base::RuntimeError("A timeline resource has no live same-document owner");
            }
        }
        else if (owner && owner->getValue()) {
            throw Base::RuntimeError("A timeline operation carries a stale owner link");
        }

        const auto* editorProperty = localTimelineMetadataProperty(candidate, EditorPropertyName);
        const auto* editor = dynamic_cast<const PropertyLinkHidden*>(editorProperty);
        if (editorProperty && !editor) {
            throw Base::RuntimeError("Timeline editor metadata has the wrong property type");
        }
        if (editorProperty) {
            validateCanonicalTimelineMetadataStatus(
                editorProperty,
                "Timeline editor metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (editor && editor->getValue() && timelineEditor(candidate) != editor->getValue()) {
            throw Base::RuntimeError("A timeline operation carries a stale editor link");
        }

        const auto* editCommandProperty
            = localTimelineMetadataProperty(candidate, EditCommandPropertyName);
        if (editCommandProperty && !dynamic_cast<const PropertyString*>(editCommandProperty)) {
            throw Base::RuntimeError("Timeline edit-command metadata has the wrong property type");
        }
        if (editCommandProperty) {
            validateCanonicalTimelineMetadataStatus(
                editCommandProperty,
                "Timeline edit-command metadata is not hidden, locked, and non-recomputing"
            );
        }
        const auto* deleteCommandProperty =
            localTimelineMetadataProperty(
                candidate,
                DeleteCommandPropertyName
            );
        if (deleteCommandProperty
            && !dynamic_cast<const PropertyString*>(
                deleteCommandProperty
            )) {
            throw Base::RuntimeError(
                "Timeline delete-command metadata has the wrong property type"
            );
        }
        if (deleteCommandProperty) {
            validateCanonicalTimelineMetadataStatus(
                deleteCommandProperty,
                "Timeline delete-command metadata is not hidden, locked, and non-recomputing"
            );
        }
        const auto* replacementProperty
            = localTimelineMetadataProperty(candidate, ReplacedInputsPropertyName);
        if (replacementProperty && !dynamic_cast<const PropertyLinkListHidden*>(replacementProperty)) {
            throw Base::RuntimeError("Timeline replacement metadata has the wrong property type");
        }
        if (replacementProperty) {
            validateCanonicalTimelineMetadataStatus(
                replacementProperty,
                "Timeline replacement metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (!replacementInputContract(candidate).valid) {
            throw Base::RuntimeError("Timeline replacement metadata is malformed");
        }
    };

    validateMetadata(operation);
    for (auto* candidate : orderedNewObjects) {
        validateMetadata(candidate);
        if (semanticOperationRoot(candidate, document) != operation) {
            throw Base::RuntimeError(
                "Every provisional block object must resolve to its declared "
                "semantic root"
            );
        }
        if (candidate != operation && !hasTimelineResourceRole(candidate)) {
            throw Base::RuntimeError(
                "Every non-root object in a semantic block must be an owned "
                "resource"
            );
        }
    }
    for (auto* candidate : orderedStagedResources) {
        validateMetadata(candidate);
        if (!hasTimelineResourceRole(candidate) || timelineOwner(candidate) != operation
            || semanticOperationRoot(candidate, document) != operation) {
            throw Base::RuntimeError(
                "Every staged object must be an owned resource of its "
                "declared operation"
            );
        }
    }

    std::vector<DocumentObject*> existingResources;
    std::unordered_set<DocumentObject*> completeBlock;
    completeBlock.reserve(storedOperations.size());
    for (auto* candidate : storedOperations) {
        if (semanticOperationRoot(candidate, document) != operation) {
            continue;
        }
        completeBlock.insert(candidate);
        if (candidate != operation && !orderedSet.contains(candidate)) {
            existingResources.push_back(candidate);
        }
    }
    for (auto* candidate : document->getObjects()) {
        if (!hasTimelineResourceRole(candidate)
            || semanticOperationRoot(candidate, document) != operation) {
            continue;
        }
        if (!storedIndices.contains(candidate)) {
            throw Base::RuntimeError("A semantic operation owns an untracked resource");
        }
        if (newRoot && !orderedSet.contains(candidate) && !stagedSet.contains(candidate)) {
            throw Base::RuntimeError(
                "A new semantic operation block is missing one of its "
                "resources"
            );
        }
        completeBlock.insert(candidate);
    }
    for (const auto& [candidate, marker] : provisionalMarkers) {
        (void)marker;
        if (semanticOperationRoot(candidate, document) == operation
            && !orderedSet.contains(candidate)) {
            throw Base::RuntimeError(
                "A provisional semantic resource is missing from the "
                "requested block order"
            );
        }
    }
    if (newRoot && completeBlock.size() != orderedSet.size() + stagedSet.size()) {
        throw Base::RuntimeError(
            "A new semantic operation block contains an unstaged "
            "pre-existing object"
        );
    }

    std::vector<DocumentObject*> canonicalBlock;
    if (newRoot) {
        canonicalBlock = orderedStagedResources;
    }
    else {
        canonicalBlock = existingResources;
    }
    canonicalBlock.insert(canonicalBlock.end(), orderedNewObjects.begin(), orderedNewObjects.end());
    if (!newRoot) {
        canonicalBlock.push_back(operation);
    }
    if (canonicalBlock.empty() || canonicalBlock.back() != operation
        || canonicalBlock.size() != completeBlock.size()) {
        throw Base::RuntimeError("The complete semantic block could not be canonicalized");
    }
    validateCanonicalSemanticBlockOrder(
        canonicalBlock,
        operation,
        "The complete semantic block is not in canonical nested resource-first, owner-last order"
    );

    std::size_t insertionRaw = storedOperations.size();
    for (std::size_t index = 0; index < storedOperations.size(); ++index) {
        if (completeBlock.contains(storedOperations[index])) {
            insertionRaw = std::min(insertionRaw, index);
        }
    }
    if (insertionRaw == storedOperations.size()) {
        throw Base::RuntimeError("The semantic block is absent from the target timeline");
    }

    std::vector<DocumentObject*> baseOperations;
    baseOperations.reserve(storedOperations.size() - completeBlock.size());
    std::size_t insertion = 0;
    for (std::size_t index = 0; index < storedOperations.size(); ++index) {
        if (completeBlock.contains(storedOperations[index])) {
            continue;
        }
        if (index < insertionRaw) {
            ++insertion;
        }
        baseOperations.push_back(storedOperations[index]);
    }

    std::vector<DocumentObject*> finalOperations;
    finalOperations.reserve(storedOperations.size());
    finalOperations
        .insert(finalOperations.end(), baseOperations.begin(), baseOperations.begin() + insertion);
    finalOperations.insert(finalOperations.end(), canonicalBlock.begin(), canonicalBlock.end());
    finalOperations
        .insert(finalOperations.end(), baseOperations.begin() + insertion, baseOperations.end());
    if (finalOperations.size() != storedOperations.size()) {
        throw Base::RuntimeError("Canonicalizing a semantic block changed the history size");
    }

    struct SemanticBlockState
    {
        std::size_t begin {std::numeric_limits<std::size_t>::max()};
        std::size_t end {0};
        std::size_t count {0};
        std::size_t rootIndex {std::numeric_limits<std::size_t>::max()};
    };
    std::unordered_map<const DocumentObject*, SemanticBlockState> finalBlocks;
    finalBlocks.reserve(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        const auto* root = semanticOperationRoot(finalOperations[index], document);
        if (!root || hasTimelineResourceRole(root)
            || !storedIndices.contains(const_cast<DocumentObject*>(root))) {
            throw Base::RuntimeError("The finalized history has an incomplete ownership graph");
        }
        auto& block = finalBlocks[root];
        block.begin = std::min(block.begin, index);
        block.end = std::max(block.end, index + 1);
        ++block.count;
        if (finalOperations[index] == root) {
            block.rootIndex = index;
        }
    }

    std::vector<std::pair<std::size_t, const DocumentObject*>> rootsInOrder;
    rootsInOrder.reserve(finalBlocks.size());
    for (const auto& [root, block] : finalBlocks) {
        if (block.rootIndex == std::numeric_limits<std::size_t>::max()
            || block.rootIndex + 1 != block.end || block.end - block.begin != block.count) {
            throw Base::RuntimeError("The finalized history contains a crossing semantic block");
        }
        validateCanonicalSemanticBlockOrder(
            std::vector<DocumentObject*>(
                finalOperations.begin() + static_cast<std::ptrdiff_t>(block.begin),
                finalOperations.begin() + static_cast<std::ptrdiff_t>(block.end)
            ),
            root,
            "The finalized history contains a noncanonical nested semantic block"
        );
        rootsInOrder.emplace_back(block.begin, root);
    }
    std::ranges::sort(rootsInOrder);

    std::unordered_map<const DocumentObject*, bool> rootWasActive;
    rootWasActive.reserve(rootsInOrder.size());
    for (const auto& [root, block] : finalBlocks) {
        (void)block;
        const auto oldRoot = storedIndices.find(const_cast<DocumentObject*>(root));
        if (oldRoot == storedIndices.end()) {
            throw Base::RuntimeError("The finalized history lost a semantic root");
        }
        rootWasActive.emplace(root, static_cast<long>(oldRoot->second) < storedPosition);
    }

    long finalPosition = 0;
    bool inactiveRootSeen = false;
    for (const auto& [begin, root] : rootsInOrder) {
        (void)begin;
        const bool active = rootWasActive.at(root);
        if (!active) {
            inactiveRootSeen = true;
            continue;
        }
        if (inactiveRootSeen) {
            throw Base::RuntimeError("The current history marker splits semantic operation order");
        }
        finalPosition = static_cast<long>(finalBlocks.at(root).end);
    }

    std::unordered_map<const DocumentObject*, std::size_t> rootOrder;
    rootOrder.reserve(rootsInOrder.size());
    for (std::size_t index = 0; index < rootsInOrder.size(); ++index) {
        rootOrder.emplace(rootsInOrder[index].second, index);
    }
    for (const auto* candidate : finalOperations) {
        const auto* candidateRoot = semanticOperationRoot(candidate, document);
        const auto candidateOrder = rootOrder.find(candidateRoot);
        if (candidateOrder == rootOrder.end()) {
            throw Base::RuntimeError("A finalized operation has no semantic history position");
        }

        const auto replacement = replacementInputContract(const_cast<DocumentObject*>(candidate));
        for (const auto* input : replacement.inputs) {
            const auto* inputRoot = semanticOperationRoot(input, document);
            const auto inputOrder = rootOrder.find(inputRoot);
            if (!inputRoot || inputRoot == candidateRoot || inputOrder == rootOrder.end()
                || inputOrder->second >= candidateOrder->second) {
                throw Base::RuntimeError("A replacement input is not an earlier semantic operation");
            }
        }

        std::vector<const DocumentObject*> pending {candidate};
        std::unordered_set<const DocumentObject*> visited {candidate};
        while (!pending.empty()) {
            const auto* current = pending.back();
            pending.pop_back();
            for (const auto* dependency : current->getOutList()) {
                if (!dependency || !document->containsObject(dependency)
                    || dependency->getDocument() != document
                    || isStructuralTimelineLink(current, dependency)
                    || !visited.insert(dependency).second) {
                    continue;
                }
                const auto* dependencyRoot = semanticOperationRoot(dependency, document);
                if (!dependencyRoot) {
                    throw Base::RuntimeError(
                        "Finalizing history encountered a malformed "
                        "dependency"
                    );
                }
                const auto dependencyOrder = rootOrder.find(dependencyRoot);
                if (dependencyRoot != candidateRoot && dependencyOrder != rootOrder.end()
                    && dependencyOrder->second > candidateOrder->second) {
                    throw Base::RuntimeError(
                        std::string("Finalizing History consumer '")
                        + candidate->getNameInDocument() + "' (semantic root '"
                        + candidateRoot->getNameInDocument() + "', index "
                        + std::to_string(candidateOrder->second) + ") before dependency '"
                        + dependency->getNameInDocument() + "' (semantic root '"
                        + dependencyRoot->getNameInDocument() + "', index "
                        + std::to_string(dependencyOrder->second) + ")"
                    );
                }
                pending.push_back(dependency);
            }
        }
    }

    std::unordered_map<DocumentObject*, std::pair<bool, bool>> savedState;
    savedState.reserve(storedOperations.size());
    for (std::size_t index = 0; index < storedOperations.size(); ++index) {
        savedState.emplace(
            storedOperations[index],
            std::pair {
                bitAt(storedVisibility, index, storedOperations[index]->Visibility.getValue()),
                bitAt(storedSuppression, index, operationSuppressed(storedOperations[index])),
            }
        );
    }
    boost::dynamic_bitset<> finalVisibility(finalOperations.size());
    boost::dynamic_bitset<> finalSuppression(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        const auto state = savedState.find(finalOperations[index]);
        if (state == savedState.end()) {
            throw Base::RuntimeError("A finalized operation lost its accepted display state");
        }
        finalVisibility.set(index, state->second.first);
        finalSuppression.set(index, state->second.second);
    }

    // Mutation begins only after the complete final sequence, graph, state,
    // metadata contract, and semantic marker have been validated.
    ApplyingScope applying(*this);
    Operations.setValues(finalOperations);
    VisibilityAtEnd.setValues(finalVisibility);
    SuppressionAtEnd.setValues(finalSuppression);
    Position.setValue(finalPosition);
    SchemaVersion.setValue(CurrentSchemaVersion);
    if (Operations.getValues() != finalOperations || VisibilityAtEnd.getValues() != finalVisibility
        || SuppressionAtEnd.getValues() != finalSuppression || Position.getValue() != finalPosition) {
        throw Base::RuntimeError("The provisional block changed while applying its validated state");
    }

    std::erase_if(
        _provisionalEnrollments,
        [transactionId, &orderedSet](const ProvisionalEnrollment& enrollment) {
            if (enrollment.transactionId != transactionId) {
                return false;
            }
            return std::any_of(
                orderedSet.begin(),
                orderedSet.end(),
                [&enrollment](const DocumentObject* candidate) {
                    return candidate && enrollment.objectId == candidate->getID()
                        && enrollment.objectName == candidate->getNameInDocument();
                }
            );
        }
    );
    if (stagedAdoption != _stagedResourceAdoptions.end()) {
        _stagedResourceAdoptions.erase(stagedAdoption);
    }
}

void DocumentTimeline::finalizeProvisionalOperationSegmentReplacement(
    const std::vector<TimelineSegmentReplacementMapping>& mappings
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Finalizing timeline replacement segments requires one normal "
            "document and one caller-owned transaction"
        );
    }

    pruneProvisionalEnrollments();
    pruneStagedResourceAdoptions();
    pruneProvisionalInternalObjects();
    pruneStagedSegmentReplacement();
    pruneStagedResourceReconciliation();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The segment-replacement transaction is no longer active");
    }
    if (_stagedSegmentReplacements.size() != 1) {
        throw Base::RuntimeError("No exact semantic segment replacement is staged");
    }
    const auto& staged = _stagedSegmentReplacements.front();
    if (staged.transactionId != transactionId || staged.documentName != document->getName()
        || staged.documentUid != document->Uid.getValueStr()
        || mappings.size() != staged.segments.size()) {
        throw Base::RuntimeError(
            "The finalized replacement does not match the staged document, "
            "transaction, or segment count"
        );
    }

    const auto currentOperations = Operations.getValues();
    const auto currentVisibility = VisibilityAtEnd.getValues();
    const auto currentSuppression = SuppressionAtEnd.getValues();
    const long currentPosition = Position.getValue();
    if (currentVisibility.size() != currentOperations.size()
        || currentSuppression.size() != currentOperations.size() || currentPosition < 0
        || currentPosition > static_cast<long>(currentOperations.size())) {
        throw Base::RuntimeError("The current timeline state is inconsistent");
    }

    std::unordered_map<DocumentObject*, std::size_t> currentIndices;
    currentIndices.reserve(currentOperations.size());
    for (std::size_t index = 0; index < currentOperations.size(); ++index) {
        auto* candidate = currentOperations[index];
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !isOperationCandidate(candidate) || !currentIndices.emplace(candidate, index).second
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The current timeline contains a missing, duplicate, "
                "internal, or malformed operation"
            );
        }
    }

    std::unordered_map<long, std::pair<std::size_t, std::size_t>> stagedIdentityLocations;
    std::unordered_set<long> stagedIdentityIds;
    for (std::size_t segmentIndex = 0; segmentIndex < staged.segments.size(); ++segmentIndex) {
        const auto& segment = staged.segments[segmentIndex];
        for (std::size_t memberIndex = 0; memberIndex < segment.members.size(); ++memberIndex) {
            const auto& member = segment.members[memberIndex];
            if (!stagedIdentityIds.insert(member.object.objectId).second
                || !stagedIdentityLocations
                        .emplace(member.object.objectId, std::pair {segmentIndex, memberIndex})
                        .second) {
                throw Base::RuntimeError("The staged replacement contains a duplicate old identity");
            }
            if (document->getObjectByID(member.object.objectId)) {
                throw Base::RuntimeError(
                    "Every staged old identity must be deleted before "
                    "replacement finalization"
                );
            }
        }
    }

    std::vector<DocumentObject*> retainedOperations;
    retainedOperations.reserve(staged.operations.size());
    std::unordered_map<long, DocumentObject*> retainedById;
    retainedById.reserve(staged.operations.size());
    for (const auto& snapshot : staged.operations) {
        if (stagedIdentityIds.contains(snapshot.object.objectId)) {
            continue;
        }
        auto* retained = document->getObjectByID(snapshot.object.objectId);
        if (!retained || !document->containsObject(retained)
            || snapshot.object.objectName != retained->getNameInDocument()
            || retained->getDocument() != document
            || !retainedById.emplace(snapshot.object.objectId, retained).second) {
            throw Base::RuntimeError(
                "A retained timeline identity changed during segment "
                "replacement"
            );
        }
        retainedOperations.push_back(retained);
    }

    struct ValidatedMapping
    {
        std::vector<std::vector<DocumentObject*>> blocks;
        std::vector<DocumentObject*> flat;
        std::vector<long> stateSources;
        std::vector<long> consumerTargets;
        long activeRootCount {0};
    };
    std::vector<ValidatedMapping> validatedMappings(staged.segments.size());
    std::vector<bool> mappedSegments(staged.segments.size(), false);
    std::unordered_set<DocumentObject*> replacementObjects;
    std::unordered_set<DocumentObject*> replacementRoots;

    for (const auto& mapping : mappings) {
        if (mapping.stagedSegmentIndex >= staged.segments.size()
            || mappedSegments[mapping.stagedSegmentIndex]) {
            throw Base::ValueError("Each staged segment must be mapped exactly once");
        }
        mappedSegments[mapping.stagedSegmentIndex] = true;
        const auto& oldSegment = staged.segments[mapping.stagedSegmentIndex];
        auto& accepted = validatedMappings[mapping.stagedSegmentIndex];
        accepted.blocks = mapping.orderedNewBlocks;
        accepted.stateSources = mapping.stateSourceIndices;
        accepted.consumerTargets = mapping.consumerReplacementIndices;

        for (const auto& block : mapping.orderedNewBlocks) {
            if (block.empty()) {
                throw Base::ValueError("A replacement semantic block cannot be empty");
            }
            auto* root = block.back();
            if (!root || !document->containsObject(root) || root->getDocument() != document
                || !hasTimelineOperationRole(root) || hasTimelineResourceRole(root)
                || timelineOwner(root) || semanticOperationRoot(root, document) != root
                || !replacementRoots.insert(root).second) {
                throw Base::ValueError(
                    "Every replacement block must end in one distinct "
                    "explicit semantic root"
                );
            }
            for (std::size_t blockIndex = 0; blockIndex < block.size(); ++blockIndex) {
                auto* candidate = block[blockIndex];
                if (!candidate || !document->containsObject(candidate)
                    || candidate->getDocument() != document || !currentIndices.contains(candidate)
                    || !replacementObjects.insert(candidate).second
                    || !isProvisionallyEnrolledByCurrentTransaction(candidate)) {
                    throw Base::ValueError(
                        "Every replacement identity must be distinct, live, "
                        "tracked, and provisional in this transaction"
                    );
                }
                if (candidate != root
                    && (!hasTimelineResourceRole(candidate)
                        || semanticOperationRoot(candidate, document) != root)) {
                    throw Base::RuntimeError(
                        "Every non-root replacement member must be an owned "
                        "resource of its declared root"
                    );
                }
                accepted.flat.push_back(candidate);
            }
            validateCanonicalSemanticBlockOrder(
                block,
                root,
                "A replacement block is not in canonical nested resource-first, owner-last order"
            );
        }

        if (accepted.stateSources.size() != accepted.flat.size()
            || accepted.consumerTargets.size() != oldSegment.members.size()) {
            throw Base::ValueError(
                "Replacement state and consumer mappings must match their "
                "flattened segment sizes"
            );
        }
        for (const long source : accepted.stateSources) {
            if (source < -1 || source >= static_cast<long>(oldSegment.members.size())) {
                throw Base::ValueError(
                    "A replacement state source is outside its staged "
                    "segment"
                );
            }
        }

        const long oldRootCount = static_cast<long>(oldSegment.roots.size());
        const long newRootCount = static_cast<long>(accepted.blocks.size());
        if (oldSegment.activeRootCount == 0) {
            if (mapping.activeRootCount != -1) {
                throw Base::ValueError(
                    "A wholly future segment derives zero active "
                    "replacement roots"
                );
            }
            accepted.activeRootCount = 0;
        }
        else if (oldSegment.activeRootCount == oldRootCount) {
            if (mapping.activeRootCount != -1) {
                throw Base::ValueError(
                    "A wholly active segment derives all active replacement "
                    "roots"
                );
            }
            accepted.activeRootCount = newRootCount;
        }
        else {
            if (mapping.activeRootCount < 0 || mapping.activeRootCount > newRootCount) {
                throw Base::ValueError(
                    "A mixed staged segment requires an explicit valid "
                    "replacement active-root count"
                );
            }
            accepted.activeRootCount = mapping.activeRootCount;
        }

        for (std::size_t oldIndex = 0; oldIndex < oldSegment.members.size(); ++oldIndex) {
            const long target = accepted.consumerTargets[oldIndex];
            const auto& consumers = oldSegment.members[oldIndex].retainedConsumers;
            const auto& hiddenConsumers = oldSegment.members[oldIndex].retainedHiddenConsumers;
            if (target < -1 || target >= static_cast<long>(accepted.flat.size())
                || (target == -1 && (!consumers.empty() || !hiddenConsumers.empty()))) {
                throw Base::ValueError(
                    "Every consumed old identity requires one explicit live "
                    "replacement target"
                );
            }
            if (target < 0) {
                continue;
            }
            auto* replacement = accepted.flat[static_cast<std::size_t>(target)];
            for (const auto& consumerIdentity : consumers) {
                auto* consumer = resolveExactTimelineIdentity(
                    document,
                    consumerIdentity.objectId,
                    consumerIdentity.objectName
                );
                if (!consumer) {
                    throw Base::RuntimeError(
                        "A retained direct consumer was not relinked to its "
                        "explicit replacement identity"
                    );
                }
                const auto dependencies = consumer->getOutList();
                if (std::ranges::find(dependencies, replacement) == dependencies.end()) {
                    throw Base::RuntimeError(
                        "A retained direct consumer was not relinked to its "
                        "explicit replacement identity"
                    );
                }
            }
            for (const auto& hiddenConsumer : hiddenConsumers) {
                auto* consumer = resolveExactTimelineIdentity(
                    document,
                    hiddenConsumer.consumer.objectId,
                    hiddenConsumer.consumer.objectName
                );
                if (!consumer) {
                    throw Base::RuntimeError(
                        "A retained semantic consumer changed identity during replacement"
                    );
                }
                if (hiddenConsumer.kind == SegmentSnapshotMember::HiddenConsumerKind::Editor) {
                    const auto* editor = dynamic_cast<const PropertyLinkHidden*>(
                        localTimelineMetadataProperty(consumer, EditorPropertyName)
                    );
                    if (!editor || editor->getValue() != replacement) {
                        throw Base::RuntimeError(
                            "A retained editor was not relinked to its explicit replacement"
                        );
                    }
                }
                else {
                    const auto* replacements = dynamic_cast<const PropertyLinkListHidden*>(
                        localTimelineMetadataProperty(consumer, ReplacedInputsPropertyName)
                    );
                    const auto values = replacements ? replacements->getValues()
                                                     : std::vector<DocumentObject*> {};
                    if (!replacements || std::ranges::count(values, replacement) != 1) {
                        throw Base::RuntimeError(
                            "A retained replacement input was not relinked exactly once"
                        );
                    }
                }
            }
        }
    }
    if (std::ranges::any_of(mappedSegments, [](bool mapped) { return !mapped; })) {
        throw Base::RuntimeError("The staged segment mapping is incomplete");
    }

    std::vector<DocumentObject*> currentRetained;
    std::vector<DocumentObject*> currentReplacements;
    currentRetained.reserve(retainedOperations.size());
    currentReplacements.reserve(replacementObjects.size());
    for (auto* candidate : currentOperations) {
        if (replacementObjects.contains(candidate)) {
            currentReplacements.push_back(candidate);
        }
        else {
            currentRetained.push_back(candidate);
        }
    }
    if (currentRetained != retainedOperations
        || currentReplacements.size() != replacementObjects.size()
        || std::ranges::any_of(
            currentReplacements,
            [&replacementObjects](DocumentObject* candidate) {
                return !replacementObjects.contains(candidate);
            }
        )
        || currentOperations.size() != retainedOperations.size() + replacementObjects.size()) {
        throw Base::RuntimeError(
            "The current timeline is not the exact staged snapshot minus old "
            "identities plus the declared provisional replacements"
        );
    }

    std::unordered_map<DocumentObject*, long> provisionalMarkers;
    provisionalMarkers.reserve(replacementObjects.size());
    for (const auto& enrollment : _provisionalEnrollments) {
        if (enrollment.transactionId != transactionId) {
            continue;
        }
        auto* candidate = document->getObjectByID(enrollment.objectId);
        if (!candidate || enrollment.objectName != candidate->getNameInDocument()
            || enrollment.insertionMarker < 0
            || enrollment.insertionMarker >= static_cast<long>(currentOperations.size())
            || currentOperations[static_cast<std::size_t>(enrollment.insertionMarker)] != candidate) {
            throw Base::RuntimeError(
                "A provisional replacement identity moved before "
                "finalization"
            );
        }
        provisionalMarkers.emplace(candidate, enrollment.insertionMarker);
    }
    if (std::ranges::any_of(replacementObjects, [&provisionalMarkers](DocumentObject* candidate) {
            return !provisionalMarkers.contains(candidate);
        })) {
        throw Base::RuntimeError(
            "A declared replacement identity lacks exact provisional "
            "enrollment proof"
        );
    }

    long expectedCurrentPosition = 0;
    for (std::size_t index = 0; index < staged.operations.size(); ++index) {
        if (static_cast<long>(index) < staged.position
            && !stagedIdentityIds.contains(staged.operations[index].object.objectId)) {
            ++expectedCurrentPosition;
        }
    }
    expectedCurrentPosition += static_cast<long>(replacementObjects.size());
    if (currentPosition != expectedCurrentPosition) {
        throw Base::RuntimeError(
            "The current history marker changed outside the exact staged "
            "replacement"
        );
    }

    for (std::size_t snapshotIndex = 0; snapshotIndex < staged.operations.size(); ++snapshotIndex) {
        const auto& snapshot = staged.operations[snapshotIndex];
        if (stagedIdentityIds.contains(snapshot.object.objectId)) {
            continue;
        }
        auto* retained = retainedById.at(snapshot.object.objectId);
        const auto currentIndex = currentIndices.find(retained);
        if (currentIndex == currentIndices.end()
            || currentVisibility.test(currentIndex->second) != snapshot.visibility
            || currentSuppression.test(currentIndex->second) != snapshot.suppression) {
            throw Base::RuntimeError(
                "A retained operation's accepted state changed during "
                "segment replacement"
            );
        }
    }

    // A replacement block must contain the complete ownership graph for each
    // of its roots; otherwise a hidden untracked resource could survive the
    // rewrite and make the caller-declared chronology incomplete.
    for (const auto* candidate : document->getObjects()) {
        if (!candidate || !hasTimelineResourceRole(candidate)) {
            continue;
        }
        const auto* root = semanticOperationRoot(candidate, document);
        if (root && replacementRoots.contains(const_cast<DocumentObject*>(root))
            && !replacementObjects.contains(const_cast<DocumentObject*>(candidate))) {
            throw Base::RuntimeError(
                "A replacement root owns a resource absent from its declared "
                "canonical block"
            );
        }
    }

    std::unordered_map<std::size_t, std::size_t> segmentAtBegin;
    std::unordered_set<long> oldMemberIds;
    for (std::size_t segmentIndex = 0; segmentIndex < staged.segments.size(); ++segmentIndex) {
        const auto& segment = staged.segments[segmentIndex];
        if (segment.members.empty()) {
            throw Base::RuntimeError("A staged replacement segment lost all members");
        }
        const auto begin = static_cast<std::size_t>(segment.members.front().timelineIndex);
        if (!segmentAtBegin.emplace(begin, segmentIndex).second) {
            throw Base::RuntimeError("Two staged replacement segments share one history boundary");
        }
        for (const auto& member : segment.members) {
            oldMemberIds.insert(member.object.objectId);
        }
    }

    std::vector<DocumentObject*> finalOperations;
    std::vector<bool> finalVisibilityValues;
    std::vector<bool> finalSuppressionValues;
    finalOperations.reserve(retainedOperations.size() + replacementObjects.size());
    finalVisibilityValues.reserve(finalOperations.capacity());
    finalSuppressionValues.reserve(finalOperations.capacity());
    long finalPosition = 0;

    for (std::size_t snapshotIndex = 0; snapshotIndex < staged.operations.size(); ++snapshotIndex) {
        if (const auto segment = segmentAtBegin.find(snapshotIndex); segment != segmentAtBegin.end()) {
            const auto segmentIndex = segment->second;
            const auto& oldSegment = staged.segments[segmentIndex];
            const auto& replacement = validatedMappings[segmentIndex];
            std::size_t flatIndex = 0;
            for (std::size_t blockIndex = 0; blockIndex < replacement.blocks.size(); ++blockIndex) {
                for (auto* candidate : replacement.blocks[blockIndex]) {
                    finalOperations.push_back(candidate);
                    const long source = replacement.stateSources[flatIndex];
                    if (source >= 0) {
                        const auto& oldMember = oldSegment.members[static_cast<std::size_t>(source)];
                        finalVisibilityValues.push_back(oldMember.visibility);
                        finalSuppressionValues.push_back(oldMember.suppression);
                    }
                    else {
                        const auto currentIndex = currentIndices.at(candidate);
                        finalVisibilityValues.push_back(currentVisibility.test(currentIndex));
                        finalSuppressionValues.push_back(currentSuppression.test(currentIndex));
                    }
                    ++flatIndex;
                }
                if (static_cast<long>(blockIndex) < replacement.activeRootCount) {
                    finalPosition += static_cast<long>(replacement.blocks[blockIndex].size());
                }
            }
        }

        const auto& snapshot = staged.operations[snapshotIndex];
        if (oldMemberIds.contains(snapshot.object.objectId)) {
            continue;
        }
        auto* retained = retainedById.at(snapshot.object.objectId);
        finalOperations.push_back(retained);
        finalVisibilityValues.push_back(snapshot.visibility);
        finalSuppressionValues.push_back(snapshot.suppression);
        if (static_cast<long>(snapshotIndex) < staged.position) {
            ++finalPosition;
        }
    }
    if (finalOperations.size() != retainedOperations.size() + replacementObjects.size()
        || finalVisibilityValues.size() != finalOperations.size()
        || finalSuppressionValues.size() != finalOperations.size() || finalPosition < 0
        || finalPosition > static_cast<long>(finalOperations.size())) {
        throw Base::RuntimeError("The replacement could not construct one complete final timeline");
    }

    struct FinalBlock
    {
        std::size_t begin {std::numeric_limits<std::size_t>::max()};
        std::size_t end {0};
        std::size_t count {0};
        std::size_t rootIndex {std::numeric_limits<std::size_t>::max()};
    };
    std::unordered_map<DocumentObject*, std::size_t> finalIndices;
    std::unordered_map<const DocumentObject*, FinalBlock> finalBlocks;
    finalIndices.reserve(finalOperations.size());
    finalBlocks.reserve(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        auto* candidate = finalOperations[index];
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !isOperationCandidate(candidate) || !finalIndices.emplace(candidate, index).second
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The final replacement history contains a missing, duplicate, "
                "internal, or malformed identity"
            );
        }
        const auto* root = semanticOperationRoot(candidate, document);
        if (!root || hasTimelineResourceRole(root)) {
            throw Base::RuntimeError(
                "The final replacement history has an incomplete ownership "
                "graph"
            );
        }
        auto& block = finalBlocks[root];
        block.begin = std::min(block.begin, index);
        block.end = std::max(block.end, index + 1);
        ++block.count;
        if (candidate == root) {
            block.rootIndex = index;
        }
    }
    std::vector<std::pair<std::size_t, const DocumentObject*>> rootsInOrder;
    rootsInOrder.reserve(finalBlocks.size());
    for (const auto& [root, block] : finalBlocks) {
        if (!finalIndices.contains(const_cast<DocumentObject*>(root))
            || block.rootIndex == std::numeric_limits<std::size_t>::max()
            || block.rootIndex + 1 != block.end || block.end - block.begin != block.count
            || (static_cast<long>(block.begin) < finalPosition
                && finalPosition < static_cast<long>(block.end))) {
            throw Base::RuntimeError(
                "The final replacement history is not canonical at its "
                "semantic block or marker boundary"
            );
        }
        validateCanonicalSemanticBlockOrder(
            std::vector<DocumentObject*>(
                finalOperations.begin() + static_cast<std::ptrdiff_t>(block.begin),
                finalOperations.begin() + static_cast<std::ptrdiff_t>(block.end)
            ),
            root,
            "The final replacement history has a noncanonical nested semantic block"
        );
        rootsInOrder.emplace_back(block.begin, root);
    }
    std::ranges::sort(rootsInOrder);

    std::unordered_map<const DocumentObject*, std::size_t> rootOrder;
    rootOrder.reserve(rootsInOrder.size());
    for (std::size_t index = 0; index < rootsInOrder.size(); ++index) {
        rootOrder.emplace(rootsInOrder[index].second, index);
    }
    for (const auto* candidate : finalOperations) {
        const auto* candidateRoot = semanticOperationRoot(candidate, document);
        const auto candidateOrder = rootOrder.find(candidateRoot);
        if (candidateOrder == rootOrder.end()) {
            throw Base::RuntimeError("A final replacement identity has no semantic root order");
        }
        const auto replacement = replacementInputContract(const_cast<DocumentObject*>(candidate));
        for (const auto* input : replacement.inputs) {
            const auto* inputRoot = semanticOperationRoot(input, document);
            const auto inputOrder = rootOrder.find(inputRoot);
            if (!inputRoot || inputRoot == candidateRoot || inputOrder == rootOrder.end()
                || inputOrder->second >= candidateOrder->second) {
                throw Base::RuntimeError("A replacement input is not an earlier semantic operation");
            }
        }

        std::vector<const DocumentObject*> pending {candidate};
        std::unordered_set<const DocumentObject*> visited {candidate};
        while (!pending.empty()) {
            const auto* current = pending.back();
            pending.pop_back();
            for (const auto* dependency : current->getOutList()) {
                if (!dependency || !document->containsObject(dependency)
                    || dependency->getDocument() != document
                    || isStructuralTimelineLink(current, dependency)
                    || !visited.insert(dependency).second) {
                    continue;
                }
                const auto* dependencyRoot = semanticOperationRoot(dependency, document);
                if (!dependencyRoot) {
                    throw Base::RuntimeError(
                        "The final replacement contains a malformed "
                        "dependency"
                    );
                }
                const auto dependencyOrder = rootOrder.find(dependencyRoot);
                if (dependencyRoot != candidateRoot && dependencyOrder != rootOrder.end()
                    && dependencyOrder->second > candidateOrder->second) {
                    throw Base::RuntimeError("A final replacement dependency follows its consumer");
                }
                pending.push_back(dependency);
            }
        }
    }

    std::unordered_set<const DocumentObject*> finalSet(finalOperations.begin(), finalOperations.end());
    for (const auto* candidate : document->getObjects()) {
        if (!candidate || !hasTimelineResourceRole(candidate)) {
            continue;
        }
        const auto* root = semanticOperationRoot(candidate, document);
        if (root && finalSet.contains(root) && !finalSet.contains(candidate)) {
            throw Base::RuntimeError("A final semantic root owns an untracked resource");
        }
    }

    for (auto* candidate : replacementObjects) {
        const auto* roleProperty = localTimelineMetadataProperty(candidate, RolePropertyName);
        const auto* role = dynamic_cast<const PropertyString*>(roleProperty);
        if (!roleProperty || !role
            || (std::string_view(role->getValue()) != OperationRole
                && std::string_view(role->getValue()) != ResourceRole)) {
            throw Base::RuntimeError("A replacement identity has invalid role metadata");
        }
        validateCanonicalTimelineMetadataStatus(
            roleProperty,
            "Replacement role metadata is not hidden, locked, and non-recomputing"
        );
        for (const char* propertyName : {
                 OwnerPropertyName,
                 EditorPropertyName,
                 EditCommandPropertyName,
                 DeleteCommandPropertyName,
                 ReplacedInputsPropertyName,
             }) {
            if (auto* property = localTimelineMetadataProperty(candidate, propertyName)) {
                validateCanonicalTimelineMetadataStatus(
                    property,
                    "Replacement metadata is not hidden, locked, and non-recomputing"
                );
            }
        }
    }

    boost::dynamic_bitset<> finalVisibility(finalOperations.size());
    boost::dynamic_bitset<> finalSuppression(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        finalVisibility.set(index, finalVisibilityValues[index]);
        finalSuppression.set(index, finalSuppressionValues[index]);
    }

    // Mutation begins only after every identity, state, block, marker,
    // dependency, and retained-consumer condition has been validated.
    ApplyingScope applying(*this);
    Operations.setValues(finalOperations);
    VisibilityAtEnd.setValues(finalVisibility);
    SuppressionAtEnd.setValues(finalSuppression);
    Position.setValue(finalPosition);
    SchemaVersion.setValue(CurrentSchemaVersion);
    if (Operations.getValues() != finalOperations || VisibilityAtEnd.getValues() != finalVisibility
        || SuppressionAtEnd.getValues() != finalSuppression || Position.getValue() != finalPosition) {
        throw Base::RuntimeError("The validated segment replacement changed while it was applied");
    }

    std::erase_if(
        _provisionalEnrollments,
        [transactionId, &replacementObjects](const ProvisionalEnrollment& enrollment) {
            if (enrollment.transactionId != transactionId) {
                return false;
            }
            return std::ranges::any_of(
                replacementObjects,
                [&enrollment](const DocumentObject* candidate) {
                    return candidate && enrollment.objectId == candidate->getID()
                        && enrollment.objectName == candidate->getNameInDocument();
                }
            );
        }
    );
    _stagedSegmentReplacements.clear();
}

void DocumentTimeline::stageOperationResourceReconciliation(
    DocumentObject* owner,
    const std::vector<DocumentObject*>& oldResourceRoots
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Staging timeline resource reconciliation requires one normal "
            "document and one caller-owned transaction"
        );
    }
    if (!owner || !document->containsObject(owner) || owner->getDocument() != document
        || !owner->getNameInDocument()) {
        throw Base::ValueError("The resource-reconciliation owner must be live in this document");
    }

    pruneProvisionalEnrollments();
    pruneStagedResourceAdoptions();
    pruneProvisionalInternalObjects();
    pruneStagedSegmentReplacement();
    pruneStagedResourceReconciliation();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The resource-reconciliation transaction is no longer active");
    }
    if (!_stagedSegmentReplacements.empty() || !_stagedResourceAdoptions.empty()
        || !_stagedResourceReconciliations.empty()) {
        throw Base::RuntimeError(
            "Another exact timeline graph rewrite is already staged by this "
            "transaction"
        );
    }

    const auto operations = Operations.getValues();
    const auto visibility = VisibilityAtEnd.getValues();
    const auto suppression = SuppressionAtEnd.getValues();
    const long position = Position.getValue();
    if (operations.empty() || visibility.size() != operations.size()
        || suppression.size() != operations.size() || position < 0
        || position > static_cast<long>(operations.size())) {
        throw Base::RuntimeError("The timeline state is inconsistent");
    }

    std::unordered_map<DocumentObject*, std::size_t> operationIndices;
    operationIndices.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* candidate = operations[index];
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !isOperationCandidate(candidate) || !operationIndices.emplace(candidate, index).second
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The timeline contains a missing, duplicate, internal, or "
                "malformed operation"
            );
        }
    }
    const auto ownerIndex = operationIndices.find(owner);
    if (ownerIndex == operationIndices.end() || isProvisionallyEnrolledByCurrentTransaction(owner)
        || !hasTimelineOperationRole(owner) || hasTimelineResourceRole(owner)
        || timelineOwner(owner) || semanticOperationRoot(owner, document) != owner) {
        throw Base::ValueError(
            "The reconciliation owner must be one pre-existing explicit "
            "semantic root"
        );
    }

    struct LiveBlock
    {
        std::size_t begin {std::numeric_limits<std::size_t>::max()};
        std::size_t end {0};
        std::size_t count {0};
        std::size_t rootIndex {std::numeric_limits<std::size_t>::max()};
    };
    std::unordered_map<const DocumentObject*, LiveBlock> blocks;
    blocks.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* candidate = operations[index];
        const auto* root = semanticOperationRoot(candidate, document);
        if (!root || !operationIndices.contains(const_cast<DocumentObject*>(root))
            || hasTimelineResourceRole(root)) {
            throw Base::RuntimeError("The timeline contains an incomplete semantic ownership graph");
        }
        auto& block = blocks[root];
        block.begin = std::min(block.begin, index);
        block.end = std::max(block.end, index + 1);
        ++block.count;
        if (candidate == root) {
            block.rootIndex = index;
        }
    }
    for (const auto& [root, block] : blocks) {
        if (block.rootIndex == std::numeric_limits<std::size_t>::max()
            || block.rootIndex + 1 != block.end || block.end - block.begin != block.count
            || (static_cast<long>(block.begin) < position && position < static_cast<long>(block.end))) {
            throw Base::RuntimeError(
                "The timeline is not canonical at one semantic block or "
                "history boundary"
            );
        }
        validateCanonicalSemanticBlockOrder(
            std::vector<DocumentObject*>(
                operations.begin() + static_cast<std::ptrdiff_t>(block.begin),
                operations.begin() + static_cast<std::ptrdiff_t>(block.end)
            ),
            root,
            "The timeline has a noncanonical nested semantic resource block"
        );
    }

    const auto ownerBlock = blocks.find(owner);
    if (ownerBlock == blocks.end() || ownerBlock->second.rootIndex != ownerIndex->second) {
        throw Base::RuntimeError("The reconciliation owner has no exact canonical timeline block");
    }

    std::vector<DocumentObject*> allOldResources;
    std::unordered_set<DocumentObject*> allOldResourceSet;
    for (std::size_t index = ownerBlock->second.begin; index < ownerBlock->second.end; ++index) {
        auto* candidate = operations[index];
        if (candidate == owner) {
            continue;
        }
        if (!hasTimelineResourceRole(candidate) || semanticOperationRoot(candidate, document) != owner
            || isProvisionallyEnrolledByCurrentTransaction(candidate)
            || !allOldResourceSet.insert(candidate).second) {
            throw Base::RuntimeError(
                "The owner's old resource graph is incomplete, duplicate, "
                "or provisional"
            );
        }
        allOldResources.push_back(candidate);
    }

    validateCanonicalNestedResourceOrder(
        allOldResources,
        [](const DocumentObject* candidate, const DocumentObject* resource) {
            return ownerChainContains(candidate, resource);
        },
        "The owner's resource graph is not in canonical nested resource-first, owner-last order"
    );

    if (allOldResources.empty() != oldResourceRoots.empty()) {
        throw Base::ValueError(
            "The staged roots must cover the owner's complete old resource "
            "graph"
        );
    }

    std::unordered_set<DocumentObject*> selectedRoots;
    std::unordered_set<DocumentObject*> expandedResources;
    std::size_t previousSubtreeEnd = 0;
    bool firstSubtree = true;
    for (auto* root : oldResourceRoots) {
        if (!root || !document->containsObject(root) || root->getDocument() != document
            || !allOldResourceSet.contains(root) || !selectedRoots.insert(root).second
            || !hasTimelineResourceRole(root) || semanticOperationRoot(root, document) != owner
            || isProvisionallyEnrolledByCurrentTransaction(root)) {
            throw Base::ValueError(
                "Every staged resource root must be one distinct "
                "pre-existing identity in the owner's graph"
            );
        }
        for (auto* selected : selectedRoots) {
            if (selected == root) {
                continue;
            }
            if (ownerChainContains(root, selected) || ownerChainContains(selected, root)) {
                throw Base::ValueError(
                    "Staged resource roots cannot overlap through an "
                    "ancestor/descendant relationship"
                );
            }
        }

        std::size_t subtreeBegin = std::numeric_limits<std::size_t>::max();
        std::size_t subtreeEnd = 0;
        std::size_t subtreeCount = 0;
        for (auto* candidate : allOldResources) {
            if (candidate != root && !ownerChainContains(candidate, root)) {
                continue;
            }
            if (!expandedResources.insert(candidate).second) {
                throw Base::RuntimeError(
                    "Two staged resource roots expand to the same old "
                    "identity"
                );
            }
            const auto index = operationIndices.at(candidate);
            subtreeBegin = std::min(subtreeBegin, index);
            subtreeEnd = std::max(subtreeEnd, index + 1);
            ++subtreeCount;
        }
        if (subtreeBegin == std::numeric_limits<std::size_t>::max()
            || subtreeEnd - subtreeBegin != subtreeCount || operations[subtreeEnd - 1] != root
            || (!firstSubtree && subtreeBegin < previousSubtreeEnd)) {
            throw Base::RuntimeError(
                "Staged resource subtrees must be canonical, disjoint, and "
                "supplied in history order"
            );
        }
        firstSubtree = false;
        previousSubtreeEnd = subtreeEnd;
    }
    if (expandedResources != allOldResourceSet) {
        throw Base::RuntimeError(
            "The staged roots do not expand to the owner's complete old "
            "resource graph"
        );
    }

    StagedResourceReconciliation staged {
        .transactionId = transactionId,
        .documentName = document->getName(),
        .documentUid = document->Uid.getValueStr(),
        .owner = {
            .objectId = owner->getID(),
            .objectName = owner->getNameInDocument(),
        },
        .position = position,
        .ownerActive =
            static_cast<long>(ownerIndex->second) < position,
        .operations = {},
        .oldResources = {},
    };
    staged.operations.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        staged.operations.push_back(
            TimelineSnapshotOperation {
                .object = {
                    .objectId = operations[index]->getID(),
                    .objectName =
                        operations[index]->getNameInDocument(),
                },
                .visibility = visibility.test(index),
                .suppression = suppression.test(index),
            }
        );
    }
    staged.oldResources.reserve(allOldResources.size());
    for (auto* resource : allOldResources) {
        const auto index = operationIndices.at(resource);
        SegmentSnapshotMember member {
            .object = {
                .objectId = resource->getID(),
                .objectName = resource->getNameInDocument(),
            },
            .timelineIndex = static_cast<long>(index),
            .visibility = visibility.test(index),
            .suppression = suppression.test(index),
            .retainedConsumers = {},
            .retainedHiddenConsumers = {},
        };
        std::unordered_set<DocumentObject*> seenConsumers;
        for (auto* consumer : resource->getInList()) {
            if (!consumer || consumer == this || consumer == owner
                || allOldResourceSet.contains(consumer) || !document->containsObject(consumer)
                || consumer->getDocument() != document || isStructuralTimelineLink(consumer, resource)
                || !seenConsumers.insert(consumer).second) {
                continue;
            }
            member.retainedConsumers.push_back(
                TimelineObjectIdentity {
                    .objectId = consumer->getID(),
                    .objectName = consumer->getNameInDocument(),
                }
            );
        }
        for (auto* consumer : document->getObjects()) {
            if (!consumer || consumer == this || allOldResourceSet.contains(consumer)) {
                continue;
            }
            if (const auto* editorProperty
                = localTimelineMetadataProperty(consumer, EditorPropertyName)) {
                const auto* editor = dynamic_cast<const PropertyLinkHidden*>(editorProperty);
                if (!editor) {
                    throw Base::RuntimeError("A retained timeline editor property has the wrong type");
                }
                if (editor->getValue() == resource) {
                    member.retainedHiddenConsumers.push_back(
                        SegmentSnapshotMember::HiddenConsumer {
                            .consumer = {
                                .objectId = consumer->getID(),
                                .objectName = consumer->getNameInDocument(),
                            },
                            .kind = SegmentSnapshotMember::HiddenConsumerKind::Editor,
                        }
                    );
                }
            }
            if (const auto* replacementProperty
                = localTimelineMetadataProperty(consumer, ReplacedInputsPropertyName)) {
                const auto* replacements = dynamic_cast<const PropertyLinkListHidden*>(
                    replacementProperty
                );
                if (!replacements) {
                    throw Base::RuntimeError(
                        "A retained replacement-input property has the wrong type"
                    );
                }
                const auto replacementValues = replacements->getValues();
                if (std::ranges::find(replacementValues, resource) != replacementValues.end()) {
                    member.retainedHiddenConsumers.push_back(
                        SegmentSnapshotMember::HiddenConsumer {
                            .consumer = {
                                .objectId = consumer->getID(),
                                .objectName = consumer->getNameInDocument(),
                            },
                            .kind =
                                SegmentSnapshotMember::HiddenConsumerKind::ReplacedInput,
                        }
                    );
                }
            }
        }
        staged.oldResources.push_back(std::move(member));
    }

    _stagedResourceReconciliations.push_back(std::move(staged));
}

void DocumentTimeline::finalizeProvisionalOperationResourceReconciliation(
    const TimelineResourceReconciliationMapping& mapping
)
{
    auto* document = getDocument();
    if (!document || document->testStatus(Document::TempDoc)
        || document->testStatus(Document::Restoring) || document->isPerformingTransaction()
        || isApplying() || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Finalizing timeline resource reconciliation requires one normal "
            "document and one caller-owned transaction"
        );
    }

    pruneProvisionalEnrollments();
    pruneStagedResourceAdoptions();
    pruneProvisionalInternalObjects();
    pruneStagedSegmentReplacement();
    pruneStagedResourceReconciliation();
    const int transactionId = document->getBookedTransactionID();
    if (!App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError("The resource-reconciliation transaction is no longer active");
    }
    if (_stagedResourceReconciliations.size() != 1) {
        throw Base::RuntimeError("No exact resource reconciliation is staged");
    }
    const auto& staged = _stagedResourceReconciliations.front();
    auto* owner = mapping.owner;
    if (!owner || !document->containsObject(owner) || owner->getDocument() != document
        || owner->getID() != staged.owner.objectId
        || staged.owner.objectName != owner->getNameInDocument()
        || staged.transactionId != transactionId || staged.documentName != document->getName()
        || staged.documentUid != document->Uid.getValueStr()
        || isProvisionallyEnrolledByCurrentTransaction(owner) || !hasTimelineOperationRole(owner)
        || hasTimelineResourceRole(owner) || timelineOwner(owner)
        || semanticOperationRoot(owner, document) != owner) {
        throw Base::RuntimeError(
            "The finalized resource graph does not match its exact staged "
            "owner, document, or transaction"
        );
    }

    const auto currentOperations = Operations.getValues();
    const auto currentVisibility = VisibilityAtEnd.getValues();
    const auto currentSuppression = SuppressionAtEnd.getValues();
    const long currentPosition = Position.getValue();
    if (currentVisibility.size() != currentOperations.size()
        || currentSuppression.size() != currentOperations.size() || currentPosition < 0
        || currentPosition > static_cast<long>(currentOperations.size())) {
        throw Base::RuntimeError("The current timeline state is inconsistent");
    }

    std::unordered_map<DocumentObject*, std::size_t> currentIndices;
    currentIndices.reserve(currentOperations.size());
    for (std::size_t index = 0; index < currentOperations.size(); ++index) {
        auto* candidate = currentOperations[index];
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !isOperationCandidate(candidate) || !currentIndices.emplace(candidate, index).second
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The current timeline contains a missing, duplicate, "
                "internal, or malformed operation"
            );
        }
    }

    std::unordered_map<long, std::size_t> oldIndexById;
    std::unordered_set<long> oldIds;
    oldIndexById.reserve(staged.oldResources.size());
    oldIds.reserve(staged.oldResources.size());
    for (std::size_t index = 0; index < staged.oldResources.size(); ++index) {
        const auto& old = staged.oldResources[index];
        if (!oldIds.insert(old.object.objectId).second
            || !oldIndexById.emplace(old.object.objectId, index).second) {
            throw Base::RuntimeError("The staged resource graph contains a duplicate old identity");
        }
    }

    std::unordered_map<long, DocumentObject*> liveSnapshotById;
    liveSnapshotById.reserve(staged.operations.size());
    for (const auto& snapshot : staged.operations) {
        auto* live = document->getObjectByID(snapshot.object.objectId);
        if (!live) {
            if (!oldIds.contains(snapshot.object.objectId)) {
                throw Base::RuntimeError(
                    "A retained timeline identity was deleted during "
                    "resource reconciliation"
                );
            }
            continue;
        }
        if (!document->containsObject(live) || snapshot.object.objectName != live->getNameInDocument()
            || live->getDocument() != document
            || !liveSnapshotById.emplace(snapshot.object.objectId, live).second) {
            throw Base::RuntimeError(
                "A staged timeline identity changed during resource "
                "reconciliation"
            );
        }
    }
    const auto liveOwner = liveSnapshotById.find(staged.owner.objectId);
    if (liveOwner == liveSnapshotById.end() || liveOwner->second != owner) {
        throw Base::RuntimeError("The staged resource owner did not survive unchanged");
    }

    if (mapping.stateSourceIndices.size() != mapping.orderedFinalResources.size()
        || mapping.consumerReplacementIndices.size() != staged.oldResources.size()
        || (!mapping.consumerReplacementObjects.empty()
            && mapping.consumerReplacementObjects.size() != staged.oldResources.size())) {
        throw Base::ValueError(
            "Resource state and consumer mappings must match their flattened "
            "final and old graph sizes"
        );
    }
    for (const long source : mapping.stateSourceIndices) {
        if (source < -1 || source >= static_cast<long>(staged.oldResources.size())) {
            throw Base::ValueError("A resource state source is outside the staged old graph");
        }
    }

    std::unordered_set<DocumentObject*> finalResourceSet;
    std::unordered_set<DocumentObject*> retainedOldSet;
    std::unordered_set<DocumentObject*> newResourceSet;
    finalResourceSet.reserve(mapping.orderedFinalResources.size());
    for (auto* resource : mapping.orderedFinalResources) {
        if (!resource || resource == owner || !document->containsObject(resource)
            || resource->getDocument() != document || !currentIndices.contains(resource)
            || !finalResourceSet.insert(resource).second || !hasTimelineResourceRole(resource)
            || !hasValidTimelineOwnerChain(resource)
            || semanticOperationRoot(resource, document) != owner
            || !replacementInputContract(resource).valid) {
            throw Base::ValueError(
                "Every final resource must be one distinct live tracked "
                "resource of the staged owner"
            );
        }

        const auto oldIndex = oldIndexById.find(resource->getID());
        if (oldIndex != oldIndexById.end()) {
            const auto& old = staged.oldResources[oldIndex->second];
            if (old.object.objectName != resource->getNameInDocument()
                || !retainedOldSet.insert(resource).second) {
                throw Base::RuntimeError(
                    "A retained resource does not match its exact staged "
                    "identity"
                );
            }
        }
        else {
            if (!isProvisionallyEnrolledByCurrentTransaction(resource)
                || !newResourceSet.insert(resource).second) {
                throw Base::RuntimeError(
                    "A new final resource lacks exact current-transaction "
                    "provisional proof"
                );
            }
        }
    }

    validateCanonicalNestedResourceOrder(
        mapping.orderedFinalResources,
        [](const DocumentObject* candidate, const DocumentObject* resource) {
            return ownerChainContains(candidate, resource);
        },
        "The final resource graph is not in canonical nested resource-first, owner-last order"
    );

    std::unordered_set<DocumentObject*> retiredLiveSet;
    std::vector<DocumentObject*> retiredLive;
    retiredLive.reserve(staged.oldResources.size());
    for (const auto& old : staged.oldResources) {
        auto* live = document->getObjectByID(old.object.objectId);
        if (!live) {
            continue;
        }
        if (!document->containsObject(live) || old.object.objectName != live->getNameInDocument()
            || live->getDocument() != document || !currentIndices.contains(live)
            || !hasTimelineResourceRole(live) || !hasValidTimelineOwnerChain(live)
            || semanticOperationRoot(live, document) != owner) {
            throw Base::RuntimeError(
                "A live staged resource changed identity, tracking, role, "
                "or ownership"
            );
        }
        if (!finalResourceSet.contains(live)) {
            retiredLiveSet.insert(live);
            retiredLive.push_back(live);
        }
    }

    // No unstaged resource may enter or survive in the owner's semantic graph.
    for (auto* candidate : document->getObjects()) {
        if (!candidate || !hasTimelineResourceRole(candidate)
            || semanticOperationRoot(candidate, document) != owner) {
            continue;
        }
        if (!finalResourceSet.contains(candidate) && !retiredLiveSet.contains(candidate)) {
            throw Base::RuntimeError(
                "The staged owner has an undeclared resource outside the "
                "exact final or retired graph"
            );
        }
    }

    std::vector<DocumentObject*> expectedCurrentBase;
    expectedCurrentBase.reserve(staged.operations.size());
    for (const auto& snapshot : staged.operations) {
        const auto live = liveSnapshotById.find(snapshot.object.objectId);
        if (live != liveSnapshotById.end()) {
            expectedCurrentBase.push_back(live->second);
        }
    }
    std::vector<DocumentObject*> actualCurrentBase;
    std::vector<DocumentObject*> actualNewResources;
    actualCurrentBase.reserve(expectedCurrentBase.size());
    actualNewResources.reserve(newResourceSet.size());
    for (auto* candidate : currentOperations) {
        if (newResourceSet.contains(candidate)) {
            actualNewResources.push_back(candidate);
        }
        else {
            actualCurrentBase.push_back(candidate);
        }
    }
    if (actualCurrentBase != expectedCurrentBase || actualNewResources.size() != newResourceSet.size()
        || std::ranges::any_of(
            actualNewResources,
            [&newResourceSet](DocumentObject* candidate) {
                return !newResourceSet.contains(candidate);
            }
        )
        || currentOperations.size() != expectedCurrentBase.size() + newResourceSet.size()) {
        throw Base::RuntimeError(
            "The current timeline is not the staged snapshot minus deleted "
            "old resources plus the declared provisional resources"
        );
    }

    std::unordered_map<DocumentObject*, long> provisionalMarkers;
    provisionalMarkers.reserve(newResourceSet.size());
    for (const auto& enrollment : _provisionalEnrollments) {
        if (enrollment.transactionId != transactionId) {
            continue;
        }
        auto* candidate = document->getObjectByID(enrollment.objectId);
        if (!candidate || enrollment.objectName != candidate->getNameInDocument()
            || enrollment.insertionMarker < 0
            || enrollment.insertionMarker >= static_cast<long>(currentOperations.size())
            || currentOperations[static_cast<std::size_t>(enrollment.insertionMarker)] != candidate) {
            throw Base::RuntimeError(
                "A provisional resource identity moved before "
                "reconciliation"
            );
        }
        provisionalMarkers.emplace(candidate, enrollment.insertionMarker);
    }
    if (std::ranges::any_of(newResourceSet, [&provisionalMarkers](DocumentObject* candidate) {
            return !provisionalMarkers.contains(candidate);
        })) {
        throw Base::RuntimeError("A declared new resource lacks exact provisional enrollment proof");
    }

    long expectedCurrentPosition = static_cast<long>(newResourceSet.size());
    for (std::size_t index = 0; index < staged.operations.size(); ++index) {
        const auto& snapshot = staged.operations[index];
        if (static_cast<long>(index) < staged.position
            && liveSnapshotById.contains(snapshot.object.objectId)) {
            ++expectedCurrentPosition;
        }
    }
    if (currentPosition != expectedCurrentPosition) {
        throw Base::RuntimeError(
            "The history marker changed outside the staged resource "
            "reconciliation"
        );
    }

    // Non-resource history state, including the surviving owner, is
    // immutable across reconciliation. Old resource state is explicitly
    // selected by stateSourceIndices.
    for (std::size_t index = 0; index < staged.operations.size(); ++index) {
        const auto& snapshot = staged.operations[index];
        if (oldIds.contains(snapshot.object.objectId)) {
            continue;
        }
        auto* live = liveSnapshotById.at(snapshot.object.objectId);
        const auto current = currentIndices.find(live);
        if (current == currentIndices.end()
            || currentVisibility.test(current->second) != snapshot.visibility
            || currentSuppression.test(current->second) != snapshot.suppression) {
            throw Base::RuntimeError(
                std::string("Retained operation '")
                + (live->getNameInDocument()
                       ? live->getNameInDocument()
                       : "<detached>")
                + "' changed its accepted visibility or suppression during "
                  "resource reconciliation"
            );
        }
    }

    for (std::size_t oldIndex = 0; oldIndex < staged.oldResources.size(); ++oldIndex) {
        const long target = mapping.consumerReplacementIndices[oldIndex];
        const auto& old = staged.oldResources[oldIndex];
        auto* externalReplacement =
            mapping.consumerReplacementObjects.empty()
            ? nullptr
            : mapping.consumerReplacementObjects[oldIndex];
        if (target < -1 || target >= static_cast<long>(mapping.orderedFinalResources.size())
            || (externalReplacement && target != -1)
            || (!externalReplacement && target == -1
                && (!old.retainedConsumers.empty() || !old.retainedHiddenConsumers.empty()))) {
            throw Base::ValueError(
                "Every consumed old resource requires one explicit final "
                "replacement target"
            );
        }
        if (!externalReplacement && target < 0) {
            continue;
        }

        auto* replacement = externalReplacement
            ? externalReplacement
            : mapping.orderedFinalResources[static_cast<std::size_t>(target)];
        if (!replacement || !document->containsObject(replacement)
            || replacement->getDocument() != document
            || retiredLiveSet.contains(replacement)) {
            throw Base::ValueError(
                "A consumer replacement must be one exact retained object in "
                "this document"
            );
        }
        auto* oldLive
            = resolveExactTimelineIdentity(document, old.object.objectId, old.object.objectName);
        for (const auto& consumerIdentity : old.retainedConsumers) {
            auto* consumer = resolveExactTimelineIdentity(
                document,
                consumerIdentity.objectId,
                consumerIdentity.objectName
            );
            if (!consumer) {
                throw Base::RuntimeError(
                    "A retained direct consumer changed identity during "
                    "resource reconciliation"
                );
            }
            const auto dependencies = consumer->getOutList();
            if (std::ranges::find(dependencies, replacement) == dependencies.end()
                || (oldLive && replacement != oldLive
                    && std::ranges::find(dependencies, oldLive) != dependencies.end())) {
                throw Base::RuntimeError(
                    "A retained direct consumer was not relinked exactly to "
                    "its declared final resource"
                );
            }
        }
        for (const auto& hiddenConsumer : old.retainedHiddenConsumers) {
            auto* consumer = resolveExactTimelineIdentity(
                document,
                hiddenConsumer.consumer.objectId,
                hiddenConsumer.consumer.objectName
            );
            if (!consumer) {
                throw Base::RuntimeError(
                    "A retained semantic consumer changed identity during reconciliation"
                );
            }
            if (hiddenConsumer.kind == SegmentSnapshotMember::HiddenConsumerKind::Editor) {
                const auto* editor = dynamic_cast<const PropertyLinkHidden*>(
                    localTimelineMetadataProperty(consumer, EditorPropertyName)
                );
                if (!editor || editor->getValue() != replacement) {
                    throw Base::RuntimeError(
                        "A retained editor was not relinked to its declared final resource"
                    );
                }
            }
            else {
                const auto* replacements = dynamic_cast<const PropertyLinkListHidden*>(
                    localTimelineMetadataProperty(consumer, ReplacedInputsPropertyName)
                );
                const auto values = replacements ? replacements->getValues()
                                                 : std::vector<DocumentObject*> {};
                if (!replacements || std::ranges::count(values, replacement) != 1
                    || (oldLive && replacement != oldLive
                        && std::ranges::find(values, oldLive) != values.end())) {
                    throw Base::RuntimeError("A retained replacement input was not relinked exactly");
                }
            }
        }
    }

    // Retired live identities may remain temporarily so native callers can
    // delete them after finalization, but no surviving object may still
    // depend on one. The timeline's own link is the only expected consumer
    // before the atomic rewrite below.
    for (auto* retired : retiredLive) {
        for (auto* consumer : retired->getInList()) {
            if (!consumer || consumer == this || !document->containsObject(consumer)
                || consumer->getDocument() != document || retiredLiveSet.contains(consumer)) {
                continue;
            }
            throw Base::RuntimeError(
                "A surviving document object still references a retired "
                "resource"
            );
        }
    }
    for (auto* consumer : document->getObjects()) {
        if (!consumer || retiredLiveSet.contains(consumer)) {
            continue;
        }
        if (const auto* editorProperty = localTimelineMetadataProperty(consumer, EditorPropertyName)) {
            const auto* editor = dynamic_cast<const PropertyLinkHidden*>(editorProperty);
            if (!editor) {
                throw Base::RuntimeError("A surviving timeline editor property has the wrong type");
            }
            if (retiredLiveSet.contains(editor->getValue())) {
                throw Base::RuntimeError("A surviving editor still targets a retired resource");
            }
        }
        if (const auto* replacementProperty
            = localTimelineMetadataProperty(consumer, ReplacedInputsPropertyName)) {
            const auto* replacements = dynamic_cast<const PropertyLinkListHidden*>(replacementProperty);
            if (!replacements) {
                throw Base::RuntimeError("A surviving replacement-input property has the wrong type");
            }
            if (std::ranges::any_of(replacements->getValues(), [&retiredLiveSet](auto* input) {
                    return retiredLiveSet.contains(input);
                })) {
                throw Base::RuntimeError(
                    "A surviving replacement input still targets a retired resource"
                );
            }
        }
    }

    std::size_t ownerSnapshotIndex = std::numeric_limits<std::size_t>::max();
    for (std::size_t index = 0; index < staged.operations.size(); ++index) {
        const auto& snapshot = staged.operations[index];
        if (snapshot.object.objectId == staged.owner.objectId) {
            ownerSnapshotIndex = index;
        }
    }
    if (ownerSnapshotIndex == std::numeric_limits<std::size_t>::max()) {
        throw Base::RuntimeError("The staged snapshot has no surviving owner position");
    }

    std::vector<DocumentObject*> finalOperations;
    std::vector<bool> finalVisibilityValues;
    std::vector<bool> finalSuppressionValues;
    finalOperations.reserve(
        staged.operations.size() - staged.oldResources.size() + mapping.orderedFinalResources.size()
    );
    finalVisibilityValues.reserve(finalOperations.capacity());
    finalSuppressionValues.reserve(finalOperations.capacity());
    long finalPosition = 0;
    for (std::size_t snapshotIndex = 0; snapshotIndex < staged.operations.size(); ++snapshotIndex) {
        const auto& snapshot = staged.operations[snapshotIndex];
        if (oldIds.contains(snapshot.object.objectId)) {
            continue;
        }
        if (snapshotIndex == ownerSnapshotIndex) {
            for (std::size_t finalResourceIndex = 0;
                 finalResourceIndex < mapping.orderedFinalResources.size();
                 ++finalResourceIndex) {
                auto* resource = mapping.orderedFinalResources[finalResourceIndex];
                finalOperations.push_back(resource);
                const long source = mapping.stateSourceIndices[finalResourceIndex];
                if (source >= 0) {
                    const auto& old = staged.oldResources[static_cast<std::size_t>(source)];
                    finalVisibilityValues.push_back(old.visibility);
                    finalSuppressionValues.push_back(old.suppression);
                }
                else {
                    const auto current = currentIndices.at(resource);
                    finalVisibilityValues.push_back(currentVisibility.test(current));
                    finalSuppressionValues.push_back(currentSuppression.test(current));
                }
            }
            if (staged.ownerActive) {
                finalPosition += static_cast<long>(mapping.orderedFinalResources.size());
            }
        }

        auto* retained = liveSnapshotById.at(snapshot.object.objectId);
        finalOperations.push_back(retained);
        finalVisibilityValues.push_back(snapshot.visibility);
        finalSuppressionValues.push_back(snapshot.suppression);
        if (static_cast<long>(snapshotIndex) < staged.position) {
            ++finalPosition;
        }
    }
    if (finalOperations.size()
            != staged.operations.size() - staged.oldResources.size()
                + mapping.orderedFinalResources.size()
        || finalVisibilityValues.size() != finalOperations.size()
        || finalSuppressionValues.size() != finalOperations.size() || finalPosition < 0
        || finalPosition > static_cast<long>(finalOperations.size())) {
        throw Base::RuntimeError(
            "The reconciliation could not construct one complete final "
            "timeline"
        );
    }

    stableTopologicallyOrderSemanticBlocks(
        document,
        finalOperations,
        finalVisibilityValues,
        finalSuppressionValues,
        finalPosition
    );

    struct FinalBlock
    {
        std::size_t begin {std::numeric_limits<std::size_t>::max()};
        std::size_t end {0};
        std::size_t count {0};
        std::size_t rootIndex {std::numeric_limits<std::size_t>::max()};
    };
    std::unordered_map<DocumentObject*, std::size_t> finalIndices;
    std::unordered_map<const DocumentObject*, FinalBlock> finalBlocks;
    finalIndices.reserve(finalOperations.size());
    finalBlocks.reserve(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        auto* candidate = finalOperations[index];
        if (!candidate || !document->containsObject(candidate) || candidate->getDocument() != document
            || !isOperationCandidate(candidate) || !finalIndices.emplace(candidate, index).second
            || !hasValidTimelineOwnerChain(candidate) || !replacementInputContract(candidate).valid) {
            throw Base::RuntimeError(
                "The final resource reconciliation contains a missing, "
                "duplicate, internal, or malformed identity"
            );
        }
        const auto* root = semanticOperationRoot(candidate, document);
        if (!root || hasTimelineResourceRole(root)) {
            throw Base::RuntimeError(
                "The final resource reconciliation has an incomplete "
                "ownership graph"
            );
        }
        auto& block = finalBlocks[root];
        block.begin = std::min(block.begin, index);
        block.end = std::max(block.end, index + 1);
        ++block.count;
        if (candidate == root) {
            block.rootIndex = index;
        }
    }

    std::vector<std::pair<std::size_t, const DocumentObject*>> rootsInOrder;
    rootsInOrder.reserve(finalBlocks.size());
    for (const auto& [root, block] : finalBlocks) {
        if (!finalIndices.contains(const_cast<DocumentObject*>(root))
            || block.rootIndex == std::numeric_limits<std::size_t>::max()
            || block.rootIndex + 1 != block.end || block.end - block.begin != block.count
            || (static_cast<long>(block.begin) < finalPosition
                && finalPosition < static_cast<long>(block.end))) {
            throw Base::RuntimeError(
                "The final timeline is not canonical at one semantic block "
                "or history boundary"
            );
        }
        validateCanonicalSemanticBlockOrder(
            std::vector<DocumentObject*>(
                finalOperations.begin() + static_cast<std::ptrdiff_t>(block.begin),
                finalOperations.begin() + static_cast<std::ptrdiff_t>(block.end)
            ),
            root,
            "The final timeline contains a noncanonical nested semantic block"
        );
        rootsInOrder.emplace_back(block.begin, root);
    }
    std::ranges::sort(rootsInOrder);

    std::unordered_map<const DocumentObject*, std::size_t> rootOrder;
    rootOrder.reserve(rootsInOrder.size());
    for (std::size_t index = 0; index < rootsInOrder.size(); ++index) {
        rootOrder.emplace(rootsInOrder[index].second, index);
    }
    for (const auto* candidate : finalOperations) {
        const auto* candidateRoot = semanticOperationRoot(candidate, document);
        const auto candidateOrder = rootOrder.find(candidateRoot);
        if (candidateOrder == rootOrder.end()) {
            throw Base::RuntimeError("A final resource identity has no semantic root order");
        }
        const auto replacement = replacementInputContract(const_cast<DocumentObject*>(candidate));
        for (const auto* input : replacement.inputs) {
            const auto* inputRoot = semanticOperationRoot(input, document);
            const auto inputOrder = rootOrder.find(inputRoot);
            if (!inputRoot || inputRoot == candidateRoot || inputOrder == rootOrder.end()
                || inputOrder->second >= candidateOrder->second) {
                throw Base::RuntimeError(
                    "A resource replacement input is not an earlier semantic "
                    "operation"
                );
            }
        }

        std::vector<const DocumentObject*> pending {candidate};
        std::unordered_set<const DocumentObject*> visited {candidate};
        while (!pending.empty()) {
            const auto* current = pending.back();
            pending.pop_back();
            for (const auto* dependency : current->getOutList()) {
                if (!dependency || !document->containsObject(dependency)
                    || dependency->getDocument() != document
                    || isStructuralTimelineLink(current, dependency)
                    || !visited.insert(dependency).second) {
                    continue;
                }
                if (retiredLiveSet.contains(const_cast<DocumentObject*>(dependency))) {
                    throw Base::RuntimeError(
                        "A surviving final dependency still targets a "
                        "retired resource"
                    );
                }
                const auto* dependencyRoot = semanticOperationRoot(dependency, document);
                if (!dependencyRoot) {
                    throw Base::RuntimeError(
                        "The final resource graph contains a malformed "
                        "dependency"
                    );
                }
                const auto dependencyOrder = rootOrder.find(dependencyRoot);
                if (dependencyRoot != candidateRoot && dependencyOrder != rootOrder.end()
                    && dependencyOrder->second > candidateOrder->second) {
                    throw Base::RuntimeError(
                        std::string("Final History consumer '")
                        + candidate->getNameInDocument() + "' (semantic root '"
                        + candidateRoot->getNameInDocument() + "', index "
                        + std::to_string(candidateOrder->second) + ") precedes dependency '"
                        + dependency->getNameInDocument() + "' (semantic root '"
                        + dependencyRoot->getNameInDocument() + "', index "
                        + std::to_string(dependencyOrder->second) + ")"
                    );
                }
                pending.push_back(dependency);
            }
        }
    }

    std::unordered_set<const DocumentObject*> finalSet(finalOperations.begin(), finalOperations.end());
    for (const auto* candidate : document->getObjects()) {
        if (!candidate || !hasTimelineResourceRole(candidate)) {
            continue;
        }
        const auto* root = semanticOperationRoot(candidate, document);
        if (root && finalSet.contains(root) && !finalSet.contains(candidate)
            && !retiredLiveSet.contains(const_cast<DocumentObject*>(candidate))) {
            throw Base::RuntimeError("A final semantic root owns an untracked resource");
        }
    }

    const auto validateMetadata = [document](DocumentObject* candidate) {
        const auto* roleProperty = localTimelineMetadataProperty(candidate, RolePropertyName);
        const auto* role = dynamic_cast<const PropertyString*>(roleProperty);
        if (!roleProperty || !role) {
            throw Base::RuntimeError("A reconciled timeline object has no correctly typed role");
        }
        validateCanonicalTimelineMetadataStatus(
            roleProperty,
            "Reconciled role metadata is not hidden, locked, and non-recomputing"
        );
        const std::string_view roleValue(role->getValue());
        if (roleValue != OperationRole && roleValue != ResourceRole) {
            throw Base::RuntimeError("A reconciled timeline object has an unknown role");
        }

        const auto* ownerProperty = localTimelineMetadataProperty(candidate, OwnerPropertyName);
        const auto* ownerLink = dynamic_cast<const PropertyLinkHidden*>(ownerProperty);
        if (ownerProperty && !ownerLink) {
            throw Base::RuntimeError("Timeline owner metadata has the wrong property type");
        }
        if (ownerProperty) {
            validateCanonicalTimelineMetadataStatus(
                ownerProperty,
                "Reconciled owner metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (roleValue == ResourceRole) {
            const auto* ownerValue = ownerLink ? ownerLink->getValue() : nullptr;
            if (!ownerValue || ownerValue == candidate || !document->containsObject(ownerValue)
                || ownerValue->getDocument() != document) {
                throw Base::RuntimeError(
                    "A reconciled resource has no live same-document "
                    "owner"
                );
            }
        }
        else if (ownerLink && ownerLink->getValue()) {
            throw Base::RuntimeError("The reconciled operation carries a stale owner link");
        }

        const auto* editorProperty = localTimelineMetadataProperty(candidate, EditorPropertyName);
        const auto* editor = dynamic_cast<const PropertyLinkHidden*>(editorProperty);
        if (editorProperty && !editor) {
            throw Base::RuntimeError("Timeline editor metadata has the wrong property type");
        }
        if (editorProperty) {
            validateCanonicalTimelineMetadataStatus(
                editorProperty,
                "Reconciled editor metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (editor && editor->getValue() && timelineEditor(candidate) != editor->getValue()) {
            throw Base::RuntimeError("A reconciled object carries a stale editor link");
        }

        const auto* editCommandProperty
            = localTimelineMetadataProperty(candidate, EditCommandPropertyName);
        if (editCommandProperty && !dynamic_cast<const PropertyString*>(editCommandProperty)) {
            throw Base::RuntimeError(
                "Timeline edit-command metadata has the wrong property "
                "type"
            );
        }
        if (editCommandProperty) {
            validateCanonicalTimelineMetadataStatus(
                editCommandProperty,
                "Reconciled edit-command metadata is not hidden, locked, and non-recomputing"
            );
        }
        const auto* deleteCommandProperty =
            localTimelineMetadataProperty(
                candidate,
                DeleteCommandPropertyName
            );
        if (deleteCommandProperty
            && !dynamic_cast<const PropertyString*>(
                deleteCommandProperty
            )) {
            throw Base::RuntimeError(
                "Timeline delete-command metadata has the wrong property "
                "type"
            );
        }
        if (deleteCommandProperty) {
            validateCanonicalTimelineMetadataStatus(
                deleteCommandProperty,
                "Reconciled delete-command metadata is not hidden, locked, and non-recomputing"
            );
        }
        const auto* replacementProperty
            = localTimelineMetadataProperty(candidate, ReplacedInputsPropertyName);
        if (replacementProperty && !dynamic_cast<const PropertyLinkListHidden*>(replacementProperty)) {
            throw Base::RuntimeError(
                "Timeline replacement metadata has the wrong property "
                "type"
            );
        }
        if (replacementProperty) {
            validateCanonicalTimelineMetadataStatus(
                replacementProperty,
                "Reconciled replacement metadata is not hidden, locked, and non-recomputing"
            );
        }
        if (!replacementInputContract(candidate).valid) {
            throw Base::RuntimeError("Timeline replacement metadata is malformed");
        }
    };
    validateMetadata(owner);
    for (auto* resource : mapping.orderedFinalResources) {
        validateMetadata(resource);
    }
    for (auto* retired : retiredLive) {
        validateMetadata(retired);
    }

    struct RetiredMetadata
    {
        TimelineObjectIdentity object;
        TimelineObjectIdentity owner;
    };
    std::vector<RetiredMetadata> retiredMetadata;
    retiredMetadata.reserve(retiredLive.size());
    for (auto* retired : retiredLive) {
        auto* role = dynamic_cast<PropertyString*>(
            localTimelineMetadataProperty(retired, RolePropertyName)
        );
        auto* ownerLink = dynamic_cast<PropertyLinkHidden*>(
            localTimelineMetadataProperty(retired, OwnerPropertyName)
        );
        if (!role || std::string_view(role->getValue()) != ResourceRole || !ownerLink
            || !ownerLink->getValue()) {
            throw Base::RuntimeError(
                "A live retired resource has malformed role or ownership "
                "metadata"
            );
        }
        auto* declaredOwner = ownerLink->getValue();
        retiredMetadata.push_back(RetiredMetadata {
            .object =
                {
                    .objectId = retired->getID(),
                    .objectName = retired->getNameInDocument(),
                },
            .owner =
                {
                    .objectId = declaredOwner->getID(),
                    .objectName = declaredOwner->getNameInDocument(),
                },
        });
    }
    std::vector<TimelineObjectIdentity> finalOperationIdentities;
    finalOperationIdentities.reserve(finalOperations.size());
    for (auto* operation : finalOperations) {
        finalOperationIdentities.push_back(
            TimelineObjectIdentity {
                .objectId = operation->getID(),
                .objectName = operation->getNameInDocument(),
            }
        );
    }

    boost::dynamic_bitset<> finalVisibility(finalOperations.size());
    boost::dynamic_bitset<> finalSuppression(finalOperations.size());
    for (std::size_t index = 0; index < finalOperations.size(); ++index) {
        finalVisibility.set(index, finalVisibilityValues[index]);
        finalSuppression.set(index, finalSuppressionValues[index]);
    }

    // Mutation begins only after the full identity, state, consumer,
    // ownership, dependency, metadata, and history contract has passed.
    ApplyingScope applying(*this);
    for (const auto& retired : retiredMetadata) {
        auto* retiredObject = resolveExactTimelineIdentity(
            document,
            retired.object.objectId,
            retired.object.objectName
        );
        auto* retiredOwner
            = resolveExactTimelineIdentity(document, retired.owner.objectId, retired.owner.objectName);
        auto* role = retiredObject
            ? dynamic_cast<PropertyString*>(
                  localTimelineMetadataProperty(retiredObject, RolePropertyName)
              )
            : nullptr;
        auto* ownerLink = retiredObject
            ? dynamic_cast<PropertyLinkHidden*>(
                  localTimelineMetadataProperty(retiredObject, OwnerPropertyName)
              )
            : nullptr;
        if (!retiredObject || !retiredOwner || !role || !ownerLink
            || std::string_view(role->getValue()) != ResourceRole
            || ownerLink->getValue() != retiredOwner) {
            throw Base::RuntimeError(
                "A retired resource changed identity, role, or ownership "
                "before reconciliation was applied"
            );
        }
        validateCanonicalTimelineMetadataStatus(
            role,
            "Retired role metadata changed before reconciliation was applied"
        );
        validateCanonicalTimelineMetadataStatus(
            ownerLink,
            "Retired owner metadata changed before reconciliation was applied"
        );

        ownerLink->setValue(nullptr);
        retiredObject = resolveExactTimelineIdentity(
            document,
            retired.object.objectId,
            retired.object.objectName
        );
        role = retiredObject ? dynamic_cast<PropertyString*>(
                                   localTimelineMetadataProperty(retiredObject, RolePropertyName)
                               )
                             : nullptr;
        ownerLink = retiredObject
            ? dynamic_cast<PropertyLinkHidden*>(
                  localTimelineMetadataProperty(retiredObject, OwnerPropertyName)
              )
            : nullptr;
        if (!retiredObject || !role || !ownerLink || ownerLink->getValue()
            || std::string_view(role->getValue()) != ResourceRole) {
            throw Base::RuntimeError("A retired resource changed during ownership detachment");
        }
        validateCanonicalTimelineMetadataStatus(
            role,
            "Retired role metadata changed during ownership detachment"
        );
        validateCanonicalTimelineMetadataStatus(
            ownerLink,
            "Retired owner metadata changed during ownership detachment"
        );

        role->setValue(InternalRole);
        retiredObject = resolveExactTimelineIdentity(
            document,
            retired.object.objectId,
            retired.object.objectName
        );
        role = retiredObject ? dynamic_cast<PropertyString*>(
                                   localTimelineMetadataProperty(retiredObject, RolePropertyName)
                               )
                             : nullptr;
        ownerLink = retiredObject
            ? dynamic_cast<PropertyLinkHidden*>(
                  localTimelineMetadataProperty(retiredObject, OwnerPropertyName)
              )
            : nullptr;
        if (!retiredObject || !role || !ownerLink || ownerLink->getValue()
            || std::string_view(role->getValue()) != InternalRole) {
            throw Base::RuntimeError("A retired resource changed during internal classification");
        }
        validateCanonicalTimelineMetadataStatus(
            role,
            "Retired role metadata changed during internal classification"
        );
        validateCanonicalTimelineMetadataStatus(
            ownerLink,
            "Retired owner metadata changed during internal classification"
        );
    }

    std::vector<DocumentObject*> resolvedFinalOperations;
    resolvedFinalOperations.reserve(finalOperationIdentities.size());
    for (const auto& identity : finalOperationIdentities) {
        auto* operation
            = resolveExactTimelineIdentity(document, identity.objectId, identity.objectName);
        if (!operation) {
            throw Base::RuntimeError("A final timeline identity changed during resource retirement");
        }
        resolvedFinalOperations.push_back(operation);
    }
    Operations.setValues(resolvedFinalOperations);
    VisibilityAtEnd.setValues(finalVisibility);
    SuppressionAtEnd.setValues(finalSuppression);
    Position.setValue(finalPosition);
    SchemaVersion.setValue(CurrentSchemaVersion);
    for (std::size_t index = 0; index < finalOperationIdentities.size(); ++index) {
        const auto& identity = finalOperationIdentities[index];
        if (resolveExactTimelineIdentity(document, identity.objectId, identity.objectName)
            != resolvedFinalOperations[index]) {
            throw Base::RuntimeError(
                "A final timeline identity changed after reconciliation was applied"
            );
        }
    }
    for (const auto& retired : retiredMetadata) {
        auto* retiredObject = resolveExactTimelineIdentity(
            document,
            retired.object.objectId,
            retired.object.objectName
        );
        const auto* ownerLink = retiredObject
            ? dynamic_cast<const PropertyLinkHidden*>(
                  localTimelineMetadataProperty(retiredObject, OwnerPropertyName)
              )
            : nullptr;
        const auto* role = retiredObject
            ? dynamic_cast<const PropertyString*>(
                  localTimelineMetadataProperty(retiredObject, RolePropertyName)
              )
            : nullptr;
        if (!retiredObject || !ownerLink || ownerLink->getValue() || !role
            || std::string_view(role->getValue()) != InternalRole) {
            throw Base::RuntimeError("A retired resource changed after reconciliation was applied");
        }
        validateCanonicalTimelineMetadataStatus(
            role,
            "Retired role metadata changed after reconciliation was applied"
        );
        validateCanonicalTimelineMetadataStatus(
            ownerLink,
            "Retired owner metadata changed after reconciliation was applied"
        );
    }
    if (Operations.getValues() != resolvedFinalOperations
        || VisibilityAtEnd.getValues() != finalVisibility
        || SuppressionAtEnd.getValues() != finalSuppression || Position.getValue() != finalPosition) {
        throw Base::RuntimeError(
            "The validated resource reconciliation changed while it was "
            "applied"
        );
    }

    std::erase_if(
        _provisionalEnrollments,
        [transactionId, &newResourceSet](const ProvisionalEnrollment& enrollment) {
            if (enrollment.transactionId != transactionId) {
                return false;
            }
            return std::ranges::any_of(newResourceSet, [&enrollment](const DocumentObject* candidate) {
                return candidate && enrollment.objectId == candidate->getID()
                    && enrollment.objectName == candidate->getNameInDocument();
            });
        }
    );
    for (auto& provenance : _provisionalTransactionCreations) {
        if (provenance.transactionId != transactionId) {
            continue;
        }
        std::erase_if(
            provenance.objects,
            [&newResourceSet](const TimelineObjectIdentity& identity) {
                return std::ranges::any_of(
                    newResourceSet,
                    [&identity](const DocumentObject* candidate) {
                        return candidate && identity.objectId == candidate->getID()
                            && identity.objectName == candidate->getNameInDocument();
                    }
                );
            }
        );
    }
    _stagedResourceReconciliations.clear();
}

void DocumentTimeline::captureVisibility()
{
    if (isApplying()) {
        return;
    }

    const auto& operations = Operations.getValues();
    if (Position.getValue() != static_cast<long>(operations.size())) {
        return;
    }

    const auto previousVisibility = VisibilityAtEnd.getValues();
    const auto previousSuppression = SuppressionAtEnd.getValues();
    auto* document = getDocument();
    if (!document) {
        return;
    }
    const auto& documentObjects = document->getObjects();
    const std::unordered_set<const DocumentObject*> liveObjects(
        documentObjects.begin(),
        documentObjects.end()
    );
    const auto isLiveOperation = [&liveObjects](const DocumentObject* operation) {
        return liveObjects.contains(operation);
    };
    boost::dynamic_bitset<> visibility(operations.size());
    boost::dynamic_bitset<> suppression(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* operation = operations[index];
        visibility.set(
            index,
            isLiveOperation(operation) ? operation->Visibility.getValue()
                                       : bitAt(previousVisibility, index, false)
        );
        suppression.set(
            index,
            isLiveOperation(operation) ? operationSuppressed(operation)
                                       : bitAt(previousSuppression, index, false)
        );
    }

    // Resources hidden because an owner was hidden or suppressed retain their
    // own independent end-state visibility. Otherwise unsuppressing or showing
    // the owner would permanently lose the resource's accepted display state.
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* operation = operations[index];
        if (!isLiveOperation(operation) || !hasTimelineResourceRole(operation)) {
            continue;
        }
        if (!ownersPresentedAtEnd(operation, operations, previousVisibility, previousSuppression)) {
            visibility.set(index, bitAt(previousVisibility, index, operation->Visibility.getValue()));
        }
    }
    struct ResourceVisibilityTarget
    {
        TimelineObjectIdentity object;
        bool visible {false};
    };
    std::vector<ResourceVisibilityTarget> resourceVisibilityTargets;
    bool resourceVisibilityChanged = false;
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* operation = operations[index];
        if (!isLiveOperation(operation) || !hasTimelineResourceRole(operation)) {
            continue;
        }
        const bool shouldShow = bitAt(visibility, index, false)
            && ownersPresentedAtEnd(operation, operations, visibility, suppression);
        resourceVisibilityTargets.push_back(
            ResourceVisibilityTarget {
                .object = {
                    .objectId = operation->getID(),
                    .objectName = operation->getNameInDocument(),
                },
                .visible = shouldShow,
            }
        );
        resourceVisibilityChanged = resourceVisibilityChanged
            || operation->Visibility.getValue() != shouldShow;
    }
    if (visibility == VisibilityAtEnd.getValues() && suppression == SuppressionAtEnd.getValues()
        && !resourceVisibilityChanged) {
        return;
    }

    ApplyingScope applying(*this);
    VisibilityAtEnd.setValues(visibility);
    SuppressionAtEnd.setValues(suppression);
    for (const auto& target : resourceVisibilityTargets) {
        auto* operation
            = resolveExactTimelineIdentity(document, target.object.objectId, target.object.objectName);
        if (!operation || !hasTimelineResourceRole(operation)) {
            continue;
        }
        if (operation->Visibility.getValue() != target.visible) {
            operation->Visibility.setValue(target.visible);
        }
    }
}

void DocumentTimeline::setApplying(bool applying) noexcept
{
    if (applying) {
        if (_applyingDepth != std::numeric_limits<unsigned int>::max()) {
            ++_applyingDepth;
        }
    }
    else if (_applyingDepth != 0) {
        --_applyingDepth;
    }
}

void DocumentTimeline::onBeforeChange(const Property* property)
{
    const auto* document = getDocument();
    if (property == &Operations && !isApplying()
        && (!document || !document->isPerformingTransaction())) {
        _operationsBeforeChange = Operations.getValues();
        _visibilityBeforeChange = VisibilityAtEnd.getValues();
        _suppressionBeforeChange = SuppressionAtEnd.getValues();
        _positionBeforeChange = Position.getValue();
        _hasOperationsSnapshot = true;
    }
    DocumentObject::onBeforeChange(property);
}

void DocumentTimeline::onChanged(const Property* property)
{
    auto* document = getDocument();
    if (!isApplying() && document && document->isPerformingTransaction()) {
        // Undo/redo replays the controller and its operations in transaction
        // order. Defer normalization and presentation reconciliation until
        // every object has been restored or removed.
        setStatus(ObjectStatus::PendingTransactionUpdate, true);
    }
    const bool canNormalize = !isApplying() && document
        && !document->testStatus(Document::Restoring) && !document->isPerformingTransaction();

    if (canNormalize && property == &Operations) {
        reconcileOperationsChange();
    }
    else if (canNormalize && property == &Position) {
        clampPosition();
    }

    DocumentObject::onChanged(property);
}

void DocumentTimeline::reconcileOperationsChange()
{
    const auto operations = Operations.getValues();
    boost::dynamic_bitset<> visibility(operations.size());
    boost::dynamic_bitset<> suppression(operations.size());
    long boundary = std::clamp(Position.getValue(), 0L, static_cast<long>(operations.size()));

    if (_hasOperationsSnapshot) {
        std::unordered_map<DocumentObject*, std::size_t> previousIndex;
        previousIndex.reserve(_operationsBeforeChange.size());
        for (std::size_t index = 0; index < _operationsBeforeChange.size(); ++index) {
            previousIndex.emplace(_operationsBeforeChange[index], index);
        }

        boundary = 0;
        for (std::size_t index = 0; index < operations.size(); ++index) {
            auto* operation = operations[index];
            const auto previous = previousIndex.find(operation);
            if (previous != previousIndex.end()) {
                visibility.set(
                    index,
                    bitAt(
                        _visibilityBeforeChange,
                        previous->second,
                        operation && operation->Visibility.getValue()
                    )
                );
                suppression.set(
                    index,
                    bitAt(_suppressionBeforeChange, previous->second, operationSuppressed(operation))
                );
                if (static_cast<long>(previous->second) < _positionBeforeChange) {
                    ++boundary;
                }
            }
            else {
                visibility.set(index, operation && operation->Visibility.getValue());
                suppression.set(index, operationSuppressed(operation));
            }
        }
    }
    else {
        const auto oldVisibility = VisibilityAtEnd.getValues();
        const auto oldSuppression = SuppressionAtEnd.getValues();
        for (std::size_t index = 0; index < operations.size(); ++index) {
            const auto* operation = operations[index];
            visibility.set(
                index,
                bitAt(oldVisibility, index, operation && operation->Visibility.getValue())
            );
            suppression.set(index, bitAt(oldSuppression, index, operationSuppressed(operation)));
        }
    }

    _operationsBeforeChange.clear();
    _visibilityBeforeChange.clear();
    _suppressionBeforeChange.clear();
    _hasOperationsSnapshot = false;

    ApplyingScope applying(*this);
    VisibilityAtEnd.setValues(visibility);
    SuppressionAtEnd.setValues(suppression);
    Position.setValue(std::clamp(boundary, 0L, static_cast<long>(operations.size())));
}

void DocumentTimeline::clampPosition()
{
    const auto clamped = std::clamp(Position.getValue(), 0L, static_cast<long>(Operations.getSize()));
    if (clamped == Position.getValue()) {
        return;
    }

    ApplyingScope applying(*this);
    Position.setValue(clamped);
}

void DocumentTimeline::normalizeAfterRestore()
{
    normalizeStoredState(true);
}

void DocumentTimeline::normalizeStoredState(bool migrateLegacy)
{
    auto* document = getDocument();
    if (!document) {
        return;
    }
    const long storedSchemaVersion = SchemaVersion.getValue();
    if (storedSchemaVersion > CurrentSchemaVersion) {
        Base::Console().warning(
            "Document timeline schema %ld is newer than the supported schema %ld; "
            "preserving its persisted state unchanged.\n",
            storedSchemaVersion,
            CurrentSchemaVersion
        );
        return;
    }

    // Undo/redo may replay Operations while automatic reconciliation is
    // suppressed.  Never let a pre-transaction snapshot escape into a later
    // edit.
    _provisionalEnrollments.clear();
    _provisionalTransactionCreations.clear();
    _provisionalPublications.clear();
    _stagedResourceAdoptions.clear();
    _provisionalInternalObjects.clear();
    _stagedSegmentReplacements.clear();
    _stagedResourceReconciliations.clear();
    _operationsBeforeChange.clear();
    _visibilityBeforeChange.clear();
    _suppressionBeforeChange.clear();
    _hasOperationsSnapshot = false;

    auto operations = Operations.getValues();
    const auto oldVisibility = VisibilityAtEnd.getValues();
    const auto oldSuppression = SuppressionAtEnd.getValues();
    const long oldPosition = Position.getValue();
    bool migrated = false;

    if (migrateLegacy && operations.empty()) {
        std::vector<DocumentObject*> candidates;
        candidates.reserve(document->getObjects().size());
        for (auto* object : document->getObjects()) {
            if (isOperationCandidate(object)) {
                candidates.push_back(object);
            }
        }

        std::sort(candidates.begin(), candidates.end(), [](const auto* left, const auto* right) {
            if (left->getID() != right->getID()) {
                return left->getID() < right->getID();
            }
            return std::strcmp(left->getNameInDocument(), right->getNameInDocument()) < 0;
        });

        std::vector<std::vector<DocumentObject*>> activeSequences;
        std::vector<std::vector<DocumentObject*>> futureSequences;
        std::unordered_set<DocumentObject*> assigned;
        const auto bodyType = Base::Type::fromName("PartDesign::Body");

        if (!bodyType.isBad()) {
            for (auto* body : candidates) {
                if (!body->isDerivedFrom(bodyType)) {
                    continue;
                }

                std::vector<DocumentObject*> active;
                std::vector<DocumentObject*> future;
                active.push_back(body);
                assigned.insert(body);

                auto* group = dynamic_cast<PropertyLinkList*>(body->getPropertyByName("Group"));
                auto* tip = dynamic_cast<PropertyLink*>(body->getPropertyByName("Tip"));
                auto* baseFeature = dynamic_cast<PropertyLink*>(body->getPropertyByName("BaseFeature"));
                if (group) {
                    const auto& members = group->getValues();
                    const auto tipMember = tip ? tip->getValue() : nullptr;
                    const auto tipPosition = tipMember
                        ? std::find(members.begin(), members.end(), tipMember)
                        : members.end();
                    // A sketch-only Body legitimately has no result Tip.  In
                    // that case all of its existing members are active.
                    const bool boundaryBeforeGroup = tipMember && baseFeature
                        && tipMember == baseFeature->getValue();
                    const bool hasSavedBoundary = boundaryBeforeGroup || tipPosition != members.end();

                    for (auto* member : members) {
                        if (!isOperationCandidate(member) || assigned.contains(member)) {
                            continue;
                        }
                        assigned.insert(member);
                        if (boundaryBeforeGroup
                            || (hasSavedBoundary
                                && std::find(tipPosition + 1, members.end(), member)
                                    != members.end())) {
                            future.push_back(member);
                        }
                        else {
                            active.push_back(member);
                        }
                    }
                }

                activeSequences.push_back(std::move(active));
                if (!future.empty()) {
                    futureSequences.push_back(std::move(future));
                }
            }
        }

        for (auto* candidate : candidates) {
            if (!assigned.contains(candidate)) {
                activeSequences.push_back({candidate});
            }
        }

        operations.clear();
        appendStableMerge(activeSequences, operations);
        const auto activeCount = static_cast<long>(operations.size());
        appendStableMerge(futureSequences, operations);

        ApplyingScope applying(*this);
        Operations.setValues(operations);
        VisibilityAtEnd.setValues(makeVisibilityBits(operations));
        SuppressionAtEnd.setValues(makeSuppressionBits(operations));
        Position.setValue(activeCount);
        SchemaVersion.setValue(CurrentSchemaVersion);
        migrated = true;
    }

    if (migrated) {
        return;
    }

    std::vector<DocumentObject*> normalized;
    normalized.reserve(operations.size());
    boost::dynamic_bitset<> normalizedVisibility;
    boost::dynamic_bitset<> normalizedSuppression;
    std::unordered_set<DocumentObject*> seen;
    long normalizedPosition = 0;

    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* operation = operations[index];
        if (!document->containsObject(operation) || !isOperationCandidate(operation)
            || operation->getDocument() != document || !seen.insert(operation).second) {
            continue;
        }

        if (static_cast<long>(index) < oldPosition) {
            ++normalizedPosition;
        }
        normalized.push_back(operation);
        normalizedVisibility.resize(normalized.size());
        normalizedVisibility.set(
            normalized.size() - 1,
            bitAt(oldVisibility, index, operation->Visibility.getValue())
        );
        normalizedSuppression.resize(normalized.size());
        normalizedSuppression.set(
            normalized.size() - 1,
            bitAt(oldSuppression, index, operationSuppressed(operation))
        );
    }

    ApplyingScope applying(*this);
    Operations.setValues(normalized);
    VisibilityAtEnd.setValues(normalizedVisibility);
    SuppressionAtEnd.setValues(normalizedSuppression);
    Position.setValue(std::clamp(normalizedPosition, 0L, static_cast<long>(normalized.size())));
    SchemaVersion.setValue(CurrentSchemaVersion);
}

void DocumentTimeline::onUndoRedoFinished()
{
    normalizeStoredState(false);

    auto* document = getDocument();
    const auto operations = Operations.getValues();
    const auto visibility = VisibilityAtEnd.getValues();
    const auto suppression = SuppressionAtEnd.getValues();
    if (!document || Position.getValue() != static_cast<long>(operations.size())
        || visibility.size() != operations.size() || suppression.size() != operations.size()) {
        DocumentObject::onUndoRedoFinished();
        return;
    }

    struct EndStateTarget
    {
        TimelineObjectIdentity object;
        bool visible {false};
        bool suppressed {false};
    };
    std::vector<EndStateTarget> targets;
    targets.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* operation = operations[index];
        if (!operation || !document->containsObject(operation)
            || operation->getDocument() != document || !operation->getNameInDocument()) {
            continue;
        }
        targets.push_back({
            {
                operation->getID(),
                operation->getNameInDocument(),
            },
            visibility.test(index)
                && ownersPresentedAtEnd(operation, operations, visibility, suppression),
            suppression.test(index),
        });
    }

    // A removed object is reconstructed from its deletion snapshot, while the
    // parallel timeline arrays are restored independently. Reconcile those
    // two authoritative pieces only after the complete undo/redo replay.
    // This is derived state, so it must not create another undo record or
    // overwrite the saved end-of-history baseline while being applied.
    ApplyingScope applying(*this);
    for (const auto& target : targets) {
        auto* operation
            = resolveExactTimelineIdentity(document, target.object.objectId, target.object.objectName);
        if (!operation) {
            continue;
        }
        if (auto* suppressible = operation->getExtensionByType<SuppressibleExtension>(true);
            suppressible && suppressible->Suppressed.getValue() != target.suppressed) {
            suppressible->Suppressed.setValue(target.suppressed);
        }
        operation
            = resolveExactTimelineIdentity(document, target.object.objectId, target.object.objectName);
        if (operation && operation->Visibility.getValue() != target.visible) {
            operation->Visibility.setValue(target.visible);
        }
    }
    DocumentObject::onUndoRedoFinished();
}
