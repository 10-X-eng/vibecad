// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ModelTreeBrowser.h"

#include <algorithm>
#include <cstddef>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <App/Datums.h>
#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/DocumentObject.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/GroupExtension.h>
#include <App/HostRuntime.h>
#include <App/MainThreadSignal.h>
#include <App/Origin.h>
#include <App/OriginGroupExtension.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/CancellationScope.h>
#include <fastsignals/signal.h>
#include <QObject>
#include <QPointer>
#include <QCoreApplication>
#include <QThread>

#include "Document.h"
#include "FrameBudget.h"


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

struct ModelTreeBrowserProjection::Preparation::Data
{
    struct Node
    {
        App::DocumentObject* object {};
        App::DocumentObject* parent {};
        App::DocumentObject* normalGroup {};
        App::DocumentObject* origin {};
        App::DocumentObject* program {};
        App::DocumentObject* timelineOwner {};
        App::DocumentObject* originObject {};
        bool plainGroup {};
        bool geoGroup {};
        bool originGroup {};
        bool datumElement {};
        bool localCoordinateSystem {};
        bool isOrigin {};
        bool timelineResource {};
        bool multiTransform {};
        bool included {};
        bool documentMember {};
        bool body {};
        bool component {};
        bool assembly {};
        Role baseRole {Role::Other};
        bool fixedRole {};
        std::optional<Role> assemblyRole;
        std::string bodyIdentity;
        std::string publicationIdentity;
        std::string targetIdentity;
        std::vector<App::DocumentObject*> occurrences;
        std::vector<App::DocumentObject*> transformations;
        std::vector<App::DocumentObject*> incoming;
        std::vector<App::DocumentObject*> groupMembers;
        std::vector<App::DocumentObject*> originFeatures;
        std::unordered_map<const App::DocumentObject*, std::size_t> groupIndex;
        std::unordered_set<const App::DocumentObject*> originFeatureIndex;
        Ownership ownership;
    };
    App::Document* document {};
    std::vector<App::DocumentObject*> pending;
    std::unordered_set<App::DocumentObject*> queued;
    std::size_t cursor {};
    std::size_t documentObjects {};
    std::optional<Node> capturing;
    std::size_t occurrenceCursor {};
    std::size_t transformationCursor {};
    std::size_t incomingCursor {};
    std::size_t groupCursor {};
    std::size_t originFeatureCursor {};
    bool operationsCaptured {};
    App::DocumentTimeline* timeline {};
    long timelineId {};
    bool canonicalTimeline {};
    std::vector<Node> nodes;
    std::unordered_map<const App::DocumentObject*, std::size_t> index;
    std::vector<App::DocumentObject*> operations;
    std::unordered_map<const App::DocumentObject*, std::vector<App::DocumentObject*>> histories;
    std::unordered_set<const App::Document*> sourceDocuments;
    std::vector<SourceRevision> sourceRevisions;

    void captureRevision(const App::Document* source)
    {
        if (source && sourceDocuments.insert(source).second) {
            sourceRevisions.push_back(
                {source->getName(), source->Uid.getValueStr(), source->getObjectStructureGeneration()});
        }
    }

