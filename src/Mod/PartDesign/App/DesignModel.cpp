// SPDX-License-Identifier: LGPL-2.1-or-later

#include "DesignModel.h"

#include <algorithm>
#include <functional>
#include <optional>
#include <ranges>
#include <string_view>
#include <unordered_map>
#include <unordered_set>

#include <boost/dynamic_bitset.hpp>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/Datums.h>
#include <App/GeoFeature.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/GroupExtension.h>
#include <App/Link.h>
#include <App/Origin.h>
#include <App/Part.h>
#include <App/PropertyLinks.h>
#include <Base/Exception.h>
#include <Base/Uuid.h>
#include <Mod/Part/App/DatumFeature.h>
#include <Mod/Part/App/Part2DObject.h>
#include <Mod/Part/App/PartFeature.h>

#include "Body.h"
#include "Component.h"
#include "DesignFeature.h"
#include "ShapeBinder.h"

using namespace PartDesign;

void DesignModel::requireBodyShape(
    const Body& body,
    const Part::TopoShape& shape,
    std::string_view context
)
{
    requireBodyShape(
        shape,
        body.AllowCompound.getValue(),
        body.Label.getValue(),
        context
    );
}

void DesignModel::requireBodyShape(
    const Part::TopoShape& shape,
    bool allowCompound,
    std::string_view bodyLabel,
    std::string_view context
)
{
    const std::string prefix = std::string(context) + " for Body '" + std::string(bodyLabel) + "'";
    if (shape.isNull() || !shape.hasSubShape(TopAbs_SOLID)) {
        throw Base::ValueError(prefix + " must contain at least one solid");
    }

    const std::size_t solidCount = shape.countSubShapes(TopAbs_SOLID);
    if (!allowCompound && solidCount != 1) {
        throw Base::ValueError(
            prefix + " contains " + std::to_string(solidCount)
            + " solids, but Allow Compound is disabled"
        );
    }
}

namespace
{

void requireSavedBodyShape(
    const Body& body,
    const Part::TopoShape& shape,
    std::string_view context
)
{
    try {
        DesignModel::requireBodyShape(body, shape, context);
    }
    catch (const Base::Exception& error) {
        // validateDesign() has always reported persisted-contract violations as
        // RuntimeError. Keep that public diagnostic contract while sharing the
        // same topology predicate with operation-time validation.
        throw Base::RuntimeError(error.what());
    }
}

App::Property* ensureTimelineProperty(
    App::DocumentObject& object,
    const char* type,
    const char* name,
    const char* description
)
{
    auto* property = object.PropertyContainer::getPropertyByName(name);
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
}

void classifyOperation(App::DocumentObject& operation)
{
    auto* role = dynamic_cast<App::PropertyString*>(ensureTimelineProperty(
        operation,
        "App::PropertyString",
        App::DocumentTimeline::RolePropertyName,
        "Document timeline classification"
    ));
    if (!role) {
        throw Base::TypeError("A Design operation has incompatible History metadata");
    }
    if (auto* ownerProperty = operation.PropertyContainer::getPropertyByName(
            App::DocumentTimeline::OwnerPropertyName
        )) {
        auto* owner = dynamic_cast<App::PropertyLinkHidden*>(ownerProperty);
        if (!owner || owner->getValue()) {
            throw Base::TypeError("A root Design operation cannot have a resource owner");
        }
    }
    role->setValue(App::DocumentTimeline::OperationRole);
}

void classifyStateResource(DesignBodyState& state, App::DocumentObject& operation)
{
    auto* role = dynamic_cast<App::PropertyString*>(ensureTimelineProperty(
        state,
        "App::PropertyString",
        App::DocumentTimeline::RolePropertyName,
        "Document timeline classification"
    ));
    auto* owner = dynamic_cast<App::PropertyLinkHidden*>(ensureTimelineProperty(
        state,
        "App::PropertyLinkHidden",
        App::DocumentTimeline::OwnerPropertyName,
        "Design operation which owns this Body state"
    ));
    if (!role || !owner) {
        throw Base::TypeError("A Design Body state has incompatible History metadata");
    }
    if (owner->getValue() && owner->getValue() != &operation) {
        throw Base::ValueError("A Design Body state already belongs to another operation");
    }
    owner->setValue(&operation);
    role->setValue(App::DocumentTimeline::ResourceRole);
}

std::vector<App::DocumentObject*> nonTimelineConsumers(App::DocumentObject& object)
{
    std::vector<App::DocumentObject*> consumers;
    for (auto* consumer : object.getInList()) {
        if (!consumer || consumer->isDerivedFrom<App::DocumentTimeline>()) {
            continue;
        }
        consumers.push_back(consumer);
    }
    return consumers;
}

void replaceOperationInput(
    App::DocumentObject* operation,
    App::DocumentObject* oldState,
    App::DocumentObject* replacement
)
{
    auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
    if (!properties) {
        throw Base::TypeError("A downstream Design operation has no state-input contract");
    }

    auto inputs = properties->InputStates.getValues();
    bool replaced = false;
    for (auto*& input : inputs) {
        if (input == oldState) {
            input = replacement;
            replaced = true;
        }
    }
    if (!replaced) {
        throw Base::RuntimeError("A downstream Body state and its operation disagree about "
                                 "their exact input");
    }
    properties->InputStates.setValues(inputs);
}

void replaceBodyStateInChain(
    App::Document& document,
    const std::string& bodyId,
    App::DocumentObject* oldState,
    App::DocumentObject* replacement
)
{
    if (!oldState || !replacement || oldState == replacement) {
        throw Base::ValueError("A Body-state replacement must change one exact identity");
    }

    std::size_t changedStates = 0;
    for (auto* state : document.getObjectsOfType<DesignBodyState>()) {
        if (!state || state == replacement || state->BodyId.getValueStr() != bodyId
            || state->PreviousState.getValue() != oldState) {
            continue;
        }
        state->PreviousState.setValue(replacement);
        replaceOperationInput(state->Operation.getValue(), oldState, replacement);
        ++changedStates;
    }
    if (changedStates > 1) {
        throw Base::RuntimeError("The active Body history branches at one state; edit the "
                                 "branches explicitly");
    }

    auto* body = DesignModel::bodyWithId(document, bodyId);
    auto* publication = findDesignBodyPublication(body);
    if (!publication) {
        throw Base::RuntimeError("A Design Body has no stable result publication");
    }
    if (publication->CurrentState.getValue() == oldState) {
        if (changedStates != 0) {
            throw Base::RuntimeError("A Body publication and downstream state both claim the "
                                     "same History tip");
        }
        publication->CurrentState.setValue(replacement);
    }
    else if (changedStates == 0) {
        throw Base::RuntimeError("The edited Body state is not on the published Body history chain");
    }
}

DesignBodyState* createOutputState(
    App::Document& document,
    App::DocumentObject& operation,
    Body& body,
    Part::Feature* previousState,
    int outputIndex
)
{
    const std::string name = document.getUniqueObjectName("BodyState");
    auto* state = document.addObject<DesignBodyState>(name.c_str());
    state->Operation.setValue(&operation);
    state->OutputIndex.setValue(outputIndex);
    auto* operationProperties = dynamic_cast<DesignOperationProperties*>(&operation);
    if (!operationProperties) {
        throw Base::TypeError("A Body state requires a Design operation identity");
    }
    state->DesignId.setValue(operationProperties->DesignId.getValue());
    state->OperationId.setValue(operationProperties->OperationId.getValue());
    state->BodyId.setValue(body.VibeCADBodyId.getValue());
    state->PreviousState.setValue(previousState);
    return state;
}

void validateComponent(App::Document& document, const App::Part* component)
{
    if (component
        && (component->getDocument() != &document || component->Type.getStrValue() != "Component")) {
        throw Base::ValueError("The destination must be one Component in the operation document");
    }
}

void moveBodyToComponent(Body& body, App::Part* destination)
{
    auto* document = body.getDocument();
    validateComponent(*document, destination);

    auto* current = App::Part::getPartOfObject(&body);
    if (current && current->Type.getStrValue() != "Component") {
        throw Base::RuntimeError("A Design Body is contained by something other than a Component");
    }
    if (current == destination) {
        body.ComponentId.setValue(destination ? DesignModel::componentId(*destination) : std::string());
        return;
    }

    if (current) {
        current->removeObject(&body);
    }
    if (destination) {
        destination->addObject(&body);
    }
    else {
        body.ComponentId.setValue("");
    }
}

void setCreatedBodyFrame(Body& body, App::Part* destination, const Base::Placement& outputFrame)
{
    const Base::Placement componentFrame = destination
        ? App::GeoFeature::getGlobalPlacement(destination)
        : Base::Placement();
    body.Placement.setValue(componentFrame.inverse() * outputFrame);
}

Body* createOperationBody(
    App::Document& document,
    DesignOperationProperties& properties,
    const std::string& bodyId,
    const std::string& destinationComponentId,
    const Base::Placement& outputFrame
)
{
    if (bodyId.empty() || DesignModel::bodyWithId(document, bodyId)) {
        throw Base::RuntimeError("A New Body result requires one unused persistent Body identity");
    }

    const std::string name = document.getUniqueObjectName("Body");
    auto* body = document.addObject<Body>(name.c_str());
    body->VibeCADBodyId.setValue(bodyId);
    body->DesignId.setValue(properties.DesignId.getValue());
    body->Label.setValue("Body");
    if (const auto* separate = dynamic_cast<const DesignSeparate*>(&properties)) {
        if (const auto* source = freecad_cast<const Part::Feature*>(separate->Source.getValue())) {
            body->ShapeMaterial.setValue(source->ShapeMaterial.getValue());
        }
    }

    App::Part* destination = nullptr;
    if (!destinationComponentId.empty()) {
        destination = DesignModel::componentWithId(document, destinationComponentId);
        if (!destination) {
            throw Base::RuntimeError("An operation-created Body's destination Component no "
                                     "longer exists");
        }
    }
    moveBodyToComponent(*body, destination);
    setCreatedBodyFrame(*body, destination, outputFrame);
    document.classifyProvisionalTimelineInternalObject(body);
    return body;
}

void removeOperationCreatedBody(App::Document& document, DesignBodyState& state)
{
    if (state.PreviousState.getValue()) {
        throw Base::RuntimeError("A New Body operation has a malformed initial state");
    }
    auto* body = DesignModel::bodyWithId(document, state.BodyId.getValueStr());
    auto* publication = findDesignBodyPublication(body);
    if (!body || !publication || body->Tip.getValue() != publication) {
        throw Base::RuntimeError("The Body created by this operation has no exact removable "
                                 "publication");
    }

    for (auto* consumer : nonTimelineConsumers(state)) {
        if (consumer == publication) {
            continue;
        }
        if (freecad_cast<DesignBodyState*>(consumer)
            || dynamic_cast<DesignOperationProperties*>(consumer)) {
            throw Base::RuntimeError("This New Body has downstream modeling history; change or "
                                     "remove those dependent operations before converting its "
                                     "creation operation into a modification");
        }
        throw Base::RuntimeError("This New Body state is used by another document object and "
                                 "cannot be retired");
    }
    if (publication->CurrentState.getValue() != &state) {
        throw Base::RuntimeError("The Body created by this operation is no longer at its initial "
                                 "History state and cannot be converted into a modification");
    }

    const auto members = body->Group.getValues();
    if (members.size() != 1 || members.front() != publication) {
        throw Base::RuntimeError("This New Body contains additional native objects; move or "
                                 "remove them before changing the operation to Join, Cut, or "
                                 "Intersect");
    }

    auto* parent = App::Part::getPartOfObject(body);
    for (auto* consumer : body->getInList()) {
        const bool ownedByBody = consumer == publication || body->hasObject(consumer)
            || (body->getOrigin()
                && (consumer == body->getOrigin() || body->getOrigin()->hasObject(consumer)));
        if (consumer != parent && !ownedByBody) {
            throw Base::RuntimeError("This New Body is used by an assembly occurrence or another "
                                     "document object and cannot lose its identity");
        }
    }

    if (parent) {
        parent->removeObject(body);
    }
    body->removeObject(publication);
    const std::string publicationName = publication->getNameInDocument();
    const std::string bodyName = body->getNameInDocument();
    document.removeObject(publicationName.c_str());
    document.removeObject(bodyName.c_str());

    if (!nonTimelineConsumers(state).empty()) {
        throw Base::RuntimeError("The removed Body left a live consumer of its initial state");
    }
}

void ensureInputBodyPublications(App::Document& document, const DesignOperationProperties& properties)
{
    const auto& inputs = properties.InputStates.getValues();
    const auto& bodyIds = properties.InputBodyIds.getValues();
    if (inputs.size() != bodyIds.size()) {
        throw Base::RuntimeError("A Design operation has inconsistent exact input ports");
    }
    std::unordered_set<std::string> uniqueBodyIds;
    for (std::size_t index = 0; index < inputs.size(); ++index) {
        auto* body = DesignModel::bodyWithId(document, bodyIds[index]);
        const auto* state = freecad_cast<const DesignBodyState*>(inputs[index]);
        const bool matchesBody = state ? state->BodyId.getValueStr() == bodyIds[index]
                                       : body && body->hasObject(inputs[index]);
        if (!body || bodyIds[index].empty() || !uniqueBodyIds.insert(bodyIds[index]).second
            || !matchesBody) {
            throw Base::RuntimeError("A Design operation lost an exact input Body before Accept");
        }
        DesignModel::ensurePublication(document, *body);
    }
}

std::string uuidPropertyValue(const App::DocumentObject& object, const char* name)
{
    const auto* property = dynamic_cast<const App::PropertyUUID*>(
        object.PropertyContainer::getPropertyByName(name)
    );
    return property ? property->getValueStr() : std::string();
}

void requireDesignIdentity(const App::DocumentObject& object, const std::string& designId, const char* kind)
{
    if (uuidPropertyValue(object, "DesignId") != designId) {
        throw Base::RuntimeError(std::string(kind) + " does not belong to this saved Design");
    }
}

template<typename Object>
void insertUniqueIdentity(
    std::unordered_map<std::string, Object*>& identities,
    const std::string& identity,
    Object* object,
    const char* kind
)
{
    if (identity.empty() || !identities.emplace(identity, object).second) {
        throw Base::RuntimeError(
            std::string("Every ") + kind + " must have one distinct persistent identity"
        );
    }
}

bool isForbiddenOperationReference(App::DocumentObject* object)
{
    return freecad_cast<Body*>(object) || freecad_cast<Component*>(object)
        || freecad_cast<DesignBodyPublication*>(object) || freecad_cast<App::Link*>(object)
        || freecad_cast<App::LinkElement*>(object);
}

App::DocumentObject* timelineRoot(App::Document& document, App::DocumentObject* object)
{
    std::unordered_set<App::DocumentObject*> visited;
    auto* root = object;
    while (root) {
        if (root->getDocument() != &document || !document.containsObject(root)
            || !visited.insert(root).second) {
            throw Base::RuntimeError("A Design dependency has a cyclic or cross-document "
                                     "History owner");
        }
        auto* owner = App::DocumentTimeline::timelineOwner(root);
        if (!owner) {
            return root;
        }
        root = owner;
    }
    return nullptr;
}

void preflightReusableDefinitionDependencies(App::DocumentObject& operation)
{
    auto* document = operation.getDocument();
    auto* timeline = App::DocumentTimeline::get(document);
    if (!document || !timeline) {
        throw Base::RuntimeError(
            "A Design operation requires one live document with global History"
        );
    }

    const auto& history = timeline->Operations.getValues();
    std::unordered_map<App::DocumentObject*, std::size_t> historyPositions;
    historyPositions.reserve(history.size());
    for (std::size_t index = 0; index < history.size(); ++index) {
        auto* entry = history[index];
        if (!entry || !historyPositions.emplace(entry, index).second) {
            throw Base::RuntimeError(
                "Global History contains a missing or duplicate object"
            );
        }
    }

    const auto operationPosition = historyPositions.find(&operation);
    if (operationPosition == historyPositions.end()) {
        throw Base::RuntimeError(
            std::string("Design operation '") + operation.getNameInDocument()
            + "' is not enrolled in global History"
        );
    }

    std::vector<App::Property*> properties;
    operation.getPropertyList(properties);
    for (auto* property : properties) {
        auto* link = freecad_cast<App::PropertyLinkBase*>(property);
        if (!link) {
            continue;
        }
        std::vector<App::DocumentObject*> linked;
        link->getLinks(linked, true);
        for (auto* target : linked) {
            const auto* sketchId = target
                ? target->PropertyContainer::getPropertyByName("VibeCADSketchId")
                : nullptr;
            const auto* definitionId = target
                ? target->PropertyContainer::getPropertyByName("VibeCADDefinitionId")
                : nullptr;
            if (!sketchId && !definitionId) {
                continue;
            }

            const char* targetName = target && target->getNameInDocument()
                ? target->getNameInDocument()
                : "<missing>";
            auto* root = timelineRoot(*document, target);
            const auto targetPosition = root ? historyPositions.find(root)
                                             : historyPositions.end();
            const std::string storedPosition = targetPosition != historyPositions.end()
                ? std::to_string(targetPosition->second)
                : std::string("<missing>");
            const std::string prefix = std::string("Design operation '")
                + operation.getNameInDocument() + "' references reusable definition '"
                + targetName + "' at History position " + storedPosition;

            if (!root) {
                throw Base::RuntimeError(prefix + ", but it has no History root");
            }
            if (!App::DocumentTimeline::hasTimelineOperationRole(root)) {
                throw Base::RuntimeError(
                    prefix + ", but its root lacks History operation classification"
                );
            }
            if (targetPosition == historyPositions.end()) {
                throw Base::RuntimeError(prefix + ", but its root is absent from global History");
            }
            if (targetPosition->second >= operationPosition->second) {
                throw Base::RuntimeError(
                    prefix + ", not before operation position "
                    + std::to_string(operationPosition->second)
                );
            }
        }
    }
}

}  // namespace

Body* DesignModel::bodyWithId(App::Document& document, const std::string& bodyId)
{
    if (bodyId.empty()) {
        return nullptr;
    }

    Body* found = nullptr;
    for (auto* body : document.getObjectsOfType<Body>()) {
        if (!body || body->VibeCADBodyId.getValueStr() != bodyId) {
            continue;
        }
        if (found) {
            throw Base::RuntimeError("Two Bodies have the same persistent identity");
        }
        found = body;
    }
    return found;
}

App::Part* DesignModel::componentWithId(App::Document& document, const std::string& componentId)
{
    if (componentId.empty()) {
        return nullptr;
    }

    App::Part* found = nullptr;
    for (auto* component : document.getObjectsOfType<App::Part>()) {
        if (!component || DesignModel::componentId(*component) != componentId) {
            continue;
        }
        if (found) {
            throw Base::RuntimeError("Two Components have the same persistent identity");
        }
        found = component;
    }
    return found;
}

std::string DesignModel::componentId(const App::Part& component)
{
    if (const auto* designComponent = freecad_cast<const Component*>(&component)) {
        return designComponent->ComponentId.getValueStr();
    }
    return component.Type.getStrValue() == "Component" ? component.Uid.getValueStr() : std::string();
}

