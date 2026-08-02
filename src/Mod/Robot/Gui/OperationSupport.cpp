// SPDX-License-Identifier: LGPL-2.1-or-later

#include "OperationSupport.h"

#include <algorithm>
#include <ranges>
#include <string_view>
#include <unordered_set>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentTimeline.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/Selection/Selection.h>
#include <Gui/ViewProvider.h>
#include <Mod/Robot/App/RobotObject.h>
#include <Mod/Robot/App/TrajectoryObject.h>

namespace
{

App::DocumentObject* resolvedSelectionObject(const Gui::SelectionSingleton::SelObj& selected) noexcept
{
    try {
        if (!selected.pObject || !selected.pDoc || selected.pObject->getDocument() != selected.pDoc
            || !RobotGui::OperationSupport::isUsableObject(selected.pObject)) {
            return nullptr;
        }
        auto* resolved = selected.pObject->getLinkedObject(true);
        return resolved ? resolved : selected.pObject;
    }
    catch (...) {
        return nullptr;
    }
}

template<typename T>
std::vector<T*> selectedResolvedObjects()
{
    std::vector<T*> result;
    std::unordered_set<T*> seen;
    const auto selection = Gui::Selection().getSelection();
    for (const auto& selected : selection) {
        auto* resolved = resolvedSelectionObject(selected);
        auto* typed = freecad_cast<T*>(resolved);
        if (typed && seen.insert(typed).second) {
            result.push_back(typed);
        }
    }
    return result;
}

bool selectionBelongsToActiveDocument() noexcept
{
    auto* active = App::GetApplication().getActiveDocument();
    if (!active) {
        return false;
    }
    try {
        const auto selection = Gui::Selection().getSelection();
        return !selection.empty()
            && std::ranges::all_of(selection, [active](const Gui::SelectionSingleton::SelObj& item) {
                   return item.pDoc == active;
               });
    }
    catch (...) {
        return false;
    }
}

}  // namespace

bool RobotGui::OperationSupport::hasCleanBoundary(const App::Document* document) noexcept
{
    return document && document->getBookedTransactionID() == App::NullTransaction
        && !document->hasPendingTransaction() && !document->isTransactionLocked()
        && !document->transacting();
}

App::Document* RobotGui::OperationSupport::cleanActiveDocument() noexcept
{
    auto* document = App::GetApplication().getActiveDocument();
    return document && hasCleanBoundary(document) && !Gui::Control().activeDialog() ? document
                                                                                    : nullptr;
}

bool RobotGui::OperationSupport::isUsableObject(const App::DocumentObject* object) noexcept
{
    try {
        if (!object || !object->getDocument() || !object->getNameInDocument()
            || !object->getDocument()->containsObject(object)
            || !App::DocumentTimeline::isObjectUsableAtCurrentPosition(object)) {
            return false;
        }
        const auto* resolved = object->getLinkedObject(true);
        return !resolved || resolved == object
            || App::DocumentTimeline::isObjectUsableAtCurrentPosition(resolved);
    }
    catch (...) {
        return false;
    }
}

