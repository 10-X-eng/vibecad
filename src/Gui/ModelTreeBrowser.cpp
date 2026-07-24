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

bool isConstruction(const App::DocumentObject* object)
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

bool hasExactType(const App::DocumentObject* object, std::string_view typeName)
{
    return object
        && std::string_view(object->getTypeId().getName()) == typeName;
}

bool isCompatibilityAdoptedResult(
    const App::DocumentObject* object,
    const App::DocumentObject* body,
    App::Document* document
)
{
    if (!object || !body || !document
        || !hasExactType(object, "PartDesign::Feature")
        || stringProperty(body, "VibeCADScriptedRole") != "implementation"
        || stringProperty(body, "VibeCADScriptedEngine")
            != "vibescript:partdesign") {
        return false;
    }

    const std::string featureRole =
        stringProperty(object, "VibeCADNativeFeatureRole");
    const std::string_view internalName =
        object->getNameInDocument() ? object->getNameInDocument() : "";
    if (featureRole != "adopted_result"
        && internalName.find("AdoptedResult_") == std::string_view::npos) {
        return false;
    }

    const std::string featureLabel = stringProperty(object, "Label");
    const std::string bodyLabel = stringProperty(body, "Label");
    return !featureLabel.empty() && !bodyLabel.empty()
        && document->haveSameBaseName(featureLabel, bodyLabel);
}

App::DocumentObject* geoParent(const App::DocumentObject* object)
{
    return App::GeoFeatureGroupExtension::getGroupOfObject(object);
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
    }

    std::unordered_map<const App::DocumentObject*, App::DocumentObject*>
        bodyRepresentations;
    std::unordered_map<const App::DocumentObject*, App::DocumentObject*>
        publicationRepresentations;
    for (const auto& [identity, published] : scriptedPublications) {
        const auto body = scriptedBodies.find(identity);
        if (!published || body == scriptedBodies.end() || !body->second
            || geoParent(published)
            || App::GroupExtension::getGroupOfObject(published)) {
            continue;
        }
        auto* linked = published->getLinkedObject(false);
        if (!linked || linked == published || linked->getDocument() != document) {
            continue;
        }
        const Ownership linkedOwnership = resolveOwnership(linked);
        const Ownership bodyOwnership = resolveOwnership(body->second);
        if (!linkedOwnership.component
            || linkedOwnership.component != bodyOwnership.component) {
            continue;
        }
        bodyRepresentations.emplace(published, body->second);
        publicationRepresentations.emplace(body->second, published);
    }

    std::unordered_set<const App::DocumentObject*> publishedImplementations;
    for (auto* object : objects) {
        if (!isLink(object) || geoParent(object)
            || App::GroupExtension::getGroupOfObject(object)) {
            continue;
        }
        auto* linked = object->getLinkedObject(false);
        if (!linked || linked == object || linked->getDocument() != document
            || isComponent(linked) || isBody(linked)) {
            continue;
        }
        if (resolveOwnership(linked).component) {
            publishedImplementations.insert(linked);
        }
    }

    for (auto* object : objects) {
        if (!object || !object->isAttachedToDocument()
            || object->testStatus(App::PartialObject)) {
            continue;
        }

        Ownership ownership = resolveOwnership(object);
        App::DocumentObject* normalGroup = App::GroupExtension::getGroupOfObject(object);
        if (normalGroup
            && normalGroup->hasExtension(
                App::GeoFeatureGroupExtension::getExtensionClassTypeId()
            )) {
            // Bodies and Parts also implement GroupExtension. They are ownership
            // contexts, not user-created organizational groups.
            normalGroup = nullptr;
        }
        bool publishedOutput = false;

        // VibeScript and other generators commonly keep implementation geometry in
        // an App::Part and expose stable public objects as root-level App::Links.
        // Present those links with the component that owns their target.  This is
        // inferred from native ownership and works for documents made before this
        // browser existed.
        if (isLink(object) && !geoParent(object) && !normalGroup) {
            auto* linked = object->getLinkedObject(false);
            if (linked && linked != object && linked->getDocument() == document
                && !isComponent(linked) && !isBody(linked)) {
                Ownership linkedOwnership = resolveOwnership(linked);
                if (linkedOwnership.component) {
                    ownership = linkedOwnership;
                    publishedOutput = true;
                }
            }
        }

        Entry entry;
        entry.object = object;
        entry.component = ownership.component;
        entry.body = ownership.body;
        entry.group = normalGroup;
        entry.publishedOutput = publishedOutput;
        entry.publishedImplementation = publishedImplementations.contains(object);
        entry.role = classify(object, ownership, publishedOutput);

        if (entry.role == Role::OriginFeature) {
            entry.logicalParent = findOriginParent(object);
        }
        else if (publishedOutput) {
            // The link is displayed with its target's component, but it remains a
            // document-root object for selection and subelement addressing.
            entry.logicalParent = nullptr;
        }
        else if (entry.role == Role::Component) {
            // resolveOwnership() starts at the object's parent, so this is the
            // containing component for nested components and null at document root.
            entry.logicalParent = ownership.component;
        }
        else if (normalGroup) {
            entry.logicalParent = normalGroup;
        }
        else if (entry.role == Role::Body) {
            entry.logicalParent = ownership.component;
        }
        else if (ownership.body) {
            entry.logicalParent = ownership.body;
        }
        else {
            entry.logicalParent = ownership.component;
        }

        if (const auto body = bodyRepresentations.find(object);
            body != bodyRepresentations.end()) {
            entry.bodyRepresentation = body->second;
        }
        if (const auto published = publicationRepresentations.find(object);
            published != publicationRepresentations.end()) {
            entry.publicationRepresentation = published->second;
        }
        entry.compatibilityResultLabel = isCompatibilityAdoptedResult(
            object,
            ownership.body,
            document
        );

        _index.emplace(object, _entries.size());
        _entries.push_back(entry);
    }

    std::unordered_map<const App::DocumentObject*, std::vector<App::DocumentObject*>>
        resultsByBody;
    for (const auto& entry : _entries) {
        if (entry.role == Role::Feature && entry.body) {
            resultsByBody[entry.body].push_back(entry.object);
        }
    }
    for (auto& entry : _entries) {
        if (entry.role != Role::Body || !entry.publicationRepresentation) {
            continue;
        }
        if (const auto results = resultsByBody.find(entry.object);
            results != resultsByBody.end()) {
            entry.bodyResultRepresentations = results->second;
        }
    }

    orderFeaturesByBodyHistory();
}

