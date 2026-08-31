// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ModelTreeBrowser.h"

#include <algorithm>
#include <cstddef>
#include <limits>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <App/Datums.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/DocumentObject.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/GroupExtension.h>
#include <App/Origin.h>
#include <App/OriginGroupExtension.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>


using namespace Gui;

namespace
{

bool isDerivedFrom(const App::DocumentObject* object, std::string_view typeName)
{
    if (!object) {
        return false;
    }
    const Base::Type type = Base::Type::fromName(typeName);
    return !type.isBad() && object->getTypeId().isDerivedFrom(type);
}

bool hasTypeNameFragment(const App::DocumentObject* object, std::string_view fragment)
{
    if (!object) {
        return false;
    }
    return object->getTypeId().getName().find(fragment) != std::string_view::npos;
}

bool isLink(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "App::Link");
}

bool isParameterObject(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "App::VarSet")
        || isDerivedFrom(object, "Spreadsheet::Sheet");
}

bool isSketch(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "Sketcher::SketchObject");
}

bool isReferenceGeometry(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "Part::Datum")
        || isDerivedFrom(object, "PartDesign::CoordinateSystem")
        || isDerivedFrom(object, "PartDesign::Plane")
        || isDerivedFrom(object, "PartDesign::Line")
        || isDerivedFrom(object, "PartDesign::Point");
}

bool isReference(const App::DocumentObject* object)
{
    return isLink(object) || hasTypeNameFragment(object, "ShapeBinder")
        || hasTypeNameFragment(object, "SubShapeBinder")
        || hasTypeNameFragment(object, "Reference");
}

bool hasGeometry(const App::DocumentObject* object)
{
    return object
        && (object->getPropertyByName("Shape") || object->getPropertyByName("Mesh")
            || object->getPropertyByName("Points"));
}

std::string stringProperty(const App::DocumentObject* object, const char* name)
{
    if (!object) {
        return {};
    }
    const auto* property =
        dynamic_cast<const App::PropertyString*>(object->getPropertyByName(name));
    return property ? property->getStrValue() : std::string();
}

std::string timelineRole(const App::DocumentObject* object)
{
    return stringProperty(object, App::DocumentTimeline::RolePropertyName);
}

std::string vibeScriptOutputType(const App::DocumentObject* object)
{
    return stringProperty(object, "VibeCADVibeScriptOutputType");
}

std::string scriptedOutputIdentity(
    const App::DocumentObject* object,
    std::string_view role
)
{
    if (stringProperty(object, "VibeCADScriptedRole") != role
        || stringProperty(object, "VibeCADScriptedEngine")
            != "vibescript:partdesign") {
        return {};
    }
    const std::string modelId =
        stringProperty(object, "VibeCADScriptedModelId");
    const std::string outputKey =
        stringProperty(object, "VibeCADScriptedOutputKey");
    if (modelId.empty() || outputKey.empty()) {
        return {};
    }
    return std::to_string(modelId.size()) + ":" + modelId + outputKey;
}

App::DocumentObject* geoParent(const App::DocumentObject* object)
{
    return App::GeoFeatureGroupExtension::getGroupOfObject(object);
}

App::DocumentObject* exactVibeScriptProgramFor(
    App::Document* document,
    const App::DocumentObject* operation
)
{
    if (!document || !isDerivedFrom(operation, "PartDesign::DesignScriptOperation")
        || stringProperty(operation, "VibeCADScriptedRole") != "implementation"
        || stringProperty(operation, "VibeCADScriptedEngine") != "vibescript:partdesign") {
        return nullptr;
    }

    const std::string programObjectName = stringProperty(operation, "ProgramObjectName");
    const std::string programId = stringProperty(operation, "ProgramId");
    if (programObjectName.empty() || programId.empty()) {
        return nullptr;
    }

    auto* program = document->getObject(programObjectName.c_str());
    if (!ModelTreeBrowserProjection::isVibeScriptProgram(program)
        || stringProperty(program, "VibeCADScriptedModelId") != programId) {
        // Incomplete or conflicting metadata remains at document root so the
        // browser never guesses a semantic owner from labels or link shape.
        return nullptr;
    }
    return program;
}

}  // namespace

