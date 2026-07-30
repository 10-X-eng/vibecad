// SPDX-License-Identifier: LGPL-2.1-or-later

#include "TimelineImport.h"

#include <algorithm>
#include <map>
#include <ranges>
#include <set>
#include <unordered_map>
#include <unordered_set>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentTimeline.h>
#include <App/PropertyLinks.h>
#include <Base/Exception.h>
#include <Base/FileInfo.h>
#include <Base/Stream.h>

#include "MergeDocuments.h"

using namespace Gui;

namespace
{

const App::Document* liveDocumentContaining(
    const App::DocumentObject* object
)
{
    if (!object) {
        return nullptr;
    }
    const auto& documents = App::GetApplication().getDocuments();
    const auto found = std::ranges::find_if(
        documents,
        [object](const App::Document* document) {
            return document && document->containsObject(object);
        }
    );
    return found == documents.end() ? nullptr : *found;
}

bool isLiveObject(const App::DocumentObject* object)
{
    const auto* document = liveDocumentContaining(object);
    return document && object->getDocument() == document
        && object->getNameInDocument()
        && object->getNameInDocument()[0] != '\0'
        && object->getID() > 0
        && document->getObject(object->getNameInDocument()) == object
        && document->getObjectByID(object->getID()) == object;
}

bool isTracked(
    const App::DocumentTimeline& timeline,
    const App::DocumentObject* object
)
{
    const auto& operations = timeline.Operations.getValues();
    return std::ranges::find(operations, object) != operations.end();
}

void insertLiveObjects(
    std::unordered_set<App::DocumentObject*>& identities,
    const std::vector<App::DocumentObject*>& objects
)
{
    for (auto* object : objects) {
        if (!isLiveObject(object)) {
            throw Base::ValueError(
                "A timeline export contains a missing or replaced object"
            );
        }
        identities.insert(object);
    }
}

std::vector<App::DocumentObject*> dependencyOrder(
    const std::unordered_set<App::DocumentObject*>& identities
)
{
    std::vector<App::DocumentObject*> inputs(
        identities.begin(),
        identities.end()
    );
    auto closure = App::Document::getDependencyList(
        inputs,
        App::Document::DepNoXLinked | App::Document::DepSort
    );
    std::vector<App::DocumentObject*> ordered;
    ordered.reserve(identities.size());
    for (auto* object : closure) {
        if (identities.contains(object)) {
            ordered.push_back(object);
        }
    }
    if (ordered.size() != identities.size()) {
        throw Base::RuntimeError(
            "Could not produce one exact dependency order for timeline export"
        );
    }
    return ordered;
}

std::vector<App::DocumentObject*> mapImportedNames(
    App::Document& target,
    const std::vector<App::DocumentObject*>& imported,
    const std::map<std::string, std::string>& nameMap,
    const std::vector<std::string>& sourceNames
)
{
    std::unordered_set<App::DocumentObject*> importedSet(
        imported.begin(),
        imported.end()
    );
    std::vector<App::DocumentObject*> mapped;
    mapped.reserve(sourceNames.size());
    std::unordered_set<App::DocumentObject*> seen;
    for (const auto& sourceName : sourceNames) {
        const auto found = nameMap.find(sourceName);
        if (found == nameMap.end()) {
            throw Base::RuntimeError(
                "The import did not map a declared source identity"
            );
        }
        auto* object = target.getObject(found->second.c_str());
        if (!object || !importedSet.contains(object)
            || !seen.insert(object).second) {
            throw Base::RuntimeError(
                "A mapped import identity is missing, duplicate, or outside "
                "the restored object set"
            );
        }
        mapped.push_back(object);
    }
    return mapped;
}

std::vector<TimelineObjectIdentity> captureIdentities(
    App::Document& target,
    const std::vector<App::DocumentObject*>& objects
)
{
    std::vector<TimelineObjectIdentity> identities;
    identities.reserve(objects.size());
    std::unordered_set<App::DocumentObject*> seen;
    for (auto* object : objects) {
        // containsObject() compares addresses without dereferencing them.
        // Only inspect an imported pointer after the exact target confirms
        // that the address still belongs to it.
        if (!object || !target.containsObject(object)
            || !seen.insert(object).second) {
            throw Base::RuntimeError(
                "A restored timeline identity was deleted, replaced, or "
                "duplicated before it could be captured"
            );
        }
        const char* name = object->getNameInDocument();
        const long id = object->getID();
        if (!name || name[0] == '\0' || id <= 0
            || object->getDocument() != &target
            || target.getObject(name) != object
            || target.getObjectByID(id) != object) {
            throw Base::RuntimeError(
                "A restored timeline identity is not exact in its target "
                "document"
            );
        }
        identities.push_back(
            TimelineObjectIdentity {
                .address = object,
                .name = name,
                .id = id,
            }
        );
    }
    return identities;
}

void validateCapturedObjects(
    App::Document& document,
    const std::vector<App::DocumentObject*>& objects,
    const std::vector<TimelineObjectIdentity>& identities
)
{
    if (objects.size() != identities.size()) {
        throw Base::RuntimeError(
            "A timeline import identity set changed before adoption"
        );
    }
    std::unordered_set<App::DocumentObject*> seen;
    for (std::size_t index = 0; index < identities.size(); ++index) {
        const auto& identity = identities[index];
        // Check target ownership by address before dereferencing the saved
        // pointer. This rejects deletion and allocator reuse unless all three
        // independently captured identities still resolve to the same object.
        if (!identity.address
            || objects[index] != identity.address
            || !document.containsObject(identity.address)
            || !seen.insert(identity.address).second
            || document.getObject(identity.name.c_str())
                != identity.address
            || document.getObjectByID(identity.id)
                != identity.address) {
            throw Base::RuntimeError(
                "A timeline import object was deleted, replaced, "
                "duplicated, or moved before adoption"
            );
        }
        if (identity.address->getDocument() != &document
            || identity.address->getID() != identity.id
            || identity.name
                != identity.address->getNameInDocument()) {
            throw Base::RuntimeError(
                "A timeline import object no longer matches its captured "
                "identity"
            );
        }
    }
}

App::Document& validateImportTarget(
    const TimelineImportResult& imported
)
{
    auto* document = imported.document;
    const auto& documents = App::GetApplication().getDocuments();
    if (!document
        || std::ranges::find(documents, document) == documents.end()
        || document->getName() != imported.documentName
        || document->Uid.getValueStr() != imported.documentUid) {
        throw Base::RuntimeError(
            "The timeline import target was closed or replaced"
        );
    }
    if (imported.transactionId == App::NullTransaction
        || document->getBookedTransactionID()
            != imported.transactionId
        || !App::GetApplication().transactionIsActive(
            imported.transactionId
        )
        || document->isPerformingTransaction()) {
        throw Base::RuntimeError(
            "The timeline import transaction was closed or replaced"
        );
    }
    return *document;
}

void validateImportResult(const TimelineImportResult& imported)
{
    auto& document = validateImportTarget(imported);
    const bool hasSourceState =
        !imported.sourceVisibility.empty()
        || !imported.sourceSuppression.empty();
    if ((imported.sourceVisibility.empty()
         != imported.sourceSuppression.empty())
        || (hasSourceState
            && (imported.sourceVisibility.size()
                    != imported.sourceOrder.size()
                || imported.sourceSuppression.size()
                    != imported.sourceOrder.size()))) {
        throw Base::RuntimeError(
            "The captured source timeline state is inconsistent"
        );
    }
    validateCapturedObjects(
        document,
        imported.objects,
        imported.objectIdentities
    );
    validateCapturedObjects(
        document,
        imported.selectedObjects,
        imported.selectedIdentities
    );
    validateCapturedObjects(
        document,
        imported.sourceOrder,
        imported.sourceOrderIdentities
    );
}

}  // namespace