void ModelTreeBrowserProjection::orderFeaturesByBodyHistory()
{
    // Collect each Body's feature entries in their current (creation) order.
    std::unordered_map<const App::DocumentObject*, std::vector<std::size_t>>
        featureSlots;
    for (std::size_t i = 0; i < _entries.size(); ++i) {
        const Entry& entry = _entries[i];
        if (entry.role == Role::Feature && entry.body) {
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

        // Stable: features missing from Group keep creation order, after the
        // history-ordered ones.
        std::vector<std::size_t> ordered = slots;
        std::stable_sort(
            ordered.begin(),
            ordered.end(),
            [&](std::size_t a, std::size_t b) { return rankOf(a) < rankOf(b); }
        );
        if (ordered == slots) {
            continue;
        }

        // Permute the feature entries into history order within the slots they
        // already occupy; every non-feature entry keeps its position.
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

ModelTreeBrowserProjection::Ownership
ModelTreeBrowserProjection::resolveOwnership(const App::DocumentObject* object)
{
    Ownership result;
    for (auto* parent = geoParent(object); parent; parent = geoParent(parent)) {
        if (!result.body && isBody(parent)) {
            result.body = parent;
        }
        else if (!result.component && isComponent(parent)) {
            result.component = parent;
        }
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
    if (isParameterObject(object)) {
        return Role::Parameter;
    }
    if (isSketch(object)) {
        return Role::Sketch;
    }
    if (isConstruction(object)) {
        return Role::Construction;
    }
    if (object->hasExtension(App::GroupExtension::getExtensionClassTypeId())
        && !object->hasExtension(App::GeoFeatureGroupExtension::getExtensionClassTypeId())) {
        return Role::Group;
    }
    if (publishedOutput) {
        return Role::Geometry;
    }
    if (isReference(object)) {
        return Role::Reference;
    }
    if (ownership.body) {
        return Role::Feature;
    }
    if (hasGeometry(object)) {
        return Role::Geometry;
    }
    return Role::Other;
}