ModelTreeBrowserProjection::ModelTreeBrowserProjection(App::Document* document)
{
    if (!document) {
        return;
    }

    const auto& objects = document->getObjects();
    _entries.reserve(objects.size());

    // VibeScript publishes through stable root-level links so Assembly,
    // TechDraw, FEM, CAM, and other consumers never lose object identity when
    // a program rebuilds. A solid output may also have a native editable Body.
    // Pair those representations by their explicit persisted contract instead
    // of relying on labels, object order, or generated names.
    std::unordered_map<std::string, App::DocumentObject*> scriptedBodies;
    std::unordered_map<std::string, App::DocumentObject*> scriptedPublications;
    std::unordered_map<std::string, App::DocumentObject*>
        scriptedPublicationTargets;
    auto recordUnique = [](
                            auto& table,
                            const std::string& identity,
                            App::DocumentObject* object
                        ) {
        if (identity.empty()) {
            return;
        }
        const auto [iterator, inserted] = table.emplace(identity, object);
        if (!inserted) {
            // Ambiguous metadata must remain fully visible for diagnosis.
            iterator->second = nullptr;
        }
    };
    for (auto* object : objects) {
        if (isBody(object)) {
            recordUnique(
                scriptedBodies,
                scriptedOutputIdentity(object, "implementation"),
                object
            );
        }
        if (isLink(object)) {
            recordUnique(
                scriptedPublications,
                scriptedOutputIdentity(object, "publication"),
                object
            );
        }
        recordUnique(
            scriptedPublicationTargets,
            scriptedOutputIdentity(object, "publication_target"),
            object
        );
    }

    std::unordered_map<const App::DocumentObject*, App::DocumentObject*>
        bodyRepresentations;
    std::unordered_map<const App::DocumentObject*, App::DocumentObject*>
        targetRepresentations;
    std::unordered_set<const App::DocumentObject*> completePublicationLinks;
    std::unordered_set<const App::DocumentObject*> pairedPublicationTargets;
    std::unordered_map<const App::DocumentObject*, App::DocumentObject*>
        modelOccurrenceOwners;
    std::unordered_set<std::string> ambiguousIdentities;
    const auto recordAmbiguities = [&](const auto& table) {
        for (const auto& [identity, object] : table) {
            if (!object) {
                ambiguousIdentities.insert(identity);
            }
        }
    };
    recordAmbiguities(scriptedBodies);
    recordAmbiguities(scriptedPublications);
    recordAmbiguities(scriptedPublicationTargets);
    for (const auto& [identity, published] : scriptedPublications) {
        const auto body = scriptedBodies.find(identity);
        const auto target = scriptedPublicationTargets.find(identity);
        if (!published || ambiguousIdentities.contains(identity)) {
            continue;
        }
        const bool hasBody =
            body != scriptedBodies.end() && body->second;
        const bool hasTarget =
            target != scriptedPublicationTargets.end() && target->second;
        if (!hasBody && !hasTarget) {
            // A lone tagged Link is damaged or incomplete, not an internal
            // publication. Keep it visible so the user can repair it.
            continue;
        }
        completePublicationLinks.insert(published);
        if (hasBody) {
            bodyRepresentations.emplace(published, body->second);
        }
        if (hasTarget) {
            targetRepresentations.emplace(published, target->second);
            pairedPublicationTargets.insert(target->second);
        }
    }
    for (auto* object : objects) {
        if (!isComponent(object)) {
            continue;
        }
        std::vector<App::DocumentObject*> occurrences;
        const auto* occurrenceNames = dynamic_cast<const App::PropertyStringList*>(
            object->getPropertyByName("VibeCADPartDesignComponentOccurrenceNames")
        );
        if (occurrenceNames) {
            App::Document* document = object->getDocument();
            const auto& names = occurrenceNames->getValues();
            occurrences.reserve(names.size());
            for (const std::string& name : names) {
                if (document) {
                    occurrences.push_back(document->getObject(name.c_str()));
                }
            }
        }
        else if (const auto* legacyOccurrences =
                     dynamic_cast<const App::PropertyLinkList*>(
                         object->getPropertyByName(
                             "VibeCADPartDesignComponentOccurrences"
                         )
                     )) {
            // Compatibility for documents loaded before the Python migration
            // runs. New documents keep this property empty so it cannot create
            // forward modeling dependencies.
            occurrences = legacyOccurrences->getValues();
        }
        for (auto* occurrence : occurrences) {
            if (!occurrence) {
                continue;
            }
            const auto [iterator, inserted] =
                modelOccurrenceOwners.emplace(occurrence, object);
            if (!inserted && iterator->second != object) {
                // Conflicting explicit owners are damage that must remain
                // visible at document root rather than being guessed away.
                iterator->second = nullptr;
            }
        }
    }

    for (auto* object : objects) {
        if (!object || !object->isAttachedToDocument()
            || object->testStatus(App::PartialObject)) {
            continue;
        }

        // Selection subnames must follow only native document containment.
        // Presentation ownership may place a root object below a semantic
        // component in the browser, but that virtual relationship is not a
        // valid getSubObject() path.
        const Ownership selectionOwnership = resolveOwnership(object);
        Ownership ownership = selectionOwnership;
        if (auto* program = exactVibeScriptProgramFor(document, object)) {
            // DesignScriptOperation is deliberately Design-global and must
            // not enter the App::Part containment graph. Its exact persisted
            // program identity supplies presentation ownership only; logical
            // selection paths continue to use selectionOwnership below.
            ownership.component = program;
        }
        if (const auto owner = modelOccurrenceOwners.find(object);
            owner != modelOccurrenceOwners.end() && owner->second) {
            ownership.component = owner->second;
        }
        App::DocumentObject* normalGroup = App::GroupExtension::getGroupOfObject(object);
        if (normalGroup
            && normalGroup->hasExtension(
                App::GeoFeatureGroupExtension::getExtensionClassTypeId()
            )) {
            // Bodies and Parts also implement GroupExtension. They are ownership
            // contexts, not user-created organizational groups.
            normalGroup = nullptr;
        }
        const bool publishedOutput =
            completePublicationLinks.contains(object);
        if (const auto body = bodyRepresentations.find(object);
            body != bodyRepresentations.end()) {
            // The output identity persisted on both objects is the publication
            // contract. Once that exact pair is established, present the
            // internal link in the Body's native ownership context. Never
            // derive publication status from an App::Link target or location.
            ownership = resolveOwnership(body->second);
        }
        else if (const auto target = targetRepresentations.find(object);
                 target != targetRepresentations.end()) {
            // A publication without a native Body uses its exact persisted
            // target pair's component solely as its presentation context.
            ownership = resolveOwnership(target->second);
        }

        Entry entry;
        entry.object = object;
        entry.component = ownership.component;
        entry.body = ownership.body;
        entry.group = normalGroup;
        entry.publishedOutput = publishedOutput;
        entry.publishedImplementation =
            pairedPublicationTargets.contains(object);
        entry.role = classify(object, ownership, publishedOutput);

        if (entry.role == Role::OriginFeature) {
            entry.logicalParent = findOriginParent(object);
        }
        else if (publishedOutput) {
            // The internal link uses the paired Body's semantic component but
            // remains a document-root object for selection and subelement
            // addressing.
            entry.logicalParent = nullptr;
        }
        else if (entry.role == Role::Component) {
            // resolveOwnership() starts at the object's parent, so this is the
            // containing component for nested components and null at document root.
            entry.logicalParent = selectionOwnership.component;
        }
        else if (normalGroup) {
            entry.logicalParent = normalGroup;
        }
        else if (entry.role == Role::Body) {
            entry.logicalParent = selectionOwnership.component;
        }
        else if (selectionOwnership.body) {
            entry.logicalParent = selectionOwnership.body;
        }
        else {
            entry.logicalParent = selectionOwnership.component;
        }

        if (const auto body = bodyRepresentations.find(object);
            body != bodyRepresentations.end()) {
            entry.bodyRepresentation = body->second;
        }
        _index.emplace(object, _entries.size());
        _entries.push_back(entry);
    }

    orderOperationsByTimeline(document);
    orderFeaturesByBodyHistory();
}