TimelineExportPlan Gui::prepareTimelineExport(
    const std::vector<App::DocumentObject*>& selectedObjects,
    bool recursive
)
{
    if (selectedObjects.empty()) {
        throw Base::ValueError(
            "A timeline export requires at least one selected object"
        );
    }

    std::unordered_set<App::DocumentObject*> identities;
    identities.reserve(selectedObjects.size());
    insertLiveObjects(identities, selectedObjects);
    if (identities.size() != selectedObjects.size()) {
        throw Base::ValueError(
            "A timeline export selection contains duplicate objects"
        );
    }

    if (recursive) {
        insertLiveObjects(
            identities,
            App::Document::getDependencyList(
                selectedObjects,
                App::Document::DepNoXLinked
                    | App::Document::DepSort
            )
        );
    }

    while (true) {
        const auto sizeBefore = identities.size();
        std::map<App::DocumentTimeline*,
                 std::vector<App::DocumentObject*>>
            trackedByTimeline;
        for (auto* object : identities) {
            auto* timeline =
                App::DocumentTimeline::get(object->getDocument());
            if (timeline && isTracked(*timeline, object)) {
                trackedByTimeline[timeline].push_back(object);
            }
        }

        std::vector<App::DocumentObject*> semanticObjects;
        for (const auto& [timeline, selected] : trackedByTimeline) {
            auto closure = timeline->semanticCopyClosure(selected);
            semanticObjects.insert(
                semanticObjects.end(),
                closure.begin(),
                closure.end()
            );
        }
        insertLiveObjects(identities, semanticObjects);
        if (!semanticObjects.empty()) {
            insertLiveObjects(
                identities,
                App::Document::getDependencyList(
                    semanticObjects,
                    App::Document::DepNoXLinked
                        | App::Document::DepSort
                )
            );
        }
        if (identities.size() == sizeBefore) {
            break;
        }
    }

    TimelineExportPlan result;
    result.objects = dependencyOrder(identities);
    result.selectedNames.reserve(selectedObjects.size());
    for (const auto* selected : selectedObjects) {
        result.selectedNames.push_back(selected->getExportName(true));
    }

    std::set<App::Document*> visitedDocuments;
    for (auto* object : result.objects) {
        auto* document = object->getDocument();
        if (!visitedDocuments.insert(document).second) {
            continue;
        }
        const auto* timeline = App::DocumentTimeline::get(document);
        if (!timeline) {
            continue;
        }
        const auto& operations = timeline->Operations.getValues();
        const auto& visibility =
            timeline->VisibilityAtEnd.getValues();
        const auto& suppression =
            timeline->SuppressionAtEnd.getValues();
        if (visibility.size() != operations.size()
            || suppression.size() != operations.size()) {
            throw Base::RuntimeError(
                "A source timeline has inconsistent accepted state"
            );
        }
        for (std::size_t index = 0;
             index < operations.size();
             ++index) {
            const auto* operation = operations[index];
            if (identities.contains(
                    const_cast<App::DocumentObject*>(operation)
                )) {
                result.sourceOrderNames.push_back(
                    operation->getExportName(true)
                );
                result.sourceVisibility.push_back(
                    visibility.test(index)
                );
                result.sourceSuppression.push_back(
                    suppression.test(index)
                );
            }
        }
    }
    return result;
}

