// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ModelingContext.h"

#include <algorithm>
#include <format>
#include <functional>
#include <map>
#include <utility>

#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/Datums.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/GroupExtension.h>
#include <App/Origin.h>
#include <App/PropertyLinks.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/ActiveObjectList.h>
#include <Gui/Application.h>
#include <Gui/Macro.h>
#include <Gui/MDIView.h>
#include <Mod/Part/App/BodyBase.h>
#include <Mod/Part/App/DatumFeature.h>
#include <Mod/Part/App/Part2DObject.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/Gui/ModelingSelection.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/Feature.h>

using namespace PartDesignGui;

namespace
{
fastsignals::connection& deletedObjectConnection()
{
    // ModelingContext is exported and intentionally process-lived. Keep the
    // additional observer handle outside its established instance layout.
    static auto* connection = new fastsignals::connection;
    return *connection;
}

struct PreparedOwnership
{
    Gui::Application::DurableTaskResultOwnership ownership;
    long ownerObjectId;
};

using PreparedResultKey = std::pair<const App::Document*, long>;

std::map<PreparedResultKey, PreparedOwnership>& preparedResults()
{
    static auto* results = new std::map<PreparedResultKey, PreparedOwnership>;
    return *results;
}
}  // namespace

ModelingContext& ModelingContext::instance()
{
    // The GUI application owns this process-wide observer.  Deliberately keep
    // it alive until process teardown so signal destruction order is harmless.
    static auto* context = new ModelingContext;
    return *context;
}

ModelingContext::ModelingContext()
{
    auto& application = App::GetApplication();
    newObjectConnection = application.signalNewObject.connect(
        std::bind(&ModelingContext::queueResult, this, std::placeholders::_1)
    );
    deletedObjectConnection() = application.signalDeletedObject.connect(
        std::bind(&ModelingContext::removePendingResult, this, std::placeholders::_1)
    );
    beforeCloseTransactionConnection = application.signalBeforeCloseTransaction.connect(
        std::bind(&ModelingContext::beforeCloseTransaction, this, std::placeholders::_1)
    );
    if (Gui::Application::Instance) {
        Gui::Application::Instance->setDurableTaskResultIntentPreparer(
            [this](
                const App::Document& document,
                const std::vector<long>& objectIds,
                const std::vector<Gui::Application::DurableTaskResultIntent>& intents
            ) { finalizeDurableResults(document, objectIds, intents); }
        );
    }
    deleteDocumentConnection = application.signalDeleteDocument.connect(
        std::bind(&ModelingContext::clearDocument, this, std::placeholders::_1)
    );
}

bool ModelingContext::isOrdinaryPartResult(const App::DocumentObject* object)
{
    return object && object->isDerivedFrom<Part::Feature>()
        && !object->isDerivedFrom<PartDesign::Feature>()
        && !object->isDerivedFrom<Part::Part2DObject>() && !object->isDerivedFrom<Part::BodyBase>()
        && !object->isDerivedFrom<Part::Datum>();
}

PartDesign::Body* ModelingContext::activeBodyFor(const App::Document* document)
{
    auto* view = Gui::Application::Instance ? Gui::Application::Instance->activeView() : nullptr;
    if (!view) {
        return nullptr;
    }
    auto* body = view->getActiveObject<PartDesign::Body*>(PDBODYKEY);
    return body && body->getDocument() == document ? body : nullptr;
}

bool ModelingContext::collectAdoptableGraph(
    PartDesign::Body* body,
    App::DocumentObject* object,
    std::set<App::DocumentObject*>& visited,
    std::vector<App::DocumentObject*>& ordered
)
{
    if (!body || !object || object->getDocument() != body->getDocument()) {
        return false;
    }
    if (object == body || !visited.insert(object).second) {
        return true;
    }

    auto* geoOwner = App::GeoFeatureGroupExtension::getGroupOfObject(object);
    if (geoOwner) {
        if (geoOwner == body) {
            return true;
        }
        // Origin geometry is a reference, not a modeling operand. It must remain owned by the
        // Origin while the retained Part result joins the Body through its adaptive global link.
        return object->isDerivedFrom<App::DatumElement>() || object->isDerivedFrom<App::Origin>();
    }
    if (App::GroupExtension::getGroupOfObject(object) || !PartDesign::Body::isAllowed(object)) {
        return false;
    }

    for (auto* dependency : object->getOutList()) {
        if (!collectAdoptableGraph(body, dependency, visited, ordered)) {
            return false;
        }
    }
    ordered.push_back(object);
    return true;
}