App::DocumentObject* DesignModel::resolveDefinitionReference(
    App::DocumentObject& definition,
    App::DocumentObject& selected
)
{
    auto* document = definition.getDocument();
    if (!document || selected.getDocument() != document || &definition == &selected) {
        throw Base::ValueError("A modeling definition can reference only an earlier object in "
                               "its own document");
    }
    if (freecad_cast<Component*>(&selected) || freecad_cast<DesignBodyPublication*>(&selected)
        || freecad_cast<App::Link*>(&selected) || freecad_cast<App::LinkElement*>(&selected)) {
        throw Base::ValueError("Components, rendered Body publications, and assembly "
                               "occurrences are not modeling definitions");
    }
    if (dynamic_cast<DesignOperationProperties*>(&selected)) {
        throw Base::ValueError("Select a Body result or reusable definition, not a History "
                               "operation controller");
    }

    App::DocumentObject* resolved = nullptr;

    // Reusable geometry remains a definition even when it came from a legacy
    // file which placed that definition inside a Body.
    if (freecad_cast<Part::Part2DObject*>(&selected) || freecad_cast<Part::Datum*>(&selected)
        || freecad_cast<ShapeBinder*>(&selected) || freecad_cast<SubShapeBinder*>(&selected)
        || selected.PropertyContainer::getPropertyByName("VibeCADDefinitionId")) {
        resolved = &selected;
    }

    if (!resolved) {
        auto* body = freecad_cast<Body*>(&selected);
        if (!body) {
            body = Body::findBodyOf(&selected);
        }
        if (!body) {
            if (const auto* state = freecad_cast<const DesignBodyState*>(&selected)) {
                body = bodyWithId(*document, state->BodyId.getValueStr());
            }
        }
        if (!body) {
            if (freecad_cast<Part::Feature*>(&selected)
                || freecad_cast<App::DatumElement*>(&selected)) {
                resolved = &selected;
            }
            else {
                throw Base::ValueError("Select reusable sketch, datum, reference, or exact "
                                       "solid geometry");
            }
        }
        else {
            resolved = designBodyStateBefore(body, &definition);
            if (!resolved) {
                throw Base::ValueError("The selected Body has no present solid state before "
                                       "this definition in History");
            }
        }
    }

    auto* root = timelineRoot(*document, resolved);
    auto* timeline = App::DocumentTimeline::get(document);
    if (timeline) {
        const auto& history = timeline->Operations.getValues();
        const auto definitionPosition = std::ranges::find(history, &definition);
        const auto dependencyPosition = std::ranges::find(history, root);
        const bool requiresHistory = App::DocumentTimeline::hasTimelineOperationRole(root)
            || freecad_cast<DesignBodyState*>(resolved)
            || resolved->PropertyContainer::getPropertyByName("VibeCADSketchId")
            || resolved->PropertyContainer::getPropertyByName("VibeCADDefinitionId");
        if ((requiresHistory && dependencyPosition == history.end())
            || (definitionPosition != history.end() && dependencyPosition != history.end()
                && dependencyPosition >= definitionPosition)) {
            throw Base::ValueError("A reusable definition can reference only an earlier "
                                   "History state");
        }
    }
    return resolved;
}

DesignDefinitionReference DesignModel::resolveDefinitionSubelementReference(
    App::DocumentObject& definition,
    App::DocumentObject& selected,
    const std::vector<std::string>& subelements
)
{
    DesignDefinitionReference reference;
    reference.object = resolveDefinitionReference(definition, selected);
    reference.subelements.reserve(subelements.size());
    if (subelements.empty()) {
        return reference;
    }

    auto* exactFeature = freecad_cast<Part::Feature*>(reference.object);
    auto* selectedFeature = freecad_cast<Part::Feature*>(&selected);
    const Part::TopoShape* exactShape =
        exactFeature ? &exactFeature->Shape.getShape() : nullptr;
    const Part::TopoShape* selectedShape =
        selectedFeature ? &selectedFeature->Shape.getShape() : nullptr;
    const bool changedObject = reference.object != &selected;
    if (changedObject && !freecad_cast<Body*>(&selected)) {
        throw Base::ValueError(
            "Select the Body result when referencing its faces, edges, or "
            "vertices. Legacy child-feature subelements are not an exact "
            "Design reference."
        );
    }

    for (const auto& subelement : subelements) {
        if (subelement.empty()) {
            reference.subelements.emplace_back();
            continue;
        }
        if (!changedObject && std::string_view(subelement).starts_with("Internal")) {
            // Sketcher publishes selectable closed profiles through its
            // separate InternalShape. They are exact subobjects of the same
            // reusable sketch, but are intentionally absent from the
            // edge-only Part::Feature::Shape used below for Body topology.
            if (!reference.object->getSubObject(subelement.c_str())) {
                throw Base::ValueError(
                    "The reusable definition does not contain the selected internal subelement"
                );
            }
            reference.subelements.push_back(subelement);
            continue;
        }
        if (!exactShape || exactShape->isNull()) {
            if (changedObject) {
                throw Base::ValueError(
                    "A Body presentation subelement did not resolve to exact "
                    "solid state geometry"
                );
            }
            reference.subelements.push_back(subelement);
            continue;
        }

        const Part::TopoShape* nameSource = changedObject ? selectedShape : exactShape;
        if (!nameSource || nameSource->isNull()) {
            throw Base::ValueError(
                "The selected Body presentation has no exact shape for its "
                "subelement reference"
            );
        }
        const Data::MappedElement sourceName =
            nameSource->getElementName(subelement.c_str());
        if (!sourceName.index) {
            if (changedObject) {
                throw Base::ValueError(
                    "The selected Body subelement is not present in its "
                    "rendered shape"
                );
            }
            // Datum and sketch reference tokens such as H_Axis are not
            // topological shape elements and remain exact on the same object.
            reference.subelements.push_back(subelement);
            continue;
        }

        const auto [shapeType, shapeIndex] =
            Part::TopoShape::shapeTypeAndIndex(sourceName.index);
        const TopoDS_Shape exactElement =
            exactShape->getSubShape(shapeType, shapeIndex, true);
        if (exactElement.IsNull()) {
            throw Base::ValueError(
                "The exact modeling state does not contain the selected "
                "subelement index"
            );
        }
        if (changedObject) {
            const TopoDS_Shape selectedElement =
                nameSource->getSubShape(shapeType, shapeIndex, true);
            if (selectedElement.IsNull()) {
                throw Base::ValueError(
                    "The rendered Body does not contain the selected "
                    "subelement index"
                );
            }
        }

        std::string canonical;
        const Data::MappedName mapped =
            exactShape->getMappedName(sourceName.index, true);
        if (mapped) {
            mapped.appendToBuffer(canonical);
        }
        else {
            sourceName.index.appendToStringBuffer(canonical);
        }
        if (canonical.empty()) {
            throw Base::RuntimeError(
                "The exact modeling state could not produce a canonical "
                "subelement name"
            );
        }
        reference.subelements.push_back(std::move(canonical));
    }
    return reference;
}

void DesignModel::initializeDefinition(App::DocumentObject& definition)
{
    if (dynamic_cast<DesignOperationProperties*>(&definition)) {
        throw Base::ValueError("A Body-producing Design operation is not a reusable definition");
    }
    App::DocumentTimeline::initializeDesignDefinition(definition);
}

void DesignModel::finalizeDefinition(App::DocumentObject& definition)
{
    initializeDefinition(definition);
    if (!definition.isValid()) {
        throw Base::RuntimeError(definition.getStatusString());
    }

    std::vector<App::Property*> properties;
    definition.getPropertyList(properties);
    for (auto* property : properties) {
        const char* rawPropertyName = definition.getPropertyName(property);
        const std::string_view propertyName = rawPropertyName ? rawPropertyName : "";
        // History ownership, editing, and viewport replacement links do not
        // contribute geometry to the reusable definition.
        if (propertyName == App::DocumentTimeline::OwnerPropertyName
            || propertyName == App::DocumentTimeline::EditorPropertyName
            || propertyName == App::DocumentTimeline::ReplacedInputsPropertyName) {
            continue;
        }
        auto* links = freecad_cast<App::PropertyLinkBase*>(property);
        if (!links) {
            continue;
        }
        std::vector<App::DocumentObject*> targets;
        links->getLinks(targets, true);
        for (auto* target : targets) {
            if (target && resolveDefinitionReference(definition, *target) != target) {
                throw Base::RuntimeError("A reusable definition retained a mutable Body "
                                         "presentation instead of its exact History state");
            }
        }
    }

    App::DocumentTimeline::finalizeDesignDefinition(definition);
}

void DesignModel::setOperationTargets(
    App::DocumentObject& operation,
    const std::string& resultMode,
    const std::vector<Body*>& bodies,
    App::Part* destinationComponent,
    const std::map<std::string, Base::Placement>& historicalFrames,
    bool allowIncompleteSelection
)
{
    auto* document = operation.getDocument();
    auto* properties = dynamic_cast<DesignOperationProperties*>(&operation);
    if (!document || !properties) {
        throw Base::TypeError("Target configuration requires one live Design operation");
    }
    if (resultMode != "New Body" && resultMode != "Join" && resultMode != "Cut"
        && resultMode != "Intersect" && resultMode != "Modify") {
        throw Base::ValueError("Result mode must be New Body, Join, Cut, Intersect, or Modify");
    }
    if (!properties->supportsDesignResultOperation(resultMode)) {
        throw Base::ValueError("This operation does not support the requested result mode");
    }
    auto* subelementProperties = dynamic_cast<DesignSubelementOperationProperties*>(&operation);
    const bool subelementOperation = subelementProperties != nullptr;
    if (subelementOperation && resultMode != "Modify") {
        throw Base::ValueError(
            "This operation modifies selected subelements and requires Modify mode"
        );
    }
    validateComponent(*document, destinationComponent);

    std::unordered_map<std::string, std::vector<std::string>> existingElementsByBody;
    if (subelementProperties) {
        const auto existingIds = properties->OutputBodyIds.getValues();
        const auto existingGroups = subelementProperties->targetElementGroups();
        if (existingIds.size() != existingGroups.size()
            && !(existingIds.empty() && existingGroups.empty())) {
            throw Base::RuntimeError("This operation has inconsistent target and subelement arrays");
        }
        for (std::size_t index = 0; index < existingIds.size(); ++index) {
            existingElementsByBody.emplace(existingIds[index], existingGroups[index]);
        }
    }

    properties->ResultOperation.setValue(resultMode.c_str());
    if (resultMode == "New Body") {
        if (!bodies.empty()) {
            throw Base::ValueError("New Body cannot also target an existing Body");
        }
        auto bodyIds = properties->OutputBodyIds.getValues();
        if (bodyIds.size() != 1 || bodyIds.front().empty()) {
            bodyIds = {Base::Uuid::createUuid()};
        }
        const std::string destinationId = destinationComponent ? componentId(*destinationComponent)
                                                               : std::string();
        const std::vector<Base::Placement> outputFrames {
            destinationComponent ? App::GeoFeature::getGlobalPlacement(destinationComponent)
                                 : Base::Placement()
        };
        properties->InputStates.setValues({});
        properties->InputBodyIds.setValues(std::vector<std::string> {});
        properties->InputFrames.setValues(std::vector<Base::Placement> {});
        properties->OutputBodyIds.setValues(bodyIds);
        properties->OutputFrames.setValues(outputFrames);
        properties->OutputPreviousInputIndices.setValues(std::vector<long> {-1});
        boost::dynamic_bitset<> outputPresence(1);
        outputPresence.set();
        properties->OutputPresence.setValues(outputPresence);
        properties->OutputComponentIds.setValues(std::vector<std::string> {destinationId});
        properties->TargetBodyIds.setValues(bodyIds);
        properties->TargetFrames.setValues(outputFrames);
        properties->DestinationComponentId.setValue(destinationId);
        return;
    }

    if (destinationComponent) {
        throw Base::ValueError("Only New Body accepts a destination Component");
    }
    if (bodies.empty()) {
        if (allowIncompleteSelection) {
            properties->InputStates.setValues({});
            properties->InputBodyIds.setValues(std::vector<std::string> {});
            properties->InputFrames.setValues(std::vector<Base::Placement> {});
            properties->OutputBodyIds.setValues(std::vector<std::string> {});
            properties->OutputFrames.setValues(std::vector<Base::Placement> {});
            properties->OutputPreviousInputIndices.setValues(std::vector<long> {});
            properties->OutputPresence.setValues(boost::dynamic_bitset<> {});
            properties->OutputComponentIds.setValues(std::vector<std::string> {});
            properties->TargetBodyIds.setValues(std::vector<std::string> {});
            properties->TargetFrames.setValues(std::vector<Base::Placement> {});
            properties->DestinationComponentId.setValue("");
            if (subelementProperties) {
                subelementProperties->setTargetElementGroups({});
            }
            return;
        }
        throw Base::ValueError("This operation requires at least one explicit target Body");
    }

    std::vector<App::DocumentObject*> inputs;
    std::vector<std::string> bodyIds;
    std::vector<Base::Placement> frames;
    inputs.reserve(bodies.size());
    bodyIds.reserve(bodies.size());
    frames.reserve(bodies.size());
    std::unordered_set<Body*> uniqueBodies;
    std::unordered_set<std::string> uniqueIds;
    for (auto* body : bodies) {
        if (!body || body->getDocument() != document || !uniqueBodies.insert(body).second) {
            throw Base::ValueError(
                "Every target must be one distinct Body in the operation document"
            );
        }
        const std::string bodyId = body->VibeCADBodyId.getValueStr();
        if (bodyId.empty() || !uniqueIds.insert(bodyId).second) {
            throw Base::RuntimeError("Every target Body must have one distinct persistent identity");
        }
        auto* input = designBodyStateBefore(body, &operation);
        if (!input) {
            throw Base::ValueError("Every target Body must contain a solid at this History position");
        }
        inputs.push_back(input);
        bodyIds.push_back(bodyId);
        const auto historical = historicalFrames.find(bodyId);
        frames.push_back(
            historical != historicalFrames.end() ? historical->second
                                                 : App::GeoFeature::getGlobalPlacement(body)
        );
    }
    properties->InputStates.setValues(inputs);
    properties->InputBodyIds.setValues(bodyIds);
    properties->InputFrames.setValues(frames);
    properties->OutputBodyIds.setValues(bodyIds);
    properties->OutputFrames.setValues(frames);
    std::vector<long> previousInputIndices;
    previousInputIndices.reserve(bodyIds.size());
    for (std::size_t index = 0; index < bodyIds.size(); ++index) {
        previousInputIndices.push_back(static_cast<long>(index));
    }
    properties->OutputPreviousInputIndices.setValues(previousInputIndices);
    boost::dynamic_bitset<> outputPresence(bodyIds.size());
    outputPresence.set();
    properties->OutputPresence.setValues(outputPresence);
    properties->OutputComponentIds.setValues(std::vector<std::string>(bodyIds.size()));
    properties->TargetBodyIds.setValues(bodyIds);
    properties->TargetFrames.setValues(frames);
    properties->DestinationComponentId.setValue("");
    if (subelementProperties) {
        std::vector<std::vector<std::string>> groups;
        groups.reserve(bodyIds.size());
        for (const auto& bodyId : bodyIds) {
            const auto existing = existingElementsByBody.find(bodyId);
            groups.push_back(
                existing != existingElementsByBody.end() ? existing->second
                                                         : std::vector<std::string> {}
            );
        }
        subelementProperties->setTargetElementGroups(groups);
    }
}

void DesignModel::setOperationTargets(
    DesignOperationEdit& edit,
    const std::string& resultMode,
    const std::vector<Body*>& bodies,
    App::Part* destinationComponent,
    bool allowIncompleteSelection
)
{
    if (!edit.operation) {
        throw Base::RuntimeError("This Design operation edit is no longer active");
    }
    setOperationTargets(
        *edit.operation,
        resultMode,
        bodies,
        destinationComponent,
        edit.originalTargetFrames,
        allowIncompleteSelection
    );
    if (resultMode == "New Body") {
        auto* properties = dynamic_cast<DesignOperationProperties*>(edit.operation);
        if (!properties) {
            throw Base::TypeError("This object has no Design operation target contract");
        }
        if (edit.newBodyId.empty()) {
            edit.newBodyId = Base::Uuid::createUuid();
        }
        properties->TargetBodyIds.setValues(std::vector<std::string> {edit.newBodyId});
        properties->OutputBodyIds.setValues(std::vector<std::string> {edit.newBodyId});

        const std::string destinationId = destinationComponent ? componentId(*destinationComponent)
                                                               : std::string();
        if (edit.originalResultMode == "New Body"
            && destinationId == edit.originalDestinationComponentId) {
            const auto historical = edit.originalTargetFrames.find(edit.newBodyId);
            if (historical != edit.originalTargetFrames.end()) {
                properties->TargetFrames.setValues({historical->second});
                properties->OutputFrames.setValues({historical->second});
            }
        }
    }
}

void DesignModel::setFeaturePatternTargets(
    DesignOperationEdit& edit,
    App::DocumentObject& sourceOperation,
    const std::vector<Body*>& bodies,
    bool allowIncompleteSelection
)
{
    auto* operation = edit.operation;
    auto* document = operation ? operation->getDocument() : nullptr;
    auto* pattern = operation ? dynamic_cast<DesignPatternProperties*>(operation) : nullptr;
    auto* properties = operation ? dynamic_cast<DesignOperationProperties*>(operation) : nullptr;
    auto* sourceProperties = dynamic_cast<DesignOperationProperties*>(&sourceOperation);
    auto* sourceFeature = freecad_cast<FeatureAddSub*>(&sourceOperation);
    if (!operation || !document || !pattern || !properties) {
        throw Base::TypeError("Feature Pattern configuration requires one live Design Pattern");
    }
    if (&sourceOperation == operation || sourceOperation.getDocument() != document
        || !sourceProperties || !sourceFeature) {
        throw Base::TypeError("A Feature Pattern source must be one earlier Design feature "
                              "with reusable tool geometry");
    }

    auto* timeline = App::DocumentTimeline::get(document);
    if (!timeline || !App::DocumentTimeline::hasTimelineOperationRole(&sourceOperation)) {
        throw Base::ValueError("A Feature Pattern source must already exist in global History");
    }
    const auto& history = timeline->Operations.getValues();
    const auto sourcePosition = std::ranges::find(history, &sourceOperation);
    const auto operationPosition = std::ranges::find(history, operation);
    if (sourcePosition == history.end()
        || (operationPosition != history.end() && sourcePosition >= operationPosition)) {
        throw Base::ValueError("A Feature Pattern source must precede the Pattern in History");
    }

    const std::string_view sourceResult = sourceProperties->ResultOperation.getValueAsString();
    const std::string resultMode = sourceResult == "Cut"         ? "Cut"
        : (sourceResult == "New Body" || sourceResult == "Join") ? "Join"
                                                                 : std::string();
    if (resultMode.empty()) {
        throw Base::ValueError("Only additive and subtractive Design features can be patterned");
    }

    pattern->PatternSource.setValue("Feature");
    pattern->SourceOperation.setValue(&sourceOperation);
    setOperationTargets(edit, resultMode, bodies, nullptr, allowIncompleteSelection);
}