void ModelTreeBrowserProjection::orderOperationsByTimeline(const App::Document* document)
{
    const auto* timeline = App::DocumentTimeline::get(document);
    if (!timeline) {
        return;
    }

    std::vector<std::size_t> slots;
    for (std::size_t index = 0; index < _entries.size(); ++index) {
        if (_entries[index].role == Role::History) {
            slots.push_back(index);
        }
    }
    if (slots.size() < 2) {
        return;
    }

    std::unordered_map<const App::DocumentObject*, std::size_t> rank;
    const auto& operations = timeline->Operations.getValues();
    rank.reserve(operations.size());
    for (std::size_t position = 0; position < operations.size(); ++position) {
        rank.emplace(operations[position], position);
    }
    constexpr std::size_t unranked = std::numeric_limits<std::size_t>::max();
    const auto rankOf = [&](std::size_t slot) {
        const auto item = rank.find(_entries[slot].object);
        return item == rank.end() ? unranked : item->second;
    };

    std::vector<std::size_t> ordered = slots;
    std::stable_sort(
        ordered.begin(),
        ordered.end(),
        [&](std::size_t left, std::size_t right) { return rankOf(left) < rankOf(right); }
    );
    if (ordered == slots) {
        return;
    }

    std::vector<Entry> reordered;
    reordered.reserve(ordered.size());
    for (const std::size_t index : ordered) {
        reordered.push_back(_entries[index]);
    }
    for (std::size_t position = 0; position < slots.size(); ++position) {
        _entries[slots[position]] = reordered[position];
    }
    for (std::size_t index = 0; index < _entries.size(); ++index) {
        _index[_entries[index].object] = index;
    }
}

