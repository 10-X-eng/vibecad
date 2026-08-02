// SPDX-License-Identifier: LGPL-2.1-or-later

#include "ExactTransaction.h"

#include <algorithm>
#include <optional>
#include <utility>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>

using namespace Gui;

struct ExactTransaction::State
{
    struct DocumentState
    {
        App::Document* document {nullptr};
        int originalUndoMode {0};
        bool temporaryUndoMode {false};
        bool locked {false};
        fastsignals::connection stable;
        fastsignals::connection lockChanged;
    };

    int transactionId {App::NullTransaction};
    bool closed {false};
    bool closing {false};
    std::optional<App::TransactionCloseMode> requestedClose;
    std::vector<DocumentState> documents;
    fastsignals::connection exactClosed;
    fastsignals::connection closeRequested;
    fastsignals::connection documentDeleted;
};

std::map<int, std::shared_ptr<ExactTransaction::State>>& ExactTransaction::pendingStates()
{
    static auto* states = new std::map<int, std::shared_ptr<State>>;
    return *states;
}

std::shared_ptr<ExactTransaction::State> ExactTransaction::beginState(
    const std::vector<App::Document*>& documents,
    const std::string& name,
    App::Document* initiator
)
{
    if (documents.empty() && !initiator) {
        throw Base::RuntimeError("An exact transaction requires at least one document");
    }

    auto state = std::make_shared<ExactTransaction::State>();
    auto& application = App::GetApplication();
    if (application.getGlobalTransaction() != App::NullTransaction) {
        throw Base::RuntimeError("Another global transaction is already active");
    }
    state->documents.reserve(documents.size() + (initiator ? 1U : 0U));
    const auto addDocument = [&state](App::Document* document) {
        if (!document) {
            throw Base::RuntimeError("An exact transaction document is unavailable");
        }
        const auto duplicate = std::ranges::find(
            state->documents,
            document,
            &ExactTransaction::State::DocumentState::document
        );
        if (duplicate != state->documents.end()) {
            return;
        }
        if (document->getBookedTransactionID() != App::NullTransaction
            || document->hasPendingTransaction()) {
            throw Base::RuntimeError("Another document transaction is already active");
        }
        State::DocumentState item;
        item.document = document;
        item.originalUndoMode = document->getUndoMode();
        state->documents.push_back(std::move(item));
    };
    if (initiator) {
        addDocument(initiator);
    }
    for (auto* document : documents) {
        addDocument(document);
    }

    try {
        for (auto& item : state->documents) {
            if (item.originalUndoMode == 0) {
                item.document->setUndoMode(1);
                item.temporaryUndoMode = item.document->getUndoMode() != 0;
                if (!item.temporaryUndoMode) {
                    throw Base::RuntimeError("Could not enable a private rollback journal");
                }
            }
        }

        if (state->documents.size() == 1) {
            state->transactionId = state->documents.front().document->openTransaction(name);
        }
        else if (initiator) {
            state->transactionId = initiator->openTransaction(name);
            if (state->transactionId == App::NullTransaction) {
                throw Base::RuntimeError("Could not establish the initiating exact transaction");
            }
            for (auto& item : state->documents) {
                if (item.document == initiator) {
                    continue;
                }
                const int enlisted = item.document->openTransaction(name, state->transactionId);
                if (enlisted != state->transactionId) {
                    throw Base::RuntimeError("Could not enlist every document in the exact "
                                             "transaction");
                }
            }
        }
        else {
            state->transactionId = application.openGlobalTransaction(App::TransactionName {
                .name = name,
                .temporary = false,
            });
            for (auto& item : state->documents) {
                const int enlisted = item.document->openTransaction(name, state->transactionId);
                if (enlisted != state->transactionId) {
                    throw Base::RuntimeError("Could not enlist every document in the exact "
                                             "transaction");
                }
            }
        }

        if (state->transactionId == App::NullTransaction
            || !application.transactionIsActive(state->transactionId)) {
            throw Base::RuntimeError("Could not establish the exact transaction");
        }
        for (auto& item : state->documents) {
            if (item.document->getBookedTransactionID() != state->transactionId) {
                throw Base::RuntimeError("Exact transaction ownership was not established");
            }
            // Document::openTransaction() intentionally books the exact ID
            // before allocating an undo journal.  The journal is created
            // lazily by the first real document mutation.  Requiring
            // hasPendingTransaction() here would therefore reject every
            // correctly opened, still-empty transaction before the command
            // had a chance to perform its work.
            item.document->lockTransaction();
            item.locked = true;
        }
        return state;
    }
    catch (...) {
        if (state->transactionId != App::NullTransaction) {
            ExactTransaction cleanup(state);
            (void)cleanup.abort();
        }
        else {
            for (auto& item : state->documents) {
                if (item.temporaryUndoMode && item.document) {
                    item.document->setUndoMode(item.originalUndoMode);
                    item.temporaryUndoMode = false;
                }
            }
        }
        throw;
    }
}

