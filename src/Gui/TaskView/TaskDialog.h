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


#pragma once

#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <QDialogButtonBox>
#include <QPointer>
#include <QVariant>
#include <FCGlobal.h>
#include <Gui/Selection/SelectionObject.h>


namespace App
{
class Document;
class DocumentObject;
}

namespace Gui
{
class Document;
class MDIView;
namespace TaskView
{

class TaskContent;
class TaskDialogAttorney;
class TaskDialogPy;
class TaskView;

/// Father class of content with header and Icon
class GuiExport TaskDialog: public QObject
{
    Q_OBJECT

public:
    enum ButtonPosition
    {
        North,
        South
    };

    TaskDialog();
    ~TaskDialog() override;

    /**
     * Capture the human interaction state at the start of a command invocation.
     *
     * A task dialog shown synchronously by that command adopts the checkpoint
     * when it is installed.  The checkpoint stores name-resolved selection
     * records, including sub-elements and pick positions, so transaction
     * rollback can delete and recreate objects without leaving dangling GUI
     * pointers behind.
     */
    static void beginCommandInvocation();
    static void endCommandInvocation();
    static void endCommandInvocation(bool commandSucceeded);
    /**
     * Return whether the outermost GUI command owns the current transaction.
     *
     * A nested child may use an enclosing command's transaction only when
     * that outermost invocation began without a booked transaction and opened
     * the current one itself. A transaction present before the outermost
     * command remains externally owned.
     */
    static bool hasOwnedEnclosingTransaction(
        const App::Document* document
    );
    /// Return the exact transaction created by the outer GUI command.
    static int ownedEnclosingTransactionId(
        const App::Document* document
    );
    /**
     * Return whether this dialog owns finalization of an exact transaction.
     *
     * Python task implementations use this after the dialog is installed to
     * distinguish a transaction adopted by the common TaskView lifecycle from
     * one that the Python task must close itself.
     */
    bool ownsCommandTransaction(int transactionId) const;
    /// Record that an edit session adopted that exact transaction.
    static void markOwnedEnclosingTransactionAdopted(
        const App::Document* document,
        int transactionId
    );
    /**
     * Record a successful exact close performed inside this command.
     *
     * A close callback may synchronously open a successor transaction before
     * the exact close call returns. This completion record prevents command
     * teardown from inferring that successor as its own transaction.
     */
    static void recordCommandTransactionCompletion(
        const App::Document* document,
        int transactionId
    );
    /// Preserve command checkpoint/macro state for an editor without a panel.
    static void adoptOwnedEditCommandInteraction(
        Gui::Document* document,
        int transactionId
    );
    /// Finalize a no-panel editor's preserved command interaction.
    static void finishOwnedEditCommandInteraction(
        Gui::Document* document,
        int transactionId,
        bool cancelled,
        bool transactionFinished
    );
    /// Discard retained no-panel editor state before its GUI document dies.
    static void discardOwnedEditCommandInteraction(
        Gui::Document* document
    );

    QWidget* addTaskBox(QWidget* widget, bool expandable = true, QWidget* parent = nullptr);
    QWidget* addTaskBox(
        const QPixmap& icon,
        QWidget* widget,
        bool expandable = true,
        QWidget* parent = nullptr
    );
    QWidget* addTaskBoxWithoutHeader(QWidget* widget);

    void setButtonPosition(ButtonPosition p)
    {
        pos = p;
    }
    ButtonPosition buttonPosition() const
    {
        return pos;
    }
    const std::vector<QWidget*>& getDialogContent() const;
    bool canClose() const;

    /// tells the framework which buttons are wished for the dialog
    virtual QDialogButtonBox::StandardButtons getStandardButtons() const
    {
        return QDialogButtonBox::Ok | QDialogButtonBox::Cancel;
    }
    virtual void modifyStandardButtons(QDialogButtonBox*)
    {}

    /// Defines whether a task dialog can be rejected by pressing Esc
    void setEscapeButtonEnabled(bool on)
    {
        escapeButton = on;
    }
    bool isEscapeButtonEnabled() const
    {
        return escapeButton;
    }
    QDialogButtonBox::ButtonRole roleOnEscape {QDialogButtonBox::ButtonRole::RejectRole};