PartDesign::Body* ModelingContext::adoptPartResult(App::DocumentObject* result, PartDesign::Body* body) const
{
    if (!isOrdinaryPartResult(result)) {
        return nullptr;
    }
    body = body ? body : activeBodyFor(result->getDocument());
    if (!body || body->getDocument() != result->getDocument()) {
        return nullptr;
    }
    if (App::GeoFeatureGroupExtension::getGroupOfObject(result) == body) {
        return body;
    }

    std::set<App::DocumentObject*> visited;
    std::vector<App::DocumentObject*> ordered;
    if (!collectAdoptableGraph(body, result, visited, ordered)) {
        return nullptr;
    }

    struct FeatureState
    {
        App::DocumentObject* object;
        bool visible;
        PartDesign::Feature* partDesignFeature;
        App::DocumentObject* baseFeature;
        App::DocumentObject* bodyLink;
        std::vector<std::pair<App::PropertyLinkBase*, App::LinkScope>> linkScopes;
    };

    const auto originalGroup = body->Group.getValues();
    auto* originalTip = body->Tip.getValue();
    std::set<App::DocumentObject*> stateObjects(originalGroup.begin(), originalGroup.end());
    stateObjects.insert(ordered.begin(), ordered.end());
    std::vector<FeatureState> originalStates;
    originalStates.reserve(stateObjects.size());
    for (auto* object : stateObjects) {
        auto* feature = freecad_cast<PartDesign::Feature*>(object);
        std::vector<std::pair<App::PropertyLinkBase*, App::LinkScope>> linkScopes;
        std::vector<App::Property*> properties;
        object->getPropertyList(properties);
        for (auto* property : properties) {
            if (auto* link = freecad_cast<App::PropertyLinkBase*>(property)) {
                linkScopes.emplace_back(link, link->getScope());
            }
        }
        originalStates.push_back({
            object,
            object->Visibility.getValue(),
            feature,
            feature ? feature->BaseFeature.getValue() : nullptr,
            feature ? feature->_Body.getValue() : nullptr,
            std::move(linkScopes),
        });
    }

    auto restoreState = [&]() {
        for (const auto& state : originalStates) {
            for (const auto& [link, scope] : state.linkScopes) {
                link->setScope(scope);
            }
        }
        body->Group.setValues(originalGroup);
        body->Tip.setValue(originalTip);
        for (const auto& state : originalStates) {
            state.object->Visibility.setValue(state.visible);
            if (state.partDesignFeature) {
                state.partDesignFeature->BaseFeature.setValue(state.baseFeature);
                state.partDesignFeature->_Body.setValue(state.bodyLink);
            }
        }
    };

    // collectAdoptableGraph validates the complete graph before the first mutation. Recheck
    // ownership as each object is inserted because Group callbacks are allowed to run during an
    // insertion. If one changes a later object's ownership, restore every Body property affected
    // by earlier insertions before declining adoption.
    try {
        for (auto* object : ordered) {
            auto* owner = App::GeoFeatureGroupExtension::getGroupOfObject(object);
            if (owner == body) {
                continue;
            }
            if (owner || App::GroupExtension::getGroupOfObject(object)) {
                restoreState();
                return nullptr;
            }
            if (auto* feature = freecad_cast<Part::Feature*>(object)) {
                feature->prepareCrossContainerLinks(body);
            }
            body->addObject(object);
        }
    }
    catch (...) {
        try {
            restoreState();
        }
        catch (const std::exception& error) {
            Base::Console().error(
                "Part Design failed to restore Body '%s' after an adoption failure: %s\n",
                body->getNameInDocument(),
                error.what()
            );
        }
        catch (...) {
            Base::Console().error(
                "Part Design failed to restore Body '%s' after an adoption failure.\n",
                body->getNameInDocument()
            );
        }
        throw;
    }
    return App::GeoFeatureGroupExtension::getGroupOfObject(result) == body ? body : nullptr;
}

