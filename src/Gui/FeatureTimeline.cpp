// SPDX-License-Identifier: LGPL-2.1-or-later

#include "FeatureTimeline.h"

#include <algorithm>
#include <functional>
#include <iterator>
#include <limits>
#include <optional>
#include <sstream>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <QAbstractItemView>
#include <QApplication>
#include <QByteArray>
#include <QFrame>
#include <QHBoxLayout>
#include <QIcon>
#include <QImage>
#include <QListView>
#include <QListWidget>
#include <QMenu>
#include <QMetaType>
#include <QMouseEvent>
#include <QPalette>
#include <QPixmap>
#include <QScopedValueRollback>
#include <QScrollBar>
#include <QSignalBlocker>
#include <QSizePolicy>
#include <QStyle>
#include <QTimer>
#include <QToolButton>
#include <QVariant>
#include <QtGlobal>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/DocumentObject.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <App/SuppressibleExtension.h>
#include <Base/Console.h>
#include <Base/Exception.h>

#include "ActiveObjectList.h"
#include "Action.h"
#include "Application.h"
#include "BitmapFactory.h"
#include "Command.h"
#include "Control.h"
#include "Document.h"
#include "Macro.h"
#include "MDIView.h"
#include "ModelTreeBrowser.h"
#include "TaskView/TaskDialog.h"
#include "ViewProviderDocumentObject.h"


using namespace Gui;

namespace
{

// Leave the icon row and its six-pixel horizontal history scrollbar enough
// independent vertical space at every supported UI scale.
constexpr int timelineHeight = 56;
constexpr int timelineItemHeight = 34;
constexpr int timelineItemWidth = 38;

QPixmap desaturatePixmap(const QPixmap& source)
{
    QImage image = source.toImage().convertToFormat(QImage::Format_ARGB32);
    for (int y = 0; y < image.height(); ++y) {
        auto* pixels = reinterpret_cast<QRgb*>(image.scanLine(y));
        for (int x = 0; x < image.width(); ++x) {
            const int gray = qGray(pixels[x]);
            pixels[x] = qRgba(gray, gray, gray, qAlpha(pixels[x]));
        }
    }
    return QPixmap::fromImage(image);
}

QIcon timelineObjectIcon(
    const QIcon& base,
    const App::DocumentObject* object,
    bool disabled,
    bool owningBodyHidden
)
{
    QPixmap pixmap = base.pixmap(QSize(22, 22), disabled ? QIcon::Disabled : QIcon::Normal);
    if (owningBodyHidden) {
        pixmap = desaturatePixmap(pixmap);
    }
    const char* overlayName = nullptr;
    if (object && object->isError()) {
        overlayName = "overlay_error";
    }
    else if (object && (object->isTouched() || object->mustExecute() == 1)) {
        overlayName = "overlay_recompute";
    }
    if (overlayName) {
        const QPixmap overlay = Gui::BitmapFactory().pixmapFromSvg(overlayName, QSize(10, 10));
        pixmap = Gui::BitmapFactory().merge(pixmap, overlay, Gui::BitmapFactoryInst::TopRight);
    }
    return QIcon(pixmap);
}

QString timelineStatusText(const App::DocumentObject* object)
{
    if (!object) {
        return {};
    }
    if (object->isError()) {
        return FeatureTimeline::tr("Error: %1").arg(QString::fromUtf8(object->getStatusString()));
    }
    if (object->isTouched() || object->mustExecute() == 1) {
        return FeatureTimeline::tr("Needs recompute");
    }
    return {};
}

QString objectLabel(const App::DocumentObject* object)
{
    return object && object->Label.getValue() ? QString::fromUtf8(object->Label.getValue())
                                              : QString();
}

struct TimelineObjectIdentity
{
    std::string name;
    long id {-1};
};

struct TimelineDocumentIdentity
{
    App::Document* pointer {};
    std::string uid;
};

TimelineDocumentIdentity documentIdentity(App::Document* document)
{
    return {document, document ? document->Uid.getValueStr() : ""};
}

App::Document* resolveDocument(const TimelineDocumentIdentity& identity)
{
    if (!identity.pointer || identity.uid.empty()) {
        return nullptr;
    }
    for (auto* document : App::GetApplication().getDocuments()) {
        if (document == identity.pointer && document->Uid.getValueStr() == identity.uid) {
            return document;
        }
    }
    return nullptr;
}

TimelineObjectIdentity objectIdentity(const App::DocumentObject* object)
{
    return {
        object && object->getNameInDocument() ? object->getNameInDocument() : "",
        object ? object->getID() : -1,
    };
}

App::DocumentObject* resolveObject(App::Document* document, const std::string& name, long id)
{
    if (!document || name.empty() || id < 0) {
        return nullptr;
    }
    auto* object = document->getObject(name.c_str());
    return object && object->getID() == id ? object : nullptr;
}

App::DocumentObject* resolveObject(App::Document* document, const TimelineObjectIdentity& identity)
{
    return resolveObject(document, identity.name, identity.id);
}

ApprovedDocumentTimelineCommand approvedTimelineEditCommand(
    App::DocumentObject* operation,
    bool requireActive
)
{
    return Gui::approvedDocumentTimelineCommand(
        operation,
        App::DocumentTimeline::EditCommandPropertyName,
        "VibeCADTimelineOperationEditor",
        requireActive
    );
}

QPoint mouseEventPosition(const QMouseEvent* event)
{
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    return event->position().toPoint();
#else
    return event->pos();
#endif
}

std::string macroObjectCommand(const App::DocumentObject* object) noexcept
{
    if (!object) {
        return {};
    }
    try {
        return Gui::Command::getObjectCmd(object);
    }
    catch (...) {
        return {};
    }
}

bool isDerivedFrom(const App::DocumentObject* object, std::string_view typeName)
{
    if (!object) {
        return false;
    }
    const Base::Type type = Base::Type::fromName(typeName);
    return !type.isBad() && object->getTypeId().isDerivedFrom(type);
}

bool hasRecomputableTimelineSuppression(const App::DocumentObject* object)
{
    if (!object) {
        return false;
    }
    const Base::Type staticFeatureType = Base::Type::fromName("PartDesign::Feature");
    // The concrete PartDesign::Feature is a static shape container. Its
    // Suppressed implementation replaces Shape with the upstream result, but
    // it has no execute() implementation which can regenerate the accepted
    // shape when suppression is cleared. Body Tip and result visibility are
    // sufficient to move this non-computational object through History.
    return staticFeatureType.isBad() || object->getTypeId() != staticFeatureType;
}

bool hasExplicitTimelineRole(const App::DocumentObject* object, std::string_view role)
{
    if (!object) {
        return false;
    }
    const auto* property = dynamic_cast<const App::PropertyString*>(
        object->getPropertyByName(App::DocumentTimeline::RolePropertyName)
    );
    return property && std::string_view(property->getValue()) == role;
}

bool isInternalTimelineChild(
    const App::DocumentObject* object,
    const std::unordered_set<App::DocumentObject*>& internalTransformations,
    const ModelTreeBrowserProjection::Entry* entry
)
{
    return !object || internalTransformations.contains(const_cast<App::DocumentObject*>(object))
        || (entry && entry->publishedImplementation);
}

bool isInternalTransformedFeature(const App::DocumentObject* object)
{
    if (!isDerivedFrom(object, "PartDesign::Transformed")) {
        return false;
    }
    const auto* transformMode = dynamic_cast<const App::PropertyEnumeration*>(
        object->getPropertyByName("TransformMode")
    );
    const auto* originals = dynamic_cast<const App::PropertyLinkList*>(
        object->getPropertyByName("Originals")
    );
    return transformMode && originals && transformMode->getValue() == 0
        && originals->getValues().empty();
}

bool canBeBodyTip(
    const App::DocumentObject* object,
    const std::unordered_set<App::DocumentObject*>& internalTransformations
)
{
    if (!object || internalTransformations.contains(const_cast<App::DocumentObject*>(object))
        || App::DocumentTimeline::hasTimelineResourceRole(object)
        || hasExplicitTimelineRole(object, App::DocumentTimeline::InternalRole)
        || isInternalTransformedFeature(object) || !isDerivedFrom(object, "Part::Feature")
        || isDerivedFrom(object, "Part::Part2DObject") || isDerivedFrom(object, "Part::BodyBase")
        || isDerivedFrom(object, "Part::Datum")) {
        return false;
    }

    if (isDerivedFrom(object, "App::DatumElement")) {
        return false;
    }
    return !isDerivedFrom(object, "PartDesign::ShapeBinder")
        && !isDerivedFrom(object, "PartDesign::SubShapeBinder");
}

bool isNavigableTimelineResult(
    const App::DocumentObject* object,
    const std::unordered_set<App::DocumentObject*>& internalTransformations,
    const ModelTreeBrowserProjection& projection
)
{
    return canBeBodyTip(object, internalTransformations)
        && !isInternalTimelineChild(object, internalTransformations, projection.find(object));
}

std::unordered_set<App::DocumentObject*> internalTransformationChildren(
    const std::vector<App::DocumentObject*>& members
)
{
    std::unordered_set<App::DocumentObject*> result;
    for (auto* object : members) {
        if (!object
            || std::string_view(object->getTypeId().getName()).find("MultiTransform")
                == std::string_view::npos) {
            continue;
        }
        const auto* transformations = dynamic_cast<const App::PropertyLinkList*>(
            object->getPropertyByName("Transformations")
        );
        if (!transformations) {
            continue;
        }
        for (auto* child : transformations->getValues()) {
            if (child) {
                result.insert(child);
            }
        }
    }
    return result;
}

bool isVisibleTimelineOperation(
    const App::DocumentObject* object,
    const std::unordered_set<App::DocumentObject*>& internalTransformations,
    const ModelTreeBrowserProjection& projection
)
{
    // A task's auto-enrolled object is still provisional preview state until
    // its owning transaction commits or explicitly publishes a semantic
    // block. It must never appear beside durable document operations.
    if (!object || object->isDerivedFrom<App::DocumentTimeline>()
        || App::DocumentTimeline::hasTimelineResourceRole(object)
        || hasExplicitTimelineRole(object, App::DocumentTimeline::InternalRole)
        || (object->getDocument()
            && object->getDocument()
                   ->isProvisionallyEnrolledInTimelineByCurrentTransaction(object))
        || isInternalTransformedFeature(object)
        || internalTransformations.contains(const_cast<App::DocumentObject*>(object))) {
        return false;
    }

    // A workbench's explicit, persisted operation contract is more precise
    // than the browser's generic presentation category or even the presence
    // of a tree-projection entry.  History is saved modeling state, while the
    // tree is only one presentation of that state.  For example, a reusable
    // Design sketch and a CAM Job are durable operations even when the tree
    // projects them through a virtual folder or container.
    if (hasExplicitTimelineRole(object, App::DocumentTimeline::OperationRole)) {
        return true;
    }

    const auto* entry = projection.find(object);
    if (!entry) {
        return false;
    }
    if (entry->publishedImplementation
        || (entry->publishedOutput && entry->bodyRepresentation)) {
        return false;
    }

    using Role = ModelTreeBrowserProjection::Role;
    switch (entry->role) {
        case Role::Origin:
        case Role::OriginFeature:
        case Role::Group:
            return false;
        case Role::Component:
        case Role::Body:
        case Role::Parameter:
        case Role::Sketch:
        case Role::Feature:
        case Role::Geometry:
        case Role::Reference:
            return true;
        case Role::Construction:
            return true;
        case Role::History:
            return true;
        case Role::Internal:
            return false;
        case Role::AssemblyOccurrence:
        case Role::AssemblyMotion:
        case Role::AssemblyOperation:
            return true;
        case Role::Other:
            // A native object with no ViewProvider is application bookkeeping,
            // not a user operation. Other view-backed document objects (for
            // example an assembly joint or drawing page) remain real history.
            return object->getViewProviderNameStored()[0] != '\0';
    }
    return false;
}

const App::DocumentObject* semanticTimelineRoot(
    const App::DocumentObject* object,
    const App::Document* document
) noexcept
{
    if (!object || !document || !document->containsObject(object)
        || object->getDocument() != document) {
        return nullptr;
    }

    std::unordered_set<const App::DocumentObject*> visited;
    auto* current = object;
    while (App::DocumentTimeline::hasTimelineResourceRole(current)) {
        if (!visited.insert(current).second) {
            return nullptr;
        }
        current = App::DocumentTimeline::timelineOwner(current);
        if (!current || !document->containsObject(current)
            || current->getDocument() != document) {
            return nullptr;
        }
    }
    return current;
}

struct SemanticTimelineBlock
{
    int begin {-1};
    int end {-1};