namespace
{
void setBodyCopySource(
    DesignOperationEdit& edit,
    Body& sourceBody,
    std::size_t generatedCopyCount,
    const char* operationName
)
{
    auto* operation = edit.operation;
    auto* document = operation ? operation->getDocument() : nullptr;
    auto* properties = operation ? dynamic_cast<DesignOperationProperties*>(operation) : nullptr;
    if (!operation || !document || !properties) {
        throw Base::TypeError(
            std::string(operationName) + " configuration requires one live Design operation"
        );
    }
    if (generatedCopyCount == 0) {
        throw Base::ValueError(std::string(operationName) + " requires at least one generated copy");
    }
    if (sourceBody.getDocument() != document) {
        throw Base::ValueError(
            std::string(operationName) + " source must belong to the operation document"
        );
    }
    const std::string sourceBodyId = sourceBody.VibeCADBodyId.getValueStr();
    auto* sourceState = designBodyStateBefore(&sourceBody, operation);
    if (sourceBodyId.empty() || !sourceState) {
        throw Base::ValueError(
            std::string(operationName) + " source has no exact solid state at this History position"
        );
    }
    if (const auto* designState = freecad_cast<const DesignBodyState*>(sourceState);
        designState && !designState->Present.getValue()) {
        throw Base::ValueError(
            std::string("An absent Body cannot be used as a ") + operationName + " source"
        );
    }

    const auto historical = edit.originalTargetFrames.find(sourceBodyId);
    const Base::Placement sourceFrame = historical != edit.originalTargetFrames.end()
        ? historical->second
        : App::GeoFeature::getGlobalPlacement(&sourceBody);

    const auto oldBodyIds = properties->OutputBodyIds.getValues();
    const auto oldMappings = properties->OutputPreviousInputIndices.getValues();
    std::vector<std::string> outputBodyIds;
    outputBodyIds.reserve(generatedCopyCount);
    for (std::size_t index = 0; index < std::min(oldBodyIds.size(), oldMappings.size())
         && outputBodyIds.size() < generatedCopyCount;
         ++index) {
        if (oldMappings[index] == -1 && !oldBodyIds[index].empty() && oldBodyIds[index] != sourceBodyId
            && std::ranges::find(outputBodyIds, oldBodyIds[index]) == outputBodyIds.end()) {
            outputBodyIds.push_back(oldBodyIds[index]);
        }
    }
    while (outputBodyIds.size() < generatedCopyCount) {
        outputBodyIds.push_back(Base::Uuid::createUuid());
    }

    const std::string componentId = sourceBody.ComponentId.getValue();
    properties->ResultOperation.setValue("New Bodies");
    properties->InputStates.setValues({sourceState});
    properties->InputBodyIds.setValues(std::vector<std::string> {sourceBodyId});
    properties->InputFrames.setValues(std::vector<Base::Placement> {sourceFrame});
    properties->OutputBodyIds.setValues(outputBodyIds);
    properties->OutputFrames.setValues(std::vector<Base::Placement>(generatedCopyCount, sourceFrame));
    properties->OutputPreviousInputIndices.setValues(std::vector<long>(generatedCopyCount, -1));
    boost::dynamic_bitset<> presence(generatedCopyCount);
    presence.set();
    properties->OutputPresence.setValues(presence);
    properties->OutputComponentIds.setValues(std::vector<std::string>(generatedCopyCount, componentId)
    );
    properties->TargetBodyIds.setValues(outputBodyIds);
    properties->TargetFrames.setValues(properties->OutputFrames.getValues());
    properties->DestinationComponentId.setValue(generatedCopyCount == 1 ? componentId : "");
}
}  // namespace

void DesignModel::setBodyPatternSource(
    DesignOperationEdit& edit,
    Body& sourceBody,
    std::size_t generatedCopyCount
)
{
    auto* pattern = edit.operation ? dynamic_cast<DesignPatternProperties*>(edit.operation) : nullptr;
    if (!pattern) {
        throw Base::TypeError("Body Pattern configuration requires one live Design Pattern");
    }
    pattern->PatternSource.setValue("Body");
    pattern->SourceOperation.setValue(nullptr);
    setBodyCopySource(edit, sourceBody, generatedCopyCount, "Body Pattern");
}

void DesignModel::setCloneSource(DesignOperationEdit& edit, Body& sourceBody)
{
    if (!freecad_cast<DesignClone*>(edit.operation)) {
        throw Base::TypeError("Clone configuration requires one live Design Clone");
    }
    setBodyCopySource(edit, sourceBody, 1, "Clone");
}

void DesignModel::setScriptOutputs(
    DesignOperationEdit& edit,
    const std::string& programObjectName,
    const std::string& programId,
    const std::string& revision,
    const std::vector<std::string>& outputKeys,
    const std::vector<std::string>& outputLabels,
    const std::vector<Part::TopoShape>& outputShapes,
    const std::vector<Body*>& adoptedBodies,
    const std::vector<std::string>& programOutputKeys,
    const std::vector<std::string>& programOutputTypes
)
{
    auto* operation = freecad_cast<DesignScriptOperation*>(edit.operation);
    auto* document = operation ? operation->getDocument() : nullptr;
    auto* properties = operation ? dynamic_cast<DesignOperationProperties*>(operation) : nullptr;
    if (!operation || !document || !properties) {
        throw Base::TypeError("VibeScript output configuration requires one live "
                              "DesignScriptOperation");
    }
    if (programObjectName.empty() || programId.empty() || revision.empty()
        || programOutputKeys.empty() || programOutputTypes.size() != programOutputKeys.size()
        || outputLabels.size() != outputKeys.size() || outputShapes.size() != outputKeys.size()
        || adoptedBodies.size() != outputKeys.size()) {
        throw Base::ValueError("A VibeScript operation requires one program identity, revision, "
                               "and topology type per published output, plus one label, shape, "
                               "and Body choice per Body output");
    }

    std::unordered_map<std::string, std::string> publishedOutputs;
    for (std::size_t index = 0; index < programOutputKeys.size(); ++index) {
        if (programOutputKeys[index].empty() || programOutputTypes[index].empty()
            || !publishedOutputs.emplace(programOutputKeys[index], programOutputTypes[index]).second) {
            throw Base::ValueError("Every published VibeScript output requires one distinct "
                                   "non-empty key and topology type");
        }
    }

    const auto oldKeys = operation->ScriptOutputKeys.getValues();
    const auto oldBodyIds = properties->OutputBodyIds.getValues();
    const auto oldMappings = properties->OutputPreviousInputIndices.getValues();
    const auto oldFrames = properties->OutputFrames.getValues();
    const auto oldComponents = properties->OutputComponentIds.getValues();
    if ((!oldKeys.empty() || !oldBodyIds.empty() || !oldMappings.empty() || !oldFrames.empty()
         || !oldComponents.empty())
        && (oldKeys.size() != oldBodyIds.size() || oldMappings.size() != oldKeys.size()
            || oldFrames.size() != oldKeys.size() || oldComponents.size() != oldKeys.size())) {
        throw Base::RuntimeError("The saved VibeScript operation has inconsistent output identity "
                                 "metadata");
    }

    struct ExistingOutput
    {
        std::string bodyId;
        long previousInputIndex;
        Base::Placement frame;
        std::string componentId;
    };
    std::unordered_map<std::string, ExistingOutput> existing;
    for (std::size_t index = 0; index < oldKeys.size(); ++index) {
        if (oldKeys[index].empty() || oldBodyIds[index].empty()
            || !existing
                    .emplace(
                        oldKeys[index],
                        ExistingOutput {
                            oldBodyIds[index],
                            oldMappings[index],
                            oldFrames[index],
                            oldComponents[index],
                        }
                    )
                    .second) {
            throw Base::RuntimeError("The saved VibeScript operation has duplicate output "
                                     "identities");
        }
    }

    std::vector<App::DocumentObject*> inputs;
    std::vector<std::string> inputBodyIds;
    std::vector<Base::Placement> inputFrames;
    std::vector<std::string> bodyIds;
    std::vector<Base::Placement> outputFrames;
    std::vector<long> previousInputIndices;
    std::vector<std::string> outputComponentIds;
    bodyIds.reserve(outputKeys.size());
    outputFrames.reserve(outputKeys.size());
    previousInputIndices.reserve(outputKeys.size());
    outputComponentIds.reserve(outputKeys.size());
    std::unordered_set<std::string> uniqueKeys;
    std::unordered_set<std::string> uniqueBodyIds;
    std::unordered_set<Body*> uniqueAdoptedBodies;

    const auto appendExistingInput = [&](Body& body, const Base::Placement& frame) {
        auto* previous = designBodyStateBefore(&body, operation);
        if (!previous) {
            throw Base::ValueError("A VibeScript output can adopt only a Body with an exact "
                                   "solid state at this History position");
        }
        if (const auto* state = freecad_cast<const DesignBodyState*>(previous);
            state && !state->Present.getValue()) {
            throw Base::ValueError("A VibeScript output cannot adopt an absent Body");
        }
        const auto inputIndex = static_cast<long>(inputs.size());
        inputs.push_back(previous);
        inputBodyIds.push_back(body.VibeCADBodyId.getValueStr());
        inputFrames.push_back(frame);
        return inputIndex;
    };

    for (std::size_t index = 0; index < outputKeys.size(); ++index) {
        const auto& key = outputKeys[index];
        const auto& label = outputLabels[index];
        const auto& shape = outputShapes[index];
        auto* adopted = adoptedBodies[index];
        if (key.empty() || label.empty() || !uniqueKeys.insert(key).second) {
            throw Base::ValueError("Every VibeScript output requires one distinct non-empty key "
                                   "and label");
        }
        const auto published = publishedOutputs.find(key);
        if (published == publishedOutputs.end() || published->second != "solid") {
            throw Base::ValueError("Every VibeScript Body output must be declared once as a "
                                   "solid program output");
        }
        if (shape.isNull() || !shape.hasSubShape(TopAbs_SOLID)) {
            throw Base::ValueError("Every VibeScript Body output must contain at least one solid");
        }
        if (adopted
            && (adopted->getDocument() != document || !uniqueAdoptedBodies.insert(adopted).second)) {
            throw Base::ValueError("Every adopted VibeScript output must be one distinct Body in "
                                   "the operation document");
        }

        const auto retained = existing.find(key);
        std::string bodyId;
        Base::Placement frame;
        long previousInputIndex = -1;
        std::string componentId;
        if (retained != existing.end()) {
            bodyId = retained->second.bodyId;
            frame = retained->second.frame;
            auto* body = bodyWithId(*document, bodyId);
            if (!body) {
                throw Base::RuntimeError("A retained VibeScript output lost its persistent Body");
            }
            if (adopted && adopted != body) {
                throw Base::ValueError("A retained VibeScript output key cannot be assigned to a "
                                       "different Body");
            }
            if (retained->second.previousInputIndex == -1) {
                componentId = retained->second.componentId;
            }
            else {
                previousInputIndex = appendExistingInput(*body, frame);
            }
        }
        else if (adopted) {
            bodyId = adopted->VibeCADBodyId.getValueStr();
            frame = App::GeoFeature::getGlobalPlacement(adopted);
            previousInputIndex = appendExistingInput(*adopted, frame);
        }
        else {
            bodyId = Base::Uuid::createUuid();
            frame = Base::Placement();
        }

        if (auto* body = bodyWithId(*document, bodyId)) {
            requireBodyShape(*body, shape, "The VibeScript output");
        }

        if (bodyId.empty() || !uniqueBodyIds.insert(bodyId).second) {
            throw Base::RuntimeError("Every VibeScript output requires one distinct persistent "
                                     "Body identity");
        }
        bodyIds.push_back(bodyId);
        outputFrames.push_back(frame);
        previousInputIndices.push_back(previousInputIndex);
        outputComponentIds.push_back(componentId);
    }

    operation->ProgramObjectName.setValue(programObjectName.c_str());
    operation->ProgramId.setValue(programId.c_str());
    operation->ProgramRevision.setValue(revision.c_str());
    operation->ProgramOutputKeys.setValues(programOutputKeys);
    operation->ProgramOutputTypes.setValues(programOutputTypes);
    operation->ScriptOutputKeys.setValues(outputKeys);
    operation->ScriptOutputLabels.setValues(outputLabels);
    operation->AcceptedShapes.setValues(outputShapes);
    properties->ResultOperation.setValue("Program Outputs");
    properties->InputStates.setValues(inputs);
    properties->InputBodyIds.setValues(inputBodyIds);
    properties->InputFrames.setValues(inputFrames);
    properties->OutputBodyIds.setValues(bodyIds);
    properties->OutputFrames.setValues(outputFrames);
    properties->OutputPreviousInputIndices.setValues(previousInputIndices);
    boost::dynamic_bitset<> presence(bodyIds.size());
    presence.set();
    properties->OutputPresence.setValues(presence);
    properties->OutputComponentIds.setValues(outputComponentIds);
    properties->TargetBodyIds.setValues(bodyIds);
    properties->TargetFrames.setValues(outputFrames);
    properties->DestinationComponentId.setValue("");
}

void DesignModel::setCombineBodies(
    App::DocumentObject& operation,
    const std::string& resultMode,
    Body& resultBody,
    const std::vector<Body*>& toolBodies,
    bool keepTools,
    const std::map<std::string, Base::Placement>& historicalFrames,
    bool allowIncompleteSelection
)
{
    auto* document = operation.getDocument();
    auto* combine = freecad_cast<DesignCombine*>(&operation);
    if (!document || !combine) {
        throw Base::TypeError("Combine configuration requires one live Design Combine operation");
    }
    ensureDesignOperationPortSchema(operation);
    if (resultMode != "Join" && resultMode != "Cut" && resultMode != "Intersect") {
        throw Base::ValueError("Combine operation must be Join, Cut, or Intersect");
    }
    if (resultBody.getDocument() != document) {
        throw Base::ValueError("The Combine result Body must belong to the operation document");
    }
    if (toolBodies.empty() && !allowIncompleteSelection) {
        throw Base::ValueError("Combine requires at least one explicit tool Body");
    }

    std::vector<Body*> orderedBodies;
    orderedBodies.reserve(toolBodies.size() + 1);
    orderedBodies.push_back(&resultBody);
    orderedBodies.insert(orderedBodies.end(), toolBodies.begin(), toolBodies.end());

    std::vector<App::DocumentObject*> inputStates;
    std::vector<std::string> inputBodyIds;
    std::vector<Base::Placement> inputFrames;
    inputStates.reserve(orderedBodies.size());
    inputBodyIds.reserve(orderedBodies.size());
    inputFrames.reserve(orderedBodies.size());
    std::unordered_set<Body*> uniqueBodies;
    std::unordered_set<std::string> uniqueBodyIds;
    for (auto* body : orderedBodies) {
        if (!body || body->getDocument() != document || !uniqueBodies.insert(body).second) {
            throw Base::ValueError("The Combine result and tools must be distinct Bodies in one "
                                   "document");
        }
        const std::string bodyId = body->VibeCADBodyId.getValueStr();
        if (bodyId.empty() || !uniqueBodyIds.insert(bodyId).second) {
            throw Base::RuntimeError("Every Combine Body must have one distinct persistent identity");
        }
        auto* state = designBodyStateBefore(body, &operation);
        if (!state) {
            throw Base::ValueError(
                "Every Combine Body must contain a solid at this History position"
            );
        }
        inputStates.push_back(state);
        inputBodyIds.push_back(bodyId);
        const auto historical = historicalFrames.find(bodyId);
        inputFrames.push_back(
            historical != historicalFrames.end() ? historical->second
                                                 : App::GeoFeature::getGlobalPlacement(body)
        );
    }

    const std::size_t outputCount = keepTools ? 1 : orderedBodies.size();
    std::vector<std::string> outputBodyIds(
        inputBodyIds.begin(),
        inputBodyIds.begin() + static_cast<std::ptrdiff_t>(outputCount)
    );
    std::vector<Base::Placement> outputFrames(
        inputFrames.begin(),
        inputFrames.begin() + static_cast<std::ptrdiff_t>(outputCount)
    );
    std::vector<long> previousInputIndices;
    previousInputIndices.reserve(outputCount);
    for (std::size_t index = 0; index < outputCount; ++index) {
        previousInputIndices.push_back(static_cast<long>(index));
    }
    boost::dynamic_bitset<> outputPresence(outputCount);
    outputPresence.set(0);

    combine->ResultOperation.setValue(resultMode.c_str());
    combine->ResultBodyId.setValue(inputBodyIds.front());
    combine->KeepTools.setValue(keepTools);
    combine->InputStates.setValues(inputStates);
    combine->InputBodyIds.setValues(inputBodyIds);
    combine->InputFrames.setValues(inputFrames);
    combine->OutputBodyIds.setValues(outputBodyIds);
    combine->OutputFrames.setValues(outputFrames);
    combine->OutputPreviousInputIndices.setValues(previousInputIndices);
    combine->OutputPresence.setValues(outputPresence);
    combine->OutputComponentIds.setValues(std::vector<std::string>(outputCount));
    combine->TargetBodyIds.setValues(outputBodyIds);
    combine->TargetFrames.setValues(outputFrames);
    combine->DestinationComponentId.setValue("");
}

void DesignModel::setCombineBodies(
    DesignOperationEdit& edit,
    const std::string& resultMode,
    Body& resultBody,
    const std::vector<Body*>& toolBodies,
    bool keepTools,
    bool allowIncompleteSelection
)
{
    if (!edit.operation) {
        throw Base::RuntimeError("This Design operation edit is no longer active");
    }
    setCombineBodies(
        *edit.operation,
        resultMode,
        resultBody,
        toolBodies,
        keepTools,
        edit.originalTargetFrames,
        allowIncompleteSelection
    );
}