    void enqueue(App::DocumentObject* object)
    {
        if (object && object->getDocument() != document && queued.insert(object).second) {
            captureRevision(object->getDocument());
            pending.push_back(object);
        }
    }
    const Node* find(const App::DocumentObject* object) const
    {
        const auto entry = index.find(object);
        return entry == index.end() ? nullptr : &nodes[entry->second];
    }
    bool contains(const Node& group, const App::DocumentObject* object) const
    {
        if (group.localCoordinateSystem) {
            return group.originFeatureIndex.contains(object);
        }
        if (group.originGroup && group.originObject) {
            const auto* origin = find(group.originObject);
            if (!origin || !origin->isOrigin) {
                throw Base::RuntimeError("Invalid Origin in captured model-browser group");
            }
            if (group.originObject == object || origin->originFeatureIndex.contains(object)) {
                return true;
            }
        }
        const auto member = group.groupIndex.find(object);
        if (object == group.object || member == group.groupIndex.end()) {
            return false;
        }
        const auto cycle = group.groupIndex.find(group.object);
        // Match nonrecursive GroupExtension::hasObject: a self-cycle before
        // the member prevents finding it; a preceding member is still valid.
        return cycle == group.groupIndex.end() || cycle->second >= member->second;
    }
    void resolveParents(Node& node) const
    {
        for (auto* incoming : node.incoming) {
            Base::CancellationScope::check();
            const auto* candidate = find(incoming);
            if (!candidate) { continue; }
            if (!node.normalGroup && candidate->plainGroup
                && candidate->groupIndex.contains(node.object)) {
                node.normalGroup = incoming;
            }
            if (node.datumElement) {
                if (!node.origin && candidate->isOrigin) { node.origin = incoming; }
                if (!node.parent && candidate->originGroup) { node.parent = incoming; }
                if (!node.parent && candidate->localCoordinateSystem) {
                    for (auto* ancestor : candidate->incoming) {
                        Base::CancellationScope::check();
                        const auto* owner = find(ancestor);
                        if (owner && owner->originGroup) {
                            node.parent = ancestor;
                            break;
                        }
                    }
                }
            }
            else if (!node.parent && candidate->geoGroup && contains(*candidate, node.object)) {
                node.parent = incoming;
            }
        }
        if (!node.parent) { node.parent = node.normalGroup; }
        if (node.origin && !node.component && !node.body && !node.isOrigin) {
            node.baseRole = Role::OriginFeature;
            node.fixedRole = true;
        }
    }
    Ownership ownership(const App::DocumentObject* object) const
    {
        Ownership result;
        std::unordered_set<const App::DocumentObject*> visited;
        for (auto* current = object; current && visited.insert(current).second;) {
            Base::CancellationScope::check();
            const auto* node = find(current);
            const auto* parent = node ? find(node->parent) : nullptr;
            if (!parent || parent->object == current) {
                break;
            }
            if (!result.body && parent->body) {
                result.body = parent->object;
            }
            else if (!result.component && parent->component) {
                result.component = parent->object;
            }
            current = parent->object;
        }
        return result;
    }
    Role classifyNode(const Node& node, const Ownership& owner, bool published) const
    {
        if (node.fixedRole) {
            return node.baseRole;
        }
        const auto* component = find(owner.component);
        if (component && component->assembly && node.assemblyRole) {
            return *node.assemblyRole;
        }
        if (published) {
            return Role::VibeCADOutput;
        }
        if (owner.body && (node.baseRole == Role::Other || node.baseRole == Role::Geometry)) {
            return Role::Feature;
        }
        return node.baseRole;
    }
};

ModelTreeBrowserProjection::Preparation::Preparation(App::Document* document)
    : data(std::make_unique<Data>())
{
    data->document = document;
    data->captureRevision(document);
    if (document) {
        data->documentObjects = document->getObjects().size();
    }
}

ModelTreeBrowserProjection::Preparation::~Preparation() = default;
ModelTreeBrowserProjection::Preparation::Preparation(Preparation&&) noexcept = default;
ModelTreeBrowserProjection::Preparation&
ModelTreeBrowserProjection::Preparation::operator=(Preparation&&) noexcept = default;

App::Document* ModelTreeBrowserProjection::SourceRevision::resolve() const
{
    auto* source = App::GetApplication().getDocument(name.c_str());
    return source && source->Uid.getValueStr() == uid ? source : nullptr;
}

bool ModelTreeBrowserProjection::SourceRevision::isCurrent() const
{
    const auto* source = resolve();
    return source
        && !Gui::Document::projectionRefreshBlocked(source)
        && source->getObjectStructureGeneration() == generation;
}

bool ModelTreeBrowserProjection::Preparation::isCurrent() const
{
    return std::ranges::all_of(data->sourceRevisions, [](const auto& source) {
        return source.isCurrent();
    });
}

bool ModelTreeBrowserProjection::isCurrent() const
{
    return std::ranges::all_of(_sourceRevisions, [](const auto& source) {
        return source.isCurrent();
    });
}

std::vector<App::Document*> ModelTreeBrowserProjection::Preparation::sourceDocuments() const
{
    std::vector<App::Document*> result;
    for (const auto& source : data->sourceRevisions) {
        if (auto* document = source.resolve()) {
            result.push_back(document);
        }
    }
    return result;
}