    [[nodiscard]] bool isValid() const noexcept
    {
        return begin >= 0 && end > begin;
    }
};

class SemanticTimelineLayout
{
public:
    SemanticTimelineLayout(
        const std::vector<App::DocumentObject*>& operations,
        const App::Document* document
    ) noexcept
        : document(document)
    {
        if (!document) {
            return;
        }
        if (operations.empty()) {
            valid = true;
            return;
        }

        std::unordered_set<const App::DocumentObject*> operationIdentities;
        operationIdentities.reserve(operations.size());
        for (const auto* operation : operations) {
            if (!operation || !document->containsObject(operation)
                || operation->getDocument() != document
                || !operationIdentities.insert(operation).second) {
                return;
            }
        }

        blocks.reserve(operations.size());
        for (std::size_t index = 0; index < operations.size(); ++index) {
            const auto* root = semanticTimelineRoot(operations[index], document);
            if (!root || !operationIdentities.contains(root)) {
                return;
            }
            const auto position = static_cast<int>(index);
            auto& block = blocks[root];
            block.begin = block.begin < 0 ? position : std::min(block.begin, position);
            block.end = std::max(block.end, position + 1);
        }

        orderedBlocks.reserve(blocks.size());
        for (const auto& [root, block] : blocks) {
            (void)root;
            if (!block.isValid()) {
                return;
            }
            orderedBlocks.push_back(block);
        }
        std::ranges::sort(orderedBlocks, {}, &SemanticTimelineBlock::begin);
        for (std::size_t index = 1; index < orderedBlocks.size(); ++index) {
            if (orderedBlocks[index - 1].end > orderedBlocks[index].begin) {
                // Interleaved semantic blocks cannot expose an exact state
                // boundary. Refuse navigation instead of activating only part
                // of either operation.
                return;
            }
        }
        valid = true;
    }

    [[nodiscard]] bool isValid() const noexcept
    {
        return valid;
    }

    [[nodiscard]] SemanticTimelineBlock blockFor(
        const App::DocumentObject* operation
    ) const noexcept
    {
        if (!valid) {
            return {};
        }
        const auto* root = semanticTimelineRoot(operation, document);
        const auto found = blocks.find(root);
        return found == blocks.end() ? SemanticTimelineBlock {} : found->second;
    }

    [[nodiscard]] const std::vector<SemanticTimelineBlock>& allBlocks() const noexcept
    {
        return orderedBlocks;
    }

private:
    const App::Document* document {};
    std::unordered_map<const App::DocumentObject*, SemanticTimelineBlock> blocks;
    std::vector<SemanticTimelineBlock> orderedBlocks;
    bool valid {false};
};

bool hasActiveTimelineBypassSource(
    const App::DocumentObject* operation,
    const App::DocumentTimeline* timeline
)
{
    if (!operation || !timeline) {
        return false;
    }

    // An operation may expose a visible suppressed result only when that
    // result represents an upstream object which exists at the requested
    // history boundary.  Otherwise rolling before both Source and operation
    // would leak stale passthrough geometry into an empty document state.
    const auto* source = dynamic_cast<const App::PropertyLink*>(
        operation->getPropertyByName("Source")
    );
    if (source && source->getValue() && timeline->isOperationActive(source->getValue())) {
        return true;
    }

    // A semantic batch controller may represent several pre-existing source
    // objects. It can present the subset which exists at the requested
    // boundary; requiring every source would produce a blank viewport while
    // navigating between their creation steps.
    const auto* sources = dynamic_cast<const App::PropertyLinkList*>(
        operation->getPropertyByName("Sources")
    );
    return sources && std::ranges::any_of(sources->getValues(), [timeline](const auto* candidate) {
               return candidate && timeline->isOperationActive(candidate);
           });
}

App::DocumentObject* operationOwner(
    App::DocumentObject* object,
    const ModelTreeBrowserProjection& projection
)
{
    const auto* entry = projection.find(object);
    if (!entry) {
        return nullptr;
    }
    if (entry->role == ModelTreeBrowserProjection::Role::Body
        || entry->role == ModelTreeBrowserProjection::Role::Component) {
        return entry->object;
    }
    return entry->body ? entry->body : entry->component;
}

std::optional<bool> owningBodyVisibility(const App::DocumentObject* owner)
{
    if (!ModelTreeBrowserProjection::isBody(owner) || !Gui::Application::Instance) {
        return std::nullopt;
    }
    const auto* viewProvider = dynamic_cast<const Gui::ViewProviderDocumentObject*>(
        Gui::Application::Instance->getViewProvider(owner)
    );
    if (!viewProvider || !viewProvider->canToggleVisibility()) {
        return std::nullopt;
    }
    return viewProvider->isShow();
}

class TimelineApplyingGuard
{
public:
    explicit TimelineApplyingGuard(App::DocumentTimeline* timeline)
        : document(documentIdentity(timeline ? timeline->getDocument() : nullptr))
        , identity(objectIdentity(timeline))
    {
        if (timeline) {
            timeline->setApplying(true);
        }
    }

    ~TimelineApplyingGuard() noexcept
    {
        try {
            auto* currentDocument = resolveDocument(document);
            if (auto* timeline
                = dynamic_cast<App::DocumentTimeline*>(resolveObject(currentDocument, identity))) {
                timeline->setApplying(false);
            }
        }
        catch (...) {
            Base::Console().error("Feature timeline could not release its applying guard\n");
        }
    }

    TimelineApplyingGuard(const TimelineApplyingGuard&) = delete;
    TimelineApplyingGuard& operator=(const TimelineApplyingGuard&) = delete;

private:
    TimelineDocumentIdentity document;
    TimelineObjectIdentity identity;
};

class TimelineTransactionLock
{
public:
    explicit TimelineTransactionLock(App::Document* document)
        : document(documentIdentity(document))
    {
        activate(true);
    }

    ~TimelineTransactionLock() noexcept
    {
        activate(false);
    }

    TimelineTransactionLock(const TimelineTransactionLock&) = delete;
    TimelineTransactionLock& operator=(const TimelineTransactionLock&) = delete;

    void activate(bool enable) noexcept
    {
        if (active == enable) {
            return;
        }
        if (enable) {
            try {
                auto* currentDocument = resolveDocument(document);
                if (!currentDocument) {
                    return;
                }
                active = true;
                currentDocument->lockTransaction();
            }
            catch (Base::Exception& error) {
                error.reportException();
                if (active) {
                    release("release its failed transaction lock");
                }
            }
            catch (const std::exception& error) {
                Base::Console().error(
                    "Feature timeline could not lock its transaction: %s\n",
                    error.what()
                );
                if (active) {
                    release("release its failed transaction lock");
                }
            }
            catch (...) {
                Base::Console().error(
                    "Feature timeline could not lock its transaction due to an unknown exception\n"
                );
                if (active) {
                    release("release its failed transaction lock");
                }
            }
            return;
        }

        // Clear the flag before publishing unlock signals. A callback may
        // synchronously close the document; destruction must then be a no-op.
        release("unlock its transaction");
    }

    [[nodiscard]] bool isActive() const
    {
        return active;
    }

private:
    void release(const char* action) noexcept
    {
        active = false;
        try {
            if (auto* currentDocument = resolveDocument(document)) {
                currentDocument->unlockTransaction();
            }
        }
        catch (Base::Exception& error) {
            error.reportException();
        }
        catch (const std::exception& error) {
            Base::Console().error("Feature timeline could not %s: %s\n", action, error.what());
        }
        catch (...) {
            Base::Console().error("Feature timeline could not %s due to an unknown exception\n", action);
        }
    }

    TimelineDocumentIdentity document;
    bool active {false};
};

class TimelineListWidget final: public QListWidget
{
public:
    struct MarkerDragIdentity
    {
        QString documentName;
        std::uint64_t documentGeneration {};

        bool isValid() const
        {
            return !documentName.isEmpty();
        }
    };

    using MarkerDropHandler
        = std::function<void(const QListWidgetItem*, bool toEnd, const MarkerDragIdentity&)>;

    TimelineListWidget(
        int objectNameRole,
        int markerRole,
        int documentNameRole,
        int documentGenerationRole,
        QWidget* parent
    )
        : QListWidget(parent)
        , objectNameRole(objectNameRole)
        , markerRole(markerRole)
        , documentNameRole(documentNameRole)
        , documentGenerationRole(documentGenerationRole)
        , dropIndicator(new QFrame(viewport()))
    {
        dropIndicator->setObjectName(QStringLiteral("VibeCADFeatureTimelineDropIndicator"));
        dropIndicator->setFrameShape(QFrame::VLine);
        dropIndicator->setFrameShadow(QFrame::Plain);
        dropIndicator->setLineWidth(2);
        dropIndicator->setAttribute(Qt::WA_TransparentForMouseEvents);
        QPalette indicatorPalette = dropIndicator->palette();
        indicatorPalette.setBrush(
            QPalette::WindowText,
            palette().brush(QPalette::Active, QPalette::Highlight)
        );
        dropIndicator->setPalette(indicatorPalette);
        dropIndicator->hide();
    }

    void setMarkerDropHandler(MarkerDropHandler handler)
    {
        markerDropHandler = std::move(handler);
    }

    void cancelMarkerDrag()
    {
        markerDragArmed = false;
        markerDragging = false;
        markerDragIdentity = {};
        viewport()->unsetCursor();
        dropIndicator->hide();
        viewport()->update();
    }

protected:
    void mousePressEvent(QMouseEvent* event) override
    {
        cancelMarkerDrag();
        auto* pressedItem = itemAt(mouseEventPosition(event));
        if (event->button() == Qt::LeftButton && isMarker(pressedItem)) {
            markerDragIdentity = {
                pressedItem->data(documentNameRole).toString(),
                static_cast<std::uint64_t>(pressedItem->data(documentGenerationRole).toULongLong()),
            };
            if (!markerDragIdentity.isValid()) {
                QListWidget::mousePressEvent(event);
                return;
            }
            markerDragArmed = true;
            markerPressPosition = mouseEventPosition(event);
            event->accept();
            return;
        }
        QListWidget::mousePressEvent(event);
    }

    void mouseMoveEvent(QMouseEvent* event) override
    {
        if (!markerDragArmed) {
            QListWidget::mouseMoveEvent(event);
            return;
        }

        const QPoint position = mouseEventPosition(event);
        if (!markerDragging
            && (position - markerPressPosition).manhattanLength()
                >= QApplication::startDragDistance()) {
            markerDragging = true;
            viewport()->setCursor(Qt::ClosedHandCursor);
        }
        if (markerDragging) {
            updateDropIndicator(position);
        }
        event->accept();
    }

    void mouseReleaseEvent(QMouseEvent* event) override
    {
        if (!markerDragArmed || event->button() != Qt::LeftButton) {
            QListWidget::mouseReleaseEvent(event);
            return;
        }

        const bool commitDrop = markerDragging;
        const QPoint position = mouseEventPosition(event);
        const MarkerDragIdentity identity = markerDragIdentity;
        cancelMarkerDrag();

        if (commitDrop && markerDropHandler) {
            if (auto* beforeItem = firstObjectAfter(position.x())) {
                markerDropHandler(beforeItem, false, identity);
            }
            else {
                markerDropHandler(nullptr, true, identity);
            }
        }
        event->accept();
    }

private:
    bool isMarker(const QListWidgetItem* item) const
    {
        return item && item->data(markerRole).toBool();
    }

    QListWidgetItem* firstObjectAfter(int x) const
    {
        QListWidgetItem* result = nullptr;
        int resultCenter = std::numeric_limits<int>::max();
        for (int row = 0; row < count(); ++row) {
            auto* item = this->item(row);
            if (!item || isMarker(item) || item->data(objectNameRole).toString().isEmpty()) {
                continue;
            }
            const QRect rect = visualItemRect(item);
            const int center = rect.center().x();
            if (center > x && center < resultCenter) {
                result = item;
                resultCenter = center;
            }
        }
        return result;
    }

    void updateDropIndicator(const QPoint& position)
    {
        int x = position.x();
        if (auto* beforeItem = firstObjectAfter(position.x())) {
            x = visualItemRect(beforeItem).left() - spacing();
        }
        else {
            int lastRight = 0;
            for (int row = 0; row < count(); ++row) {
                auto* item = this->item(row);
                if (!item || isMarker(item) || item->data(objectNameRole).toString().isEmpty()) {
                    continue;
                }
                lastRight = std::max(lastRight, visualItemRect(item).right() + spacing());
            }
            x = lastRight;
        }
        x = std::clamp(x, 0, std::max(0, viewport()->width() - 1));
        dropIndicator->setGeometry(x, 2, 2, std::max(1, viewport()->height() - 4));
        dropIndicator->raise();
        dropIndicator->show();
    }