void ModelingContext::queueResult(const App::DocumentObject& object)
{
    auto* document = object.getDocument();
    if (!document) {
        return;
    }
    preparedResults().erase({document, object.getID()});
    if (document->testStatus(App::Document::Restoring) || !document->hasPendingTransaction()) {
        return;
    }

    const int transactionId = document->getTransactionID(true);
    if (transactionId == App::NullTransaction) {
        return;
    }

    auto* body = activeBodyFor(document);
    const auto duplicate = std::ranges::find_if(pending, [&](const PendingResult& item) {
        return item.document == document && item.objectId == object.getID()
            && item.transactionId == transactionId;
    });
    if (duplicate == pending.end()) {
        pending.push_back({
            document,
            object.getID(),
            body ? body->getID() : -1,
            transactionId,
            object.getNameInDocument() ? object.getNameInDocument() : "",
        });
    }
}

void ModelingContext::removePendingResult(const App::DocumentObject& object)
{
    preparedResults().erase({object.getDocument(), object.getID()});
    std::erase_if(pending, [&](const PendingResult& item) {
        return item.document == object.getDocument() && item.objectId == object.getID();
    });
}

void ModelingContext::beforeCloseTransaction(bool abort)
{
    std::set<int> closingTransactions;
    auto& application = App::GetApplication();
    for (auto* document : application.getDocuments()) {
        // The signal is emitted from Document::_commitTransaction()/_abortTransaction() while the
        // initiating document still owns its active undo transaction. All documents sharing that
        // ID are about to close as one application transaction.
        if (!document->transacting() || !document->hasPendingTransaction()) {
            continue;
        }
        const int transactionId = document->getTransactionID(true);
        if (transactionId != App::NullTransaction) {
            closingTransactions.insert(transactionId);
        }
    }

    // Application-driven closes remove their transaction description before committing the first
    // document. Keep a defensive fallback for callers whose document does not report transacting
    // during the signal.
    if (closingTransactions.empty()) {
        for (const auto& item : pending) {
            if (item.transactionId != App::NullTransaction
                && !application.transactionIsActive(item.transactionId)) {
                closingTransactions.insert(item.transactionId);
            }
        }
    }
    if (closingTransactions.empty()) {
        return;
    }

    if (abort) {
        std::erase_if(pending, [&](const PendingResult& item) {
            return closingTransactions.contains(item.transactionId);
        });
        return;
    }
    flushPending(closingTransactions);
}