std::vector<Base::Vector3d> DesignModel::setSplitDefinition(
    DesignOperationEdit& edit,
    Body& sourceBody,
    const std::vector<App::PropertyLinkSubList::SubSet>& splitters
)
{
    auto* split = edit.operation ? freecad_cast<DesignSplit*>(edit.operation) : nullptr;
    auto* document = split ? split->getDocument() : nullptr;
    if (!split || !document) {
        throw Base::TypeError("Split configuration requires one live Design Split operation");
    }
    if (sourceBody.getDocument() != document) {
        throw Base::ValueError("The Split source Body must belong to the operation document");
    }
    if (splitters.empty()) {
        throw Base::ValueError("Split requires at least one explicit face, surface, shell, or solid");
    }

    const std::string sourceBodyId = sourceBody.VibeCADBodyId.getValueStr();
    auto* sourceState = designBodyStateBefore(&sourceBody, edit.operation);
    if (sourceBodyId.empty() || !sourceState) {
        throw Base::ValueError(
            "The Split source Body has no exact solid state at this History position"
        );
    }

    const auto previousReferences = split->Splitters.getSubListValues();
    const auto previousSplitterFrames = split->SplitterFrames.getValues();
    const auto frameForPreviousReference = [&previousReferences, &previousSplitterFrames](
                                               const App::PropertyLinkSubList::SubSet& reference
                                           ) -> std::optional<Base::Placement> {
        if (previousReferences.size() != previousSplitterFrames.size()) {
            return std::nullopt;
        }
        for (std::size_t index = 0; index < previousReferences.size(); ++index) {
            if (previousReferences[index] == reference) {
                return previousSplitterFrames[index];
            }
        }
        return std::nullopt;
    };

    std::vector<App::DocumentObject*> inputs {sourceState};
    std::vector<std::string> inputBodyIds {sourceBodyId};
    std::vector<Base::Placement> inputFrames;
    const auto historicalSource = edit.originalTargetFrames.find(sourceBodyId);
    inputFrames.push_back(
        historicalSource != edit.originalTargetFrames.end()
            ? historicalSource->second
            : App::GeoFeature::getGlobalPlacement(&sourceBody)
    );

    std::vector<App::PropertyLinkSubList::SubSet> normalized;
    std::vector<Base::Placement> splitterFrames;
    normalized.reserve(splitters.size());
    splitterFrames.reserve(splitters.size());
    std::unordered_set<std::string> inputToolBodyIds;
    inputToolBodyIds.insert(sourceBodyId);

    for (const auto& requested : splitters) {
        auto* requestedObject = requested.first;
        if (!requestedObject || requestedObject == edit.operation
            || requestedObject->getDocument() != document) {
            throw Base::ValueError(
                "Every Split definition must be a live object in the operation document"
            );
        }

        Body* definitionBody = nullptr;
        if (auto* body = freecad_cast<Body*>(requestedObject)) {
            definitionBody = body;
        }
        else if (auto* publication = freecad_cast<DesignBodyPublication*>(requestedObject)) {
            definitionBody = bodyWithId(*document, publication->BodyId.getValueStr());
        }
        else if (auto* state = freecad_cast<DesignBodyState*>(requestedObject)) {
            definitionBody = bodyWithId(*document, state->BodyId.getValueStr());
        }
        else {
            definitionBody = Body::findBodyOf(requestedObject);
        }

        App::DocumentObject* exactDefinition = requestedObject;
        Base::Placement definitionFrame;
        if (definitionBody) {
            const std::string definitionBodyId = definitionBody->VibeCADBodyId.getValueStr();
            if (definitionBody == &sourceBody || definitionBodyId == sourceBodyId) {
                throw Base::ValueError("The source Body cannot also be its own Split definition");
            }
            auto* exactState = designBodyStateBefore(definitionBody, edit.operation);
            if (!exactState || definitionBodyId.empty()) {
                throw Base::ValueError("A Body-backed Split definition has no exact solid state "
                                       "at this History position");
            }
            exactDefinition = exactState;
            const auto historical = edit.originalTargetFrames.find(definitionBodyId);
            definitionFrame = historical != edit.originalTargetFrames.end()
                ? historical->second
                : App::GeoFeature::getGlobalPlacement(definitionBody);

            if (inputToolBodyIds.insert(definitionBodyId).second) {
                inputs.push_back(exactState);
                inputBodyIds.push_back(definitionBodyId);
                inputFrames.push_back(definitionFrame);
            }
        }
        else {
            auto* feature = freecad_cast<Part::Feature*>(requestedObject);
            if (!feature || isForbiddenOperationReference(requestedObject)) {
                throw Base::TypeError("A standalone Split definition must be a modeling feature, "
                                      "face, surface, shell, or solid");
            }
            const App::PropertyLinkSubList::SubSet exactReference {
                exactDefinition,
                requested.second,
            };
            if (const auto previous = frameForPreviousReference(exactReference)) {
                definitionFrame = *previous;
            }
            else {
                definitionFrame = App::GeoFeature::getGlobalPlacement(feature)
                    * feature->Placement.getValue().inverse();
            }
        }

        App::PropertyLinkSubList::SubSet exactReference {
            exactDefinition,
            requested.second,
        };
        if (std::ranges::find(normalized, exactReference) != normalized.end()) {
            throw Base::ValueError(
                "The same Split definition and subelements were selected more than once"
            );
        }
        normalized.push_back(std::move(exactReference));
        splitterFrames.push_back(definitionFrame);
    }

    const bool preserveAssignment = split->SourceBodyId.getValueStr() == sourceBodyId
        && previousReferences == normalized && previousSplitterFrames == splitterFrames
        && split->RetainedRegionChosen.getValue() && split->RegionWitnesses.getSize() >= 2;

    split->ResultOperation.setValue("Split");
    split->SourceBodyId.setValue(sourceBodyId);
    split->InputStates.setValues(inputs);
    split->InputBodyIds.setValues(inputBodyIds);
    split->InputFrames.setValues(inputFrames);
    split->Splitters.setSubListValues(normalized);
    split->SplitterFrames.setValues(splitterFrames);

    if (preserveAssignment) {
        return split->RegionWitnesses.getValues();
    }

    split->RetainedRegionChosen.setValue(false);
    split->RegionWitnesses.setValues({});
    split->OutputBodyIds.setValues(std::vector<std::string> {});
    split->OutputFrames.setValues({});
    split->OutputPreviousInputIndices.setValues({});
    split->OutputPresence.setValues(boost::dynamic_bitset<> {});
    split->OutputComponentIds.setValues(std::vector<std::string> {});
    split->OutputShapes.setValues({});
    split->TargetBodyIds.setValues(std::vector<std::string> {});
    split->TargetFrames.setValues({});
    split->DestinationComponentId.setValue("");
    split->PreviewShape.setValue(Part::TopoShape());
    return discoverDesignSplitRegionWitnesses(*split);
}

void DesignModel::assignSplitRegions(
    DesignOperationEdit& edit,
    Body& sourceBody,
    const std::vector<Base::Vector3d>& witnesses,
    std::size_t retainedRegion
)
{
    auto* split = edit.operation ? freecad_cast<DesignSplit*>(edit.operation) : nullptr;
    auto* document = split ? split->getDocument() : nullptr;
    if (!split || !document) {
        throw Base::TypeError("Split region assignment requires one live Design Split operation");
    }
    const std::string sourceBodyId = sourceBody.VibeCADBodyId.getValueStr();
    const auto& inputBodyIds = split->InputBodyIds.getValues();
    const auto& inputFrames = split->InputFrames.getValues();
    if (sourceBody.getDocument() != document || sourceBodyId.empty()
        || split->SourceBodyId.getValueStr() != sourceBodyId || inputBodyIds.empty()
        || inputBodyIds.front() != sourceBodyId || inputFrames.empty()) {
        throw Base::ValueError(
            "Configure this Split's source and definitions before assigning its regions"
        );
    }
    if (witnesses.size() < 2 || retainedRegion >= witnesses.size()) {
        throw Base::ValueError(
            "Choose one of at least two Split regions to retain the source Body identity"
        );
    }
    for (std::size_t left = 0; left < witnesses.size(); ++left) {
        for (std::size_t right = left + 1; right < witnesses.size(); ++right) {
            if (witnesses[left] == witnesses[right]) {
                throw Base::ValueError(
                    "Every Split region must have one distinct strict interior identity point"
                );
            }
        }
    }

    const auto oldWitnesses = split->RegionWitnesses.getValues();
    const auto oldBodyIds = split->OutputBodyIds.getValues();
    std::vector<Base::Vector3d> orderedWitnesses;
    orderedWitnesses.reserve(witnesses.size());
    orderedWitnesses.push_back(witnesses[retainedRegion]);
    for (std::size_t index = 0; index < witnesses.size(); ++index) {
        if (index != retainedRegion) {
            orderedWitnesses.push_back(witnesses[index]);
        }
    }

    std::vector<std::string> outputBodyIds(orderedWitnesses.size());
    outputBodyIds.front() = sourceBodyId;
    std::vector<bool> oldIdentityUsed(oldBodyIds.size(), false);
    for (std::size_t outputIndex = 1; outputIndex < orderedWitnesses.size(); ++outputIndex) {
        for (std::size_t oldIndex = 0; oldIndex < std::min(oldWitnesses.size(), oldBodyIds.size());
             ++oldIndex) {
            if (oldIdentityUsed[oldIndex] || oldBodyIds[oldIndex].empty()
                || oldBodyIds[oldIndex] == sourceBodyId
                || oldWitnesses[oldIndex] != orderedWitnesses[outputIndex]) {
                continue;
            }
            outputBodyIds[outputIndex] = oldBodyIds[oldIndex];
            oldIdentityUsed[oldIndex] = true;
            break;
        }
    }
    for (std::size_t outputIndex = 1; outputIndex < outputBodyIds.size(); ++outputIndex) {
        if (!outputBodyIds[outputIndex].empty()) {
            continue;
        }
        for (std::size_t oldIndex = 0; oldIndex < oldBodyIds.size(); ++oldIndex) {
            if (oldIdentityUsed[oldIndex] || oldBodyIds[oldIndex].empty()
                || oldBodyIds[oldIndex] == sourceBodyId) {
                continue;
            }
            outputBodyIds[outputIndex] = oldBodyIds[oldIndex];
            oldIdentityUsed[oldIndex] = true;
            break;
        }
        if (outputBodyIds[outputIndex].empty()) {
            outputBodyIds[outputIndex] = Base::Uuid::createUuid();
        }
    }

    std::vector<Base::Placement> outputFrames(outputBodyIds.size(), inputFrames.front());
    std::vector<long> previousInputIndices(outputBodyIds.size(), -1);
    previousInputIndices.front() = 0;
    std::vector<std::string> outputComponentIds(outputBodyIds.size(), sourceBody.ComponentId.getValue());
    outputComponentIds.front().clear();
    boost::dynamic_bitset<> outputPresence(outputBodyIds.size());
    outputPresence.set();

    split->RegionWitnesses.setValues(orderedWitnesses);
    split->OutputBodyIds.setValues(outputBodyIds);
    split->OutputFrames.setValues(outputFrames);
    split->OutputPreviousInputIndices.setValues(previousInputIndices);
    split->OutputPresence.setValues(outputPresence);
    split->OutputComponentIds.setValues(outputComponentIds);
    split->TargetBodyIds.setValues(outputBodyIds);
    split->TargetFrames.setValues(outputFrames);
    split->DestinationComponentId.setValue(
        outputBodyIds.size() == 2 ? sourceBody.ComponentId.getValue() : ""
    );
    split->RetainedRegionChosen.setValue(true);
}

void DesignModel::setSeparateDefinition(
    DesignOperationEdit& edit,
    App::DocumentObject& source,
    App::Part* destinationComponent
)
{
    auto* separate = edit.operation ? freecad_cast<DesignSeparate*>(edit.operation) : nullptr;
    auto* document = separate ? separate->getDocument() : nullptr;
    if (!separate || !document) {
        throw Base::TypeError("Separate configuration requires one live Design Separate "
                              "operation");
    }
    if (destinationComponent
        && (destinationComponent->getDocument() != document
            || componentId(*destinationComponent).empty())) {
        throw Base::ValueError("Separate output must use a Component from the operation "
                               "document");
    }

    auto* exactSource = resolveDefinitionReference(*separate, source);
    auto* feature = freecad_cast<Part::Feature*>(exactSource);
    if (!feature || freecad_cast<DesignBodyState*>(feature)
        || freecad_cast<DesignBodyPublication*>(feature)
        || App::GeoFeatureGroupExtension::getGroupOfObject(feature)
        || App::GroupExtension::getGroupOfObject(feature)) {
        throw Base::ValueError("Separate requires a reusable Design-root multi-solid "
                               "definition, not a Body or grouped feature");
    }

    auto* previousSource = separate->Source.getValue();
    if (!edit.provisionalOperation && previousSource != exactSource) {
        throw Base::ValueError("Changing a Separate source requires explicit result-identity "
                               "reassignment");
    }

    separate->Source.setValue(exactSource);
    separate->ResultOperation.setValue("New Bodies");
    separate->InputStates.setValues({});
    separate->InputBodyIds.setValues(std::vector<std::string> {});
    separate->InputFrames.setValues({});

    const auto oldWitnesses = separate->RegionWitnesses.getValues();
    const auto oldBodyIds = separate->OutputBodyIds.getValues();
    if (!edit.provisionalOperation && oldWitnesses.size() != oldBodyIds.size()) {
        throw Base::RuntimeError(
            "Separate has inconsistent saved result identities and cannot be edited safely"
        );
    }
    const auto assignments = reconcileDesignSeparateRegions(
        *separate,
        edit.provisionalOperation ? std::vector<Base::Vector3d> {} : oldWitnesses
    );

    std::vector<Base::Vector3d> witnesses;
    std::vector<std::string> bodyIds;
    witnesses.reserve(assignments.size());
    bodyIds.reserve(assignments.size());
    for (const auto& assignment : assignments) {
        witnesses.push_back(assignment.witness);
        if (assignment.previousWitnessIndex < 0) {
            bodyIds.push_back(Base::Uuid::createUuid());
            continue;
        }
        const auto previousIndex = static_cast<std::size_t>(assignment.previousWitnessIndex);
        if (previousIndex >= oldBodyIds.size()) {
            throw Base::RuntimeError(
                "Separate reconciliation returned an invalid saved result identity"
            );
        }
        bodyIds.push_back(oldBodyIds[previousIndex]);
    }

    const std::string destinationId = destinationComponent ? componentId(*destinationComponent)
                                                           : std::string();
    std::vector<std::string> outputComponentIds(bodyIds.size(), destinationId);
    std::vector<Base::Placement> outputFrames;
    const auto& previousComponents = separate->OutputComponentIds.getValues();
    const auto& previousFrames = separate->OutputFrames.getValues();
    const bool preserveFrames = !edit.provisionalOperation
        && previousComponents == outputComponentIds && previousFrames.size() == bodyIds.size();
    if (preserveFrames) {
        outputFrames = previousFrames;
    }
    else {
        const Base::Placement frame = destinationComponent
            ? App::GeoFeature::getGlobalPlacement(destinationComponent)
            : Base::Placement();
        outputFrames.assign(bodyIds.size(), frame);
    }

    separate->RegionWitnesses.setValues(witnesses);
    separate->OutputBodyIds.setValues(bodyIds);
    separate->OutputFrames.setValues(outputFrames);
    separate->OutputPreviousInputIndices.setValues(std::vector<long>(bodyIds.size(), -1));
    separate->OutputComponentIds.setValues(outputComponentIds);
    boost::dynamic_bitset<> presence(bodyIds.size());
    presence.set();
    separate->OutputPresence.setValues(presence);
    separate->OutputShapes.setValues({});
    separate->TargetBodyIds.setValues(bodyIds);
    separate->TargetFrames.setValues(outputFrames);
    separate->DestinationComponentId.setValue("");
    separate->PreviewShape.setValue(Part::TopoShape());
}

DesignOperationEdit DesignModel::beginOperationEdit(App::DocumentObject& operation)
{
    auto* document = operation.getDocument();
    auto* properties = dynamic_cast<DesignOperationProperties*>(&operation);
    if (!document || !properties) {
        throw Base::TypeError("Operation editing requires one live Design operation");
    }
    recoverInterruptedOperationPublications(*document);
    ensureDesignOperationPortSchema(operation);

    DesignOperationEdit edit;
    edit.operation = &operation;
    edit.originalResultMode = properties->ResultOperation.getValueAsString();
    const auto outputComponents = properties->OutputComponentIds.getValues();
    const auto previousInputIndices = properties->OutputPreviousInputIndices.getValues();
    if (outputComponents.size() == 1 && previousInputIndices.size() == 1
        && previousInputIndices.front() == -1) {
        edit.originalDestinationComponentId = outputComponents.front();
    }
    const auto rememberFrames =
        [&edit](const std::vector<std::string>& bodyIds, const std::vector<Base::Placement>& frames) {
            for (std::size_t index = 0; index < std::min(bodyIds.size(), frames.size()); ++index) {
                const auto [stored, inserted]
                    = edit.originalTargetFrames.emplace(bodyIds[index], frames[index]);
                if (!inserted && stored->second != frames[index]) {
                    throw Base::RuntimeError("One Body has conflicting saved coordinate frames in "
                                             "the operation ports");
                }
            }
        };
    rememberFrames(properties->InputBodyIds.getValues(), properties->InputFrames.getValues());
    const auto targetIds = properties->OutputBodyIds.getValues();
    const auto targetFrames = properties->OutputFrames.getValues();
    rememberFrames(targetIds, targetFrames);

    auto* timeline = App::DocumentTimeline::get(document);
    edit.provisionalOperation = timeline
        && timeline->isProvisionallyEnrolledByCurrentTransaction(&operation);
    if (edit.originalResultMode == "New Body" && targetIds.size() == 1 && !targetIds.front().empty()) {
        edit.newBodyId = targetIds.front();
    }
    if (edit.provisionalOperation || !timeline
        || !App::DocumentTimeline::hasTimelineOperationRole(&operation)) {
        return edit;
    }

    edit.originalStates = designBodyStatesForOperation(&operation);
    std::vector<App::DocumentObject*> resources;
    for (auto* candidate : timeline->Operations.getValues()) {
        if (candidate && App::DocumentTimeline::isTimelineResourceOwnedBy(candidate, &operation)) {
            resources.push_back(candidate);
        }
    }
    if (resources.size() != edit.originalStates.size()
        || !std::ranges::all_of(edit.originalStates, [&resources](auto* state) {
               return std::ranges::find(resources, state) != resources.end();
           })) {
        throw Base::RuntimeError("The Design operation has an incomplete persisted resource graph");
    }

    return edit;
}

std::vector<Body*> DesignModel::finalizeOperation(
    DesignOperationEdit& edit
)
{
    return finalizeOperationImpl(edit, false);
}

std::vector<Body*> DesignModel::finalizeOperation(
    DesignOperationEdit& edit,
    bool affectedBodiesOnly
)
{
    return finalizeOperationImpl(edit, affectedBodiesOnly);
}

std::vector<Body*> DesignModel::finalizeScriptOperation(DesignOperationEdit& edit)
{
    if (!freecad_cast<DesignScriptOperation*>(edit.operation)) {
        throw Base::TypeError(
            "Only a DesignScriptOperation can use deferred downstream recompute"
        );
    }
    return finalizeOperationImpl(edit, true);
}

std::vector<Body*> DesignModel::finalizeOperationImpl(
    DesignOperationEdit& edit,
    bool affectedBodiesOnly
)
{
    auto* operation = edit.operation;
    auto* document = operation ? operation->getDocument() : nullptr;
    auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
    if (!document || !properties) {
        throw Base::RuntimeError("The Design operation was removed while it was being edited");
    }
    ensureDesignOperationPortSchema(*operation);

    auto* timeline = App::DocumentTimeline::get(document);
    if (edit.provisionalOperation
        && (!timeline
            || !timeline->isProvisionallyEnrolledByCurrentTransaction(operation))) {
        throw Base::RuntimeError(
            std::string("New Design operation '") + operation->getNameInDocument()
            + "' is no longer enrolled by the active creation transaction; cancel "
              "this task and start the operation again"
        );
    }

    // Recompute the edited controller and its prerequisites before changing
    // the persistent state graph. A full document recompute here would also
    // execute old state resources whose output slots are intentionally being
    // added or removed by this edit, producing transient false errors.
    document->recomputeFeature(operation, true);
    if (!operation->isValid()) {
        throw Base::RuntimeError(operation->getStatusString());
    }
    const auto outputCount = properties->OutputBodyIds.getValues().size();
    const bool bodylessScript = outputCount == 0
        && freecad_cast<const DesignScriptOperation*>(operation);
    if ((!bodylessScript && outputCount == 0)
        || properties->OutputShapes.getValues().size() != outputCount
        || properties->OutputPresence.getValues().size() != outputCount
        || properties->OutputFrames.getValues().size() != outputCount
        || properties->OutputPreviousInputIndices.getValues().size() != outputCount
        || properties->OutputComponentIds.getValues().size() != outputCount) {
        throw Base::RuntimeError("The Design operation did not produce one atomic output per "
                                 "declared output Body port");
    }

    // Reusable definitions are immutable History inputs.  Reject incomplete
    // identity or forward-ordering state before creating Bodies, publications,
    // or BodyState resources for this operation.
    preflightReusableDefinitionDependencies(*operation);

    std::vector<Body*> targets;
    if (edit.provisionalOperation) {
        finalizeNewOperation(edit, targets);
    }
    else {
        finalizeExistingOperation(edit, targets);
    }
    if (const auto* script = freecad_cast<const DesignScriptOperation*>(operation)) {
        const auto labels = script->ScriptOutputLabels.getValues();
        if (labels.size() != targets.size()) {
            throw Base::RuntimeError("A VibeScript operation did not resolve one Body per output "
                                     "label");
        }
        for (std::size_t index = 0; index < targets.size(); ++index) {
            targets[index]->Label.setValue(labels[index]);
        }
    }
    else if (const auto* generated = freecad_cast<const DesignGeneratedOperation*>(operation)) {
        if (targets.size() != 1 || generated->OutputLabel.getStrValue().empty()) {
            throw Base::RuntimeError(
                "A generated Design operation did not resolve one labeled output Body"
            );
        }
        targets.front()->Label.setValue(generated->OutputLabel.getValue());
    }
    if (affectedBodiesOnly) {
        std::vector<App::DocumentObject*> affectedBodies;
        affectedBodies.reserve(targets.size());
        for (auto* body : targets) {
            affectedBodies.push_back(body);
        }
        bool hasError = false;
        document->recompute(affectedBodies, true, &hasError);
        if (hasError
            || std::ranges::any_of(targets, [](const Body* body) {
                   return !body || !body->isValid();
               })) {
            throw Base::RuntimeError(
                "A VibeScript output Body failed its targeted publication recompute"
            );
        }
    }
    else {
        document->recompute();
    }
    validateDesign(*document);
    return targets;
}

