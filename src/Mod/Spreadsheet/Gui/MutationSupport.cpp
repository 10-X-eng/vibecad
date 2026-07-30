// SPDX-License-Identifier: LGPL-2.1-or-later

#include "MutationSupport.h"

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Exception.h>
#include <Gui/Control.h>
#include <Gui/ExactTransaction.h>
#include <Mod/Spreadsheet/App/Sheet.h>

bool SpreadsheetGui::MutationSupport::hasCleanBoundary(const App::Document* document) noexcept
{
    return document && App::GetApplication().getActiveDocument() == document
        && document->getBookedTransactionID() == App::NullTransaction
        && !document->hasPendingTransaction() && !document->isTransactionLocked()
        && !document->transacting() && !Gui::Control().activeDialog();
}

void SpreadsheetGui::MutationSupport::requireCleanBoundary(const App::Document& document)
{
    if (!hasCleanBoundary(&document)) {
        throw Base::RuntimeError("Another task or document change is already in progress");
    }
}

void SpreadsheetGui::MutationSupport::recompute(App::Document& document)
{
    document.recompute();
    if (document.hasPendingTransaction()
        && document.getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError("Spreadsheet recompute left an unowned document transaction");
    }
}

void SpreadsheetGui::MutationSupport::commit(Gui::ExactTransaction& transaction)
{
    if (!transaction.commit()) {
        throw Base::RuntimeError("The spreadsheet change could not be committed");
    }
}

void SpreadsheetGui::MutationSupport::publishCreatedSheet(Spreadsheet::Sheet& sheet)
{
    auto* document = sheet.getDocument();
    if (!document) {
        throw Base::RuntimeError("The spreadsheet is not attached to a document");
    }
    auto* timeline = App::DocumentTimeline::ensure(document);
    if (!timeline) {
        throw Base::RuntimeError("The spreadsheet could not be added to document History");
    }
    timeline->publishProvisionalOperationBlock(&sheet, {});
}