void ModelingContext::finalizeDurableResults(
    const App::Document& document,
    const std::vector<long>& acceptedObjectIds,
    const std::vector<Gui::Application::DurableTaskResultIntent>& intents
)
{
    using Ownership = Gui::Application::DurableTaskResultOwnership;

    if (acceptedObjectIds.empty()) {
        return;
    }

    const std::set<long> accepted(acceptedObjectIds.begin(), acceptedObjectIds.end());
    std::set<int> acceptedTransactions;
    for (const auto& item : pending) {
        if (item.document == &document && accepted.contains(item.objectId)) {
            acceptedTransactions.insert(item.transactionId);
        }
    }
    std::set<long> attemptCreatedObjectIds;
    for (const auto& item : pending) {
        if (item.document == &document && acceptedTransactions.contains(item.transactionId)) {
            attemptCreatedObjectIds.insert(item.objectId);
        }
    }
    std::set<long> preparedGraphIds = accepted;
    for (long objectId : accepted) {
        auto* result = document.getObjectByID(objectId);
        if (!result) {
            continue;
        }
        std::set<const App::DocumentObject*> visited;
        std::vector<App::DocumentObject*> dependencies = result->getOutList();
        while (!dependencies.empty()) {
            auto* dependency = dependencies.back();
            dependencies.pop_back();
            if (!dependency || dependency->getDocument() != &document
                || !visited.insert(dependency).second
                || !attemptCreatedObjectIds.contains(dependency->getID())) {
                continue;
            }
            preparedGraphIds.insert(dependency->getID());
            const auto nested = dependency->getOutList();
            dependencies.insert(dependencies.end(), nested.begin(), nested.end());
        }
    }

    std::map<long, Gui::Application::DurableTaskResultIntent> requested;
    for (const auto& intent : intents) {
        if (!accepted.contains(intent.objectId)) {
            throw Base::ValueError("Durable result ownership intent references an unaccepted object");
        }
        if (!requested.emplace(intent.objectId, intent).second) {
            throw Base::ValueError("Durable result ownership intent is duplicated");
        }
        if (intent.ownership == Ownership::ExactOwner && intent.ownerObjectId < 0) {
            throw Base::ValueError("Exact durable result ownership requires an owner object");
        }
    }

    std::vector<std::pair<App::DocumentObject*, PartDesign::Body*>> adopted;
    std::vector<std::pair<PreparedResultKey, PreparedOwnership>> newlyPrepared;
    for (long objectId : accepted) {
        auto* result = document.getObjectByID(objectId);
        if (!result || !isOrdinaryPartResult(result)) {
            continue;
        }

        const auto intent = requested.find(objectId);
        const auto requestedOwnership = intent == requested.end() ? Ownership::Automatic
                                                                  : intent->second.ownership;
        const long requestedOwnerId = intent == requested.end() ? -1 : intent->second.ownerObjectId;

        const PreparedResultKey preparedKey {&document, objectId};
        const auto previous = preparedResults().find(preparedKey);
        if (previous != preparedResults().end()) {
            if (requestedOwnership != Ownership::Automatic
                && (requestedOwnership != previous->second.ownership
                    || (requestedOwnership == Ownership::ExactOwner
                        && requestedOwnerId != previous->second.ownerObjectId))) {
                throw Base::RuntimeError(
                    "Durable result ownership conflicts with its prepared state"
                );
            }

            if (previous->second.ownership == Ownership::DocumentRoot) {
                if (App::GeoFeatureGroupExtension::getGroupOfObject(result)
                    || App::GroupExtension::getGroupOfObject(result)) {
                    throw Base::RuntimeError("A prepared document-root result acquired an owner");
                }
                continue;
            }

            auto* body = freecad_cast<PartDesign::Body*>(
                document.getObjectByID(previous->second.ownerObjectId)
            );
            if (!body || PartDesign::Body::findBodyOf(result) != body) {
                throw Base::RuntimeError("A prepared Body result lost its exact owner");
            }
            adopted.emplace_back(result, body);
            continue;
        }

        if (requestedOwnership == Ownership::DocumentRoot) {
            if (App::GeoFeatureGroupExtension::getGroupOfObject(result)
                || App::GroupExtension::getGroupOfObject(result)) {
                throw Base::RuntimeError("An explicit document-root result already has an owner");
            }
            newlyPrepared.push_back({
                preparedKey,
                {Ownership::DocumentRoot, -1},
            });
            continue;
        }

        PartDesign::Body* body = nullptr;
        if (requestedOwnership == Ownership::ExactOwner) {
            body = freecad_cast<PartDesign::Body*>(document.getObjectByID(requestedOwnerId));
            if (!body || body->getDocument() != &document) {
                throw Base::ValueError("The exact durable result Body is unavailable");
            }
        }
        else {
            body = PartDesign::Body::findBodyOf(result);
            if (!body) {
                if (!attemptCreatedObjectIds.contains(objectId)) {
                    newlyPrepared.push_back({
                        preparedKey,
                        {Ownership::DocumentRoot, -1},
                    });
                    continue;
                }

                const auto inferred
                    = PartGui::inferModelingResultOwner(*result, attemptCreatedObjectIds);
                if (inferred.ownership == PartGui::ModelingResultOwnership::DocumentRoot) {
                    newlyPrepared.push_back({
                        preparedKey,
                        {Ownership::DocumentRoot, -1},
                    });
                    continue;
                }
                if (inferred.ownership == PartGui::ModelingResultOwnership::Body) {
                    body = freecad_cast<PartDesign::Body*>(inferred.body);
                    if (!body) {
                        newlyPrepared.push_back({
                            preparedKey,
                            {Ownership::DocumentRoot, -1},
                        });
                        continue;
                    }
                }
                else {
                    body = activeBodyFor(&document);
                }
            }
        }

        // The automatic path remains compatible for primitives and existing
        // callers. Once it resolves to root, remember that decision so the
        // TaskDialog's later automatic preparation cannot reinterpret it.
        if (!body) {
            newlyPrepared.push_back({
                preparedKey,
                {Ownership::DocumentRoot, -1},
            });
            continue;
        }

        const bool alreadyAdopted = App::GeoFeatureGroupExtension::getGroupOfObject(result) == body;
        auto* adoptedBody = adoptPartResult(result, body);
        if (!adoptedBody) {
            throw Base::RuntimeError(
                std::string("Could not place '") + result->getNameInDocument()
                + "' in active Body '" + body->getNameInDocument() + "'"
            );
        }
        adopted.emplace_back(result, adoptedBody);
        newlyPrepared.push_back({
            preparedKey,
            {Ownership::ExactOwner, adoptedBody->getID()},
        });

        if (!alreadyAdopted && document.hasPendingTransaction() && Gui::Application::Instance) {
            auto* manager = Gui::Application::Instance->macroManager();
            if (manager) {
                manager->addLine(Gui::MacroManager::App, "import PartDesignGui");
                const std::string line = std::format(
                    "PartDesignGui.adoptPartResult("
                    "App.getDocument('{}').getObject('{}'), "
                    "App.getDocument('{}').getObject('{}'))",
                    document.getName(),
                    result->getNameInDocument(),
                    document.getName(),
                    body->getNameInDocument()
                );
                manager->addLine(Gui::MacroManager::App, line.c_str());
            }
        }
    }

    std::erase_if(pending, [&](const PendingResult& item) {
        return item.document == &document && preparedGraphIds.contains(item.objectId);
    });

    if (!adopted.empty()) {
        auto& mutableDocument = const_cast<App::Document&>(document);
        mutableDocument.recompute();
        for (const auto& [result, body] : adopted) {
            if (!result->isValid()) {
                const char* status = result->getStatusString();
                throw Base::RuntimeError(status && *status ? status : "Adopted Part result is invalid");
            }
            const auto shape = Part::Feature::getTopoShape(result, Part::ShapeOption::NoFlag);
            if (shape.isNull() || !shape.isValid()) {
                throw Base::RuntimeError(
                    std::string(result->getFullLabel()) + " became invalid after Body adoption"
                );
            }
            if (!body->isValid() || PartDesign::Body::findBodyOf(result) != body) {
                throw Base::RuntimeError(
                    std::string("Body adoption did not produce a valid owner for ")
                    + result->getFullLabel()
                );
            }
        }
    }
    for (const auto& [key, ownership] : newlyPrepared) {
        preparedResults().insert_or_assign(key, ownership);
    }
}