    const int objectNameRole;
    const int markerRole;
    const int documentNameRole;
    const int documentGenerationRole;
    QFrame* dropIndicator {};
    MarkerDropHandler markerDropHandler;
    MarkerDragIdentity markerDragIdentity;
    QPoint markerPressPosition;
    bool markerDragArmed {false};
    bool markerDragging {false};
};

}  // namespace

FeatureTimeline::FeatureTimeline(QWidget* parent)
    : QWidget(parent)
    , SelectionObserver(true, ResolveMode::OldStyleElement)
{
    clearDocumentScope();
    setObjectName(QStringLiteral("VibeCADFeatureTimeline"));
    setAccessibleName(tr("Feature timeline"));
    setAccessibleDescription(tr("Ordered native modeling history for the active document"));
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    setFixedHeight(timelineHeight);
    setAutoFillBackground(true);

    auto* layout = new QHBoxLayout(this);
    layout->setContentsMargins(6, 3, 6, 3);
    layout->setSpacing(6);

    auto makeNavigationButton = [this, layout](
                                    const QString& objectName,
                                    QStyle::StandardPixmap icon,
                                    const QString& accessibleName,
                                    const QString& toolTip
                                ) {
        auto* button = new QToolButton(this);
        button->setObjectName(objectName);
        button->setAccessibleName(accessibleName);
        button->setToolTip(toolTip);
        button->setIcon(style()->standardIcon(icon));
        button->setAutoRaise(true);
        button->setFixedSize(24, 24);
        button->setEnabled(false);
        layout->addWidget(button);
        return button;
    };
    startButton = makeNavigationButton(
        QStringLiteral("VibeCADFeatureTimelineStart"),
        QStyle::SP_MediaSkipBackward,
        tr("Move current model state to start"),
        tr("Move the current model state to the start of history")
    );
    previousButton = makeNavigationButton(
        QStringLiteral("VibeCADFeatureTimelinePrevious"),
        QStyle::SP_MediaSeekBackward,
        tr("Move current model state to previous operation"),
        tr("Move the current model state to the previous operation")
    );
    nextButton = makeNavigationButton(
        QStringLiteral("VibeCADFeatureTimelineNext"),
        QStyle::SP_MediaSeekForward,
        tr("Move current model state to next operation"),
        tr("Move the current model state to the next operation")
    );
    endButton = makeNavigationButton(
        QStringLiteral("VibeCADFeatureTimelineEnd"),
        QStyle::SP_MediaSkipForward,
        tr("Move current model state to end"),
        tr("Move the current model state to the end of history")
    );

    auto* separator = new QFrame(this);
    separator->setObjectName(QStringLiteral("VibeCADFeatureTimelineSeparator"));
    separator->setFrameShape(QFrame::VLine);
    separator->setFrameShadow(QFrame::Sunken);
    layout->addWidget(separator);

    auto* timelineList = new TimelineListWidget(
        ObjectNameRole,
        IsMarkerRole,
        DocumentNameRole,
        DocumentGenerationRole,
        this
    );
    timeline = timelineList;
    timeline->setObjectName(QStringLiteral("VibeCADFeatureTimelineItems"));
    timeline->setAccessibleName(tr("Feature timeline operations"));
    timeline->setViewMode(QListView::IconMode);
    timeline->setFlow(QListView::LeftToRight);
    timeline->setWrapping(false);
    timeline->setMovement(QListView::Static);
    timeline->setResizeMode(QListView::Adjust);
    timeline->setSelectionMode(QAbstractItemView::ExtendedSelection);
    timeline->setHorizontalScrollMode(QAbstractItemView::ScrollPerPixel);
    timeline->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
    timeline->horizontalScrollBar()->setFixedHeight(6);
    timeline->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    timeline->setFrameShape(QFrame::NoFrame);
    timeline->setSpacing(2);
    timeline->setIconSize(QSize(22, 22));
    timeline->setContextMenuPolicy(Qt::CustomContextMenu);
    layout->addWidget(timeline, 1);
    timelineList->setMarkerDropHandler([this](
                                           const QListWidgetItem* beforeItem,
                                           bool toEnd,
                                           const TimelineListWidget::MarkerDragIdentity& identity
                                       ) {
        auto* document = activeAppDocument();
        if (!document || document->getName() != identity.documentName.toStdString()
            || observedDocumentGeneration != identity.documentGeneration) {
            return;
        }
        if (beforeItem
            && (beforeItem->data(DocumentNameRole).toString() != identity.documentName
                || beforeItem->data(DocumentGenerationRole).toULongLong()
                    != identity.documentGeneration)) {
            return;
        }
        const auto* controller = App::DocumentTimeline::get(document);
        if (!controller) {
            return;
        }
        int position = static_cast<int>(controller->Operations.getSize());
        if (!toEnd && beforeItem) {
            const int operationIndex = beforeItem->data(OperationIndexRole).toInt();
            const QByteArray objectName = beforeItem->data(ObjectNameRole).toString().toUtf8();
            const long objectId = beforeItem->data(ObjectIdRole).toLongLong();
            if (operationIndex < 0 || operationIndex >= controller->Operations.getSize()
                || controller->Operations[operationIndex]
                    != resolveObject(document, objectName.constData(), objectId)) {
                return;
            }
            const auto operations = controller->Operations.getValues();
            const SemanticTimelineLayout layout(operations, document);
            const auto block = layout.blockFor(controller->Operations[operationIndex]);
            if (!block.isValid()) {
                return;
            }
            position = block.begin;
        }
        moveCurrentStateToPosition(!toEnd && !beforeItem ? 0 : position);
    });

    refreshTimer = new QTimer(this);
    refreshTimer->setSingleShot(true);
    refreshTimer->setInterval(0);

    connect(refreshTimer, &QTimer::timeout, this, &FeatureTimeline::rebuild);
    connect(
        timeline,
        &QListWidget::itemSelectionChanged,
        this,
        &FeatureTimeline::onTimelineSelectionChanged
    );
    connect(timeline, &QListWidget::itemDoubleClicked, this, &FeatureTimeline::onTimelineItemDoubleClicked);
    connect(
        timeline,
        &QListWidget::customContextMenuRequested,
        this,
        &FeatureTimeline::onTimelineContextMenu
    );
    connect(startButton, &QToolButton::clicked, this, [this]() { navigateCurrentState(-2); });
    connect(previousButton, &QToolButton::clicked, this, [this]() { navigateCurrentState(-1); });
    connect(nextButton, &QToolButton::clicked, this, [this]() { navigateCurrentState(1); });
    connect(endButton, &QToolButton::clicked, this, [this]() { navigateCurrentState(2); });

    if (Gui::Application::Instance) {
        activeDocumentConnection = Gui::Application::Instance->signalActiveDocument.connect(
            [this](const Gui::Document& document) {
                setObservedDocument(const_cast<Gui::Document*>(&document));
            }
        );
        activeViewConnection = Gui::Application::Instance->signalActivateView.connect(
            [this](const Gui::MDIView*) { scheduleRefresh(); }
        );
        renamedDocumentConnection = Gui::Application::Instance->signalRenameDocument.connect(
            [this](const Gui::Document& document) {
                if (document.getDocument() != observedAppDocument) {
                    return;
                }
                observedDocumentName = observedAppDocument ? observedAppDocument->getName() : "";
                ++observedDocumentGeneration;
                scheduleRefresh();
            }
        );
        setObservedDocument(Gui::Application::Instance->activeDocument());
    }
    else {
        scheduleRefresh();
    }
}

FeatureTimeline::~FeatureTimeline()
{
    detachDocument();
}

App::Document* FeatureTimeline::activeAppDocument() const
{
    if (!observedAppDocument || observedDocumentName.empty()) {
        return nullptr;
    }
    auto* document = App::GetApplication().getDocument(observedDocumentName.c_str());
    return document == observedAppDocument ? document : nullptr;
}

void FeatureTimeline::setObservedDocument(Gui::Document* document)
{
    auto* nextDocument = document ? document->getDocument() : nullptr;
    const std::string nextName = nextDocument ? nextDocument->getName() : "";
    if (nextDocument == observedAppDocument) {
        if (nextName != observedDocumentName) {
            if (auto* timelineList = dynamic_cast<TimelineListWidget*>(timeline)) {
                timelineList->cancelMarkerDrag();
            }
            observedDocumentName = nextName;
            ++observedDocumentGeneration;
        }
        scheduleRefresh();
        return;
    }

    if (auto* timelineList = dynamic_cast<TimelineListWidget*>(timeline)) {
        timelineList->cancelMarkerDrag();
    }
    bookedTransactionConnection.disconnect();
    stableDocumentConnection.disconnect();
    changedObjectConnection.disconnect();
    touchedObjectConnection.disconnect();
    recomputedObjectConnection.disconnect();
    detachDocument();
    observedAppDocument = nextDocument;
    observedDocumentName = nextName;
    ++observedDocumentGeneration;

    if (document && nextDocument) {
        attachDocument(document);
        bookedTransactionConnection = document->getDocument()->signalBookedTransactionChanged.connect(
            [this](const App::Document&, int, int) { scheduleRefresh(); }
        );
        stableDocumentConnection = document->getDocument()->signalBecameStable.connect(
            [this](const App::Document&) { scheduleRefresh(); }
        );
        changedObjectConnection = document->getDocument()->signalChangedObject.connect(
            [this](const App::DocumentObject&, const App::Property&) { scheduleRefresh(); }
        );
        touchedObjectConnection = document->getDocument()->signalTouchedObject.connect(
            [this](const App::DocumentObject&) { scheduleRefresh(); }
        );
        recomputedObjectConnection = document->getDocument()->signalRecomputedObject.connect(
            [this](const App::DocumentObject&) { scheduleRefresh(); }
        );
    }

    scheduleRefresh();
}

void FeatureTimeline::scheduleRefresh()
{
    if (refreshTimer && !refreshTimer->isActive()) {
        refreshTimer->start();
    }
}

bool FeatureTimeline::canChangeHistory() const
{
    auto* document = activeAppDocument();
    if (!document || document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction() || document->isPerformingTransaction()
        || document->isTransactionLocked() || document->testStatus(App::Document::Recomputing)
        || App::GetApplication().hasPendingRecomputeRequest(document->getName())
        || App::GetApplication().isRestoring() || !Gui::Application::Instance) {
        return false;
    }

    auto* guiDocument = Gui::Application::Instance->getDocument(document);
    auto* editProvider = guiDocument ? guiDocument->getEditViewProvider() : nullptr;
    const bool editAllowsHistory = !editProvider
        || editProvider->allowsDocumentTimelineNavigationInEdit();
    return guiDocument && Gui::Application::Instance->activeDocument() == guiDocument
        && editAllowsHistory && !Gui::Control().activeDialog(document);
}