ExactTransaction::ExactTransaction(App::Document& document, const std::string& name)
    : ExactTransaction(beginState({&document}, name))
{}

ExactTransaction::ExactTransaction(
    App::Document& initiator,
    const std::vector<App::Document*>& documents,
    const std::string& name
)
    : ExactTransaction(beginState(documents, name, &initiator))
{}

ExactTransaction::ExactTransaction(const std::vector<App::Document*>& documents, const std::string& name)
    : ExactTransaction(beginState(documents, name))
{}

ExactTransaction::ExactTransaction(std::shared_ptr<State> state)
    : state(std::move(state))
{
    armState(this->state);
}

ExactTransaction::~ExactTransaction()
{
    if (state && !state->closed && !state->requestedClose) {
        (void)closeState(state, App::TransactionCloseMode::Abort, true);
    }
}

ExactTransaction::ExactTransaction(ExactTransaction&&) noexcept = default;

ExactTransaction& ExactTransaction::operator=(ExactTransaction&& other) noexcept
{
    if (this != &other && state && !state->closed && !state->requestedClose) {
        (void)closeState(state, App::TransactionCloseMode::Abort, true);
    }
    state = std::move(other.state);
    return *this;
}

void ExactTransaction::armState(const std::shared_ptr<State>& state)
{
    if (!state || state->transactionId == App::NullTransaction) {
        return;
    }
    const std::weak_ptr<State> weak = state;
    for (auto& item : state->documents) {
        item.stable = item.document->signalBecameStable.connect([weak](const App::Document&) {
            retryState(weak);
        });
        item.lockChanged = item.document->signalTransactionLockChanged.connect(
            [weak](const App::Document&) { retryState(weak); }
        );
    }
    state->exactClosed = App::GetApplication().signalExactTransactionClosed.connect(
        [weak](int transactionId, bool, const std::vector<App::Document*>&) {
            const auto current = weak.lock();
            if (!current || current->closing || current->transactionId != transactionId) {
                return;
            }
            completeState(current);
        }
    );
    state->closeRequested = App::GetApplication().signalBeforeCloseDocument.connect(
        [weak](const App::Document& requested) {
            const auto current = weak.lock();
            if (!current || current->closed || current->closing) {
                return;
            }
            const bool participates = std::ranges::any_of(
                current->documents,
                [&requested](const State::DocumentState& item) {
                    return item.document == &requested;
                }
            );
            if (participates) {
                (void)closeState(current, App::TransactionCloseMode::Abort, true);
            }
        }
    );
    state->documentDeleted = App::GetApplication().signalDeleteDocument.connect(
        [weak](const App::Document& deleted) {
            const auto current = weak.lock();
            if (!current) {
                return;
            }
            const bool participates = std::ranges::any_of(
                current->documents,
                [&deleted](const State::DocumentState& item) { return item.document == &deleted; }
            );
            if (participates && !current->closed
                && App::GetApplication().transactionIsActive(current->transactionId)) {
                // A document cannot leave a live multi-document T behind.
                // No durable outcome was accepted while T is active, so
                // document deletion converts any failed pending close into
                // an exact rollback before the pointer becomes invalid.
                current->requestedClose = App::TransactionCloseMode::Abort;
                (void)closeState(current, App::TransactionCloseMode::Abort, true);
            }
            for (auto& item : current->documents) {
                if (item.document != &deleted) {
                    continue;
                }
                if (item.locked) {
                    item.document->unlockTransaction();
                }
                item.locked = false;
                item.document = nullptr;
                item.stable.disconnect();
                item.lockChanged.disconnect();
            }
            if (current->closed) {
                restoreUndoModes(current);
            }
        }
    );
}

bool ExactTransaction::commit() noexcept
{
    return closeState(state, App::TransactionCloseMode::Commit, true);
}

bool ExactTransaction::abort() noexcept
{
    return closeState(state, App::TransactionCloseMode::Abort, true);
}