void ModelingContext::clearDocument(const App::Document& document)
{
    std::erase_if(preparedResults(), [&](const auto& item) { return item.first.first == &document; });
    std::erase_if(pending, [&](const PendingResult& item) { return item.document == &document; });
}

void ModelingContext::flushPending(const std::set<int>& transactionIds)
{
    std::vector<PendingResult> queued;
    std::erase_if(pending, [&](PendingResult& item) {
        if (!transactionIds.contains(item.transactionId)) {
            return false;
        }
        queued.push_back(std::move(item));
        return true;
    });

    adoptQueued(std::move(queued));
}

std::size_t ModelingContext::adoptQueued(std::vector<PendingResult> queued)
{
    using InferredOwnership = PartGui::ModelingResultOwnership;
    using InferredOwner = PartGui::ModelingResultOwner;

    using TransactionKey = std::pair<App::Document*, int>;
    std::map<TransactionKey, std::set<long>> createdByTransaction;
    for (const auto& item : queued) {
        createdByTransaction[{item.document, item.transactionId}].insert(item.objectId);
    }

    std::map<TransactionKey, std::map<long, InferredOwner>> ownershipByTransaction;
    std::map<TransactionKey, std::map<long, App::DocumentObject*>> fixedGroupsByTransaction;
    std::map<TransactionKey, std::map<long, long>> fallbackBodiesByTransaction;
    for (const auto& item : queued) {
        auto* result = item.document->getObjectByID(item.objectId);
        if (!isOrdinaryPartResult(result)) {
            continue;
        }

        const TransactionKey key {item.document, item.transactionId};
        auto* group = App::GeoFeatureGroupExtension::getGroupOfObject(result);
        if (!group) {
            group = App::GroupExtension::getGroupOfObject(result);
        }
        if (group) {
            fixedGroupsByTransaction[key].insert_or_assign(item.objectId, group);
        }
        if (auto* body = freecad_cast<PartDesign::Body*>(group)) {
            ownershipByTransaction[key].insert_or_assign(
                item.objectId,
                InferredOwner {InferredOwnership::Body, body}
            );
        }
        else if (group || App::GroupExtension::getGroupOfObject(result)) {
            // A command which explicitly inserted its result into an App::Part
            // or ordinary group already supplied ownership. ModelingContext
            // must not reinterpret or move it.
            ownershipByTransaction[key].insert_or_assign(
                item.objectId,
                InferredOwner {InferredOwnership::DocumentRoot, nullptr}
            );
        }
        else {
            ownershipByTransaction[key].insert_or_assign(
                item.objectId,
                PartGui::inferModelingResultOwner(*result, createdByTransaction.at(key))
            );
        }
        fallbackBodiesByTransaction[key].insert_or_assign(item.objectId, item.bodyId);
    }

    // Resolve each same-transaction dependency component as one ownership
    // unit before mutating any Body. Root requirements spread upward to every
    // consumer as well as downward to helpers; conflicting Body requirements
    // likewise force the unowned component to document root. This fixed point
    // makes ownership independent of signal/queue order.
    for (auto& [key, owners] : ownershipByTransaction) {
        const auto& created = createdByTransaction.at(key);
        std::map<long, std::set<long>> adjacency;
        for (long objectId : created) {
            adjacency[objectId];
            auto* object = key.first->getObjectByID(objectId);
            if (!object) {
                continue;
            }
            for (auto* dependency : object->getOutList()) {
                if (!dependency || dependency->getDocument() != key.first
                    || !created.contains(dependency->getID())) {
                    continue;
                }
                adjacency[objectId].insert(dependency->getID());
                adjacency[dependency->getID()].insert(objectId);
            }
        }

        std::set<long> resolved;
        for (const auto& [seed, unused] : adjacency) {
            (void)unused;
            if (resolved.contains(seed)) {
                continue;
            }
            std::set<long> component;
            std::vector<long> work {seed};
            while (!work.empty()) {
                const long objectId = work.back();
                work.pop_back();
                if (!component.insert(objectId).second) {
                    continue;
                }
                const auto& neighbours = adjacency.at(objectId);
                work.insert(work.end(), neighbours.begin(), neighbours.end());
            }
            resolved.insert(component.begin(), component.end());

            bool requiresRoot = false;
            std::set<PartDesign::Body*> requiredBodies;
            std::set<PartDesign::Body*> fallbackBodies;
            for (long objectId : component) {
                if (const auto found = owners.find(objectId); found != owners.end()) {
                    if (found->second.ownership == InferredOwnership::DocumentRoot) {
                        requiresRoot = true;
                    }
                    else if (found->second.ownership == InferredOwnership::Body) {
                        if (auto* body = freecad_cast<PartDesign::Body*>(found->second.body)) {
                            requiredBodies.insert(body);
                        }
                        else {
                            requiresRoot = true;
                        }
                    }
                }

                auto* object = key.first->getObjectByID(objectId);
                auto* group = object ? App::GeoFeatureGroupExtension::getGroupOfObject(object)
                                     : nullptr;
                if (!group && object) {
                    group = App::GroupExtension::getGroupOfObject(object);
                }
                if (auto* body = freecad_cast<PartDesign::Body*>(group)) {
                    requiredBodies.insert(body);
                }
                else if (group) {
                    requiresRoot = true;
                }

                const auto fallbackTransaction = fallbackBodiesByTransaction.find(key);
                if (fallbackTransaction != fallbackBodiesByTransaction.end()) {
                    const auto fallback = fallbackTransaction->second.find(objectId);
                    if (fallback != fallbackTransaction->second.end()) {
                        if (auto* body = freecad_cast<PartDesign::Body*>(
                                key.first->getObjectByID(fallback->second)
                            )) {
                            fallbackBodies.insert(body);
                        }
                    }
                }
            }

            InferredOwner componentOwner {
                InferredOwnership::DocumentRoot,
                nullptr,
            };
            if (!requiresRoot && requiredBodies.size() == 1) {
                componentOwner = {
                    InferredOwnership::Body,
                    *requiredBodies.begin(),
                };
            }
            else if (!requiresRoot && requiredBodies.empty() && fallbackBodies.size() == 1) {
                componentOwner = {
                    InferredOwnership::Body,
                    *fallbackBodies.begin(),
                };
            }

            for (long objectId : component) {
                if (!owners.contains(objectId) || fixedGroupsByTransaction[key].contains(objectId)) {
                    continue;
                }
                owners.insert_or_assign(objectId, componentOwner);
            }
        }
    }

    struct AdoptedResult
    {
        const PendingResult* pending;
        App::DocumentObject* result;
        PartDesign::Body* body;
        bool newlyAdopted;
    };

    std::vector<AdoptedResult> adoptedResults;
    std::set<App::Document*> documentsToRecompute;
    for (const auto& item : queued) {
        auto* result = item.document->getObjectByID(item.objectId);
        if (!isOrdinaryPartResult(result)) {
            continue;
        }

        const TransactionKey key {item.document, item.transactionId};
        const auto owner = ownershipByTransaction.at(key).at(item.objectId);
        if (owner.ownership == InferredOwnership::DocumentRoot) {
            continue;
        }

        auto* body = owner.ownership == InferredOwnership::Body
            ? freecad_cast<PartDesign::Body*>(owner.body)
            : freecad_cast<PartDesign::Body*>(item.document->getObjectByID(item.bodyId));
        if (!body) {
            continue;
        }

        const bool alreadyAdopted = App::GeoFeatureGroupExtension::getGroupOfObject(result) == body;
        auto* adoptedBody = adoptPartResult(result, body);
        if (!adoptedBody) {
            throw Base::RuntimeError(
                std::string("Part Design could not place completed Part result '") + item.objectName
                + "' in Body '" + body->getNameInDocument() + "'"
            );
        }

        adoptedResults.push_back({&item, result, adoptedBody, !alreadyAdopted});
        documentsToRecompute.insert(item.document);
    }

    // Establish ownership for every member of a same-transaction graph before
    // recomputing it.  Recomputing after only a dependency has entered a Body
    // leaves its still-unowned consumer with a temporarily out-of-scope link,
    // even though the completed graph is valid.  One recompute per affected
    // document makes the transaction visible to the model as one atomic
    // ownership change.
    for (auto* document : documentsToRecompute) {
        document->recompute();
    }

    for (const auto& adoptedResult : adoptedResults) {
        const auto& item = *adoptedResult.pending;
        const auto shape = Part::Feature::getTopoShape(adoptedResult.result, Part::ShapeOption::NoFlag);
        if (!adoptedResult.result->isValid() || shape.isNull() || !shape.isValid()
            || !adoptedResult.body->isValid()
            || PartDesign::Body::findBodyOf(adoptedResult.result) != adoptedResult.body) {
            throw Base::RuntimeError(
                std::string("Part Design Body adoption invalidated '") + item.objectName + "'"
            );
        }
        if (adoptedResult.newlyAdopted && item.document->hasPendingTransaction()
            && Gui::Application::Instance) {
            if (auto* manager = Gui::Application::Instance->macroManager()) {
                manager->addLine(Gui::MacroManager::App, "import PartDesignGui");
                const std::string line = std::format(
                    "PartDesignGui.adoptPartResult("
                    "App.getDocument('{}').getObject('{}'), "
                    "App.getDocument('{}').getObject('{}'))",
                    item.document->getName(),
                    adoptedResult.result->getNameInDocument(),
                    item.document->getName(),
                    adoptedResult.body->getNameInDocument()
                );
                manager->addLine(Gui::MacroManager::App, line.c_str());
            }
        }
    }

    for (const auto& item : queued) {
        auto* result = item.document->getObjectByID(item.objectId);
        if (!isOrdinaryPartResult(result)) {
            continue;
        }
        const TransactionKey key {item.document, item.transactionId};
        const auto fixedTransaction = fixedGroupsByTransaction.find(key);
        if (fixedTransaction != fixedGroupsByTransaction.end()) {
            const auto fixed = fixedTransaction->second.find(item.objectId);
            if (fixed != fixedTransaction->second.end()) {
                auto* current = App::GeoFeatureGroupExtension::getGroupOfObject(result);
                if (!current) {
                    current = App::GroupExtension::getGroupOfObject(result);
                }
                if (current != fixed->second) {
                    throw Base::RuntimeError(
                        std::string("Completed Part result '") + item.objectName
                        + "' lost its command-declared owner"
                    );
                }
                continue;
            }
        }

        const auto owner = ownershipByTransaction.at(key).at(item.objectId);
        if (owner.ownership == InferredOwnership::DocumentRoot) {
            if (App::GeoFeatureGroupExtension::getGroupOfObject(result)
                || App::GroupExtension::getGroupOfObject(result)) {
                throw Base::RuntimeError(
                    std::string("Document-root Part result '") + item.objectName + "' acquired an owner"
                );
            }
            continue;
        }

        auto* body = freecad_cast<PartDesign::Body*>(owner.body);
        if (!body || PartDesign::Body::findBodyOf(result) != body) {
            throw Base::RuntimeError(
                std::string("Completed Part result '") + item.objectName
                + "' does not have its inferred Body owner"
            );
        }
    }
    return adoptedResults.size();
}