void FeatureTimeline::rebuild()
{
    App::Document* document = activeAppDocument();
    if (document
        && (document->isPerformingTransaction() || document->testStatus(App::Document::Recomputing)
            || App::GetApplication().hasPendingRecomputeRequest(document->getName())
            || App::GetApplication().isRestoring())) {
        // Undo, redo, and transaction rollback recreate objects in dependency
        // order, and recompute may replace generated results. Wait for the
        // corresponding stable/recomputed signal before resolving item names.
        timeline->setEnabled(false);
        startButton->setEnabled(false);
        previousButton->setEnabled(false);
        nextButton->setEnabled(false);
        endButton->setEnabled(false);
        return;
    }

    if (auto* timelineList = dynamic_cast<TimelineListWidget*>(timeline)) {
        timelineList->cancelMarkerDrag();
    }
    QScopedValueRollback rebuildingGuard(rebuildingTimeline, true);
    QSignalBlocker timelineBlocker(timeline);

    timeline->clear();
    timeline->setEnabled(false);
    startButton->setEnabled(false);
    previousButton->setEnabled(false);
    nextButton->setEnabled(false);
    endButton->setEnabled(false);

    if (!document) {
        auto* empty = new QListWidgetItem(tr("Open a document to see its feature history"));
        empty->setFlags(Qt::NoItemFlags);
        timeline->addItem(empty);
        return;
    }

    auto* controller = App::DocumentTimeline::get(document);
    if (!controller) {
        auto* empty = new QListWidgetItem(tr("Create a modeling operation to begin"));
        empty->setFlags(Qt::NoItemFlags);
        timeline->addItem(empty);
        return;
    }

    const auto operations = controller->Operations.getValues();
    const int position = static_cast<int>(
        std::clamp(controller->Position.getValue(), 0L, static_cast<long>(operations.size()))
    );
    const SemanticTimelineLayout semanticLayout(operations, document);
    ModelTreeBrowserProjection projection(document);
    const auto internalTransformations = internalTransformationChildren(operations);
    const bool historyEnabled = canChangeHistory() && semanticLayout.isValid();
    timeline->setEnabled(historyEnabled);

    const bool transactionBusy = document->getBookedTransactionID() != App::NullTransaction
        || document->hasPendingTransaction() || document->isPerformingTransaction()
        || document->isTransactionLocked();

    struct VisibleSemanticBlock
    {
        SemanticTimelineBlock block;
        bool active {false};
    };
    std::vector<VisibleSemanticBlock> visibleBlocks;
    visibleBlocks.reserve(operations.size());
    int lastActiveVisible = -1;
    for (std::size_t index = 0; index < operations.size(); ++index) {
        if (!isVisibleTimelineOperation(operations[index], internalTransformations, projection)) {
            continue;
        }
        const auto block = semanticLayout.blockFor(operations[index]);
        if (!block.isValid()) {
            continue;
        }
        const bool active = controller->isOperationActive(operations[index]);
        visibleBlocks.push_back({block, active});
        if (active) {
            lastActiveVisible = static_cast<int>(index);
        }
    }
    const auto activeVisibleCount = static_cast<int>(std::ranges::count_if(
        visibleBlocks,
        [](const VisibleSemanticBlock& visible) {
            return visible.active;
        }
    ));
    timeline->setToolTip(
        !historyEnabled
            ? !semanticLayout.isValid()
                ? tr("Document history has invalid operation ownership")
                : transactionBusy
                ? tr("Wait for the current document operation to finish")
                : App::GetApplication().isRestoring()
                ? tr("Wait for the document to finish opening")
                : tr("Finish or cancel the active task before changing model history")
            : tr("Document history: %1 of %2 operations active")
                  .arg(activeVisibleCount)
                  .arg(static_cast<int>(visibleBlocks.size()))
    );
    const bool hasPrevious = std::ranges::any_of(
        visibleBlocks,
        [position](const VisibleSemanticBlock& visible) {
            return visible.active && visible.block.begin < position;
        }
    );
    const bool hasNext = std::ranges::any_of(
        visibleBlocks,
        [position](const VisibleSemanticBlock& visible) {
            return !visible.active && visible.block.end > position;
        }
    );
    startButton->setEnabled(historyEnabled && position > 0);
    previousButton->setEnabled(historyEnabled && hasPrevious);
    nextButton->setEnabled(historyEnabled && hasNext);
    endButton->setEnabled(historyEnabled && position < static_cast<int>(operations.size()));

    int operationCount = 0;
    bool markerAdded = false;
    QListWidgetItem* stateMarker = nullptr;
    auto addCurrentStateMarker = [&]() {
        auto* marker = new QListWidgetItem(tr("▮"));
        marker->setData(DocumentNameRole, QString::fromStdString(document->getName()));
        marker->setData(
            DocumentGenerationRole,
            QVariant::fromValue<qulonglong>(observedDocumentGeneration)
        );
        marker->setData(IsCurrentRole, false);
        marker->setData(IsAfterPositionRole, false);
        marker->setData(OperationIndexRole, position);
        marker->setData(IsMarkerRole, true);
        marker->setData(Qt::AccessibleTextRole, tr("Current document state"));
        marker->setFlags(Qt::ItemIsEnabled);
        QFont font = timeline->font();
        font.setBold(true);
        marker->setFont(font);
        marker->setForeground(palette().brush(QPalette::Active, QPalette::Highlight));
        marker->setToolTip(tr("Current document state\n"
                              "Drag to roll the complete document backward or forward"));
        marker->setSizeHint(QSize(22, timelineItemHeight));
        timeline->addItem(marker);
        stateMarker = marker;
        markerAdded = true;
    };

    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* object = operations[index];
        if (!object || !object->isAttachedToDocument()
            || !isVisibleTimelineOperation(object, internalTransformations, projection)) {
            continue;
        }

        if (!markerAdded && static_cast<int>(index) >= position) {
            addCurrentStateMarker();
        }

        const bool isCurrent = static_cast<int>(index) == lastActiveVisible;
        const bool afterPosition = !controller->isOperationActive(object);
        auto* owner = operationOwner(object, projection);
        const std::optional<bool> bodyVisible = owningBodyVisibility(owner);
        const QString label = objectLabel(object);
        const QString ownerLabel = owner && owner != object ? objectLabel(owner) : QString();
        const QString statusText = timelineStatusText(object);
        auto* item = new QListWidgetItem;
        item->setData(ObjectNameRole, QString::fromUtf8(object->getNameInDocument()));
        item->setData(ObjectIdRole, QVariant::fromValue<qlonglong>(object->getID()));
        item->setData(
            OwnerNameRole,
            owner && owner->getNameInDocument() ? QString::fromUtf8(owner->getNameInDocument())
                                                : QString()
        );
        item->setData(OwnerIdRole, QVariant::fromValue<qlonglong>(owner ? owner->getID() : -1));
        item->setData(DocumentNameRole, QString::fromStdString(document->getName()));
        item->setData(
            DocumentGenerationRole,
            QVariant::fromValue<qulonglong>(observedDocumentGeneration)
        );
        item->setData(IsCurrentRole, isCurrent);
        item->setData(IsAfterPositionRole, afterPosition);
        item->setData(OperationIndexRole, static_cast<qlonglong>(index));
        item->setData(IsMarkerRole, false);
        item->setData(
            Qt::AccessibleTextRole,
            statusText.isEmpty() ? label : tr("%1, %2").arg(label, statusText)
        );

        if (Gui::Application::Instance) {
            const auto* editor = App::DocumentTimeline::timelineEditor(object);
            const auto* iconObject = editor ? editor : object;
            if (auto* viewProvider = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                    Gui::Application::Instance->getViewProvider(iconObject)
                )) {
                const QIcon icon = viewProvider->getIcon();
                item->setIcon(
                    timelineObjectIcon(
                        icon,
                        object,
                        afterPosition,
                        bodyVisible.has_value() && !*bodyVisible
                    )
                );
            }
        }

        QFont font = timeline->font();
        font.setBold(isCurrent);
        if (object->hasExtension(App::SuppressibleExtension::getExtensionClassTypeId())) {
            if (auto* suppressible = object->getExtensionByType<App::SuppressibleExtension>()) {
                font.setStrikeOut(suppressible->Suppressed.getValue());
            }
        }
        item->setFont(font);

        const QString ownershipText = ownerLabel.isEmpty() ? QString()
                                                           : tr("\nPart: %1").arg(ownerLabel);
        const QString visibilityText = !bodyVisible.has_value()
            ? QString()
            : *bodyVisible ? tr("\nBody visibility: Visible")
                           : tr("\nBody visibility: Hidden");
        if (afterPosition) {
            item->setForeground(palette().brush(QPalette::Disabled, QPalette::Text));
            item->setToolTip(
                statusText.isEmpty()
                    ? tr("%1%2%3\nAfter the current document state")
                          .arg(label, ownershipText, visibilityText)
                    : tr("%1%2%3\n%4\nAfter the current document state")
                          .arg(label, ownershipText, visibilityText, statusText)
            );
        }
        else if (isCurrent) {
            item->setForeground(palette().brush(QPalette::Active, QPalette::Link));
            item->setToolTip(
                statusText.isEmpty()
                    ? tr("%1%2%3\nCurrent document state")
                          .arg(label, ownershipText, visibilityText)
                    : tr("%1%2%3\n%4\nCurrent document state")
                          .arg(label, ownershipText, visibilityText, statusText)
            );
        }
        else {
            item->setToolTip(
                statusText.isEmpty()
                    ? tr("%1%2%3").arg(label, ownershipText, visibilityText)
                    : tr("%1%2%3\n%4").arg(label, ownershipText, visibilityText, statusText)
            );
        }

        item->setSizeHint(QSize(timelineItemWidth, timelineItemHeight));
        timeline->addItem(item);
        ++operationCount;
    }

    if (!markerAdded) {
        addCurrentStateMarker();
    }

    if (operationCount == 0) {
        auto* empty = new QListWidgetItem(tr("No modeling operations in this document"));
        empty->setFlags(Qt::NoItemFlags);
        timeline->addItem(empty);
    }

    timelineBlocker.unblock();
    if (stateMarker) {
        // QListWidget resets its horizontal position when rebuilt. Reveal the
        // end-of-history marker first; syncSelectionFromGui() deliberately runs
        // afterward so an ordinary object selection still takes precedence.
        timeline->scrollToItem(stateMarker, QAbstractItemView::EnsureVisible);
    }
    syncSelectionFromGui();
}

void FeatureTimeline::activateOwningBody(App::DocumentObject* object)
{
    if (!object || !Gui::Application::Instance) {
        return;
    }
    ModelTreeBrowserProjection projection(object->getDocument());
    const auto* entry = projection.find(object);
    auto* body = entry ? entry->bodyRepresentation                  ? entry->bodyRepresentation
            : entry->role == ModelTreeBrowserProjection::Role::Body ? entry->object
                                                                    : entry->body
                       : nullptr;
    if (!body) {
        return;
    }
    auto* appDocument = body->getDocument();
    const std::uint64_t documentGeneration = observedDocumentGeneration;
    const TimelineObjectIdentity bodyIdentity = objectIdentity(body);
    auto* guiDocument = Gui::Application::Instance->getDocument(appDocument);
    auto* viewProvider = dynamic_cast<Gui::ViewProviderDocumentObject*>(
        Gui::Application::Instance->getViewProvider(body)
    );
    if (!guiDocument || !viewProvider) {
        return;
    }
    guiDocument->setActiveView(viewProvider);
    auto* currentDocument = activeAppDocument();
    auto* currentGuiDocument = currentDocument == appDocument
            && observedDocumentGeneration == documentGeneration
        ? Gui::Application::Instance->getDocument(currentDocument)
        : nullptr;
    auto* currentBody = resolveObject(currentDocument, bodyIdentity);
    if (currentGuiDocument && currentBody) {
        if (auto* view = currentGuiDocument->getActiveView();
            view && !view->isActiveObject(currentBody, PDBODYKEY)) {
            view->setActiveObject(currentBody, PDBODYKEY);
        }
    }
}

App::DocumentObject* FeatureTimeline::objectForItem(const QListWidgetItem* item) const
{
    App::Document* document = activeAppDocument();
    if (!itemBelongsToObservedDocument(item) || !document) {
        return nullptr;
    }
    const QByteArray objectName = item->data(ObjectNameRole).toString().toUtf8();
    if (objectName.isEmpty()) {
        return nullptr;
    }
    return resolveObject(document, objectName.constData(), item->data(ObjectIdRole).toLongLong());
}

bool FeatureTimeline::itemBelongsToObservedDocument(const QListWidgetItem* item) const
{
    return item && activeAppDocument()
        && item->data(DocumentNameRole).toString().toStdString() == observedDocumentName
        && item->data(DocumentGenerationRole).toULongLong() == observedDocumentGeneration;
}

void FeatureTimeline::onTimelineSelectionChanged()
{
    if (rebuildingTimeline || syncingSelection) {
        return;
    }

    QScopedValueRollback syncingGuard(syncingSelection, true);
    auto* document = activeAppDocument();
    if (!document) {
        return;
    }
    const std::string documentName = document->getName();
    const std::uint64_t documentGeneration = observedDocumentGeneration;
    std::vector<TimelineObjectIdentity> selectedIdentities;
    selectedIdentities.reserve(timeline->selectedItems().size());
    for (auto* item : timeline->selectedItems()) {
        if (auto* object = objectForItem(item); object && object->isAttachedToDocument()) {
            selectedIdentities.push_back(objectIdentity(object));
        }
    }

    Gui::Selection().clearSelection();
    for (const auto& identity : selectedIdentities) {
        document = activeAppDocument();
        if (!document || document->getName() != documentName
            || observedDocumentGeneration != documentGeneration) {
            return;
        }
        auto* object = resolveObject(document, identity);
        if (!object) {
            continue;
        }
        Gui::Selection().addSelection(documentName.c_str(), object->getNameInDocument());
    }
}

void FeatureTimeline::syncSelectionFromGui()
{
    if (syncingSelection) {
        return;
    }
    App::Document* document = activeAppDocument();
    QScopedValueRollback syncingGuard(syncingSelection, true);
    QSignalBlocker blocker(timeline);

    std::unordered_set<App::DocumentObject*> selectedObjects;
    if (document) {
        for (const auto& selection :
             Gui::Selection().getSelection(document->getName(), ResolveMode::OldStyleElement)) {
            if (selection.pObject) {
                selectedObjects.insert(selection.pObject);
            }
        }
    }

    QListWidgetItem* firstSelected = nullptr;
    for (int row = 0; row < timeline->count(); ++row) {
        auto* item = timeline->item(row);
        const bool selected = selectedObjects.contains(objectForItem(item));
        item->setSelected(selected);
        if (selected && !firstSelected) {
            firstSelected = item;
        }
    }
    if (firstSelected) {
        timeline->setCurrentItem(firstSelected);
        timeline->scrollToItem(firstSelected, QAbstractItemView::EnsureVisible);
    }
}