std::vector<std::string> DesignModel::removeOperationResources(App::DocumentObject& operation)
{
    auto* document = operation.getDocument();
    auto* properties = dynamic_cast<DesignOperationProperties*>(&operation);
    if (!document || !properties) {
        throw Base::TypeError("Design operation removal requires one live Design operation");
    }
    auto* timeline = App::DocumentTimeline::get(document);
    if (!timeline || !App::DocumentTimeline::hasTimelineOperationRole(&operation)) {
        throw Base::RuntimeError("Only an accepted global History operation can be removed");
    }

    Part::Feature* generatedResource = nullptr;
    if (auto* generated = freecad_cast<DesignGeneratedOperation*>(&operation)) {
        generatedResource = freecad_cast<Part::Feature*>(generated->Generator.getValue());
        if (!generatedResource || generatedResource == &operation
            || generatedResource->getDocument() != document) {
            throw Base::RuntimeError(
                "This generated Design operation has no exact internal generator"
            );
        }
        const auto generatorConsumers = nonTimelineConsumers(*generatedResource);
        if (generatorConsumers.size() != 1 || generatorConsumers.front() != &operation) {
            throw Base::RuntimeError(
                "This generated Design operation's internal generator is used elsewhere"
            );
        }
    }

    auto states = designBodyStatesForOperation(&operation);
    if (states.size() != static_cast<std::size_t>(properties->OutputBodyIds.getSize())) {
        throw Base::RuntimeError("The Design operation has an incomplete output resource graph");
    }
    const std::unordered_set<App::DocumentObject*> stateObjects(states.begin(), states.end());
    for (auto* consumer : nonTimelineConsumers(operation)) {
        if (!stateObjects.contains(consumer)) {
            throw Base::RuntimeError("This Design operation is referenced by another document "
                                     "object and cannot be removed");
        }
    }

    std::vector<std::string> removedBodies;
    for (auto* state : states) {
        if (!state) {
            throw Base::RuntimeError("The Design operation has a missing output state");
        }
        auto* previous = state->PreviousState.getValue();
        if (previous) {
            replaceBodyStateInChain(*document, state->BodyId.getValueStr(), state, previous);
            continue;
        }
        auto* body = bodyWithId(*document, state->BodyId.getValueStr());
        if (!body) {
            throw Base::RuntimeError("An operation-created Body lost its persistent identity");
        }
        removedBodies.emplace_back(body->getNameInDocument());
        removeOperationCreatedBody(*document, *state);
    }

    for (auto* state : states) {
        if (!nonTimelineConsumers(*state).empty()) {
            throw Base::RuntimeError("A removed Design output still has a live consumer");
        }
        const std::string name = state->getNameInDocument();
        document->removeObject(name.c_str());
    }
    if (generatedResource) {
        auto* generated = freecad_cast<DesignGeneratedOperation*>(&operation);
        generated->Generator.setValue(nullptr);
        if (!nonTimelineConsumers(*generatedResource).empty()) {
            throw Base::RuntimeError(
                "The generated Design operation left a live internal-generator consumer"
            );
        }
        const std::string generatorName = generatedResource->getNameInDocument();
        document->removeObject(generatorName.c_str());
    }
    if (!nonTimelineConsumers(operation).empty()) {
        throw Base::RuntimeError("The removed Design resources left a live operation consumer");
    }
    return removedBodies;
}

std::vector<std::string> DesignModel::removeOperation(App::DocumentObject& operation)
{
    auto* document = operation.getDocument();
    if (!document) {
        throw Base::TypeError("Design operation removal requires one live Design operation");
    }
    std::vector<std::string> removedBodies = removeOperationResources(operation);
    const std::string operationName = operation.getNameInDocument();
    document->removeObject(operationName.c_str());
    document->recompute();
    validateDesign(*document);
    return removedBodies;
}

DesignBodyPublication* DesignModel::ensurePublication(App::Document& document, Body& body)
{
    if (body.getDocument() != &document) {
        throw Base::ValueError("A Body publication must be created in its Body document");
    }
    if (auto* publication = findDesignBodyPublication(&body)) {
        return publication;
    }

    auto* previousState = freecad_cast<Part::Feature*>(body.Tip.getValue());
    const std::string name = document.getUniqueObjectName("BodyResult");
    auto* publication = document.addObject<DesignBodyPublication>(name.c_str());
    body.addObject(publication);
    publication->BaseFeature.setValue(nullptr);
    publication->DesignId.setValue(body.DesignId.getValue());
    publication->BodyId.setValue(body.VibeCADBodyId.getValue());
    publication->CurrentState.setValue(previousState);
    document.classifyProvisionalTimelineInternalObject(publication);
    return publication;
}

DesignBodyState* DesignModel::initializeLegacyBodyState(Body& body, Part::Feature& legacyTip)
{
    auto* document = body.getDocument();
    if (!document || legacyTip.getDocument() != document) {
        throw Base::ValueError(
            "Legacy Body promotion requires one Body and tip in the same live document"
        );
    }
    const int transactionId = document->getBookedTransactionID();
    if (transactionId == App::NullTransaction
        || !App::GetApplication().transactionIsActive(transactionId)) {
        throw Base::RuntimeError(
            "Legacy Body promotion requires one caller-owned document transaction"
        );
    }
    if (findDesignBodyPublication(&body)) {
        throw Base::ValueError("This Body already has a stable Design publication");
    }
    if (body.Tip.getValue() != &legacyTip || !body.hasObject(&legacyTip)
        || Body::findBodyOf(&legacyTip) != &body) {
        throw Base::ValueError(
            "Legacy Body promotion requires the selected feature to be the exact Body tip"
        );
    }
    const auto members = body.Group.getValues();
    if (members.size() != 1 || members.front() != &legacyTip) {
        throw Base::ValueError(
            "Only a Body containing one standalone legacy feature can be promoted automatically"
        );
    }
    if (body.VibeCADBodyId.getValueStr().empty() || body.DesignId.getValueStr().empty()) {
        throw Base::RuntimeError("The legacy Body has no persistent Design identity");
    }
    if (std::ranges::any_of(
            document->getObjectsOfType<DesignBodyState>(),
            [&body](const DesignBodyState* state) {
                return state && state->BodyId.getValueStr() == body.VibeCADBodyId.getValueStr();
            }
        )) {
        throw Base::ValueError("This Body already participates in the Design state graph");
    }

    Part::TopoShape initialShape = legacyTip.Shape.getShape();
    requireBodyShape(body, initialShape, "The legacy Body tip");
    const Base::Placement legacyGlobal = App::GeoFeature::getGlobalPlacement(&legacyTip);

    const std::string stateName = document->getUniqueObjectName("InitialBodyState");
    auto* initialState = document->addObject<DesignBodyState>(stateName.c_str());
    initialState->Operation.setValue(nullptr);
    initialState->BodyId.setValue(body.VibeCADBodyId.getValue());
    initialState->PreviousState.setValue(nullptr);
    initialState->Present.setValue(true);
    initialState->Shape.setValue(initialShape);
    document->classifyProvisionalTimelineInternalObject(initialState);

    auto* publication = ensurePublication(*document, body);
    publication->CurrentState.setValue(initialState);
    body.removeObject(&legacyTip);
    legacyTip.Placement.setValue(legacyGlobal);
    body.Tip.setValue(publication);

    const auto finalMembers = body.Group.getValues();
    if (finalMembers.size() != 1 || finalMembers.front() != publication
        || Body::findBodyOf(&legacyTip)) {
        throw Base::RuntimeError(
            "Legacy Body promotion did not isolate its generator from the rendered Body"
        );
    }
    document->recomputeFeature(initialState, true);
    document->recomputeFeature(publication, true);
    document->recomputeFeature(&body, true);
    if (!initialState->isValid() || !publication->isValid() || !body.isValid()) {
        throw Base::RuntimeError("Legacy Body promotion produced an invalid initial state");
    }
    return initialState;
}

void DesignModel::remapImportedGraph(
    App::Document& document,
    const std::vector<App::DocumentObject*>& importedObjects
)
{
    if (importedObjects.empty()) {
        return;
    }

    std::unordered_set<App::DocumentObject*> imported;
    imported.reserve(importedObjects.size());
    std::vector<Component*> components;
    std::vector<Body*> bodies;
    std::vector<App::DocumentObject*> sketches;
    std::vector<App::DocumentObject*> definitions;
    std::vector<App::DocumentObject*> operations;
    std::vector<DesignBodyState*> states;
    std::vector<DesignBodyPublication*> publications;
    for (auto* object : importedObjects) {
        if (!object || object->getDocument() != &document || !document.containsObject(object)
            || !object->testStatus(App::ObjectStatus::ObjImporting)
            || !imported.insert(object).second) {
            throw Base::RuntimeError("A Design import contains a missing, duplicate, or "
                                     "cross-document object");
        }
        if (auto* component = freecad_cast<Component*>(object)) {
            components.push_back(component);
        }
        if (auto* body = freecad_cast<Body*>(object)) {
            bodies.push_back(body);
        }
        if (object->PropertyContainer::getPropertyByName("VibeCADSketchId")) {
            sketches.push_back(object);
        }
        if (object->PropertyContainer::getPropertyByName("VibeCADDefinitionId")) {
            definitions.push_back(object);
        }
        if (dynamic_cast<DesignOperationProperties*>(object)) {
            ensureDesignOperationPortSchema(*object);
            operations.push_back(object);
        }
        if (auto* state = freecad_cast<DesignBodyState*>(object)) {
            states.push_back(state);
        }
        if (auto* publication = freecad_cast<DesignBodyPublication*>(object)) {
            publications.push_back(publication);
        }
    }
    if (components.empty() && bodies.empty() && sketches.empty() && definitions.empty()
        && operations.empty() && states.empty() && publications.empty()) {
        return;
    }

    auto* timeline = App::DocumentTimeline::ensure(&document);
    const std::string targetDesignId = timeline->DesignId.getValueStr();
    if (targetDesignId.empty()) {
        throw Base::RuntimeError("A Design import requires one persistent destination Design");
    }

    std::unordered_set<std::string> usedIdentities;
    usedIdentities.insert(targetDesignId);
    constexpr const char* identityProperties[] = {
        "ComponentId",
        "VibeCADBodyId",
        "VibeCADSketchId",
        "VibeCADDefinitionId",
        "OperationId",
        "BodyStateId",
    };
    for (auto* object : document.getObjects()) {
        if (!object) {
            continue;
        }
        for (const auto* name : identityProperties) {
            const std::string value = uuidPropertyValue(*object, name);
            if (!value.empty()) {
                usedIdentities.insert(value);
            }
        }
    }

    const auto freshIdentity = [&usedIdentities]() {
        std::string identity;
        do {
            identity = Base::Uuid::createUuid();
        } while (!usedIdentities.insert(identity).second);
        return identity;
    };

    std::unordered_map<std::string, std::string> remap;
    const auto defineIdentity = [&remap,
                                 &freshIdentity](const std::string& oldIdentity, const char* kind) {
        if (oldIdentity.empty() || remap.contains(oldIdentity)) {
            throw Base::RuntimeError(
                std::string("The imported ") + kind
                + " graph has a missing or duplicate defining identity"
            );
        }
        remap.emplace(oldIdentity, freshIdentity());
    };
    const auto requireMapped =
        [&remap](const std::string& oldIdentity, const char* relationship) -> const std::string& {
        const auto found = remap.find(oldIdentity);
        if (found == remap.end()) {
            throw Base::RuntimeError(
                std::string("Cannot import a partial Design graph: ") + relationship
                + " is outside the imported object set"
            );
        }
        return found->second;
    };

    for (auto* component : components) {
        defineIdentity(component->ComponentId.getValueStr(), "Component");
    }
    for (auto* body : bodies) {
        defineIdentity(body->VibeCADBodyId.getValueStr(), "Body");
    }
    for (auto* sketch : sketches) {
        defineIdentity(uuidPropertyValue(*sketch, "VibeCADSketchId"), "Sketch");
    }
    for (auto* definition : definitions) {
        defineIdentity(uuidPropertyValue(*definition, "VibeCADDefinitionId"), "Definition");
    }
    for (auto* operation : operations) {
        auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
        defineIdentity(properties->OperationId.getValueStr(), "History operation");
    }
    for (auto* state : states) {
        defineIdentity(state->BodyStateId.getValueStr(), "Body state");
    }

    // First replace every defining identity. References are intentionally
    // untouched until all definitions have their final UUIDs.
    for (auto* component : components) {
        component->ComponentId.setValue(
            requireMapped(component->ComponentId.getValueStr(), "Component identity")
        );
        component->DesignId.setValue(targetDesignId);
    }
    for (auto* body : bodies) {
        body->VibeCADBodyId.setValue(requireMapped(body->VibeCADBodyId.getValueStr(), "Body identity")
        );
        body->DesignId.setValue(targetDesignId);
    }
    for (auto* sketch : sketches) {
        auto* sketchId = dynamic_cast<App::PropertyUUID*>(
            sketch->PropertyContainer::getPropertyByName("VibeCADSketchId")
        );
        auto* designId = dynamic_cast<App::PropertyUUID*>(
            sketch->PropertyContainer::getPropertyByName("DesignId")
        );
        if (!sketchId || !designId) {
            throw Base::RuntimeError("An imported Sketch has an incomplete Design identity contract");
        }
        sketchId->setValue(requireMapped(sketchId->getValueStr(), "Sketch identity"));
        designId->setValue(targetDesignId);
    }
    for (auto* definition : definitions) {
        auto* definitionId = dynamic_cast<App::PropertyUUID*>(
            definition->PropertyContainer::getPropertyByName("VibeCADDefinitionId")
        );
        auto* designId = dynamic_cast<App::PropertyUUID*>(
            definition->PropertyContainer::getPropertyByName("DesignId")
        );
        if (!definitionId || !designId) {
            throw Base::RuntimeError("An imported definition has an incomplete Design identity "
                                     "contract");
        }
        definitionId->setValue(requireMapped(definitionId->getValueStr(), "Definition identity"));
        designId->setValue(targetDesignId);
    }
    for (auto* operation : operations) {
        auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
        properties->OperationId.setValue(
            requireMapped(properties->OperationId.getValueStr(), "History operation identity")
        );
        properties->DesignId.setValue(targetDesignId);
    }
    for (auto* state : states) {
        state->BodyStateId.setValue(
            requireMapped(state->BodyStateId.getValueStr(), "Body-state identity")
        );
        state->DesignId.setValue(targetDesignId);
    }

    // Then rewrite every UUID reference using the one batch-wide map.
    for (auto* body : bodies) {
        const auto* parent = freecad_cast<const Component*>(App::Part::getPartOfObject(body));
        body->ComponentId.setValue(parent ? parent->ComponentId.getValueStr() : std::string());
    }
    for (auto* operation : operations) {
        auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
        auto inputBodyIds = properties->InputBodyIds.getValues();
        for (auto& bodyId : inputBodyIds) {
            bodyId = requireMapped(bodyId, "an operation input Body");
        }
        properties->InputBodyIds.setValues(inputBodyIds);

        auto outputBodyIds = properties->OutputBodyIds.getValues();
        for (auto& bodyId : outputBodyIds) {
            bodyId = requireMapped(bodyId, "an operation output Body");
        }
        properties->OutputBodyIds.setValues(outputBodyIds);
        properties->TargetBodyIds.setValues(outputBodyIds);
        if (auto* combine = freecad_cast<DesignCombine*>(operation)) {
            combine->ResultBodyId.setValue(
                requireMapped(combine->ResultBodyId.getValueStr(), "a Combine result Body")
            );
        }
        if (auto* split = freecad_cast<DesignSplit*>(operation)) {
            split->SourceBodyId.setValue(
                requireMapped(split->SourceBodyId.getValueStr(), "a Split source Body")
            );
        }

        auto outputComponentIds = properties->OutputComponentIds.getValues();
        for (auto& componentId : outputComponentIds) {
            if (!componentId.empty()) {
                componentId = requireMapped(componentId, "an operation output Component");
            }
        }
        properties->OutputComponentIds.setValues(outputComponentIds);

        const std::string destination = properties->DestinationComponentId.getValue();
        if (!destination.empty()) {
            properties->DestinationComponentId.setValue(
                requireMapped(destination, "an operation destination Component")
            );
        }
    }
    for (auto* state : states) {
        auto* operation = state->Operation.getValue();
        if (!operation || !imported.contains(operation)) {
            throw Base::RuntimeError("Cannot import a partial Design graph: a Body state's "
                                     "producing operation is outside the imported object set");
        }
        state->BodyId.setValue(requireMapped(state->BodyId.getValueStr(), "a Body-state Body"));
        state->OperationId.setValue(
            requireMapped(state->OperationId.getValueStr(), "a Body-state operation")
        );
    }
    for (auto* publication : publications) {
        publication->DesignId.setValue(targetDesignId);
        publication->BodyId.setValue(
            requireMapped(publication->BodyId.getValueStr(), "a publication Body")
        );
        if (auto* state = freecad_cast<DesignBodyState*>(publication->CurrentState.getValue())) {
            if (!imported.contains(state)) {
                throw Base::RuntimeError("Cannot import a partial Design graph: a publication's "
                                         "current Body state is outside the imported object set");
            }
            publication->BodyStateId.setValue(state->BodyStateId.getValue());
        }
        else {
            publication->BodyStateId.setValue(NoDesignBodyStateId);
        }
    }
}