void ModelTreeBrowserProjection::orderFeaturesByBodyHistory()
{
    // Collect each Body's user-facing operation entries in creation order.
    std::unordered_map<const App::DocumentObject*, std::vector<std::size_t>>
        featureSlots;
    for (std::size_t i = 0; i < _entries.size(); ++i) {
        const Entry& entry = _entries[i];
        if ((entry.role == Role::Feature || entry.role == Role::History) && entry.body) {
            featureSlots[entry.body].push_back(i);
        }
    }

    bool changed = false;
    for (const auto& [body, slots] : featureSlots) {
        if (slots.size() < 2) {
            continue;
        }
        // A Body's Group property is its feature history: move up/down and
        // similar edits reorder Group without changing creation order.
        const auto* group = dynamic_cast<const App::PropertyLinkList*>(
            body->getPropertyByName("Group"));
        if (!group) {
            continue;
        }
        std::unordered_map<const App::DocumentObject*, std::size_t> rank;
        const auto& members = group->getValues();
        rank.reserve(members.size());
        for (std::size_t position = 0; position < members.size(); ++position) {
            rank.emplace(members[position], position);
        }
        constexpr std::size_t unranked = std::numeric_limits<std::size_t>::max();
        auto rankOf = [&](std::size_t slot) {
            const auto it = rank.find(_entries[slot].object);
            return it == rank.end() ? unranked : it->second;
        };

        // Stable: operations missing from Group keep creation order, after the
        // Body-history-ordered ones.
        std::vector<std::size_t> ordered = slots;
        std::stable_sort(
            ordered.begin(),
            ordered.end(),
            [&](std::size_t a, std::size_t b) { return rankOf(a) < rankOf(b); }
        );
        if (ordered == slots) {
            continue;
        }

        // Permute the operation entries into Body history order within the
        // slots they already occupy; every other entry keeps its position.
        std::vector<Entry> reordered;
        reordered.reserve(ordered.size());
        for (const std::size_t index : ordered) {
            reordered.push_back(_entries[index]);
        }
        for (std::size_t position = 0; position < slots.size(); ++position) {
            _entries[slots[position]] = reordered[position];
        }
        changed = true;
    }

    if (changed) {
        for (std::size_t i = 0; i < _entries.size(); ++i) {
            _index[_entries[i].object] = i;
        }
    }
}

const ModelTreeBrowserProjection::Entry*
ModelTreeBrowserProjection::find(const App::DocumentObject* object) const
{
    const auto it = _index.find(object);
    return it == _index.end() ? nullptr : &_entries[it->second];
}

bool ModelTreeBrowserProjection::isBody(const App::DocumentObject* object)
{
    return isDerivedFrom(object, "Part::BodyBase")
        || isDerivedFrom(object, "PartDesign::Body");
}

bool ModelTreeBrowserProjection::isComponent(const App::DocumentObject* object)
{
    return object && !isBody(object)
        && object->hasExtension(App::OriginGroupExtension::getExtensionClassTypeId());
}