void FeatureTimeline::onSelectionChanged(const SelectionChanges& message)
{
    if (syncingSelection) {
        return;
    }
    switch (message.Type) {
        case SelectionChanges::AddSelection:
        case SelectionChanges::SetSelection:
        case SelectionChanges::RmvSelection:
        case SelectionChanges::ClrSelection:
            syncSelectionFromGui();
            // Selection observers can be called before the singleton finishes
            // publishing its new aggregate selection. Re-check once the event
            // loop settles so clicks in the 3D view and tree always reach the
            // same global timeline without changing its contents.
            QTimer::singleShot(0, this, [this]() { syncSelectionFromGui(); });
            break;
        default:
            break;
    }
}

void FeatureTimeline::onTimelineItemDoubleClicked(QListWidgetItem* item)
{
    if (!canChangeHistory()) {
        return;
    }
    auto* operation = objectForItem(item);
    if (invokeTimelineEditCommand(operation)) {
        return;
    }
    auto* object = App::DocumentTimeline::timelineEditor(operation);
    const bool redirected = object != nullptr;
    if (!object) {
        object = operation;
    }
    const auto* viewProvider = object && Gui::Application::Instance
        ? Gui::Application::Instance->getViewProvider(object)
        : nullptr;
    if (!viewProvider || !viewProvider->supportsDocumentTimelineEdit()) {
        return;
    }
    auto* document = object ? object->getDocument() : nullptr;
    const TimelineDocumentIdentity clickedDocument = documentIdentity(document);
    const TimelineObjectIdentity clickedObject = objectIdentity(object);
    const std::uint64_t documentGeneration = observedDocumentGeneration;
    activateOwningBody(object);
    document = resolveDocument(clickedDocument);
    object = document && observedDocumentGeneration == documentGeneration
        ? resolveObject(document, clickedObject)
        : nullptr;
    if (object && !isDerivedFrom(object, "PartDesign::Body")) {
        if (redirected) {
            selectOnly(object);
        }
        editObject(object);
    }
}

bool FeatureTimeline::invokeTimelineEditCommand(App::DocumentObject* operation)
{
    if (!canChangeHistory()) {
        return false;
    }
    const auto approved = approvedTimelineEditCommand(operation, true);
    if (!approved.command) {
        return false;
    }

    const TimelineDocumentIdentity targetDocument = documentIdentity(operation->getDocument());
    const TimelineObjectIdentity targetOperation = objectIdentity(operation);
    const std::uint64_t documentGeneration = observedDocumentGeneration;
    selectOnly(operation);

    auto* document = resolveDocument(targetDocument);
    operation = document && activeAppDocument() == document
            && observedDocumentGeneration == documentGeneration
        ? resolveObject(document, targetOperation)
        : nullptr;
    const auto current = approvedTimelineEditCommand(operation, true);
    if (!current.command || current.name != approved.name || current.command != approved.command) {
        return false;
    }
    current.command->invoke(0, Gui::Command::TriggerAction);
    return true;
}

void FeatureTimeline::selectOnly(App::DocumentObject* object)
{
    if (!object || !object->isAttachedToDocument()) {
        return;
    }
    {
        QScopedValueRollback<bool> syncingGuard(syncingSelection, true);
        Gui::Selection().clearSelection();
        Gui::Selection().addSelection(object->getDocument()->getName(), object->getNameInDocument());
    }
    syncSelectionFromGui();
}

void FeatureTimeline::runSelectionCommand(App::DocumentObject* object, const char* commandName)
{
    if (!canChangeHistory() || !object || !commandName || !Gui::Application::Instance) {
        return;
    }
    const std::string documentName = object->getDocument()->getName();
    const TimelineObjectIdentity identity = objectIdentity(object);
    const std::uint64_t documentGeneration = observedDocumentGeneration;
    selectOnly(object);
    auto* document = activeAppDocument();
    if (!document || document->getName() != documentName
        || observedDocumentGeneration != documentGeneration || !resolveObject(document, identity)) {
        return;
    }
    Gui::Application::Instance->commandManager().runCommandByName(commandName);
}

void FeatureTimeline::moveCurrentStateToPosition(int position)
{
    setDocumentPosition(position);
}

bool FeatureTimeline::moveCurrentStateAfterOperation(
    const QString& documentName,
    const QString& documentUid,
    const QString& operationName,
    qlonglong operationId
)
{
    if (!canChangeHistory() || operationId < 0 || operationId > std::numeric_limits<long>::max()) {
        return false;
    }

    auto* document = activeAppDocument();
    const QByteArray documentNameUtf8 = documentName.toUtf8();
    const QByteArray documentUidUtf8 = documentUid.toUtf8();
    const QByteArray operationNameUtf8 = operationName.toUtf8();
    const std::string expectedDocumentName(
        documentNameUtf8.constData(),
        static_cast<std::size_t>(documentNameUtf8.size())
    );
    const std::string expectedDocumentUid(
        documentUidUtf8.constData(),
        static_cast<std::size_t>(documentUidUtf8.size())
    );
    const std::string expectedOperationName(
        operationNameUtf8.constData(),
        static_cast<std::size_t>(operationNameUtf8.size())
    );
    if (!document || document->getName() != expectedDocumentName
        || document->Uid.getValueStr() != expectedDocumentUid) {
        return false;
    }

    auto* controller = App::DocumentTimeline::get(document);
    auto* operation = resolveObject(document, expectedOperationName, static_cast<long>(operationId));
    if (!controller || !operation || operation->getDocument() != document
        || !operation->isAttachedToDocument()) {
        return false;
    }

    const auto operations = controller->Operations.getValues();
    const auto found = std::ranges::find(operations, operation);
    if (found == operations.end()) {
        return false;
    }
    const SemanticTimelineLayout semanticLayout(operations, document);
    const auto operationBlock = semanticLayout.blockFor(operation);
    if (!operationBlock.isValid()) {
        return false;
    }
    const int operationIndex = static_cast<int>(
        std::distance(operations.begin(), found)
    );
    const int requestedPosition = operationBlock.end;
    const TimelineDocumentIdentity documentIdentityBefore = documentIdentity(document);
    const TimelineObjectIdentity operationIdentity = objectIdentity(operation);
    const std::uint64_t documentGeneration = observedDocumentGeneration;

    setDocumentPosition(requestedPosition);

    document = ::resolveDocument(documentIdentityBefore);
    if (!document || activeAppDocument() != document
        || observedDocumentGeneration != documentGeneration) {
        return false;
    }
    controller = App::DocumentTimeline::get(document);
    operation = resolveObject(document, operationIdentity);
    if (!controller || !operation
        || controller->Position.getValue() != requestedPosition
        || controller->Operations.getSize() != static_cast<int>(operations.size())
        || operationIndex < 0
        || controller->Operations[operationIndex] != operation) {
        return false;
    }
    const auto currentOperations = controller->Operations.getValues();
    const SemanticTimelineLayout currentLayout(currentOperations, document);
    const auto currentBlock = currentLayout.blockFor(operation);
    return currentBlock.isValid()
        && currentBlock.begin == operationBlock.begin
        && currentBlock.end == requestedPosition;
}

void FeatureTimeline::navigateCurrentState(int direction)
{
    if (!canChangeHistory()) {
        return;
    }
    auto* document = activeAppDocument();
    auto* controller = App::DocumentTimeline::get(document);
    if (!controller) {
        return;
    }
    const auto operations = controller->Operations.getValues();
    const SemanticTimelineLayout semanticLayout(operations, document);
    if (!semanticLayout.isValid()) {
        return;
    }
    const int currentPosition = static_cast<int>(
        std::clamp(controller->Position.getValue(), 0L, static_cast<long>(operations.size()))
    );
    const auto internalTransformations = internalTransformationChildren(operations);
    ModelTreeBrowserProjection projection(document);
    struct VisibleSemanticBlock
    {
        SemanticTimelineBlock block;
        bool active {false};
    };
    std::vector<VisibleSemanticBlock> blocks;
    for (std::size_t index = 0; index < operations.size(); ++index) {
        if (isVisibleTimelineOperation(operations[index], internalTransformations, projection)) {
            const auto block = semanticLayout.blockFor(operations[index]);
            if (block.isValid()) {
                blocks.push_back(
                    {
                        block,
                        controller->isOperationActive(operations[index]),
                    }
                );
            }
        }
    }
    std::ranges::sort(
        blocks,
        {},
        [](const VisibleSemanticBlock& visible) {
            return visible.block.begin;
        }
    );

    int nextPosition = currentPosition;
    if (direction <= -2) {
        nextPosition = 0;
    }
    else if (direction >= 2) {
        nextPosition = static_cast<int>(operations.size());
    }
    else if (direction < 0) {
        const auto previous = std::find_if(
            blocks.rbegin(),
            blocks.rend(),
            [currentPosition](const VisibleSemanticBlock& visible) {
                return visible.active
                    && visible.block.begin < currentPosition;
            }
        );
        nextPosition =
            previous == blocks.rend()
            ? currentPosition
            : previous->block.begin;
    }
    else if (direction > 0) {
        const auto next = std::ranges::find_if(
            blocks,
            [currentPosition](const VisibleSemanticBlock& visible) {
                return !visible.active
                    && visible.block.end > currentPosition;
            }
        );
        if (next != blocks.end()) {
            nextPosition = next->block.end;
        }
    }
    setDocumentPosition(nextPosition);
}

