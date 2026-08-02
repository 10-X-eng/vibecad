// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ModelingSelection.h"
#include "TaskResultValidation.h"

#include <algorithm>
#include <exception>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <string_view>
#include <utility>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/DocumentObject.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/GroupExtension.h>
#include <App/Link.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/ActiveObjectList.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Macro.h>
#include <Gui/MDIView.h>
#include <Gui/Selection/Selection.h>
#include <Gui/TaskView/TaskDialog.h>
#include <Gui/ViewProviderDocumentObject.h>
#include <Mod/Part/App/BodyBase.h>
#include <Mod/Part/App/PartFeature.h>

namespace PartGui
{

bool canStartRetainedModelingTask(const App::Document* document)
{
    return document
        && (document->getBookedTransactionID() == App::NullTransaction
            || Gui::TaskView::TaskDialog::hasOwnedEnclosingTransaction(document));
}

ModelingResultOwner inferModelingResultOwner(
    const App::DocumentObject& result,
    const std::set<long>& trackedResultIds
)
{
    std::set<const App::DocumentObject*> visited;
    std::vector<App::DocumentObject*> pending = result.getOutList();
    Part::BodyBase* commonBody = nullptr;
    bool foundEstablishedOperand = false;
    while (!pending.empty()) {
        auto* operand = pending.back();
        pending.pop_back();
        if (!operand || !visited.insert(operand).second) {
            continue;
        }

        if (operand->getDocument() != result.getDocument()) {
            return {ModelingResultOwnership::DocumentRoot, nullptr};
        }

        auto* geoOwner = App::GeoFeatureGroupExtension::getGroupOfObject(operand);
        auto* groupOwner = App::GroupExtension::getGroupOfObject(operand);
        const bool hasEstablishedOwner = geoOwner || groupOwner;
        if (!trackedResultIds.contains(operand->getID()) || hasEstablishedOwner) {
            foundEstablishedOperand = true;
            auto* body = freecad_cast<Part::BodyBase*>(operand);
            if (!body) {
                body = freecad_cast<Part::BodyBase*>(geoOwner);
            }

            // Classify the exact occurrence stored by the result. Never
            // follow a root App::Link to the Body of its definition.
            if (!body || body->getDocument() != result.getDocument() || !body->getNameInDocument()
                || (commonBody && commonBody != body)) {
                return {ModelingResultOwnership::DocumentRoot, nullptr};
            }
            commonBody = body;
            // An established operand's owner is authoritative. Traversing its
            // own Body/group links would turn unrelated siblings into result
            // dependencies.
            continue;
        }

        const auto dependencies = operand->getOutList();
        pending.insert(pending.end(), dependencies.begin(), dependencies.end());
    }

    if (!foundEstablishedOperand) {
        return {ModelingResultOwnership::Automatic, nullptr};
    }
    return {ModelingResultOwnership::Body, commonBody};
}

ModelingResultOwner inferModelingOperandOwner(
    const App::Document& resultDocument,
    const std::vector<const App::DocumentObject*>& operands
)
{
    const Part::BodyBase* commonBody = nullptr;
    bool foundOperand = false;
    std::set<const App::DocumentObject*> visited;
    for (auto* operand : operands) {
        if (!operand || !visited.insert(operand).second) {
            continue;
        }
        foundOperand = true;
        if (operand->getDocument() != &resultDocument) {
            return {ModelingResultOwnership::DocumentRoot, nullptr};
        }

        auto* body = freecad_cast<const Part::BodyBase*>(operand);
        if (!body) {
            body = freecad_cast<const Part::BodyBase*>(
                App::GeoFeatureGroupExtension::getGroupOfObject(operand)
            );
        }
        if (!body || (commonBody && commonBody != body)) {
            return {ModelingResultOwnership::DocumentRoot, nullptr};
        }
        commonBody = body;
    }

    if (!foundOperand) {
        return {ModelingResultOwnership::DocumentRoot, nullptr};
    }
    // The document owns both the operand and its Body. Returning that exact
    // owner as the later adoption target does not mutate the inspected
    // operand; adoption itself remains the caller's explicit operation.
    return {ModelingResultOwnership::Body, const_cast<Part::BodyBase*>(commonBody)};
}

void prepareModelingResultsForOperands(
    const std::vector<App::DocumentObject*>& results,
    const std::vector<const App::DocumentObject*>& operands
)
{
    if (!Gui::Application::Instance) {
        return;
    }

    std::map<App::Document*, std::vector<long>> resultIdsByDocument;
    for (auto* result : results) {
        if (result && result->getDocument() && result->getNameInDocument()) {
            resultIdsByDocument[result->getDocument()].push_back(result->getID());
        }
    }

    for (const auto& [document, resultIds] : resultIdsByDocument) {
        const auto owner = inferModelingOperandOwner(*document, operands);
        if (owner.ownership == ModelingResultOwnership::Automatic) {
            Gui::Application::Instance->prepareDurableTaskResults(*document, resultIds);
            continue;
        }

        std::vector<Gui::Application::DurableTaskResultIntent> intents;
        intents.reserve(resultIds.size());
        for (long objectId : resultIds) {
            intents.push_back({
                .objectId = objectId,
                .ownership = owner.ownership == ModelingResultOwnership::DocumentRoot
                    ? Gui::Application::DurableTaskResultOwnership::DocumentRoot
                    : Gui::Application::DurableTaskResultOwnership::ExactOwner,
                .ownerObjectId = owner.body ? owner.body->getID() : -1,
            });
        }
        Gui::Application::Instance->prepareDurableTaskResults(*document, resultIds, intents);
    }
}

void groupModelingCommandOutputs(const std::vector<App::DocumentObject*>& outputs)
{
    std::vector<App::DocumentObject*> exactOutputs;
    exactOutputs.reserve(outputs.size());
    App::Document* document = nullptr;
    for (auto* output : outputs) {
        if (!output || !output->getDocument() || !output->getNameInDocument()
            || !output->getDocument()->containsObject(output)) {
            throw Base::ValueError("A grouped modeling output must be live in its document");
        }
        if (!document) {
            document = output->getDocument();
        }
        if (output->getDocument() != document) {
            throw Base::ValueError("Grouped modeling outputs must share one document");
        }
        if (std::ranges::find(exactOutputs, output) == exactOutputs.end()) {
            exactOutputs.push_back(output);
        }
    }
    if (exactOutputs.size() < 2) {
        return;
    }

    const auto ensureTimelineProperty =
        [](App::DocumentObject& object, const char* type, const char* name, const char* description) {
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

    auto* operation = exactOutputs.back();
    auto* operationRole = dynamic_cast<App::PropertyString*>(ensureTimelineProperty(
        *operation,
        "App::PropertyString",
        App::DocumentTimeline::RolePropertyName,
        "Document timeline classification"
    ));
    if (!operationRole) {
        throw Base::TypeError("Modeling operation timeline metadata has an incompatible type");
    }
    if (auto* ownerProperty = operation->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)) {
        ownerProperty->setStatus(App::Property::Hidden, true);
        ownerProperty->setStatus(App::Property::LockDynamic, true);
        ownerProperty->setStatus(App::Property::NoRecompute, true);
        auto* owner = dynamic_cast<App::PropertyLinkHidden*>(ownerProperty);
        if (!owner || owner->getValue()) {
            throw Base::TypeError("A root modeling operation cannot retain resource-owner metadata");
        }
    }
    operationRole->setValue(App::DocumentTimeline::OperationRole);

    for (std::size_t index = 0; index + 1 < exactOutputs.size(); ++index) {
        auto* resource = exactOutputs[index];
        if (resource->getPropertyByName(App::DocumentTimeline::ReplacedInputsPropertyName)) {
            throw Base::TypeError("A modeling resource cannot carry replaced-input metadata");
        }
        auto* resourceRole = dynamic_cast<App::PropertyString*>(ensureTimelineProperty(
            *resource,
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName,
            "Document timeline classification"
        ));
        auto* resourceOwner = dynamic_cast<App::PropertyLinkHidden*>(ensureTimelineProperty(
            *resource,
            "App::PropertyLinkHidden",
            App::DocumentTimeline::OwnerPropertyName,
            "Modeling operation which owns this generated result"
        ));
        if (!resourceRole || !resourceOwner) {
            throw Base::TypeError("Modeling resource timeline metadata has an incompatible type");
        }
        if (resourceOwner->getValue() && resourceOwner->getValue() != operation) {
            throw Base::ValueError("A modeling output already belongs to another timeline operation");
        }
        resourceOwner->setValue(operation);
        resourceRole->setValue(App::DocumentTimeline::ResourceRole);
    }

    App::DocumentTimeline::ensure(document)->finalizeProvisionalOperationBlock(operation, exactOutputs);
}

App::DocumentObject* resolveModelingObject(App::DocumentObject* object)
{
    if (!object) {
        return nullptr;
    }
    if (auto* body = freecad_cast<Part::BodyBase*>(object)) {
        return body->getModelingState();
    }
    auto* owner = freecad_cast<Part::BodyBase*>(
        App::GeoFeatureGroupExtension::getGroupOfObject(object)
    );
    if (owner && owner->getModelingPresentation() == object) {
        return owner->getModelingState();
    }
    return object;
}

const App::DocumentObject* resolveModelingObject(const App::DocumentObject* object)
{
    if (!object) {
        return nullptr;
    }
    if (auto* body = freecad_cast<const Part::BodyBase*>(object)) {
        return body->getModelingState();
    }
    const auto* owner = freecad_cast<const Part::BodyBase*>(
        App::GeoFeatureGroupExtension::getGroupOfObject(object)
    );
    if (owner && owner->getModelingPresentation() == object) {
        return owner->getModelingState();
    }
    return object;
}

namespace
{
const Part::BodyBase*
bodyContainingExactModelingState(const App::DocumentObject* object) noexcept
{
    try {
        if (!object || !object->getDocument()) {
            return nullptr;
        }

        const Part::BodyBase* result = nullptr;
        for (auto* candidate :
             object->getDocument()->getObjectsOfType<Part::BodyBase>()) {
            if (!candidate || !candidate->containsModelingState(object)) {
                continue;
            }
            if (result && result != candidate) {
                return nullptr;
            }
            result = candidate;
        }
        return result;
    }
    catch (...) {
        return nullptr;
    }
}

const Part::BodyBase* uniqueBodyInGroup(const App::DocumentObject* object) noexcept
{
    try {
        auto* extension =
            object ? object->getExtensionByType<App::GroupExtension>() : nullptr;
        if (!extension) {
            return nullptr;
        }

        const Part::BodyBase* result = nullptr;
        std::set<const App::DocumentObject*> visited;
        std::vector<const App::DocumentObject*> pending;
        pending.reserve(extension->getObjects().size());
        pending.insert(
            pending.end(),
            extension->getObjects().begin(),
            extension->getObjects().end()
        );
        while (!pending.empty()) {
            const auto* candidate = pending.back();
            pending.pop_back();
            if (!candidate || !visited.insert(candidate).second) {
                continue;
            }
            if (const auto* body = freecad_cast<const Part::BodyBase*>(candidate)) {
                if (result && result != body) {
                    return nullptr;
                }
                result = body;
                continue;
            }
            if (auto* childGroup = candidate->getExtensionByType<App::GroupExtension>()) {
                pending.insert(
                    pending.end(),
                    childGroup->getObjects().begin(),
                    childGroup->getObjects().end()
                );
            }
        }
        return result;
    }
    catch (...) {
        return nullptr;
    }
}

const App::DocumentObject* linkedModelingObject(const App::DocumentObject* object) noexcept
{
    try {
        const auto* linked = object ? object->getLinkedObject(true) : nullptr;
        return linked && linked != object ? linked : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}
}  // namespace

Part::BodyBase* findModelingBody(App::DocumentObject* object) noexcept
{
    return const_cast<Part::BodyBase*>(
        findModelingBody(static_cast<const App::DocumentObject*>(object))
    );
}

const Part::BodyBase* findModelingBody(const App::DocumentObject* object) noexcept
{
    if (!object) {
        return nullptr;
    }
    if (const auto* body = freecad_cast<const Part::BodyBase*>(object)) {
        return body;
    }
    if (const auto* owner = freecad_cast<const Part::BodyBase*>(
            App::GeoFeatureGroupExtension::getGroupOfObject(object)
        )) {
        return owner;
    }
    if (const auto* body = bodyContainingExactModelingState(object)) {
        return body;
    }

    if (const auto* linked = linkedModelingObject(object)) {
        if (const auto* body = freecad_cast<const Part::BodyBase*>(linked)) {
            return body;
        }
        if (const auto* owner = freecad_cast<const Part::BodyBase*>(
                App::GeoFeatureGroupExtension::getGroupOfObject(linked)
            )) {
            return owner;
        }
        if (const auto* body = bodyContainingExactModelingState(linked)) {
            return body;
        }
        if (const auto* body = uniqueBodyInGroup(linked)) {
            return body;
        }
    }
    return uniqueBodyInGroup(object);
}

App::DocumentObject*
resolveModelingObjectForBody(App::DocumentObject* object, const Part::BodyBase* body) noexcept
{
    return const_cast<App::DocumentObject*>(resolveModelingObjectForBody(
        static_cast<const App::DocumentObject*>(object),
        body,
        body ? body->getModelingState() : nullptr
    ));
}

const App::DocumentObject* resolveModelingObjectForBody(
    const App::DocumentObject* object,
    const Part::BodyBase* body
) noexcept
{
    return resolveModelingObjectForBody(
        object,
        body,
        body ? body->getModelingState() : nullptr
    );
}

App::DocumentObject* resolveModelingObjectForBody(
    App::DocumentObject* object,
    const Part::BodyBase* body,
    App::DocumentObject* bodyResult
) noexcept
{
    return const_cast<App::DocumentObject*>(resolveModelingObjectForBody(
        static_cast<const App::DocumentObject*>(object),
        body,
        static_cast<const App::DocumentObject*>(bodyResult)
    ));
}

const App::DocumentObject* resolveModelingObjectForBody(
    const App::DocumentObject* object,
    const Part::BodyBase* body,
    const App::DocumentObject* bodyResult
) noexcept
{
    if (!object) {
        return nullptr;
    }

    const auto* ordinary = resolveModelingObject(object);
    if (!body || findModelingBody(object) != body) {
        return ordinary;
    }

    if (object == body
        || object->hasExtension(App::GroupExtension::getExtensionClassTypeId())) {
        return bodyResult;
    }

    const auto* linked = linkedModelingObject(object);
    if (!linked) {
        return ordinary;
    }
    if (linked == body) {
        return bodyResult;
    }
    if (linked->hasExtension(App::GroupExtension::getExtensionClassTypeId())
        && findModelingBody(linked) == body) {
        return bodyResult;
    }
    return findModelingBody(linked) == body ? resolveModelingObject(linked) : ordinary;
}

void resolveModelingReferencesForBody(
    App::PropertyLinkSubList& references,
    const Part::BodyBase* body
)
{
    if (!body) {
        return;
    }

    std::vector<App::PropertyLinkSubList::SubSet> normalized;
    for (const auto& [object, subElements] : references.getSubListValues()) {
        auto* resolved = resolveModelingObjectForBody(object, body);
        if (!resolved || resolved == body) {
            continue;
        }

        auto existing = std::ranges::find(normalized, resolved, [](const auto& item) {
            return item.first;
        });
        if (existing == normalized.end()) {
            normalized.emplace_back(resolved, subElements);
            continue;
        }
        for (const auto& subElement : subElements) {
            if (std::ranges::find(existing->second, subElement) == existing->second.end()) {
                existing->second.push_back(subElement);
            }
        }
    }
    references.setSubListValues(normalized);
}

namespace
{
bool isExactTimelineObjectActive(const App::DocumentObject* object) noexcept
{
    try {
        if (!App::DocumentTimeline::isObjectUsableAtCurrentPosition(object)) {
            return false;
        }

        const auto* linked = object->getLinkedObject(true);
        if (!linked || linked == object) {
            return true;
        }
        return App::DocumentTimeline::isObjectUsableAtCurrentPosition(linked);
    }
    catch (...) {
        return false;
    }
}
}  // namespace

bool isModelingObjectActive(const App::DocumentObject* object) noexcept
{
    if (!object) {
        return false;
    }

    const auto* resolved = resolveModelingObject(object);
    // A Design Body and its stable publication are intentionally classified
    // as timeline-internal presentation objects. Their exact current Body
    // state—not that presentation classification—determines whether viewport
    // selection is a usable modeling input at the current History position.
    if (resolved && resolved != object) {
        return isExactTimelineObjectActive(resolved);
    }

    // An empty Body is still a selectable structural identity even though it
    // has no state to use as a shape operand yet.
    if (!resolved) {
        auto* document = object->getDocument();
        return object->isDerivedFrom<Part::BodyBase>() && document
            && document->containsObject(object)
            && !object->testStatus(App::ObjectStatus::Remove)
            && !object->testStatus(App::ObjectStatus::Destroy);
    }
    return isExactTimelineObjectActive(object);
}

App::DocumentObject* resolveModelingPresentationObject(App::DocumentObject* object)
{
    return const_cast<App::DocumentObject*>(
        resolveModelingPresentationObject(static_cast<const App::DocumentObject*>(object))
    );
}

const App::DocumentObject* resolveModelingPresentationObject(const App::DocumentObject* object)
{
    if (!object) {
        return nullptr;
    }
    if (object->isDerivedFrom<Part::BodyBase>()) {
        return object;
    }

    const auto* resolved = resolveModelingObject(object);
    auto* owner = resolved ? App::GeoFeatureGroupExtension::getGroupOfObject(resolved) : nullptr;
    if (owner && owner->isDerivedFrom<Part::BodyBase>()) {
        return owner;
    }
    if (!linkedModelingObject(object)) {
        if (const auto* body = bodyContainingExactModelingState(resolved)) {
            return body;
        }
    }
    return object;
}

std::vector<App::DocumentObject*> resolveModelingObjects(
    const std::vector<App::DocumentObject*>& objects
)
{
    std::vector<App::DocumentObject*> result;
    result.reserve(objects.size());
    for (auto* raw : objects) {
        auto* object = resolveModelingObject(raw);
        if (object && isModelingObjectActive(raw)
            && std::ranges::find(result, object) == result.end()) {
            result.push_back(object);
        }
    }
    return result;
}

Gui::SelectionObject resolveModelingSelection(const Gui::SelectionObject& selection)
{
    auto* object = selection.getObject();
    auto* resolved = resolveModelingObject(object);
    return object == resolved ? selection : selection.withObject(resolved);
}

Gui::SelectionObject resolveModelingSelectionForBody(
    const Gui::SelectionObject& selection,
    const Part::BodyBase* body
)
{
    auto* object = selection.getObject();
    auto* resolved = resolveModelingObjectForBody(object, body);
    return object == resolved ? selection : selection.withObject(resolved);
}

std::vector<Gui::SelectionObject> resolveModelingSelections(
    const std::vector<Gui::SelectionObject>& selection
)
{
    std::vector<Gui::SelectionObject> result;
    result.reserve(selection.size());
    for (const auto& raw : selection) {
        if (!isModelingObjectActive(raw.getObject())) {
            continue;
        }
        auto resolved = resolveModelingSelection(raw);
        const bool duplicate
            = std::ranges::any_of(result, [&resolved](const Gui::SelectionObject& current) {
                  return current.getObject() == resolved.getObject()
                      && current.getSubNames() == resolved.getSubNames();
              });
        if (resolved.getObject() && !duplicate) {
            result.push_back(std::move(resolved));
        }
    }
    return result;
}

std::vector<Gui::SelectionObject> resolveModelingSelectionsForBody(
    const std::vector<Gui::SelectionObject>& selection,
    const Part::BodyBase* body
)
{
    std::vector<Gui::SelectionObject> result;
    result.reserve(selection.size());
    for (const auto& raw : selection) {
        if (!isModelingObjectActive(raw.getObject())) {
            continue;
        }
        auto resolved = resolveModelingSelectionForBody(raw, body);
        const bool duplicate
            = std::ranges::any_of(result, [&resolved](const Gui::SelectionObject& current) {
                  return current.getObject() == resolved.getObject()
                      && current.getSubNames() == resolved.getSubNames();
              });
        if (resolved.getObject() && !duplicate) {
            result.push_back(std::move(resolved));
        }
    }
    return result;
}

std::vector<Gui::SelectionObject> getModelingSelection(const char* documentName)
{
    auto selection = Gui::Selection().getSelectionEx(
        documentName,
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::OldStyleElement
    );
    return resolveModelingSelections(selection);
}

std::vector<Gui::SelectionObject> getModelingSelectionForBody(
    const Part::BodyBase* body,
    const char* documentName
)
{
    auto selection = Gui::Selection().getSelectionEx(
        documentName,
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::OldStyleElement
    );
    return resolveModelingSelectionsForBody(selection, body);
}

std::vector<Gui::SelectionObject> getModelingShapeSelection(const char* documentName)
{
    auto selection = getModelingSelection(documentName);
    std::erase_if(selection, [](const Gui::SelectionObject& item) {
        return Part::Feature::getTopoShape(
                   item.getObject(),
                   Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
        )
            .isNull();
    });
    return selection;
}

bool setModelingReplacedInputs(App::DocumentObject& result, const std::vector<App::DocumentObject*>& inputs)
{
    auto* document = result.getDocument();
    if (!document || !result.getNameInDocument() || !document->containsObject(&result)) {
        throw Base::ValueError("A replaced-input result must be live in its document");
    }

    auto* group = App::GeoFeatureGroupExtension::getGroupOfObject(&result);
    if (result.isDerivedFrom<Part::BodyBase>() || (group && group->isDerivedFrom<Part::BodyBase>())) {
        return false;
    }

    const std::set<long> provisionalIds {result.getID()};
    if (inferModelingResultOwner(result, provisionalIds).ownership == ModelingResultOwnership::Body) {
        return false;
    }

    std::vector<App::DocumentObject*> exactInputs;
    exactInputs.reserve(inputs.size());
    for (auto* input : inputs) {
        if (!input || input == &result || input->getDocument() != document
            || !input->getNameInDocument() || !document->containsObject(input)) {
            throw Base::ValueError(
                "A replaced input must be a distinct live object in the result document"
            );
        }
        if (std::ranges::find(exactInputs, input) == exactInputs.end()) {
            exactInputs.push_back(input);
        }
    }
    if (exactInputs.empty()) {
        throw Base::ValueError("A replaced-input result requires at least one exact input");
    }

    App::Property* property = result.getPropertyByName(App::DocumentTimeline::ReplacedInputsPropertyName
    );
    if (!property) {
        property = result.addDynamicProperty(
            "App::PropertyLinkListHidden",
            App::DocumentTimeline::ReplacedInputsPropertyName,
            "Timeline",
            "Visible input objects hidden by this operation",
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
        throw Base::TypeError("Modeling replaced-input metadata has an incompatible type");
    }

    App::Property* roleProperty = result.getPropertyByName(App::DocumentTimeline::RolePropertyName);
    if (!roleProperty) {
        roleProperty = result.addDynamicProperty(
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
        throw Base::TypeError("Modeling timeline role metadata has an incompatible type");
    }
    if (auto* ownerProperty = result.getPropertyByName(App::DocumentTimeline::OwnerPropertyName)) {
        ownerProperty->setStatus(App::Property::Hidden, true);
        ownerProperty->setStatus(App::Property::LockDynamic, true);
        ownerProperty->setStatus(App::Property::NoRecompute, true);
        auto* owner = dynamic_cast<App::PropertyLinkHidden*>(ownerProperty);
        if (!owner || owner->getValue()) {
            throw Base::TypeError("A root modeling operation cannot retain resource-owner metadata");
        }
    }

    replacedInputs->setValues(exactInputs);
    role->setValue(App::DocumentTimeline::OperationRole);
    return true;
}

void finalizeModelingDesignDefinition(
    App::DocumentObject& definition,
    const std::vector<App::DocumentObject*>& semanticBlock
)
{
    for (auto* member : semanticBlock) {
        if (!member) {
            throw Base::RuntimeError(
                "A Design definition has a missing semantic resource"
            );
        }
        if (member->getDocument() != definition.getDocument()) {
            throw Base::ValueError(
                "A Design definition block cannot span documents"
            );
        }
        if (App::GeoFeatureGroupExtension::getGroupOfObject(member)) {
            throw Base::ValueError(
                "A Design definition resource cannot belong to a Body "
                "or another modeling group"
            );
        }
        std::vector<App::Property*> properties;
        member->getPropertyList(properties);
        for (auto* property : properties) {
            const char* rawPropertyName =
                member->getPropertyName(property);
            const std::string_view propertyName =
                rawPropertyName ? rawPropertyName : "";
            // These links describe History ownership, editing, and viewport
            // replacement. They are lifecycle metadata, not geometric
            // dependencies of the reusable definition.
            if (propertyName
                    == App::DocumentTimeline::OwnerPropertyName
                || propertyName
                    == App::DocumentTimeline::EditorPropertyName
                || propertyName
                    == App::DocumentTimeline::
                           ReplacedInputsPropertyName) {
                continue;
            }
            auto* links = freecad_cast<App::PropertyLinkBase*>(property);
            if (!links) {
                continue;
            }
            std::vector<App::DocumentObject*> targets;
            links->getLinks(targets, true);
            for (auto* target : targets) {
                if (!target) {
                    continue;
                }

                auto* root = target;
                std::set<App::DocumentObject*> owners;
                while (App::DocumentTimeline::
                           hasTimelineResourceRole(root)) {
                    if (!owners.insert(root).second) {
                        throw Base::RuntimeError(
                            "A Design definition input has a cyclic History "
                            "owner"
                        );
                    }
                    root = App::DocumentTimeline::timelineOwner(root);
                    if (!root) {
                        throw Base::RuntimeError(
                            "A Design definition input has no History root"
                        );
                    }
                }
                if (root == &definition) {
                    continue;
                }

                if (freecad_cast<App::Link*>(target)
                    || freecad_cast<App::LinkElement*>(target)) {
                    throw Base::ValueError(
                        "An assembly occurrence cannot be a modeling "
                        "definition input"
                    );
                }
                if (auto* body = findModelingBody(target)) {
                    if (target == body
                        || body->getModelingPresentation() == target
                        || !body->containsModelingState(target)) {
                        const char* diagnosticPropertyName =
                            member->getPropertyName(property);
                        throw Base::ValueError(
                            std::string("Design definition ")
                            + member->getNameInDocument() + "."
                            + (diagnosticPropertyName
                                   ? diagnosticPropertyName
                                   : "<unknown>")
                            + " references mutable Body presentation "
                            + target->getNameInDocument()
                            + "; it must reference the exact Body state"
                        );
                    }
                }
                if (resolveModelingObject(target) != target) {
                    throw Base::ValueError(
                        "A Design definition retained a mutable modeling "
                        "presentation"
                    );
                }
            }
        }
    }

    App::DocumentTimeline::finalizeDesignDefinition(definition);
}

void publishModelingDesignDefinitionBlock(
    const std::vector<App::DocumentObject*>& semanticBlock
)
{
    if (semanticBlock.empty() || !semanticBlock.back()) {
        throw Base::ValueError(
            "A Design definition block requires one semantic root"
        );
    }
    auto& definition = *semanticBlock.back();
    App::DocumentTimeline::initializeDesignDefinition(definition);
    groupModelingCommandOutputs(semanticBlock);
    finalizeModelingDesignDefinition(definition, semanticBlock);
}

class ModelingTaskAttempt::Private
{
public:
    struct ObjectIdentity
    {
        long id;
        std::string name;
    };

    struct VisibilityState
    {
        std::string name;
        bool objectVisible;
        bool viewVisible;
        bool hasViewProvider;
    };

    struct ReplacedInputIntent
    {
        long resultId;
        std::vector<long> inputIds;
    };

    explicit Private(App::Document& targetDocument)
        : document(&targetDocument)
    {
        if (Gui::Application::Instance) {
            auto* guiDocument = Gui::Application::Instance->getDocument(document->getName());
            if (guiDocument) {
                hadGuiDocument = true;
                guiDocumentModified = guiDocument->isModified();
            }
        }

        macroRedirector = std::make_unique<Gui::MacroManager::MacroRedirector>(
            [this](Gui::MacroManager::LineType type, const char* line) {
                if (line) {
                    macroLines.emplace_back(type, line);
                }
            }
        );

        for (auto* object : document->getObjects()) {
            if (!object) {
                continue;
            }
            initialObjectIds.insert(object->getID());
            if (!object->getNameInDocument()) {
                continue;
            }
            auto* viewProvider = Gui::Application::Instance
                ? Gui::Application::Instance->getViewProvider<Gui::ViewProviderDocumentObject>(object)
                : nullptr;
            visibility.push_back({
                object->getNameInDocument(),
                object->Visibility.getValue(),
                viewProvider ? viewProvider->Visibility.getValue() : false,
                viewProvider != nullptr,
            });
        }

        selection = Gui::Selection().getSelectionEx(
            "*",
            App::DocumentObject::getClassTypeId(),
            Gui::ResolveMode::NoResolve
        );

        if (auto* activeObject = document->getActiveObject();
            activeObject && activeObject->getNameInDocument()) {
            activeObjectName = activeObject->getNameInDocument();
        }

        if (!Gui::Application::Instance) {
            return;
        }
        activeView = Gui::Application::Instance->activeView();
        auto* viewDocument = activeView ? activeView->getAppDocument() : nullptr;
        if (!viewDocument || viewDocument != document) {
            activeView = nullptr;
            return;
        }

        hadActiveBody = activeView->hasActiveObject(PDBODYKEY);
        if (hadActiveBody) {
            App::DocumentObject* root = nullptr;
            activeView->getActiveObject<App::DocumentObject*>(PDBODYKEY, &root, &activeBodySubname);
            if (root && root->getNameInDocument()) {
                activeBodyRootName = root->getNameInDocument();
            }
        }
    }

    ~Private()
    {
        rollback();
    }

    std::vector<App::DocumentObject*> semanticOutputs() const
    {
        if (!document || createdObjects.empty()) {
            throw Base::RuntimeError(
                "A native modeling attempt requires at least one tracked result"
            );
        }
        auto* timeline = App::DocumentTimeline::get(document);
        if (!timeline) {
            throw Base::RuntimeError("A native modeling attempt requires a valid document timeline");
        }

        std::vector<App::DocumentObject*> trackedResults;
        trackedResults.reserve(createdObjects.size());
        std::set<long> trackedIds;
        const auto isCurrentTransactionOutput = [timeline](const App::DocumentObject* object) {
            return timeline->isProvisionallyEnrolledByCurrentTransaction(object)
                || timeline->isSemanticallyPublishedByCurrentTransaction(object);
        };
        for (const auto& identity : createdObjects) {
            auto* result = document->getObjectByID(identity.id);
            if (!result || !result->getNameInDocument()
                || identity.name != result->getNameInDocument() || result->getDocument() != document
                || !isCurrentTransactionOutput(result) || !trackedIds.insert(result->getID()).second) {
                throw Base::RuntimeError(
                    "A tracked modeling result changed identity or current-transaction history "
                    "proof before commit"
                );
            }
            trackedResults.push_back(result);
        }

        auto* operation = trackedResults.back();
        std::set<long> visiting;
        std::set<long> orderedIds;
        std::vector<App::DocumentObject*> ordered;
        std::function<void(App::DocumentObject*)> appendDependenciesFirst;
        appendDependenciesFirst = [&](App::DocumentObject* object) {
            if (!object || object->getDocument() != document
                || initialObjectIds.contains(object->getID()) || !isCurrentTransactionOutput(object)
                || orderedIds.contains(object->getID())) {
                return;
            }
            if (!visiting.insert(object->getID()).second) {
                throw Base::RuntimeError("A native modeling attempt created a cyclic result graph");
            }
            for (auto* dependency : object->getOutList()) {
                appendDependenciesFirst(dependency);
            }
            visiting.erase(object->getID());
            orderedIds.insert(object->getID());
            ordered.push_back(object);
        };

        for (auto* result : trackedResults) {
            appendDependenciesFirst(result);
        }
        if (!orderedIds.contains(operation->getID())) {
            throw Base::RuntimeError(
                "The final tracked modeling result is absent from its semantic "
                "output graph"
            );
        }

        const auto operationPosition = std::ranges::find(ordered, operation);
        if (operationPosition == ordered.end()) {
            throw Base::RuntimeError("The final tracked modeling result lost its exact identity");
        }
        ordered.erase(operationPosition);

        // The last explicitly tracked result is the one user-visible operation.
        // Every other explicitly tracked result, plus every same-transaction
        // object it recursively consumes, is an implementation resource. A
        // callback-created object with no dependency path from a result remains
        // independent and is never adopted merely because it shares a
        // transaction.
        if (std::ranges::any_of(ordered, [operation](App::DocumentObject* resource) {
                const std::vector<App::DocumentObject*> roots {resource};
                const auto dependencies = App::Document::getDependencyList(roots);
                return std::ranges::find(dependencies, operation) != dependencies.end();
            })) {
            throw Base::RuntimeError("A modeling resource cannot depend on its operation result");
        }
        ordered.push_back(operation);

        const bool includesPublishedBlock
            = std::ranges::any_of(ordered, [timeline](const App::DocumentObject* object) {
                  return timeline->isSemanticallyPublishedByCurrentTransaction(object);
              });
        if (includesPublishedBlock
            && (!std::ranges::all_of(
                    ordered,
                    [timeline](const App::DocumentObject* object) {
                        return timeline->isSemanticallyPublishedByCurrentTransaction(object);
                    }
                )
                || !timeline->isExactSemanticBlockPublishedByCurrentTransaction(operation, ordered))) {
            throw Base::RuntimeError(
                "A tracked modeling result no longer matches one exact current-transaction "
                "semantic publication"
            );
        }
        return ordered;
    }

    void rollback() noexcept
    {
        if (complete || !document || !ownsAttempt) {
            return;
        }
        const int currentTransactionId = document->getBookedTransactionID();
        if (transactionId == App::NullTransaction || currentTransactionId != transactionId) {
            Base::Console().error(
                "Native modeling attempt transaction %d was replaced by %d; "
                "the replacement transaction was not modified.\n",
                transactionId,
                currentTransactionId
            );
            macroRedirector.reset();
            macroLines.clear();
            return;
        }

        // Capture the complete attempt-owned delta before abort. Python commands
        // can create an object and throw before returning its pointer to the
        // caller, and a failed command may create more than one provisional
        // object. The initial-ID exclusion guarantees existing objects are never
        // cleanup candidates.
        try {
            for (auto* object : document->getObjects()) {
                if (!object || initialObjectIds.contains(object->getID())
                    || !object->getNameInDocument()) {
                    continue;
                }
                const auto duplicate
                    = std::ranges::find(createdObjects, object->getID(), &ObjectIdentity::id);
                if (duplicate == createdObjects.end()) {
                    createdObjects.push_back({object->getID(), object->getNameInDocument()});
                }
            }
        }
        catch (const std::exception& error) {
            Base::Console().error("Could not enumerate every failed native result: %s\n", error.what());
        }
        catch (...) {
            Base::Console().error("Could not enumerate every failed native result.\n");
        }

        bool transactionAborted = false;
        try {
            // The lock rejects every nested open/commit/abort attempt while
            // provisional geometry is live. Release it only for this exact
            // owner close.
            transactionAborted = transaction && transaction->abort();
            if (!transactionAborted) {
                Base::Console().error(
                    "Could not abort failed native modeling transaction %d.\n",
                    transactionId
                );
            }
        }
        catch (const Base::Exception& error) {
            Base::Console().error("Could not abort failed native modeling attempt: %s\n", error.what());
        }
        catch (...) {
            Base::Console().error("Could not abort failed native modeling attempt.\n");
        }

        if (!transactionAborted) {
            macroRedirector.reset();
            macroLines.clear();
            return;
        }

        transaction.reset();
        complete = true;
        ownsAttempt = false;

        for (auto item = createdObjects.rbegin(); item != createdObjects.rend(); ++item) {
            auto* object = document->getObjectByID(item->id);
            if (!object || !object->getNameInDocument() || item->name != object->getNameInDocument()) {
                continue;
            }
            try {
                document->removeObject(item->name.c_str());
            }
            catch (const Base::Exception& error) {
                Base::Console().error(
                    "Could not remove failed native result '%s': %s\n",
                    item->name.c_str(),
                    error.what()
                );
            }
            catch (...) {
                Base::Console().error(
                    "Could not remove failed native result '%s'.\n",
                    item->name.c_str()
                );
            }
        }

        if (!Gui::Application::Instance) {
            macroRedirector.reset();
            macroLines.clear();
            return;
        }
        for (const auto& item : visibility) {
            try {
                auto* object = document->getObject(item.name.c_str());
                if (object) {
                    object->Visibility.setValue(item.objectVisible);
                }
                auto* viewProvider = object
                    ? Gui::Application::Instance->getViewProvider<Gui::ViewProviderDocumentObject>(
                          object
                      )
                    : nullptr;
                if (viewProvider && item.hasViewProvider) {
                    viewProvider->Visibility.setValue(item.viewVisible);
                }
            }
            catch (const Base::Exception& error) {
                Base::Console().error(
                    "Could not restore visibility for '%s': %s\n",
                    item.name.c_str(),
                    error.what()
                );
            }
            catch (const std::exception& error) {
                Base::Console().error(
                    "Could not restore visibility for '%s': %s\n",
                    item.name.c_str(),
                    error.what()
                );
            }
            catch (...) {
                Base::Console().error("Could not restore visibility for '%s'.\n", item.name.c_str());
            }
        }

        try {
            {
                Gui::SelectionLogDisabler selectionLogDisabler(true);
                auto& currentSelection = Gui::Selection();
                currentSelection.clearSelection();
                for (const auto& selected : selection) {
                    auto* selectedDocument = App::GetApplication().getDocument(selected.getDocName());
                    if (selectedDocument && selectedDocument->getObject(selected.getFeatName())) {
                        currentSelection.addSelection(selected);
                    }
                }
            }
        }
        catch (const Base::Exception& error) {
            Base::Console().error("Could not restore failed native task selection: %s\n", error.what());
        }
        catch (const std::exception& error) {
            Base::Console().error("Could not restore failed native task selection: %s\n", error.what());
        }
        catch (...) {
            Base::Console().error("Could not restore failed native task selection.\n");
        }

        try {
            auto* activeObject = activeObjectName.empty()
                ? nullptr
                : document->getObject(activeObjectName.c_str());
            document->setActiveObject(activeObject);

            if (activeView) {
                if (hadActiveBody && !activeBodyRootName.empty()) {
                    activeView->setActiveObject(
                        document->getObject(activeBodyRootName.c_str()),
                        PDBODYKEY,
                        activeBodySubname.c_str()
                    );
                }
                else {
                    activeView->setActiveObject(nullptr, PDBODYKEY);
                }
            }
        }
        catch (const Base::Exception& error) {
            Base::Console().error(
                "Could not restore failed native task active objects: %s\n",
                error.what()
            );
        }
        catch (const std::exception& error) {
            Base::Console().error(
                "Could not restore failed native task active objects: %s\n",
                error.what()
            );
        }
        catch (...) {
            Base::Console().error("Could not restore failed native task active objects.\n");
        }

        macroRedirector.reset();
        macroLines.clear();
        restoreGuiDocumentModified();
    }

    void restoreGuiDocumentModified() noexcept
    {
        if (!hadGuiDocument || !document || !Gui::Application::Instance) {
            return;
        }
        try {
            auto* guiDocument = Gui::Application::Instance->getDocument(document->getName());
            if (guiDocument) {
                guiDocument->setModified(guiDocumentModified);
            }
        }
        catch (const Base::Exception& error) {
            Base::Console().error(
                "Could not restore failed native task modified state: %s\n",
                error.what()
            );
        }
        catch (...) {
            Base::Console().error("Could not restore failed native task modified state.\n");
        }
    }

    void publishMacroLines() noexcept
    {
        auto lines = std::move(macroLines);
        macroRedirector.reset();
        if (!Gui::Application::Instance) {
            return;
        }
        try {
            auto* manager = Gui::Application::Instance->macroManager();
            if (!manager) {
                return;
            }
            for (const auto& [type, line] : lines) {
                manager->addLine(type, line.c_str());
            }
        }
        catch (const Base::Exception& error) {
            Base::Console().warning(
                "Native result was accepted, but its macro record failed: %s\n",
                error.what()
            );
        }
        catch (const std::exception& error) {
            Base::Console().warning(
                "Native result was accepted, but its macro record failed: %s\n",
                error.what()
            );
        }
        catch (...) {
            Base::Console().warning("Native result was accepted, but its macro record failed.\n");
        }
    }

    void setResultIntent(
        App::DocumentObject& result,
        Gui::Application::DurableTaskResultOwnership ownership,
        long ownerObjectId
    )
    {
        if (result.getDocument() != document || !result.getNameInDocument()
            || initialObjectIds.contains(result.getID())
            || std::ranges::find(createdObjects, result.getID(), &ObjectIdentity::id)
                == createdObjects.end()) {
            throw Base::ValueError(
                "Ownership intent requires a result tracked by this modeling attempt"
            );
        }

        auto found = std::ranges::find(
            resultIntents,
            result.getID(),
            &Gui::Application::DurableTaskResultIntent::objectId
        );
        const Gui::Application::DurableTaskResultIntent intent {
            .objectId = result.getID(),
            .ownership = ownership,
            .ownerObjectId = ownerObjectId,
        };
        if (found == resultIntents.end()) {
            resultIntents.push_back(intent);
        }
        else {
            *found = intent;
        }
    }

    App::Document* document;
    std::set<long> initialObjectIds;
    std::vector<ObjectIdentity> createdObjects;
    std::vector<VisibilityState> visibility;
    std::vector<Gui::SelectionObject> selection;
    std::string activeObjectName;
    Gui::MDIView* activeView {};
    bool hadActiveBody {false};
    std::string activeBodyRootName;
    std::string activeBodySubname;
    std::vector<std::pair<Gui::MacroManager::LineType, std::string>> macroLines;
    std::unique_ptr<Gui::MacroManager::MacroRedirector> macroRedirector;
    std::vector<Gui::Application::DurableTaskResultIntent> resultIntents;
    std::vector<ReplacedInputIntent> replacedInputIntents;
    long designDefinitionId {-1};
    bool hadGuiDocument {false};
    bool guiDocumentModified {false};
    bool ownsAttempt {false};
    bool complete {false};
    int transactionId {App::NullTransaction};
    std::unique_ptr<Gui::ExactTransaction> transaction;
};

ModelingTaskAttempt::ModelingTaskAttempt(App::Document& document, const char* transactionName)
    : d(std::make_unique<Private>(document))
{
    if (document.getBookedTransactionID() != App::NullTransaction
        || document.hasPendingTransaction()) {
        throw Base::RuntimeError(
            "Cannot start a native modeling attempt while another transaction is open"
        );
    }
    d->transaction
        = std::make_unique<Gui::ExactTransaction>(document, transactionName ? transactionName : "");
    d->transactionId = d->transaction->id();
    d->ownsAttempt = true;
}

ModelingTaskAttempt::~ModelingTaskAttempt()
{
    if (d) {
        d->rollback();
    }
}

void ModelingTaskAttempt::trackCreatedObject(App::DocumentObject& object)
{
    if (object.getDocument() != d->document) {
        throw Base::ValueError(
            "A native modeling attempt cannot track an object from another document"
        );
    }
    if (d->initialObjectIds.contains(object.getID())) {
        throw Base::ValueError(
            "A native modeling attempt cannot track an existing object as a new result"
        );
    }
    if (!object.getNameInDocument()) {
        throw Base::ValueError("A native modeling attempt cannot track a detached result");
    }
    const auto duplicate
        = std::ranges::find(d->createdObjects, object.getID(), &Private::ObjectIdentity::id);
    if (duplicate == d->createdObjects.end()) {
        d->createdObjects.push_back({object.getID(), object.getNameInDocument()});
    }
}

void ModelingTaskAttempt::keepResultAtDocumentRoot(App::DocumentObject& result)
{
    d->setResultIntent(result, Gui::Application::DurableTaskResultOwnership::DocumentRoot, -1);
}

void ModelingTaskAttempt::markResultAsDesignDefinition(
    App::DocumentObject& result
)
{
    if (d->designDefinitionId >= 0
        && d->designDefinitionId != result.getID()) {
        throw Base::ValueError(
            "One native modeling attempt can publish only one Design "
            "definition root"
        );
    }
    d->setResultIntent(
        result,
        Gui::Application::DurableTaskResultOwnership::DocumentRoot,
        -1
    );
    App::DocumentTimeline::initializeDesignDefinition(result);
    d->designDefinitionId = result.getID();
}

void ModelingTaskAttempt::targetResultBody(App::DocumentObject& result, Part::BodyBase& body)
{
    if (body.getDocument() != d->document || !body.getNameInDocument()) {
        throw Base::ValueError("A modeling result Body target must belong to the result document");
    }
    d->setResultIntent(result, Gui::Application::DurableTaskResultOwnership::ExactOwner, body.getID());
}

void ModelingTaskAttempt::trackReplacedInputs(
    App::DocumentObject& result,
    const std::vector<App::DocumentObject*>& inputs
)
{
    if (result.getDocument() != d->document || !result.getNameInDocument()
        || d->initialObjectIds.contains(result.getID())
        || std::ranges::find(d->createdObjects, result.getID(), &Private::ObjectIdentity::id)
            == d->createdObjects.end()) {
        throw Base::ValueError("Replaced inputs require a result tracked by this modeling attempt");
    }

    std::vector<long> inputIds;
    inputIds.reserve(inputs.size());
    for (auto* input : inputs) {
        if (!input || input == &result || input->getDocument() != d->document
            || !input->getNameInDocument() || !d->document->containsObject(input)) {
            throw Base::ValueError(
                "A modeling replacement input must be distinct and live in the result document"
            );
        }
        if (std::ranges::find(inputIds, input->getID()) == inputIds.end()) {
            inputIds.push_back(input->getID());
        }
    }
    if (inputIds.empty()) {
        throw Base::ValueError("A modeling replacement requires at least one exact input");
    }

    auto found = std::ranges::find(
        d->replacedInputIntents,
        result.getID(),
        &Private::ReplacedInputIntent::resultId
    );
    const Private::ReplacedInputIntent intent {
        .resultId = result.getID(),
        .inputIds = std::move(inputIds),
    };
    if (found == d->replacedInputIntents.end()) {
        d->replacedInputIntents.push_back(intent);
    }
    else {
        *found = intent;
    }
}

void ModelingTaskAttempt::commit()
{
    if (d->complete) {
        throw Base::RuntimeError("Native modeling attempt is already closed");
    }
    if (!d->ownsAttempt || d->transactionId == App::NullTransaction
        || d->document->getBookedTransactionID() != d->transactionId) {
        throw Base::RuntimeError("Native modeling attempt no longer owns its transaction");
    }
    const auto resultObjects = d->semanticOutputs();
    auto* timeline = App::DocumentTimeline::get(d->document);
    if (!timeline) {
        throw Base::RuntimeError("Native modeling commit lost its document timeline");
    }
    const bool blockWasAlreadyPublished = timeline->isExactSemanticBlockPublishedByCurrentTransaction(
        resultObjects.back(),
        resultObjects
    );
    std::vector<long> semanticResultIds;
    semanticResultIds.reserve(resultObjects.size());
    for (const auto* result : resultObjects) {
        semanticResultIds.push_back(result->getID());
    }
    const std::set<long> trackedResultIds(semanticResultIds.begin(), semanticResultIds.end());
    const std::vector<App::DocumentObject*> durableResults = blockWasAlreadyPublished
        ? std::vector<App::DocumentObject*> {resultObjects.back()}
        : resultObjects;
    std::vector<long> durableResultIds;
    durableResultIds.reserve(durableResults.size());
    for (const auto* result : durableResults) {
        durableResultIds.push_back(result->getID());
    }

    auto ownershipIntents = d->resultIntents;
    for (auto* object : durableResults) {
        auto explicitIntent = std::ranges::find(
            ownershipIntents,
            object->getID(),
            &Gui::Application::DurableTaskResultIntent::objectId
        );
        if (d->designDefinitionId >= 0) {
            const Gui::Application::DurableTaskResultIntent designIntent {
                .objectId = object->getID(),
                .ownership = Gui::Application::DurableTaskResultOwnership::DocumentRoot,
                .ownerObjectId = -1,
            };
            if (explicitIntent == ownershipIntents.end()) {
                ownershipIntents.push_back(designIntent);
            }
            else {
                *explicitIntent = designIntent;
            }
            continue;
        }
        if (explicitIntent != ownershipIntents.end()) {
            continue;
        }
        const auto inferred = inferModelingResultOwner(*object, trackedResultIds);
        if (inferred.ownership == ModelingResultOwnership::DocumentRoot) {
            ownershipIntents.push_back({
                .objectId = object->getID(),
                .ownership = Gui::Application::DurableTaskResultOwnership::DocumentRoot,
                .ownerObjectId = -1,
            });
        }
        else if (inferred.ownership == ModelingResultOwnership::Body) {
            ownershipIntents.push_back({
                .objectId = object->getID(),
                .ownership = Gui::Application::DurableTaskResultOwnership::ExactOwner,
                .ownerObjectId = inferred.body->getID(),
            });
        }
    }

    // Ownership is part of the modeling result, not a post-commit
    // presentation side effect. Explicit/inferred intent prevents a selected
    // root occurrence from being pulled into an unrelated active Body. A
    // directly published semantic block is already one durable root with an
    // owned dependency graph; preparing its resource children as independent
    // results would incorrectly reparent them. The result preparer recursively
    // consumes the root's newly created dependency graph itself.
    if (Gui::Application::Instance && !durableResultIds.empty()) {
        if (ownershipIntents.empty()) {
            Gui::Application::Instance->prepareDurableTaskResults(*d->document, durableResultIds);
        }
        else {
            Gui::Application::Instance
                ->prepareDurableTaskResults(*d->document, durableResultIds, ownershipIntents);
        }
    }

    if (blockWasAlreadyPublished) {
        if (!timeline->isExactSemanticBlockPublishedByCurrentTransaction(
                resultObjects.back(),
                resultObjects
            )) {
            throw Base::RuntimeError(
                "A directly published modeling block changed during result ownership preparation"
            );
        }
    }
    else {
        groupModelingCommandOutputs(resultObjects);
    }

    if (resultObjects.size() > 1) {
        std::vector<App::DocumentObject*> inputs;
        for (const auto& intent : d->replacedInputIntents) {
            for (const long inputId : intent.inputIds) {
                auto* input = d->document->getObjectByID(inputId);
                if (!input) {
                    throw Base::RuntimeError(
                        "A modeling replacement input was removed before commit"
                    );
                }
                if (std::ranges::find(inputs, input) == inputs.end()) {
                    inputs.push_back(input);
                }
            }
        }
        if (!inputs.empty()) {
            setModelingReplacedInputs(*resultObjects.back(), inputs);
        }
    }
    else {
        for (const auto& intent : d->replacedInputIntents) {
            auto* result = d->document->getObjectByID(intent.resultId);
            if (!result) {
                throw Base::RuntimeError("A modeling replacement result was removed before commit");
            }
            std::vector<App::DocumentObject*> inputs;
            inputs.reserve(intent.inputIds.size());
            for (const long inputId : intent.inputIds) {
                auto* input = d->document->getObjectByID(inputId);
                if (!input) {
                    throw Base::RuntimeError(
                        "A modeling replacement input was removed before commit"
                    );
                }
                inputs.push_back(input);
            }
            setModelingReplacedInputs(*result, inputs);
        }
    }

    d->document->recompute();
    for (const long resultId : semanticResultIds) {
        auto* object = d->document->getObjectByID(resultId);
        if (object && object->isDerivedFrom<Part::Feature>()) {
            TaskResultValidation::validatePartResult(object);
        }
    }
    if (d->designDefinitionId >= 0) {
        auto* definition =
            d->document->getObjectByID(d->designDefinitionId);
        if (!definition || definition != resultObjects.back()) {
            throw Base::RuntimeError(
                "The accepted Design definition is not the semantic root of "
                "its modeling attempt"
            );
        }
        finalizeModelingDesignDefinition(
            *definition,
            resultObjects
        );
        const std::string definitionExpression =
            Gui::Command::getObjectCmd(definition);
        d->macroLines.emplace_back(
            Gui::MacroManager::App,
            "import PartDesign"
        );
        d->macroLines.emplace_back(
            Gui::MacroManager::App,
            "PartDesign.initializeDesignDefinition("
                + definitionExpression + ")"
        );
        d->macroLines.emplace_back(
            Gui::MacroManager::App,
            "PartDesign.finalizeDesignDefinition("
                + definitionExpression + ")"
        );
    }
    if (blockWasAlreadyPublished
        && !timeline->isExactSemanticBlockPublishedByCurrentTransaction(
            resultObjects.back(),
            resultObjects
        )) {
        throw Base::RuntimeError(
            "A directly published modeling block changed before transaction commit"
        );
    }

    if (d->document->getBookedTransactionID() != d->transactionId) {
        throw Base::RuntimeError("Native modeling transaction was replaced before commit");
    }
    if (!d->transaction || !d->transaction->commit()) {
        throw Base::RuntimeError("Could not commit native modeling transaction");
    }
    d->transaction.reset();
    d->complete = true;
    d->ownsAttempt = false;
    d->publishMacroLines();
}

}  // namespace PartGui