    /// Defines whether a task dialog must be closed if the document changed the
    /// active transaction.
    void setAutoCloseOnTransactionChange(bool on)
    {
        autoCloseTransaction = on;
    }
    bool isAutoCloseOnTransactionChange() const
    {
        return autoCloseTransaction;
    }

    /// Defines whether a task dialog must be closed if the document exits edit mode.
    void setAutoCloseOnResetEdit(bool on)
    {
        autoCloseResetEdit = on;
    }
    bool isAutoCloseOnResetEdit() const
    {
        return autoCloseResetEdit;
    }

    /// Defines whether a task dialog must be closed if the document is
    /// deleted.
    void setAutoCloseOnDeletedDocument(bool on)
    {
        autoCloseDeletedDocument = on;
    }
    bool isAutoCloseOnDeletedDocument() const
    {
        return autoCloseDeletedDocument;
    }

    const std::string& getDocumentName() const
    {
        return documentName;
    }
    void setDocumentName(const std::string& doc)
    {
        documentName = doc;
    }

    /// Defines whether a task dialog must be closed if the associated view
    /// is deleted.
    void setAutoCloseOnClosedView(bool on)
    {
        autoCloseClosedView = on;
    }
    bool isAutoCloseOnClosedView() const
    {
        return autoCloseClosedView;
    }

    /**
     * Control whether a successful Accept closes this panel.
     *
     * Some creation panels intentionally remain open so the user can create
     * several independent results. Their Accept still means the current
     * result was accepted and must be committed, recorded, and validated.
     */
    void setAcceptClosesDialog(bool closes)
    {
        setProperty("taskview_accept_closes_dialog", closes);
    }
    bool acceptClosesDialog() const
    {
        const QVariant setting =
            property("taskview_accept_closes_dialog");
        return !setting.isValid() || setting.toBool();
    }

    void associateToObject3dView(App::DocumentObject* obj);

    const Gui::MDIView* getAssociatedView() const
    {
        return associatedView;
    }
    void setAssociatedView(const Gui::MDIView* view)
    {
        associatedView = view;
    }

    /*!
      Indicates whether this task dialog allows other commands to modify
      the document while it is open.
    */
    virtual bool isAllowedAlterDocument() const
    {
        return false;
    }
    /*!
      Indicates whether this task dialog allows other commands to modify
      the 3d view while it is open.
    */
    virtual bool isAllowedAlterView() const
    {
        return true;
    }
    /*!
      Indicates whether this task dialog allows other commands to modify
      the selection while it is open.
    */
    virtual bool isAllowedAlterSelection() const
    {
        return true;
    }
    virtual bool needsFullSpace() const
    {
        return false;
    }

public:
    /// is called by the framework when the dialog is opened
    virtual void open();
    /// is called by the framework when the dialog is closed
    virtual void closed();
    /// is called by the framework when the dialog is automatically closed due to
    /// changing the active transaction
    virtual void autoClosedOnTransactionChange();
    /// is called by the framework when the dialog is automatically closed due to
    /// exiting edit mode
    virtual void autoClosedOnResetEdit();
    /// is called by the framework when the dialog is automatically closed due to
    /// deleting the document
    virtual void autoClosedOnDeletedDocument();
    /// is called by the framework when the dialog is automatically closed due to
    /// closing of associated view
    virtual void autoClosedOnClosedView();
    /// is called by the framework if a button is clicked which has no accept or reject role
    virtual void clicked(int);
    /// is called by the framework if the dialog is accepted (Ok)
    virtual bool accept();
    /// is called by the framework if the dialog is rejected (Cancel)
    virtual bool reject();
    /// is called by the framework if the user press the help button
    virtual void helpRequested();
    /// is called by the framework if the user press the undo button
    virtual void onUndo();
    /// is called by the framework if the user press the redo button
    virtual void onRedo();

    /// Called by the framework when it becomes the shown dialog
    /// of the stacked task panel (e.g. when it's document becomes active)
    virtual void activate();
    /// Called by the framework when it stops being the shown dialog
    /// of the stacked task panel (e.g. when it's document stops being active)
    virtual void deactivate();