void FeatureTimeline::setDocumentPosition(int requestedPosition)
{
    auto* document = activeAppDocument();
    auto* controller = App::DocumentTimeline::get(document);
    if (!document || !controller || !canChangeHistory()) {
        return;
    }
    const TimelineDocumentIdentity originalDocument = documentIdentity(document);
    const std::uint64_t documentGeneration = observedDocumentGeneration;
    const auto resolveDocument = [&]() -> App::Document* {
        return ::resolveDocument(originalDocument);
    };
    const auto resolveDocumentContext = [&]() -> App::Document* {
        auto* current = resolveDocument();
        return current && observedDocumentGeneration == documentGeneration ? current : nullptr;
    };
    const auto operations = controller->Operations.getValues();
    const SemanticTimelineLayout semanticLayout(operations, document);
    if (!semanticLayout.isValid()) {
        Base::Console().error("The saved document timeline has invalid operation ownership\n");
        return;
    }
    const auto visibilityAtEnd = controller->VisibilityAtEnd.getValues();
    const auto suppressionAtEnd = controller->SuppressionAtEnd.getValues();
    if (visibilityAtEnd.size() != operations.size() || suppressionAtEnd.size() != operations.size()) {
        Base::Console().error("The saved document timeline has mismatched operation and "
                              "state data\n");
        return;
    }
    const int nextPosition = std::clamp(requestedPosition, 0, static_cast<int>(operations.size()));

    std::unordered_map<App::DocumentObject*, int> operationIndices;
    std::vector<TimelineObjectIdentity> operationIdentities;
    std::vector<std::string> operationMacroCommands;
    operationIdentities.reserve(operations.size());
    operationMacroCommands.reserve(operations.size());
    for (std::size_t index = 0; index < operations.size(); ++index) {
        auto* operation = operations[index];
        if (!operation || operation->getDocument() != document || !operation->isAttachedToDocument()
            || !operationIndices.emplace(operation, static_cast<int>(index)).second) {
            Base::Console().error("The saved document timeline contains a missing or duplicate "
                                  "operation\n");
            return;
        }
        operationIdentities.push_back(objectIdentity(operation));
        operationMacroCommands.push_back(macroObjectCommand(operation));
    }

    const auto internalTransformations = internalTransformationChildren(operations);
    ModelTreeBrowserProjection projection(document);
    const TimelineObjectIdentity controllerIdentity = objectIdentity(controller);
    const std::string controllerCommand = macroObjectCommand(controller);

    struct ResultMember
    {
        TimelineObjectIdentity identity;
        std::string macroCommand;
    };
    struct BodyPlan
    {
        TimelineObjectIdentity body;
        TimelineObjectIdentity baseFeature;
        TimelineObjectIdentity nextTip;
        std::vector<TimelineObjectIdentity> group;
        std::vector<ResultMember> results;
        std::string bodyCommand;
        std::string tipCommand;
    };
    std::vector<BodyPlan> bodyPlans;
    std::unordered_set<App::DocumentObject*> plannedBodies;
    std::unordered_set<App::DocumentObject*> bodyResults;
    for (const auto& entry : projection.entries()) {
        auto* body = entry.role == ModelTreeBrowserProjection::Role::Body ? entry.object : nullptr;
        if (!body || App::DocumentTimeline::hasTimelineResourceRole(body)
            || body->getDocument() != document || !plannedBodies.insert(body).second) {
            continue;
        }
        auto* group = dynamic_cast<App::PropertyLinkList*>(body->getPropertyByName("Group"));
        auto* tip = dynamic_cast<App::PropertyLink*>(body->getPropertyByName("Tip"));
        auto* baseFeature = dynamic_cast<App::PropertyLink*>(body->getPropertyByName("BaseFeature"));
        if (!group || !tip) {
            Base::Console().error("A Body in the document has no native Group/Tip history\n");
            return;
        }

        BodyPlan plan;
        plan.body = objectIdentity(body);
        plan.baseFeature = objectIdentity(baseFeature ? baseFeature->getValue() : nullptr);
        plan.bodyCommand = macroObjectCommand(body);
        App::DocumentObject* nextTip = nullptr;
        const auto bodyOperation = operationIndices.find(body);
        const bool structuralBody = hasExplicitTimelineRole(
            body,
            App::DocumentTimeline::InternalRole
        );
        const bool bodyTracked = bodyOperation != operationIndices.end();
        if (!bodyTracked && !structuralBody) {
            Base::Console().error("A Body is absent from the saved document timeline\n");
            return;
        }
        // A native Body is normally a structural container, not a History
        // step. It remains live while its exact tracked members determine the
        // result Tip at the document-wide marker. Some semantic tools (for
        // example one-step standard fasteners) deliberately publish the Body
        // itself as their operation root; preserve that explicit contract.
        const bool bodyActive =
            structuralBody || (bodyTracked && bodyOperation->second < nextPosition);
        int nextTipOperation = bodyTracked ? bodyOperation->second : -1;
        auto* semanticBodyEditor = !bodyTracked
            ? nullptr
            : const_cast<App::DocumentObject*>(
                  App::DocumentTimeline::timelineEditor(body)
              );
        App::DocumentObject* designPublication = nullptr;
        for (auto* member : group->getValues()) {
            if (!isDerivedFrom(member, "PartDesign::DesignBodyPublication")) {
                continue;
            }
            if (designPublication) {
                Base::Console().error(
                    "A Design Body contains more than one persistent publication\n"
                );
                return;
            }
            designPublication = member;
        }
        if (designPublication) {
            nextTip = designPublication;
        }
        for (auto* member : group->getValues()) {
            plan.group.push_back(objectIdentity(member));
            if (member == designPublication) {
                // A Design Body has one stable rendered publication for its
                // entire lifetime.  Moving the global History marker changes
                // which immutable Body state that publication resolves; it
                // must never replace or clear the Body's Tip.
                nextTip = member;
                plan.results.push_back(
                    {objectIdentity(member), macroObjectCommand(member)}
                );
                bodyResults.insert(member);
                continue;
            }
            const auto operation = operationIndices.find(member);
            if (operation == operationIndices.end()) {
                if (hasExplicitTimelineRole(member, App::DocumentTimeline::InternalRole)
                    || internalTransformations.contains(member)) {
                    continue;
                }
                Base::Console().error(
                    "Body member '%s' is absent from the saved document "
                    "timeline\n",
                    member && member->getNameInDocument() ? member->getNameInDocument() : "<detached>"
                );
                return;
            }
            if (bodyTracked && bodyActive && member == semanticBodyEditor
                && !designPublication
                && isDerivedFrom(member, "Part::Feature")
                && !isDerivedFrom(member, "Part::Part2DObject")
                && !isDerivedFrom(member, "Part::BodyBase")
                && !isDerivedFrom(member, "Part::Datum")
                && !isDerivedFrom(member, "App::DatumElement")) {
                // A single-operation component may publish its native Body as
                // the visible root and keep the editable shape feature as an
                // owned implementation object. The explicit editor link is
                // the unambiguous saved result for that semantic block.
                nextTip = member;
                nextTipOperation = bodyOperation->second;
            }
            if (!designPublication && bodyActive && operation->second < nextPosition) {
                if (operation->second > nextTipOperation
                    && isNavigableTimelineResult(member, internalTransformations, projection)) {
                    nextTip = member;
                    nextTipOperation = operation->second;
                }
            }
            if (canBeBodyTip(member, internalTransformations)) {
                plan.results.push_back({objectIdentity(member), macroObjectCommand(member)});
                bodyResults.insert(member);
            }
        }
        plan.nextTip = objectIdentity(nextTip);
        plan.tipCommand = nextTip ? macroObjectCommand(nextTip) : "None";
        bodyPlans.push_back(std::move(plan));
    }

    // Use the same exact command checkpoint as every ribbon action. This gives
    // Undo-disabled documents a private rollback journal, buffers macro output
    // until validation succeeds, and prevents this workspace control from
    // becoming a second, weaker transaction implementation.
    TaskView::TaskDialog::beginCommandInvocation();
    bool invocationFinished = false;
    const auto finishInvocation = [&](bool success) {
        if (!invocationFinished) {
            invocationFinished = true;
            TaskView::TaskDialog::endCommandInvocation(success);
        }
    };

    const int transaction = document->openTransaction("Move end of history");
    document = resolveDocumentContext();
    if (transaction == App::NullTransaction || !document
        || document->getBookedTransactionID() != transaction
        || !App::GetApplication().transactionIsActive(transaction)) {
        if (transaction != App::NullTransaction
            && App::GetApplication().abortTransaction(transaction)) {
            if (auto* currentDocument = resolveDocument()) {
                TaskView::TaskDialog::recordCommandTransactionCompletion(currentDocument, transaction);
            }
        }
        Base::Console().warning("Moving the feature-history marker was skipped because "
                                "its exact undo transaction could not be opened\n");
        finishInvocation(false);
        scheduleRefresh();
        return;
    }

    // Changing Tip publishes several synchronous property and view-provider
    // signals. A callback reached through one of those signals must not be
    // able to replace or finish this operation's transaction midway through
    // validation. Keep the exact transaction locked until the model state has
    // been recomputed and checked, then release it immediately before the
    // exact commit or abort.
    TimelineTransactionLock transactionLock(document);
    document = resolveDocumentContext();
    controller = document
        ? dynamic_cast<App::DocumentTimeline*>(resolveObject(document, controllerIdentity))
        : nullptr;
    if (!transactionLock.isActive() || !document || !controller
        || document->getBookedTransactionID() != transaction
        || !App::GetApplication().transactionIsActive(transaction)) {
        transactionLock.activate(false);
        if (App::GetApplication().abortTransaction(transaction)) {
            if (auto* currentDocument = resolveDocument()) {
                TaskView::TaskDialog::recordCommandTransactionCompletion(currentDocument, transaction);
            }
        }
        finishInvocation(false);
        scheduleRefresh();
        return;
    }

    std::vector<std::pair<std::string, bool>> acceptedVisibility;
    acceptedVisibility.reserve(operations.size());
    std::vector<std::pair<std::string, bool>> acceptedSuppression;
    acceptedSuppression.reserve(operations.size());
    TimelineApplyingGuard applyingGuard(controller);
    const auto abortExactTransaction = [&]() {
        if (transactionLock.isActive()) {
            transactionLock.activate(false);
        }
        if (App::GetApplication().abortTransaction(transaction)) {
            if (auto* currentDocument = resolveDocument()) {
                TaskView::TaskDialog::recordCommandTransactionCompletion(currentDocument, transaction);
            }
        }
    };
    try {
        controller->Position.setValue(nextPosition);
        document = resolveDocumentContext();
        controller = document
            ? dynamic_cast<App::DocumentTimeline*>(resolveObject(document, controllerIdentity))
            : nullptr;
        if (!document || !controller || controller->Position.getValue() != nextPosition) {
            throw Base::RuntimeError("The document timeline changed while moving its marker");
        }

        std::vector<TimelineObjectIdentity> replacementInputsToShow;
        std::vector<TimelineObjectIdentity> replacementInputsToHide;
        std::unordered_set<long> replacementInputIdsToShow;
        std::unordered_set<long> replacementInputIdsToHide;
        const auto addReplacementInput = [](const App::DocumentObject* input,
                                            std::unordered_set<long>& inputIds,
                                            std::vector<TimelineObjectIdentity>& inputs) {
            if (input && input->getID() >= 0 && inputIds.insert(input->getID()).second) {
                inputs.push_back(objectIdentity(input));
            }
        };
        for (const auto& operationIdentity : operationIdentities) {
            auto* semanticOperation = resolveObject(document, operationIdentity);
            const auto replacement = App::DocumentTimeline::replacementInputContract(semanticOperation
            );
            if (!replacement.valid || replacement.inputs.empty()) {
                // Invalid explicit metadata fails closed: it can never reveal
                // unrelated geometry or prevent the rest of the document from
                // navigating normally.
                continue;
            }
            const bool replacementIsActive = controller->isOperationActive(semanticOperation);
            for (auto* input : replacement.inputs) {
                if (!controller->isOperationActive(input)) {
                    continue;
                }
                auto& inputIds = replacementIsActive ? replacementInputIdsToHide
                                                     : replacementInputIdsToShow;
                auto& inputs = replacementIsActive ? replacementInputsToHide
                                                   : replacementInputsToShow;
                addReplacementInput(input, inputIds, inputs);
                for (auto* owner = App::DocumentTimeline::timelineOwner(input); owner;
                     owner = App::DocumentTimeline::timelineOwner(owner)) {
                    addReplacementInput(owner, inputIds, inputs);
                }
            }
        }

        for (std::size_t index = 0; index < operations.size(); ++index) {
            document = resolveDocumentContext();
            controller = document
                ? dynamic_cast<App::DocumentTimeline*>(resolveObject(document, controllerIdentity))
                : nullptr;
            auto* operation = resolveObject(document, operationIdentities[index]);
            if (!controller || !operation) {
                throw Base::RuntimeError(
                    "The document timeline changed while applying its history state"
                );
            }
            auto* suppressible = hasRecomputableTimelineSuppression(operation)
                ? operation->getExtensionByType<App::SuppressibleExtension>(true)
                : nullptr;
            if (suppressible) {
                const bool shouldSuppress = !controller->isOperationActive(operation)
                    || suppressionAtEnd[index];
                if (suppressible->Suppressed.getValue() != shouldSuppress) {
                    suppressible->Suppressed.setValue(shouldSuppress);
                }
                document = resolveDocumentContext();
                controller = document
                    ? dynamic_cast<App::DocumentTimeline*>(resolveObject(document, controllerIdentity))
                    : nullptr;
                operation = resolveObject(document, operationIdentities[index]);
                suppressible = operation
                    ? operation->getExtensionByType<App::SuppressibleExtension>(true)
                    : nullptr;
                if (!controller || !operation || !suppressible
                    || suppressible->Suppressed.getValue() != shouldSuppress) {
                    throw Base::RuntimeError(
                        "A document operation could not accept its history state"
                    );
                }
                acceptedSuppression.emplace_back(operationMacroCommands[index], shouldSuppress);
            }
            // Body result visibility is derived from the structural Body and
            // its exact Tip below. Suppression is still computational state:
            // future Part Design features must not recompute behind a rolled
            // back marker, and advancing to the end must restore each feature's
            // accepted suppression baseline.
            if (bodyResults.contains(operation)) {
                continue;
            }
            const bool afterMarker = !controller->isOperationActive(operation);
            const auto replacement = App::DocumentTimeline::replacementInputContract(operation);
            const bool showBypassResult = afterMarker
                && !App::DocumentTimeline::hasTimelineResourceRole(operation) && suppressible
                && suppressible->isTimelineResultVisibleWhenSuppressed()
                && replacement.inputs.empty()
                && hasActiveTimelineBypassSource(operation, controller);
            const bool hideReplacedInput = replacementInputIdsToHide.contains(operation->getID());
            const bool showReplacedInput = replacementInputIdsToShow.contains(operation->getID());
            const bool shouldShow = !hideReplacedInput
                && (showReplacedInput
                    || (controller->isOperationVisibleAtEnd(operation)
                        && (!afterMarker || showBypassResult)));
            if (operation->Visibility.getValue() != shouldShow) {
                operation->Visibility.setValue(shouldShow);
            }
            document = resolveDocumentContext();
            operation = resolveObject(document, operationIdentities[index]);
            if (!operation || operation->Visibility.getValue() != shouldShow) {
                throw Base::RuntimeError(
                    "A document operation could not accept its history visibility"
                );
            }
            acceptedVisibility.emplace_back(operationMacroCommands[index], shouldShow);
        }

        for (const auto& identity : replacementInputsToShow) {
            document = resolveDocumentContext();
            auto* input = resolveObject(document, identity);
            if (!input) {
                throw Base::RuntimeError("A replaced input changed while moving document history");
            }
            if (operationIndices.contains(input)) {
                continue;
            }
            if (!input->Visibility.getValue()) {
                input->Visibility.setValue(true);
            }
            document = resolveDocumentContext();
            input = resolveObject(document, identity);
            if (!input || !input->Visibility.getValue()) {
                throw Base::RuntimeError("A replaced input could not accept its history visibility");
            }
            acceptedVisibility.emplace_back(macroObjectCommand(input), true);
        }

        for (const auto& identity : replacementInputsToHide) {
            document = resolveDocumentContext();
            auto* input = resolveObject(document, identity);
            if (!input) {
                throw Base::RuntimeError("A replaced input changed while moving document history");
            }
            if (operationIndices.contains(input)) {
                continue;
            }
            if (input->Visibility.getValue()) {
                input->Visibility.setValue(false);
            }
            document = resolveDocumentContext();
            input = resolveObject(document, identity);
            if (!input || input->Visibility.getValue()) {
                throw Base::RuntimeError("A replaced input could not accept its history visibility");
            }
            acceptedVisibility.emplace_back(macroObjectCommand(input), false);
        }

        for (const auto& plan : bodyPlans) {
            document = resolveDocumentContext();
            auto* currentBody = resolveObject(document, plan.body);
            auto* currentTip = resolveObject(document, plan.nextTip);
            auto* tipProperty = currentBody
                ? dynamic_cast<App::PropertyLink*>(currentBody->getPropertyByName("Tip"))
                : nullptr;
            if (!currentBody || !tipProperty || (plan.nextTip.id >= 0 && !currentTip)) {
                throw Base::RuntimeError("A Body changed while moving the document history");
            }
            tipProperty->setValue(currentTip);
            document = resolveDocumentContext();
            currentBody = resolveObject(document, plan.body);
            currentTip = resolveObject(document, plan.nextTip);
            tipProperty = currentBody
                ? dynamic_cast<App::PropertyLink*>(currentBody->getPropertyByName("Tip"))
                : nullptr;
            if (!currentBody || !tipProperty || tipProperty->getValue() != currentTip
                || (plan.nextTip.id >= 0 && !currentTip)) {
                throw Base::RuntimeError("A Body changed while setting its current result");
            }
            for (const auto& result : plan.results) {
                document = resolveDocumentContext();
                currentBody = resolveObject(document, plan.body);
                auto* member = resolveObject(document, result.identity);
                if (!currentBody || !member) {
                    throw Base::RuntimeError("A Body result was removed while changing history");
                }
                const bool shouldShow = currentBody->Visibility.getValue() && currentTip == member;
                if (member->Visibility.getValue() != shouldShow) {
                    member->Visibility.setValue(shouldShow);
                }
                document = resolveDocumentContext();
                member = resolveObject(document, result.identity);
                if (!member || member->Visibility.getValue() != shouldShow) {
                    throw Base::RuntimeError(
                        "A Body result could not accept its history visibility"
                    );
                }
                acceptedVisibility.emplace_back(result.macroCommand, shouldShow);
            }
        }

        document = resolveDocumentContext();
        if (!document) {
            throw Base::RuntimeError("The active document changed while moving history");
        }
        document->recompute();
        document = resolveDocumentContext();
        auto* currentController = document
            ? dynamic_cast<App::DocumentTimeline*>(resolveObject(document, controllerIdentity))
            : nullptr;
        if (!currentController || currentController->Position.getValue() != nextPosition
            || currentController->Operations.getSize()
                != static_cast<int>(operationIdentities.size())) {
            throw Base::RuntimeError("The document timeline changed during history validation");
        }
        for (const auto& identity : replacementInputsToShow) {
            const auto* input = resolveObject(document, identity);
            if (!input
                || (replacementInputIdsToHide.contains(input->getID())
                        ? input->Visibility.getValue()
                        : !input->Visibility.getValue())) {
                throw Base::RuntimeError("A replaced input lost its requested history visibility");
            }
        }
        for (const auto& identity : replacementInputsToHide) {
            const auto* input = resolveObject(document, identity);
            if (!input || input->Visibility.getValue()) {
                throw Base::RuntimeError("A replaced input lost its requested history visibility");
            }
        }
        for (std::size_t index = 0; index < operationIdentities.size(); ++index) {
            auto* currentOperation = resolveObject(document, operationIdentities[index]);
            if (!currentOperation || currentController->Operations[index] != currentOperation) {
                throw Base::RuntimeError("The ordered document history changed during validation");
            }
            const auto* suppressible = hasRecomputableTimelineSuppression(currentOperation)
                ? currentOperation->getExtensionByType<App::SuppressibleExtension>(true)
                : nullptr;
            const bool computationallyActive = !suppressible || !suppressible->Suppressed.getValue();
            if (currentController->isOperationActive(currentOperation) && computationallyActive
                && currentOperation->isError()) {
                throw Base::RuntimeError("The active document history contains an invalid operation");
            }
        }
        for (const auto& plan : bodyPlans) {
            auto* currentBody = resolveObject(document, plan.body);
            auto* currentBaseFeature = resolveObject(document, plan.baseFeature);
            auto* currentTip = resolveObject(document, plan.nextTip);
            auto* tipProperty = currentBody
                ? dynamic_cast<App::PropertyLink*>(currentBody->getPropertyByName("Tip"))
                : nullptr;
            auto* baseFeatureProperty = currentBody
                ? dynamic_cast<App::PropertyLink*>(currentBody->getPropertyByName("BaseFeature"))
                : nullptr;
            auto* groupProperty = currentBody
                ? dynamic_cast<App::PropertyLinkList*>(currentBody->getPropertyByName("Group"))
                : nullptr;
            if (!currentBody || !tipProperty || !groupProperty
                || (plan.baseFeature.id >= 0 && !baseFeatureProperty)
                || (baseFeatureProperty && baseFeatureProperty->getValue() != currentBaseFeature)
                || tipProperty->getValue() != currentTip || currentBody->isError()
                || (currentTip && currentTip->isError())
                || groupProperty->getSize() != static_cast<int>(plan.group.size())) {
                throw Base::RuntimeError("A Body could not accept the requested document state");
            }
            for (std::size_t index = 0; index < plan.group.size(); ++index) {
                if ((*groupProperty)[index] != resolveObject(document, plan.group[index])) {
                    throw Base::RuntimeError("A Body history changed while moving the document "
                                             "marker");
                }
            }
        }

        transactionLock.activate(false);
        document = resolveDocumentContext();
        if (!document || document->getBookedTransactionID() != transaction
            || !App::GetApplication().transactionIsActive(transaction)) {
            throw Base::RuntimeError(
                "The feature-history transaction changed before it could be committed"
            );
        }
        if (!App::GetApplication().commitTransaction(transaction)) {
            throw Base::RuntimeError("The feature-history transaction could not be committed");
        }
        if (auto* currentDocument = resolveDocument()) {
            TaskView::TaskDialog::recordCommandTransactionCompletion(currentDocument, transaction);
        }
    }
    catch (Base::Exception& error) {
        abortExactTransaction();
        finishInvocation(false);
        error.reportException();
        scheduleRefresh();
        return;
    }
    catch (const std::exception& error) {
        abortExactTransaction();
        finishInvocation(false);
        Base::Console().error("Moving the feature-history marker failed: %s\n", error.what());
        scheduleRefresh();
        return;
    }
    catch (...) {
        abortExactTransaction();
        finishInvocation(false);
        Base::Console().error("Moving the feature-history marker failed with an unknown exception\n");
        scheduleRefresh();
        return;
    }

    // Recording is intentionally after validation and commit. Macro I/O is
    // supplementary: it must never decide whether the native document change
    // succeeds or roll an accepted model state back.
    try {
        auto* macroDocument = resolveDocument();
        if (auto* macroManager = macroDocument && Gui::Application::Instance
                ? Gui::Application::Instance->macroManager()
                : nullptr) {
            std::ostringstream line;
            if (!controllerCommand.empty()) {
                line << controllerCommand << ".Position = " << nextPosition;
                macroManager->addLine(MacroManager::App, line.str().c_str());
            }
            for (const auto& plan : bodyPlans) {
                if (plan.bodyCommand.empty() || plan.tipCommand.empty()) {
                    continue;
                }
                line.str(std::string());
                line.clear();
                line << plan.bodyCommand << ".Tip = " << plan.tipCommand;
                macroManager->addLine(MacroManager::App, line.str().c_str());
            }
            for (const auto& [memberCommand, visible] : acceptedVisibility) {
                if (memberCommand.empty()) {
                    continue;
                }
                line.str(std::string());
                line.clear();
                line << memberCommand << ".Visibility = " << (visible ? "True" : "False");
                macroManager->addLine(MacroManager::App, line.str().c_str());
            }
            for (const auto& [operationCommand, suppressed] : acceptedSuppression) {
                if (operationCommand.empty()) {
                    continue;
                }
                line.str(std::string());
                line.clear();
                line << operationCommand << ".Suppressed = " << (suppressed ? "True" : "False");
                macroManager->addLine(MacroManager::App, line.str().c_str());
            }
            line.str(std::string());
            line.clear();
            line << "App.getDocument('" << macroDocument->getName() << "').recompute()";
            macroManager->addLine(MacroManager::App, line.str().c_str());
        }
    }
    catch (const std::exception& error) {
        Base::Console().warning(
            "Recording the accepted feature-history change failed: %s\n",
            error.what()
        );
    }
    catch (...) {
        Base::Console().warning("Recording the accepted feature-history change failed\n");
    }
    finishInvocation(true);
    scheduleRefresh();
}