Robot::RobotObject* RobotGui::OperationSupport::selectedRobot() noexcept
{
    if (!selectionBelongsToActiveDocument()) {
        return nullptr;
    }
    try {
        const auto robots = selectedResolvedObjects<Robot::RobotObject>();
        return robots.size() == 1 ? robots.front() : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

Robot::TrajectoryObject* RobotGui::OperationSupport::selectedTrajectory() noexcept
{
    if (!selectionBelongsToActiveDocument()) {
        return nullptr;
    }
    try {
        const auto trajectories = selectedResolvedObjects<Robot::TrajectoryObject>();
        return trajectories.size() == 1 ? trajectories.front() : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

RobotGui::OperationSupport::RobotTrajectorySelection RobotGui::OperationSupport::selectedRobotAndTrajectory() noexcept
{
    RobotTrajectorySelection result;
    result.activeDocument = App::GetApplication().getActiveDocument();
    if (!result.activeDocument || !selectionBelongsToActiveDocument()) {
        return {};
    }
    try {
        const auto robots = selectedResolvedObjects<Robot::RobotObject>();
        const auto trajectories = selectedResolvedObjects<Robot::TrajectoryObject>();
        if (robots.size() == 1 && trajectories.size() == 1) {
            result.robot = robots.front();
            result.trajectory = trajectories.front();
        }
    }
    catch (...) {
        return {};
    }
    return result;
}

std::vector<Robot::TrajectoryObject*> RobotGui::OperationSupport::selectedTrajectories()
{
    return selectionBelongsToActiveDocument() ? selectedResolvedObjects<Robot::TrajectoryObject>()
                                              : std::vector<Robot::TrajectoryObject*> {};
}

App::DocumentObject* RobotGui::OperationSupport::selectedToolShape(const Robot::RobotObject& robot) noexcept
{
    if (!selectionBelongsToActiveDocument()) {
        return nullptr;
    }
    try {
        App::DocumentObject* result = nullptr;
        std::size_t count = 0;
        for (const auto& selected : Gui::Selection().getSelection()) {
            auto* object = resolvedSelectionObject(selected);
            if (!object || object == &robot || object->isDerivedFrom<Robot::RobotObject>()) {
                continue;
            }
            if (!object->isDerivedFrom(Base::Type::fromName("Part::Feature"))
                && !object->isDerivedFrom(Base::Type::fromName("App::VRMLObject"))) {
                continue;
            }
            result = object;
            ++count;
        }
        return count == 1 ? result : nullptr;
    }
    catch (...) {
        return nullptr;
    }
}

std::vector<App::Document*> RobotGui::OperationSupport::mutationDocuments(
    App::Document& activeDocument,
    const std::vector<App::DocumentObject*>& mutatedObjects
)
{
    std::vector<App::Document*> result {&activeDocument};
    for (auto* object : mutatedObjects) {
        auto* document = object ? object->getDocument() : nullptr;
        if (!document) {
            throw Base::RuntimeError("A selected robot object is no longer attached to a document");
        }
        if (std::ranges::find(result, document) == result.end()) {
            result.push_back(document);
        }
    }
    return result;
}

void RobotGui::OperationSupport::requireCleanDocuments(
    App::Document& activeDocument,
    const std::vector<App::Document*>& documents
)
{
    if (App::GetApplication().getActiveDocument() != &activeDocument
        || Gui::Control().activeDialog() || documents.empty()) {
        throw Base::RuntimeError("Another task or document change is already in progress");
    }
    for (const auto* document : documents) {
        if (!hasCleanBoundary(document)) {
            throw Base::RuntimeError("Another task or document change is already in progress");
        }
    }
}

void RobotGui::OperationSupport::publishOperation(
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& resources
)
{
    auto* document = operation.getDocument();
    if (!document || !operation.getNameInDocument() || !document->containsObject(&operation)) {
        throw Base::RuntimeError("The robot operation is no longer attached to its document");
    }
    for (auto* resource : resources) {
        if (!resource || resource->getDocument() != document || !resource->getNameInDocument()
            || !document->containsObject(resource) || resource == &operation) {
            throw Base::ValueError(
                "Robot History resources must be distinct live objects in "
                "the operation document"
            );
        }
    }
    auto* timeline = App::DocumentTimeline::ensure(document);
    if (!timeline) {
        throw Base::RuntimeError("The robot operation could not create document History");
    }
    timeline->publishProvisionalOperationBlock(&operation, resources);
}

void RobotGui::OperationSupport::publishReplacingOperation(
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& replacedInputs,
    const std::vector<App::DocumentObject*>& resources
)
{
    publishOperation(operation, resources);
    setReplacedInputs(operation, replacedInputs);
}

void RobotGui::OperationSupport::setReplacedInputs(
    App::DocumentObject& operation,
    const std::vector<App::DocumentObject*>& replacedInputs
)
{
    auto* document = operation.getDocument();
    if (!document) {
        throw Base::RuntimeError("The robot operation is not attached to a document");
    }
    std::vector<App::DocumentObject*> exactInputs;
    for (auto* input : replacedInputs) {
        if (!input || input == &operation || input->getDocument() != document
            || !input->getNameInDocument() || !document->containsObject(input)
            || !isUsableObject(input)) {
            throw Base::ValueError(
                "A replaced robot input must be a distinct usable object "
                "in the operation document"
            );
        }
        if (std::ranges::find(exactInputs, input) == exactInputs.end()) {
            exactInputs.push_back(input);
        }
    }
    auto* roleProperty = operation.getPropertyByName(App::DocumentTimeline::RolePropertyName);
    if (!roleProperty) {
        roleProperty = operation.addDynamicProperty(
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName,
            "Timeline",
            "Document timeline classification",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    auto* role = dynamic_cast<App::PropertyString*>(roleProperty);
    if (!role) {
        throw Base::TypeError("Robot timeline role metadata has an incompatible type");
    }
    roleProperty->setStatus(App::Property::Hidden, true);
    roleProperty->setStatus(App::Property::LockDynamic, true);
    roleProperty->setStatus(App::Property::NoRecompute, true);
    if (const std::string_view value(role->getValue());
        !value.empty() && value != App::DocumentTimeline::OperationRole) {
        throw Base::ValueError("A robot replacement must be a root History operation");
    }
    role->setValue(App::DocumentTimeline::OperationRole);
    if (auto* ownerProperty = operation.getPropertyByName(App::DocumentTimeline::OwnerPropertyName)) {
        auto* owner = dynamic_cast<App::PropertyLinkHidden*>(ownerProperty);
        if (!owner || owner->getValue()) {
            throw Base::ValueError("A robot replacement cannot retain History owner metadata");
        }
        ownerProperty->setStatus(App::Property::Hidden, true);
        ownerProperty->setStatus(App::Property::LockDynamic, true);
        ownerProperty->setStatus(App::Property::NoRecompute, true);
    }
    auto* property = operation.getPropertyByName(App::DocumentTimeline::ReplacedInputsPropertyName);
    std::vector<App::DocumentObject*> previousInputs;
    if (property) {
        auto* previous = dynamic_cast<App::PropertyLinkListHidden*>(property);
        if (!previous) {
            throw Base::TypeError("Robot replaced-input metadata has an incompatible type");
        }
        previousInputs = previous->getValues();
    }
    if (!exactInputs.empty() || property) {
        if (!property) {
            property = operation.addDynamicProperty(
                "App::PropertyLinkListHidden",
                App::DocumentTimeline::ReplacedInputsPropertyName,
                "Timeline",
                "Visible inputs replaced by this robot operation",
                App::Prop_NoRecompute,
                true,
                true
            );
        }
        auto* links = dynamic_cast<App::PropertyLinkListHidden*>(property);
        if (!links) {
            throw Base::TypeError("Robot replaced-input metadata has an incompatible type");
        }
        property->setStatus(App::Property::Hidden, true);
        property->setStatus(App::Property::LockDynamic, true);
        property->setStatus(App::Property::NoRecompute, true);
        links->setValues(exactInputs);
    }

    std::unordered_set<App::DocumentObject*> hiddenInputs;
    if (auto* timeline = App::DocumentTimeline::get(document)) {
        for (auto* candidate : timeline->Operations.getValues()) {
            if (!candidate || !timeline->isOperationActive(candidate)) {
                continue;
            }
            const auto replacement = App::DocumentTimeline::replacementInputContract(candidate);
            if (!replacement.valid) {
                throw Base::RuntimeError("An active robot replacement contract is invalid");
            }
            for (auto* input : replacement.inputs) {
                hiddenInputs.insert(input);
                for (auto* owner = App::DocumentTimeline::timelineOwner(input); owner;
                     owner = App::DocumentTimeline::timelineOwner(owner)) {
                    hiddenInputs.insert(owner);
                }
            }
        }
    }
    for (auto* input : exactInputs) {
        hiddenInputs.insert(input);
        for (auto* owner = App::DocumentTimeline::timelineOwner(input); owner;
             owner = App::DocumentTimeline::timelineOwner(owner)) {
            hiddenInputs.insert(owner);
        }
    }

    std::unordered_set<App::DocumentObject*> affected;
    const auto addAffected = [&affected](App::DocumentObject* input) {
        for (auto* current = input; current; current = App::DocumentTimeline::timelineOwner(current)) {
            affected.insert(current);
        }
    };
    for (auto* input : previousInputs) {
        addAffected(input);
    }
    for (auto* input : exactInputs) {
        addAffected(input);
    }
    for (auto* input : affected) {
        const bool visible = !hiddenInputs.contains(input);
        if (auto* view = Gui::Application::Instance->getViewProvider(input)) {
            view->setVisible(visible);
        }
        else {
            input->Visibility.setValue(visible);
        }
    }
}

void RobotGui::OperationSupport::recompute(const std::vector<App::Document*>& documents)
{
    std::unordered_set<App::Document*> seen;
    for (auto* document : documents) {
        if (!document || !seen.insert(document).second) {
            continue;
        }
        document->recompute();
        if (document->hasPendingTransaction()
            && document->getBookedTransactionID() == App::NullTransaction) {
            throw Base::RuntimeError("Robot recompute left an unowned document transaction");
        }
    }
}

void RobotGui::OperationSupport::commit(Gui::ExactTransaction& transaction)
{
    if (!transaction.commit()) {
        throw Base::RuntimeError("The robot change could not be committed");
    }
}

void RobotGui::OperationSupport::ensureEditTransaction(
    App::DocumentObject& object,
    const char* transactionName
)
{
    auto* document = object.getDocument();
    auto* guiDocument = document && Gui::Application::Instance
        ? Gui::Application::Instance->getDocument(document)
        : nullptr;
    if (!document || !guiDocument || !object.getNameInDocument()
        || !document->containsObject(&object)) {
        throw Base::RuntimeError("The robot editor is no longer attached to its document");
    }

    const int existing = document->getBookedTransactionID();
    if (existing != App::NullTransaction) {
        if (!guiDocument->ownsEditTransaction(existing)) {
            throw Base::RuntimeError("The robot editor does not own the active transaction");
        }
        return;
    }
    if (document->hasPendingTransaction() || document->transacting()
        || document->isTransactionLocked()) {
        throw Base::RuntimeError("Another document change is already in progress");
    }

    const int transactionId = guiDocument->openCommand(
        transactionName && *transactionName ? transactionName : "Edit robot operation"
    );
    if (transactionId == App::NullTransaction
        || !guiDocument->adoptOwnedEditTransaction(transactionId)) {
        if (transactionId != App::NullTransaction) {
            App::GetApplication().abortTransaction(transactionId);
        }
        throw Base::RuntimeError("The robot editor could not establish an undoable transaction");
    }
}

bool RobotGui::OperationSupport::resetEdit(const App::DocumentObject& object) noexcept
{
    try {
        auto* document = object.getDocument();
        auto* guiDocument = document && Gui::Application::Instance
            ? Gui::Application::Instance->getDocument(document)
            : nullptr;
        if (!guiDocument) {
            return false;
        }
        guiDocument->resetEdit();
        return true;
    }
    catch (...) {
        return false;
    }
}

std::string RobotGui::OperationSupport::pythonString(const std::string& value)
{
    return "\"" + Base::InterpreterSingleton::strToPython(value) + "\"";
}
