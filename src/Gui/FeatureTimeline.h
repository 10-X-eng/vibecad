// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <cstdint>
#include <string>

#include <QWidget>
#include <fastsignals/signal.h>

#include "DocumentObserver.h"
#include "Selection/Selection.h"

class QListWidget;
class QListWidgetItem;
class QPoint;
class QTimer;
class QToolButton;

namespace App
{
class Document;
class DocumentObject;
class Property;
}  // namespace App

namespace Gui
{
class Document;
class MDIView;
class ViewProviderDocumentObject;

/**
 * A compact presentation of the active document's native modeling history.
 *
 * The persisted App::DocumentTimeline is the authority for ordering and the
 * current history boundary. Body ownership is presentation metadata only:
 * selecting or activating another Body never filters or replaces the timeline.
 *
 * The timeline is deliberately a permanent part of the central workspace,
 * rather than a dock panel. VibeCAD mirrors the MDI document tabs into its
 * ribbon and collapses Qt's original bottom tab bar; this strip occupies that
 * reclaimed edge without participating in workbench or bottom-panel toggles.
 */
class GuiExport FeatureTimeline: public QWidget,
                                 public Gui::SelectionObserver,
                                 private Gui::DocumentObserver
{
    Q_OBJECT

public:
    explicit FeatureTimeline(QWidget* parent = nullptr);
    ~FeatureTimeline() override;

    /**
     * Move the document's current-state marker immediately after one exact
     * native operation.
     *
     * This is the typed entry point used by non-widget modeling surfaces.  It
     * deliberately accepts the complete persisted identities instead of a
     * raw history position, then delegates to the same validated transaction
     * path used by the timeline controls.
     */
    Q_INVOKABLE bool moveCurrentStateAfterOperation(
        const QString& documentName,
        const QString& documentUid,
        const QString& operationName,
        qlonglong operationId
    );

private Q_SLOTS:
    void onTimelineSelectionChanged();
    void onTimelineItemDoubleClicked(QListWidgetItem* item);
    void onTimelineContextMenu(const QPoint& position);
    void rebuild();

private:
    enum ItemRole
    {
        ObjectNameRole = Qt::UserRole,
        OwnerNameRole,
        IsCurrentRole,
        IsAfterPositionRole,
        OperationIndexRole,
        IsMarkerRole,
        ObjectIdRole,
        OwnerIdRole,
        DocumentNameRole,
        DocumentGenerationRole,
    };

    void onSelectionChanged(const SelectionChanges& message) override;

    void slotCreatedObject(const ViewProviderDocumentObject&) override;
    void slotDeletedObject(const ViewProviderDocumentObject&) override;
    void slotChangedObject(const ViewProviderDocumentObject&, const App::Property&) override;
    void slotRelabelObject(const ViewProviderDocumentObject&) override;
    void slotEnterEditObject(const ViewProviderDocumentObject&) override;
    void slotResetEditObject(const ViewProviderDocumentObject&) override;
    void slotUndoDocument(const Gui::Document&) override;
    void slotRedoDocument(const Gui::Document&) override;
    void slotDeleteDocument(const Gui::Document&) override;

    void setObservedDocument(Gui::Document* document);
    void scheduleRefresh();
    void syncSelectionFromGui();
    bool canChangeHistory() const;
    void activateOwningBody(App::DocumentObject* object);
    void editObject(App::DocumentObject* object);
    bool invokeTimelineEditCommand(App::DocumentObject* operation);
    void selectOnly(App::DocumentObject* object);
    void moveCurrentStateToPosition(int position);
    void navigateCurrentState(int direction);
    void setDocumentPosition(int position);
    void runSelectionCommand(App::DocumentObject* object, const char* commandName);
    App::DocumentObject* objectForItem(const QListWidgetItem* item) const;
    bool itemBelongsToObservedDocument(const QListWidgetItem* item) const;
    App::Document* activeAppDocument() const;

    QToolButton* recomputeButton {};
    QToolButton* previousButton {};
    QToolButton* nextButton {};
    QToolButton* endButton {};
    QListWidget* timeline {};
    QTimer* refreshTimer {};

    std::string observedDocumentName;
    App::Document* observedAppDocument {};
    std::uint64_t observedDocumentGeneration {0};
    bool rebuildingTimeline {false};
    bool syncingSelection {false};

    fastsignals::scoped_connection activeDocumentConnection;
    fastsignals::scoped_connection activeViewConnection;
    fastsignals::scoped_connection renamedDocumentConnection;
    fastsignals::scoped_connection bookedTransactionConnection;
    fastsignals::scoped_connection recomputeRequestFinishedConnection;
    fastsignals::scoped_connection stableDocumentConnection;
    fastsignals::scoped_connection changedObjectConnection;
    fastsignals::scoped_connection touchedObjectConnection;
    fastsignals::scoped_connection recomputedObjectConnection;
};

}  // namespace Gui