void DesignModel::finalizeNewOperation(DesignOperationEdit& edit, std::vector<Body*>& targets)
{
    auto* operation = edit.operation;
    auto* document = operation->getDocument();
    auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
    const auto bodyIds = properties->OutputBodyIds.getValues();
    const auto inputs = properties->InputStates.getValues();
    const auto inputBodyIds = properties->InputBodyIds.getValues();
    const auto previousInputIndices = properties->OutputPreviousInputIndices.getValues();
    const auto outputFrames = properties->OutputFrames.getValues();
    const auto outputComponentIds = properties->OutputComponentIds.getValues();

    const bool bodylessScript = bodyIds.empty()
        && freecad_cast<const DesignScriptOperation*>(operation);
    if ((!bodylessScript && bodyIds.empty()) || (bodylessScript && !inputs.empty())
        || inputs.size() != inputBodyIds.size() || previousInputIndices.size() != bodyIds.size()
        || outputFrames.size() != bodyIds.size() || outputComponentIds.size() != bodyIds.size()) {
        throw Base::RuntimeError("A Design operation has inconsistent input or output ports");
    }
    ensureInputBodyPublications(*document, *properties);
    targets.reserve(bodyIds.size());
    std::vector<Part::Feature*> previousStates;
    previousStates.reserve(bodyIds.size());
    for (std::size_t index = 0; index < bodyIds.size(); ++index) {
        const long previousInputIndex = previousInputIndices[index];
        Body* body = nullptr;
        Part::Feature* previous = nullptr;
        if (previousInputIndex == -1) {
            body = createOperationBody(
                *document,
                *properties,
                bodyIds[index],
                outputComponentIds[index],
                outputFrames[index]
            );
        }
        else {
            if (previousInputIndex < 0
                || static_cast<std::size_t>(previousInputIndex) >= inputs.size()
                || inputBodyIds[static_cast<std::size_t>(previousInputIndex)] != bodyIds[index]
                || !outputComponentIds[index].empty()) {
                throw Base::RuntimeError("A Design output has an invalid prior-state mapping");
            }
            body = bodyWithId(*document, bodyIds[index]);
            previous = freecad_cast<Part::Feature*>(
                inputs[static_cast<std::size_t>(previousInputIndex)]
            );
            if (!body || !previous || previous->getDocument() != document) {
                throw Base::RuntimeError("A Design output lost its Body or exact prior state");
            }
        }
        if (!body) {
            throw Base::RuntimeError("A Design operation could not resolve an output Body");
        }
        targets.push_back(body);
        previousStates.push_back(previous);
    }

    std::vector<DesignBodyState*> states;
    states.reserve(targets.size());
    for (std::size_t index = 0; index < targets.size(); ++index) {
        auto* previous = previousStates[index];
        auto* publication = ensurePublication(*document, *targets[index]);
        auto* state = createOutputState(
            *document,
            *operation,
            *targets[index],
            previous,
            static_cast<int>(index)
        );
        states.push_back(state);

        if (!previous) {
            publication->CurrentState.setValue(state);
        }
        else {
            replaceBodyStateInChain(*document, bodyIds[index], previous, state);
        }
    }

    classifyOperation(*operation);
    std::vector<App::DocumentObject*> block;
    block.reserve(states.size() + 1);
    for (auto* state : states) {
        classifyStateResource(*state, *operation);
        block.push_back(state);
    }
    block.push_back(operation);
    App::DocumentTimeline::ensure(document)->finalizeProvisionalOperationBlock(operation, block);

    // The semantic block is now persistent.  Any later recompute or Design
    // validation failure must retry this as an existing operation rather than
    // publishing another set of BodyState resources.
    edit.originalStates.assign(states.begin(), states.end());
    edit.provisionalOperation = false;
    edit.resourcesStaged = false;
}

void DesignModel::finalizeExistingOperation(DesignOperationEdit& edit, std::vector<Body*>& targets)
{
    auto* operation = edit.operation;
    auto* document = operation->getDocument();
    auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
    auto* timeline = App::DocumentTimeline::get(document);
    const auto bodyIds = properties->OutputBodyIds.getValues();
    const auto inputs = properties->InputStates.getValues();
    const auto inputBodyIds = properties->InputBodyIds.getValues();
    const auto previousInputIndices = properties->OutputPreviousInputIndices.getValues();
    const auto outputFrames = properties->OutputFrames.getValues();
    const auto outputComponentIds = properties->OutputComponentIds.getValues();
    const bool bodylessScript = bodyIds.empty()
        && freecad_cast<const DesignScriptOperation*>(operation);
    if (!timeline || (!bodylessScript && bodyIds.empty()) || (bodylessScript && !inputs.empty())
        || inputs.size() != inputBodyIds.size() || previousInputIndices.size() != bodyIds.size()
        || outputFrames.size() != bodyIds.size() || outputComponentIds.size() != bodyIds.size()) {
        throw Base::RuntimeError(
            "The edited Design operation has inconsistent input or output ports"
        );
    }

    std::unordered_map<std::string, DesignBodyState*> oldByBody;
    for (auto* state : edit.originalStates) {
        if (!state || !oldByBody.emplace(state->BodyId.getValueStr(), state).second) {
            throw Base::RuntimeError("The existing operation has duplicate Body-state resources");
        }
    }

    // Removing an output which this operation originally created also removes
    // that Body's publication and container. Complete that presentation
    // change before the accepted-state snapshot is staged; otherwise timeline
    // reconciliation correctly sees an unrelated visibility mutation.
    std::unordered_set<DesignBodyState*> removedCreatedStates;
    std::unordered_map<std::string, long> finalMappings;
    for (std::size_t index = 0; index < bodyIds.size(); ++index) {
        finalMappings.emplace(bodyIds[index], previousInputIndices[index]);
    }
    for (const auto& [bodyId, state] : oldByBody) {
        if (!state || state->PreviousState.getValue()) {
            continue;
        }
        const auto retained = finalMappings.find(bodyId);
        if (retained != finalMappings.end()) {
            if (retained->second != -1) {
                throw Base::RuntimeError("An operation-created Body cannot become an update of "
                                         "that same Body identity");
            }
            continue;
        }
        removeOperationCreatedBody(*document, *state);
        removedCreatedStates.insert(state);
    }

    // Adopting an existing input-only or output Body creates its one stable
    // publication and changes which object supplies the rendered result.
    // Complete that presentation migration before staging resource
    // reconciliation so it cannot be mistaken for an unrelated mutation.
    ensureInputBodyPublications(*document, *properties);

    if (!edit.resourcesStaged) {
        timeline->stageOperationResourceReconciliation(
            operation,
            std::vector<App::DocumentObject*>(edit.originalStates.begin(), edit.originalStates.end())
        );
        edit.resourcesStaged = true;
    }

    std::vector<DesignBodyState*> finalStates;
    finalStates.reserve(bodyIds.size());
    targets.reserve(bodyIds.size());
    for (std::size_t index = 0; index < bodyIds.size(); ++index) {
        Body* body = nullptr;
        Part::Feature* previous = nullptr;
        const long previousInputIndex = previousInputIndices[index];
        const auto old = oldByBody.find(bodyIds[index]);
        if (previousInputIndex == -1) {
            if (old != oldByBody.end()) {
                if (old->second->PreviousState.getValue()) {
                    throw Base::RuntimeError("An existing Body cannot become an operation-created "
                                             "output without receiving a new identity");
                }
                body = bodyWithId(*document, bodyIds[index]);
                if (!body) {
                    throw Base::RuntimeError(
                        "An operation-created Body lost its persistent identity"
                    );
                }
                App::Part* destination = nullptr;
                if (!outputComponentIds[index].empty()) {
                    destination = componentWithId(*document, outputComponentIds[index]);
                    if (!destination) {
                        throw Base::RuntimeError(
                            "An operation-created Body's destination Component "
                            "no longer exists"
                        );
                    }
                }
                moveBodyToComponent(*body, destination);
                setCreatedBodyFrame(*body, destination, outputFrames[index]);
            }
            else {
                body = createOperationBody(
                    *document,
                    *properties,
                    bodyIds[index],
                    outputComponentIds[index],
                    outputFrames[index]
                );
            }
        }
        else {
            if (previousInputIndex < 0
                || static_cast<std::size_t>(previousInputIndex) >= inputs.size()
                || inputBodyIds[static_cast<std::size_t>(previousInputIndex)] != bodyIds[index]
                || !outputComponentIds[index].empty()) {
                throw Base::RuntimeError(
                    "An edited Design output has an invalid prior-state mapping"
                );
            }
            body = bodyWithId(*document, bodyIds[index]);
            previous = freecad_cast<Part::Feature*>(
                inputs[static_cast<std::size_t>(previousInputIndex)]
            );
            if (!body || !previous) {
                throw Base::RuntimeError("An edited target lost its Body or exact prior state");
            }
            if (old != oldByBody.end() && !old->second->PreviousState.getValue()) {
                throw Base::RuntimeError("An operation-created Body cannot become an update of "
                                         "that same Body identity");
            }
        }
        targets.push_back(body);

        DesignBodyState* state = nullptr;
        if (old != oldByBody.end()) {
            state = old->second;
            oldByBody.erase(old);
            state->OutputIndex.setValue(static_cast<int>(index));
            state->PreviousState.setValue(previous);
            auto* publication = ensurePublication(*document, *body);
            if (!previous && publication->CurrentState.getValue() != state) {
                throw Base::RuntimeError("An operation-created Body publication no longer points "
                                         "to this operation's exact state");
            }
        }
        else {
            auto* publication = ensurePublication(*document, *body);
            state = createOutputState(*document, *operation, *body, previous, static_cast<int>(index));
            classifyStateResource(*state, *operation);
            if (!previous) {
                publication->CurrentState.setValue(state);
            }
            else {
                replaceBodyStateInChain(*document, bodyIds[index], previous, state);
            }
        }
        finalStates.push_back(state);
    }

    std::vector<DesignBodyState*> retiredStates;
    std::unordered_map<DesignBodyState*, App::DocumentObject*> retiredReplacements;
    for (const auto& [bodyId, state] : oldByBody) {
        auto* previous = state ? state->PreviousState.getValue() : nullptr;
        if (!state) {
            throw Base::RuntimeError("A removed target has no exact Body state");
        }
        if (previous) {
            replaceBodyStateInChain(*document, bodyId, state, previous);
            retiredReplacements.emplace(state, previous);
        }
        else if (!removedCreatedStates.contains(state)) {
            removeOperationCreatedBody(*document, *state);
        }
        if (!nonTimelineConsumers(*state).empty()) {
            throw Base::RuntimeError("A removed target Body state still has a retained consumer");
        }
        retiredStates.push_back(state);
    }

    App::TimelineResourceReconciliationMapping mapping;
    mapping.owner = operation;
    mapping.orderedFinalResources.assign(finalStates.begin(), finalStates.end());
    mapping.stateSourceIndices.assign(finalStates.size(), -1);
    mapping.consumerReplacementIndices.assign(edit.originalStates.size(), -1);
    mapping.consumerReplacementObjects.assign(edit.originalStates.size(), nullptr);
    for (std::size_t finalIndex = 0; finalIndex < finalStates.size(); ++finalIndex) {
        const auto old = std::ranges::find(edit.originalStates, finalStates[finalIndex]);
        if (old == edit.originalStates.end()) {
            continue;
        }
        const auto oldIndex = static_cast<long>(std::distance(edit.originalStates.begin(), old));
        mapping.stateSourceIndices[finalIndex] = oldIndex;
        mapping.consumerReplacementIndices[static_cast<std::size_t>(oldIndex)] = static_cast<long>(
            finalIndex
        );
    }
    for (std::size_t oldIndex = 0; oldIndex < edit.originalStates.size(); ++oldIndex) {
        const auto replacement = retiredReplacements.find(edit.originalStates[oldIndex]);
        if (replacement != retiredReplacements.end()) {
            mapping.consumerReplacementObjects[oldIndex] = replacement->second;
        }
    }

    timeline->finalizeProvisionalOperationResourceReconciliation(mapping);
    for (auto* state : retiredStates) {
        const std::string name = state->getNameInDocument();
        document->removeObject(name.c_str());
    }

    // Existing state objects retain their persistent identities across a
    // parameter edit. Output properties are deliberately marked as outputs,
    // so changing only generated geometry does not dirty consumers through
    // ordinary property propagation. Explicitly republish every retained
    // state at this accepted-edit boundary; downstream states, the stable
    // publication, and the Body then recompute in dependency order.
    for (std::size_t index = 0; index < finalStates.size(); ++index) {
        auto* state = finalStates[index];
        auto* body = index < targets.size() ? targets[index] : nullptr;
        if (!state || !body || state->getDocument() != document
            || body->getDocument() != document) {
            throw Base::RuntimeError(
                "An edited Design output lost its retained state or Body "
                "before publication"
            );
        }
        // Mark the exact dependency property, not only the coarse object
        // status. DesignBodyState::mustExecute() intentionally keys off its
        // operation port so timeline filtering cannot clear an otherwise
        // anonymous force-recompute request.
        state->Operation.touch();
        document->recomputeFeature(state, true);
        if (!state->isValid()) {
            throw Base::RuntimeError(
                state->getStatusString() && *state->getStatusString()
                    ? state->getStatusString()
                    : "An edited Design Body state failed to republish"
            );
        }
        if (auto* publication = findDesignBodyPublication(body)) {
            publication->CurrentState.touch();
        }
        body->Tip.touch();
    }
}