bool ModelTreeBrowserProjection::isVibeScriptProgram(const App::DocumentObject* object)
{
    return isComponent(object)
        && stringProperty(object, "VibeCADScriptedRole") == "model"
        && stringProperty(object, "VibeCADScriptedEngine") == "vibescript:partdesign"
        && !stringProperty(object, "VibeCADScriptedModelId").empty();
}

ModelTreeBrowserProjection::Ownership
ModelTreeBrowserProjection::resolveOwnership(const App::DocumentObject* object)
{
    Ownership result;
    std::unordered_set<const App::DocumentObject*> visited;
    for (auto* current = object; current && visited.insert(current).second;) {
        auto* parent = geoParent(current);
        if (!parent) {
            parent = App::GroupExtension::getGroupOfObject(current);
        }
        if (!parent || parent == current) {
            break;
        }
        if (!result.body && isBody(parent)) {
            result.body = parent;
        }
        else if (!result.component && isComponent(parent)) {
            result.component = parent;
        }
        current = parent;
    }
    return result;
}

App::DocumentObject*
ModelTreeBrowserProjection::findOriginParent(const App::DocumentObject* object)
{
    if (!object || !object->isDerivedFrom<App::DatumElement>()) {
        return nullptr;
    }
    for (auto* incoming : object->getInList()) {
        if (incoming && incoming->isDerivedFrom<App::Origin>()) {
            return incoming;
        }
    }
    return nullptr;
}

ModelTreeBrowserProjection::Role ModelTreeBrowserProjection::classify(
    const App::DocumentObject* object,
    const Ownership& ownership,
    bool publishedOutput
)
{
    if (isComponent(object)) {
        return Role::Component;
    }
    if (isBody(object)) {
        return Role::Body;
    }
    if (object->isDerivedFrom<App::Origin>()) {
        return Role::Origin;
    }
    if (findOriginParent(object)) {
        return Role::OriginFeature;
    }
    // Assembly BOMs derive from Spreadsheet::Sheet for their tabular editor,
    // but they remain assembly results in the model browser. Classify the
    // concrete semantic type before the generic spreadsheet parameter rule so
    // the BOM stays beneath its Bills of Materials group.
    if (isDerivedFrom(object, "Assembly::BomObject")) {
        return Role::AssemblyOperation;
    }
    if (isParameterObject(object)) {
        return Role::Parameter;
    }
    if (isSketch(object)) {
        return Role::Sketch;
    }
    if (isReferenceGeometry(object)) {
        return Role::Reference;
    }
    const std::string outputType = vibeScriptOutputType(object);
    if (outputType == "component_link" && isLink(object)) {
        return Role::AssemblyOccurrence;
    }
    if (isDerivedFrom(ownership.component, "Assembly::AssemblyObject")
        || isDerivedFrom(ownership.component, "Assembly::AssemblyLink")) {
        if (outputType == "component_link" || isDerivedFrom(object, "Assembly::AssemblyLink")
            || isLink(object)) {
            return Role::AssemblyOccurrence;
        }
        if (outputType == "motion") {
            return Role::AssemblyMotion;
        }
        if (outputType == "joint" || outputType == "simulation"
            || outputType == "mechanism_verification" || outputType == "exploded_view"
            || outputType == "bom") {
            return Role::AssemblyOperation;
        }
    }
    if (publishedOutput) {
        return Role::VibeCADOutput;
    }
    if (isReference(object)) {
        return Role::Reference;
    }
    const std::string persistedTimelineRole = timelineRole(object);
    if (persistedTimelineRole == App::DocumentTimeline::OperationRole) {
        return Role::History;
    }
    if (persistedTimelineRole == App::DocumentTimeline::ResourceRole
        || persistedTimelineRole == App::DocumentTimeline::InternalRole) {
        return Role::Internal;
    }
    if (object->hasExtension(App::GroupExtension::getExtensionClassTypeId())
        && !object->hasExtension(App::GeoFeatureGroupExtension::getExtensionClassTypeId())) {
        return Role::Group;
    }
    if (ownership.body) {
        return Role::Feature;
    }
    if (hasGeometry(object)) {
        return Role::Geometry;
    }
    return Role::Other;
}
