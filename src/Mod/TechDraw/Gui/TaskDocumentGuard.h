// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>
#include <utility>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/TaskView/TaskDialog.h>

namespace TechDrawGui::TaskInternal
{

/**
 * A document reference which cannot silently bind to a later same-name
 * document.
 *
 * Task panels outlive command activation and may receive callbacks after the
 * user changes the active document.  Comparing the current application entry
 * with the captured pointer before dereferencing it makes document closure
 * and same-name replacement a clean "no target" result.
 */
class DocumentIdentity
{
public:
    DocumentIdentity() = default;

    explicit DocumentIdentity(App::Document* document)
        : document(document)
        , name(document ? document->getName() : "")
        , uid(document ? document->Uid.getValueStr() : "")
    {}

    [[nodiscard]] App::Document* resolve() const noexcept
    {
        if (!document || name.empty() || uid.empty()) {
            return nullptr;
        }
        try {
            auto* current =
                App::GetApplication().getDocument(name.c_str());
            return current == document
                    && current->Uid.getValueStr() == uid
                ? current
                : nullptr;
        }
        catch (...) {
            return nullptr;
        }
    }

    [[nodiscard]] Gui::Document* guiDocument() const noexcept
    {
        auto* current = resolve();
        return current && Gui::Application::Instance
            ? Gui::Application::Instance->getDocument(current)
            : nullptr;
    }

    [[nodiscard]] const std::string& documentName() const noexcept
    {
        return name;
    }

private:
    App::Document* document {nullptr};
    std::string name;
    std::string uid;
};

/**
 * One exact-document transaction owned by a non-preview task operation.
 *
 * Some TechDraw panels collect parameters without modifying the model and
 * create/update the result only when OK is pressed.  This guard gives that
 * operation one undo entry without committing or aborting a transaction
 * opened by another caller.
 */
class OwnedDocumentTransaction
{
public:
    OwnedDocumentTransaction(
        App::Document* document,
        App::TransactionName name
    )
        : document(document)
    {
        auto* current = this->document.resolve();
        if (!current) {
            throw Base::RuntimeError(
                "The drawing operation has no live target document"
            );
        }
        if (current->getBookedTransactionID() != App::NullTransaction) {
            throw Base::RuntimeError(
                "The drawing document already has an active transaction"
            );
        }

        transactionId = Gui::Command::openDocumentCommand(
            current,
            std::move(name)
        );
        if (transactionId == App::NullTransaction
            || current->getBookedTransactionID() != transactionId) {
            transactionId = App::NullTransaction;
            throw Base::RuntimeError(
                "The drawing operation could not start its transaction"
            );
        }
    }

    OwnedDocumentTransaction(
        App::Document* document,
        std::string name
    )
        : OwnedDocumentTransaction(
              document,
              App::TransactionName {
                  .name = std::move(name),
                  .temporary = false,
              }
          )
    {}

    OwnedDocumentTransaction(const OwnedDocumentTransaction&) = delete;
    OwnedDocumentTransaction& operator=(
        const OwnedDocumentTransaction&
    ) = delete;

    ~OwnedDocumentTransaction()
    {
        if (transactionId != App::NullTransaction) {
            Gui::Command::abortCommand(transactionId);
        }
    }

    void commit()
    {
        auto* current = document.resolve();
        if (!current
            || current->getBookedTransactionID() != transactionId) {
            throw Base::RuntimeError(
                "The drawing operation lost its owning transaction"
            );
        }
        const int completedId = std::exchange(
            transactionId,
            App::NullTransaction
        );
        Gui::Command::commitCommand(completedId);
    }

private:
    DocumentIdentity document;
    int transactionId {App::NullTransaction};
};

/**
 * A task object reference keyed by its immutable document object ID.
 *
 * Object names can be reused after deletion.  Resolving by ID and verifying
 * both the captured name and expected runtime type prevents a live task from
 * editing a replacement object which merely inherited the old name.
 */
template<typename ObjectT = App::DocumentObject>
class ObjectIdentity
{
public:
    ObjectIdentity() = default;

    explicit ObjectIdentity(App::DocumentObject* object)
        : document(object ? object->getDocument() : nullptr)
        , address(object)
        , objectId(object ? object->getID() : -1)
        , objectName(
              object && object->getNameInDocument()
                  ? object->getNameInDocument()
                  : ""
          )
    {}

    [[nodiscard]] ObjectT* resolve() const noexcept
    {
        auto* currentDocument = document.resolve();
        if (!currentDocument || objectId < 0 || objectName.empty()) {
            return nullptr;
        }
        try {
            auto* object = currentDocument->getObjectByID(objectId);
            if (!object || object != address
                || !currentDocument->containsObject(object)
                || !object->getNameInDocument()
                || objectName != object->getNameInDocument()
                || currentDocument->getObject(objectName.c_str())
                    != object) {
                return nullptr;
            }
            return dynamic_cast<ObjectT*>(object);
        }
        catch (...) {
            return nullptr;
        }
    }

    [[nodiscard]] App::Document* resolveDocument() const noexcept
    {
        return document.resolve();
    }

    [[nodiscard]] Gui::Document* guiDocument() const noexcept
    {
        return document.guiDocument();
    }

    [[nodiscard]] const std::string& name() const noexcept
    {
        return objectName;
    }

    [[nodiscard]] long id() const noexcept
    {
        return objectId;
    }

private:
    DocumentIdentity document;
    App::DocumentObject* address {nullptr};
    long objectId {-1};
    std::string objectName;
};

/**
 * Attach a task panel to the document which owns its modeling target.
 *
 * This prevents TaskView Accept/Cancel from following a later active-document
 * switch.  Document deletion automatically closes the panel before its raw
 * model pointers become invalid.
 */
inline void showDocumentDialog(
    Gui::TaskView::TaskDialog* dialog,
    App::Document* document
)
{
    if (!dialog || !document || !Gui::Application::Instance
        || !Gui::Application::Instance->getDocument(document)) {
        delete dialog;
        throw Base::RuntimeError(
            "The drawing task has no live GUI document"
        );
    }
    if (Gui::Control().activeDialog(document)) {
        delete dialog;
        throw Base::RuntimeError(
            "Another task is already active for this drawing"
        );
    }

    dialog->setAutoCloseOnDeletedDocument(true);
    dialog->setDocumentName(document->getName());
    Gui::Control().showDialog(dialog, document);
    if (Gui::Control().activeDialog(document) != dialog) {
        delete dialog;
        throw Base::RuntimeError(
            "The drawing task could not be opened"
        );
    }
}

inline void resetExactEdit(App::Document* document) noexcept
{
    DocumentIdentity identity(document);
    if (auto* guiDocument = identity.guiDocument()) {
        guiDocument->resetEdit();
    }
}

inline void updateExactDocument(App::Document* document)
{
    DocumentIdentity identity(document);
    if (auto* current = identity.resolve()) {
        Gui::Command::updateDocument(current);
    }
}

}  // namespace TechDrawGui::TaskInternal