void FeatureTimeline::onTimelineContextMenu(const QPoint& position)
{
    if (!canChangeHistory()) {
        return;
    }
    auto* item = timeline->itemAt(position);
    if (!itemBelongsToObservedDocument(item)) {
        return;
    }

    const bool marker = item->data(IsMarkerRole).toBool();
    auto* object = objectForItem(item);
    if (!marker && (!object || !object->isAttachedToDocument())) {
        return;
    }
    const std::string documentName = activeAppDocument() ? activeAppDocument()->getName() : "";
    const std::uint64_t documentGeneration = observedDocumentGeneration;
    const std::string objectName = object && object->getNameInDocument()
        ? object->getNameInDocument()
        : "";
    const long objectId = object ? object->getID() : -1;

    if (object) {
        selectOnly(object);
        auto* currentDocument = activeAppDocument();
        if (!currentDocument || currentDocument->getName() != documentName
            || observedDocumentGeneration != documentGeneration) {
            return;
        }
        object = resolveObject(currentDocument, objectName, objectId);
        if (!object) {
            return;
        }
    }

    QMenu menu(this);
    if (marker) {
        auto* endAction = menu.addAction(tr("Move current model state to end of history"));
        connect(endAction, &QAction::triggered, this, [this, documentName, documentGeneration]() {
            auto* document = activeAppDocument();
            if (!document || document->getName() != documentName
                || observedDocumentGeneration != documentGeneration) {
                return;
            }
            const auto* controller = App::DocumentTimeline::get(document);
            if (!controller) {
                return;
            }
            moveCurrentStateToPosition(controller->Operations.getSize());
        });
        auto* startAction = menu.addAction(tr("Move current model state to start of history"));
        connect(startAction, &QAction::triggered, this, [this, documentName, documentGeneration]() {
            auto* document = activeAppDocument();
            if (!document || document->getName() != documentName
                || observedDocumentGeneration != documentGeneration) {
                return;
            }
            moveCurrentStateToPosition(0);
        });
    }
    else {
        auto* currentAction = menu.addAction(tr("Set current model state here"));
        currentAction->setObjectName(QStringLiteral("VibeCADTimelineSetCurrent"));
        const int operationIndex = item->data(OperationIndexRole).toInt();
        connect(
            currentAction,
            &QAction::triggered,
            this,
            [this, documentName, documentGeneration, objectName, objectId, operationIndex]() {
                auto* document = activeAppDocument();
                if (!document || document->getName() != documentName
                    || observedDocumentGeneration != documentGeneration) {
                    return;
                }
                const auto* controller = App::DocumentTimeline::get(document);
                if (!controller || operationIndex < 0
                    || operationIndex >= controller->Operations.getSize()
                    || controller->Operations[operationIndex]
                        != resolveObject(document, objectName, objectId)) {
                    return;
                }
                const auto operations = controller->Operations.getValues();
                const SemanticTimelineLayout layout(operations, document);
                const auto block = layout.blockFor(controller->Operations[operationIndex]);
                if (block.isValid()) {
                    moveCurrentStateToPosition(block.end);
                }
            }
        );

        menu.addSeparator();
        auto* editor = App::DocumentTimeline::timelineEditor(object);
        auto* editTarget = editor ? editor : object;
        const auto operationCommand = approvedTimelineEditCommand(object, true);
        const auto* viewProvider = Gui::Application::Instance
            ? dynamic_cast<Gui::ViewProviderDocumentObject*>(
                  Gui::Application::Instance->getViewProvider(editTarget)
              )
            : nullptr;
        if (isDerivedFrom(object, "PartDesign::Body")) {
            auto* activateAction = menu.addAction(tr("Activate Body"));
            activateAction->setObjectName(QStringLiteral("VibeCADTimelineActivateBody"));
            connect(
                activateAction,
                &QAction::triggered,
                this,
                [this, documentName, documentGeneration, objectName, objectId]() {
                    auto* document = activeAppDocument();
                    if (!document || document->getName() != documentName
                        || observedDocumentGeneration != documentGeneration) {
                        return;
                    }
                    activateOwningBody(resolveObject(document, objectName, objectId));
                }
            );
        }
        else if (operationCommand.command
                 || (viewProvider && viewProvider->supportsDocumentTimelineEdit())) {
            auto* editAction = menu.addAction(editor ? tr("Edit Parameters") : tr("Edit"));
            editAction->setObjectName(QStringLiteral("VibeCADTimelineEdit"));
            connect(
                editAction,
                &QAction::triggered,
                this,
                [this, documentName, documentGeneration, objectName, objectId]() {
                    auto* document = activeAppDocument();
                    if (!document || document->getName() != documentName
                        || observedDocumentGeneration != documentGeneration) {
                        return;
                    }
                    auto* operation = resolveObject(document, objectName, objectId);
                    if (invokeTimelineEditCommand(operation)) {
                        return;
                    }
                    const TimelineDocumentIdentity targetDocument = documentIdentity(document);
                    const TimelineObjectIdentity targetObject {
                        objectName,
                        objectId,
                    };
                    auto* object = resolveObject(document, targetObject);
                    const auto* editor = App::DocumentTimeline::timelineEditor(object);
                    const bool redirected = editor != nullptr;
                    const TimelineObjectIdentity editTarget = objectIdentity(
                        redirected ? editor : object
                    );
                    object = resolveObject(document, editTarget);
                    if (redirected) {
                        selectOnly(object);
                    }
                    activateOwningBody(object);
                    document = resolveDocument(targetDocument);
                    object = document && observedDocumentGeneration == documentGeneration
                        ? resolveObject(document, editTarget)
                        : nullptr;
                    editObject(object);
                }
            );
        }

        if (auto* suppressible = object->getExtensionByType<App::SuppressibleExtension>(true);
            suppressible && !suppressible->Suppressed.testStatus(App::Property::Hidden)
            && Gui::Application::Instance->commandManager().getCommandByName("Std_ToggleSuppress")
            && Gui::Application::Instance->commandManager()
                   .getCommandByName("Std_ToggleSuppress")
                   ->isActive()) {
            auto* suppressAction = menu.addAction(
                suppressible->Suppressed.getValue() ? tr("Unsuppress") : tr("Suppress")
            );
            suppressAction->setObjectName(QStringLiteral("VibeCADTimelineSuppress"));
            connect(
                suppressAction,
                &QAction::triggered,
                this,
                [this, documentName, documentGeneration, objectName, objectId]() {
                    auto* document = activeAppDocument();
                    if (!document || document->getName() != documentName
                        || observedDocumentGeneration != documentGeneration) {
                        return;
                    }
                    runSelectionCommand(
                        resolveObject(document, objectName, objectId),
                        "Std_ToggleSuppress"
                    );
                }
            );
        }

        if (Gui::Application::Instance->commandManager().getCommandByName("Std_Delete")
            && Gui::Application::Instance->commandManager().getCommandByName("Std_Delete")->isActive()) {
            auto* deleteAction = menu.addAction(tr("Delete"));
            deleteAction->setObjectName(QStringLiteral("VibeCADTimelineDelete"));
            connect(
                deleteAction,
                &QAction::triggered,
                this,
                [this, documentName, documentGeneration, objectName, objectId]() {
                    auto* document = activeAppDocument();
                    if (!document || document->getName() != documentName
                        || observedDocumentGeneration != documentGeneration) {
                        return;
                    }
                    runSelectionCommand(resolveObject(document, objectName, objectId), "Std_Delete");
                }
            );
        }
    }

    menu.exec(timeline->viewport()->mapToGlobal(position));
}