TimelineImportResult Gui::restoreTimelineImport(
    App::Document& target,
    std::istream& input,
    const std::vector<std::string>& selectedNames,
    const std::vector<std::string>& sourceOrderNames,
    const std::vector<bool>& sourceVisibility,
    const std::vector<bool>& sourceSuppression
)
{
    if (target.testStatus(App::Document::TempDoc)
        || target.getBookedTransactionID() == App::NullTransaction
        || target.isPerformingTransaction()) {
        throw Base::RuntimeError(
            "A user-facing timeline import requires one normal target "
            "document and one caller-owned transaction"
        );
    }

    TimelineImportResult result;
    result.document = &target;
    result.documentName = target.getName();
    result.documentUid = target.Uid.getValueStr();
    result.transactionId = target.getBookedTransactionID();
    validateImportTarget(result);
    App::DocumentTimeline::ensure(target);
    validateImportTarget(result);

    MergeDocuments importer(&target);
    result.objects = importer.importObjects(input);
    validateImportTarget(result);
    result.selectedObjects = mapImportedNames(
        target,
        result.objects,
        importer.getNameMap(),
        selectedNames
    );
    result.sourceOrder = mapImportedNames(
        target,
        result.objects,
        importer.getNameMap(),
        sourceOrderNames
    );
    result.sourceVisibility = sourceVisibility;
    result.sourceSuppression = sourceSuppression;
    result.objectIdentities =
        captureIdentities(target, result.objects);
    result.selectedIdentities =
        captureIdentities(target, result.selectedObjects);
    result.sourceOrderIdentities =
        captureIdentities(target, result.sourceOrder);
    validateImportResult(result);
    return result;
}