std::vector<App::Document*> ModelTreeBrowserProjection::sourceDocuments() const
{
    std::vector<App::Document*> result;
    for (const auto& source : _sourceRevisions) {
        if (auto* document = source.resolve()) {
            result.push_back(document);
        }
    }
    return result;
}

bool ModelTreeBrowserProjection::Preparation::captureNext()
{
    if (data->cursor == data->documentObjects + data->pending.size()) {
        if (data->timeline
            && data->operations.size() < data->timeline->Operations.getValues().size()) {
            data->operations.push_back(
                data->timeline->Operations.getValues()[data->operations.size()]);
            return false;
        }
        data->operationsCaptured = true;
        return true;
    }
    if (data->capturing) {
        auto& node = *data->capturing;
        auto* object = node.object;
        if (data->incomingCursor < object->getInList().size()) {
            auto* incoming = object->getInList()[data->incomingCursor++];
            node.incoming.push_back(incoming);
            data->enqueue(incoming);
            return false;
        }
        if (auto* group = object->getExtensionByType<App::GroupExtension>(true)) {
            if (data->groupCursor < group->Group.getValues().size()) {
                node.groupMembers.push_back(group->Group.getValues()[data->groupCursor++]);
                return false;
            }
        }
        if (node.localCoordinateSystem) {
            auto* coordinateSystem = static_cast<App::LocalCoordinateSystem*>(object);
            if (data->originFeatureCursor < coordinateSystem->OriginFeatures.getValues().size()) {
                node.originFeatures.push_back(
                    coordinateSystem->OriginFeatures.getValues()[data->originFeatureCursor++]);
                return false;
            }
        }
        if (node.multiTransform) {
            if (auto* children = dynamic_cast<const App::PropertyLinkList*>(
                    object->getPropertyByName("Transformations"))) {
                if (data->transformationCursor < children->getValues().size()) {
                    node.transformations.push_back(
                        children->getValues()[data->transformationCursor++]);
                    return false;
                }
            }
        }
        if (node.component) {
            if (auto* names = dynamic_cast<const App::PropertyStringList*>(
                    object->getPropertyByName("VibeCADPartDesignComponentOccurrenceNames"))) {
                if (data->occurrenceCursor < names->getValues().size()) {
                    auto* owner = object->getDocument();
                    const auto& name = names->getValues()[data->occurrenceCursor++];
                    node.occurrences.push_back(owner ? owner->getObject(name.c_str()) : nullptr);
                    return false;
                }
            }
            else if (auto* legacy = dynamic_cast<const App::PropertyLinkList*>(
                         object->getPropertyByName("VibeCADPartDesignComponentOccurrences"))) {
                if (data->occurrenceCursor < legacy->getValues().size()) {
                    node.occurrences.push_back(legacy->getValues()[data->occurrenceCursor++]);
                    return false;
                }
            }
        }
        data->index.emplace(object, data->nodes.size());
        data->nodes.push_back(std::move(node));
        data->capturing.reset();
        ++data->cursor;
        return false;
    }
    auto* object = data->cursor < data->documentObjects
        ? data->document->getObjects()[data->cursor]
        : data->pending[data->cursor - data->documentObjects];
    Data::Node node;
    node.object = object;
    node.documentMember = data->cursor < data->documentObjects;
    if (node.documentMember && object->isDerivedFrom<App::DocumentTimeline>()) {
        const bool canonical = std::string_view(object->getNameInDocument())
            == App::DocumentTimeline::ObjectName;
        if (canonical || (!data->canonicalTimeline
                          && (!data->timeline || object->getID() < data->timelineId))) {
            data->timeline = static_cast<App::DocumentTimeline*>(object);
            data->timelineId = object->getID();
            data->canonicalTimeline = canonical;
        }
    }
    node.included = node.documentMember
        && object->isAttachedToDocument() && !object->testStatus(App::PartialObject);
    node.body = isBody(object);
    node.component = isComponent(object);
    node.assembly = isDerivedFrom(object, "Assembly::AssemblyObject")
        || isDerivedFrom(object, "Assembly::AssemblyLink");
    node.plainGroup = object->getExtension(App::GroupExtension::getExtensionClassTypeId(), false, true)
        || object->getExtension(Base::Type::fromName("App::GroupExtensionPython"), false, true);
    node.geoGroup = object->hasExtension(App::GeoFeatureGroupExtension::getExtensionClassTypeId());
    node.originGroup = object->hasExtension(App::OriginGroupExtension::getExtensionClassTypeId());
    node.datumElement = object->isDerivedFrom<App::DatumElement>();
    node.localCoordinateSystem = object->isDerivedFrom<App::LocalCoordinateSystem>();
    node.isOrigin = object->isDerivedFrom<App::Origin>();
    if (node.originGroup) {
        node.originObject = object->getExtensionByType<App::OriginGroupExtension>()->Origin.getValue();
        data->enqueue(node.originObject);
    }
    node.program = exactVibeScriptProgramFor(data->document, object);
    node.timelineResource = App::DocumentTimeline::hasTimelineResourceRole(object);
    node.timelineOwner = node.timelineResource ? App::DocumentTimeline::timelineOwner(object) : nullptr;
    data->enqueue(node.timelineOwner);
    node.multiTransform = hasTypeNameFragment(object, "MultiTransform");
    data->enqueue(node.program);
    node.baseRole = classify(object, {}, false, false);
    const auto outputType = vibeScriptOutputType(object);
    node.fixedRole = node.component || node.body || object->isDerivedFrom<App::Origin>()
        || node.origin || isDerivedFrom(object, "Assembly::BomObject")
        || isParameterObject(object) || isSketch(object) || isReferenceGeometry(object)
        || (outputType == "component_link" && isLink(object));
    if (outputType == "component_link" || isDerivedFrom(object, "Assembly::AssemblyLink")
        || isLink(object)) {
        node.assemblyRole = Role::AssemblyOccurrence;
    }
    else if (outputType == "motion") {
        node.assemblyRole = Role::AssemblyMotion;
    }
    else if (outputType == "joint" || outputType == "simulation"
             || outputType == "mechanism_verification" || outputType == "exploded_view"
             || outputType == "bom") {
        node.assemblyRole = Role::AssemblyOperation;
    }
    if (node.body) {
        node.bodyIdentity = scriptedOutputIdentity(object, "implementation");
    }
    if (isLink(object)) {
        node.publicationIdentity = scriptedOutputIdentity(object, "publication");
    }
    node.targetIdentity = scriptedOutputIdentity(object, "publication_target");
    data->capturing = std::move(node);
    data->occurrenceCursor = 0;
    data->transformationCursor = 0;
    data->incomingCursor = 0;
    data->groupCursor = 0;
    data->originFeatureCursor = 0;
    return false;
}