void FeatureTimeline::editObject(App::DocumentObject* object)
{
    if (!canChangeHistory() || !object || !object->isAttachedToDocument()
        || !Gui::Application::Instance) {
        return;
    }

    auto* viewProvider = dynamic_cast<Gui::ViewProviderDocumentObject*>(
        Gui::Application::Instance->getViewProvider(object)
    );
    auto* guiDocument = Gui::Application::Instance->getDocument(object->getDocument());
    if (!viewProvider || !viewProvider->supportsDocumentTimelineEdit() || !guiDocument) {
        return;
    }

    auto* appDocument = object->getDocument();
    const TimelineDocumentIdentity editDocumentIdentity = documentIdentity(appDocument);
    const std::uint64_t documentGeneration = observedDocumentGeneration;
    const TimelineObjectIdentity editObjectIdentity = objectIdentity(object);
    const std::string editCommand = macroObjectCommand(object);
    const char* transactionLabel = viewProvider->getTransactionText();
    const std::string transactionText = transactionLabel ? transactionLabel : "";
    TaskView::TaskDialog::beginCommandInvocation();
    bool invocationFinished = false;
    const auto finishInvocation = [&](bool success) {
        if (!invocationFinished) {
            invocationFinished = true;
            TaskView::TaskDialog::endCommandInvocation(success);
        }
    };

    int transaction = App::NullTransaction;
    bool transactionAdopted = false;
    const auto abortOwnedTransaction = [&]() {
        if (transaction == App::NullTransaction
            || !App::GetApplication().abortTransaction(transaction)) {
            return;
        }
        if (auto* currentDocument = resolveDocument(editDocumentIdentity)) {
            TaskView::TaskDialog::recordCommandTransactionCompletion(currentDocument, transaction);
        }
    };
    const auto resetOpenedEditor = [&]() {
        auto* currentDocument = resolveDocument(editDocumentIdentity);
        auto* currentGuiDocument = currentDocument && Gui::Application::Instance
            ? Gui::Application::Instance->getDocument(currentDocument)
            : nullptr;
        if (!currentGuiDocument || !currentGuiDocument->getEditViewProvider()) {
            return true;
        }
        try {
            currentGuiDocument->resetEdit();
            return true;
        }
        catch (Base::Exception& error) {
            error.reportException();
        }
        catch (const std::exception& error) {
            Base::Console().error("Closing the rejected timeline editor failed: %s\n", error.what());
        }
        catch (...) {
            Base::Console().error(
                "Closing the rejected timeline editor failed with an unknown exception\n"
            );
        }
        return false;
    };
    const auto rejectEdit = [&]() {
        const bool editorClosed = resetOpenedEditor();
        if (!transactionAdopted && editorClosed) {
            abortOwnedTransaction();
        }
        finishInvocation(false);
    };

    try {
        guiDocument->setActiveView(viewProvider);
        auto* currentAppDocument = resolveDocument(editDocumentIdentity);
        auto* currentObject = currentAppDocument && activeAppDocument() == currentAppDocument
                && observedDocumentGeneration == documentGeneration
            ? resolveObject(currentAppDocument, editObjectIdentity)
            : nullptr;
        auto* currentViewProvider = currentObject
            ? dynamic_cast<Gui::ViewProviderDocumentObject*>(
                  Gui::Application::Instance->getViewProvider(currentObject)
              )
            : nullptr;
        if (!currentObject || currentViewProvider != viewProvider) {
            rejectEdit();
            return;
        }
        auto* macroManager = Gui::Application::Instance->macroManager();
        const auto submittedMacroLines = macroManager ? macroManager->getSubmittedCommandLines() : 0;

        if (!transactionText.empty()) {
            transaction = currentAppDocument->openTransaction(transactionText);
            currentAppDocument = resolveDocument(editDocumentIdentity);
            currentObject = currentAppDocument && activeAppDocument() == currentAppDocument
                    && observedDocumentGeneration == documentGeneration
                ? resolveObject(currentAppDocument, editObjectIdentity)
                : nullptr;
            currentViewProvider = currentObject
                ? dynamic_cast<Gui::ViewProviderDocumentObject*>(
                      Gui::Application::Instance->getViewProvider(currentObject)
                  )
                : nullptr;
            if (!currentObject || currentViewProvider != viewProvider
                || (transaction == App::NullTransaction && currentAppDocument
                    && currentAppDocument->getUndoMode() != 0)
                || (transaction != App::NullTransaction
                    && (!App::GetApplication().transactionIsActive(transaction)
                        || currentAppDocument->getBookedTransactionID() != transaction))) {
                rejectEdit();
                return;
            }
        }

        const bool legacyHandled = currentViewProvider->doubleClicked();
        currentAppDocument = resolveDocument(editDocumentIdentity);
        auto* currentGuiDocument = currentAppDocument
            ? Gui::Application::Instance->getDocument(currentAppDocument)
            : nullptr;
        currentObject = currentAppDocument ? resolveObject(currentAppDocument, editObjectIdentity)
                                           : nullptr;
        auto* liveViewProvider = currentObject
            ? dynamic_cast<Gui::ViewProviderDocumentObject*>(
                  Gui::Application::Instance->getViewProvider(currentObject)
              )
            : nullptr;
        auto* editingViewProvider = currentGuiDocument ? currentGuiDocument->getEditViewProvider()
                                                       : nullptr;
        // Python and native ViewProvider wrappers return false when launching
        // the editor failed, including when a callback entered edit and then
        // threw. Require both that success result and the exact live
        // document/object/editor identities. Never accept a redirected editor
        // or one left behind after the user changed document context.
        const bool editing = legacyHandled && currentGuiDocument && currentObject
            && liveViewProvider == currentViewProvider && editingViewProvider == currentViewProvider
            && activeAppDocument() == currentAppDocument
            && observedDocumentGeneration == documentGeneration
            && Gui::Application::Instance->isInEdit(currentGuiDocument);
        if (!editing) {
            // No editor existed before this invocation. Tear down any editor
            // opened inside doubleClicked() before rolling back objects it may
            // still reference.
            rejectEdit();
            return;
        }
        if (transaction != App::NullTransaction) {
            if (!currentGuiDocument->adoptOwnedEditTransaction(transaction)) {
                // Rollback must never replay object deletion into a live
                // ViewProvider. Tear down the editor first; because adoption
                // failed, resetEdit() cannot close this exact transaction.
                rejectEdit();
                return;
            }
            transactionAdopted = true;
        }
        try {
            if (macroManager && !editCommand.empty()
                && submittedMacroLines == macroManager->getSubmittedCommandLines()) {
                std::ostringstream command;
                command << editCommand << ".ViewObject.doubleClicked()";
                macroManager->addLine(MacroManager::Gui, command.str().c_str());
            }
        }
        catch (const std::exception& error) {
            Base::Console().warning(
                "Recording the accepted feature-timeline edit failed: %s\n",
                error.what()
            );
        }
        catch (...) {
            Base::Console().warning("Recording the accepted feature-timeline edit failed\n");
        }
        finishInvocation(true);
    }
    catch (Base::Exception& error) {
        // This control may close only the transaction it opened. A callback
        // can synchronously open a successor transaction; comparing that
        // transaction with the pre-command ID does not make it ours.
        rejectEdit();
        error.reportException();
    }
    catch (const std::exception& error) {
        rejectEdit();
        Base::Console().error("Feature timeline edit failed: %s\n", error.what());
    }
    catch (...) {
        rejectEdit();
        Base::Console().error("Feature timeline edit failed with an unknown exception\n");
    }
}

void FeatureTimeline::slotCreatedObject(const ViewProviderDocumentObject&)
{
    scheduleRefresh();
}

void FeatureTimeline::slotDeletedObject(const ViewProviderDocumentObject&)
{
    scheduleRefresh();
}

void FeatureTimeline::slotChangedObject(const ViewProviderDocumentObject&, const App::Property&)
{
    scheduleRefresh();
}

void FeatureTimeline::slotRelabelObject(const ViewProviderDocumentObject&)
{
    scheduleRefresh();
}

void FeatureTimeline::slotEnterEditObject(const ViewProviderDocumentObject&)
{
    scheduleRefresh();
}

void FeatureTimeline::slotResetEditObject(const ViewProviderDocumentObject&)
{
    scheduleRefresh();
}

void FeatureTimeline::slotUndoDocument(const Gui::Document&)
{
    scheduleRefresh();
}

void FeatureTimeline::slotRedoDocument(const Gui::Document&)
{
    scheduleRefresh();
}

void FeatureTimeline::slotDeleteDocument(const Gui::Document& document)
{
    if (document.getDocument() == observedAppDocument) {
        setObservedDocument(nullptr);
    }
}

#include "moc_FeatureTimeline.cpp"
