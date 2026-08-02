// SPDX-License-Identifier: LGPL-2.1-or-later
#pragma once

#include <map>
#include <memory>
#include <string>
#include <vector>

#include <App/TransactionDefs.h>
#include <FCGlobal.h>

namespace App
{
class Document;
}

namespace Gui
{

/**
 * Own one exact modeling transaction from creation through durable close.
 *
 * The owner supplies a private rollback journal when Undo is disabled, locks
 * every enlisted document while provisional geometry is live, and closes
 * only its captured transaction ID. A synchronous close observer may open a
 * successor transaction without changing the result of commit()/abort().
 *
 * A failed close retains the exact ID, relocks every still-enlisted document,
 * and retries the same requested outcome when those documents become
 * unlockable/stable. Destruction requests rollback only when no other outcome
 * has already been requested.
 */
class GuiExport ExactTransaction
{
public:
    ExactTransaction(App::Document& document, const std::string& name);
    ExactTransaction(
        App::Document& initiator,
        const std::vector<App::Document*>& documents,
        const std::string& name
    );
    ExactTransaction(const std::vector<App::Document*>& documents, const std::string& name);
    ~ExactTransaction();

    ExactTransaction(const ExactTransaction&) = delete;
    ExactTransaction& operator=(const ExactTransaction&) = delete;
    ExactTransaction(ExactTransaction&&) noexcept;
    ExactTransaction& operator=(ExactTransaction&&) noexcept;

    bool commit() noexcept;
    bool abort() noexcept;
    bool retry() noexcept;

    [[nodiscard]] bool ownsCurrentTransaction() const noexcept;
    [[nodiscard]] bool isClosed() const noexcept;
    [[nodiscard]] int id() const noexcept;

private:
    struct State;

    explicit ExactTransaction(std::shared_ptr<State> state);
    static std::shared_ptr<State> beginState(
        const std::vector<App::Document*>& documents,
        const std::string& name,
        App::Document* initiator = nullptr
    );
    static std::map<int, std::shared_ptr<State>>& pendingStates();
    static bool closeState(
        const std::shared_ptr<State>& state,
        App::TransactionCloseMode mode,
        bool retainOnFailure
    ) noexcept;
    static void armState(const std::shared_ptr<State>& state);
    static void retryState(const std::weak_ptr<State>& state) noexcept;
    static void completeState(const std::shared_ptr<State>& state) noexcept;
    static void restoreUndoModes(const std::shared_ptr<State>& state) noexcept;

    std::shared_ptr<State> state;
};

}  // namespace Gui
