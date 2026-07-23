// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ModelingContext.h"

#include <algorithm>
#include <functional>
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
#include <Gui/MDIView.h>
#include <Mod/Part/App/BodyBase.h>
#include <Mod/Part/App/DatumFeature.h>
#include <Mod/Part/App/Part2DObject.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/Feature.h>

using namespace PartDesignGui;

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
    beforeCloseTransactionConnection = application.signalBeforeCloseTransaction.connect(
        std::bind(&ModelingContext::beforeCloseTransaction, this, std::placeholders::_1)
    );
    deleteDocumentConnection = application.signalDeleteDocument.connect(
        std::bind(&ModelingContext::clearDocument, this, std::placeholders::_1)
    );
}

bool ModelingContext::isOrdinaryPartResult(const App::DocumentObject* object)
{
    return object && object->isDerivedFrom<Part::Feature>()
        && !object->isDerivedFrom<PartDesign::Feature>()
        && !object->isDerivedFrom<Part::Part2DObject>()
        && !object->isDerivedFrom<Part::BodyBase>() && !object->isDerivedFrom<Part::Datum>();
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

PartDesign::Body*
ModelingContext::adoptPartResult(App::DocumentObject* result, PartDesign::Body* body) const
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
        originalStates.push_back(
            {
                object,
                object->Visibility.getValue(),
                feature,
                feature ? feature->BaseFeature.getValue() : nullptr,
                feature ? feature->_Body.getValue() : nullptr,
                std::move(linkScopes),
            }
        );
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
    if (!document || document->testStatus(App::Document::Restoring)
        || !document->hasPendingTransaction() || !isOrdinaryPartResult(&object)) {
        return;
    }
    auto* body = activeBodyFor(document);
    if (!body) {
        return;
    }

    const int transactionId = document->getTransactionID(true);
    if (transactionId == App::NullTransaction) {
        return;
    }

    const auto duplicate = std::ranges::find_if(pending, [&](const PendingResult& item) {
        return item.document == document && item.objectId == object.getID()
            && item.transactionId == transactionId;
    });
    if (duplicate == pending.end()) {
        pending.push_back(
            {
                document,
                object.getID(),
                body->getID(),
                transactionId,
                object.getNameInDocument(),
            }
        );
    }
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
            if (!application.transactionIsActive(item.transactionId)) {
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

void ModelingContext::clearDocument(const App::Document& document)
{
    std::erase_if(pending, [&](const PendingResult& item) {
        return item.document == &document;
    });
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

    for (const auto& item : queued) {
        auto* result = item.document->getObjectByID(item.objectId);
        auto* body
            = freecad_cast<PartDesign::Body*>(item.document->getObjectByID(item.bodyId));
        if (!result || !body) {
            continue;
        }
        try {
            adoptPartResult(result, body);
        }
        catch (const Base::Exception& error) {
            Base::Console().error(
                "Part Design could not place completed Part result '%s': %s\n",
                item.objectName.c_str(),
                error.what()
            );
        }
        catch (const std::exception& error) {
            Base::Console().error(
                "Part Design could not place completed Part result '%s': %s\n",
                item.objectName.c_str(),
                error.what()
            );
        }
    }
}