TimelineImportResult Gui::copyTimelineObjects(
    App::Document& target,
    const std::vector<App::DocumentObject*>& selectedObjects,
    bool recursive
)
{
    auto plan =
        prepareTimelineExport(selectedObjects, recursive);
    if (!target.testStatus(App::Document::TempDoc)
        && !target.isSaved()
        && App::PropertyXLink::hasXLink(plan.objects)) {
        throw Base::RuntimeError(
            "The target document must be saved before copying external "
            "links"
        );
    }

    unsigned int memorySize = 1000;
    for (const auto* object : plan.objects) {
        memorySize += object->getMemSize();
    }
    bool useBuffer = memorySize < 0xA00000;
    std::string bytes;
    if (useBuffer) {
        try {
            bytes.reserve(memorySize);
        }
        catch (const std::bad_alloc&) {
            useBuffer = false;
        }
    }

    auto* sourceDocument =
        plan.objects.front()->getDocument();
    if (useBuffer) {
        Base::StringOStreambuf outputBuffer(bytes);
        std::ostream output(&outputBuffer);
        {
            MergeDocuments exporter(sourceDocument);
            sourceDocument->exportObjects(
                plan.objects,
                output
            );
        }

        Base::StringIStreambuf inputBuffer(bytes);
        std::istream input(&inputBuffer);
        return restoreTimelineImport(
            target,
            input,
            plan.selectedNames,
            plan.sourceOrderNames,
            plan.sourceVisibility,
            plan.sourceSuppression
        );
    }

    Base::FileInfo file(
        App::Application::getTempFileName()
    );
    try {
        Base::ofstream output(
            file,
            std::ios::out | std::ios::binary
        );
        {
            MergeDocuments exporter(sourceDocument);
            sourceDocument->exportObjects(
                plan.objects,
                output
            );
        }
        output.close();

        Base::ifstream input(
            file,
            std::ios::in | std::ios::binary
        );
        auto result = restoreTimelineImport(
            target,
            input,
            plan.selectedNames,
            plan.sourceOrderNames,
            plan.sourceVisibility,
            plan.sourceSuppression
        );
        input.close();
        file.deleteFile();
        return result;
    }
    catch (...) {
        file.deleteFile();
        throw;
    }
}

void Gui::adoptTimelineImport(
    const TimelineImportResult& imported
)
{
    validateImportResult(imported);
    imported.document->adoptImportedTimelineOperations(
        imported.objects,
        imported.sourceOrder,
        imported.sourceVisibility,
        imported.sourceSuppression
    );
}