bool ExactTransaction::retry() noexcept
{
    if (!state || !state->requestedClose) {
        return state && state->closed;
    }
    return closeState(state, *state->requestedClose, true);
}

bool ExactTransaction::closeState(
    const std::shared_ptr<State>& state,
    App::TransactionCloseMode mode,
    bool retainOnFailure
) noexcept
{
    if (!state) {
        return true;
    }
    if (state->closed) {
        return true;
    }
    if (state->closing) {
        return false;
    }
    if (state->requestedClose && *state->requestedClose != mode) {
        Base::Console().error(
            "Exact transaction %d already owns a different close outcome.\n",
            state->transactionId
        );
        return false;
    }
    state->requestedClose = mode;
    state->closing = true;
    for (auto& item : state->documents) {
        if (item.document && item.locked) {
            item.document->unlockTransaction();
            item.locked = false;
        }
    }

    bool closeReturned = false;
    try {
        closeReturned = App::GetApplication().closeActiveTransaction(mode, state->transactionId);
    }
    catch (...) {
        closeReturned = false;
    }
    const bool exactInactive = !App::GetApplication().transactionIsActive(state->transactionId);
    const bool detachedEverywhere
        = std::ranges::none_of(state->documents, [state](const State::DocumentState& item) {
              return item.document && item.document->getBookedTransactionID() == state->transactionId;
          });
    if ((closeReturned || exactInactive) && exactInactive && detachedEverywhere) {
        completeState(state);
        state->closing = false;
        return true;
    }

    for (auto& item : state->documents) {
        if (item.document && item.document->getBookedTransactionID() == state->transactionId
            && !item.locked) {
            item.document->lockTransaction();
            item.locked = true;
        }
    }
    if (retainOnFailure) {
        pendingStates().insert_or_assign(state->transactionId, state);
    }
    state->closing = false;
    Base::Console().error(
        "Exact transaction %d could not %s; ownership was retained for "
        "retry.\n",
        state->transactionId,
        mode == App::TransactionCloseMode::Commit ? "commit" : "roll back"
    );
    return false;
}

void ExactTransaction::completeState(const std::shared_ptr<State>& state) noexcept
{
    if (!state || state->closed) {
        return;
    }
    for (auto& item : state->documents) {
        if (item.document && item.locked) {
            item.document->unlockTransaction();
        }
        item.locked = false;
    }
    state->closed = true;
    restoreUndoModes(state);
}

void ExactTransaction::restoreUndoModes(const std::shared_ptr<State>& state) noexcept
{
    if (!state) {
        return;
    }
    bool restorationPending = false;
    for (auto& item : state->documents) {
        if (!item.temporaryUndoMode) {
            continue;
        }
        if (!item.document) {
            item.temporaryUndoMode = false;
            continue;
        }
        if (item.document->getBookedTransactionID() == App::NullTransaction
            && !item.document->hasPendingTransaction()) {
            try {
                item.document->setUndoMode(item.originalUndoMode);
                item.temporaryUndoMode = false;
            }
            catch (...) {
                restorationPending = true;
            }
        }
        else {
            restorationPending = true;
        }
    }
    if (restorationPending) {
        pendingStates().insert_or_assign(state->transactionId, state);
    }
    else {
        pendingStates().erase(state->transactionId);
        state->exactClosed.disconnect();
        state->closeRequested.disconnect();
        state->documentDeleted.disconnect();
        for (auto& item : state->documents) {
            item.stable.disconnect();
            item.lockChanged.disconnect();
        }
    }
}

void ExactTransaction::retryState(const std::weak_ptr<State>& weak) noexcept
{
    const auto state = weak.lock();
    if (!state || state->closing) {
        return;
    }
    if (state->closed) {
        restoreUndoModes(state);
        return;
    }
    if (state->requestedClose) {
        (void)closeState(state, *state->requestedClose, true);
    }
}

bool ExactTransaction::ownsCurrentTransaction() const noexcept
{
    if (!state || state->closed || state->transactionId == App::NullTransaction
        || !App::GetApplication().transactionIsActive(state->transactionId)) {
        return false;
    }
    return std::ranges::all_of(state->documents, [this](const State::DocumentState& item) {
        return !item.document || item.document->getBookedTransactionID() == state->transactionId;
    });
}

bool ExactTransaction::isClosed() const noexcept
{
    return !state || state->closed;
}

int ExactTransaction::id() const noexcept
{
    return state ? state->transactionId : App::NullTransaction;
}
