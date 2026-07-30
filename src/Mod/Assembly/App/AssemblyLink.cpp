// SPDX-License-Identifier: LGPL-2.1-or-later
/****************************************************************************
 *                                                                          *
 *   Copyright (c) 2024 Ondsel <development@ondsel.com>                     *
 *                                                                          *
 *   This file is part of FreeCAD.                                          *
 *                                                                          *
 *   FreeCAD is free software: you can redistribute it and/or modify it     *
 *   under the terms of the GNU Lesser General Public License as            *
 *   published by the Free Software Foundation, either version 2.1 of the   *
 *   License, or (at your option) any later version.                        *
 *                                                                          *
 *   FreeCAD is distributed in the hope that it will be useful, but         *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of             *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU       *
 *   Lesser General Public License for more details.                        *
 *                                                                          *
 *   You should have received a copy of the GNU Lesser General Public       *
 *   License along with FreeCAD. If not, see                                *
 *   <https://www.gnu.org/licenses/>.                                       *
 *                                                                          *
 ***************************************************************************/

#include <algorithm>
#include <cmath>
#include <functional>
#include <memory>
#include <optional>
#include <set>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <fastsignals/signal.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObjectGroup.h>
#include <App/DocumentTimeline.h>
#include <App/FeaturePythonPyImp.h>
#include <App/GroupExtension.h>
#include <App/Link.h>
#include <App/PropertyPythonObject.h>
#include <App/PropertyStandard.h>
#include <Base/Console.h>
#include <Base/Placement.h>
#include <Base/Rotation.h>
#include <Base/Tools.h>
#include <Base/Interpreter.h>

#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/App/TopoShape.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/Part/App/DatumFeature.h>

#include "AssemblyObject.h"
#include "AssemblyUtils.h"
#include "JointGroup.h"

#include "AssemblyLink.h"
#include "AssemblyLinkPy.h"

namespace PartApp = Part;

using namespace Assembly;

namespace
{
struct SourceIdentity
{
    std::string documentUid;
    long objectId {-1};
    std::string objectName;