void Gui::deleteTimelineExportSource(
    const TimelineExportPlan& source
)
{
    if (source.objects.empty()) {
        throw Base::ValueError(
            "A timeline source deletion requires one complete export"
        );
    }
    // A prepared plan may outlive one of its source objects (for example, a
    // command callback can delete the selection before the move completes).
    // Resolve the source document by address membership before dereferencing
    // the saved pointer.
    auto* document = const_cast<App::Document*>(
        liveDocumentContaining(source.objects.front())
    );
    if (!document
        || document->getBookedTransactionID()
            == App::NullTransaction
        || !App::GetApplication().transactionIsActive(
            document->getBookedTransactionID()
        )
        || document->isPerformingTransaction()) {
        throw Base::RuntimeError(
            "Timeline source deletion requires one caller-owned transaction"
        );
    }

    std::unordered_set<App::DocumentObject*> closure;
    closure.reserve(source.objects.size());
    std::vector<TimelineObjectIdentity> identities;
    identities.reserve(source.objects.size());
    for (auto* object : source.objects) {
        if (!object || !document->containsObject(object)
            || object->getDocument() != document
            || !closure.insert(object).second
            || !object->getNameInDocument()
            || object->getNameInDocument()[0] == '\0'
            || object->getID() <= 0
            || document->getObject(object->getNameInDocument())
                != object
            || document->getObjectByID(object->getID())
                != object) {
            throw Base::RuntimeError(
                "The timeline source closure contains a missing, duplicate, "
                "or replaced identity"
            );
        }
        identities.push_back(
            {
                .address = object,
                .name = object->getNameInDocument(),
                .id = object->getID(),
            }
        );
    }

    for (const auto* object : closure) {
        for (const auto* dependent : object->getInList()) {
            if (!dependent
                || dependent->isDerivedFrom<App::DocumentTimeline>()
                || closure.contains(
                    const_cast<App::DocumentObject*>(dependent)
                )) {
                continue;
            }
            throw Base::RuntimeError(
                "A timeline source object is still used outside the moved "
                "closure"
            );
        }
    }

    std::vector<TimelineObjectIdentity> deletionOrder;
    deletionOrder.reserve(identities.size());
    auto remaining = identities;
    while (!remaining.empty()) {
        bool progressed = false;
        for (auto iterator = remaining.begin();
             iterator != remaining.end();) {
            const auto* object = iterator->address;
            const bool hasRemainingDependent =
                std::ranges::any_of(
                    object->getInList(),
                    [&remaining](
                        const App::DocumentObject* dependent
                    ) {
                        return dependent
                            && !dependent->isDerivedFrom<
                                App::DocumentTimeline>()
                            && std::ranges::any_of(
                                remaining,
                                [dependent](const auto& identity) {
                                    return identity.address
                                        == dependent;
                                }
                            );
                    }
                );
            if (hasRemainingDependent) {
                ++iterator;
                continue;
            }
            deletionOrder.push_back(*iterator);
            iterator = remaining.erase(iterator);
            progressed = true;
        }
        if (!progressed) {
            throw Base::RuntimeError(
                "The timeline source closure contains an undeletable "
                "dependency cycle"
            );
        }
    }

    // Mutation begins only after the complete closure and deletion order are
    // known-valid.
    for (const auto& identity : deletionOrder) {
        auto* object = document->getObjectByID(identity.id);
        if (!object) {
            // Removing a parent container may have removed an owned child.
            continue;
        }
        if (object != identity.address
            || document->getObject(identity.name.c_str()) != object
            || identity.name != object->getNameInDocument()) {
            throw Base::RuntimeError(
                "A timeline source identity changed during deletion"
            );
        }
        document->removeObject(identity.name.c_str());
        if (document->getObject(identity.name.c_str())) {
            throw Base::RuntimeError(
                "A timeline source object survived exact deletion"
            );
        }
    }
    for (const auto& identity : identities) {
        if (document->getObject(identity.name.c_str())
            || document->getObjectByID(identity.id)) {
            throw Base::RuntimeError(
                "Timeline source deletion left an orphaned semantic resource"
            );
        }
    }
}