    void emitDestructionSignal()
    {
        Q_EMIT aboutToBeDestroyed();
    }

Q_SIGNALS:
    void aboutToBeDestroyed();

protected:
    /**
     * Mark a successfully created/applied result as durable while this panel remains open.
     *
     * A later Close/Cancel must not restore the interaction checkpoint captured before the
     * command over a result the user already accepted. Call this only after the durable operation
     * succeeds, never for previews or other provisional task-panel changes.
     */
    void markCommandInteractionStateDurable(
        const std::vector<App::DocumentObject*>& acceptedResults = {}
    );

    QPointer<QDialogButtonBox> buttonBox;
    /// List of TaskBoxes of that dialog
    std::vector<QWidget*> Content;
    ButtonPosition pos;

private:
    struct MacroCapture;

    struct InteractionState
    {
        struct ObjectIdentity
        {
            std::string name;
            long id {-1};
        };

        struct VisibilityState
        {
            std::string documentName;
            std::string objectName;
            bool visible {false};
        };

        App::Document* commandDocument {nullptr};
        std::string commandDocumentName;
        std::string commandActiveObjectName;
        bool commandDocumentModified {false};
        bool commandUndoEnabled {true};
        bool temporaryRollbackJournal {false};
        bool taskAdopted {false};
        bool commandTransactionCompleted {false};
        bool editWasActiveAtInvocationStart {false};
        bool standaloneTransactionLocked {false};
        int initialTransactionId {0};
        int commandTransactionId {0};
        std::shared_ptr<MacroCapture> macroCapture;
        std::vector<ObjectIdentity> commandObjects;
        std::vector<Gui::SelectionObject> selection;
        std::string activeBodyDocumentName;
        bool hadActiveBody {false};
        std::string activeBodyRootName;
        std::string activeBodySubname;
        std::vector<VisibilityState> visibility;
    };

    struct DialogState;
    struct PendingInteractionRollback;

    static std::vector<InteractionState>& commandInvocationStack();
    static std::map<TaskDialog*, DialogState>& dialogStates();
    static std::map<Gui::Document*, InteractionState>&
    ownedEditInteractionStates();
    static std::map<
        std::pair<const App::Document*, int>,
        std::shared_ptr<PendingInteractionRollback>
    >& pendingInteractionRollbacks();
    static std::optional<InteractionState>
    takeCommandInteractionState(TaskDialog* dialog);
    static void appendAcceptedMacroLines(
        TaskDialog* dialog,
        const std::vector<std::pair<int, std::string>>& lines
    ) noexcept;
    static void clearPendingDurableResults(TaskDialog* dialog);
    static void pauseCommandMacroCapture(TaskDialog* dialog);
    static void resumeCommandMacroCapture(TaskDialog* dialog);
    static void discardCommandMacroCapture(TaskDialog* dialog);
    void adoptCommandInteractionState(App::Document* document);
    static bool restoreCommandInteractionState(
        const std::optional<InteractionState>& state,
        bool restorePresentation = true
    );
    static void retainPendingInteractionRollback(
        const InteractionState& state,
        bool restorePresentation
    );
    static void retryPendingInteractionRollback(
        const App::Document* document,
        int transactionId
    );
    static bool finishCommandTransaction(
        const InteractionState& state,
        bool commit
    );
    static void restoreOriginalUndoMode(const InteractionState& state);
    static void removeUnacceptedObjects(const InteractionState& state);

    std::string documentName;
    const Gui::MDIView* associatedView;
    bool escapeButton;
    bool autoCloseTransaction;
    bool autoCloseResetEdit;
    bool autoCloseDeletedDocument;
    bool autoCloseClosedView;

    friend class TaskDialogAttorney;
    friend class TaskView;
};

class TaskDialogAttorney
{
private:
    static void setButtonBox(TaskDialog* dlg, QDialogButtonBox* box)
    {
        dlg->buttonBox = box;
    }
    static QDialogButtonBox* getButtonBox(TaskDialog* dlg)
    {
        return dlg->buttonBox;
    }

    friend class TaskDialogPy;
    friend class TaskView;
};

}  // namespace TaskView
}  // namespace Gui