    bool operator==(const SourceIdentity&) const = default;
};

int requireStructuralSynchronizationTransaction(App::Document* document)
{
    const int transactionId =
        document ? document->getBookedTransactionID()
                 : App::NullTransaction;
    if (!document
        || transactionId == App::NullTransaction
        || document->isPerformingTransaction()
        || !App::GetApplication().transactionIsActive(
            transactionId
        )) {
        throw Base::RuntimeError(
            "A tracked AssemblyLink structural change requires one active "
            "caller-owned transaction; implicit recompute cannot rewrite "
            "History"
        );
    }
    return transactionId;
}

SourceIdentity sourceIdentity(const App::DocumentObject* source)
{
    const auto* document = source ? source->getDocument() : nullptr;
    if (!source || !document || !document->containsObject(source)
        || !source->getNameInDocument()) {
        throw Base::ValueError(
            "An AssemblyLink managed resource requires one exact live source"
        );
    }
    return {
        .documentUid = document->Uid.getValueStr(),
        .objectId = source->getID(),
        .objectName = source->getNameInDocument(),
    };
}

std::optional<SourceIdentity> managedSourceIdentity(
    const App::DocumentObject* object
)
{
    if (!object) {
        return std::nullopt;
    }
    const auto* documentProperty = object->PropertyContainer::getDynamicPropertyByName(
        AssemblyLink::SourceDocumentPropertyName
    );
    const auto* idProperty = object->PropertyContainer::getDynamicPropertyByName(
        AssemblyLink::SourceObjectIdPropertyName
    );
    const auto* nameProperty = object->PropertyContainer::getDynamicPropertyByName(
        AssemblyLink::SourceObjectNamePropertyName
    );
    if (!documentProperty && !idProperty && !nameProperty) {
        return std::nullopt;
    }
    const auto* sourceDocument =
        dynamic_cast<const App::PropertyString*>(documentProperty);
    const auto* sourceId =
        dynamic_cast<const App::PropertyInteger*>(idProperty);
    const auto* sourceName =
        dynamic_cast<const App::PropertyString*>(nameProperty);
    if (!sourceDocument || !sourceId || !sourceName
        || sourceDocument->getStrValue().empty()
        || sourceId->getValue() < 0
        || sourceName->getStrValue().empty()) {
        throw Base::TypeError(
            "AssemblyLink managed-source metadata is incomplete or has "
            "incompatible types"
        );
    }
    return SourceIdentity {
        .documentUid = sourceDocument->getStrValue(),
        .objectId = sourceId->getValue(),
        .objectName = sourceName->getStrValue(),
    };
}

App::Property* ensureManagedSourceProperty(
    App::DocumentObject* object,
    const char* type,
    const char* name,
    const char* description
)
{
    auto* property = object->PropertyContainer::getDynamicPropertyByName(name);
    if (!property) {
        property = object->addDynamicProperty(
            type,
            name,
            "Assembly",
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
}

void markManagedSource(
    App::DocumentObject* resource,
    const App::DocumentObject* source
)
{
    const auto identity = sourceIdentity(source);
    const auto existing = managedSourceIdentity(resource);
    if (existing) {
        if (*existing != identity) {
            throw Base::RuntimeError(
                "An AssemblyLink managed resource cannot change its exact "
                "source identity"
            );
        }
        return;
    }

    auto* sourceDocument = dynamic_cast<App::PropertyString*>(
        ensureManagedSourceProperty(
            resource,
            "App::PropertyString",
            AssemblyLink::SourceDocumentPropertyName,
            "Exact source-document identity for this AssemblyLink resource"
        )
    );
    auto* sourceId = dynamic_cast<App::PropertyInteger*>(
        ensureManagedSourceProperty(
            resource,
            "App::PropertyInteger",
            AssemblyLink::SourceObjectIdPropertyName,
            "Exact source-object identity for this AssemblyLink resource"
        )
    );
    auto* sourceName = dynamic_cast<App::PropertyString*>(
        ensureManagedSourceProperty(
            resource,
            "App::PropertyString",
            AssemblyLink::SourceObjectNamePropertyName,
            "Exact source-object name for this AssemblyLink resource"
        )
    );
    if (!sourceDocument || !sourceId || !sourceName) {
        throw Base::TypeError(
            "AssemblyLink managed-source metadata has incompatible types"
        );
    }
    sourceDocument->setValue(identity.documentUid);
    sourceId->setValue(identity.objectId);
    sourceName->setValue(identity.objectName);
}

bool hasManagedSource(
    const App::DocumentObject* resource,
    const App::DocumentObject* source
)
{
    const auto identity = managedSourceIdentity(resource);
    return identity && *identity == sourceIdentity(source);
}

std::vector<App::DocumentObject*> managedOccurrenceResources(
    AssemblyLink* occurrence
)
{
    std::vector<App::DocumentObject*> resources;
    if (!occurrence || !occurrence->getDocument()) {
        return resources;
    }

    auto* document = occurrence->getDocument();
    std::unordered_set<App::DocumentObject*> seen {occurrence};
    std::vector<App::DocumentObject*> pending {occurrence};
    while (!pending.empty()) {
        auto* owner = pending.back();
        pending.pop_back();

        std::vector<App::DocumentObject*> children;
        if (auto* assemblyLink = freecad_cast<AssemblyLink*>(owner)) {
            const auto group = assemblyLink->Group.getValues();
            children.insert(
                children.end(),
                group.begin(),
                group.end()
            );
        }
        else if (auto* group =
                     owner->getExtensionByType<App::GroupExtension>(
                         true
                     )) {
            const auto members = group->Group.getValues();
            children.insert(
                children.end(),
                members.begin(),
                members.end()
            );
        }
        if (auto* link = freecad_cast<App::Link*>(owner);
            link && link->ElementCount.getValue() > 0) {
            const auto elements = link->ElementList.getValues();
            children.insert(
                children.end(),
                elements.begin(),
                elements.end()
            );
        }

        for (auto* child : children) {
            if (!child || child->getDocument() != document
                || !document->containsObject(child)
                || !managedSourceIdentity(child)
                || !seen.insert(child).second) {
                continue;
            }
            resources.push_back(child);
            pending.push_back(child);
        }
    }
    return resources;
}

struct OccurrenceGroupContents
{
    JointGroup* jointGroup {nullptr};
    std::unordered_set<App::DocumentObject*> jointMembers;
    std::vector<App::DocumentObject*> componentCandidates;
};

OccurrenceGroupContents classifyOccurrenceGroup(
    const std::vector<App::DocumentObject*>& members
)
{
    OccurrenceGroupContents contents;
    for (auto* member : members) {
        auto* jointGroup = freecad_cast<JointGroup*>(member);
        if (!jointGroup) {
            continue;
        }
        if (contents.jointGroup
            && contents.jointGroup != jointGroup) {
            throw Base::RuntimeError(
                "An AssemblyLink contains more than one joint group"
            );
        }
        contents.jointGroup = jointGroup;
    }
    if (contents.jointGroup) {
        const auto joints =
            contents.jointGroup->Group.getValues();
        contents.jointMembers.insert(
            joints.begin(),
            joints.end()
        );
    }
    for (auto* member : members) {
        if (!member || member == contents.jointGroup
            || contents.jointMembers.contains(member)) {
            continue;
        }
        contents.componentCandidates.push_back(member);
    }
    return contents;
}

std::vector<App::DocumentObject*> topLevelComponents(
    AssemblyObject* assembly
)
{
    if (!Assembly::isTimelineOperationActive(assembly)) {
        return {};
    }
    const auto assemblyGroup = assembly->Group.getValues();
    std::set<App::DocumentObject*> children;
    for (auto* object : assemblyGroup) {
        if (auto* feature = freecad_cast<PartApp::Feature*>(object)) {
            if (!Assembly::isTimelineOperationActive(feature)) {
                continue;
            }
            if (auto* base = freecad_cast<App::PropertyLink*>(
                    feature->getPropertyByName("Base")
                );
                base && base->getValue()) {
                children.insert(base->getValue());
            }
            if (auto* tool = freecad_cast<App::PropertyLink*>(
                    feature->getPropertyByName("Tool")
                );
                tool && tool->getValue()) {
                children.insert(tool->getValue());
            }
            if (auto* shapes =
                    freecad_cast<App::PropertyLinkList*>(
                        feature->getPropertyByName("Shapes")
                    )) {
                const auto values = shapes->getValues();
                children.insert(values.begin(), values.end());
            }
        }
    }

    std::vector<App::DocumentObject*> result;
    std::ranges::copy_if(
        assemblyGroup,
        std::back_inserter(result),
        [&children](App::DocumentObject* object) {
            if (!Assembly::isTimelineOperationActive(object)
                || children.find(object) != children.end()) {
                return false;
            }
            if (auto* link = freecad_cast<App::Link*>(object)) {
                auto* linkedObject = link->getLinkedObject(false);
                if (linkedObject
                    && linkedObject != object
                    && !Assembly::isTimelineOperationActive(
                        linkedObject
                    )) {
                    return false;
                }
            }
            return object->isDerivedFrom<App::Part>()
                || object->isDerivedFrom<PartApp::Feature>()
                || object->isDerivedFrom<App::Link>();
        }
    );
    return result;
}

bool sourceDocumentIsAtHistoryTip(
    const AssemblyObject* assembly
) noexcept
{
    try {
        const auto* document =
            assembly ? assembly->getDocument() : nullptr;
        const auto* timeline =
            App::DocumentTimeline::get(document);
        return document
            && (!timeline
                || timeline->Position.getValue()
                    == static_cast<long>(
                        timeline->Operations.getSize()
                    ));
    }
    catch (...) {
        return false;
    }
}

bool componentRepresentationMatches(
    App::DocumentObject* source,
    App::DocumentObject* local,
    bool requireManagedElements
)
{
    if (!source || !local) {
        return false;
    }
    if (source->isDerivedFrom<AssemblyLink>()) {
        auto* nested = freecad_cast<AssemblyLink*>(local);
        return nested && nested->getLinkedObject2(false) == source;
    }
    if (auto* sourceLink = freecad_cast<App::Link*>(source);
        sourceLink && sourceLink->isLinkGroup()) {
        auto* localLink = freecad_cast<App::Link*>(local);
        if (!localLink || !localLink->isLinkGroup()
            || localLink->getTrueLinkedObject(false)
                != sourceLink->getTrueLinkedObject(false)
            || localLink->ElementCount.getValue()
                != sourceLink->ElementCount.getValue()) {
            return false;
        }
        const auto sourceElements =
            sourceLink->ElementList.getValues();
        const auto localElements =
            localLink->ElementList.getValues();
        if (sourceElements.size() != localElements.size()) {
            return false;
        }
        if (requireManagedElements) {
            for (std::size_t index = 0;
                 index < sourceElements.size();
                 ++index) {
                if (!hasManagedSource(
                        localElements[index],
                        sourceElements[index]
                    )) {
                    return false;
                }
            }
        }
        return true;
    }
    auto* localLink = freecad_cast<App::Link*>(local);
    return localLink && !localLink->isLinkGroup()
        && localLink->getLinkedObject(false) == source;
}

App::DocumentObject* semanticTimelineRoot(
    App::DocumentObject* object
)
{
    std::unordered_set<App::DocumentObject*> visited;
    while (object
           && App::DocumentTimeline::hasTimelineResourceRole(
               object
           )) {
        if (!visited.insert(object).second) {
            return nullptr;
        }
        object = App::DocumentTimeline::timelineOwner(object);
    }
    return object;
}

bool isPublishedOccurrenceUsable(AssemblyLink* occurrence)
{
    const auto* document =
        occurrence ? occurrence->getDocument() : nullptr;
    return occurrence
        && document
        && document->containsObject(occurrence)
        && App::DocumentTimeline::hasTimelineOperationRole(
            occurrence
        )
        && semanticTimelineRoot(occurrence) == occurrence
        && App::DocumentTimeline::isObjectUsableAtCurrentPosition(
            occurrence
        );
}

void markTimelineResource(
    App::DocumentObject* resource,
    App::DocumentObject* owner
)
{
    if (!resource || !owner || resource == owner
        || !resource->getDocument()
        || resource->getDocument() != owner->getDocument()) {
        throw Base::ValueError(
            "An AssemblyLink timeline resource requires a distinct owner in "
            "the same document"
        );
    }
    auto* role = resource->PropertyContainer::getDynamicPropertyByName(
        App::DocumentTimeline::RolePropertyName
    );
    auto* ownerProperty = resource->PropertyContainer::getDynamicPropertyByName(
        App::DocumentTimeline::OwnerPropertyName
    );
    if (!role) {
        role = resource->addDynamicProperty(
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName,
            "Timeline",
            "Document timeline classification",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    if (!ownerProperty) {
        ownerProperty = resource->addDynamicProperty(
            "App::PropertyLinkHidden",
            App::DocumentTimeline::OwnerPropertyName,
            "Timeline",
            "Assembly occurrence which owns this synchronized resource",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    auto* resourceRole = dynamic_cast<App::PropertyString*>(role);
    auto* resourceOwner =
        dynamic_cast<App::PropertyLinkHidden*>(ownerProperty);
    if (!resourceRole || !resourceOwner) {
        throw Base::TypeError(
            "AssemblyLink timeline metadata has incompatible types"
        );
    }
    for (auto* property : {role, ownerProperty}) {
        property->setStatus(App::Property::Hidden, true);
        property->setStatus(App::Property::LockDynamic, true);
        property->setStatus(App::Property::NoRecompute, true);
    }
    if (std::string_view(resourceRole->getValue())
            == App::DocumentTimeline::ResourceRole
        && resourceOwner->getValue() == owner) {
        return;
    }
    resourceRole->setStatus(App::Property::ReadOnly, false);
    resourceOwner->setStatus(App::Property::ReadOnly, false);
    resourceOwner->setValue(owner);
    resourceRole->setValue(App::DocumentTimeline::ResourceRole);
}

void replaceRetainedConsumerLinks(
    App::DocumentObject* oldResource,
    App::DocumentObject* finalResource,
    const std::unordered_set<App::DocumentObject*>& retiringResources,
    App::DocumentObject* occurrence
)
{
    if (!oldResource || !finalResource
        || oldResource == finalResource) {
        return;
    }
    auto* document = occurrence ? occurrence->getDocument() : nullptr;
    if (!document || oldResource->getDocument() != document
        || finalResource->getDocument() != document
        || !document->containsObject(oldResource)
        || !document->containsObject(finalResource)) {
        throw Base::RuntimeError(
            "AssemblyLink consumer replacement requires two live resources "
            "in the occurrence document"
        );
    }

    const auto consumers = oldResource->getInList();
    for (auto* consumer : consumers) {
        if (!consumer || consumer == occurrence
            || consumer->isDerivedFrom<App::DocumentTimeline>()
            || consumer->getDocument() != document
            || !document->containsObject(consumer)
            || retiringResources.contains(consumer)) {
            continue;
        }
        std::vector<App::Property*> properties;
        consumer->getPropertyList(properties);
        for (auto* property : properties) {
            if (!property || property->getContainer() != consumer) {
                continue;
            }
            auto* link =
                freecad_cast<App::PropertyLinkBase*>(property);
            if (!link) {
                continue;
            }
            std::unique_ptr<App::Property> replacement(
                link->CopyOnLinkReplace(
                    consumer,
                    oldResource,
                    finalResource
                )
            );
            if (replacement) {
                property->Paste(*replacement);
            }
        }
    }
}
}  // namespace

// ================================ Assembly Object ============================

PROPERTY_SOURCE(Assembly::AssemblyLink, App::Part)

AssemblyLink::AssemblyLink()
{
    ADD_PROPERTY_TYPE(
        Rigid,
        (true),
        "General",
        (App::PropertyType)(App::Prop_None),
        "If the sub-assembly is set to Rigid, it will act "
        "as a rigid body. Else its joints will be taken into account."
    );

    ADD_PROPERTY_TYPE(
        LinkedObject,
        (nullptr),
        "General",
        (App::PropertyType)(App::Prop_None),
        "The linked assembly."
    );
}

AssemblyLink::~AssemblyLink() = default;

void AssemblyLink::installTransactionSynchronization()
{
    static fastsignals::scoped_connection transactionConnection;
    if (transactionConnection.connected()) {
        return;
    }
    transactionConnection =
        App::GetApplication()
            .signalBeforeExactTransactionClose.connect(
                &AssemblyLink::synchronizeTransactionBeforeClose
            );
}

void AssemblyLink::synchronizeTransactionBeforeClose(
    int transactionId,
    bool aborted,
    const std::vector<App::Document*>& participatingDocuments
)
{
    if (aborted || transactionId == App::NullTransaction
        || participatingDocuments.empty()) {
        return;
    }
    auto& application = App::GetApplication();
    if (!application.transactionIsActive(transactionId)) {
        throw Base::RuntimeError(
            "Assembly occurrence synchronization requires the exact source "
            "transaction to remain active"
        );
    }

    const std::unordered_set<App::Document*> initialDocuments(
        participatingDocuments.begin(),
        participatingDocuments.end()
    );
    std::unordered_set<App::Document*> affectedSourceDocuments =
        initialDocuments;
    std::unordered_set<AssemblyLink*> candidates;
    std::vector<AssemblyLink*> candidateDiscoveryOrder;
    const auto documents = application.getDocuments();

    bool discoveredDocument = true;
    while (discoveredDocument) {
        discoveredDocument = false;
        for (auto* document : documents) {
            if (!document
                || document->testStatus(App::Document::Closing)
                || document->testStatus(App::Document::Restoring)) {
                continue;
            }
            for (auto* occurrence :
                 document->getObjectsOfType<AssemblyLink>()) {
                if (!isPublishedOccurrenceUsable(occurrence)
                    || candidates.contains(occurrence)) {
                    continue;
                }
                auto* sourceAssembly =
                    occurrence->getLinkedAssembly();
                auto* sourceDocument = sourceAssembly
                    ? sourceAssembly->getDocument()
                    : occurrence->LinkedObject.getDocument();
                const bool sourceAffected =
                    sourceDocument
                    && affectedSourceDocuments.contains(
                        sourceDocument
                    );
                const bool occurrenceChangedHere =
                    initialDocuments.contains(document)
                    && occurrence->isTouched();
                if (!sourceAffected
                    && !occurrenceChangedHere) {
                    continue;
                }
                if (!occurrence->hasStructuralContentDiff()) {
                    continue;
                }
                candidates.insert(occurrence);
                candidateDiscoveryOrder.push_back(occurrence);
                if (affectedSourceDocuments.insert(document).second) {
                    discoveredDocument = true;
                }
            }
        }
    }
    if (candidates.empty()) {
        return;
    }

    std::unordered_map<AssemblyLink*, unsigned char> visitState;
    std::vector<AssemblyLink*> orderedCandidates;
    orderedCandidates.reserve(candidates.size());
    const std::function<void(AssemblyLink*)> visit =
        [&](AssemblyLink* occurrence) {
            const unsigned char state = visitState[occurrence];
            if (state == 2) {
                return;
            }
            if (state == 1) {
                throw Base::RuntimeError(
                    "Assembly occurrence synchronization found a cyclic "
                    "linked-assembly structure"
                );
            }
            visitState[occurrence] = 1;

            const auto visitDependency =
                [&](App::DocumentObject* object) {
                    auto* dependency =
                        freecad_cast<AssemblyLink*>(object);
                    if (dependency
                        && candidates.contains(dependency)) {
                        visit(dependency);
                    }
                };
            visitDependency(
                occurrence->getLinkedObject2(false)
            );
            if (auto* sourceAssembly =
                    occurrence->getLinkedAssembly()) {
                for (auto* source :
                     topLevelComponents(sourceAssembly)) {
                    visitDependency(source);
                }
            }

            visitState[occurrence] = 2;
            orderedCandidates.push_back(occurrence);
        };
    for (auto* candidate : candidateDiscoveryOrder) {
        visit(candidate);
    }

    std::unordered_set<App::Document*> changedDocuments;
    for (auto* occurrence : orderedCandidates) {
        auto* document = occurrence
            ? occurrence->getDocument()
            : nullptr;
        const char* occurrenceName = occurrence
            ? occurrence->getNameInDocument()
            : nullptr;
        if (!document || !occurrenceName
            || !document->containsObject(occurrence)
            || !isPublishedOccurrenceUsable(occurrence)) {
            throw Base::RuntimeError(
                "Assembly occurrence synchronization lost one exact "
                "published occurrence"
            );
        }
        if (document->isTransactionLocked()
            || document->transacting()) {
            throw Base::RuntimeError(
                "Assembly occurrence synchronization cannot update busy "
                "document '"
                + std::string(document->getName())
                + "'"
            );
        }
        const int bookedTransaction =
            document->getBookedTransactionID();
        if (bookedTransaction == App::NullTransaction) {
            document->openTransaction(
                application.getTransactionName(transactionId),
                transactionId
            );
        }
        if (document->getBookedTransactionID()
                != transactionId
            || !application.transactionIsActive(
                transactionId
            )) {
            throw Base::RuntimeError(
                "Assembly occurrence synchronization cannot join document '"
                + std::string(document->getName())
                + "' to the exact source transaction"
            );
        }

        occurrence->updateContents();
        if (occurrence->hasStructuralContentDiff()) {
            throw Base::RuntimeError(
                "Assembly occurrence synchronization did not converge for '"
                + occurrence->getFullName()
                + "'"
            );
        }
        changedDocuments.insert(document);
    }

    for (auto* document : documents) {
        if (!changedDocuments.contains(document)) {
            continue;
        }
        bool hasError = false;
        document->recompute({}, false, &hasError);
        if (hasError) {
            throw Base::RuntimeError(
                "Assembly occurrence synchronization failed to recompute "
                "document '"
                + std::string(document->getName())
                + "'"
            );
        }
    }
}

PyObject* AssemblyLink::getPyObject()
{
    if (PythonObject.is(Py::_None())) {
        // ref counter is set to 1
        PythonObject = Py::Object(new AssemblyLinkPy(this), true);
    }
    return Py::new_reference_to(PythonObject);
}

App::DocumentObjectExecReturn* AssemblyLink::execute()
{
    refreshContentsDuringExecution();

    return App::Part::execute();
}

void AssemblyLink::onBeforeChange(const App::Property* prop)
{
    if ((prop == &Rigid || prop == &LinkedObject)
        && !App::GetApplication().isRestoring()) {
        auto* document = getDocument();
        const bool tracked =
            App::DocumentTimeline::hasTimelineOperationRole(this)
            || App::DocumentTimeline::hasTimelineResourceRole(this);
        const auto* linkedObject =
            prop == &LinkedObject ? LinkedObject.getValue() : nullptr;
        const bool linkedDocumentClosing =
            linkedObject
            && linkedObject->getDocument()
            && linkedObject->getDocument()->testStatus(
                App::Document::Closing
            );
        const bool liveModelEdit =
            document
            && document->containsObject(this)
            && !document->testStatus(App::Document::Closing)
            && !linkedDocumentClosing
            && App::GetApplication().getDocument(document->getName()) == document
            && !testStatus(App::ObjectStatus::Remove)
            && !testStatus(App::ObjectStatus::Destroy);
        if (liveModelEdit && tracked
            && !document->isPerformingTransaction()) {
            auto* semanticOwner = semanticTimelineRoot(this);
            const int transactionId =
                document->getBookedTransactionID();
            if (!Assembly::isTimelineOperationActive(
                    semanticOwner
                )
                || transactionId == App::NullTransaction
                || !App::GetApplication().transactionIsActive(
                    transactionId
                )) {
                throw Base::RuntimeError(
                    "Editing tracked AssemblyLink '"
                    + getFullName()
                    + "' property '"
                    + prop->getName()
                    + "' requires its exact active History operation and "
                      "document transaction"
                );
            }
        }
    }
    App::Part::onBeforeChange(prop);
}

void AssemblyLink::onChanged(const App::Property* prop)
{
    if (App::GetApplication().isRestoring()) {
        App::Part::onChanged(prop);
        return;
    }

    if (prop == &Rigid) {
        auto* document = getDocument();
        if (!document || document->isPerformingTransaction()) {
            App::Part::onChanged(prop);
            return;
        }
        const bool trackedOperation =
            App::DocumentTimeline::hasTimelineOperationRole(this);
        const bool trackedResource =
            App::DocumentTimeline::hasTimelineResourceRole(this);
        if (trackedOperation || trackedResource) {
            auto* semanticOwner = trackedResource
                ? semanticTimelineRoot(this)
                : static_cast<App::DocumentObject*>(this);
            if (!Assembly::isTimelineOperationActive(
                    semanticOwner
                )) {
                App::Part::onChanged(prop);
                return;
            }
            const int transactionId =
                document->getBookedTransactionID();
            if (transactionId == App::NullTransaction
                || !App::GetApplication().transactionIsActive(
                    transactionId
                )) {
                throw Base::RuntimeError(
                    "Changing a tracked AssemblyLink requires one active "
                    "transaction in its exact document"
                );
            }
        }
        auto* parentAssembly = getParentAssembly();
        if (!Assembly::isTimelineOperationActive(parentAssembly)
            || parentAssembly->getDocument() != document
            || !parentAssembly->hasObject(this, true)) {
            throw Base::RuntimeError(
                "Changing AssemblyLink rigidity requires one exact active "
                "parent assembly"
            );
        }
        if (auto* linkedAssembly = getLinkedAssembly();
            linkedAssembly
            && !Assembly::isTimelineOperationActive(
                linkedAssembly
            )) {
            throw Base::RuntimeError(
                "Changing AssemblyLink rigidity requires an active linked "
                "assembly"
            );
        }
        Base::Placement movePlc;

        // A flexible sub-assembly cannot be grounded.
        // If a rigid sub-assembly has an object that is grounded, we also remove it.
        auto groundedJoints =
            parentAssembly->getGroundedJoints();
        for (auto* joint : groundedJoints) {
            auto* propObj = dynamic_cast<App::PropertyLink*>(
                joint->getPropertyByName("ObjectToGround")
            );
            if (!propObj) {
                continue;
            }
            auto* groundedObj = propObj->getValue();
            if (auto* linkElt = dynamic_cast<App::LinkElement*>(groundedObj)) {
                // hasObject does not handle link groups so we must handle it manually.
                groundedObj = linkElt->getLinkGroup();
            }

            if (Rigid.getValue() ? hasObject(groundedObj) : groundedObj == this) {
                getDocument()->removeObject(joint->getNameInDocument());
            }
        }

        if (Rigid.getValue()) {
            // movePlc needs to be computed before updateContents.
            App::DocumentObject* firstLink = nullptr;
            for (auto* obj : Group.getValues()) {
                if (Assembly::isTimelineOperationActive(
                        semanticTimelineRoot(obj)
                    )
                    && (obj->isDerivedFrom<App::Link>()
                        || obj->isDerivedFrom<AssemblyLink>())) {
                    firstLink = obj;
                    break;
                }
            }

            if (firstLink) {
                App::DocumentObject* sourceObj = nullptr;
                if (auto* link = dynamic_cast<App::Link*>(firstLink)) {
                    sourceObj = link->getLinkedObject(false);  // Get non-recursive linked object
                }
                else if (auto* asmLink = dynamic_cast<AssemblyLink*>(firstLink)) {
                    sourceObj = asmLink->getLinkedAssembly();
                }

                if (Assembly::isTimelineOperationActive(sourceObj)) {
                    auto* propSource = dynamic_cast<App::PropertyPlacement*>(
                        sourceObj->getPropertyByName("Placement")
                    );
                    auto* propLink = dynamic_cast<App::PropertyPlacement*>(
                        firstLink->getPropertyByName("Placement")
                    );

                    if (propSource && propLink) {
                        movePlc = propLink->getValue() * propSource->getValue().inverse();
                    }
                }
            }
        }

        updateContents();

        auto* propPlc = dynamic_cast<App::PropertyPlacement*>(getPropertyByName("Placement"));
        if (!propPlc) {
            return;
        }

        if (!Rigid.getValue()) {
            // when the assemblyLink becomes flexible, we need to make sure its placement is
            // identity or it's going to mess up moving parts placement within.
            Base::Placement plc = propPlc->getValue();
            if (!plc.isIdentity()) {
                propPlc->setValue(Base::Placement());

                // We need to apply the placement of the assembly link to the children or they will
                // move.
                std::vector<App::DocumentObject*> group = Group.getValues();
                for (auto* obj : group) {
                    if (!Assembly::isTimelineOperationActive(
                            semanticTimelineRoot(obj)
                        )
                        || (!obj->isDerivedFrom<App::Part>()
                            && !obj->isDerivedFrom<PartApp::Feature>()
                            && !obj->isDerivedFrom<App::Link>())) {
                        continue;
                    }

                    if (obj->isLinkGroup()) {
                        auto* srcLink = static_cast<App::Link*>(obj);
                        const std::vector<App::DocumentObject*> srcElements
                            = srcLink->ElementList.getValues();

                        for (auto elt : srcElements) {
                            if (!Assembly::isTimelineOperationActive(
                                    semanticTimelineRoot(elt)
                                )) {
                                continue;
                            }

                            auto* prop = dynamic_cast<App::PropertyPlacement*>(
                                elt->getPropertyByName("Placement")
                            );
                            if (prop) {
                                prop->setValue(plc * prop->getValue());
                            }
                        }
                    }
                    else {
                        auto* prop = dynamic_cast<App::PropertyPlacement*>(
                            obj->getPropertyByName("Placement")
                        );
                        if (prop) {
                            prop->setValue(plc * prop->getValue());
                        }
                    }
                }

                AssemblyObject::redrawJointPlacements(getJoints());
            }
        }
        else {
            // For the assemblylink not to move to origin, we need to update its placement.
            if (!movePlc.isIdentity()) {
                propPlc->setValue(movePlc);
            }
        }
        updateParentJoints();
        return;
    }
    App::Part::onChanged(prop);
}

void AssemblyLink::updateParentJoints()
{
    AssemblyObject* parent = getParentAssembly();
    if (!Assembly::isTimelineOperationActive(
            semanticTimelineRoot(this)
        )
        || !Assembly::isTimelineOperationActive(parent)) {
        return;
    }

    bool rigid = Rigid.getValue();
    // Iterate joints in the immediate parent assembly only (recursive=false)
    for (auto* joint : parent->getJoints(false, false)) {
        for (const char* refName : {"Reference1", "Reference2"}) {
            auto* prop = dynamic_cast<App::PropertyXLinkSub*>(joint->getPropertyByName(refName));
            if (!prop) {
                continue;
            }
            App::DocumentObject* refObj = prop->getValue();
            if (!refObj) {
                continue;
            }

            if (rigid) {  // Flexible -> Rigid
                if (hasObject(refObj)) {
                    // The joint currently points to a child (refObj) inside this AssemblyLink.
                    // We must repoint it to 'this' and prepend the child's name to the sub-elements.
                    std::vector<std::string> subs = prop->getSubValues();
                    std::vector<std::string> newSubs;
                    std::string prefix = refObj->getNameInDocument();
                    prefix += ".";
                    for (const auto& s : subs) {
                        newSubs.push_back(prefix + s);
                    }
                    prop->setValue(this);
                    prop->setSubValues(std::move(newSubs));
                }
            }
            else {  // Rigid -> Flexible
                if (refObj == this) {
                    // The joint currently points to 'this'.
                    // We must extract the child's name from the sub-element, point to the child,
                    // and strip the prefix.
                    std::vector<std::string> subs = prop->getSubValues();
                    if (subs.empty()) {
                        continue;
                    }
                    std::vector<std::string> parts = Base::Tools::splitSubName(subs[0]);
                    if (parts.empty()) {
                        continue;
                    }
                    std::string childName = parts[0];
                    App::DocumentObject* child = getDocument()->getObject(childName.c_str());
                    if (child && hasObject(child)) {
                        std::vector<std::string> newSubs;
                        size_t prefixLen = childName.length() + 1;  // "Name."
                        for (const auto& s : subs) {
                            if (s.length() >= prefixLen) {
                                newSubs.push_back(s.substr(prefixLen));
                            }
                            else {
                                newSubs.push_back(s);
                            }
                        }
                        prop->setValue(child);
                        prop->setSubValues(std::move(newSubs));
                    }
                }
            }
        }
        if (joint->isTouched()) {
            joint->recomputeFeature();
        }
    }
}

void AssemblyLink::updateContentsUnchecked()
{
    if (auto* assembly = getLinkedAssembly();
        assembly && !sourceDocumentIsAtHistoryTip(assembly)) {
        return;
    }
    synchronizeComponents();

    if (!getLinkedAssembly() || isRigid()) {
        ensureNoJointGroup();
    }
    else {
        synchronizeJoints();
    }
    purgeTouched();
}

void AssemblyLink::rebaseAfterSameDocumentSources()
{
    auto* document = getDocument();
    auto* timeline = App::DocumentTimeline::get(document);
    auto* assembly = getLinkedAssembly();
    if (!document || !timeline || !assembly
        || assembly->getDocument() != document) {
        return;
    }

    const auto operations = timeline->Operations.getValues();
    const auto occurrencePosition =
        std::ranges::find(operations, this);
    if (occurrencePosition == operations.end()) {
        throw Base::RuntimeError(
            "A tracked AssemblyLink occurrence is missing from its "
            "document History"
        );
    }
    const auto occurrenceIndex = static_cast<std::size_t>(
        std::distance(operations.begin(), occurrencePosition)
    );

    std::vector<App::DocumentObject*> sourceObjects {
        assembly
    };
    const auto sourceComponents = topLevelComponents(assembly);
    sourceObjects.insert(
        sourceObjects.end(),
        sourceComponents.begin(),
        sourceComponents.end()
    );
    const auto sourceJoints =
        assembly->getJoints(false, false);
    sourceObjects.insert(
        sourceObjects.end(),
        sourceJoints.begin(),
        sourceJoints.end()
    );
    if (auto* sourceJointGroup = assembly->getJointGroup()) {
        sourceObjects.push_back(sourceJointGroup);
    }

    App::DocumentObject* latestSourceRoot = nullptr;
    std::size_t latestSourceIndex = occurrenceIndex;
    std::unordered_set<App::DocumentObject*> visitedRoots;
    for (auto* source : sourceObjects) {
        auto* sourceRoot = semanticTimelineRoot(source);
        if (!sourceRoot || sourceRoot == this
            || sourceRoot->getDocument() != document
            || !visitedRoots.insert(sourceRoot).second) {
            continue;
        }
        const auto sourcePosition =
            std::ranges::find(operations, sourceRoot);
        if (sourcePosition == operations.end()) {
            continue;
        }
        const auto sourceIndex = static_cast<std::size_t>(
            std::distance(operations.begin(), sourcePosition)
        );
        if (sourceIndex > latestSourceIndex) {
            latestSourceIndex = sourceIndex;
            latestSourceRoot = sourceRoot;
        }
    }
    if (latestSourceRoot) {
        timeline->reorderOperationDependentClosureAfter(
            this,
            latestSourceRoot
        );
    }
}

void AssemblyLink::refreshContentsDuringExecution()
{
    if (_resourceReconciliationActive) {
        return;
    }

    auto* document = getDocument();
    auto* timeline = App::DocumentTimeline::get(document);
    auto* linkedAssembly = getLinkedAssembly();
    if (linkedAssembly
        && (!Assembly::isTimelineOperationActive(linkedAssembly)
            || !sourceDocumentIsAtHistoryTip(linkedAssembly))) {
        return;
    }
    if (!document || !timeline
        || !App::DocumentTimeline::hasTimelineOperationRole(this)) {
        updateContentsUnchecked();
        return;
    }
    if (!isPublishedOccurrenceUsable(this)) {
        return;
    }
    if (hasStructuralContentDiff()) {
        const int transactionId =
            document->getBookedTransactionID();
        if (transactionId != App::NullTransaction
            && !document->isPerformingTransaction()
            && App::GetApplication().transactionIsActive(
                transactionId
            )) {
            updateContents();
        }
        return;
    }

    Base::StateLocker valueRefresh(_valueRefreshOnly);
    updateContentsUnchecked();
}

bool AssemblyLink::hasStructuralContentDiff() const
{
    auto* assembly = getLinkedAssembly();
    if (assembly
        && (!Assembly::isTimelineOperationActive(assembly)
            || !sourceDocumentIsAtHistoryTip(assembly))) {
        return false;
    }
    const auto localGroup = Group.getValues();

    const auto localContents =
        classifyOccurrenceGroup(localGroup);
    auto* localJointGroup = localContents.jointGroup;
    std::vector<App::DocumentObject*> managedComponents;
    for (auto* candidate :
         localContents.componentCandidates) {
        if (managedSourceIdentity(candidate)) {
            managedComponents.push_back(candidate);
        }
    }

    if (!assembly) {
        if (localJointGroup
            && !managedSourceIdentity(localJointGroup)) {
            throw Base::RuntimeError(
                "A tracked AssemblyLink cannot reinterpret an unmarked joint "
                "group as native synchronization state"
            );
        }
        return !managedComponents.empty() || localJointGroup;
    }

    const auto sourceComponents = topLevelComponents(assembly);
    std::unordered_set<App::DocumentObject*> matchedComponents;
    for (auto* source : sourceComponents) {
        App::DocumentObject* match = nullptr;
        for (auto* candidate : managedComponents) {
            if (!hasManagedSource(candidate, source)) {
                continue;
            }
            if (match) {
                throw Base::RuntimeError(
                    "An AssemblyLink contains duplicate managed component "
                    "resources for one exact source"
                );
            }
            match = candidate;
        }
        if (!match
            || !componentRepresentationMatches(source, match, true)) {
            return true;
        }
        if (auto* sourceNested =
                freecad_cast<AssemblyLink*>(source);
            sourceNested) {
            auto* localNested =
                freecad_cast<AssemblyLink*>(match);
            if (!localNested
                || localNested->Rigid.getValue()
                    != sourceNested->Rigid.getValue()) {
                return true;
            }
        }
        matchedComponents.insert(match);
        if (auto* nested = freecad_cast<AssemblyLink*>(match);
            nested && nested->hasStructuralContentDiff()) {
            return true;
        }
    }
    if (matchedComponents.size() != managedComponents.size()) {
        return true;
    }

    if (isRigid()) {
        if (!localJointGroup) {
            return false;
        }
        if (!managedSourceIdentity(localJointGroup)) {
            throw Base::RuntimeError(
                "A tracked AssemblyLink cannot reinterpret an unmarked joint "
                "group as native synchronization state"
            );
        }
        return true;
    }

    auto* sourceJointGroup = assembly->getJointGroup();
    auto* jointGroupSource = sourceJointGroup
        ? static_cast<App::DocumentObject*>(sourceJointGroup)
        : static_cast<App::DocumentObject*>(assembly);
    if (!localJointGroup) {
        return true;
    }
    if (!managedSourceIdentity(localJointGroup)) {
        throw Base::RuntimeError(
            "A tracked AssemblyLink cannot reinterpret an unmarked joint "
            "group as native synchronization state"
        );
    }
    if (!hasManagedSource(localJointGroup, jointGroupSource)) {
        return true;
    }

    const auto sourceJoints = assembly->getJoints(false, false);
    std::vector<App::DocumentObject*> managedLocalJoints;
    for (auto* candidate : localJointGroup->Group.getValues()) {
        if (candidate && managedSourceIdentity(candidate)) {
            managedLocalJoints.push_back(candidate);
        }
    }
    std::unordered_set<App::DocumentObject*> matchedJoints;
    for (auto* sourceJoint : sourceJoints) {
        App::DocumentObject* match = nullptr;
        for (auto* candidate : managedLocalJoints) {
            if (!hasManagedSource(candidate, sourceJoint)) {
                continue;
            }
            if (match) {
                throw Base::RuntimeError(
                    "An AssemblyLink contains duplicate managed joint "
                    "resources for one exact source"
                );
            }
            match = candidate;
        }
        if (!match) {
            return true;
        }
        matchedJoints.insert(match);
    }
    return matchedJoints.size() != managedLocalJoints.size();
}

void AssemblyLink::updateContents()
{
    if (_resourceReconciliationActive) {
        return;
    }

    auto* document = getDocument();
    auto* timeline = App::DocumentTimeline::get(document);
    auto* linkedAssembly = getLinkedAssembly();
    if (linkedAssembly
        && (!Assembly::isTimelineOperationActive(linkedAssembly)
            || !sourceDocumentIsAtHistoryTip(linkedAssembly))) {
        return;
    }
    if (!document || !timeline
        || !App::DocumentTimeline::hasTimelineOperationRole(this)) {
        updateContentsUnchecked();
        return;
    }
    if (!isPublishedOccurrenceUsable(this)) {
        return;
    }

    if (!hasStructuralContentDiff()) {
        Base::StateLocker valueRefresh(_valueRefreshOnly);
        updateContentsUnchecked();
        return;
    }

    requireStructuralSynchronizationTransaction(document);
    rebaseAfterSameDocumentSources();

    std::vector<App::DocumentObject*> oldManagedResources;
    for (auto* candidate : timeline->Operations.getValues()) {
        if (candidate && candidate != this
            && semanticTimelineRoot(candidate) == this
            && managedSourceIdentity(candidate)) {
            oldManagedResources.push_back(candidate);
        }
    }
    synchronizeContentsWithResourceMap(oldManagedResources);
}

void AssemblyLink::recordResourceReplacement(
    App::DocumentObject* oldResource,
    App::DocumentObject* finalResource
)
{
    if (!_resourceReplacementTrace || !oldResource || !finalResource
        || oldResource == finalResource) {
        return;
    }
    _resourceReplacementTrace->insert_or_assign(
        oldResource,
        finalResource
    );
}

void AssemblyLink::recordResourceRetirement(
    App::DocumentObject* oldResource
)
{
    if (!_resourceRetirementTrace || !oldResource) {
        return;
    }
    if (std::ranges::find(
            *_resourceRetirementTrace,
            oldResource
        )
        == _resourceRetirementTrace->end()) {
        _resourceRetirementTrace->push_back(oldResource);
    }
}

AssemblyLinkSynchronizationResult
AssemblyLink::synchronizeContentsWithResourceMapUnchecked(
    const std::vector<App::DocumentObject*>& orderedOldResources
)
{
    auto* document = getDocument();
    if (!document || !document->containsObject(this)) {
        throw Base::RuntimeError(
            "AssemblyLink synchronization requires one live occurrence"
        );
    }
    if (_resourceReplacementTrace) {
        throw Base::RuntimeError(
            "AssemblyLink synchronization is already being traced"
        );
    }

    const auto structuralOldResources =
        managedOccurrenceResources(this);
    std::unordered_set<App::DocumentObject*> structuralOldSet(
        structuralOldResources.begin(),
        structuralOldResources.end()
    );
    std::unordered_set<App::DocumentObject*> suppliedOldSet;
    suppliedOldSet.reserve(orderedOldResources.size());
    AssemblyLinkSynchronizationResult result;
    result.orderedOldResourceIdentities.reserve(
        orderedOldResources.size()
    );
    for (auto* resource : orderedOldResources) {
        if (!resource || resource == this
            || resource->getDocument() != document
            || !document->containsObject(resource)
            || !structuralOldSet.contains(resource)
            || !suppliedOldSet.insert(resource).second) {
            throw Base::ValueError(
                "Every old AssemblyLink resource must be one distinct "
                "live object in its exact native occurrence graph"
            );
        }
        result.orderedOldResourceIdentities.push_back(
            {
                .objectId = resource->getID(),
                .objectName = resource->getNameInDocument(),
            }
        );
    }
    if (suppliedOldSet != structuralOldSet) {
        throw Base::ValueError(
            "The old AssemblyLink resource list must cover its complete "
            "native occurrence graph"
        );
    }

    std::unordered_map<
        const App::DocumentObject*,
        App::DocumentObject*
    > replacements;
    std::vector<App::DocumentObject*> deferredRetirements;
    _resourceReplacementTrace = &replacements;
    if (App::DocumentTimeline::hasTimelineOperationRole(this)) {
        _resourceRetirementTrace = &deferredRetirements;
    }
    try {
        updateContentsUnchecked();

        // Synchronize retained nested occurrences in the same exact refresh.
        // Their own component/joint branches write into this one trace.
        std::unordered_set<AssemblyLink*> synchronized {this};
        bool discovered = true;
        while (discovered) {
            discovered = false;
            for (auto* resource :
                 managedOccurrenceResources(this)) {
                auto* nested = freecad_cast<AssemblyLink*>(resource);
                if (!nested || !synchronized.insert(nested).second) {
                    continue;
                }
                discovered = true;
                nested->_resourceReplacementTrace = &replacements;
                nested->_resourceRetirementTrace =
                    _resourceRetirementTrace;
                try {
                    if (auto* sourceNested =
                            freecad_cast<AssemblyLink*>(
                                nested->getLinkedObject2(false)
                            );
                        sourceNested
                        && nested->Rigid.getValue()
                            != sourceNested->Rigid.getValue()) {
                        nested->Rigid.setValue(
                            sourceNested->Rigid.getValue()
                        );
                    }
                    nested->updateContentsUnchecked();
                }
                catch (...) {
                    nested->_resourceReplacementTrace = nullptr;
                    nested->_resourceRetirementTrace = nullptr;
                    throw;
                }
                nested->_resourceReplacementTrace = nullptr;
                nested->_resourceRetirementTrace = nullptr;
            }
        }
    }
    catch (...) {
        _resourceReplacementTrace = nullptr;
        _resourceRetirementTrace = nullptr;
        throw;
    }
    _resourceReplacementTrace = nullptr;
    _resourceRetirementTrace = nullptr;

    const auto structuralFinalResources =
        managedOccurrenceResources(this);
    std::unordered_set<App::DocumentObject*> finalSet(
        structuralFinalResources.begin(),
        structuralFinalResources.end()
    );
    result.oldToFinalResources.reserve(orderedOldResources.size());
    std::unordered_set<App::DocumentObject*> orderedFinalSet;
    orderedFinalSet.reserve(structuralFinalResources.size());
    for (std::size_t index = 0;
         index < orderedOldResources.size();
        ++index) {
        auto* oldResource = orderedOldResources[index];
        const auto& oldIdentity =
            result.orderedOldResourceIdentities[index];
        App::DocumentObject* finalResource = nullptr;
        auto* retained =
            document->getObjectByID(oldIdentity.objectId);
        if (retained
            && retained->getNameInDocument()
                == oldIdentity.objectName
            && retained->getDocument() == document
            && document->containsObject(retained)
            && finalSet.contains(retained)) {
            finalResource = retained;
        }
        else if (const auto replacement =
                     replacements.find(oldResource);
                 replacement != replacements.end()
                 && replacement->second
                 && replacement->second->getDocument() == document
                 && document->containsObject(replacement->second)
                 && finalSet.contains(replacement->second)) {
            finalResource = replacement->second;
        }

        result.oldToFinalResources.push_back(finalResource);
        if (finalResource) {
            if (orderedFinalSet.insert(finalResource).second) {
                result.orderedFinalResources.push_back(finalResource);
            }
        }
        if (!retained || finalResource != retained) {
            result.retiredResourceIdentities.push_back(
                result.orderedOldResourceIdentities[index]
            );
        }
    }
    for (auto* resource : structuralFinalResources) {
        if (orderedFinalSet.insert(resource).second) {
            result.orderedFinalResources.push_back(resource);
        }
    }
    if (orderedFinalSet != finalSet) {
        throw Base::RuntimeError(
            "AssemblyLink synchronization did not return its complete "
            "final native occurrence graph"
        );
    }
    return result;
}

AssemblyLinkSynchronizationResult
AssemblyLink::synchronizeContentsWithResourceMap(
    const std::vector<App::DocumentObject*>& orderedOldResources
)
{
    auto* document = getDocument();
    auto* timeline = App::DocumentTimeline::get(document);
    if (!document || !document->containsObject(this)) {
        throw Base::RuntimeError(
            "AssemblyLink synchronization requires one live occurrence"
        );
    }
    if (!timeline
        || !App::DocumentTimeline::hasTimelineOperationRole(this)) {
        return synchronizeContentsWithResourceMapUnchecked(
            orderedOldResources
        );
    }
    if (!isPublishedOccurrenceUsable(this)) {
        throw Base::RuntimeError(
            "An inactive AssemblyLink occurrence cannot synchronize its "
            "native resource graph"
        );
    }
    if (_resourceReconciliationActive) {
        throw Base::RuntimeError(
            "AssemblyLink resource reconciliation is already active"
        );
    }

    const auto currentManagedResources =
        managedOccurrenceResources(this);
    if (currentManagedResources.size()
            != orderedOldResources.size()
        || std::unordered_set<App::DocumentObject*>(
               currentManagedResources.begin(),
               currentManagedResources.end()
           )
            != std::unordered_set<App::DocumentObject*>(
                orderedOldResources.begin(),
                orderedOldResources.end()
            )) {
        throw Base::ValueError(
            "The supplied AssemblyLink resources do not exactly cover its "
            "current native synchronization graph"
        );
    }
    if (!hasStructuralContentDiff()) {
        return synchronizeContentsWithResourceMapUnchecked(
            orderedOldResources
        );
    }

    const int transactionId =
        requireStructuralSynchronizationTransaction(document);
    const std::string documentUid =
        document->Uid.getValueStr();
    const long occurrenceId = getID();
    const char* occurrenceNameValue =
        getNameInDocument();
    if (!occurrenceNameValue) {
        throw Base::RuntimeError(
            "AssemblyLink synchronization lost its occurrence name"
        );
    }
    const std::string occurrenceName =
        occurrenceNameValue;
    const auto requireExactTransaction =
        [&]() {
            const char* currentName =
                getNameInDocument();
            if (getDocument() != document
                || document->Uid.getValueStr()
                    != documentUid
                || document->getObjectByID(occurrenceId)
                    != this
                || !currentName
                || occurrenceName != currentName
                || document->getBookedTransactionID()
                    != transactionId
                || !App::GetApplication().transactionIsActive(
                    transactionId
                )) {
                throw Base::RuntimeError(
                    "AssemblyLink synchronization lost its exact "
                    "occurrence or caller-owned transaction"
                );
            }
    };
    requireExactTransaction();
    Base::StateLocker reconciliation(
        _resourceReconciliationActive
    );

    const auto timelineOperations = timeline->Operations.getValues();
    std::vector<App::DocumentObject*> oldCompleteResources;
    std::vector<App::DocumentObject*> oldResourceRoots;
    for (auto* candidate : timelineOperations) {
        if (!candidate || candidate == this
            || semanticTimelineRoot(candidate) != this) {
            continue;
        }
        oldCompleteResources.push_back(candidate);
        if (App::DocumentTimeline::timelineOwner(candidate) == this) {
            oldResourceRoots.push_back(candidate);
        }
    }

    std::vector<App::DocumentObject*> oldManagedResources;
    std::vector<long> completeToManaged(
        oldCompleteResources.size(),
        -1
    );
    for (std::size_t index = 0;
         index < oldCompleteResources.size();
         ++index) {
        if (!managedSourceIdentity(oldCompleteResources[index])) {
            continue;
        }
        completeToManaged[index] =
            static_cast<long>(oldManagedResources.size());
        oldManagedResources.push_back(oldCompleteResources[index]);
    }
    if (oldManagedResources != orderedOldResources) {
        throw Base::ValueError(
            "AssemblyLink managed resources must be supplied in their exact "
            "canonical History order"
        );
    }

    struct ResourceSnapshot
    {
        long objectId {-1};
        std::string objectName;
    };
    std::vector<ResourceSnapshot> oldCompleteIdentities;
    oldCompleteIdentities.reserve(oldCompleteResources.size());
    for (auto* resource : oldCompleteResources) {
        oldCompleteIdentities.push_back(
            {
                .objectId = resource->getID(),
                .objectName = resource->getNameInDocument(),
            }
        );
    }

    document->stageTimelineOperationResourceReconciliation(
        this,
        oldResourceRoots
    );
    requireExactTransaction();
    auto synchronization =
        synchronizeContentsWithResourceMapUnchecked(
            orderedOldResources
        );
    requireExactTransaction();

    std::unordered_set<App::DocumentObject*> retiringResources;
    retiringResources.reserve(orderedOldResources.size());
    for (std::size_t index = 0;
         index < orderedOldResources.size();
         ++index) {
        const auto& identity =
            synchronization.orderedOldResourceIdentities[index];
        auto* oldLive =
            document->getObjectByID(identity.objectId);
        if (!oldLive
            || oldLive->getNameInDocument()
                != identity.objectName) {
            continue;
        }
        auto* finalResource =
            synchronization.oldToFinalResources[index];
        if (finalResource != oldLive) {
            retiringResources.insert(oldLive);
        }
    }
    for (std::size_t index = 0;
         index < orderedOldResources.size();
         ++index) {
        const auto& identity =
            synchronization.orderedOldResourceIdentities[index];
        auto* oldLive =
            document->getObjectByID(identity.objectId);
        auto* finalResource =
            synchronization.oldToFinalResources[index];
        if (oldLive && finalResource
            && oldLive->getNameInDocument()
                == identity.objectName
            && oldLive != finalResource) {
            replaceRetainedConsumerLinks(
                oldLive,
                finalResource,
                retiringResources,
                this
            );
        }
    }

    for (auto* resource :
         synchronization.orderedFinalResources) {
        markTimelineResource(resource, this);
    }

    std::vector<App::DocumentObject*> finalResources;
    std::vector<long> stateSourceIndices;
    std::unordered_set<App::DocumentObject*> finalResourceSet;
    finalResources.reserve(
        oldCompleteResources.size()
        + synchronization.orderedFinalResources.size()
    );
    stateSourceIndices.reserve(finalResources.capacity());
    const auto appendFinal =
        [&finalResources, &stateSourceIndices, &finalResourceSet](
            App::DocumentObject* resource,
            long stateSource
        ) {
            if (resource
                && finalResourceSet.insert(resource).second) {
                finalResources.push_back(resource);
                stateSourceIndices.push_back(stateSource);
            }
        };

    for (std::size_t completeIndex = 0;
         completeIndex < oldCompleteResources.size();
         ++completeIndex) {
        const long managedIndex =
            completeToManaged[completeIndex];
        if (managedIndex >= 0) {
            appendFinal(
                synchronization.oldToFinalResources[
                    static_cast<std::size_t>(managedIndex)
                ],
                static_cast<long>(completeIndex)
            );
            continue;
        }

        const auto& identity =
            oldCompleteIdentities[completeIndex];
        auto* retained =
            document->getObjectByID(identity.objectId);
        if (!retained
            || retained->getNameInDocument()
                != identity.objectName
            || retained->getDocument() != document
            || !document->containsObject(retained)
            || semanticTimelineRoot(retained) != this) {
            throw Base::RuntimeError(
                "AssemblyLink synchronization changed a non-native resource "
                "owned by the occurrence"
            );
        }
        appendFinal(
            retained,
            static_cast<long>(completeIndex)
        );
    }
    for (auto* managed :
         synchronization.orderedFinalResources) {
        appendFinal(managed, -1);
    }

    std::vector<long> consumerReplacementIndices;
    consumerReplacementIndices.reserve(
        oldCompleteResources.size()
    );
    for (std::size_t completeIndex = 0;
         completeIndex < oldCompleteResources.size();
         ++completeIndex) {
        App::DocumentObject* replacement = nullptr;
        const long managedIndex =
            completeToManaged[completeIndex];
        if (managedIndex >= 0) {
            replacement =
                synchronization.oldToFinalResources[
                    static_cast<std::size_t>(managedIndex)
                ];
        }
        else {
            const auto& identity =
                oldCompleteIdentities[completeIndex];
            replacement =
                document->getObjectByID(identity.objectId);
        }

        long replacementIndex = -1;
        if (replacement) {
            const auto found = std::ranges::find(
                finalResources,
                replacement
            );
            if (found != finalResources.end()) {
                replacementIndex = static_cast<long>(
                    std::distance(finalResources.begin(), found)
                );
            }
        }
        consumerReplacementIndices.push_back(
            replacementIndex
        );
    }

    App::TimelineResourceReconciliationMapping mapping;
    mapping.owner = this;
    mapping.orderedFinalResources = finalResources;
    mapping.stateSourceIndices = stateSourceIndices;
    mapping.consumerReplacementIndices =
        consumerReplacementIndices;
    document
        ->finalizeProvisionalTimelineOperationResourceReconciliation(
            mapping
        );
    requireExactTransaction();

    for (auto iterator =
             synchronization.orderedOldResourceIdentities.rbegin();
         iterator
         != synchronization.orderedOldResourceIdentities.rend();
         ++iterator) {
        requireExactTransaction();
        auto* retired =
            document->getObjectByID(iterator->objectId);
        if (!retired
            || retired->getNameInDocument()
                != iterator->objectName
            || !retiringResources.contains(retired)) {
            continue;
        }
        document->removeObject(retired->getNameInDocument());
    }
    requireExactTransaction();
    return synchronization;
}

void AssemblyLink::synchronizeComponents()
{
    auto* doc = getDocument();
    auto* assembly = getLinkedAssembly();
    if (!doc || !doc->containsObject(this)) {
        throw Base::RuntimeError(
            "AssemblyLink component synchronization requires one live "
            "occurrence"
        );
    }

    const auto previousObjLinkMap = objLinkMap;
    objLinkMap.clear();
    const auto assemblyLinkGroup = Group.getValues();
    const auto groupContents =
        classifyOccurrenceGroup(assemblyLinkGroup);
    const bool exactSources =
        App::DocumentTimeline::hasTimelineOperationRole(this)
        || App::DocumentTimeline::hasTimelineResourceRole(this)
        || _resourceReplacementTrace;
    std::unordered_set<App::DocumentObject*> consumedLocalLinks;

    for (auto* source : topLevelComponents(assembly)) {
        App::DocumentObject* link = nullptr;
        App::DocumentObject* previousLink = nullptr;

        for (auto* candidate :
             groupContents.componentCandidates) {
            if (!candidate || consumedLocalLinks.contains(candidate)) {
                continue;
            }
            if (exactSources) {
                const auto identity = managedSourceIdentity(candidate);
                if (!identity || *identity != sourceIdentity(source)) {
                    continue;
                }
                if (previousLink) {
                    throw Base::RuntimeError(
                        "An AssemblyLink contains duplicate managed resources "
                        "for one exact source"
                    );
                }
                previousLink = candidate;
            }

            if (componentRepresentationMatches(
                    source,
                    candidate,
                    exactSources
                )) {
                link = candidate;
                break;
            }
            if (exactSources) {
                break;
            }
        }

        if (!link && !previousLink) {
            if (const auto previous = previousObjLinkMap.find(source);
                previous != previousObjLinkMap.end()
                && previous->second
                && previous->second->getDocument() == doc
                && doc->containsObject(previous->second)
                && (!exactSources
                    || hasManagedSource(
                        previous->second,
                        source
                    ))) {
                previousLink = previous->second;
            }
        }

        if (!link) {
            if (_valueRefreshOnly) {
                throw Base::RuntimeError(
                    "AssemblyLink value refresh found a structural component "
                    "change"
                );
            }
            if (source->isDerivedFrom<AssemblyLink>()) {
                auto* sourceAssemblyLink =
                    static_cast<AssemblyLink*>(source);

                auto* nested = static_cast<AssemblyLink*>(
                    doc->addObject(
                        "Assembly::AssemblyLink",
                        source->getNameInDocument()
                    )
                );
                nested->LinkedObject.setValue(source);
                nested->Label.setValue(source->Label.getValue());
                addObject(nested);
                nested->Rigid.setValue(
                    sourceAssemblyLink->Rigid.getValue()
                );
                link = nested;
            }
            else if (auto* sourceLink =
                         freecad_cast<App::Link*>(source);
                     sourceLink && sourceLink->isLinkGroup()) {

                auto* localLink = static_cast<App::Link*>(
                    doc->addObject(
                        "App::Link",
                        source->getNameInDocument()
                    )
                );
                localLink->LinkedObject.setValue(
                    sourceLink->getTrueLinkedObject(false)
                );
                localLink->Label.setValue(source->Label.getValue());
                addObject(localLink);
                localLink->ElementCount.setValue(
                    sourceLink->ElementCount.getValue()
                );
                link = localLink;
            }
            else {
                auto* localLink = static_cast<App::Link*>(
                    doc->addObject(
                        "App::Link",
                        source->getNameInDocument()
                    )
                );
                localLink->LinkedObject.setValue(source);
                localLink->Label.setValue(source->Label.getValue());
                addObject(localLink);
                link = localLink;
            }

            recordResourceReplacement(previousLink, link);
        }

        markManagedSource(link, source);
        consumedLocalLinks.insert(link);
        objLinkMap[source] = link;

        if (auto* sourceLink = freecad_cast<App::Link*>(source);
            sourceLink && sourceLink->isLinkGroup()) {
            auto* localLink = freecad_cast<App::Link*>(link);
            if (!localLink) {
                throw Base::RuntimeError(
                    "An AssemblyLink link-group source has no local link"
                );
            }
            const auto sourceElements =
                sourceLink->ElementList.getValues();
            const auto localElements =
                localLink->ElementList.getValues();
            if (sourceElements.size() != localElements.size()) {
                throw Base::RuntimeError(
                    "An AssemblyLink link-group copy has an inconsistent "
                    "element count"
                );
            }
            for (std::size_t index = 0;
                 index < sourceElements.size();
                 ++index) {
                auto* sourceElement = sourceElements[index];
                auto* localElement = localElements[index];
                if (!sourceElement || !localElement) {
                    throw Base::RuntimeError(
                        "An AssemblyLink link group contains a null element"
                    );
                }
                if (!hasManagedSource(localElement, sourceElement)) {
                    markManagedSource(localElement, sourceElement);
                }
                if (previousLink && previousLink != link) {
                    auto* previousGroup =
                        freecad_cast<App::Link*>(previousLink);
                    if (previousGroup) {
                        for (auto* previousElement :
                             previousGroup->ElementList.getValues()) {
                            const auto previousIdentity =
                                managedSourceIdentity(previousElement);
                            if (previousIdentity
                                && *previousIdentity
                                    == sourceIdentity(sourceElement)) {
                                recordResourceReplacement(
                                    previousElement,
                                    localElement
                                );
                                break;
                            }
                        }
                    }
                }
                syncPlacements(sourceElement, localElement);
                objLinkMap[sourceElement] = localElement;
            }
        }
    }

    if (isRigid()) {
        for (const auto& [sourceObj, linkObj] : objLinkMap) {
            syncPlacements(sourceObj, linkObj);
        }
    }

    for (auto* candidate :
         groupContents.componentCandidates) {
        if (!candidate
            || consumedLocalLinks.contains(candidate)) {
            continue;
        }
        if (exactSources) {
            if (!managedSourceIdentity(candidate)) {
                continue;
            }
        }
        else if (!candidate->isDerivedFrom<App::Part>()
                 && !candidate->isDerivedFrom<PartApp::Feature>()
                 && !candidate->isDerivedFrom<App::Link>()) {
            continue;
        }
        if (_valueRefreshOnly) {
            throw Base::RuntimeError(
                "AssemblyLink value refresh found a retired component"
            );
        }
        if (_resourceRetirementTrace) {
            removeObject(candidate);
            recordResourceRetirement(candidate);
        }
        else {
            doc->removeObject(candidate->getNameInDocument());
        }
    }
}

namespace
{
template<typename T>
void copyPropertyIfDifferent(
    App::DocumentObject* source,
    App::DocumentObject* target,
    const char* propertyName
)
{
    auto sourceProp = freecad_cast<T*>(source->getPropertyByName(propertyName));
    auto targetProp = freecad_cast<T*>(target->getPropertyByName(propertyName));
    if (sourceProp && targetProp && sourceProp->getValue() != targetProp->getValue()) {
        targetProp->setValue(sourceProp->getValue());
    }
}

[[maybe_unused]] std::string removeUpToName(const std::string& sub, const std::string& name)
{
    size_t pos = sub.find(name);
    if (pos != std::string::npos) {
        // Move the position to the character after the found substring and the following '.'
        pos += name.length() + 1;
        if (pos < sub.length()) {
            return sub.substr(pos);
        }
    }
    // If s2 is not found in s1, return the original string
    return sub;
}

[[maybe_unused]] std::string replaceLastOccurrence(
    const std::string& str,
    const std::string& oldStr,
    const std::string& newStr
)
{
    size_t pos = str.rfind(oldStr);
    if (pos != std::string::npos) {
        std::string result = str;
        result.replace(pos, oldStr.length(), newStr);
        return result;
    }
    return str;
}
};  // namespace

void AssemblyLink::synchronizeJoints()
{
    auto* doc = getDocument();
    auto* assembly = getLinkedAssembly();
    if (!assembly) {
        return;
    }

    auto* localGroup = _valueRefreshOnly
        ? getJointGroup(this)
        : ensureJointGroup();
    if (!localGroup) {
        throw Base::RuntimeError(
            "AssemblyLink value refresh found a missing joint group"
        );
    }
    auto* sourceGroup = assembly->getJointGroup();
    markManagedSource(
        localGroup,
        sourceGroup
            ? static_cast<App::DocumentObject*>(sourceGroup)
            : static_cast<App::DocumentObject*>(assembly)
    );

    const auto sourceJoints = assembly->getJoints(false, false);
    const bool exactSources =
        App::DocumentTimeline::hasTimelineOperationRole(this)
        || App::DocumentTimeline::hasTimelineResourceRole(this)
        || _resourceReplacementTrace;
    const auto localJoints = exactSources
        ? localGroup->Group.getValues()
        : getJoints();
    std::unordered_set<App::DocumentObject*> retainedLocalJoints;
    std::vector<App::DocumentObject*> orderedLocalJoints;
    orderedLocalJoints.reserve(sourceJoints.size());

    for (std::size_t index = 0;
         index < sourceJoints.size();
         ++index) {
        auto* sourceJoint = sourceJoints[index];
        App::DocumentObject* localJoint = nullptr;
        if (exactSources) {
            for (auto* candidate : localJoints) {
                if (!candidate
                    || retainedLocalJoints.contains(candidate)
                    || !hasManagedSource(candidate, sourceJoint)) {
                    continue;
                }
                if (localJoint) {
                    throw Base::RuntimeError(
                        "An AssemblyLink contains duplicate joint resources "
                        "for one exact source"
                    );
                }
                localJoint = candidate;
            }
        }
        else if (index < localJoints.size()) {
            localJoint = localJoints[index];
        }

        if (!localJoint) {
            if (_valueRefreshOnly) {
                throw Base::RuntimeError(
                    "AssemblyLink value refresh found a structural joint "
                    "change"
                );
            }
            auto copied = doc->copyObject({sourceJoint});
            if (copied.size() != 1 || !copied.front()) {
                throw Base::RuntimeError(
                    "AssemblyLink could not copy one linked joint"
                );
            }
            localJoint = copied.front();
            localGroup->addObject(localJoint);
        }
        markManagedSource(localJoint, sourceJoint);
        retainedLocalJoints.insert(localJoint);
        orderedLocalJoints.push_back(localJoint);

        copyPropertyIfDifferent<App::PropertyBool>(
            sourceJoint,
            localJoint,
            "Suppressed"
        );
        copyPropertyIfDifferent<App::PropertyFloat>(
            sourceJoint,
            localJoint,
            "Distance"
        );
        copyPropertyIfDifferent<App::PropertyFloat>(
            sourceJoint,
            localJoint,
            "Distance2"
        );
        copyPropertyIfDifferent<App::PropertyEnumeration>(
            sourceJoint,
            localJoint,
            "JointType"
        );
        copyPropertyIfDifferent<App::PropertyPlacement>(
            sourceJoint,
            localJoint,
            "Offset1"
        );
        copyPropertyIfDifferent<App::PropertyPlacement>(
            sourceJoint,
            localJoint,
            "Offset2"
        );

        copyPropertyIfDifferent<App::PropertyBool>(
            sourceJoint,
            localJoint,
            "Detach1"
        );
        copyPropertyIfDifferent<App::PropertyBool>(
            sourceJoint,
            localJoint,
            "Detach2"
        );

        copyPropertyIfDifferent<App::PropertyFloat>(
            sourceJoint,
            localJoint,
            "AngleMax"
        );
        copyPropertyIfDifferent<App::PropertyFloat>(
            sourceJoint,
            localJoint,
            "AngleMin"
        );
        copyPropertyIfDifferent<App::PropertyFloat>(
            sourceJoint,
            localJoint,
            "LengthMax"
        );
        copyPropertyIfDifferent<App::PropertyFloat>(
            sourceJoint,
            localJoint,
            "LengthMin"
        );
        copyPropertyIfDifferent<App::PropertyBool>(
            sourceJoint,
            localJoint,
            "EnableAngleMax"
        );
        copyPropertyIfDifferent<App::PropertyBool>(
            sourceJoint,
            localJoint,
            "EnableAngleMin"
        );
        copyPropertyIfDifferent<App::PropertyBool>(
            sourceJoint,
            localJoint,
            "EnableLengthMax"
        );
        copyPropertyIfDifferent<App::PropertyBool>(
            sourceJoint,
            localJoint,
            "EnableLengthMin"
        );

        handleJointReference(
            sourceJoint,
            localJoint,
            "Reference1"
        );
        handleJointReference(
            sourceJoint,
            localJoint,
            "Reference2"
        );
    }

    for (auto* candidate : localJoints) {
        if (!candidate || retainedLocalJoints.contains(candidate)) {
            continue;
        }
        if (exactSources && !managedSourceIdentity(candidate)) {
            continue;
        }
        if (_valueRefreshOnly) {
            throw Base::RuntimeError(
                "AssemblyLink value refresh found a retired joint"
            );
        }
        if (_resourceRetirementTrace) {
            localGroup->removeObject(candidate);
            removeObject(candidate);
            recordResourceRetirement(candidate);
        }
        else {
            doc->removeObject(candidate->getNameInDocument());
        }
    }
    for (auto* localJoint : orderedLocalJoints) {
        localJoint->purgeTouched();
    }
}


void AssemblyLink::handleJointReference(
    App::DocumentObject* joint,
    App::DocumentObject* lJoint,
    const char* refName
)
{
    auto prop1 = dynamic_cast<App::PropertyXLinkSub*>(joint->getPropertyByName(refName));
    auto prop2 = dynamic_cast<App::PropertyXLinkSub*>(lJoint->getPropertyByName(refName));
    if (!prop1 || !prop2) {
        return;
    }

    // 1. Get the external component prop1 is [ExternalPart, "Sub"]
    App::DocumentObject* externalComponent = prop1->getValue();
    if (!externalComponent) {
        return;
    }

    // 2. Map to local link
    auto it = objLinkMap.find(externalComponent);
    if (it == objLinkMap.end()) {
        Base::Console().warning(
            "AssemblyLink: Could not map external component %s to a local link for joint %s\n",
            externalComponent->getNameInDocument(),
            joint->getNameInDocument()
        );
        return;
    }
    App::DocumentObject* localLink = it->second;

    // 3. Set the new reference
    // The local joint now points to the local link [LocalLink, "Sub"]
    if (prop2->getValue() != localLink) {
        prop2->setValue(localLink);
    }

    // 4. Sync sub-elements
    // The sub-elements (e.g. "Body.Face1") are relative to the component.
    // Since the LocalLink points to the ExternalPart, the relative path is identical.
    std::vector<std::string> subs1 = prop1->getSubValues();
    std::vector<std::string> subs2 = prop2->getSubValues();

    bool changed = false;
    if (subs1.size() != subs2.size()) {
        changed = true;
    }
    else {
        for (size_t i = 0; i < subs1.size(); ++i) {
            if (subs1[i] != subs2[i]) {
                changed = true;
                break;
            }
        }
    }

    if (changed) {
        prop2->setSubValues(std::move(subs1));
    }
}

void AssemblyLink::ensureNoJointGroup()
{
    auto* jointGroup = getJointGroup(this);
    if (!jointGroup) {
        return;
    }
    if (_valueRefreshOnly) {
        throw Base::RuntimeError(
            "AssemblyLink value refresh found a retired joint group"
        );
    }

    // Only the native synchronization graph is retired.  A domain may own
    // another exact resource through this occurrence; group membership must
    // never grant AssemblyLink permission to delete it.
    for (auto* member : jointGroup->Group.getValues()) {
        if (!member) {
            continue;
        }
        if (managedSourceIdentity(member)) {
            if (_resourceRetirementTrace) {
                jointGroup->removeObject(member);
                removeObject(member);
                recordResourceRetirement(member);
            }
            else {
                getDocument()->removeObject(
                    member->getNameInDocument()
                );
            }
        }
        else {
            jointGroup->removeObject(member);
        }
    }
    if (_resourceRetirementTrace) {
        removeObject(jointGroup);
        recordResourceRetirement(jointGroup);
    }
    else {
        getDocument()->removeObject(
            jointGroup->getNameInDocument()
        );
    }
}
JointGroup* AssemblyLink::ensureJointGroup()
{
    // Make sure there is a jointGroup
    JointGroup* jGroup = getJointGroup(this);
    if (!jGroup) {
        if (_valueRefreshOnly) {
            throw Base::RuntimeError(
                "AssemblyLink value refresh found a missing joint group"
            );
        }
        jGroup = new JointGroup();
        getDocument()->addObject(jGroup, tr("Joints").toStdString().c_str());

        std::vector<DocumentObject*> grp = Group.getValues();
        grp.insert(grp.begin(), jGroup);
        Group.setValues(grp);
    }
    return jGroup;
}

App::DocumentObject* AssemblyLink::getLinkedObject2(bool recursive) const
{
    auto* linkedObject = LinkedObject.getValue();
    if (!recursive) {
        if (freecad_cast<AssemblyObject*>(linkedObject)
            || freecad_cast<AssemblyLink*>(linkedObject)) {
            return linkedObject;
        }
        return nullptr;
    }

    std::unordered_set<const AssemblyLink*> visited {this};
    while (auto* linkedOccurrence =
               freecad_cast<AssemblyLink*>(linkedObject)) {
        if (!visited.insert(linkedOccurrence).second) {
            return nullptr;
        }
        linkedObject = linkedOccurrence->LinkedObject.getValue();
    }

    return freecad_cast<AssemblyObject*>(linkedObject);
}

AssemblyObject* AssemblyLink::getLinkedAssembly() const
{
    return freecad_cast<AssemblyObject*>(getLinkedObject2());
}

AssemblyObject* AssemblyLink::getParentAssembly() const
{
    auto* document = getDocument();
    if (!document) {
        return nullptr;
    }

    std::vector<
        std::pair<App::DocumentObject*, const App::DocumentObject*>
    > pending;
    for (auto* parent : getInList()) {
        pending.emplace_back(parent, this);
    }
    std::unordered_set<const App::DocumentObject*> visited {
        this,
    };
    AssemblyObject* result = nullptr;
    for (std::size_t index = 0;
         index < pending.size();
         ++index) {
        const auto [candidate, child] = pending[index];
        if (!candidate || !child
            || candidate->getDocument() != document
            || visited.contains(candidate)) {
            continue;
        }
        const auto* group =
            candidate->getExtensionByType<App::GroupExtension>(
                true
            );
        if (!group || !group->hasObject(child, false)) {
            continue;
        }
        visited.insert(candidate);

        if (auto* assembly =
                freecad_cast<AssemblyObject*>(candidate)) {
            if (result && result != assembly) {
                throw Base::RuntimeError(
                    "An AssemblyLink cannot belong to multiple assemblies"
                );
            }
            result = assembly;
            continue;
        }
        for (auto* parent : candidate->getInList()) {
            pending.emplace_back(parent, candidate);
        }
    }
    return result;
}

bool AssemblyLink::isRigid() const
{
    auto* prop = dynamic_cast<App::PropertyBool*>(getPropertyByName("Rigid"));
    if (!prop) {
        return true;
    }
    return prop->getValue();
}

std::vector<App::DocumentObject*> AssemblyLink::getJoints()
{
    JointGroup* jointGroup = getJointGroup(this);

    if (!jointGroup) {
        return {};
    }
    return jointGroup->getJoints();
}

bool AssemblyLink::allowDuplicateLabel() const
{
    return true;
}

int AssemblyLink::numberOfComponents() const
{
    if (isRigid()) {
        return 1;
    }
    auto* assembly = getLinkedAssembly();
    return assembly ? assembly->numberOfComponents() : 0;
}

bool AssemblyLink::isEmpty() const
{
    return numberOfComponents() == 0;
}
