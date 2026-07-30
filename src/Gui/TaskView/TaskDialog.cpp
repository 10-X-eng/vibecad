// SPDX-License-Identifier: LGPL-2.1-or-later
/***************************************************************************
 *   Copyright (c) 2009 Jürgen Riegel <juergen.riegel@web.de>              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/


#include <algorithm>
#include <map>
#include <utility>

#include <QMessageBox>


#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/ActiveObjectList.h>
#include <Gui/Application.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/Macro.h>
#include <Gui/MDIView.h>
#include <Gui/Selection/Selection.h>
#include <Gui/View3DInventor.h>
#include <Gui/ViewProviderDocumentObject.h>


#include "TaskDialog.h"
#include "TaskView.h"

using namespace Gui::TaskView;

struct TaskDialog::MacroCapture
{
    using Line = std::pair<Gui::MacroManager::LineType, std::string>;

    MacroCapture()
    {
        start();
    }

    void start()
    {
        if (redirector) {
            return;
        }
        redirector = std::make_unique<Gui::MacroManager::MacroRedirector>(
            [this](Gui::MacroManager::LineType type, const char* line) {
                if (line) {
                    lines.emplace_back(type, line);
                }
            }
        );
    }

    void stop()
    {
        redirector.reset();
    }

    void publish() noexcept
    {
        stop();
        if (!Gui::Application::Instance) {
            lines.clear();
            return;
        }
        auto* manager = Gui::Application::Instance->macroManager();
        if (!manager) {
            lines.clear();
            return;
        }
        auto accepted = std::move(lines);
        lines.clear();
        try {
            for (const auto& [type, line] : accepted) {
                manager->addLine(type, line.c_str());
            }
        }
        catch (const Base::Exception& error) {
            Base::Console().warning(
                "Native result was accepted, but its macro trace "
                "could not be published: %s\n",
                error.what()
            );
        }
        catch (const std::exception& error) {
            Base::Console().warning(
                "Native result was accepted, but its macro trace "
                "could not be published: %s\n",
                error.what()
            );
        }
        catch (...) {
            Base::Console().warning("Native result was accepted, but its macro trace "
                                    "could not be published.\n");
        }
    }

    void discard()
    {
        stop();
        lines.clear();
    }

    std::vector<Line> lines;
    std::unique_ptr<Gui::MacroManager::MacroRedirector> redirector;
};

struct TaskDialog::DialogState
{
    std::optional<InteractionState> interaction;
    std::map<std::string, std::vector<long>> pendingDurableResults;
};

struct TaskDialog::PendingInteractionRollback
{
    InteractionState interaction;
    std::shared_ptr<fastsignals::connection> stable;
    std::shared_ptr<fastsignals::connection> lockChanged;
    std::shared_ptr<fastsignals::connection> deleted;
    bool retrying {false};
};

using PendingRollbackKey = std::pair<const App::Document*, int>;

std::map<PendingRollbackKey, std::shared_ptr<TaskDialog::PendingInteractionRollback>>& TaskDialog::
    pendingInteractionRollbacks()
{
    static auto* pending
        = new std::map<PendingRollbackKey, std::shared_ptr<TaskDialog::PendingInteractionRollback>>;
    return *pending;
}


//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskDialog::TaskDialog()
    : QObject(nullptr)
    , pos(North)
    , escapeButton(true)
    , autoCloseTransaction(false)
    , autoCloseResetEdit(false)
    , autoCloseDeletedDocument(false)
    , autoCloseClosedView(false)
{}

TaskDialog::~TaskDialog()
{
    auto interaction = takeCommandInteractionState(this);
    if (interaction && interaction->macroCapture) {
        interaction->macroCapture->discard();
    }
    // Widget and ViewProvider teardown is not a reproducible modeling action.
    // Keep legacy destructors from leaking restore/delete lines after either
    // Accept or Cancel.
    Gui::MacroManager::MacroRedirector teardownTrace([](Gui::MacroManager::LineType, const char*) {});
    for (auto it : Content) {
        delete it;
        it = nullptr;
    }
    if (interaction) {
        try {
            restoreCommandInteractionState(interaction);
        }
        catch (const Base::Exception& error) {
            Base::Console().error(
                "Could not finish native task rollback during teardown: %s\n",
                error.what()
            );
        }
        catch (...) {
            Base::Console().error("Could not finish native task rollback during teardown.\n");
        }
    }
}

std::vector<TaskDialog::InteractionState>& TaskDialog::commandInvocationStack()
{
    // Commands and task dialogs are installed on the GUI thread. A stack is
    // required because composite commands invoke their selected child command
    // synchronously; the child must inherit the state from immediately before
    // the composite started modifying the GUI.
    static std::vector<InteractionState> states;
    return states;
}

std::map<TaskDialog*, TaskDialog::DialogState>& TaskDialog::dialogStates()
{
    // GUI task dialogs can be destroyed during application/static teardown.
    // Keep the registry alive for the process lifetime and erase every entry
    // when its dialog closes.
    static auto* states = new std::map<TaskDialog*, DialogState>;
    return *states;
}

std::map<Gui::Document*, TaskDialog::InteractionState>& TaskDialog::ownedEditInteractionStates()
{
    static auto* states = new std::map<Gui::Document*, InteractionState>;
    return *states;
}

namespace
{
void ensureExactTransactionCompletionObservation()
{
    static auto* connection = new fastsignals::connection(
        App::GetApplication().signalExactTransactionClosed.connect(
            [](int transactionId, bool, const std::vector<App::Document*>& documents) {
                for (const auto* document : documents) {
                    TaskDialog::recordCommandTransactionCompletion(document, transactionId);
                }
            }
        )
    );
    (void)connection;
}
}  // namespace

std::optional<TaskDialog::InteractionState> TaskDialog::takeCommandInteractionState(TaskDialog* dialog)
{
    auto& states = dialogStates();
    auto found = states.find(dialog);
    if (found == states.end()) {
        return std::nullopt;
    }
    auto interaction = std::move(found->second.interaction);
    states.erase(found);
    return interaction;
}

void TaskDialog::appendAcceptedMacroLines(
    TaskDialog* dialog,
    const std::vector<std::pair<int, std::string>>& lines
) noexcept
{
    if (lines.empty()) {
        return;
    }
    auto& states = dialogStates();
    auto found = states.find(dialog);
    try {
        if (found != states.end() && found->second.interaction
            && found->second.interaction->macroCapture) {
            auto& accepted = found->second.interaction->macroCapture->lines;
            for (const auto& [type, line] : lines) {
                accepted.emplace_back(static_cast<Gui::MacroManager::LineType>(type), line);
            }
            return;
        }

        // A specialized dialog may mark its result durable from inside
        // accept(). Its launch trace is then redirected into this accept
        // attempt; publish the completed attempt only after accept() returns.
        if (Gui::Application::Instance) {
            auto* manager = Gui::Application::Instance->macroManager();
            if (manager) {
                for (const auto& [type, line] : lines) {
                    manager->addLine(static_cast<Gui::MacroManager::LineType>(type), line.c_str());
                }
            }
        }
    }
    catch (const Base::Exception& error) {
        if (found != states.end() && found->second.interaction
            && found->second.interaction->macroCapture) {
            found->second.interaction->macroCapture->discard();
        }
        Base::Console().warning(
            "Native result was accepted, but its final macro trace "
            "could not be published: %s\n",
            error.what()
        );
    }
    catch (const std::exception& error) {
        if (found != states.end() && found->second.interaction
            && found->second.interaction->macroCapture) {
            found->second.interaction->macroCapture->discard();
        }
        Base::Console().warning(
            "Native result was accepted, but its final macro trace "
            "could not be published: %s\n",
            error.what()
        );
    }
    catch (...) {
        if (found != states.end() && found->second.interaction
            && found->second.interaction->macroCapture) {
            found->second.interaction->macroCapture->discard();
        }
        Base::Console().warning("Native result was accepted, but its final macro trace "
                                "could not be published.\n");
    }
}

void TaskDialog::clearPendingDurableResults(TaskDialog* dialog)
{
    auto& states = dialogStates();
    const auto found = states.find(dialog);
    if (found != states.end()) {
        found->second.pendingDurableResults.clear();
    }
}

void TaskDialog::pauseCommandMacroCapture(TaskDialog* dialog)
{
    auto& states = dialogStates();
    const auto found = states.find(dialog);
    if (found != states.end() && found->second.interaction
        && found->second.interaction->macroCapture) {
        found->second.interaction->macroCapture->stop();
    }
}

void TaskDialog::resumeCommandMacroCapture(TaskDialog* dialog)
{
    auto& states = dialogStates();
    const auto found = states.find(dialog);
    if (found != states.end() && found->second.interaction
        && found->second.interaction->macroCapture) {
        found->second.interaction->macroCapture->start();
    }
}

void TaskDialog::discardCommandMacroCapture(TaskDialog* dialog)
{
    auto& states = dialogStates();
    const auto found = states.find(dialog);
    if (found != states.end() && found->second.interaction
        && found->second.interaction->macroCapture) {
        found->second.interaction->macroCapture->discard();
    }
}

void TaskDialog::beginCommandInvocation()
{
    ensureExactTransactionCompletionObservation();
    InteractionState state;
    auto& invocationStack = commandInvocationStack();
    state.macroCapture = invocationStack.empty() ? std::make_shared<MacroCapture>()
                                                 : invocationStack.front().macroCapture;
    App::DocumentObject* activeBody = nullptr;
    if (Gui::Application::Instance) {
        if (auto* guiDocument = Gui::Application::Instance->activeDocument()) {
            if (auto* appDocument = guiDocument->getDocument()) {
                state.commandDocument = appDocument;
                state.commandDocumentName = appDocument->getName();
                state.editWasActiveAtInvocationStart = guiDocument->getEditViewProvider() != nullptr;
                state.commandDocumentModified = guiDocument->isModified();
                state.commandUndoEnabled = appDocument->getUndoMode() != 0;
                state.initialTransactionId = appDocument->getBookedTransactionID();
                for (auto* object : appDocument->getObjects()) {
                    if (object && object->getNameInDocument()) {
                        state.commandObjects.push_back({
                            object->getNameInDocument(),
                            object->getID(),
                        });
                    }
                }
                if (auto* activeObject = appDocument->getActiveObject()) {
                    if (activeObject->getNameInDocument()) {
                        state.commandActiveObjectName = activeObject->getNameInDocument();
                    }
                }
                // User-facing Undo may be disabled, but a modal editor still
                // needs an exact private rollback journal.  The journal is
                // discarded when the command closes, so this does not expose
                // an Undo entry or change the user's persistent preference.
                if (!state.commandUndoEnabled && state.initialTransactionId == App::NullTransaction
                    && !appDocument->hasPendingTransaction()) {
                    appDocument->setUndoMode(1);
                    state.temporaryRollbackJournal = appDocument->getUndoMode() != 0;
                }
            }
        }

        if (auto* activeView = Gui::Application::Instance->activeView()) {
            if (auto* viewDocument = activeView->getAppDocument()) {
                state.activeBodyDocumentName = viewDocument->getName();
                state.hadActiveBody = activeView->hasActiveObject(PDBODYKEY);
                App::DocumentObject* activeBodyRoot = nullptr;
                std::string activeBodySubname;
                activeBody = activeView->getActiveObject<App::DocumentObject*>(
                    PDBODYKEY,
                    &activeBodyRoot,
                    &activeBodySubname
                );
                if (activeBodyRoot && activeBodyRoot->getNameInDocument()) {
                    state.activeBodyRootName = activeBodyRoot->getNameInDocument();
                    state.activeBodySubname = std::move(activeBodySubname);
                }
            }
        }
    }

    state.selection = Gui::Selection().getSelectionEx(
        "*",
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::NoResolve
    );

    auto captureObjectVisibility = [&state](const App::DocumentObject* object) {
        if (!object || !object->getDocument() || !object->getNameInDocument()) {
            return;
        }
        const auto alreadyCaptured = std::ranges::any_of(
            state.visibility,
            [object](const InteractionState::VisibilityState& visibility) {
                return visibility.documentName == object->getDocument()->getName()
                    && visibility.objectName == object->getNameInDocument();
            }
        );
        if (alreadyCaptured) {
            return;
        }
        auto* viewProvider
            = Gui::Application::Instance->getViewProvider<Gui::ViewProviderDocumentObject>(object);
        if (viewProvider) {
            state.visibility.push_back({
                object->getDocument()->getName(),
                object->getNameInDocument(),
                viewProvider->Visibility.getValue(),
            });
        }
    };
    auto captureVisibilityTree = [&captureObjectVisibility](const App::DocumentObject* root) {
        if (!root) {
            return;
        }
        captureObjectVisibility(root);
        for (auto* dependency : root->getOutListRecursive()) {
            captureObjectVisibility(dependency);
        }
    };

    // Capture the complete command document before activation can create a
    // task panel or let a ViewProvider temporarily hide/show objects.  A
    // dialog-time snapshot records those editor presentation changes as the
    // supposed original state, so Cancel cannot restore the user's viewport.
    if (state.commandDocument) {
        for (auto* object : state.commandDocument->getObjects()) {
            captureObjectVisibility(object);
        }
    }

    // Active/selected dependencies may belong to linked documents. Preserve
    // their presentation too, without broadening the command transaction.
    captureVisibilityTree(activeBody);
    for (const auto& selected : state.selection) {
        captureVisibilityTree(selected.getObject());
    }
    invocationStack.push_back(std::move(state));
}

void TaskDialog::endCommandInvocation()
{
    endCommandInvocation(true);
}

void TaskDialog::endCommandInvocation(bool commandSucceeded)
{
    auto& states = commandInvocationStack();
    if (states.empty()) {
        return;
    }

    InteractionState state = std::move(states.back());
    states.pop_back();
    const bool outermost = states.empty();
    if (outermost && !state.taskAdopted && state.macroCapture) {
        state.macroCapture->stop();
    }
    if (outermost && !state.taskAdopted && !state.commandTransactionCompleted) {
        if (Gui::Application::Instance && !state.commandDocumentName.empty()) {
            auto* guiDocument = Gui::Application::Instance->getDocument(
                state.commandDocumentName.c_str()
            );
            auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
            const int currentTransactionId = document ? document->getBookedTransactionID()
                                                      : App::NullTransaction;
            if (currentTransactionId != state.initialTransactionId) {
                state.commandTransactionId = currentTransactionId;
            }
        }
    }
    bool hasPostCompletionSuccessor = false;
    if (outermost && !state.taskAdopted && state.commandTransactionCompleted
        && Gui::Application::Instance && !state.commandDocumentName.empty()) {
        if (auto* guiDocument
            = Gui::Application::Instance->getDocument(state.commandDocumentName.c_str())) {
            const int currentTransactionId = guiDocument->getDocument()
                ? guiDocument->getDocument()->getBookedTransactionID()
                : App::NullTransaction;
            hasPostCompletionSuccessor = currentTransactionId != App::NullTransaction
                && currentTransactionId != state.commandTransactionId
                && currentTransactionId != state.initialTransactionId;
        }
    }
    if (outermost && !state.taskAdopted && !commandSucceeded && hasPostCompletionSuccessor) {
        // Exact T has already rolled back, and its close callback opened S.
        // S now owns the live GUI/model presentation; replaying T's launch
        // checkpoint would overwrite selection, visibility, and active-object
        // changes that belong to S.
        if (state.macroCapture) {
            state.macroCapture->discard();
        }
        restoreOriginalUndoMode(state);
        return;
    }
    if (outermost && !state.taskAdopted && !commandSucceeded) {
        restoreCommandInteractionState(state);
        return;
    }
    bool transactionFinished = true;
    if (state.temporaryRollbackJournal && !state.taskAdopted) {
        transactionFinished = finishCommandTransaction(state, commandSucceeded);
        restoreOriginalUndoMode(state);
    }
    if (outermost && !state.taskAdopted && state.macroCapture) {
        if (commandSucceeded && transactionFinished) {
            state.macroCapture->publish();
        }
        else {
            state.macroCapture->discard();
        }
    }
}

bool TaskDialog::hasOwnedEnclosingTransaction(const App::Document* document)
{
    return ownedEnclosingTransactionId(document) != App::NullTransaction;
}

int TaskDialog::ownedEnclosingTransactionId(const App::Document* document)
{
    const auto& states = commandInvocationStack();
    if (!document || states.empty()) {
        return App::NullTransaction;
    }
    const auto& outermost = states.front();
    const int currentTransactionId = document->getBookedTransactionID();
    if (outermost.commandDocumentName != document->getName()
        || outermost.initialTransactionId != App::NullTransaction
        || outermost.commandTransactionCompleted || currentTransactionId == App::NullTransaction) {
        return App::NullTransaction;
    }
    return currentTransactionId;
}

bool TaskDialog::ownsCommandTransaction(int transactionId) const
{
    if (transactionId == App::NullTransaction || !Gui::Application::Instance) {
        return false;
    }

    const auto& states = dialogStates();
    const auto found = states.find(const_cast<TaskDialog*>(this));
    if (found == states.end() || !found->second.interaction) {
        return false;
    }

    const auto& interaction = *found->second.interaction;
    if (interaction.commandTransactionCompleted || interaction.commandTransactionId != transactionId
        || interaction.commandDocumentName.empty()) {
        return false;
    }

    auto* guiDocument = Gui::Application::Instance->getDocument(
        interaction.commandDocumentName.c_str()
    );
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    return document && document == interaction.commandDocument
        && document->getBookedTransactionID() == transactionId
        && App::GetApplication().transactionIsActive(transactionId);
}

void TaskDialog::markOwnedEnclosingTransactionAdopted(const App::Document* document, int transactionId)
{
    auto& states = commandInvocationStack();
    if (!document || states.empty() || transactionId == App::NullTransaction
        || ownedEnclosingTransactionId(document) != transactionId) {
        return;
    }
    auto& outermost = states.front();
    outermost.taskAdopted = true;
    outermost.commandTransactionId = transactionId;
}

void TaskDialog::recordCommandTransactionCompletion(const App::Document* document, int transactionId)
{
    if (!document || transactionId == App::NullTransaction) {
        return;
    }
    for (auto& state : commandInvocationStack()) {
        if (state.commandDocument != document) {
            continue;
        }
        // A transaction which predated this invocation is not command-owned.
        // Its exact close is still recorded so a synchronous successor cannot
        // be inferred as command-owned by endCommandInvocation().
        if (state.initialTransactionId != transactionId) {
            state.commandTransactionId = transactionId;
        }
        state.commandTransactionCompleted = true;
    }
}

void TaskDialog::adoptOwnedEditCommandInteraction(Gui::Document* document, int transactionId)
{
    if (!document || transactionId == App::NullTransaction) {
        return;
    }
    // A real TaskDialog already owns the richer lifecycle for this exact
    // interaction. The no-panel registry is only for editor ViewProviders
    // which do not install a task panel.
    const auto panelOwnsInteraction
        = std::ranges::any_of(dialogStates(), [document, transactionId](const auto& entry) {
              return entry.second.interaction
                  && entry.second.interaction->commandDocumentName
                  == document->getDocument()->getName()
                  && entry.second.interaction->commandTransactionId == transactionId;
          });
    if (panelOwnsInteraction) {
        return;
    }

    auto& invocations = commandInvocationStack();
    if (invocations.empty()) {
        return;
    }
    auto& outermost = invocations.front();
    if (outermost.commandDocumentName != document->getDocument()->getName()
        || outermost.commandTransactionId != transactionId) {
        return;
    }
    ownedEditInteractionStates().insert_or_assign(document, outermost);
}

void TaskDialog::finishOwnedEditCommandInteraction(
    Gui::Document* document,
    int transactionId,
    bool cancelled,
    bool transactionFinished
)
{
    auto& states = ownedEditInteractionStates();
    const auto found = states.find(document);
    if (found == states.end() || found->second.commandTransactionId != transactionId
        || !transactionFinished) {
        return;
    }

    InteractionState state = std::move(found->second);
    states.erase(found);
    auto* appDocument = document->getDocument();
    const int currentTransactionId = appDocument ? appDocument->getBookedTransactionID()
                                                 : App::NullTransaction;
    const bool successorIsActive = currentTransactionId != App::NullTransaction
        && currentTransactionId != transactionId;
    if (cancelled && !successorIsActive) {
        restoreCommandInteractionState(state);
        return;
    }

    if (state.macroCapture) {
        if (cancelled) {
            state.macroCapture->discard();
        }
        else {
            state.macroCapture->publish();
        }
    }
    restoreOriginalUndoMode(state);
}

void TaskDialog::discardOwnedEditCommandInteraction(Gui::Document* document)
{
    if (!document) {
        return;
    }
    auto& states = ownedEditInteractionStates();
    const auto found = states.find(document);
    if (found == states.end()) {
        return;
    }
    if (found->second.macroCapture) {
        found->second.macroCapture->discard();
    }
    states.erase(found);
}

void TaskDialog::adoptCommandInteractionState(App::Document* document)
{
    auto& dialogState = dialogStates()[this];
    dialogState.interaction.reset();
    auto& states = commandInvocationStack();
    if (!document || states.empty()) {
        return;
    }
    // A composite action may invoke a child command. The outermost state is
    // the state before the user's ribbon action began; an inner command must
    // not replace it with state already modified by its parent.
    auto& state = states.front();
    if (state.commandDocumentName == document->getName()) {
        state.taskAdopted = true;
        const int currentTransactionId = document->getBookedTransactionID();
        state.commandTransactionId = currentTransactionId != state.initialTransactionId
            ? currentTransactionId
            : App::NullTransaction;
        if (state.commandTransactionId != App::NullTransaction) {
            const auto lockStandaloneTransaction = [&] {
                document->lockTransaction();
                state.standaloneTransactionLocked = true;
                markOwnedEnclosingTransactionAdopted(document, state.commandTransactionId);
            };
            if (state.editWasActiveAtInvocationStart) {
                // A task opened inside a persistent editor (notably Assembly)
                // owns T, but it does not own or close that pre-existing edit
                // context. Lock T directly and let the panel close it.
                lockStandaloneTransaction();
            }
            else if (auto* guiDocument = Gui::Application::Instance->getDocument(document);
                     guiDocument) {
                if (guiDocument->adoptEditTransaction(state.commandTransactionId)) {
                    markOwnedEnclosingTransactionAdopted(document, state.commandTransactionId);
                }
                else if (!guiDocument->getEditViewProvider() && !document->isTransactionLocked()) {
                    // A standalone task panel (for example the Shaft wizard)
                    // has no ViewProvider edit session to own T. Keep its
                    // exact launch transaction locked until the panel's
                    // common Accept/Cancel finalization commits or rolls it
                    // back. A task-specific ExactTransaction already holds
                    // its own lock and finalizes itself; taking a second lock
                    // here would prevent that owner from closing T.
                    lockStandaloneTransaction();
                }
            }
        }
        dialogState.interaction = state;
    }
}

bool TaskDialog::restoreCommandInteractionState(const std::optional<InteractionState>& state)
{
    if (!state || !Gui::Application::Instance) {
        return true;
    }

    // Teardown has already released task widgets, selection gates, and edit
    // ViewProviders.  It is now safe to replay the exact command journal.
    if (state->macroCapture) {
        state->macroCapture->discard();
    }
    Gui::MacroManager::MacroRedirector rollbackTrace([](Gui::MacroManager::LineType, const char*) {});
    if (!finishCommandTransaction(*state, false)) {
        // The provisional model is still live. Restoring selection,
        // visibility, active objects, modified state, or UndoMode now would
        // publish a presentation that disagrees with the actual document.
        // Preserve the complete checkpoint and exact transaction ownership
        // until the same document can close T.
        retainPendingInteractionRollback(*state);
        return false;
    }

    // The private journal is complete for UndoMode=0 commands.  Keep the
    // conservative ID-based cleanup only as a fallback if a journal could not
    // be established (for example, a locked document).
    if (!state->commandUndoEnabled && !state->temporaryRollbackJournal) {
        removeUnacceptedObjects(*state);
    }

    for (const auto& visibility : state->visibility) {
        try {
            auto* guiDocument = Gui::Application::Instance->getDocument(
                visibility.documentName.c_str()
            );
            auto* appDocument = guiDocument ? guiDocument->getDocument() : nullptr;
            auto* object = appDocument ? appDocument->getObject(visibility.objectName.c_str())
                                       : nullptr;
            auto* viewProvider = object
                ? Gui::Application::Instance->getViewProvider<Gui::ViewProviderDocumentObject>(object)
                : nullptr;
            if (viewProvider) {
                viewProvider->Visibility.setValue(visibility.visible);
            }
        }
        catch (const Base::Exception& error) {
            Base::Console().error(
                "Could not restore visibility for '%s': %s\n",
                visibility.objectName.c_str(),
                error.what()
            );
        }
        catch (...) {
            Base::Console().error(
                "Could not restore visibility for '%s'.\n",
                visibility.objectName.c_str()
            );
        }
    }

    try {
        Gui::SelectionLogDisabler selectionLogDisabler(true);
        auto& selection = Gui::Selection();
        selection.clearSelection();
        for (const auto& selected : state->selection) {
            auto* guiDocument = Gui::Application::Instance->getDocument(selected.getDocName());
            auto* appDocument = guiDocument ? guiDocument->getDocument() : nullptr;
            if (!appDocument || !appDocument->getObject(selected.getFeatName())) {
                continue;
            }
            selection.addSelection(selected);
        }
    }
    catch (const Base::Exception& error) {
        Base::Console().error("Could not restore native task selection: %s\n", error.what());
    }
    catch (...) {
        Base::Console().error("Could not restore native task selection.\n");
    }

    auto* commandGuiDocument = Gui::Application::Instance->getDocument(
        state->commandDocumentName.c_str()
    );
    auto* commandDocument = commandGuiDocument ? commandGuiDocument->getDocument() : nullptr;
    if (commandDocument) {
        try {
            auto* activeObject = state->commandActiveObjectName.empty()
                ? nullptr
                : commandDocument->getObject(state->commandActiveObjectName.c_str());
            commandDocument->setActiveObject(activeObject);
        }
        catch (const Base::Exception& error) {
            Base::Console().error("Could not restore native task active object: %s\n", error.what());
        }
        catch (...) {
            Base::Console().error("Could not restore native task active object.\n");
        }
    }

    if (!state->activeBodyDocumentName.empty()) {
        try {
            auto* guiDocument = Gui::Application::Instance->getDocument(
                state->activeBodyDocumentName.c_str()
            );
            auto* appDocument = guiDocument ? guiDocument->getDocument() : nullptr;
            auto* activeView = guiDocument ? guiDocument->getActiveView() : nullptr;
            if (appDocument && activeView) {
                auto* activeBodyRoot = state->activeBodyRootName.empty()
                    ? nullptr
                    : appDocument->getObject(state->activeBodyRootName.c_str());
                if (state->hadActiveBody && activeBodyRoot) {
                    activeView->setActiveObject(
                        activeBodyRoot,
                        PDBODYKEY,
                        state->activeBodySubname.c_str()
                    );
                }
                else {
                    activeView->setActiveObject(nullptr, PDBODYKEY);
                }
            }
        }
        catch (const Base::Exception& error) {
            Base::Console().error("Could not restore native task active Body: %s\n", error.what());
        }
        catch (...) {
            Base::Console().error("Could not restore native task active Body.\n");
        }
    }

    // Selection and ViewProvider restoration can themselves mark the GUI
    // document dirty.  Cancel must return the document to the exact saved/
    // modified state it had before the command.
    if (commandGuiDocument) {
        try {
            commandGuiDocument->setModified(state->commandDocumentModified);
        }
        catch (...) {
            Base::Console().error("Could not restore native task document state.\n");
        }
    }

    restoreOriginalUndoMode(*state);
    return true;
}

void TaskDialog::retainPendingInteractionRollback(const InteractionState& state)
{
    auto* document = state.commandDocument;
    const int transactionId = state.commandTransactionId;
    if (!document || transactionId == App::NullTransaction) {
        Base::Console().error("Could not retain failed native rollback without an exact "
                              "document and transaction identity.\n");
        return;
    }

    const PendingRollbackKey key {document, transactionId};
    auto& pending = pendingInteractionRollbacks();
    if (pending.contains(key)) {
        return;
    }

    auto rollback = std::make_shared<PendingInteractionRollback>();
    rollback->interaction = state;
    rollback->stable = std::make_shared<fastsignals::connection>();
    rollback->lockChanged = std::make_shared<fastsignals::connection>();
    rollback->deleted = std::make_shared<fastsignals::connection>();
    pending.emplace(key, rollback);

    *rollback->stable = document->signalBecameStable.connect([key](const App::Document&) {
        TaskDialog::retryPendingInteractionRollback(key.first, key.second);
    });
    *rollback->lockChanged = document->signalTransactionLockChanged.connect(
        [key](const App::Document&) {
            TaskDialog::retryPendingInteractionRollback(key.first, key.second);
        }
    );
    *rollback->deleted = App::GetApplication().signalDeleteDocument.connect(
        [key](const App::Document& deleted) {
            if (&deleted != key.first) {
                return;
            }
            auto& pending = pendingInteractionRollbacks();
            const auto found = pending.find(key);
            if (found != pending.end() && found->second->interaction.macroCapture) {
                found->second->interaction.macroCapture->discard();
            }
            pending.erase(key);
        }
    );

    Base::Console().error(
        "Native task rollback transaction %d remains owned and will retry "
        "when its document becomes closable.\n",
        transactionId
    );
}

void TaskDialog::retryPendingInteractionRollback(const App::Document* document, int transactionId)
{
    const PendingRollbackKey key {document, transactionId};
    auto& pending = pendingInteractionRollbacks();
    const auto found = pending.find(key);
    if (found == pending.end() || found->second->retrying) {
        return;
    }
    const auto rollback = found->second;
    rollback->retrying = true;
    if (!finishCommandTransaction(rollback->interaction, false)) {
        rollback->retrying = false;
        return;
    }

    InteractionState interaction = std::move(rollback->interaction);
    interaction.commandTransactionCompleted = true;
    pending.erase(key);
    (void)restoreCommandInteractionState(interaction);
}

void TaskDialog::removeUnacceptedObjects(const InteractionState& state)
{
    // With Undo disabled, abortTransaction() deliberately has no journal to
    // replay. Cancel must still remove the provisional objects created by the
    // command. Existing objects are identified by immutable document IDs and
    // are never candidates for removal.
    if (state.commandUndoEnabled || state.commandDocumentName.empty()) {
        return;
    }

    auto* guiDocument = Gui::Application::Instance->getDocument(state.commandDocumentName.c_str());
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    if (!document) {
        return;
    }

    std::vector<InteractionState::ObjectIdentity> provisional;
    for (auto* object : document->getObjects()) {
        if (!object || !object->getNameInDocument()) {
            continue;
        }
        const bool existedBefore = std::ranges::any_of(
            state.commandObjects,
            [object](const InteractionState::ObjectIdentity& initial) {
                return initial.id == object->getID() && initial.name == object->getNameInDocument();
            }
        );
        if (!existedBefore) {
            provisional.push_back({
                object->getNameInDocument(),
                object->getID(),
            });
        }
    }

    for (auto it = provisional.rbegin(); it != provisional.rend(); ++it) {
        try {
            auto* object = document->getObjectByID(it->id);
            if (!object) {
                continue;
            }
            if (!object->getNameInDocument() || it->name != object->getNameInDocument()) {
                Base::Console().error(
                    "Task cancel refused to remove provisional object "
                    "'%s' (id %ld): identity changed\n",
                    it->name.c_str(),
                    it->id
                );
                continue;
            }
            document->removeObject(it->name.c_str());
        }
        catch (const Base::Exception& error) {
            Base::Console().error(
                "Task cancel could not remove provisional object "
                "'%s' (id %ld): %s\n",
                it->name.c_str(),
                it->id,
                error.what()
            );
        }
        catch (...) {
            Base::Console().error(
                "Task cancel could not remove provisional object "
                "'%s' (id %ld)\n",
                it->name.c_str(),
                it->id
            );
        }
    }

    try {
        document->recompute();
    }
    catch (const Base::Exception& error) {
        Base::Console().error("Task cancel could not recompute fallback cleanup: %s\n", error.what());
    }
}

bool TaskDialog::finishCommandTransaction(const InteractionState& state, bool commit)
{
    if (!Gui::Application::Instance || state.commandDocumentName.empty()) {
        return true;
    }
    auto* guiDocument = Gui::Application::Instance->getDocument(state.commandDocumentName.c_str());
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    if (!document) {
        return true;
    }

    try {
        if (state.commandTransactionCompleted) {
            return true;
        }
        const int currentId = document->getBookedTransactionID();
        if (state.commandTransactionId != App::NullTransaction) {
            // Never close a transaction that replaced the one adopted by this
            // task panel.
            if (currentId != state.commandTransactionId) {
                const bool transactionInactive = !App::GetApplication().transactionIsActive(
                    state.commandTransactionId
                );
                if (transactionInactive && state.standaloneTransactionLocked) {
                    document->unlockTransaction();
                }
                return transactionInactive;
            }
            if (guiDocument->ownsEditTransaction(state.commandTransactionId)) {
                const bool closed
                    = guiDocument->finishEditTransaction(state.commandTransactionId, commit);
                if (closed) {
                    recordCommandTransactionCompletion(document, state.commandTransactionId);
                }
                return closed;
            }
            if (state.standaloneTransactionLocked) {
                document->unlockTransaction();
            }
            const bool closed = commit
                ? App::GetApplication().commitTransaction(state.commandTransactionId)
                : App::GetApplication().abortTransaction(state.commandTransactionId);
            if (closed) {
                recordCommandTransactionCompletion(document, state.commandTransactionId);
            }
            if (!closed) {
                if (state.standaloneTransactionLocked
                    && document->getBookedTransactionID() == state.commandTransactionId
                    && App::GetApplication().transactionIsActive(state.commandTransactionId)) {
                    document->lockTransaction();
                }
                Base::Console().error(
                    "Could not %s native command transaction %d\n",
                    commit ? "commit" : "roll back",
                    state.commandTransactionId
                );
            }
            return closed;
        }
        else if (state.temporaryRollbackJournal) {
            // A changed synchronous command must have captured its exact
            // transaction ID at the outer command boundary. Never fall back
            // to closing whichever document-local transaction is current.
            if (currentId != App::NullTransaction || document->hasPendingTransaction()) {
                Base::Console().error("Temporary native command journal has no owned "
                                      "transaction ID\n");
                return false;
            }
        }
        return true;
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not %s native command transaction: %s\n",
            commit ? "commit" : "roll back",
            error.what()
        );
        return false;
    }
    catch (...) {
        Base::Console().error(
            "Could not %s native command transaction\n",
            commit ? "commit" : "roll back"
        );
        return false;
    }
}

void TaskDialog::restoreOriginalUndoMode(const InteractionState& state)
{
    if (!state.temporaryRollbackJournal || !Gui::Application::Instance
        || state.commandDocumentName.empty()) {
        return;
    }
    auto* guiDocument = Gui::Application::Instance->getDocument(state.commandDocumentName.c_str());
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    try {
        if (document && document->getUndoMode() != 0
            && document->getBookedTransactionID() == App::NullTransaction
            && !document->hasPendingTransaction()) {
            document->setUndoMode(0);
        }
        else if (document && document->getUndoMode() != 0) {
            // A synchronous close callback may legitimately open successor S.
            // S is not this command's transaction, so leave it untouched and
            // restore the user's original UndoMode only after S becomes
            // stable. Keep one self-disconnecting callback per document.
            using DeferredConnection = std::shared_ptr<fastsignals::connection>;
            struct DeferredUndoModeRestore
            {
                DeferredConnection stable;
                DeferredConnection deleted;
            };
            static std::map<const App::Document*, DeferredUndoModeRestore> pendingUndoModeRestorations;
            if (!pendingUndoModeRestorations.contains(document)) {
                DeferredUndoModeRestore deferred {
                    std::make_shared<fastsignals::connection>(),
                    std::make_shared<fastsignals::connection>(),
                };
                pendingUndoModeRestorations.emplace(document, deferred);
                *deferred.stable = document->signalBecameStable.connect(
                    [document](const App::Document& stableDocument) {
                        if (&stableDocument != document
                            || stableDocument.getBookedTransactionID() != App::NullTransaction
                            || stableDocument.hasPendingTransaction()) {
                            return;
                        }
                        auto& pending = pendingUndoModeRestorations;
                        const auto found = pending.find(document);
                        if (found == pending.end()) {
                            return;
                        }
                        const auto stableConnection = found->second.stable;
                        const auto deleteConnection = found->second.deleted;
                        try {
                            const_cast<App::Document&>(stableDocument).setUndoMode(0);
                        }
                        catch (const Base::Exception& error) {
                            Base::Console().error(
                                "Could not restore native command Undo "
                                "mode after successor transaction: %s\n",
                                error.what()
                            );
                            return;
                        }
                        stableConnection->disconnect();
                        deleteConnection->disconnect();
                        pending.erase(found);
                    }
                );
                *deferred.deleted = App::GetApplication().signalDeleteDocument.connect(
                    [document](const App::Document& deletedDocument) {
                        if (&deletedDocument != document) {
                            return;
                        }
                        auto& pending = pendingUndoModeRestorations;
                        const auto found = pending.find(document);
                        if (found == pending.end()) {
                            return;
                        }
                        const auto stableConnection = found->second.stable;
                        const auto deleteConnection = found->second.deleted;
                        stableConnection->disconnect();
                        deleteConnection->disconnect();
                        pending.erase(found);
                    }
                );
            }
        }
    }
    catch (const Base::Exception& error) {
        Base::Console().error("Could not restore native command Undo mode: %s\n", error.what());
    }
    catch (...) {
        Base::Console().error("Could not restore native command Undo mode.\n");
    }
}

void TaskDialog::markCommandInteractionStateDurable(
    const std::vector<App::DocumentObject*>& acceptedResults
)
{
    auto& states = dialogStates();
    auto found = states.find(this);
    auto addResultIdentity = [](auto& target, const std::string& documentName, long objectId) {
        auto& resultIds = target[documentName];
        if (std::ranges::find(resultIds, objectId) == resultIds.end()) {
            resultIds.push_back(objectId);
        }
    };

    std::map<std::string, std::vector<long>> resultIdentities;
    for (auto* result : acceptedResults) {
        if (result && result->getDocument() && result->isAttachedToDocument()) {
            addResultIdentity(resultIdentities, result->getDocument()->getName(), result->getID());
        }
    }

    // accept() executes under a short-lived macro redirector. Defer durable
    // publication until it returns so the command-launch trace remains before
    // the accepted parameter/finalization trace during replay.
    if (property("taskview_accept_or_reject").toBool()) {
        auto& pending = states[this].pendingDurableResults;
        for (const auto& [documentName, objectIds] : resultIdentities) {
            for (long objectId : objectIds) {
                addResultIdentity(pending, documentName, objectId);
            }
        }
        return;
    }

    if (found != states.end()) {
        for (const auto& [documentName, objectIds] : found->second.pendingDurableResults) {
            for (long objectId : objectIds) {
                addResultIdentity(resultIdentities, documentName, objectId);
            }
        }
    }

    std::map<App::Document*, std::vector<long>> resultsByDocument;
    for (const auto& [documentName, objectIds] : resultIdentities) {
        try {
            if (auto* document = App::GetApplication().getDocument(documentName.c_str())) {
                resultsByDocument.emplace(document, objectIds);
            }
        }
        catch (...) {
            Base::Console().warning(
                "Accepted native task document '%s' is no longer open.\n",
                documentName.c_str()
            );
        }
    }

    if (Gui::Application::Instance) {
        for (const auto& [document, resultIds] : resultsByDocument) {
            Gui::Application::Instance->prepareDurableTaskResults(*document, resultIds);
        }
    }

    if (found != states.end() && found->second.interaction) {
        if (!finishCommandTransaction(*found->second.interaction, true)) {
            throw Base::RuntimeError("The native task transaction could not be committed");
        }
        restoreOriginalUndoMode(*found->second.interaction);
        if (found->second.interaction->macroCapture) {
            found->second.interaction->macroCapture->publish();
        }
    }
    if (found != states.end()) {
        states.erase(found);
    }
}

//==== Slots ===============================================================

QWidget* TaskDialog::addTaskBox(QWidget* widget, bool expandable, QWidget* parent)
{
    return addTaskBox(QPixmap(), widget, expandable, parent);
}

QWidget* TaskDialog::addTaskBox(const QPixmap& icon, QWidget* widget, bool expandable, QWidget* parent)
{
    auto taskbox = new Gui::TaskView::TaskBox(icon, widget->windowTitle(), expandable, parent);
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    return taskbox;
}

QWidget* TaskDialog::addTaskBoxWithoutHeader(QWidget* widget)
{
    auto taskbox = new Gui::TaskView::TaskBox();
    taskbox->groupLayout()->addWidget(widget);
    Content.push_back(taskbox);
    return taskbox;
}

const std::vector<QWidget*>& TaskDialog::getDialogContent() const
{
    return Content;
}

bool TaskDialog::canClose() const
{
    QMessageBox msgBox(Gui::getMainWindow());
    msgBox.setText(tr("A dialog is already open in the task panel"));
    msgBox.setInformativeText(QObject::tr("Close this dialog?"));
    msgBox.setStandardButtons(QMessageBox::Yes | QMessageBox::No);
    msgBox.setDefaultButton(QMessageBox::Yes);
    int ret = msgBox.exec();
    return (ret == QMessageBox::Yes);
}

void TaskDialog::associateToObject3dView(App::DocumentObject* obj)
{
    if (!obj) {
        return;
    }

    Gui::Document* guiDoc = Gui::Application::Instance->activeDocument();
    auto* vp = Gui::Application::Instance->getViewProvider(obj);
    auto* vpdo = static_cast<Gui::ViewProviderDocumentObject*>(vp);
    auto* view = guiDoc->openEditingView3D(vpdo);

    if (!view) {
        return;
    }

    setAssociatedView(view);
    setAutoCloseOnClosedView(true);
}

//==== calls from the TaskView ===============================================================

void TaskDialog::open()
{}

void TaskDialog::closed()
{}

void TaskDialog::autoClosedOnTransactionChange()
{}

void TaskDialog::autoClosedOnResetEdit()
{}

void TaskDialog::autoClosedOnDeletedDocument()
{}

void TaskDialog::autoClosedOnClosedView()
{}

void TaskDialog::clicked(int)
{}

bool TaskDialog::accept()
{
    return true;
}

bool TaskDialog::reject()
{
    return true;
}

void TaskDialog::helpRequested()
{}

void TaskDialog::onUndo()
{}

void TaskDialog::onRedo()
{}

void TaskDialog::activate()
{}

void TaskDialog::deactivate()
{}


#include "moc_TaskDialog.cpp"