void DesignModel::validateDesign(App::Document& document)
{
    auto* timeline = App::DocumentTimeline::get(&document);
    if (!timeline || timeline->DesignId.getValueStr().empty()) {
        throw Base::RuntimeError("This document has no persistent Design identity");
    }
    const std::string designId = timeline->DesignId.getValueStr();

    std::unordered_map<std::string, Component*> components;
    for (auto* component : document.getObjectsOfType<Component>()) {
        requireDesignIdentity(*component, designId, "Component");
        if (component->Type.getStrValue() != "Component") {
            throw Base::RuntimeError("A Design Component lost its Component classification");
        }
        insertUniqueIdentity(components, component->ComponentId.getValueStr(), component, "Component");
    }

    std::unordered_map<std::string, Body*> bodies;
    for (auto* body : document.getObjectsOfType<Body>()) {
        insertUniqueIdentity(bodies, body->VibeCADBodyId.getValueStr(), body, "Body");
    }

    const auto& history = timeline->Operations.getValues();
    std::unordered_map<App::DocumentObject*, std::size_t> historyPositions;
    historyPositions.reserve(history.size());
    for (std::size_t index = 0; index < history.size(); ++index) {
        auto* entry = history[index];
        if (!entry || !historyPositions.emplace(entry, index).second) {
            throw Base::RuntimeError("Global History contains a missing or duplicate object");
        }
    }

    std::unordered_map<std::string, App::DocumentObject*> sketches;
    for (auto* sketch : document.getObjects()) {
        auto* identityProperty = sketch
            ? sketch->PropertyContainer::getPropertyByName("VibeCADSketchId")
            : nullptr;
        if (!identityProperty || !App::DocumentTimeline::hasTimelineOperationRole(sketch)) {
            continue;
        }
        auto* sketchId = dynamic_cast<App::PropertyUUID*>(identityProperty);
        if (!sketchId) {
            throw Base::RuntimeError("A reusable Sketch has an incompatible identity property");
        }
        requireDesignIdentity(*sketch, designId, "Sketch");
        insertUniqueIdentity(sketches, sketchId->getValueStr(), sketch, "Sketch");
        if (dynamic_cast<DesignOperationProperties*>(sketch) || isForbiddenOperationReference(sketch)
            || Body::findBodyOf(sketch) || App::GeoFeatureGroupExtension::getGroupOfObject(sketch)
            || App::GroupExtension::getGroupOfObject(sketch)) {
            throw Base::RuntimeError("A reusable Sketch is not at Design scope");
        }
        const auto* ownerProperty = sketch->PropertyContainer::getPropertyByName(
            App::DocumentTimeline::OwnerPropertyName
        );
        const auto* ownerLink = dynamic_cast<const App::PropertyLinkHidden*>(ownerProperty);
        if (ownerProperty && (!ownerLink || ownerLink->getValue())) {
            throw Base::RuntimeError("A reusable Sketch is not one root History operation");
        }
        const auto sketchPosition = historyPositions.find(sketch);
        const bool publicationPending = sketchPosition == historyPositions.end()
            && document.getBookedTransactionID() != App::NullTransaction;
        if (sketchPosition == historyPositions.end() && !publicationPending) {
            throw Base::RuntimeError("A reusable Sketch is missing from global History");
        }
        if (!sketch->isValid()) {
            throw Base::RuntimeError("A reusable Sketch is invalid");
        }

        for (const char* propertyName : {"AttachmentSupport", "ExternalGeometry"}) {
            auto* links = freecad_cast<App::PropertyLinkBase*>(
                sketch->PropertyContainer::getPropertyByName(propertyName)
            );
            if (!links) {
                continue;
            }
            std::vector<App::DocumentObject*> targets;
            links->getLinks(targets, true);
            for (auto* target : targets) {
                if (!target) {
                    continue;
                }
                if (resolveDefinitionReference(*sketch, *target) != target) {
                    throw Base::RuntimeError("A reusable Sketch retained a mutable Body "
                                             "presentation instead of its exact History state");
                }
                auto* root = timelineRoot(document, target);
                if (!root || !App::DocumentTimeline::hasTimelineOperationRole(root)) {
                    continue;
                }
                const auto targetPosition = historyPositions.find(root);
                if (targetPosition == historyPositions.end()
                    || (!publicationPending && targetPosition->second >= sketchPosition->second)) {
                    throw Base::RuntimeError("A reusable Sketch has a forward History dependency");
                }
            }
        }
    }

    std::unordered_map<std::string, App::DocumentObject*> definitions;
    for (auto* definition : document.getObjects()) {
        auto* identityProperty = definition
            ? definition->PropertyContainer::getPropertyByName("VibeCADDefinitionId")
            : nullptr;
        if (!identityProperty) {
            continue;
        }
        auto* definitionId = dynamic_cast<App::PropertyUUID*>(identityProperty);
        if (!definitionId) {
            throw Base::RuntimeError("A reusable definition has an incompatible identity property");
        }
        requireDesignIdentity(*definition, designId, "Reusable definition");
        insertUniqueIdentity(definitions, definitionId->getValueStr(), definition, "reusable definition");
        if (dynamic_cast<DesignOperationProperties*>(definition)
            || isForbiddenOperationReference(definition) || Body::findBodyOf(definition)
            || App::GeoFeatureGroupExtension::getGroupOfObject(definition)
            || App::GroupExtension::getGroupOfObject(definition)) {
            throw Base::RuntimeError("A reusable definition is not at Design scope");
        }
        const auto* ownerProperty = definition->PropertyContainer::getPropertyByName(
            App::DocumentTimeline::OwnerPropertyName
        );
        const auto* ownerLink = dynamic_cast<const App::PropertyLinkHidden*>(ownerProperty);
        if (!App::DocumentTimeline::hasTimelineOperationRole(definition)
            || (ownerProperty && (!ownerLink || ownerLink->getValue()))) {
            throw Base::RuntimeError("A reusable definition is not one root History operation");
        }
        const auto definitionPosition = historyPositions.find(definition);
        const bool publicationPending = definitionPosition == historyPositions.end()
            && document.getBookedTransactionID() != App::NullTransaction;
        if (definitionPosition == historyPositions.end() && !publicationPending) {
            throw Base::RuntimeError("A reusable definition is missing from global History");
        }
        if (!definition->isValid()) {
            throw Base::RuntimeError("A reusable definition is invalid");
        }

        std::vector<App::Property*> propertyList;
        definition->getPropertyList(propertyList);
        for (auto* property : propertyList) {
            auto* link = freecad_cast<App::PropertyLinkBase*>(property);
            if (!link) {
                continue;
            }
            std::vector<App::DocumentObject*> linked;
            link->getLinks(linked, true);
            for (auto* target : linked) {
                if (!target) {
                    continue;
                }
                if (resolveDefinitionReference(*definition, *target) != target) {
                    throw Base::RuntimeError("A reusable definition retained a mutable Body "
                                             "presentation instead of its exact History state");
                }
                auto* root = timelineRoot(document, target);
                if (!root || !App::DocumentTimeline::hasTimelineOperationRole(root)) {
                    continue;
                }
                const auto targetPosition = historyPositions.find(root);
                if (targetPosition == historyPositions.end()
                    || (!publicationPending && targetPosition->second >= definitionPosition->second)) {
                    throw Base::RuntimeError("A reusable definition has a forward History "
                                             "dependency");
                }
            }
        }
    }

    std::unordered_map<std::string, App::DocumentObject*> operations;
    std::unordered_set<Body*> participatingBodies;
    std::unordered_set<App::DocumentObject*> referencedDefinitions;
    std::unordered_map<App::DocumentObject*, std::vector<App::DocumentObject*>> operationDependencies;
    for (auto* object : document.getObjects()) {
        auto* properties = dynamic_cast<DesignOperationProperties*>(object);
        if (!properties) {
            continue;
        }
        ensureDesignOperationPortSchema(*object);

        requireDesignIdentity(*object, designId, "History operation");
        insertUniqueIdentity(operations, properties->OperationId.getValueStr(), object, "History operation");
        if (Body::findBodyOf(object) || App::GeoFeatureGroupExtension::getGroupOfObject(object)
            || App::GroupExtension::getGroupOfObject(object)) {
            throw Base::RuntimeError("A Design History operation is not at Design scope");
        }
        if (!App::DocumentTimeline::hasTimelineOperationRole(object)) {
            throw Base::RuntimeError("A Design operation is missing from global History");
        }
        const auto operationHistoryPosition = historyPositions.find(object);
        const bool historyPublicationPending = operationHistoryPosition == historyPositions.end()
            && document.getBookedTransactionID() != App::NullTransaction;
        if (operationHistoryPosition == historyPositions.end() && !historyPublicationPending) {
            throw Base::RuntimeError("A Design operation is not persisted in global History");
        }
        const auto requirePriorState = [&](DesignBodyState& state, const char* relationship) {
            auto* producer = state.Operation.getValue();
            if (!producer) {
                return;
            }
            const auto producerPosition = historyPositions.find(producer);
            if (historyPublicationPending
                || (producerPosition == historyPositions.end()
                    && document.getBookedTransactionID() != App::NullTransaction)) {
                operationDependencies[object].push_back(producer);
                return;
            }
            if (producerPosition == historyPositions.end()
                || producerPosition->second >= operationHistoryPosition->second) {
                throw Base::RuntimeError(
                    std::string("A Design operation has a forward ") + relationship + " dependency"
                );
            }
            operationDependencies[object].push_back(producer);
        };

        const std::string resultMode = properties->ResultOperation.getValueAsString();
        const bool knownResultMode = resultMode == "New Body" || resultMode == "New Bodies"
            || resultMode == "Join" || resultMode == "Cut" || resultMode == "Intersect"
            || resultMode == "Modify" || resultMode == "Split" || resultMode == "Program Outputs";
        auto* subelementProperties = dynamic_cast<DesignSubelementOperationProperties*>(object);
        if (!knownResultMode || !properties->supportsDesignResultOperation(resultMode)
            || (subelementProperties && resultMode != "Modify")) {
            throw Base::RuntimeError("A Design operation has an incompatible result-mode contract");
        }

        const auto* script = freecad_cast<const DesignScriptOperation*>(object);
        const auto inputs = properties->InputStates.getValues();
        const auto inputBodyIds = properties->InputBodyIds.getValues();
        const auto inputFrames = properties->InputFrames.getValues();
        const auto bodyIds = properties->OutputBodyIds.getValues();
        const auto frames = properties->OutputFrames.getValues();
        const auto previousInputIndices = properties->OutputPreviousInputIndices.getValues();
        const auto outputPresence = properties->OutputPresence.getValues();
        const auto outputComponentIds = properties->OutputComponentIds.getValues();
        const auto outputs = properties->OutputShapes.getValues();
        const bool newBody = resultMode == "New Body";
        const bool bodylessScript = script && bodyIds.empty();
        if (inputs.size() != inputBodyIds.size() || inputs.size() != inputFrames.size()
            || (!bodylessScript && bodyIds.empty()) || (bodylessScript && !inputs.empty())
            || frames.size() != bodyIds.size() || previousInputIndices.size() != bodyIds.size()
            || outputPresence.size() != bodyIds.size() || outputComponentIds.size() != bodyIds.size()
            || outputs.size() != bodyIds.size() || properties->TargetBodyIds.getValues() != bodyIds
            || properties->TargetFrames.getValues() != frames
            || (newBody
                && (bodyIds.size() != 1 || !inputs.empty() || previousInputIndices.front() != -1))) {
            throw Base::RuntimeError(
                "A Design operation has inconsistent persistent input or "
                "output ports"
            );
        }

        if (subelementProperties) {
            const auto groups = subelementProperties->targetElementGroups();
            const auto* useAllEdges = dynamic_cast<App::PropertyBool*>(
                object->PropertyContainer::getPropertyByName("UseAllEdges")
            );
            const bool acceptsEmptyGroups = useAllEdges && useAllEdges->getValue();
            if (groups.size() != bodyIds.size()
                || (!acceptsEmptyGroups && std::ranges::any_of(groups, [](const auto& group) {
                       return group.empty();
                   }))) {
                throw Base::RuntimeError(
                    "A Design subelement operation has incomplete target selections"
                );
            }
        }

        if (script) {
            const auto programKeys = script->ProgramOutputKeys.getValues();
            const auto programTypes = script->ProgramOutputTypes.getValues();
            const auto keys = script->ScriptOutputKeys.getValues();
            const auto labels = script->ScriptOutputLabels.getValues();
            const auto accepted = script->AcceptedShapes.getValues();
            std::unordered_map<std::string, std::string> publishedOutputs;
            std::unordered_set<std::string> uniqueKeys;
            auto* programObject = document.getObject(script->ProgramObjectName.getValue());
            if (resultMode != "Program Outputs" || script->ProgramId.getStrValue().empty()
                || script->ProgramRevision.getStrValue().empty() || !programObject
                || programKeys.empty() || programTypes.size() != programKeys.size()
                || keys.size() != bodyIds.size() || labels.size() != keys.size()
                || accepted.size() != keys.size()) {
                throw Base::RuntimeError("A VibeScript History operation has inconsistent program "
                                         "or output metadata");
            }
            for (std::size_t index = 0; index < programKeys.size(); ++index) {
                if (programKeys[index].empty() || programTypes[index].empty()
                    || !publishedOutputs.emplace(programKeys[index], programTypes[index]).second) {
                    throw Base::RuntimeError("Every published VibeScript output requires one "
                                             "distinct key and topology type");
                }
            }
            for (std::size_t index = 0; index < keys.size(); ++index) {
                const auto& shape = accepted[index];
                if (keys[index].empty() || labels[index].empty()
                    || !uniqueKeys.insert(keys[index]).second
                    || !publishedOutputs.contains(keys[index])
                    || publishedOutputs[keys[index]] != "solid") {
                    throw Base::RuntimeError("Every VibeScript History output requires one "
                                             "distinct key, label, and solid-bearing shape");
                }
                const auto body = bodies.find(bodyIds[index]);
                if (body == bodies.end()) {
                    throw Base::RuntimeError("A VibeScript History output lost its Body");
                }
                requireSavedBodyShape(*body->second, shape, "The saved VibeScript output");
            }
        }

        if (const auto* generated = freecad_cast<const DesignGeneratedOperation*>(object)) {
            auto* generator = freecad_cast<Part::Feature*>(generated->Generator.getValue());
            const auto* generatorRole = generator
                ? dynamic_cast<const App::PropertyString*>(
                      generator->PropertyContainer::getPropertyByName(
                          App::DocumentTimeline::RolePropertyName
                      )
                  )
                : nullptr;
            const auto generatorPosition = generator ? historyPositions.find(generator)
                                                     : historyPositions.end();
            const auto generatorConsumers = generator ? nonTimelineConsumers(*generator)
                                                      : std::vector<App::DocumentObject*> {};
            const auto& generatedShape = generator ? generator->Shape.getShape()
                                                   : Part::TopoShape();
            const bool createsBody = resultMode == "New Body" && inputs.empty()
                && inputBodyIds.empty() && inputFrames.empty() && bodyIds.size() == 1
                && previousInputIndices.size() == 1 && previousInputIndices.front() == -1;
            const bool modifiesBody = resultMode == "Modify" && inputs.size() == 1
                && inputBodyIds.size() == 1 && inputFrames.size() == 1
                && bodyIds.size() == 1 && frames.size() == 1
                && inputBodyIds.front() == bodyIds.front()
                && inputFrames.front() == frames.front()
                && previousInputIndices.size() == 1 && previousInputIndices.front() == 0
                && outputComponentIds.size() == 1 && outputComponentIds.front().empty();
            if ((!createsBody && !modifiesBody) || generated->GeneratorKind.getStrValue().empty()
                || generated->OutputLabel.getStrValue().empty() || !generator || generator == object
                || generator->getDocument() != &document || Body::findBodyOf(generator)
                || App::GeoFeatureGroupExtension::getGroupOfObject(generator)
                || App::GroupExtension::getGroupOfObject(generator) || !generatorRole
                || std::string_view(generatorRole->getValue()) != App::DocumentTimeline::InternalRole
                || (generatorPosition != historyPositions.end() && !historyPublicationPending
                    && generatorPosition->second >= operationHistoryPosition->second)
                || generatorConsumers.size() != 1 || generatorConsumers.front() != object
                || generatedShape.isNull() || !generatedShape.hasSubShape(TopAbs_SOLID)) {
                throw Base::RuntimeError(
                    "A generated Design operation has an inconsistent internal generator, "
                    "label, or History position"
                );
            }

            std::vector<App::Property*> generatorProperties;
            generator->getPropertyList(generatorProperties);
            for (auto* property : generatorProperties) {
                auto* link = freecad_cast<App::PropertyLinkBase*>(property);
                if (!link) {
                    continue;
                }
                std::vector<App::DocumentObject*> linked;
                link->getLinks(linked, true);
                for (auto* target : linked) {
                    if (!target) {
                        continue;
                    }
                    const char* propertyName = generator->getPropertyName(property);
                    std::string invalidReason;
                    if (target == object) {
                        invalidReason = "the owning operation";
                    }
                    else if (target == generator) {
                        invalidReason = "itself";
                    }
                    else if (target->getDocument() != &document) {
                        invalidReason = "an object in another document";
                    }
                    else if (isForbiddenOperationReference(target)) {
                        invalidReason = "a rendered or structural container";
                    }
                    else if (dynamic_cast<DesignOperationProperties*>(target)) {
                        invalidReason = "another Design operation";
                    }
                    if (!invalidReason.empty()) {
                        throw Base::RuntimeError(
                            std::string("Generated feature '") + generator->getNameInDocument()
                            + "' property '" + (propertyName ? propertyName : "<unknown>")
                            + "' references '"
                            + (target->getNameInDocument() ? target->getNameInDocument() : "<unnamed>")
                            + "', which is " + invalidReason
                        );
                    }
                    if (auto* state = freecad_cast<DesignBodyState*>(target)) {
                        requirePriorState(*state, "generator");
                    }
                }
            }
        }

        if (const auto* clone = freecad_cast<const DesignClone*>(object)) {
            const auto sourceBody = inputBodyIds.size() == 1 ? bodies.find(inputBodyIds.front())
                                                             : bodies.end();
            const auto outputBody = bodyIds.size() == 1 ? bodies.find(bodyIds.front()) : bodies.end();
            const bool suppressed = clone->Suppressed.getValue();
            if (resultMode != "New Bodies" || inputs.size() != 1 || sourceBody == bodies.end()
                || outputBody == bodies.end() || bodyIds.front() == inputBodyIds.front()
                || previousInputIndices.size() != 1 || previousInputIndices.front() != -1
                || inputFrames.size() != 1 || frames.size() != 1
                || inputFrames.front() != frames.front() || outputComponentIds.size() != 1
                || outputComponentIds.front() != sourceBody->second->ComponentId.getValue()
                || outputPresence.size() != 1 || outputPresence[0] == suppressed) {
                throw Base::RuntimeError("A Design Clone has an inconsistent exact source, "
                                         "created Body, frame, Component, or presence contract");
            }
        }

        if (auto* draft = freecad_cast<DesignDraft*>(object)) {
            if (draft->NeutralPlane.getScope() != App::LinkScope::Global
                || draft->PullDirection.getScope() != App::LinkScope::Global) {
                throw Base::RuntimeError("A Design Draft reference is not Design-global");
            }
            const auto validateReference = [&](const App::PropertyLinkSub& reference,
                                               const App::PropertyPlacement& savedFrame,
                                               const char* relationship) {
                auto* target = reference.getValue();
                const auto subelements = reference.getSubValues();
                if (!target) {
                    if (!subelements.empty() || savedFrame.getValue() != Base::Placement()) {
                        throw Base::RuntimeError(
                            std::string("An empty Draft ") + relationship
                            + " retained stale reference data"
                        );
                    }
                    return;
                }
                if (target == object || target->getDocument() != &document
                    || isForbiddenOperationReference(target)
                    || dynamic_cast<DesignOperationProperties*>(target)) {
                    throw Base::RuntimeError(
                        std::string("A Draft ") + relationship + " is not an exact modeling definition"
                    );
                }

                Body* referenceBody = nullptr;
                if (auto* state = freecad_cast<DesignBodyState*>(target)) {
                    referenceBody = DesignModel::bodyWithId(document, state->BodyId.getValueStr());
                    requirePriorState(*state, relationship);
                }
                else {
                    referenceBody = Body::findBodyOf(target);
                }
                if (referenceBody && designBodyStateBefore(referenceBody, object) != target) {
                    throw Base::RuntimeError(
                        std::string("A Draft ") + relationship
                        + " does not reference the exact prior Body state"
                    );
                }
            };
            validateReference(draft->NeutralPlane, draft->NeutralPlaneFrame, "neutral-plane");
            validateReference(draft->PullDirection, draft->PullDirectionFrame, "pull-direction");
        }

        if (auto* pattern = dynamic_cast<DesignPatternProperties*>(object)) {
            const std::string sourceMode = pattern->PatternSource.getValueAsString();
            auto* sourceOperation = pattern->SourceOperation.getValue();
            const auto* patternFeature = freecad_cast<const Feature*>(object);
            const bool suppressed = patternFeature && patternFeature->Suppressed.getValue();

            const App::PropertyLinkSub* geometricReference = nullptr;
            const App::PropertyPlacement* geometricReferenceFrame = nullptr;
            const char* geometricRelationship = nullptr;
            if (const auto* mirror = freecad_cast<const DesignMirror*>(object)) {
                geometricReference = &mirror->PlaneReference;
                geometricReferenceFrame = &mirror->PlaneReferenceFrame;
                geometricRelationship = "mirror-plane";
            }
            else if (const auto* linear = freecad_cast<const DesignLinearPattern*>(object)) {
                geometricReference = &linear->DirectionReference;
                geometricReferenceFrame = &linear->DirectionReferenceFrame;
                geometricRelationship = "linear-direction";
            }
            else if (const auto* circular = freecad_cast<const DesignCircularPattern*>(object)) {
                geometricReference = &circular->AxisReference;
                geometricReferenceFrame = &circular->AxisReferenceFrame;
                geometricRelationship = "rotation-axis";
            }
            else {
                throw Base::RuntimeError("An unknown Design Pattern type is present in History");
            }

            if (pattern->SourceOperation.getScope() != App::LinkScope::Global
                || geometricReference->getScope() != App::LinkScope::Global) {
                throw Base::RuntimeError("A Design Pattern source or geometric reference is not "
                                         "Design-global");
            }
            const auto referenceSubelements = geometricReference->getSubValues();
            auto* referenceObject = geometricReference->getValue();
            if (!referenceObject) {
                if (!referenceSubelements.empty()
                    || geometricReferenceFrame->getValue() != Base::Placement()) {
                    throw Base::RuntimeError(
                        std::string("An empty Pattern ") + geometricRelationship
                        + " retained stale reference data"
                    );
                }
            }
            else {
                if (referenceSubelements.size() > 1 || referenceObject == object
                    || referenceObject->getDocument() != &document
                    || isForbiddenOperationReference(referenceObject)
                    || dynamic_cast<DesignOperationProperties*>(referenceObject)) {
                    throw Base::RuntimeError(
                        std::string("A Pattern ") + geometricRelationship
                        + " is not one exact modeling definition"
                    );
                }

                Body* referenceBody = nullptr;
                if (auto* state = freecad_cast<DesignBodyState*>(referenceObject)) {
                    referenceBody = DesignModel::bodyWithId(document, state->BodyId.getValueStr());
                    requirePriorState(*state, geometricRelationship);
                }
                else {
                    referenceBody = Body::findBodyOf(referenceObject);
                }
                if (referenceBody && designBodyStateBefore(referenceBody, object) != referenceObject) {
                    throw Base::RuntimeError(
                        std::string("A Pattern ") + geometricRelationship
                        + " does not reference the exact prior Body state"
                    );
                }
            }

            std::size_t expectedGenerated = 0;
            if (freecad_cast<DesignMirror*>(object)) {
                expectedGenerated = 1;
            }
            else if (const auto* linear = freecad_cast<DesignLinearPattern*>(object)) {
                if (linear->Occurrences.getValue() >= 2) {
                    expectedGenerated = static_cast<std::size_t>(linear->Occurrences.getValue() - 1);
                }
            }
            else if (const auto* circular = freecad_cast<DesignCircularPattern*>(object)) {
                if (circular->Occurrences.getValue() >= 2) {
                    expectedGenerated = static_cast<std::size_t>(circular->Occurrences.getValue() - 1);
                }
            }
            if (expectedGenerated == 0) {
                throw Base::RuntimeError("A Design Pattern has no generated occurrences");
            }

            if (sourceMode == "Body") {
                const auto* sourceState = inputs.size() == 1
                    ? freecad_cast<const Part::Feature*>(inputs.front())
                    : nullptr;
                const auto* designState = freecad_cast<const DesignBodyState*>(sourceState);
                const auto sourceBody = inputBodyIds.size() == 1 ? bodies.find(inputBodyIds.front())
                                                                 : bodies.end();
                if (sourceOperation || resultMode != "New Bodies" || !sourceState
                    || sourceBody == bodies.end() || (designState && !designState->Present.getValue())
                    || bodyIds.size() != expectedGenerated) {
                    throw Base::RuntimeError("A Body Pattern has an inconsistent exact source or "
                                             "generated output contract");
                }
                for (std::size_t index = 0; index < bodyIds.size(); ++index) {
                    if (previousInputIndices[index] != -1 || frames[index] != inputFrames.front()
                        || outputComponentIds[index] != sourceBody->second->ComponentId.getValue()
                        || outputPresence[index] == suppressed) {
                        throw Base::RuntimeError("A Body Pattern occurrence does not preserve its "
                                                 "created identity, source frame, Component, or "
                                                 "suppression state");
                    }
                }
            }
            else if (sourceMode == "Feature") {
                auto* sourceProperties = dynamic_cast<DesignOperationProperties*>(sourceOperation);
                auto* sourceFeature = freecad_cast<FeatureAddSub*>(sourceOperation);
                if (!sourceProperties || !sourceFeature || sourceOperation == object
                    || sourceOperation->getDocument() != &document) {
                    throw Base::RuntimeError("A Feature Pattern has no valid earlier reusable "
                                             "Design feature");
                }
                const auto sourcePosition = historyPositions.find(sourceOperation);
                if (sourcePosition == historyPositions.end()
                    || (!historyPublicationPending
                        && sourcePosition->second >= operationHistoryPosition->second)) {
                    throw Base::RuntimeError("A Feature Pattern source does not precede it in "
                                             "global History");
                }
                operationDependencies[object].push_back(sourceOperation);

                const std::string_view sourceResult
                    = sourceProperties->ResultOperation.getValueAsString();
                const std::string_view expectedResult = sourceResult == "Cut" ? "Cut"
                    : (sourceResult == "New Body" || sourceResult == "Join")  ? "Join"
                                                                              : std::string_view();
                if (expectedResult.empty() || resultMode != expectedResult || inputs.empty()
                    || inputs.size() != bodyIds.size()) {
                    throw Base::RuntimeError("A Feature Pattern does not preserve its source "
                                             "feature's additive or subtractive semantic");
                }
                for (std::size_t index = 0; index < bodyIds.size(); ++index) {
                    if (previousInputIndices[index] != static_cast<long>(index)
                        || inputBodyIds[index] != bodyIds[index]
                        || inputFrames[index] != frames[index] || !outputComponentIds[index].empty()
                        || !outputPresence[index]) {
                        throw Base::RuntimeError("A Feature Pattern output does not advance its "
                                                 "matching exact Body input");
                    }
                }
            }
            else {
                throw Base::RuntimeError("A Design Pattern source is neither Feature nor Body");
            }
        }

        if (const auto* combine = freecad_cast<const DesignCombine*>(object)) {
            const std::size_t expectedOutputs = combine->KeepTools.getValue() ? 1 : inputs.size();
            const bool suppressed = combine->Suppressed.getValue();
            if (inputs.size() < 2 || combine->ResultBodyId.getValueStr().empty()
                || inputBodyIds.front() != combine->ResultBodyId.getValueStr()
                || (resultMode != "Join" && resultMode != "Cut" && resultMode != "Intersect")
                || bodyIds.size() != expectedOutputs) {
                throw Base::RuntimeError("A Design Combine has an inconsistent result Body, "
                                         "operation, or tool contract");
            }
            for (std::size_t index = 0; index < expectedOutputs; ++index) {
                const bool expectedPresence = index == 0 || suppressed;
                if (bodyIds[index] != inputBodyIds[index] || frames[index] != inputFrames[index]
                    || previousInputIndices[index] != static_cast<long>(index)
                    || !outputComponentIds[index].empty()
                    || outputPresence[index] != expectedPresence) {
                    throw Base::RuntimeError("A Design Combine output does not advance the exact "
                                             "saved state of its declared Body");
                }
            }
            for (std::size_t index = 0; index < inputs.size(); ++index) {
                auto* input = inputs[index];
                const auto* feature = freecad_cast<const Part::Feature*>(input);
                const auto* state = freecad_cast<const DesignBodyState*>(input);
                const auto body = index < inputBodyIds.size()
                    ? bodies.find(inputBodyIds[index])
                    : bodies.end();
                if (!feature || (state && !state->Present.getValue()) || body == bodies.end()) {
                    throw Base::RuntimeError(
                        "A Design Combine input is not one exact present Body state"
                    );
                }
                requireSavedBodyShape(
                    *body->second,
                    feature->Shape.getShape(),
                    "The Design Combine input"
                );
            }
        }

        if (const auto* split = freecad_cast<const DesignSplit*>(object)) {
            const auto splitterReferences = split->Splitters.getSubListValues();
            const auto splitterFrames = split->SplitterFrames.getValues();
            const auto witnesses = split->RegionWitnesses.getValues();
            const std::string sourceBodyId = split->SourceBodyId.getValueStr();
            const auto sourceBody = bodies.find(sourceBodyId);
            const bool suppressed = split->Suppressed.getValue();
            if (resultMode != "Split" || sourceBody == bodies.end() || inputs.empty()
                || inputBodyIds.empty() || inputBodyIds.front() != sourceBodyId
                || splitterReferences.empty() || splitterReferences.size() != splitterFrames.size()
                || !split->RetainedRegionChosen.getValue() || bodyIds.size() < 2
                || witnesses.size() != bodyIds.size()) {
                throw Base::RuntimeError("A Design Split has an inconsistent source, definition, "
                                         "or region-identity contract");
            }

            for (const auto& reference : splitterReferences) {
                auto* feature = freecad_cast<Part::Feature*>(reference.first);
                if (!feature || feature == object || feature->getDocument() != &document
                    || freecad_cast<DesignBodyPublication*>(feature)) {
                    throw Base::RuntimeError(
                        "A Design Split contains an invalid modeling definition"
                    );
                }
                if (const auto* state = freecad_cast<const DesignBodyState*>(feature)) {
                    const auto input = std::ranges::find(inputs, const_cast<DesignBodyState*>(state));
                    if (input == inputs.end() || state->BodyId.getValueStr() == sourceBodyId) {
                        throw Base::RuntimeError("A Body-backed Split definition is not one of its "
                                                 "saved exact input states");
                    }
                }
            }

            const auto* sourceState = freecad_cast<const DesignBodyState*>(inputs.front());
            const bool sourcePresent = !sourceState || sourceState->Present.getValue();
            for (std::size_t index = 0; index < bodyIds.size(); ++index) {
                const bool expectedPresence = !suppressed || (index == 0 && sourcePresent);
                const bool sourceOutput = index == 0;
                if ((sourceOutput
                     && (bodyIds[index] != sourceBodyId || previousInputIndices[index] != 0
                         || !outputComponentIds[index].empty()))
                    || (!sourceOutput
                        && (previousInputIndices[index] != -1
                            || outputComponentIds[index] != sourceBody->second->ComponentId.getValue()))
                    || frames[index] != inputFrames.front()
                    || outputPresence[index] != expectedPresence) {
                    throw Base::RuntimeError("A Design Split output does not preserve its explicit "
                                             "Body identity, source frame, or presence state");
                }
            }
        }

        if (const auto* separate = freecad_cast<const DesignSeparate*>(object)) {
            auto* source = freecad_cast<Part::Feature*>(separate->Source.getValue());
            const auto witnesses = separate->RegionWitnesses.getValues();
            const bool suppressed = separate->Suppressed.getValue();
            if (resultMode != "New Bodies" || !inputs.empty() || !inputBodyIds.empty()
                || !inputFrames.empty() || !source || source == object
                || source->getDocument() != &document || freecad_cast<DesignBodyState*>(source)
                || freecad_cast<DesignBodyPublication*>(source)
                || App::GeoFeatureGroupExtension::getGroupOfObject(source)
                || App::GroupExtension::getGroupOfObject(source) || bodyIds.size() < 2
                || witnesses.size() != bodyIds.size()) {
                throw Base::RuntimeError("A Design Separate has an inconsistent root source or "
                                         "solid-identity contract");
            }
            for (std::size_t index = 0; index < bodyIds.size(); ++index) {
                if (previousInputIndices[index] != -1 || outputPresence[index] == suppressed) {
                    throw Base::RuntimeError("A Design Separate output does not preserve one "
                                             "created Body and one distinct solid identity");
                }
                for (std::size_t previous = 0; previous < index; ++previous) {
                    if (witnesses[previous] == witnesses[index]) {
                        throw Base::RuntimeError("A Design Separate contains duplicate solid "
                                                 "identity points");
                    }
                }
            }
        }

        std::size_t createdOutputCount = 0;
        std::string singleCreatedDestination;
        std::unordered_set<std::string> uniqueOutputs;
        for (std::size_t index = 0; index < bodyIds.size(); ++index) {
            const auto body = bodies.find(bodyIds[index]);
            if (body == bodies.end() || !uniqueOutputs.insert(bodyIds[index]).second) {
                throw Base::RuntimeError("A Design operation has a missing or duplicate output Body");
            }
            participatingBodies.insert(body->second);

            const bool present = outputPresence[index];
            const auto& output = outputs[index];
            if (present) {
                requireSavedBodyShape(*body->second, output, "The Design operation output");
            }
            if (!present && !output.isNull()) {
                throw Base::RuntimeError("A Design operation's output presence and solid geometry "
                                         "disagree");
            }

            const long previousInputIndex = previousInputIndices[index];
            if (previousInputIndex == -1) {
                ++createdOutputCount;
                singleCreatedDestination = outputComponentIds[index];
                if (!outputComponentIds[index].empty()
                    && !components.contains(outputComponentIds[index])) {
                    throw Base::RuntimeError("An operation-created Body has an invalid destination "
                                             "Component");
                }
                continue;
            }
            if (previousInputIndex < 0
                || static_cast<std::size_t>(previousInputIndex) >= inputs.size()
                || inputBodyIds[static_cast<std::size_t>(previousInputIndex)] != bodyIds[index]
                || inputFrames[static_cast<std::size_t>(previousInputIndex)] != frames[index]
                || !outputComponentIds[index].empty()) {
                throw Base::RuntimeError("A Design output has an invalid exact prior-state port");
            }
        }
        const std::string expectedCompatibilityDestination = createdOutputCount == 1
            ? singleCreatedDestination
            : std::string();
        if (properties->DestinationComponentId.getValue() != expectedCompatibilityDestination) {
            throw Base::RuntimeError("A Design operation's compatibility destination does not "
                                     "match its created output ports");
        }

        std::unordered_set<App::DocumentObject*> uniqueInputs;
        std::unordered_set<std::string> uniqueInputBodies;
        for (std::size_t index = 0; index < inputs.size(); ++index) {
            auto* input = inputs[index];
            auto* feature = freecad_cast<Part::Feature*>(input);
            const auto body = bodies.find(inputBodyIds[index]);
            if (!feature || feature->getDocument() != &document
                || freecad_cast<DesignBodyPublication*>(input) || body == bodies.end()
                || !uniqueInputs.insert(input).second
                || !uniqueInputBodies.insert(inputBodyIds[index]).second) {
                throw Base::RuntimeError("A Design operation has an invalid exact input state");
            }
            const auto* designState = freecad_cast<const DesignBodyState*>(feature);
            if ((designState && designState->BodyId.getValueStr() != inputBodyIds[index])
                || (!designState && !body->second->hasObject(feature))) {
                throw Base::RuntimeError("A Design input state does not belong to its declared Body");
            }
            participatingBodies.insert(body->second);
            if (auto* state = freecad_cast<DesignBodyState*>(input);
                state && state->Operation.getValue()) {
                requirePriorState(*state, "Body-state input");
            }
        }

        std::vector<App::Property*> propertyList;
        object->getPropertyList(propertyList);
        for (auto* property : propertyList) {
            auto* link = freecad_cast<App::PropertyLinkBase*>(property);
            if (!link) {
                continue;
            }
            std::vector<App::DocumentObject*> linked;
            link->getLinks(linked, true);
            for (auto* target : linked) {
                if (isForbiddenOperationReference(target)) {
                    throw Base::RuntimeError("A Design operation references a container, "
                                             "publication, or assembly occurrence");
                }
                const bool reusableSketch = target
                    && target->PropertyContainer::getPropertyByName("VibeCADSketchId");
                const bool reusableDefinition = target
                    && target->PropertyContainer::getPropertyByName("VibeCADDefinitionId");
                if (reusableSketch || reusableDefinition) {
                    referencedDefinitions.insert(target);
                    auto* root = timelineRoot(document, target);
                    const auto targetPosition = root ? historyPositions.find(root)
                                                     : historyPositions.end();
                    if (!root) {
                        throw Base::RuntimeError(
                            std::string("Design operation '") + object->getNameInDocument()
                            + "' references reusable definition '"
                            + (target ? target->getNameInDocument() : "<missing>")
                            + "', but that definition has no History root"
                        );
                    }
                    if (!App::DocumentTimeline::hasTimelineOperationRole(root)) {
                        throw Base::RuntimeError(
                            std::string("Design operation '") + object->getNameInDocument()
                            + "' references reusable definition '"
                            + (target ? target->getNameInDocument() : "<missing>")
                            + "' at History position "
                            + (targetPosition != historyPositions.end()
                                   ? std::to_string(targetPosition->second)
                                   : std::string("<missing>"))
                            + ", but its root lacks History operation classification"
                        );
                    }
                    if (targetPosition == historyPositions.end()) {
                        throw Base::RuntimeError(
                            std::string("Design operation '") + object->getNameInDocument()
                            + "' references reusable definition '"
                            + (target ? target->getNameInDocument() : "<missing>")
                            + "', but its root is absent from global History"
                        );
                    }
                    if (!historyPublicationPending
                        && targetPosition->second >= operationHistoryPosition->second) {
                        throw Base::RuntimeError(
                            std::string("Design operation '") + object->getNameInDocument()
                            + "' references reusable definition '"
                            + (target ? target->getNameInDocument() : "<missing>")
                            + "' at History position "
                            + std::to_string(targetPosition->second)
                            + ", not before operation position "
                            + (operationHistoryPosition != historyPositions.end()
                                   ? std::to_string(operationHistoryPosition->second)
                                   : std::string("<pending>"))
                        );
                    }
                }
            }
        }
    }

    for (auto* definition : referencedDefinitions) {
        requireDesignIdentity(
            *definition,
            designId,
            definition->PropertyContainer::getPropertyByName("VibeCADDefinitionId")
                ? "Reusable definition"
                : "Sketch"
        );
    }

    enum class Visit
    {
        None,
        Active,
        Complete,
    };
    std::unordered_map<App::DocumentObject*, Visit> visits;
    std::function<void(App::DocumentObject*)> visitOperation = [&](App::DocumentObject* operation) {
        auto& visit = visits[operation];
        if (visit == Visit::Active) {
            throw Base::RuntimeError("The Design History operation graph contains a cycle");
        }
        if (visit == Visit::Complete) {
            return;
        }
        visit = Visit::Active;
        for (auto* dependency : operationDependencies[operation]) {
            if (!dependency || !dynamic_cast<DesignOperationProperties*>(dependency)) {
                throw Base::RuntimeError("A Body state references an invalid producing operation");
            }
            visitOperation(dependency);
        }
        visit = Visit::Complete;
    };
    for (const auto& [operationId, operation] : operations) {
        (void)operationId;
        visitOperation(operation);
    }

    std::unordered_map<std::string, DesignBodyState*> states;
    std::unordered_map<App::DocumentObject*, DesignBodyState*> successors;
    std::unordered_map<App::DocumentObject*, std::size_t> stateCounts;
    for (auto* state : document.getObjectsOfType<DesignBodyState>()) {
        requireDesignIdentity(*state, designId, "Body state");
        insertUniqueIdentity(states, state->BodyStateId.getValueStr(), state, "Body state");
        if (Body::findBodyOf(state) || App::GeoFeatureGroupExtension::getGroupOfObject(state)
            || App::GroupExtension::getGroupOfObject(state)) {
            throw Base::RuntimeError("A Design Body state is not at Design scope");
        }
        const auto body = bodies.find(state->BodyId.getValueStr());
        if (body == bodies.end()) {
            throw Base::RuntimeError("A Body state has no persistent Body");
        }
        participatingBodies.insert(body->second);

        auto* operation = state->Operation.getValue();
        if (!operation) {
            const auto& shape = state->Shape.getShape();
            if (state->PreviousState.getValue() || !state->Present.getValue()) {
                throw Base::RuntimeError("An initial Body state must be present with no previous "
                                         "state");
            }
            requireSavedBodyShape(*body->second, shape, "The initial Body state");
            continue;
        }
        auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
        if (!properties || state->OperationId.getValueStr() != properties->OperationId.getValueStr()
            || !App::DocumentTimeline::isTimelineResourceOwnedBy(state, operation)) {
            throw Base::RuntimeError("A Body state does not match its producing History operation");
        }
        const int index = state->OutputIndex.getValue();
        const auto outputBodyIds = properties->OutputBodyIds.getValues();
        const auto outputPresence = properties->OutputPresence.getValues();
        const auto outputShapes = properties->OutputShapes.getValues();
        const auto previousInputIndices = properties->OutputPreviousInputIndices.getValues();
        const auto inputs = properties->InputStates.getValues();
        const auto inputBodyIds = properties->InputBodyIds.getValues();
        if (index < 0 || static_cast<std::size_t>(index) >= outputBodyIds.size()
            || outputPresence.size() != outputBodyIds.size()
            || outputShapes.size() != outputBodyIds.size()
            || previousInputIndices.size() != outputBodyIds.size()
            || outputBodyIds[static_cast<std::size_t>(index)] != state->BodyId.getValueStr()) {
            throw Base::RuntimeError("A Body state does not match its operation output slot");
        }
        const auto outputIndex = static_cast<std::size_t>(index);
        const long previousInputIndex = previousInputIndices[outputIndex];
        if ((previousInputIndex == -1 && state->PreviousState.getValue())
            || (previousInputIndex != -1
                && (previousInputIndex < 0
                    || static_cast<std::size_t>(previousInputIndex) >= inputs.size()
                    || static_cast<std::size_t>(previousInputIndex) >= inputBodyIds.size()
                    || inputBodyIds[static_cast<std::size_t>(previousInputIndex)]
                        != state->BodyId.getValueStr()
                    || state->PreviousState.getValue()
                        != inputs[static_cast<std::size_t>(previousInputIndex)]))) {
            throw Base::RuntimeError("A Body state does not match its output port's exact prior "
                                     "state");
        }
        const bool present = outputPresence[outputIndex];
        const auto& stateShape = state->Shape.getShape();
        if (state->Present.getValue() != present || (!present && !stateShape.isNull())) {
            throw Base::RuntimeError("A Body state's presence and solid geometry do not match its "
                                     "operation output");
        }
        if (present) {
            requireSavedBodyShape(*body->second, stateShape, "The saved Body state");
        }
        ++stateCounts[operation];
        if (auto* previous = state->PreviousState.getValue();
            previous && !successors.emplace(previous, state).second) {
            throw Base::RuntimeError("A Body history branches from one exact state");
        }
    }
    for (const auto& [operationId, operation] : operations) {
        (void)operationId;
        auto* properties = dynamic_cast<DesignOperationProperties*>(operation);
        if (stateCounts[operation] != static_cast<std::size_t>(properties->OutputBodyIds.getSize())) {
            throw Base::RuntimeError("A Design operation does not own one Body state per output");
        }
    }

    for (auto* body : participatingBodies) {
        requireDesignIdentity(*body, designId, "Body");
        const std::string componentId = body->ComponentId.getValue();
        const auto* parent = App::Part::getPartOfObject(body);
        const auto* component = freecad_cast<const Component*>(parent);
        const std::string actualComponentId = component ? component->ComponentId.getValueStr()
                                                        : std::string();
        if (componentId != actualComponentId) {
            throw Base::RuntimeError("A Body's persistent Component membership disagrees with "
                                     "its Component container");
        }

        auto* publication = findDesignBodyPublication(body);
        if (!publication) {
            throw Base::RuntimeError("A Design Body does not have exactly one stable publication");
        }
        const auto bodyMembers = body->Group.getValues();
        if (bodyMembers.size() != 1 || bodyMembers.front() != publication
            || body->Tip.getValue() != publication || Body::findBodyOf(publication) != body
            || publication->BaseFeature.getValue()) {
            throw Base::RuntimeError(
                "A Design Body does not contain exactly its stable publication as the Tip"
            );
        }
        requireDesignIdentity(*publication, designId, "Body publication");
        auto* current = freecad_cast<Part::Feature*>(publication->CurrentState.getValue());
        if (!current || publication->BodyId.getValueStr() != body->VibeCADBodyId.getValueStr()
            || successors.contains(current)) {
            throw Base::RuntimeError("A Body publication does not point to the unique History tip");
        }
        const auto* publishedState = freecad_cast<const DesignBodyState*>(current);
        if ((publishedState
             && publication->BodyStateId.getValueStr() != publishedState->BodyStateId.getValueStr())
            || (!publishedState && publication->BodyStateId.getValueStr() != NoDesignBodyStateId)) {
            throw Base::RuntimeError("A Body publication's persistent state identity does not "
                                     "match its History tip");
        }

        std::unordered_set<App::DocumentObject*> visitedStates;
        while (auto* state = freecad_cast<DesignBodyState*>(current)) {
            if (state->BodyId.getValueStr() != body->VibeCADBodyId.getValueStr()
                || !visitedStates.insert(state).second) {
                throw Base::RuntimeError("A Body publication has a cyclic or cross-Body state chain");
            }
            current = freecad_cast<Part::Feature*>(state->PreviousState.getValue());
        }
    }
}