ModelTreeBrowserProjection::ModelTreeBrowserProjection(App::Document* document)
{
    Preparation preparation(document);
    while (!preparation.captureNext()) {}
    *this = std::move(preparation).finish();
}

ModelTreeBrowserProjection ModelTreeBrowserProjection::Preparation::finish(
    App::HostRuntime* runtime, std::stop_token cancellation) &&
{
    if (runtime && App::MainThreadSignalConfig::hasHooks()
        && App::MainThreadSignalConfig::isMainThread()) {
        throw std::logic_error("Parallel model browser preparation requires a worker caller");
    }
    Base::CancellationScope operationScope(cancellation);
    Base::CancellationScope::check();
    if (!data->operationsCaptured || data->capturing
        || data->cursor != data->documentObjects + data->pending.size()) {
        throw std::logic_error("Model browser preparation has uncaptured objects");
    }
    ModelTreeBrowserProjection result;
    auto& _entries = result._entries;
    auto& _index = result._index;

    _entries.reserve(data->documentObjects);
    const auto indexRelationships = [this, cancellation](std::size_t index) {
        Base::CancellationScope childScope(cancellation);
        auto& node = data->nodes[index];
        for (std::size_t member = 0; member < node.groupMembers.size(); ++member) {
            Base::CancellationScope::check();
            node.groupIndex.try_emplace(node.groupMembers[member], member);
        }
        for (auto* feature : node.originFeatures) {
            Base::CancellationScope::check();
            node.originFeatureIndex.insert(feature);
        }
    };
    const auto resolveParents = [this, cancellation](std::size_t index) {
        Base::CancellationScope childScope(cancellation);
        data->resolveParents(data->nodes[index]);
    };
    const auto resolve = [this, cancellation](std::size_t index) {
        Base::CancellationScope childScope(cancellation);
        Base::CancellationScope::check();
        auto& node = data->nodes[index];
        node.ownership = data->ownership(node.object);
    };
    if (runtime) {
        runtime->parallelFor(data->nodes.size(), indexRelationships);
        runtime->parallelFor(data->nodes.size(), resolveParents);
        runtime->parallelFor(data->nodes.size(), resolve);
    }
    else {
        // The existing synchronous constructor retains its public contract.
        // Asynchronous consumers supply the shared runtime, never wait here
        // on the GUI owner, and never select this adapter after a failure.
        for (const auto& phase : {std::function<void(std::size_t)>(indexRelationships),
                                  std::function<void(std::size_t)>(resolveParents),
                                  std::function<void(std::size_t)>(resolve)}) {
            for (std::size_t index = 0; index < data->nodes.size(); ++index) {
                phase(index);
            }
        }
    }

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
    for (const auto& node : data->nodes) {
        Base::CancellationScope::check();
        auto* object = node.object;
        if (!node.documentMember) {
            continue;
        }
        if (node.body) {
            recordUnique(
                scriptedBodies,
                node.bodyIdentity,
                object
            );
        }
        if (!node.publicationIdentity.empty()) {
            recordUnique(
                scriptedPublications,
                node.publicationIdentity,
                object
            );
        }
        recordUnique(
            scriptedPublicationTargets,
            node.targetIdentity,
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
    for (const auto& node : data->nodes) {
        if (!node.documentMember || !node.component) {
            continue;
        }
        for (auto* occurrence : node.occurrences) {
            if (!occurrence) {
                continue;
            }
            const auto [iterator, inserted] =
                modelOccurrenceOwners.emplace(occurrence, node.object);
            if (!inserted && iterator->second != node.object) {
                // Conflicting explicit owners stay visible at document root.
                iterator->second = nullptr;
            }
        }
    }

    for (const auto& node : data->nodes) {
        auto* object = node.object;
        if (!node.included) {
            continue;
        }

        // Selection subnames must follow only native document containment.
        // Presentation ownership may place a root object below a semantic
        // component in the browser, but that virtual relationship is not a
        // valid getSubObject() path.
        const Ownership selectionOwnership = node.ownership;
        Ownership ownership = selectionOwnership;
        if (auto* program = node.program) {
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
        App::DocumentObject* normalGroup = node.normalGroup;
        const bool publishedOutput =
            completePublicationLinks.contains(object);
        if (const auto body = bodyRepresentations.find(object);
            body != bodyRepresentations.end()) {
            // The output identity persisted on both objects is the publication
            // contract. Once that exact pair is established, present the
            // internal link in the Body's native ownership context. Never
            // derive publication status from an App::Link target or location.
            ownership = data->find(body->second)->ownership;
        }
        else if (const auto target = targetRepresentations.find(object);
                 target != targetRepresentations.end()) {
            // A publication without a native Body uses its exact persisted
            // target pair's component solely as its presentation context.
            ownership = data->find(target->second)->ownership;
        }

        Entry entry;
        entry.object = object;
        entry.component = ownership.component;
        entry.body = ownership.body;
        entry.group = normalGroup;
        entry.publishedOutput = publishedOutput;
        entry.publishedImplementation =
            pairedPublicationTargets.contains(object);
        entry.role = data->classifyNode(node, ownership, publishedOutput);

        if (entry.role == Role::OriginFeature) {
            entry.logicalParent = node.origin;
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

    // The membership snapshot already contains each Body's ordered Group.
    // Transfer that storage after parent resolution instead of walking and
    // copying the same live property a second time on the GUI owner.
    for (auto& node : data->nodes) {
        Base::CancellationScope::check();
        if (node.body) {
            data->histories.emplace(node.object, std::move(node.groupMembers));
        }
    }
    result.orderOperationsByTimeline(data->operations);
    result.orderFeaturesByBodyHistory(data->histories);
    // Resolve semantic History ownership once from the same immutable graph.
    // Null memo entries also break malformed resource cycles without walking
    // them once per operation or ever consulting a live document on a worker.
    for (const auto& node : data->nodes) {
        if (!node.documentMember) {
            continue;
        }
        Base::CancellationScope::check();
        std::vector<const App::DocumentObject*> path;
        const auto* current = &node;
        const App::DocumentObject* root = nullptr;
        while (current && current->documentMember) {
            const auto [entry, inserted] = result._timelineRoots.emplace(current->object, nullptr);
            if (!inserted) {
                root = entry->second;
                break;
            }
            path.push_back(current->object);
            if (!current->timelineResource) {
                root = current->object;
                break;
            }
            current = data->find(current->timelineOwner);
        }
        for (const auto* object : path) {
            result._timelineRoots[object] = root;
        }
    }
    for (const auto* operation : data->operations) {
        if (const auto* node = data->find(operation)) {
            for (auto* child : node->transformations) {
                if (child) {
                    result._internalTransformations.insert(child);
                }
            }
        }
    }
    result._operations = std::move(data->operations);
    result._sourceRevisions = std::move(data->sourceRevisions);
    return result;
}

void ModelTreeBrowserProjection::orderOperationsByTimeline(
    const std::vector<App::DocumentObject*>& operations)
{
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

void ModelTreeBrowserProjection::orderFeaturesByBodyHistory(
    const std::unordered_map<const App::DocumentObject*,
                             std::vector<App::DocumentObject*>>& histories)
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
        const auto history = histories.find(body);
        if (history == histories.end()) {
            continue;
        }
        std::unordered_map<const App::DocumentObject*, std::size_t> rank;
        const auto& members = history->second;
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
    bool publishedOutput,
    bool inspectOrigin
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
    if (inspectOrigin && findOriginParent(object)) {
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

struct ModelTreeBrowserCache::Data: QObject
{
    struct Waiter
    {
        QPointer<QObject> receiver;
        Completion completion;
    };
    struct Observer
    {
        QPointer<QObject> receiver;
        std::function<void()> invalidated;
    };
    std::string documentUid;
    Result cached;
    std::unique_ptr<ModelTreeBrowserProjection::Preparation> preparation;
    std::unordered_map<QObject*, Waiter> waiters;
    std::unordered_map<QObject*, Observer> observers;
    std::vector<fastsignals::scoped_connection> stableConnections;
    std::vector<fastsignals::scoped_connection> sourceConnections;
    std::stop_source cancellation;
    bool scheduled {};
    bool computing {};
    bool invalidationPending {};
    std::uint64_t invalidationGeneration {};

    explicit Data(App::Document* document)
        : documentUid(document ? document->Uid.getValueStr() : "")
    {}
    ~Data() override { cancellation.request_stop(); }

    App::Document* document() const
    {
        for (auto* candidate : App::GetApplication().getDocuments()) {
            if (candidate->Uid.getValueStr() == documentUid) {
                return candidate;
            }
        }
        return nullptr;
    }

    void request(QObject* receiver, Completion completion)
    {
        if (!receiver || !completion) {
            return;
        }
        waiters.insert_or_assign(receiver, Waiter {receiver, std::move(completion)});
        schedule();
    }

    void sourceChanged()
    {
        if (invalidationPending) {
            return;
        }
        invalidationPending = true;
        const auto generation = ++invalidationGeneration;
        dispatchToGuiFrame(this, [this, generation] {
            if (!invalidationPending || invalidationGeneration != generation) {
                return;
            }
            std::erase_if(observers, [](const auto& entry) { return !entry.second.receiver; });
            // Post separately: a subscriber may unregister, request again, or
            // throw without invalidating iteration or starving other consumers.
            const QPointer<Data> lifetime(this);
            for (const auto& [key, observer] : observers) {
                dispatchToGuiFrame([lifetime, observer, generation] {
                    if (lifetime && lifetime->invalidationPending
                        && lifetime->invalidationGeneration == generation && observer.receiver) {
                        observer.invalidated();
                    }
                });
            }
        });
    }

    void observeSources()
    {
        sourceConnections.clear();
        const auto sources = cached->sourceDocuments();
        const auto* owner = document();
        for (auto* source : sources) {
            sourceConnections.emplace_back(source->signalObjectSchemaChanged.connect(
                [this](long) { sourceChanged(); }));
            // Tree/History already observe their own document with property-
            // specific handlers. Do not turn local Visibility/Shape changes
            // into unconditional hierarchy rebuilds through this shared cache.
            if (source == owner) {
                continue;
            }
            sourceConnections.emplace_back(source->signalNewObject.connect(
                [this](const App::DocumentObject&) { sourceChanged(); }));
            sourceConnections.emplace_back(source->signalDeletedObject.connect(
                [this](const App::DocumentObject&) { sourceChanged(); }));
            sourceConnections.emplace_back(source->signalChangedObject.connect(
                [this, source, generation = source->getObjectStructureGeneration()]
                (const App::DocumentObject&, const App::Property&) {
                    if (source->getObjectStructureGeneration() != generation) { sourceChanged(); }
                }));
            sourceConnections.emplace_back(source->signalBecameStable.connect(
                [this](const App::Document&) {
                    if (cached && !cached->isCurrent()) { sourceChanged(); }
                }));
        }
        sourceConnections.emplace_back(App::GetApplication().signalDeleteDocument.connect(
            [this, sources](const App::Document& closing) {
                if (std::ranges::find(sources, &closing) != sources.end()) {
                    sourceChanged();
                }
            }));
        invalidationPending = false;
    }

    void schedule()
    {
        if (!scheduled && !computing && !waiters.empty()) {
            scheduled = true;
            if (!dispatchToGuiFrame(this, [this] {
                    try {
                        drain();
                    }
                    catch (const std::exception& error) {
                        preparation.reset();
                        scheduled = false;
                        deliver({}, error.what());
                    }
                    catch (...) {
                        preparation.reset();
                        scheduled = false;
                        deliver({}, "Model browser capture cancelled or failed");
                    }
                })) {
                scheduled = false;
                throw std::runtime_error("Model browser owner dispatcher is unavailable");
            }
        }
    }

    bool waitForStableSources(const std::vector<App::Document*>& sources)
    {
        stableConnections.clear();
        bool busy = false;
        for (auto* source : sources) {
            if (!Gui::Document::projectionRefreshBlocked(source)) {
                continue;
            }
            busy = true;
            stableConnections.emplace_back(source->signalBecameStable.connect(
                [this](const App::Document&) { schedule(); }));
            stableConnections.emplace_back(source->signalCooperativeMutationChanged.connect(
                [this](const App::Document&, bool active) { if (!active) { schedule(); } }));
            stableConnections.emplace_back(source->signalSkipRecompute.connect(
                [this](const App::Document&, const std::vector<App::DocumentObject*>&) { schedule(); }));
        }
        if (busy) {
            // Global restore activity and early-return recomputes can unblock
            // a source without emitting that source's normal stable signal.
            auto& app = App::GetApplication();
            stableConnections.emplace_back(app.signalFinishRestoreDocument.connect(
                [this](const App::Document&) { schedule(); }));
            stableConnections.emplace_back(app.signalRestoreActivityIdle.connect(
                [this] { schedule(); }));
            stableConnections.emplace_back(app.signalFinishOpenDocument.connect(
                [this] { schedule(); }));
            stableConnections.emplace_back(app.signalRecomputeRequestFinished.connect(
                [this](const std::string&) { schedule(); }));
            stableConnections.emplace_back(app.signalDeleteDocument.connect(
                [this](const App::Document&) { schedule(); }));
        }
        return busy;
    }

    void deliver(Result result, std::string failure)
    {
        auto recipients = std::move(waiters);
        waiters.clear();
        const QPointer<Data> lifetime(this);
        for (auto& [key, waiter] : recipients) {
            dispatchToGuiFrame([lifetime, waiter = std::move(waiter), result, failure]() mutable {
                if (!lifetime || !waiter.receiver) {
                    return;
                }
                if (result && !result->isCurrent()) {
                    lifetime->request(waiter.receiver, std::move(waiter.completion));
                    return;
                }
                waiter.completion(result, std::move(failure));
            });
        }
    }

    void drain()
    {
        scheduled = false;
        std::erase_if(waiters, [](const auto& entry) { return !entry.second.receiver; });
        if (waiters.empty() || computing) {
            return;
        }
        auto* owner = document();
        if (!owner) {
            deliver({}, "The projection's source document was closed");
            return;
        }
        const auto sources = preparation ? preparation->sourceDocuments()
            : cached ? cached->sourceDocuments() : std::vector<App::Document*> {owner};
        if (waitForStableSources(sources)) {
            return;
        }
        if (cached && cached->isCurrent()) {
            if (invalidationPending) {
                // A notification may have been raised before the actual
                // mutation; only a current capture can clear the dirty epoch.
                observeSources();
            }
            deliver(cached, {});
            return;
        }
        cached.reset();
        if (!preparation || !preparation->isCurrent()) {
            preparation = std::make_unique<ModelTreeBrowserProjection::Preparation>(owner);
        }
        FrameBudget budget;
        bool captured = false;
        do {
            if (!preparation->isCurrent()) {
                if (!waitForStableSources(preparation->sourceDocuments())) {
                    preparation.reset();
                    schedule();
                }
                return;
            }
            captured = preparation->captureNext();
        } while (!captured && !budget.exhausted());
        if (!captured) {
            schedule();
            return;
        }
        computing = true;
        const QPointer<Data> lifetime(this);
        auto complete = [lifetime](Result result, std::string failure) {
            dispatchToGuiFrame([lifetime, result = std::move(result), failure = std::move(failure)] {
                if (!lifetime) {
                    return;
                }
                lifetime->computing = false;
                try {
                    if (!result) {
                        lifetime->deliver({}, failure);
                    }
                    else {
                        lifetime->cached = result;
                        if (result->isCurrent()) {
                            lifetime->observeSources();
                        }
                        lifetime->schedule();
                    }
                }
                catch (const std::exception& error) {
                    lifetime->cached.reset();
                    lifetime->deliver({}, error.what());
                }
                catch (...) {
                    lifetime->cached.reset();
                    lifetime->deliver({}, "Model browser completion cancelled or failed");
                }
            });
        };
        auto& runtime = App::GetApplication().hostRuntime();
        const auto stop = cancellation.get_token();
        try {
            runtime.submitWithCompletion(App::HostRuntime::Lane::Compute,
                [input = std::move(preparation), &runtime, stop](std::stop_token shutdown) mutable {
                    Base::CancellationScope shutdownScope(shutdown);
                    return std::make_shared<ModelTreeBrowserProjection>(
                        std::move(*input).finish(&runtime, stop));
                },
                [complete](std::future<std::shared_ptr<ModelTreeBrowserProjection>> result) {
                    try { complete(result.get(), {}); }
                    catch (const std::exception& error) { complete({}, error.what()); }
                    catch (...) { complete({}, "Model browser preparation cancelled or failed"); }
                });
        }
        catch (const std::exception& error) {
            complete({}, error.what());
        }
    }
};

ModelTreeBrowserCache::ModelTreeBrowserCache(App::Document* document)
{
    if (!QCoreApplication::instance()
        || QThread::currentThread() != QCoreApplication::instance()->thread()) {
        throw std::runtime_error("Model browser cache must be created on the GUI owner");
    }
    data = std::make_unique<Data>(document);
}

ModelTreeBrowserCache::~ModelTreeBrowserCache() = default;

void ModelTreeBrowserCache::request(QObject* receiver, Completion completion)
{
    if (QThread::currentThread() != data->thread()
        || (receiver && receiver->thread() != data->thread())) {
        throw std::runtime_error("Model browser requests must belong to the GUI owner");
    }
    data->request(receiver, std::move(completion));
}

void ModelTreeBrowserCache::observeInvalidation(QObject* receiver,
                                              std::function<void()> invalidated)
{
    if (QThread::currentThread() != data->thread()
        || (receiver && receiver->thread() != data->thread())) {
        throw std::runtime_error("Model browser observers must belong to the GUI owner");
    }
    if (receiver && invalidated) {
        data->observers.insert_or_assign(receiver, Data::Observer {receiver, std::move(invalidated)});
    }
    else {
        data->observers.erase(receiver);
    }
}
