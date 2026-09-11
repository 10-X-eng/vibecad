/***************************************************************************
 *   Copyright (c) 2004 Jürgen Riegel <juergen.riegel@web.de>              *
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

#include <tuple>
#include <chrono>
#include <deque>
#include <utility>
#include <memory>
#include <list>
#include <string>
#include <sstream>
#include <map>
#include <set>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <cctype>
#include <mutex>
#include <QApplication>
#include <QCheckBox>
#include <QFileInfo>
#include <QMessageBox>
#include <QObject>
#include <QOpenGLWidget>
#include <QPointer>
#include <QTextStream>
#include <QTimer>
#include <QStatusBar>
#include <Inventor/actions/SoSearchAction.h>
#include <Inventor/nodes/SoSeparator.h>

#include <App/AutoTransaction.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentObjectGroup.h>
#include <App/Transactions.h>
#include <App/ElementNamingUtils.h>
#include <App/HostWorkflow.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Matrix.h>
#include <Base/Reader.h>
#include <Base/Sequencer.h>
#include <Base/Writer.h>
#include <Base/Tools.h>

#include "Document.h"
#include "FrameBudget.h"
#include "ModelTreeBrowser.h"
#include "DocumentPy.h"
#include "Application.h"
#include "Command.h"
#include "Control.h"
#include "FileDialog.h"
#include "MainWindow.h"
#include "Macro.h"
#include "MDIView.h"
#include "NotificationArea.h"
#include "Selection.h"
#include "Thumbnail.h"
#include "TaskView/TaskDialog.h"
#include "Tree.h"
#include "View3DInventor.h"
#include "View3DInventorViewer.h"
#include "ViewProviderDocumentObject.h"
#include "ViewProviderDocumentObjectGroup.h"
#include "WaitCursor.h"


FC_LOG_LEVEL_INIT("Gui", true, true)

using namespace Gui;
namespace sp = std::placeholders;

namespace Gui
{

namespace
{
void restoreOnGui(std::function<void()> step)
{
    if (!dispatchToGuiFrameAndWait(std::move(step))) {
        throw Base::RuntimeError("Unable to dispatch document presentation restore");
    }
}

void captureOnGui(Base::Writer& writer, const std::function<void()>& capture)
{
    writer.captureOnOwner(
        [](std::function<void()> step) {
            if (!dispatchToGuiFrameAndWait(std::move(step))) {
                throw Base::RuntimeError("Unable to dispatch document presentation save");
            }
        },
        capture
    );
}

std::shared_ptr<std::istream> bufferRestoreInput(std::istream& source)
{
    std::ostringstream buffer;
    buffer << source.rdbuf();
    return std::make_shared<std::istringstream>(std::move(buffer).str());
}

// The archive belongs to the document worker. Its GUI XML and binary payloads
// are read there before the owner is asked to adopt persistent presentation
// state. Retaining the XML input also keeps Xerces' stream alive while its
// registered embedded files are being restored.
struct GuiRestoreInput
{
    explicit GuiRestoreInput(std::istream& source)
        : input(bufferRestoreInput(source))
    {}

    std::shared_ptr<std::istream> input;
};

class GuiRestoreReader final: private GuiRestoreInput, public Base::XMLReader
{
public:
    explicit GuiRestoreReader(Base::Reader& reader)
        : GuiRestoreInput(reader)
        , Base::XMLReader("GuiDocument.xml", *input)
    {
        FileVersion = reader.getFileVersion();
    }

protected:
    void restoreFile(Base::Persistence& object, Base::Reader& reader) const override
    {
        auto input = bufferRestoreInput(reader);
        Base::Reader buffered(*input, reader.getFileName(), reader.getFileVersion());
        restoreOnGui([&] { object.RestoreDocFile(buffered); });
        if (auto child = buffered.getLocalReader()) {
            // Preserve stream lifetime for any nested parser registered by
            // the persistence callback, not just the built-in GUI properties.
            struct NestedInput
            {
                std::shared_ptr<std::istream> input;
                std::shared_ptr<Base::XMLReader> parser;
            };
            auto lifetime = std::make_shared<NestedInput>(NestedInput {input, child});
            reader.initLocalReader(std::shared_ptr<Base::XMLReader>(lifetime, child.get()));
        }
    }
};

struct DetachedExactAbort
{
    int transactionId {App::NullTransaction};
    bool retrying {false};
    std::vector<fastsignals::connection> participantChanges;
    fastsignals::connection exactClosed;
    fastsignals::connection documentDeleted;
};

std::map<int, std::shared_ptr<DetachedExactAbort>>&
detachedExactAborts()
{
    static auto* pending =
        new std::map<int, std::shared_ptr<DetachedExactAbort>>;
    return *pending;
}

void retryDetachedExactAbort(int transactionId)
{
    auto& pending = detachedExactAborts();
    const auto found = pending.find(transactionId);
    if (found == pending.end() || found->second->retrying) {
        return;
    }
    const auto state = found->second;
    state->retrying = true;
    bool closed = false;
    try {
        closed = App::GetApplication().abortTransaction(transactionId);
    }
    catch (...) {
        closed = false;
    }
    closed = closed
        || !App::GetApplication().transactionIsActive(transactionId);
    state->retrying = false;
    if (closed) {
        pending.erase(transactionId);
    }
}

void retainDetachedExactAbort(
    int transactionId,
    const App::Document* closingDocument
)
{
    if (transactionId == App::NullTransaction
        || !App::GetApplication().transactionIsActive(transactionId)) {
        return;
    }
    auto state = std::make_shared<DetachedExactAbort>();
    state->transactionId = transactionId;
    const auto retry = [transactionId](const auto&) {
        retryDetachedExactAbort(transactionId);
    };
    for (auto* document : App::GetApplication().getDocuments()) {
        if (!document || document == closingDocument
            || document->getBookedTransactionID() != transactionId) {
            continue;
        }
        state->participantChanges.push_back(
            document->signalTransactionLockChanged.connect(retry)
        );
        state->participantChanges.push_back(
            document->signalBecameStable.connect(retry)
        );
    }
    state->exactClosed =
        App::GetApplication().signalExactTransactionClosed.connect(
            [transactionId](
                int closedId,
                bool,
                const std::vector<App::Document*>&
            ) {
                if (closedId == transactionId) {
                    detachedExactAborts().erase(transactionId);
                }
            }
        );
    state->documentDeleted =
        App::GetApplication().signalDeletedDocument.connect(
            [transactionId] {
                retryDetachedExactAbort(transactionId);
            }
        );
    detachedExactAborts().insert_or_assign(transactionId, state);
}

template<typename Callable>
void runViewProviderUpdate(const std::string& objectName, Callable&& callable)
{
    try {
        std::forward<Callable>(callable)();
    }
    catch (const Base::MemoryException& error) {
        FC_ERR("Memory exception in " << objectName << " thrown: " << error.what());
    }
    catch (Base::Exception& error) {
        error.reportException();
    }
    catch (const std::exception& error) {
        FC_ERR("C++ exception in " << objectName << " thrown " << error.what());
    }
#ifndef FC_DEBUG
    catch (...) {
        FC_ERR("Unknown exception in Feature " << objectName << " thrown");
    }
#endif
}
}

// Pimpl class
struct DocumentP
{
    using Connection = fastsignals::connection;
    using AdvancedConnection = fastsignals::advanced_connection;

    Thumbnail thumb;
    int _iWinCount;
    int _iDocId;
    bool _isClosing;
    bool closeQueryActive {false};
    bool _isModified;
    std::uint64_t modificationGeneration {0};
    std::optional<std::uint64_t> savedModificationGeneration;
    bool _isTransacting;
    bool _isActive;
    bool _changeViewTouchDocument;
    bool _editWantsRestore;
    bool _editWantsRestorePrevious;
    bool _abortEditTransaction;
    bool _editTransactionLocked;
    bool _editTransactionClosePending;
    bool _editTransactionClosing;
    int _editTransactionId;
    std::vector<Connection> _editTransactionRetryConnections;
    Connection _editTransactionExactCloseConnection;
    int _editMode;
    int _editModePrevious;
    ViewProvider* _editViewProvider;
    ViewProvider* _editViewProviderPrevious;
    App::DocumentObject* _editingObject;
    ViewProviderDocumentObject* _editViewProviderParent;
    std::string _editSubname;
    std::string _editSubElement;
    std::string _workbenchName;  // Name of the workbench acting on this document
    Base::Matrix4D _editingTransform;
    View3DInventorViewer* _editingViewer;
    std::set<const App::DocumentObject*> _editObjs;

    Application* _pcAppWnd;
    // the doc/Document
    App::Document* _pcDocument;
    /// List of all registered views
    std::list<Gui::BaseView*> baseViews;
    /// List of all registered views
    std::list<Gui::BaseView*> passiveViews;
    std::map<const App::DocumentObject*, ViewProviderDocumentObject*> _ViewProviderMap;
    std::map<SoSeparator*, ViewProviderDocumentObject*> _CoinMap;
    std::map<std::string, ViewProvider*> _ViewProviderMapAnnotation;
    std::list<ViewProviderDocumentObject*> _redoViewProviders;

    struct DeferredNewViewProvider
    {
        long objectId;
        bool updateView;
        bool recordRedo;
    };
    struct DeferredViewUpdate
    {
        std::set<std::string> propertyNames;
        bool refreshAll {false};
    };
    std::deque<long> deferredNewViewProviderOrder;
    std::unordered_map<long, DeferredNewViewProvider> deferredNewViewProviders;
    std::deque<long> deferredViewUpdateOrder;
    std::unordered_map<long, DeferredViewUpdate> deferredViewUpdates;
    std::deque<long> deferredRelabelOrder;
    std::unordered_set<long> deferredRelabels;
    long deferredActivatedObjectId {0};
    bool deferredActionUpdate {false};
    bool bulkViewAdoptionLease {false};
    bool bulkViewAdoptionScheduled {false};
    bool bulkViewAdoptionFinishing {false};
    std::unique_ptr<QObject> bulkViewDispatchContext;
    std::unique_ptr<ModelTreeBrowserCache> modelBrowserCache;
    std::unordered_map<BaseView*, QPointer<QWidget>> presentationSuspendedViews;

    Connection connectNewObject;
    Connection connectDelObject;
    Connection connectCngObject;
    Connection connectRenObject;
    AdvancedConnection connectActObject;
    Connection connectSaveDocument;
    Connection connectRestDocument;
    Connection connectStartLoadDocument;
    Connection connectFinishLoadDocument;
    Connection connectShowHidden;
    Connection connectFinishRestoreDocument;
    Connection connectFinishRestoreObject;
    Connection connectExportObjects;
    Connection connectImportObjects;
    Connection connectFinishImportObjects;
    Connection connectUndoDocument;
    Connection connectRedoDocument;
    Connection connectRecomputed;
    Connection connectSkipRecompute;
    Connection connectTransactionAppend;
    Connection connectTransactionRemove;
    Connection connectBookedTransactionChanged;
    Connection connectTransactionLockChanged;
    Connection connectCooperativeMutationChanged;
    Connection connectPresentationUpdateChanged;
    Connection connectTouchedObject;
    Connection connectChangePropertyEditor;
    AdvancedConnection connectChangeDocument;

    using ConnectionBlock = fastsignals::shared_connection_block;
    ConnectionBlock connectActObjectBlocker;
    ConnectionBlock connectChangeDocumentBlocker;

    static ViewProviderDocumentObject* throwIfCastFails(ViewProvider* p)
    {
        if (auto vp = freecad_cast<ViewProviderDocumentObject*>(p)) {
            return vp;
        }

        throw Base::RuntimeError("cannot edit non ViewProviderDocumentObject");
    }

    static App::DocumentObject* tryGetObject(ViewProviderDocumentObject* vp)
    {
        auto obj = vp->getObject();
        if (!obj->isAttachedToDocument()) {
            throw Base::RuntimeError("cannot edit detached object");
        }

        return obj;
    }

    void throwIfNotInMap(App::DocumentObject* obj, App::Document* doc) const
    {
        if (_ViewProviderMap.find(obj) == _ViewProviderMap.end()) {
            // We can actually support editing external object, by calling
            // View3DInventViewer::setupEditingRoot() before exiting from
            // ViewProvider::setEditViewer(), which transfer all child node of the view
            // provider into an editing node inside the viewer of this document. And
            // that's may actually be the case, as the subname referenced sub object
            // is allowed to be in other documents.
            //
            // We just disabling editing external parent object here, for bug
            // tracking purpose. Because, bringing an unrelated external object to
            // the current view for editing will confuse user, and is certainly a
            // bug. By right, the top parent object should always belong to the
            // editing document, and the actually editing sub object can be
            // external.
            //
            // So, you can either call setEdit() with subname set to 0, which cause
            // the code above to auto detect selection context, and dispatch the
            // editing call to the correct document. Or, supply subname yourself,
            // and make sure you get the document right.
            //
            std::stringstream str;
            str << "cannot edit object '" << obj->getNameInDocument() << "': not found in document "
                << "'" << doc->getName() << "'";
            throw Base::RuntimeError(str.str());
        }
    }

    App::DocumentObject* tryGetSubObject(App::DocumentObject* obj, const char* subname)
    {
        _editingTransform = Base::Matrix4D();
        auto sobj = obj->getSubObject(subname, nullptr, &_editingTransform);
        if (!sobj || !sobj->isAttachedToDocument()) {
            std::stringstream str;
            str << "Invalid sub object '" << obj->getFullName() << '.' << (subname ? subname : "")
                << "'";
            throw Base::RuntimeError(str.str());
        }

        return sobj;
    }

    ViewProviderDocumentObject* tryGetSubViewProvider(
        ViewProviderDocumentObject* vp,
        App::DocumentObject* obj,
        App::DocumentObject* sobj
    ) const
    {
        auto svp = vp;
        if (sobj != obj) {
            svp = freecad_cast<ViewProviderDocumentObject*>(
                Application::Instance->getViewProvider(sobj)
            );
            if (!svp) {
                std::stringstream str;
                str << "Cannot edit '" << sobj->getFullName() << "' without view provider";
                throw Base::RuntimeError(str.str());
            }
        }

        return svp;
    }

    void setParentViewProvider(ViewProviderDocumentObject* vp)
    {
        _editViewProviderParent = vp;
    }

    void clearSubElement()
    {
        _editSubElement.clear();
        _editSubname.clear();
    }

    void findElementName(const char* subname)
    {
        if (subname) {
            const char* element = Data::findElementName(subname);
            if (element) {
                _editSubname = std::string(subname, element - subname);
                _editSubElement = element;
            }
            else {
                _editSubname = subname;
            }
        }
    }

    void findSubObjectList(App::DocumentObject* obj, const char* subname)
    {
        auto sobjs = obj->getSubObjectList(subname);
        _editObjs.clear();
        _editObjs.insert(sobjs.begin(), sobjs.end());
    }

    bool tryStartEditing(
        ViewProviderDocumentObject* vp,
        App::DocumentObject* obj,
        const char* subname,
        int ModNum
    )
    {
        auto sobj = tryGetSubObject(obj, subname);
        auto svp = tryGetSubViewProvider(vp, obj, sobj);

        setParentViewProvider(vp);
        clearSubElement();
        findElementName(subname);
        findSubObjectList(obj, subname);
        return tryStartEditing(svp, sobj, ModNum);
    }

    bool tryStartEditing(ViewProviderDocumentObject* svp, App::DocumentObject* sobj, int ModNum)
    {
        _editingObject = sobj;
        _editMode = ModNum;
        _editViewProvider = svp;  // Used to resolve start editing (find the document in edit from
                                  // within the viewprovider)
        _editViewProvider = svp->startEditing(ModNum);
        if (!_editViewProvider) {
            _editViewProviderParent = nullptr;
            _editObjs.clear();
            _editingObject = nullptr;
            FC_LOG("object '" << sobj->getFullName() << "' refuse to edit");
            return false;
        }

        return true;
    }

    void setEditingViewerIfPossible(View3DInventor* view3d, int ModNum)
    {
        if (view3d) {
            view3d->getViewer()->setEditingViewProvider(_editViewProvider, ModNum);
            _editingViewer = view3d->getViewer();
        }
    }

    void signalEditMode()
    {
        if (auto vpd = freecad_cast<ViewProviderDocumentObject*>(_editViewProvider)) {
            vpd->getDocument()->signalInEdit(*vpd);
        }
    }

    void setDocumentNameOfTaskDialog(App::Document* doc)
    {
        Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog(_pcDocument);
        if (dlg) {
            dlg->setDocumentName(doc->getName());
        }
    }
};

class ParentFinder
{
public:
    ParentFinder(App::DocumentObject* obj, ViewProviderDocumentObject* vp, const std::string& subname)
        : obj {obj}
        , vp {vp}
        , subname {subname}
    {}

    App::DocumentObject* getObject() const
    {
        return obj;
    }

    ViewProviderDocumentObject* getViewProvider() const
    {
        return vp;
    }

    std::string getSubname() const
    {
        return subname;
    }

    bool findParent()
    {
        auto result = findParentAndSubName(obj);
        App::DocumentObject* parentObj = std::get<0>(result);

        if (parentObj) {
            subname = std::get<1>(result);
            obj = parentObj;
            vp = findParentObject(parentObj, subname.c_str());
            return true;
        }

        return false;
    }

private:
    static std::tuple<App::DocumentObject*, std::string> findParentAndSubName(App::DocumentObject* obj)
    {
        // No subname reference is given, we try to extract one from the current
        // selection in order to obtain the correct transformation matrix below
        auto sels = Gui::Selection().getCompleteSelection(ResolveMode::NoResolve);
        App::DocumentObject* parentObj = nullptr;
        std::string _subname;
        for (auto& sel : sels) {
            if (!sel.pObject || !sel.pObject->isAttachedToDocument()) {
                continue;
            }
            if (!parentObj) {
                parentObj = sel.pObject;
            }
            else if (parentObj != sel.pObject) {
                FC_LOG("Cannot deduce subname for editing, more than one parent?");
                parentObj = nullptr;
                break;
            }

            auto sobj = parentObj->getSubObject(sel.SubName);
            if (!sobj || (sobj != obj && sobj->getLinkedObject(true) != obj)) {
                FC_LOG("Cannot deduce subname for editing, subname mismatch");
                parentObj = nullptr;
                break;
            }

            _subname = sel.SubName;
        }

        return std::make_tuple(parentObj, _subname);
    }

    static Gui::ViewProviderDocumentObject* findParentObject(
        App::DocumentObject* parentObj,
        const char* subname
    )
    {
        FC_LOG("deduced editing reference " << parentObj->getFullName() << '.' << subname);
        auto vp = freecad_cast<ViewProviderDocumentObject*>(
            Application::Instance->getViewProvider(parentObj)
        );
        if (!vp || !vp->getDocument()) {
            throw Base::RuntimeError("invalid view provider for parent object");
        }

        return vp;
    }

private:
    App::DocumentObject* obj;
    ViewProviderDocumentObject* vp;
    std::string subname;
};

}  // namespace Gui

/* TRANSLATOR Gui::Document */

/// @namespace Gui @class Document

int Document::_iDocCount = 0;

Document::Document(App::Document* pcDocument, Application* app)
{
    d = new DocumentP;
    d->_iWinCount = 1;
    // new instance
    d->_iDocId = (++_iDocCount);
    d->_isClosing = false;
    d->_isModified = false;
    d->_isTransacting = false;
    d->_isActive = false;
    d->_pcAppWnd = app;
    d->_pcDocument = pcDocument;
    d->_editViewProvider = nullptr;
    d->_editViewProviderPrevious = nullptr;
    d->_editingObject = nullptr;
    d->_editViewProviderParent = nullptr;
    d->_editingViewer = nullptr;
    d->_editMode = 0;
    d->_editModePrevious = 0;
    d->_editWantsRestore = false;
    d->_editWantsRestorePrevious = false;
    d->_abortEditTransaction = false;
    d->_editTransactionLocked = false;
    d->_editTransactionClosePending = false;
    d->_editTransactionClosing = false;
    d->_editTransactionId = App::NullTransaction;
    d->bulkViewDispatchContext = std::make_unique<QObject>();

    // NOLINTBEGIN
    //  Setup the connections
    d->connectNewObject = pcDocument->signalNewObject.connect(
        std::bind(&Gui::Document::slotNewObject, this, sp::_1)
    );
    d->connectDelObject = pcDocument->signalDeletedObject.connect(
        std::bind(&Gui::Document::slotDeletedObject, this, sp::_1)
    );
    d->connectCngObject = pcDocument->signalChangedObject.connect(
        std::bind(&Gui::Document::slotChangedObject, this, sp::_1, sp::_2)
    );
    d->connectRenObject = pcDocument->signalRelabelObject.connect(
        std::bind(&Gui::Document::slotRelabelObject, this, sp::_1)
    );
    d->connectActObject = pcDocument->signalActivatedObject.connect(
        std::bind(&Gui::Document::slotActivatedObject, this, sp::_1),
        fastsignals::advanced_tag()
    );
    d->connectActObjectBlocker = fastsignals::shared_connection_block(d->connectActObject, false);
    d->connectSaveDocument = pcDocument->signalSaveDocument.connect(
        std::bind(&Gui::Document::Save, this, sp::_1)
    );
    d->connectRestDocument = pcDocument->signalRestoreDocument.connect(
        std::bind(&Gui::Document::Restore, this, sp::_1)
    );
    d->connectStartLoadDocument = App::GetApplication().signalStartRestoreDocument.connect(
        std::bind(&Gui::Document::slotStartRestoreDocument, this, sp::_1)
    );
    d->connectFinishLoadDocument = App::GetApplication().signalFinishRestoreDocument.connect(
        std::bind(&Gui::Document::slotFinishRestoreDocument, this, sp::_1)
    );
    d->connectShowHidden = App::GetApplication().signalShowHidden.connect(
        std::bind(&Gui::Document::slotShowHidden, this, sp::_1)
    );

    d->connectChangePropertyEditor = pcDocument->signalChangePropertyEditor.connect(
        std::bind(&Gui::Document::slotChangePropertyEditor, this, sp::_1, sp::_2)
    );
    d->connectChangeDocument
        = d->_pcDocument->signalChanged.connect  // use the same slot function
          (std::bind(&Gui::Document::slotChangePropertyEditor, this, sp::_1, sp::_2),
           fastsignals::advanced_tag());
    d->connectChangeDocumentBlocker
        = fastsignals::shared_connection_block(d->connectChangeDocument, true);
    d->connectFinishRestoreObject = pcDocument->signalFinishRestoreObject.connect(
        std::bind(&Gui::Document::slotFinishRestoreObject, this, sp::_1)
    );
    d->connectExportObjects = pcDocument->signalExportViewObjects.connect(
        std::bind(&Gui::Document::exportObjects, this, sp::_1, sp::_2)
    );
    d->connectImportObjects = pcDocument->signalImportViewObjects.connect(
        std::bind(&Gui::Document::importObjects, this, sp::_1, sp::_2, sp::_3)
    );
    d->connectFinishImportObjects = pcDocument->signalFinishImportObjects.connect(
        std::bind(&Gui::Document::slotFinishImportObjects, this, sp::_1)
    );

    d->connectUndoDocument = pcDocument->signalUndo.connect(
        std::bind(&Gui::Document::slotUndoDocument, this, sp::_1)
    );
    d->connectRedoDocument = pcDocument->signalRedo.connect(
        std::bind(&Gui::Document::slotRedoDocument, this, sp::_1)
    );
    d->connectRecomputed = pcDocument->signalRecomputed.connect(
        std::bind(&Gui::Document::slotRecomputed, this, sp::_1)
    );
    d->connectSkipRecompute = pcDocument->signalSkipRecompute.connect(
        std::bind(&Gui::Document::slotSkipRecompute, this, sp::_1, sp::_2)
    );
    d->connectTouchedObject = pcDocument->signalTouchedObject.connect(
        std::bind(&Gui::Document::slotTouchedObject, this, sp::_1)
    );

    d->connectTransactionAppend = pcDocument->signalTransactionAppend.connect(
        std::bind(&Gui::Document::slotTransactionAppend, this, sp::_1, sp::_2)
    );
    d->connectTransactionRemove = pcDocument->signalTransactionRemove.connect(
        std::bind(&Gui::Document::slotTransactionRemove, this, sp::_1, sp::_2)
    );
    const auto updateTransactionSensitiveActions = [this](const auto&...) {
        if (d->_pcAppWnd) {
            // Transaction ownership is itself an activation condition for
            // modeling commands. Refresh on the next GUI turn so a ribbon
            // action cannot remain visibly clickable during the old 150 ms
            // polling window after a transaction opens, closes, or locks.
            d->_pcAppWnd->updateActions(false);
        }
    };
    d->connectBookedTransactionChanged =
        pcDocument->signalBookedTransactionChanged.connect(
            updateTransactionSensitiveActions
        );
    d->connectTransactionLockChanged =
        pcDocument->signalTransactionLockChanged.connect(
            updateTransactionSensitiveActions
        );
    d->connectCooperativeMutationChanged =
        pcDocument->signalCooperativeMutationChanged.connect(
            std::bind(
                &Gui::Document::slotCooperativeMutationChanged,
                this,
                sp::_1,
                sp::_2
            )
        );
    d->connectPresentationUpdateChanged =
        pcDocument->signalMutationBlockingPresentationUpdateChanged.connect(
            std::bind(
                &Gui::Document::slotPresentationUpdateChanged,
                this,
                sp::_1,
                sp::_2
            )
        );
    // NOLINTEND

    // pointer to the python class
    // NOTE: As this Python object doesn't get returned to the interpreter we
    // mustn't increment it (Werner Jan-12-2006)
    Base::PyGILStateLocker lock;
    _pcDocPy = new Gui::DocumentPy(this);

    ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Document"
    );
    if (hGrp->GetBool("UsingUndo", true)) {
        d->_pcDocument->setUndoMode(1);
        // set the maximum stack size
        d->_pcDocument->setMaxUndoStackSize(hGrp->GetInt("MaxUndoSize", 20));
    }

    d->_changeViewTouchDocument = hGrp->GetBool("ChangeViewProviderTouchDocument", true);
}

Document::~Document()
{
    PerformanceScope closeTiming("Gui::Document destruction");
    d->modelBrowserCache.reset();
    // A retained no-panel command checkpoint is keyed by this GUI document.
    // Remove it before the pointer can become stale, and resolve the exact
    // adopted transaction while the App document is still alive.
    TaskView::TaskDialog::discardOwnedEditCommandInteraction(this);
    const int closingEditTransaction = d->_editTransactionId;
    if (closingEditTransaction != App::NullTransaction) {
        clearPendingEditTransactionRetry();
        releaseEditTransactionLock();
        bool aborted = false;
        try {
            aborted = App::GetApplication().abortTransaction(
                closingEditTransaction
            );
        }
        catch (...) {
            aborted = false;
        }
        if (!aborted
            && App::GetApplication().transactionIsActive(
                closingEditTransaction
            )) {
            retainDetachedExactAbort(
                closingEditTransaction,
                getDocument()
            );
            Base::Console().error(
                "Could not roll back exact edit transaction %d while "
                "closing its GUI document.\n",
                closingEditTransaction
            );
        }
        d->_editTransactionId = App::NullTransaction;
        d->_editTransactionClosePending = false;
    }

    // disconnect everything to avoid to be double-deleted
    // in case an exception is raised somewhere
    d->connectNewObject.disconnect();
    d->connectDelObject.disconnect();
    d->connectCngObject.disconnect();
    d->connectRenObject.disconnect();
    d->connectActObject.disconnect();
    d->connectSaveDocument.disconnect();
    d->connectRestDocument.disconnect();
    d->connectStartLoadDocument.disconnect();
    d->connectFinishLoadDocument.disconnect();
    d->connectShowHidden.disconnect();
    d->connectFinishRestoreObject.disconnect();
    d->connectExportObjects.disconnect();
    d->connectImportObjects.disconnect();
    d->connectFinishImportObjects.disconnect();
    d->connectUndoDocument.disconnect();
    d->connectRedoDocument.disconnect();
    d->connectRecomputed.disconnect();
    d->connectSkipRecompute.disconnect();
    d->connectTransactionAppend.disconnect();
    d->connectTransactionRemove.disconnect();
    d->connectBookedTransactionChanged.disconnect();
    d->connectTransactionLockChanged.disconnect();
    d->connectCooperativeMutationChanged.disconnect();
    d->connectTouchedObject.disconnect();
    d->connectChangePropertyEditor.disconnect();
    d->connectChangeDocument.disconnect();
    d->bulkViewDispatchContext.reset();

    // e.g. if document gets closed from within a Python command
    d->_isClosing = true;
    // calls Document::detachView() and alter the view list
    std::list<Gui::BaseView*> temp = d->baseViews;
    for (auto& it : temp) {
        it->deleteSelf();
    }

    {
        PerformanceScope providerTiming("Gui::Document view-provider destruction");
        for (const auto& vp : d->_ViewProviderMap) {
            delete vp.second;
        }
    }

    for (const auto& va : d->_ViewProviderMapAnnotation) {
        delete va.second;
    }

    releaseEditTransactionLock();

    // remove the reference from the object
    Base::PyGILStateLocker lock;
    _pcDocPy->setInvalid();
    _pcDocPy->DecRef();
    delete d;
}

//*****************************************************************************************************
// 3D viewer handling
//*****************************************************************************************************

bool Document::setEdit(Gui::ViewProvider* p, int ModNum, const char* subname)
{
    try {
        return trySetEdit(p, ModNum, subname);
    }
    catch (const Base::Exception& e) {
        // A failed edit launch must not leave a transaction attributed to an
        // edit session that the caller was told did not start.
        releaseEditTransactionLock();
        d->_editTransactionId = App::NullTransaction;
        FC_ERR("" << e.what());
        return false;
    }
}

bool Document::adoptEditTransaction(int transactionId)
{
    if (d->_editViewProvider
        && d->_editTransactionLocked
        && d->_editTransactionId == transactionId
        && transactionId != App::NullTransaction
        && getDocument()->getBookedTransactionID() == transactionId
        && App::GetApplication().transactionIsActive(transactionId)) {
        return true;
    }
    if (!d->_editViewProvider
        || transactionId == App::NullTransaction
        || d->_editTransactionId != App::NullTransaction
        || getDocument()->getBookedTransactionID() != transactionId
        || !App::GetApplication().transactionIsActive(transactionId)) {
        return false;
    }

    getDocument()->lockTransaction();
    d->_editTransactionLocked = true;
    d->_editTransactionId = transactionId;
    return true;
}

void Document::releaseEditTransactionLock()
{
    if (!d->_editTransactionLocked) {
        return;
    }

    getDocument()->unlockTransaction();
    d->_editTransactionLocked = false;
}

void Document::clearPendingEditTransactionRetry()
{
    d->_editTransactionRetryConnections.clear();
    d->_editTransactionExactCloseConnection.disconnect();
}

void Document::armPendingEditTransactionRetry()
{
    clearPendingEditTransactionRetry();
    if (!d->_editTransactionClosePending
        || d->_editTransactionId == App::NullTransaction) {
        return;
    }
    const int transactionId = d->_editTransactionId;
    for (auto* document : App::GetApplication().getDocuments()) {
        if (!document
            || document->getBookedTransactionID() != transactionId) {
            continue;
        }
        d->_editTransactionRetryConnections.push_back(
            document->signalTransactionLockChanged.connect(
                [this](const App::Document&) {
                    retryPendingEditTransaction();
                }
            )
        );
        d->_editTransactionRetryConnections.push_back(
            document->signalBecameStable.connect(
                [this](const App::Document&) {
                    retryPendingEditTransaction();
                }
            )
        );
    }
    d->_editTransactionExactCloseConnection =
        App::GetApplication().signalExactTransactionClosed.connect(
            [this](int closedId,
                   bool aborted,
                   const std::vector<App::Document*>&) {
                if (!d->_editTransactionClosing
                    && d->_editTransactionClosePending
                    && d->_editTransactionId == closedId) {
                    completePendingEditTransaction(
                        closedId,
                        !aborted
                    );
                }
            }
        );
}

void Document::completePendingEditTransaction(
    int transactionId,
    bool commit
)
{
    if (!d->_editTransactionClosePending
        || d->_editTransactionId != transactionId) {
        return;
    }
    clearPendingEditTransactionRetry();
    d->_editTransactionClosePending = false;
    d->_editTransactionId = App::NullTransaction;
    d->_abortEditTransaction = false;
    TaskView::TaskDialog::finishOwnedEditCommandInteraction(
        this,
        transactionId,
        !commit,
        true
    );
    Application::Instance->signalFinishEdit(
        *this,
        !commit,
        true
    );
}

void Document::retryPendingEditTransaction()
{
    if (d->_editTransactionClosing
        || !d->_editTransactionClosePending
        || d->_editTransactionId == App::NullTransaction) {
        return;
    }
    const int transactionId = d->_editTransactionId;
    const bool commit = !d->_abortEditTransaction;
    (void)finishEditTransaction(transactionId, commit);
}

void Document::resetIfEditing()
{
    // Fix regression: https://forum.freecad.org/viewtopic.php?f=19&t=43629&p=371972#p371972
    // When an object is already in edit mode a subsequent call for editing is only possible
    // when resetting the currently edited object.
    if (d->_editViewProvider) {
        _resetEdit();
    }
}

View3DInventor* Document::openEditingView3D(const ViewProviderDocumentObject* vp)
{
    auto view3d = dynamic_cast<View3DInventor*>(getActiveView());
    // if the currently active view is not the 3d view search for it and activate it
    if (view3d) {
        getMainWindow()->setActiveWindow(view3d);
    }
    else {
        view3d = dynamic_cast<View3DInventor*>(setActiveView(vp));
    }

    return view3d;
}

View3DInventor* Document::openEditingView3D(const App::DocumentObject* obj)
{
    if (auto vp
        = freecad_cast<ViewProviderDocumentObject*>(Application::Instance->getViewProvider(obj))) {
        return openEditingView3D(vp);
    }

    return nullptr;
}

bool Document::trySetEdit(Gui::ViewProvider* p, int ModNum, const char* subname)
{
    auto vp = DocumentP::throwIfCastFails(p);

    auto obj = DocumentP::tryGetObject(vp);

    std::string _subname = subname ? subname : "";
    if (_subname.empty()) {
        ParentFinder finder(obj, vp, _subname);
        if (finder.findParent()) {
            _subname = finder.getSubname();
            obj = finder.getObject();
            vp = finder.getViewProvider();
            if (vp->getDocument() != this) {
                resetIfEditing();

                return vp->getDocument()->setEdit(vp, ModNum, _subname.c_str());
            }
        }
    }

    // Fix for #13852: When switching edit directly between sketches, resetIfEditing()
    // triggers unsetEdit() on the previous sketch which restores its selection.
    // This clobbers the selection of the new sketch that ParentFinder relies on.
    // Moving resetIfEditing() after ParentFinder ensures we resolve the parent context correctly
    // using the current selection before closing the previous edit.
    resetIfEditing();

    if (d->_editTransactionClosePending) {
        FC_ERR(
            "Cannot enter edit mode while a previous exact edit "
            "transaction still requires closure"
        );
        return false;
    }
    d->throwIfNotInMap(obj, getDocument());
    d->_editTransactionId = App::NullTransaction;

    try {
        Application::Instance->setEditDocument(this);

        if (!d->tryStartEditing(vp, obj, _subname.c_str(), ModNum)) {
            releaseEditTransactionLock();
            d->_editTransactionId = App::NullTransaction;
            d->setDocumentNameOfTaskDialog(getDocument());
            return false;
        }

        d->setDocumentNameOfTaskDialog(getDocument());

        auto view3d = openEditingView3D(vp);
        d->setEditingViewerIfPossible(view3d, ModNum);
        d->signalEditMode();

        return true;
    }
    catch (...) {
        // TaskDialog may have adopted a command transaction while
        // ViewProvider::startEditing() was constructing the panel. If launch
        // does not complete, there is no edit session allowed to own it.
        releaseEditTransactionLock();
        d->_editTransactionId = App::NullTransaction;
        throw;
    }
}

const Base::Matrix4D& Document::getEditingTransform() const
{
    return d->_editingTransform;
}

void Document::setEditingTransform(const Base::Matrix4D& mat)
{
    d->_editObjs.clear();
    d->_editingTransform = mat;
    auto activeView = dynamic_cast<View3DInventor*>(getActiveView());
    if (activeView) {
        activeView->getViewer()->setEditingTransform(mat);
    }
}

void Document::resetEdit()
{
    bool vpIsNotNull = d->_editViewProvider != nullptr;
    bool vpHasChanged = d->_editViewProvider != d->_editViewProviderPrevious;
    int modeToRestore = d->_editModePrevious;
    Gui::ViewProvider* vpToRestore = d->_editViewProviderPrevious;
    bool shouldRestorePrevious = d->_editWantsRestorePrevious;

    Application::Instance->unsetEditDocument(this);

    if (vpIsNotNull && vpHasChanged && shouldRestorePrevious) {
        setEdit(vpToRestore, modeToRestore);
    }
}

void Document::cancelEdit()
{
    if (!d->_editViewProvider) {
        return;
    }
    prepareCancelEdit();
    resetEdit();
}

int Document::prepareCancelEdit()
{
    if (!d->_editViewProvider || !d->_editTransactionLocked
        || d->_editTransactionId == App::NullTransaction
        || getDocument()->getBookedTransactionID()
            != d->_editTransactionId) {
        return App::NullTransaction;
    }

    d->_abortEditTransaction = true;
    return d->_editTransactionId;
}

void Document::clearCancelEdit(int transactionId)
{
    // A declined/failed reject may clear only the mark it established. Never
    // let cleanup for an old dialog alter a replacement edit session.
    if (transactionId != App::NullTransaction
        && d->_editTransactionLocked
        && d->_editTransactionId == transactionId
        && getDocument()->getBookedTransactionID() == transactionId) {
        d->_abortEditTransaction = false;
    }
}

bool Document::ownsEditTransaction(int transactionId) const
{
    return transactionId != App::NullTransaction
        && (d->_editViewProvider
            || d->_editTransactionClosePending)
        && d->_editTransactionLocked
        && d->_editTransactionId == transactionId
        && getDocument()->getBookedTransactionID() == transactionId
        && App::GetApplication().transactionIsActive(transactionId);
}

bool Document::adoptOwnedEditTransaction(int transactionId)
{
    if (!adoptEditTransaction(transactionId)) {
        return false;
    }
    TaskView::TaskDialog::markOwnedEnclosingTransactionAdopted(
        getDocument(),
        transactionId
    );
    TaskView::TaskDialog::adoptOwnedEditCommandInteraction(
        this,
        transactionId
    );
    return true;
}

bool Document::finishEditTransaction(
    int transactionId,
    bool commit
)
{
    if (!ownsEditTransaction(transactionId)) {
        return false;
    }
    if (d->_editTransactionClosePending) {
        // The editor is already gone; retry only the exact outcome retained
        // by the failed teardown. Never reinterpret Cancel as OK (or vice
        // versa) on a later retry.
        const bool pendingCommit = !d->_abortEditTransaction;
        if (commit != pendingCommit
            || getDocument()->getBookedTransactionID()
                != transactionId
            || !App::GetApplication().transactionIsActive(
                transactionId
            )) {
            return false;
        }

        d->_editTransactionClosing = true;
        releaseEditTransactionLock();
        bool closed = false;
        try {
            closed = commit
                ? App::GetApplication().commitTransaction(transactionId)
                : App::GetApplication().abortTransaction(transactionId);
        }
        catch (...) {
            closed = false;
        }
        closed = closed
            || !App::GetApplication().transactionIsActive(transactionId);
        if (!closed) {
            if (getDocument()->getBookedTransactionID()
                    == transactionId
                && App::GetApplication().transactionIsActive(
                    transactionId
                )) {
                getDocument()->lockTransaction();
                d->_editTransactionLocked = true;
            }
            d->_editTransactionClosing = false;
            return false;
        }

        completePendingEditTransaction(transactionId, commit);
        d->_editTransactionClosing = false;
        return true;
    }
    if (!commit) {
        d->_abortEditTransaction = true;
    }
    resetEdit();
    return !App::GetApplication().transactionIsActive(transactionId);
}

void Document::_resetEdit()
{
    const bool abortEditTransaction = d->_abortEditTransaction;
    d->_abortEditTransaction = false;
    const int editTransactionId = d->_editTransactionId;
    d->_editTransactionId = App::NullTransaction;
    if (d->_editViewProvider) {
        for (auto* v : d->baseViews) {
            auto activeView = dynamic_cast<View3DInventor*>(v);
            if (activeView) {
                activeView->getViewer()->resetEditingViewProvider();
            }
        }

        d->_editViewProvider->finishEditing();

        d->_editViewProviderPrevious = d->_editViewProvider;
        d->_editModePrevious = d->_editMode;
        d->_editWantsRestorePrevious = d->_editWantsRestore;
        d->_editWantsRestore = false;

        // Have to check d->_editViewProvider below, because there is a chance
        // the editing object gets deleted inside the above call to
        // 'finishEditing()', which will trigger our slotDeletedObject(), which
        // nullifies _editViewProvider.
        if (d->_editViewProvider
            && d->_editViewProvider->isDerivedFrom<ViewProviderDocumentObject>()) {
            auto vpd = static_cast<ViewProviderDocumentObject*>(d->_editViewProvider);
            vpd->getDocument()->signalResetEdit(*vpd);
        }
        d->_editViewProvider = nullptr;

        // The logic below is not necessary anymore, because this method is
        // changed into a private one,  _resetEdit(). And the exposed
        // resetEdit() above calls into Application->unsetEditDocument() which
        // will prevent recursive calling.

    }

    // The edit lock prevents another caller from replacing or closing the
    // transaction while provisional task geometry is live. Release only when
    // the panel has finished teardown, immediately before closing the exact
    // transaction transferred to this edit session.
    releaseEditTransactionLock();

    bool editTransactionFinished = true;
    // An edit session closes only the command transaction explicitly
    // transferred to it. If that transaction was already closed or
    // replaced, the current transaction belongs to somebody else.
    if (editTransactionId != App::NullTransaction
        && getDocument()->getBookedTransactionID() == editTransactionId) {
        editTransactionFinished = abortEditTransaction
            ? App::GetApplication().abortTransaction(editTransactionId)
            : App::GetApplication().commitTransaction(editTransactionId);
    }
    else if (editTransactionId != App::NullTransaction
             && App::GetApplication().transactionIsActive(
                 editTransactionId
             )) {
        editTransactionFinished = false;
    }
    if (!editTransactionFinished) {
        // Preserve exact ownership after teardown. A cross-document lock can
        // make close fail even though this editor released its own lock; do
        // not leave that live transaction broad, unlocked, or replaceable.
        if (editTransactionId != App::NullTransaction
            && getDocument()->getBookedTransactionID()
                == editTransactionId
            && App::GetApplication().transactionIsActive(
                editTransactionId
            )) {
            getDocument()->lockTransaction();
            d->_editTransactionLocked = true;
            d->_editTransactionClosePending = true;
            d->_editTransactionId = editTransactionId;
            d->_abortEditTransaction = abortEditTransaction;
            armPendingEditTransactionRetry();
        }
        Base::Console().error(
            "Could not %s exact edit transaction %d after editor teardown\n",
            abortEditTransaction ? "roll back" : "commit",
            editTransactionId
        );
    }
    else {
        clearPendingEditTransactionRetry();
        d->_editTransactionClosePending = false;
    }
    d->_editViewProviderParent = nullptr;
    d->_editingViewer = nullptr;
    d->_editObjs.clear();
    d->_editingObject = nullptr;
    Application::Instance->unsetEditDocument(this);
    TaskView::TaskDialog::finishOwnedEditCommandInteraction(
        this,
        editTransactionId,
        abortEditTransaction,
        editTransactionFinished
    );
    Application::Instance->signalFinishEdit(
        *this,
        abortEditTransaction,
        editTransactionFinished
    );
}

ViewProvider* Document::getInEdit(
    ViewProviderDocumentObject** parentVp,
    std::string* subname,
    int* mode,
    std::string* subelement
) const
{
    if (parentVp) {
        *parentVp = d->_editViewProviderParent;
    }
    if (subname) {
        *subname = d->_editSubname;
    }
    if (subelement) {
        *subelement = d->_editSubElement;
    }
    if (mode) {
        *mode = d->_editMode;
    }

    if (d->_editViewProvider) {
        // there is only one 3d view which is in edit mode
        auto activeView = dynamic_cast<View3DInventor*>(getActiveView());
        if (activeView && activeView->getViewer()->isEditingViewProvider()) {
            return d->_editViewProvider;
        }
    }

    return nullptr;
}
ViewProvider* Document::getEditViewProvider() const
{
    return d->_editViewProvider;
}

void Document::setInEdit(ViewProviderDocumentObject* parentVp, const char* subname)
{
    if (d->_editViewProvider) {
        d->_editViewProviderParent = parentVp;
        d->_editSubname = subname ? subname : "";
    }
}

void Document::setAnnotationViewProvider(const char* name, ViewProvider* pcProvider)
{
    // already in ?
    std::map<std::string, ViewProvider*>::iterator it = d->_ViewProviderMapAnnotation.find(name);
    if (it != d->_ViewProviderMapAnnotation.end()) {
        removeAnnotationViewProvider(name);
    }

    // add
    d->_ViewProviderMapAnnotation[name] = pcProvider;

    // cycling to all views of the document
    for (auto* v : d->baseViews) {
        auto activeView = dynamic_cast<View3DInventor*>(v);
        if (activeView) {
            activeView->getViewer()->addViewProvider(pcProvider);
        }
    }
}

void Document::setEditRestore(bool askRestore)
{
    d->_editWantsRestore = askRestore;
}

ViewProvider* Document::getAnnotationViewProvider(const char* name) const
{
    std::map<std::string, ViewProvider*>::const_iterator it = d->_ViewProviderMapAnnotation.find(name);
    return ((it != d->_ViewProviderMapAnnotation.end()) ? it->second : 0);
}

bool Document::isAnnotationViewProvider(const ViewProvider* vp) const
{
    for (const auto& va : d->_ViewProviderMapAnnotation) {
        if (va.second == vp) {
            return true;
        }
    }
    return false;
}


ViewProvider* Document::takeAnnotationViewProvider(const char* name)
{
    auto it = d->_ViewProviderMapAnnotation.find(name);
    if (it == d->_ViewProviderMapAnnotation.end()) {
        return nullptr;
    }

    ViewProvider* vp = it->second;
    d->_ViewProviderMapAnnotation.erase(it);

    // cycling to all views of the document
    for (auto vIt : d->baseViews) {
        if (auto activeView = dynamic_cast<View3DInventor*>(vIt)) {
            activeView->getViewer()->removeViewProvider(vp);
        }
    }

    return vp;
}


void Document::removeAnnotationViewProvider(const char* name)
{
    delete takeAnnotationViewProvider(name);
}


ViewProvider* Document::getViewProvider(const App::DocumentObject* Feat) const
{
    std::map<const App::DocumentObject*, ViewProviderDocumentObject*>::const_iterator it
        = d->_ViewProviderMap.find(Feat);
    return ((it != d->_ViewProviderMap.end()) ? it->second : 0);
}

std::vector<ViewProvider*> Document::getViewProvidersOfType(const Base::Type& typeId) const
{
    std::vector<ViewProvider*> Objects;
    for (const auto& vp : d->_ViewProviderMap) {
        if (vp.second->isDerivedFrom(typeId)) {
            Objects.push_back(vp.second);
        }
    }
    return Objects;
}

ViewProvider* Document::getViewProviderByName(const char* name) const
{
    // first check on feature name
    App::DocumentObject* pcFeat = getDocument()->getObject(name);

    if (pcFeat) {
        std::map<const App::DocumentObject*, ViewProviderDocumentObject*>::const_iterator it
            = d->_ViewProviderMap.find(pcFeat);

        if (it != d->_ViewProviderMap.end()) {
            return it->second;
        }
    }
    else {
        // then try annotation name
        std::map<std::string, ViewProvider*>::const_iterator it2
            = d->_ViewProviderMapAnnotation.find(name);

        if (it2 != d->_ViewProviderMapAnnotation.end()) {
            return it2->second;
        }
    }

    return nullptr;
}

ModelTreeBrowserCache& Document::modelBrowserProjectionCache()
{
    requireMainThread("Gui::Document::modelBrowserProjectionCache");
    if (!d->modelBrowserCache) {
        d->modelBrowserCache = std::make_unique<ModelTreeBrowserCache>(d->_pcDocument);
    }
    return *d->modelBrowserCache;
}

bool Document::projectionRefreshBlocked(const App::Document* document)
{
    return document
        && (document->isCooperativeMutationActive()
            || document->isPerformingTransaction()
            || document->testStatus(App::Document::Recomputing)
            || App::GetApplication().hasPendingRecomputeRequest(document->getName())
            || document->testStatus(App::Document::Restoring)
            || App::GetApplication().isRestoring());
}

bool Document::historyMutationBlocked(const App::Document* document)
{
    return projectionRefreshBlocked(document)
        || (document
            && (document->isMutationBlockingPresentationUpdateActive()
                || document->getBookedTransactionID() != App::NullTransaction
                || document->hasPendingTransaction()
                || document->isTransactionLocked()));
}

bool Document::isShow(const char* name)
{
    ViewProvider* pcProv = getViewProviderByName(name);
    return pcProv ? pcProv->isShow() : false;
}

/// put the feature in show
void Document::setShow(const char* name)
{
    ViewProvider* pcProv = getViewProviderByName(name);

    if (pcProv && pcProv->isDerivedFrom<ViewProviderDocumentObject>()) {
        static_cast<ViewProviderDocumentObject*>(pcProv)->Visibility.setValue(true);
    }
}

/// set the feature in Noshow
void Document::setHide(const char* name)
{
    ViewProvider* pcProv = getViewProviderByName(name);

    if (pcProv && pcProv->isDerivedFrom<ViewProviderDocumentObject>()) {
        static_cast<ViewProviderDocumentObject*>(pcProv)->Visibility.setValue(false);
    }
}

/// set the feature in Noshow
void Document::setPos(const char* name, const Base::Matrix4D& rclMtrx)
{
    ViewProvider* pcProv = getViewProviderByName(name);
    if (pcProv) {
        pcProv->setTransformation(rclMtrx);
    }
}

//*****************************************************************************************************
// Document
//*****************************************************************************************************
bool Document::hasDeferredViewProviderWork() const
{
    return !d->deferredNewViewProviders.empty()
        || !d->deferredViewUpdates.empty()
        || !d->deferredRelabels.empty()
        || d->deferredActivatedObjectId != 0
        || d->deferredActionUpdate;
}

void Document::queueDeferredNewViewProvider(
    const App::DocumentObject& object,
    bool updateView,
    bool recordRedo
)
{
    const long objectId = object.getID();
    const auto [found, inserted] = d->deferredNewViewProviders.try_emplace(
        objectId,
        DocumentP::DeferredNewViewProvider {objectId, updateView, recordRedo}
    );
    if (inserted) {
        d->deferredNewViewProviderOrder.push_back(objectId);
    }
    else {
        found->second.updateView = found->second.updateView || updateView;
        found->second.recordRedo = found->second.recordRedo || recordRedo;
    }

    // A provider's first full update observes the final state of every App
    // property. Earlier incremental notifications for that same new object
    // would only repeat work and expose a half-constructed representation.
    d->deferredViewUpdates.erase(objectId);
    d->deferredRelabels.erase(objectId);
    d->deferredActionUpdate = true;
}

void Document::slotCooperativeMutationChanged(
    const App::Document& document,
    bool active
)
{
    if (&document != d->_pcDocument) {
        return;
    }

    // App::Document objects are mutated by the document workers. Coin may not
    // traverse those same live shapes concurrently, so suspend only this
    // document's canvases for the mutation lifetime. The main window, task
    // panel and progress UI continue to process events.
    for (auto* view : getMDIViews(true)) {
        synchronizePresentationView(*view);
    }

    if (active
        || d->bulkViewAdoptionLease
        || d->bulkViewAdoptionFinishing
        || !hasDeferredViewProviderWork()) {
        if (!active) {
            slotPresentationUpdateChanged(document, false);
        }
        return;
    }

    // Deferred view-provider work is bounded GUI presentation, not a model
    // mutation. Keep its lifetime visible to close/save coordination without
    // rejecting the next modeling command after recompute has completed.
    d->bulkViewAdoptionLease = true;
    d->_pcDocument->beginVisualUpdate();
    scheduleDeferredViewProviderWork();
    slotPresentationUpdateChanged(document, false);
}

void Document::slotPresentationUpdateChanged(
    const App::Document& document,
    bool /*active*/
)
{
    if (&document != d->_pcDocument) {
        return;
    }

    const bool suspend = document.isCooperativeMutationActive()
        || document.isMutationBlockingPresentationUpdateActive();
    if (suspend) {
        for (auto* view : getMDIViews(true)) {
            synchronizePresentationView(*view);
        }
        return;
    }

    auto suspended = std::move(d->presentationSuspendedViews);
    d->presentationSuspendedViews.clear();
    for (const auto& [view, widget] : suspended) {
        (void)view;
        if (widget) {
            widget->setUpdatesEnabled(true);
        }
    }
}

void Document::synchronizePresentationView(MDIView& view)
{
    if (!(d->_pcDocument->isCooperativeMutationActive()
          || d->_pcDocument->isMutationBlockingPresentationUpdateActive())
        || !view.updatesEnabled()) {
        return;
    }
    view.setUpdatesEnabled(false);
    d->presentationSuspendedViews.try_emplace(&view, &view);
}

void Document::scheduleDeferredViewProviderWork()
{
    if (d->bulkViewAdoptionScheduled) {
        return;
    }
    Q_ASSERT(d->bulkViewDispatchContext);
    d->bulkViewAdoptionScheduled = true;
    if (!dispatchToGuiFrame(
        d->bulkViewDispatchContext.get(),
        [this] { drainDeferredViewProviderWork(); }
    )) {
        d->bulkViewAdoptionScheduled = false;
        FC_ERR("Cannot schedule deferred view-provider adoption");
    }
}

void Document::finishNewViewProvider(
    long objectId,
    bool updateView,
    bool recordRedo
)
{
    auto* object = d->_pcDocument->getObjectByID(objectId);
    auto* provider = object
        ? freecad_cast<ViewProviderDocumentObject*>(getViewProvider(object))
        : nullptr;
    if (!object || !provider || provider->getObject() != object) {
        return;
    }

    const std::string objectName = object->getFullName();
    if (updateView) {
        runViewProviderUpdate(objectName, [provider] {
            provider->updateView();
            provider->setActiveMode();
        });
    }

    object = d->_pcDocument->getObjectByID(objectId);
    provider = object
        ? freecad_cast<ViewProviderDocumentObject*>(getViewProvider(object))
        : nullptr;
    if (!object || !provider || provider->getObject() != object) {
        return;
    }

    // From this point onward deletion must follow the ordinary exposed-view
    // path so a callback can remove the provider from viewers and observers.
    d->deferredNewViewProviders.erase(objectId);
    for (auto* view : d->baseViews) {
        if (auto* activeView = dynamic_cast<View3DInventor*>(view)) {
            activeView->getViewer()->addViewProvider(provider);
        }
    }

    signalNewObject(*provider);

    object = d->_pcDocument->getObjectByID(objectId);
    provider = object
        ? freecad_cast<ViewProviderDocumentObject*>(getViewProvider(object))
        : nullptr;
    if (!object || !provider || provider->getObject() != object) {
        return;
    }
    provider->pcDocument = this;
    handleChildren3D(provider);
    if (recordRedo) {
        d->_redoViewProviders.push_back(provider);
    }
}

void Document::finishDeferredViewUpdate(
    long objectId,
    const std::set<std::string>& propertyNames,
    bool refreshAll
)
{
    auto* object = d->_pcDocument->getObjectByID(objectId);
    auto* provider = object ? getViewProvider(object) : nullptr;
    if (!object || !provider) {
        return;
    }
    const std::string objectName = object->getFullName();
    bool editingTransformChanged = refreshAll;

    if (refreshAll) {
        auto* documentProvider = freecad_cast<ViewProviderDocumentObject*>(provider);
        if (documentProvider) {
            runViewProviderUpdate(objectName, [documentProvider] {
                documentProvider->updateView();
            });
        }
    }
    else {
        for (const auto& propertyName : propertyNames) {
            object = d->_pcDocument->getObjectByID(objectId);
            provider = object ? getViewProvider(object) : nullptr;
            auto* property = object
                ? object->getPropertyByName(propertyName.c_str())
                : nullptr;
            if (!object || !provider || !property) {
                continue;
            }
            editingTransformChanged = editingTransformChanged
                || property->isDerivedFrom<App::PropertyPlacement>()
                || propertyName.find("Scale") != std::string::npos;
            runViewProviderUpdate(objectName, [provider, property] {
                provider->update(property);
            });
        }
    }

    object = d->_pcDocument->getObjectByID(objectId);
    provider = object ? getViewProvider(object) : nullptr;
    if (!object || !provider) {
        return;
    }
    if (editingTransformChanged
        && d->_editingViewer
        && d->_editingObject
        && d->_editViewProviderParent
        && d->_editObjs.contains(object)) {
        Base::Matrix4D matrix;
        auto* editedObject = d->_editViewProviderParent->getObject()->getSubObject(
            d->_editSubname.c_str(),
            nullptr,
            &matrix
        );
        if (editedObject == d->_editingObject && d->_editingTransform != matrix) {
            d->_editingTransform = matrix;
            d->_editingViewer->setEditingTransform(d->_editingTransform);
        }
    }

    handleChildren3D(provider);
    for (const auto& propertyName : propertyNames) {
        object = d->_pcDocument->getObjectByID(objectId);
        auto* documentProvider = object
            ? freecad_cast<ViewProviderDocumentObject*>(getViewProvider(object))
            : nullptr;
        auto* property = object
            ? object->getPropertyByName(propertyName.c_str())
            : nullptr;
        if (!object || !documentProvider || !property) {
            continue;
        }
        signalChangedObject(*documentProvider, *property);
    }
}

void Document::drainDeferredViewProviderWork()
{
    d->bulkViewAdoptionScheduled = false;
    FrameBudget budget;

    while (!budget.exhausted()) {
        bool consumedWork = false;

        while (!d->deferredNewViewProviderOrder.empty()) {
            const long objectId = d->deferredNewViewProviderOrder.front();
            d->deferredNewViewProviderOrder.pop_front();
            const auto found = d->deferredNewViewProviders.find(objectId);
            if (found == d->deferredNewViewProviders.end()) {
                continue;
            }
            const auto work = found->second;
            finishNewViewProvider(work.objectId, work.updateView, work.recordRedo);
            d->deferredNewViewProviders.erase(objectId);
            consumedWork = true;
            break;
        }
        if (consumedWork) {
            continue;
        }

        while (!d->deferredViewUpdateOrder.empty()) {
            const long objectId = d->deferredViewUpdateOrder.front();
            d->deferredViewUpdateOrder.pop_front();
            const auto found = d->deferredViewUpdates.find(objectId);
            if (found == d->deferredViewUpdates.end()) {
                continue;
            }
            const auto work = std::move(found->second);
            d->deferredViewUpdates.erase(found);
            finishDeferredViewUpdate(objectId, work.propertyNames, work.refreshAll);
            consumedWork = true;
            break;
        }
        if (consumedWork) {
            continue;
        }

        while (!d->deferredRelabelOrder.empty()) {
            const long objectId = d->deferredRelabelOrder.front();
            d->deferredRelabelOrder.pop_front();
            if (d->deferredRelabels.erase(objectId) == 0) {
                continue;
            }
            auto* object = d->_pcDocument->getObjectByID(objectId);
            auto* provider = object
                ? freecad_cast<ViewProviderDocumentObject*>(getViewProvider(object))
                : nullptr;
            if (provider) {
                signalRelabelObject(*provider);
            }
            consumedWork = true;
            break;
        }
        if (consumedWork) {
            continue;
        }

        if (d->deferredActivatedObjectId != 0) {
            const long objectId = d->deferredActivatedObjectId;
            d->deferredActivatedObjectId = 0;
            auto* object = d->_pcDocument->getObjectByID(objectId);
            auto* provider = object
                ? freecad_cast<ViewProviderDocumentObject*>(getViewProvider(object))
                : nullptr;
            if (provider) {
                signalActivatedObject(*provider);
            }
            continue;
        }

        break;
    }

    const bool hasQueuedProjectionWork =
        !d->deferredNewViewProviders.empty()
        || !d->deferredViewUpdates.empty()
        || !d->deferredRelabels.empty()
        || d->deferredActivatedObjectId != 0;
    if (hasQueuedProjectionWork) {
        scheduleDeferredViewProviderWork();
        return;
    }

    if (d->deferredActionUpdate) {
        d->deferredActionUpdate = false;
        getMainWindow()->updateActions(true);
        if (hasDeferredViewProviderWork()) {
            scheduleDeferredViewProviderWork();
            return;
        }
    }

    if (d->bulkViewAdoptionLease) {
        d->bulkViewAdoptionFinishing = true;
        d->bulkViewAdoptionLease = false;
        d->_pcDocument->endVisualUpdate();
        d->bulkViewAdoptionFinishing = false;
    }
}

void Document::slotNewObject(const App::DocumentObject& Obj)
{
    const bool traceRestore = d->_pcDocument->testStatus(App::Document::Status::Restoring)
        && qEnvironmentVariableIsSet("VIBECAD_RESTORE_DETAIL_TRACE");
    const auto traceStart = std::chrono::steady_clock::now();
    auto providerTypeReady = traceStart;
    auto providerCreated = traceStart;
    auto providerAttached = traceStart;
    auto providerUpdated = traceStart;
    auto providerPublished = traceStart;
    const bool deferViewAdoption = d->_pcDocument->isCooperativeMutationActive();
    bool createdProvider = false;
    auto pcProvider = static_cast<ViewProviderDocumentObject*>(getViewProvider(&Obj));
    if (!pcProvider) {
        std::string_view cName {Obj.getViewProviderNameStored()};
        for (;;) {
            if (cName.empty()) {
                // handle document object with no view provider specified
                FC_LOG(Obj.getFullName() << " has no view provider specified");
                return;
            }
            Base::Type type = Base::Type::getTypeIfDerivedFrom(
                cName,
                ViewProviderDocumentObject::getClassTypeId(),
                true
            );
            providerTypeReady = std::chrono::steady_clock::now();
            pcProvider = static_cast<ViewProviderDocumentObject*>(type.createInstance());
            providerCreated = std::chrono::steady_clock::now();
            // createInstance could return a null pointer
            if (!pcProvider) {
                // type not derived from ViewProviderDocumentObject!!!
                FC_ERR("Invalid view provider type '" << cName << "' for " << Obj.getFullName());
                return;
            }
            else if (cName != Obj.getViewProviderName() && !pcProvider->allowOverride(Obj)) {
                FC_WARN("View provider type '" << cName << "' does not support " << Obj.getFullName());
                delete pcProvider;
                pcProvider = nullptr;
                cName = Obj.getViewProviderName();
            }
            else {
                createdProvider = true;
                break;
            }
        }

        setModified(true);
        d->_ViewProviderMap[&Obj] = pcProvider;
        d->_CoinMap[pcProvider->getRoot()] = pcProvider;
        pcProvider->setStatus(Gui::ViewStatus::TouchDocument, d->_changeViewTouchDocument);

        try {
            // if successfully created set the right name and calculate the view
            // FIXME: Consider to change argument of attach() to const pointer
            pcProvider->attach(const_cast<App::DocumentObject*>(&Obj));
            providerAttached = std::chrono::steady_clock::now();
            if (!deferViewAdoption) {
                pcProvider->updateView();
                pcProvider->setActiveMode();
            }
            providerUpdated = std::chrono::steady_clock::now();
        }
        catch (const Base::MemoryException& e) {
            FC_ERR("Memory exception in " << Obj.getFullName() << " thrown: " << e.what());
        }
        catch (Base::Exception& e) {
            e.reportException();
        }
#ifndef FC_DEBUG
        catch (...) {
            FC_ERR("Unknown exception in Feature " << Obj.getFullName() << " thrown");
        }
#endif
    }
    else {
        try {
            pcProvider->reattach(const_cast<App::DocumentObject*>(&Obj));
        }
        catch (Base::Exception& e) {
            e.reportException();
        }
    }

    if (pcProvider) {
        if (deferViewAdoption) {
            queueDeferredNewViewProvider(
                Obj,
                createdProvider,
                d->_isTransacting
            );
            return;
        }

        // cycling to all views of the document
        for (auto* v : d->baseViews) {
            auto activeView = dynamic_cast<View3DInventor*>(v);
            if (activeView) {
                activeView->getViewer()->addViewProvider(pcProvider);
            }
        }

        // adding to the tree
        signalNewObject(*pcProvider);
        pcProvider->pcDocument = this;

        // it is possible that a new viewprovider already claims children
        handleChildren3D(pcProvider);
        providerPublished = std::chrono::steady_clock::now();
        if (d->_isTransacting) {
            d->_redoViewProviders.push_back(pcProvider);
        }
    }

    if (traceRestore) {
        const auto elapsed = [](auto begin, auto end) {
            return std::chrono::duration_cast<std::chrono::milliseconds>(end - begin).count();
        };
        const auto end = std::chrono::steady_clock::now();
        const auto total = elapsed(traceStart, end);
        if (total >= 20) {
            Base::Console().message(
                "VIBECAD_RESTORE_DETAIL new_view_provider object=%s stored_type=%s "
                "type_lookup_ms=%lld create_ms=%lld attach_ms=%lld update_ms=%lld "
                "publish_ms=%lld remainder_ms=%lld total_ms=%lld\n",
                Obj.getNameInDocument(),
                Obj.getViewProviderNameStored(),
                static_cast<long long>(elapsed(traceStart, providerTypeReady)),
                static_cast<long long>(elapsed(providerTypeReady, providerCreated)),
                static_cast<long long>(elapsed(providerCreated, providerAttached)),
                static_cast<long long>(elapsed(providerAttached, providerUpdated)),
                static_cast<long long>(elapsed(providerUpdated, providerPublished)),
                static_cast<long long>(elapsed(providerPublished, end)),
                static_cast<long long>(total)
            );
        }
    }
}

void Document::slotDeletedObject(const App::DocumentObject& Obj)
{
    setModified(true);

    // cycling to all views of the document
    ViewProvider* viewProvider = getViewProvider(&Obj);
    if (!viewProvider) {
        return;
    }

    const long objectId = Obj.getID();
    if (d->deferredNewViewProviders.erase(objectId) != 0) {
        d->deferredViewUpdates.erase(objectId);
        d->deferredRelabels.erase(objectId);
        if (d->deferredActivatedObjectId == objectId) {
            d->deferredActivatedObjectId = 0;
        }
        // The provider was reserved for transaction identity but was never
        // published to a viewer or projection, so no inverse GUI notification
        // is valid. slotTransactionRemove() owns its transfer or deletion.
        viewProvider->beforeDelete();
        d->deferredActionUpdate = true;
        return;
    }

    if (d->_editViewProvider == viewProvider || d->_editViewProviderParent == viewProvider) {
        _resetEdit();
    }
    else {
        Application::Instance->unsetEditDocumentIf([&viewProvider](Gui::Document* editdoc) {
            return editdoc->d->_editViewProvider == viewProvider
                || editdoc->d->_editViewProviderParent == viewProvider;
        });
    }

    handleChildren3D(viewProvider, true);

    if (viewProvider && viewProvider->isDerivedFrom(ViewProviderDocumentObject::getClassTypeId())) {
        // go through the views
        for (auto* v : d->baseViews) {
            auto activeView = dynamic_cast<View3DInventor*>(v);
            if (activeView) {
                activeView->getViewer()->removeViewProvider(viewProvider);
            }
        }

        // removing from tree
        signalDeletedObject(*(static_cast<ViewProviderDocumentObject*>(viewProvider)));
    }

    viewProvider->beforeDelete();
}

void Document::beforeDelete()
{
    PerformanceScope closeTiming("Gui::Document beforeDelete");
    Application::Instance->unsetEditDocumentIf([this](Gui::Document* editDoc) {
        auto vp = freecad_cast<ViewProviderDocumentObject*>(editDoc->d->_editViewProvider);
        auto vpp = freecad_cast<ViewProviderDocumentObject*>(editDoc->d->_editViewProviderParent);

        return editDoc == this || (vp && vp->getDocument() == this)
            || (vpp && vpp->getDocument() == this);
    });
    for (auto& v : d->_ViewProviderMap) {
        v.second->beforeDelete();
    }
}

void Document::slotChangedObject(const App::DocumentObject& Obj, const App::Property& Prop)
{
    if (d->_pcDocument->isCooperativeMutationActive()) {
        if (!Prop.testStatus(App::Property::NoModify)) {
            FC_LOG(Prop.getFullName() << " modified");
            setModified(true);
        }
        d->deferredActionUpdate = true;

        const long objectId = Obj.getID();
        if (getViewProvider(&Obj)
            && !d->deferredNewViewProviders.contains(objectId)) {
            const auto [found, inserted] =
                d->deferredViewUpdates.try_emplace(objectId);
            if (inserted) {
                d->deferredViewUpdateOrder.push_back(objectId);
            }
            if (const char* propertyName = Prop.getName()) {
                found->second.propertyNames.emplace(propertyName);
            }
            else {
                found->second.refreshAll = true;
            }
        }
        return;
    }

    ViewProvider* viewProvider = getViewProvider(&Obj);
    if (viewProvider) {
        try {
            viewProvider->update(&Prop);
            if (d->_editingViewer && d->_editingObject && d->_editViewProviderParent
                && (Prop.isDerivedFrom<App::PropertyPlacement>()
                    // Issue ID 0004230 : getName() can return null in which case strstr() crashes
                    || (Prop.getName() && strstr(Prop.getName(), "Scale")))
                && d->_editObjs.contains(&Obj)) {
                Base::Matrix4D mat;
                auto sobj = d->_editViewProviderParent->getObject()
                                ->getSubObject(d->_editSubname.c_str(), nullptr, &mat);
                if (sobj == d->_editingObject && d->_editingTransform != mat) {
                    d->_editingTransform = mat;
                    d->_editingViewer->setEditingTransform(d->_editingTransform);
                }
            }
        }
        catch (const Base::MemoryException& e) {
            FC_ERR("Memory exception in " << Obj.getFullName() << " thrown: " << e.what());
        }
        catch (Base::Exception& e) {
            e.reportException();
        }
        catch (const std::exception& e) {
            FC_ERR("C++ exception in " << Obj.getFullName() << " thrown " << e.what());
        }
        catch (...) {
            FC_ERR("Cannot update representation for " << Obj.getFullName());
        }

        handleChildren3D(viewProvider);

        if (viewProvider->isDerivedFrom<ViewProviderDocumentObject>()) {
            signalChangedObject(static_cast<ViewProviderDocumentObject&>(*viewProvider), Prop);
        }
    }

    // a property of an object has changed
    if (!Prop.testStatus(App::Property::NoModify)) {
        FC_LOG(Prop.getFullName() << " modified");
        setModified(true);
    }

    getMainWindow()->updateActions(true);
}

void Document::slotRelabelObject(const App::DocumentObject& Obj)
{
    if (d->_pcDocument->isCooperativeMutationActive()) {
        const long objectId = Obj.getID();
        if (!d->deferredNewViewProviders.contains(objectId)
            && d->deferredRelabels.insert(objectId).second) {
            d->deferredRelabelOrder.push_back(objectId);
        }
        d->deferredActionUpdate = true;
        return;
    }

    ViewProvider* viewProvider = getViewProvider(&Obj);
    if (viewProvider && viewProvider->isDerivedFrom<ViewProviderDocumentObject>()) {
        signalRelabelObject(*(static_cast<ViewProviderDocumentObject*>(viewProvider)));
    }
}

void Document::slotTransactionAppend(const App::DocumentObject& obj, App::Transaction* transaction)
{
    ViewProvider* viewProvider = getViewProvider(&obj);
    if (viewProvider && viewProvider->isDerivedFrom<ViewProviderDocumentObject>()) {
        transaction->addObjectDel(viewProvider);
    }
}

void Document::slotTransactionRemove(const App::DocumentObject& obj, App::Transaction* transaction)
{
    const long objectId = obj.getID();
    d->deferredNewViewProviders.erase(objectId);
    d->deferredViewUpdates.erase(objectId);
    d->deferredRelabels.erase(objectId);
    if (d->deferredActivatedObjectId == objectId) {
        d->deferredActivatedObjectId = 0;
    }

    std::map<const App::DocumentObject*, ViewProviderDocumentObject*>::const_iterator it
        = d->_ViewProviderMap.find(&obj);
    if (it != d->_ViewProviderMap.end()) {
        ViewProvider* viewProvider = it->second;

        auto itC = d->_CoinMap.find(viewProvider->getRoot());
        if (itC != d->_CoinMap.end()) {
            d->_CoinMap.erase(itC);
        }

        d->_ViewProviderMap.erase(&obj);
        // transaction being a nullptr indicates that undo/redo is off and the object
        // can be safely deleted
        if (transaction) {
            transaction->addObjectNew(viewProvider);
        }
        else {
            delete viewProvider;
        }
    }
}

void Document::slotActivatedObject(const App::DocumentObject& Obj)
{
    if (d->_pcDocument->isCooperativeMutationActive()) {
        d->deferredActivatedObjectId = Obj.getID();
        d->deferredActionUpdate = true;
        return;
    }

    ViewProvider* viewProvider = getViewProvider(&Obj);
    if (viewProvider && viewProvider->isDerivedFrom<ViewProviderDocumentObject>()) {
        signalActivatedObject(*(static_cast<ViewProviderDocumentObject*>(viewProvider)));
    }
}

void Document::slotUndoDocument(const App::Document& doc)
{
    if (d->_pcDocument != &doc) {
        return;
    }

    signalUndoDocument(*this);
    getMainWindow()->updateActions();
}

void Document::slotRedoDocument(const App::Document& doc)
{
    if (d->_pcDocument != &doc) {
        return;
    }

    signalRedoDocument(*this);
    getMainWindow()->updateActions();
}

void Document::slotRecomputed(const App::Document& doc)
{
    if (d->_pcDocument != &doc) {
        return;
    }
    getMainWindow()->updateActions();
    TreeWidget::updateStatus();
}

// This function is called when some asks to recompute a document that is marked
// as 'SkipRecompute'. We'll check if we are the current document, and if either
// not given an explicit recomputing object list, or the given single object is
// the eidting object or the active object. If the conditions are met, we'll
// force recompute only that object and all its dependent objects.
void Document::slotSkipRecompute(const App::Document& doc, const std::vector<App::DocumentObject*>& objs)
{
    if (d->_pcDocument != &doc) {
        return;
    }
    if (objs.size() > 1 || App::GetApplication().getActiveDocument() != &doc
        || !doc.testStatus(App::Document::AllowPartialRecompute)) {
        return;
    }
    App::DocumentObject* obj = nullptr;

    if (Gui::Application::Instance->isInEdit(this)) {
        auto vp = freecad_cast<ViewProviderDocumentObject*>(getInEdit());
        if (vp) {
            obj = vp->getObject();
        }
    }
    if (objs.size() > 1 || App::GetApplication().getActiveDocument() != &doc
        || !doc.testStatus(App::Document::AllowPartialRecompute)) {
        return;
    }

    auto editDoc = Application::Instance->editDocument();
    if (editDoc) {
        auto vp = freecad_cast<ViewProviderDocumentObject*>(editDoc->getInEdit());
        if (vp) {
            obj = vp->getObject();
        }
    }
    if (!obj) {
        obj = doc.getActiveObject();
    }
    if (!obj || !obj->isAttachedToDocument() || (!objs.empty() && objs.front() != obj)) {
        return;
    }
    obj->recomputeFeature(true);
}

void Document::slotTouchedObject(const App::DocumentObject& Obj)
{
    FC_LOG(Obj.getFullName() << " touched");
    setModified(true);
    if (d->_pcDocument->isCooperativeMutationActive()) {
        d->deferredActionUpdate = true;
        return;
    }
    getMainWindow()->updateActions(true);
}

void Document::addViewProvider(Gui::ViewProviderDocumentObject* vp)
{
    // Hint: The undo/redo first adds the view provider to the Gui
    // document before adding the objects to the App document.

    // the view provider is added by TransactionViewProvider and an
    // object can be there only once
    assert(d->_ViewProviderMap.find(vp->getObject()) == d->_ViewProviderMap.end());
    vp->setStatus(Detach, false);
    d->_ViewProviderMap[vp->getObject()] = vp;
    d->_CoinMap[vp->getRoot()] = vp;
}

void Document::setModified(bool b)
{
    if (b) {
        ++d->modificationGeneration;
    }
    if (d->_isModified == b) {
        return;
    }
    d->_isModified = b;

    std::list<MDIView*> mdis = getMDIViews();
    for (auto& mdi : mdis) {
        mdi->setWindowModified(b);
    }
}

bool Document::isModified() const
{
    return d->_isModified;
}

void Document::clearModifiedAfterSave()
{
    if (d->savedModificationGeneration == d->modificationGeneration) {
        setModified(false);
    }
}
void Document::setWorkbench(const std::string& name)
{
    d->_workbenchName = name;
}
std::string Document::workbench() const
{
    return d->_workbenchName;
}

bool Document::isAboutToClose() const
{
    return d->_isClosing;
}

ViewProviderDocumentObject* Document::getViewProviderByPathFromTail(SoPath* path) const
{
    // Get the lowest root node in the pick path!
    for (int i = 0; i < path->getLength(); i++) {
        SoNode* node = path->getNodeFromTail(i);
        if (node->isOfType(SoSeparator::getClassTypeId())) {
            auto it = d->_CoinMap.find(static_cast<SoSeparator*>(node));
            if (it != d->_CoinMap.end()) {
                return it->second;
            }
        }
    }

    return nullptr;
}

ViewProviderDocumentObject* Document::getViewProviderByPathFromHead(SoPath* path) const
{
    for (int i = 0; i < path->getLength(); i++) {
        SoNode* node = path->getNode(i);
        if (node->isOfType(SoSeparator::getClassTypeId())) {
            auto it = d->_CoinMap.find(static_cast<SoSeparator*>(node));
            if (it != d->_CoinMap.end()) {
                return it->second;
            }
        }
    }

    return nullptr;
}

ViewProviderDocumentObject* Document::getViewProvider(SoNode* node) const
{
    if (!node || !node->isOfType(SoSeparator::getClassTypeId())) {
        return nullptr;
    }
    auto it = d->_CoinMap.find(static_cast<SoSeparator*>(node));
    if (it != d->_CoinMap.end()) {
        return it->second;
    }
    return nullptr;
}

std::vector<std::pair<ViewProviderDocumentObject*, int>> Document::getViewProvidersByPath(
    SoPath* path
) const
{
    std::vector<std::pair<ViewProviderDocumentObject*, int>> ret;
    for (int i = 0; i < path->getLength(); i++) {
        SoNode* node = path->getNodeFromTail(i);
        if (node->isOfType(SoSeparator::getClassTypeId())) {
            auto it = d->_CoinMap.find(static_cast<SoSeparator*>(node));
            if (it != d->_CoinMap.end()) {
                ret.emplace_back(it->second, i);
            }
        }
    }
    return ret;
}

App::Document* Document::getDocument() const
{
    return d->_pcDocument;
}
void Document::setIsActive(bool active)
{
    d->_isActive = active;
    if (d->_editViewProvider) {
        d->_editViewProvider->setActive(active);
    }
}
bool Document::isActive() const
{
    return d->_isActive;
}

static bool checkCanonicalPath(const std::map<App::Document*, bool>& docs)
{
    std::map<QString, std::vector<App::Document*>> paths;
    bool warn = false;
    for (auto doc : App::GetApplication().getDocuments()) {
        QFileInfo info(QString::fromUtf8(doc->FileName.getValue()));
        auto& d = paths[info.canonicalFilePath()];
        d.push_back(doc);
        if (!warn && d.size() > 1) {
            if (docs.contains(d.front()) || docs.contains(d.back())) {
                warn = true;
            }
        }
    }
    if (!warn) {
        return true;
    }
    QString msg;
    QTextStream ts(&msg);
    ts << QObject::tr("Identical physical path detected. It may cause unwanted overwrite of existing document!\n\n")
       << QObject::tr("Are you sure you want to continue?");

    auto docName = [](App::Document* doc) -> QString {
        if (doc->Label.getStrValue() == doc->getName()) {
            return QString::fromUtf8(doc->getName());
        }
        return QStringLiteral("%1 (%2)").arg(
            QString::fromUtf8(doc->Label.getValue()),
            QString::fromUtf8(doc->getName())
        );
    };
    int count = 0;
    for (auto& v : paths) {
        if (v.second.size() <= 1) {
            continue;
        }
        for (auto doc : v.second) {
            if (docs.contains(doc)) {
                FC_WARN("Physical path: " << v.first.toUtf8().constData());
                for (auto d : v.second) {
                    FC_WARN(
                        "  Document: " << docName(d).toUtf8().constData() << ": "
                                       << d->FileName.getValue()
                    );
                }
                if (count == 3) {
                    ts << "\n\n" << QObject::tr("Check report view for more…");
                }
                else if (count < 3) {
                    ts << "\n\n"
                       << QObject::tr("Physical path:") << ' ' << v.first << "\n"
                       << QObject::tr("Document:") << ' ' << docName(doc) << "\n  "
                       << QObject::tr("Path:") << ' ' << QString::fromUtf8(doc->FileName.getValue());
                    for (auto d : v.second) {
                        if (d == doc) {
                            continue;
                        }
                        ts << "\n"
                           << QObject::tr("Document:") << ' ' << docName(d) << "\n  "
                           << QObject::tr("Path:") << ' '
                           << QString::fromUtf8(d->FileName.getValue());
                    }
                }
                ++count;
                break;
            }
        }
    }
    int ret = QMessageBox::warning(
        getMainWindow(),
        QObject::tr("Identical physical path"),
        msg,
        QMessageBox::Yes,
        QMessageBox::No
    );
    return ret == QMessageBox::Yes;
}

namespace
{
enum class SaveAction
{
    Save,
    SaveAs,
    Copy
};

QString chooseDocumentSaveFilename(const App::Document& document, bool useLabel)
{
    const auto applicationName = qApp->applicationName();
    QString name = QString::fromUtf8(document.FileName.getValue());
    if (name.isEmpty() && useLabel) {
        name = QString::fromUtf8(document.Label.getValue());
    }
    if (name.endsWith(QStringLiteral(".FCBak"), Qt::CaseInsensitive)) {
        name.chop(QStringLiteral(".FCBak").size());
        if (!name.endsWith(QStringLiteral(".FCStd"), Qt::CaseInsensitive)) {
            name += QStringLiteral(".FCStd");
        }
    }
    return FileDialog::getSaveFileName(
        getMainWindow(),
        QObject::tr("Save %1 Document").arg(applicationName),
        name,
        FileDialog::FilterList {{QObject::tr("%1 document").arg(applicationName), {"*.FCStd"}}}
    );
}

struct SaveTarget
{
    App::Document* document;
    std::string name;
    bool neededRecompute;
    SaveAction action;
    std::string filename;

    SaveTarget(
        App::Document* document,
        bool neededRecompute,
        SaveAction action = SaveAction::Save,
        std::string filename = {}
    )
        : document(document)
        , name(document->getName())
        , neededRecompute(neededRecompute)
        , action(action)
        , filename(std::move(filename))
    {}
};

struct PendingSave
{
    std::vector<SaveTarget> targets;
    std::size_t leased = 0;
    std::future<bool> result;

    void release()
    {
        // Release on the owner, before invoking any user completion callback.
        // Observers may close a document at the idle notification, so do not
        // dereference it again after releasing its lease.
        std::exception_ptr failure;
        while (leased) {
            const auto& target = targets[--leased];
            if (App::GetApplication().getDocument(target.name.c_str()) == target.document) {
                try {
                    target.document->endCooperativeMutation();
                }
                catch (...) {
                    if (!failure) {
                        failure = std::current_exception();
                    }
                }
            }
        }
        if (failure) {
            std::rethrow_exception(failure);
        }
    }
};

App::HostWorkflow<bool> saveDocuments(std::shared_ptr<PendingSave> pending)
{
    for (const auto& target : pending->targets) {
        if (App::GetApplication().getDocument(target.name.c_str()) != target.document) {
            throw Base::RuntimeError("The document selected for saving no longer exists");
        }
        const auto method = target.action == SaveAction::Copy ? "saveCopy"
            : target.action == SaveAction::SaveAs             ? "saveAs"
                                                              : "save";
        const auto escaped = Base::Tools::escapeEncodeFilename(
            Base::Tools::escapedUnicodeFromUtf8(target.filename.c_str()));
        const auto argument = target.filename.empty() ? std::string {} : "u\"" + escaped + "\"";
        Application::Instance->macroManager()->addLine(
            MacroManager::App,
            ("App.getDocument(\"" + target.name + "\")." + method + "(" + argument + ")").c_str()
        );

        co_yield App::HostWorkflowStep {
            App::HostRuntime::Lane::Document,
            [&](std::stop_token stop) {
                Base::SequencerLauncher progress("Saving document...", 1);
                // External document saves can dirty their dependants. Retain the
                // existing save-order recompute policy, but execute it on a worker.
                if (!target.neededRecompute && target.document->mustExecute()) {
                    App::AutoTransaction transaction(target.document, "Recompute");
                    bool failed = false;
                    target.document->recomputeCancellable({}, false, &failed, 0, stop);
                    if (failed) {
                        throw Base::RuntimeError("Cannot save: dependent document recompute failed");
                    }
                }
                const bool saved = target.action == SaveAction::Copy
                    ? target.document->saveCopy(target.filename.c_str())
                    : target.action == SaveAction::SaveAs
                    ? target.document->saveAs(target.filename.c_str())
                    : target.document->save();
                if (!saved) {
                    throw Base::RuntimeError("Document save did not complete");
                }
                progress.next(false);
            }
        };

        if (target.action != SaveAction::Copy) {
            if (auto* gui = Application::Instance->getDocument(target.document)) {
                gui->clearModifiedAfterSave();
            }
            if (target.action == SaveAction::SaveAs) {
                getMainWindow()->appendRecentFile(
                    QString::fromUtf8(target.document->FileName.getValue())
                );
            }
        }
    }
    co_return true;
}

bool queueDocumentSave(std::vector<SaveTarget> targets, Document::SaveCallback finished)
{
    if (targets.empty()) {
        return false;
    }
    for (const auto& target : targets) {
        if (App::GetApplication().getDocument(target.name.c_str()) != target.document
            || Document::historyMutationBlocked(target.document)) {
            getMainWindow()->showMessage(QObject::tr("Cannot save while document work is active"));
            return false;
        }
    }
    auto pending = std::make_shared<PendingSave>();
    pending->targets = std::move(targets);
    try {
        for (const auto& target : pending->targets) {
            ++pending->leased;
            target.document->beginCooperativeMutation();
        }
        getMainWindow()->showMessage(QObject::tr("Saving document…"));
        pending->result = saveDocuments(pending).runAsyncWithCleanup(
            App::GetApplication().hostRuntime(),
            [](std::function<void()> resume) {
                if (!dispatchToGuiFrame(std::move(resume))) {
                    throw Base::RuntimeError("Unable to dispatch document save completion");
                }
            },
            [](std::function<void()> cleanup) {
                if (!dispatchToGuiCleanup(std::move(cleanup))) {
                    throw Base::RuntimeError("Document save cleanup requested after runtime shutdown");
                }
            },
            [pending, finished = std::move(finished)] {
                bool success = false;
                std::string error;
                try {
                    success = pending->result.get();
                }
                catch (const Base::Exception& failure) {
                    error = failure.what();
                }
                catch (const std::exception& failure) {
                    error = failure.what();
                }
                catch (...) {
                    error = "Unknown document save failure";
                }
                try {
                    pending->release();
                }
                catch (const std::exception& failure) {
                    success = false;
                    error = failure.what();
                }
                catch (...) {
                    success = false;
                    error = "Document save cleanup failed";
                }
                if (success) {
                    getMainWindow()->showMessage(QObject::tr("Document saved"), 3000);
                }
                else {
                    Base::Console().error("Document save failed: %s\n", error.c_str());
                    getMainWindow()->showMessage(
                        QObject::tr("Document save failed: %1").arg(QString::fromStdString(error))
                    );
                }
                if (finished) {
                    try {
                        finished(success);
                    }
                    catch (...) {
                        Base::Console().error("Document save completion callback failed\n");
                    }
                }
                else if (!success) {
                    QMessageBox::critical(
                        getMainWindow(),
                        QObject::tr("Saving document failed"),
                        QString::fromStdString(error)
                    );
                }
            }
        );
    }
    catch (...) {
        pending->release();
        throw;
    }
    return true;
}
}  // namespace

bool Document::askIfSavingFailed(const QString& error)
{
    int ret = QMessageBox::question(
        getMainWindow(),
        QObject::tr("Could not save document"),
        QObject::tr("There was an issue trying to save the file. "
                    "This may be because some of the parent folders do not exist, "
                    "or you do not have sufficient permissions, "
                    "or for other reasons. Error details:\n\n\"%1\"\n\n"
                    "Would you like to save the file with a different name?")
            .arg(error),
        QMessageBox::Yes,
        QMessageBox::No
    );

    if (ret == QMessageBox::No) {
        // TODO: Understand what exactly is supposed to be returned here
        getMainWindow()->showMessage(QObject::tr("Saving aborted"), 2000);
        return false;
    }
    else if (ret == QMessageBox::Yes) {
        return saveAs();
    }

    return false;
}

bool Document::warnIfOlderVersion(bool asynchronous, SaveCallback finished, bool* queuedSaveAs)
{
    // Skip warning if no GUI (headless/scripted mode)
    if (!getMainWindow()) {
        return true;
    }

    // Check if version checking is disabled in preferences
    if (App::GetApplication()
            .GetParameterGroupByPath("User parameter:BaseApp/Preferences/Document")
            ->GetBool("DisableVersionCheckOnSave", false)) {
        return true;
    }

    // Get document version info
    const char* docVersion = d->_pcDocument->getProgramVersion();
    const bool hasVersionString = !Base::Tools::isNullOrEmpty(docVersion);

    // Parse document version string like "1.0R39319 (Git)" or "0.21R33694 (Git)"
    // hasVersion is true only if the string is present AND parses as major.minor.
    // Unrecognised strings like "pre-0.14" still display in the dialog but cannot
    // be compared numerically, so they are treated as older versions.
    int docMajor = 0, docMinor = 0;
    const bool hasVersion = hasVersionString
        && std::sscanf(docVersion, "%d.%d", &docMajor, &docMinor) == 2;

    // Get current FreeCAD version
    auto config = App::Application::Config();
    int currentMajor = 0, currentMinor = 0;
    if (config.count("BuildVersionMajor") && config.count("BuildVersionMinor")) {
        currentMajor = std::stoi(config["BuildVersionMajor"]);
        currentMinor = std::stoi(config["BuildVersionMinor"]);
    }
    else {
        return true;
    }

    // Warn if the document was created with an older version or has no version info
    if (!hasVersion || (docMajor < currentMajor)
        || (docMajor == currentMajor && docMinor < currentMinor)) {
        auto msgBox = std::make_unique<QMessageBox>(getMainWindow());
        msgBox->setObjectName(QStringLiteral("confirmVersionUpgrade"));
        msgBox->setWindowTitle(QObject::tr("File Created with Older FreeCAD Version"));
        msgBox->setIcon(QMessageBox::Warning);
        msgBox->setText(
            QObject::tr(
                "This file was created with %1, but you are using v%2.%3.\n\n"
                "Saving will upgrade the file format. The file may not be readable "
                "by older versions of FreeCAD after saving.\n\n"
                "Use 'Save As…' to preserve the original file."
                "\n"
            )
                .arg(
                    !hasVersionString
                        ? QObject::tr("an unknown older version of FreeCAD")
                        : QObject::tr("FreeCAD version %1").arg(QString::fromUtf8(docVersion))
                )
                .arg(currentMajor)
                .arg(currentMinor)
        );
        QPushButton* saveButton = msgBox->addButton(QObject::tr("Save"), QMessageBox::AcceptRole);
        QPushButton* saveAsButton = msgBox->addButton(QObject::tr("Save As…"), QMessageBox::ActionRole);
        msgBox->addButton(QMessageBox::Cancel);
        msgBox->setDefaultButton(QMessageBox::Cancel);

        auto* dontShowCheckBox = new QCheckBox(QObject::tr("Do not show this warning again"), msgBox.get());
        msgBox->setCheckBox(dontShowCheckBox);

        if (asynchronous) {
            // A prompt is preparation, not a running save. Do not nest the Qt
            // event loop or retain a document pointer while the user decides.
            const std::string name = getDocument()->getName();
            const std::string filename = getDocument()->FileName.getStrValue();
            const int identity = d->_iDocId;
            auto completion = std::make_shared<SaveCallback>(std::move(finished));
            auto* prompt = msgBox.release();
            prompt->setAttribute(Qt::WA_DeleteOnClose);
            QObject::connect(prompt, &QObject::destroyed, getMainWindow(), [completion] {
                if (auto callback = std::exchange(*completion, {})) { callback(false); }
            });
            QObject::connect(prompt, &QDialog::finished, getMainWindow(),
                [prompt, saveButton, saveAsButton, dontShowCheckBox, name, filename,
                 identity, completion](int) {
                    auto callback = std::exchange(*completion, {});
                    auto* app = App::GetApplication().getDocument(name.c_str());
                    auto* gui = app ? Application::Instance->getDocument(app) : nullptr;
                    if (!gui || gui->d->_iDocId != identity
                        || app->FileName.getStrValue() != filename) {
                        if (callback) { callback(false); }
                        return;
                    }
                    bool queued = false;
                    try {
                        if (prompt->clickedButton() == saveButton) {
                            if (dontShowCheckBox->isChecked()) {
                                App::GetApplication().GetParameterGroupByPath(
                                    "User parameter:BaseApp/Preferences/Document")
                                    ->SetBool("DisableVersionCheckOnSave", true);
                            }
                            queued = gui->saveImpl(true, callback, true);
                        }
                        else if (prompt->clickedButton() == saveAsButton) {
                            queued = gui->saveAsImpl(true, callback);
                        }
                    }
                    catch (const Base::Exception& error) {
                        error.reportException();
                    }
                    if (!queued && callback) { callback(false); }
                });
            prompt->open();
            if (queuedSaveAs) { *queuedSaveAs = true; }
            return false;
        }

        int ret = msgBox->exec();

        if (msgBox->clickedButton() == saveButton) {
            if (dontShowCheckBox->isChecked()) {
                App::GetApplication()
                    .GetParameterGroupByPath("User parameter:BaseApp/Preferences/Document")
                    ->SetBool("DisableVersionCheckOnSave", true);
            }
        }
        else if (msgBox->clickedButton() == saveAsButton) {
            const bool accepted = saveAsImpl(asynchronous, std::move(finished));
            if (asynchronous && queuedSaveAs) { *queuedSaveAs = accepted; }
            return false;
        }

        if (ret == QMessageBox::Cancel) {
            return false;
        }
    }
    return true;
}

/// Save the document
bool Document::save()
{
    return saveImpl(false);
}

bool Document::saveAsync(SaveCallback finished)
{
    return saveImpl(true, std::move(finished));
}

bool Document::saveImpl(bool asynchronous, SaveCallback finished, bool versionApproved)
{
    if (d->_pcDocument->isSaved()) {
        // Warn if this document was created with an older FreeCAD version
        bool queuedSaveAs = false;
        if (!versionApproved && !warnIfOlderVersion(asynchronous, finished, &queuedSaveAs)) {
            return queuedSaveAs;
        }

        try {
            std::vector<App::Document*> docs;
            std::map<App::Document*, bool> dmap;
            try {
                docs = getDocument()->getDependentDocuments();
                for (auto it = docs.begin(); it != docs.end();) {
                    App::Document* doc = *it;
                    if (doc == getDocument()) {
                        dmap[doc] = doc->mustExecute();
                        ++it;
                        continue;
                    }
                    auto gdoc = Application::Instance->getDocument(doc);
                    if ((gdoc && !gdoc->isModified()) || doc->testStatus(App::Document::PartialDoc)
                        || doc->testStatus(App::Document::TempDoc)) {
                        it = docs.erase(it);
                        continue;
                    }
                    dmap[doc] = doc->mustExecute();
                    ++it;
                }
            }
            catch (const Base::RuntimeError& e) {
                FC_ERR(e.what());
                docs = {getDocument()};
                dmap.clear();
                dmap[getDocument()] = getDocument()->mustExecute();
            }

            if (docs.size() > 1) {
                int ret = QMessageBox::question(
                    getMainWindow(),
                    QObject::tr("Save dependent files"),
                    QObject::tr("The file contains external dependencies. "
                                "Do you want to save the dependent files, too?"),
                    QMessageBox::Yes,
                    QMessageBox::No
                );

                if (ret != QMessageBox::Yes) {
                    docs = {getDocument()};
                    dmap.clear();
                    dmap[getDocument()] = getDocument()->mustExecute();
                }
            }

            if (!checkCanonicalPath(dmap)) {
                return false;
            }

            if (asynchronous) {
                std::vector<SaveTarget> targets;
                for (auto* doc : docs) {
                    if (!doc->isSaved()) {
                        const auto filename = chooseDocumentSaveFilename(*doc, true);
                        if (filename.isEmpty()) {
                            return false;
                        }
                        targets.emplace_back(
                            doc,
                            dmap.at(doc),
                            SaveAction::SaveAs,
                            filename.toUtf8().toStdString()
                        );
                    }
                    else {
                        targets.emplace_back(doc, dmap.at(doc));
                    }
                }
                return queueDocumentSave(std::move(targets), std::move(finished));
            }

            Gui::WaitCursor wc;
            // save all documents
            for (auto doc : docs) {
                // Changed 'mustExecute' status may be triggered by saving external document
                if (!dmap[doc] && doc->mustExecute()) {
                    App::AutoTransaction trans(doc, "Recompute");
                    Command::doCommand(
                        Command::Doc,
                        "App.getDocument(\"%s\").recompute()",
                        doc->getName()
                    );
                }

                Command::doCommand(Command::Doc, "App.getDocument(\"%s\").save()", doc->getName());
                auto gdoc = Application::Instance->getDocument(doc);
                if (gdoc) {
                    gdoc->setModified(false);
                }
            }
        }
        catch (const Base::FileException& e) {
            e.reportException();
            return askIfSavingFailed(QString::fromUtf8(e.what()));
        }
        catch (const Base::Exception& e) {
            QMessageBox::critical(
                getMainWindow(),
                QObject::tr("Saving document failed"),
                QString::fromLatin1(e.what())
            );
            return false;
        }
        return true;
    }
    else {
        return saveAsImpl(asynchronous, std::move(finished));
    }
}

/// Save the document under a new file name
bool Document::saveAs()
{
    return saveAsImpl(false);
}

bool Document::saveAsAsync(SaveCallback finished)
{
    return saveAsImpl(true, std::move(finished));
}

bool Document::saveAsImpl(bool asynchronous, SaveCallback finished)
{
    getMainWindow()->showMessage(QObject::tr("Save document under new filename…"));

    const QString fn = chooseDocumentSaveFilename(*getDocument(), true);

    if (!fn.isEmpty()) {
        QFileInfo fi;
        fi.setFile(fn);

        if (asynchronous) {
            return queueDocumentSave({SaveTarget(getDocument(), getDocument()->mustExecute(),
                SaveAction::SaveAs, fn.toUtf8().toStdString())}, std::move(finished));
        }

        const char* DocName = App::GetApplication().getDocumentName(getDocument());

        // save as new file name
        try {
            Gui::WaitCursor wc;
            std::string escapedstr = Base::Tools::escapedUnicodeFromUtf8(fn.toUtf8());
            escapedstr = Base::Tools::escapeEncodeFilename(escapedstr);
            Command::doCommand(
                Command::Doc,
                "App.getDocument(\"%s\").saveAs(u\"%s\")",
                DocName,
                escapedstr.c_str()
            );
            // App::Document::saveAs() may modify the passed file name
            fi.setFile(QString::fromUtf8(d->_pcDocument->FileName.getValue()));
            setModified(false);
            getMainWindow()->appendRecentFile(fi.filePath());
        }
        catch (const Base::FileException& e) {
            e.reportException();
            return askIfSavingFailed(QString::fromUtf8(e.what()));
        }
        catch (const Base::Exception& e) {
            QMessageBox::critical(
                getMainWindow(),
                QObject::tr("Saving document failed"),
                QString::fromLatin1(e.what())
            );
        }
        return true;
    }
    else {
        getMainWindow()->showMessage(QObject::tr("Saving aborted"), 2000);
        return false;
    }
}

void Document::saveAll()
{
    saveAllImpl(false);
}

void Document::saveAllAsync()
{
    saveAllImpl(true);
}

void Document::saveAllImpl(bool asynchronous)
{
    std::vector<App::Document*> docs;
    try {
        docs = App::Document::getDependentDocuments(App::GetApplication().getDocuments(), true);
    }
    catch (Base::Exception& e) {
        e.reportException();
        int ret = QMessageBox::critical(
            getMainWindow(),
            QObject::tr("Failed to save document"),
            QObject::tr("Documents contains cyclic dependencies. Do you still want to save them?"),
            QMessageBox::Yes,
            QMessageBox::No
        );
        if (ret != QMessageBox::Yes) {
            return;
        }
        docs = App::GetApplication().getDocuments();
    }

    std::map<App::Document*, bool> dmap;
    for (auto doc : docs) {
        if (doc->testStatus(App::Document::PartialDoc) || doc->testStatus(App::Document::TempDoc)) {
            continue;
        }
        dmap[doc] = doc->mustExecute();
    }

    if (!checkCanonicalPath(dmap)) {
        return;
    }

    if (asynchronous) {
        std::vector<SaveTarget> targets;
        for (auto* doc : docs) {
            if (!dmap.contains(doc) || !Application::Instance->getDocument(doc)) {
                continue;
            }
            if (!doc->isSaved()) {
                const auto filename = chooseDocumentSaveFilename(*doc, true);
                if (filename.isEmpty()) { return; }
                targets.emplace_back(doc, dmap.at(doc), SaveAction::SaveAs,
                    filename.toUtf8().toStdString());
            }
            else {
                targets.emplace_back(doc, dmap.at(doc));
            }
        }
        queueDocumentSave(std::move(targets), {});
        return;
    }

    for (auto doc : docs) {
        if (doc->testStatus(App::Document::PartialDoc) || doc->testStatus(App::Document::TempDoc)) {
            continue;
        }
        auto gdoc = Application::Instance->getDocument(doc);
        if (!gdoc) {
            continue;
        }
        if (!doc->isSaved()) {
            if (!gdoc->saveAs()) {
                break;
            }
        }
        Gui::WaitCursor wc;

        try {
            // Changed 'mustExecute' status may be triggered by saving external document
            if (!dmap[doc] && doc->mustExecute()) {
                App::AutoTransaction trans(doc, "Recompute");
                Command::doCommand(Command::Doc, "App.getDocument('%s').recompute()", doc->getName());
            }
            Command::doCommand(Command::Doc, "App.getDocument('%s').save()", doc->getName());
            gdoc->setModified(false);
        }
        catch (const Base::Exception& e) {
            QMessageBox::critical(
                getMainWindow(),
                QObject::tr("Failed to save document")
                    + QStringLiteral(": %1").arg(QString::fromUtf8(doc->getName())),
                QString::fromLatin1(e.what())
            );
            break;
        }
    }
}

/// Save a copy of the document under a new file name
bool Document::saveCopy()
{
    return saveCopyImpl(false);
}

bool Document::saveCopyAsync(SaveCallback finished)
{
    return saveCopyImpl(true, std::move(finished));
}

bool Document::saveCopyImpl(bool asynchronous, SaveCallback finished)
{
    getMainWindow()->showMessage(QObject::tr("Save a copy of the document under new filename…"));

    const QString fn = chooseDocumentSaveFilename(*getDocument(), false);
    if (!fn.isEmpty()) {
        if (asynchronous) {
            return queueDocumentSave({SaveTarget(getDocument(), true,
                SaveAction::Copy, fn.toUtf8().toStdString())}, std::move(finished));
        }
        const char* DocName = App::GetApplication().getDocumentName(getDocument());

        // save as new file name
        Gui::WaitCursor wc;
        std::string pyfn = Base::Tools::escapeEncodeFilename(fn.toUtf8().constData());
        Command::doCommand(
            Command::Doc,
            "App.getDocument(\"%s\").saveCopy(\"%s\")",
            DocName,
            pyfn.c_str()
        );

        return true;
    }
    else {
        getMainWindow()->showMessage(QObject::tr("Saving aborted"), 2000);
        return false;
    }
}

unsigned int Document::getMemSize() const
{
    unsigned int size = 0;

    // size of the view providers in the document

    for (const auto& vp : d->_ViewProviderMap) {
        size += vp.second->getMemSize();
    }
    return size;
}

/**
 * Adds a separate XML file to the projects file that contains information about the view providers.
 */
void Document::Save(Base::Writer& writer) const
{
    // It's only possible to add extra information if force of XML is disabled
    if (!writer.isForceXML()) {
        writer.addFile("GuiDocument.xml", this);

        ParameterGrp::handle hGrp = App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/Document"
        );
        if (hGrp->GetBool("SaveThumbnail", true)) {
            int size = hGrp->GetInt("ThumbnailSize", 256);
            size = Base::clamp<int>(size, 64, 512);
            std::list<MDIView*> mdi = getMDIViews();

            View3DInventorViewer* view = nullptr;
            for (const auto& it : mdi) {
                if (it->isDerivedFrom<View3DInventor>()) {
                    view = static_cast<View3DInventor*>(it)->getViewer();
                    break;
                }
            }

            d->thumb.setFileName(d->_pcDocument->FileName.getValue());
            d->thumb.setSize(size);
            d->thumb.setViewer(view);
            d->thumb.Save(writer);
        }
    }
}

/**
 * Loads a separate XML file from the projects file with information about the view providers.
 */
void Document::Restore(Base::XMLReader& reader)
{
    reader.addFile("GuiDocument.xml", this);

    // hide all elements to avoid to update the 3d view when loading data files
    // RestoreDocFile then restores the visibility status again
    std::map<const App::DocumentObject*, ViewProviderDocumentObject*>::iterator it;
    for (const auto& vp : d->_ViewProviderMap) {
        vp.second->startRestoring();
        vp.second->setStatus(Gui::isRestoring, true);
    }
}

/**
 * Restores the properties of the view providers.
 */
void Document::RestoreDocFile(Base::Reader& reader)
{
    const bool traceRestore = qEnvironmentVariableIsSet("VIBECAD_RESTORE_DETAIL_TRACE");
    const auto restoreStarted = std::chrono::steady_clock::now();
    // We must create an XML parser to read from the input stream
    auto localreader = std::make_shared<GuiRestoreReader>(reader);

    localreader->readElement("Document");
    long scheme = localreader->getAttribute<long>("SchemaVersion");
    localreader->DocumentSchema = scheme;
    restoreOnGui([&] { localreader->ProgramVersion = d->_pcDocument->getProgramVersion(); });

    bool hasExpansion = localreader->hasAttribute("HasExpansion");
    if (hasExpansion) {
        restoreOnGui([&] {
            auto tree = TreeWidget::instance();
            if (tree) {
                auto docItem = tree->getDocumentItem(this);
                if (docItem) {
                    docItem->Restore(*localreader);
                }
            }
        });
    }

    // At this stage all the document objects and their associated view providers exist.
    // Now we must restore the properties of the view providers only.
    //
    // SchemeVersion "1"
    if (scheme == 1) {
        // read the viewproviders itself
        localreader->readElement("ViewProviderData");
        int Cnt = localreader->getAttribute<long>("Count");
        Base::SequencerLauncher restoreViews("Restoring document presentation...", Cnt);
        for (int i = 0; i < Cnt; i++) {
            const auto viewStarted = std::chrono::steady_clock::now();
            restoreViews.next();
            localreader->readElement("ViewProvider");
            std::string name = localreader->getAttribute<const char*>("name");

            bool expanded = false;
            if (!hasExpansion && localreader->hasAttribute("expanded")) {
                const char* attr = localreader->getAttribute<const char*>("expanded");
                if (strcmp(attr, "1") == 0) {
                    expanded = true;
                }
            }

            int treeRank = -1;
            if (localreader->hasAttribute("treeRank")) {
                treeRank = localreader->getAttribute<int>("treeRank");
            }

            restoreOnGui([&] {
                auto pObj = freecad_cast<ViewProviderDocumentObject*>(
                    getViewProviderByName(name.c_str())
                );
                // Resolve and consume the provider only on its owner thread.
                if (pObj) {
                    pObj->Restore(*localreader);
                }

                if (pObj && treeRank >= 0) {
                    pObj->setTreeRank(treeRank);
                }

                if (pObj && expanded) {
                    this->signalExpandObject(*pObj, TreeItemMode::ExpandItem, 0, 0);
                }
            });
            localreader->readEndElement("ViewProvider");

            if (traceRestore) {
                const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::steady_clock::now() - viewStarted
                ).count();
                if (elapsed >= 20) {
                    Base::Console().message(
                        "VIBECAD_RESTORE_DETAIL restore_view index=%d count=%d name=%s "
                        "elapsed_ms=%lld\n",
                        i + 1,
                        Cnt,
                        name.c_str(),
                        static_cast<long long>(elapsed)
                    );
                }
            }

        }
        localreader->readEndElement("ViewProviderData");

        // read camera settings
        localreader->readElement("Camera");
        const std::string settings = localreader->getAttribute<const char*>("settings");
        restoreOnGui([&] {
            cameraSettings.clear();
            if (!settings.empty()) {
                saveCameraSettings(settings.c_str());
                try {
                    for (const auto& it : getMDIViews()) {
                        if (auto* viewCamera = freecad_cast<MDIViewWithCamera*>(it)) {
                            viewCamera->setCamera(cameraSettings.c_str());
                        }
                    }
                }
                catch (const Base::Exception& e) {
                    Base::Console().error("%s\n", e.what());
                }
            }
        });
    }

    reader.initLocalReader(localreader);

    // reset modified flag
    restoreOnGui([&] { setModified(false); });
    if (traceRestore) {
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - restoreStarted
        ).count();
        Base::Console().message(
            "VIBECAD_RESTORE_DETAIL gui_document elapsed_ms=%lld\n",
            static_cast<long long>(elapsed)
        );
    }
}

void Document::slotStartRestoreDocument(const App::Document& doc)
{
    if (d->_pcDocument != &doc) {
        return;
    }
    // disable this signal while loading a document
    d->connectActObjectBlocker.block();
}

void Document::slotFinishRestoreObject(const App::DocumentObject& obj)
{
    auto vpd = freecad_cast<ViewProviderDocumentObject*>(getViewProvider(&obj));
    if (vpd) {
        vpd->setStatus(Gui::isRestoring, false);
        vpd->finishRestoring();
        if (!vpd->canAddToSceneGraph()) {
            toggleInSceneGraph(vpd);
        }
    }
}

void Document::slotFinishRestoreDocument(const App::Document& doc)
{
    if (d->_pcDocument != &doc) {
        return;
    }
    d->connectActObjectBlocker.unblock();
    App::DocumentObject* act = doc.getActiveObject();
    if (act) {
        ViewProvider* viewProvider = getViewProvider(act);
        if (viewProvider && viewProvider->isDerivedFrom<ViewProviderDocumentObject>()) {
            signalActivatedObject(*(static_cast<ViewProviderDocumentObject*>(viewProvider)));
        }
    }

    // reset modified flag
    setModified(doc.testStatus(App::Document::LinkStampChanged));
}

void Document::slotShowHidden(const App::Document& doc)
{
    if (d->_pcDocument != &doc) {
        return;
    }

    Application::Instance->signalShowHidden(*this);
}

/**
 * Saves the properties of the view providers.
 */
void Document::SaveDocFile(Base::Writer& writer) const
{
    // The archive/compressor stays on the caller. Only GUI-owned state capture
    // enters the frame dispatcher; embedded property files inherit that owner.
    std::vector<std::pair<const App::DocumentObject*, ViewProviderDocumentObject*>> providers;
    captureOnGui(writer, [&] {
        writer.Stream() << "<?xml version='1.0' encoding='utf-8'?>" << std::endl
                        << "<!--" << std::endl
                        << " FreeCAD Document, see https://www.freecad.org for more information…"
                        << std::endl
                        << "-->" << std::endl;

        writer.Stream() << "<Document SchemaVersion=\"1\"";

        writer.incInd();

        auto tree = TreeWidget::instance();
        bool hasExpansion = false;
        if (tree) {
            auto docItem = tree->getDocumentItem(this);
            if (docItem) {
                hasExpansion = true;
                writer.Stream() << " HasExpansion=\"1\">" << std::endl;
                docItem->Save(writer);
            }
        }
        if (!hasExpansion) {
            writer.Stream() << ">" << std::endl;
        }

        // writing the view provider names itself
        writer.Stream() << writer.ind() << "<ViewProviderData Count=\""
                        << d->_ViewProviderMap.size() << "\">" << std::endl;
        providers.assign(d->_ViewProviderMap.begin(), d->_ViewProviderMap.end());
    });

    bool xml = writer.isForceXML();
    // writer.setForceXML(true);
    writer.incInd();  // indentation for 'ViewProvider name'
    for (const auto& it : providers) {
        captureOnGui(writer, [&] {
            const App::DocumentObject* doc = it.first;
            ViewProviderDocumentObject* obj = it.second;
            writer.Stream() << writer.ind() << "<ViewProvider name=\"" << doc->getNameInDocument()
                            << "\""
                            << " expanded=\"" << (doc->testStatus(App::Expand) ? 1 : 0) << "\""
                            << " treeRank=\"" << obj->getTreeRank() << "\"";
            if (obj->hasExtensions()) {
                writer.Stream() << " Extensions=\"True\"";
            }

            writer.Stream() << ">" << std::endl;
            obj->Save(writer);
            writer.Stream() << writer.ind() << "</ViewProvider>" << std::endl;
        });
    }
    writer.setForceXML(xml);

    writer.decInd();  // indentation for 'ViewProvider name'
    writer.Stream() << writer.ind() << "</ViewProviderData>" << std::endl;
    writer.decInd();  // indentation for 'ViewProviderData Count'

    // save camera settings
    captureOnGui(writer, [&] {
        for (const auto& it : getMDIViews()) {
            if (auto* viewCamera = freecad_cast<MDIViewWithCamera*>(it)) {
                const std::string& camera = viewCamera->getCamera();
                if (saveCameraSettings(camera.c_str())) {
                    break;
                }
            }
        }

        writer.incInd();  // indentation for camera settings
        writer.Stream() << writer.ind() << "<Camera settings=\""
                        << encodeAttribute(getCameraSettings()) << "\"/>\n";
        writer.decInd();  // indentation for camera settings

        writer.Stream() << "</Document>" << std::endl;
        d->savedModificationGeneration = d->modificationGeneration;
    });
}

void Document::exportObjects(const std::vector<App::DocumentObject*>& obj, Base::Writer& writer)
{
    writer.Stream() << "<?xml version='1.0' encoding='utf-8'?>" << std::endl;
    writer.Stream() << "<Document SchemaVersion=\"1\">" << std::endl;

    std::map<const App::DocumentObject*, ViewProvider*> views;
    for (const auto& it : obj) {
        Document* doc = Application::Instance->getDocument(it->getDocument());
        if (doc) {
            ViewProvider* vp = doc->getViewProvider(it);
            if (vp) {
                views[it] = vp;
            }
        }
    }

    // writing the view provider names itself
    writer.incInd();  // indentation for 'ViewProviderData Count'
    writer.Stream() << writer.ind() << "<ViewProviderData Count=\"" << views.size() << "\">"
                    << std::endl;

    bool xml = writer.isForceXML();
    // writer.setForceXML(true);
    writer.incInd();  // indentation for 'ViewProvider name'
    std::map<const App::DocumentObject*, ViewProvider*>::const_iterator jt;
    for (jt = views.begin(); jt != views.end(); ++jt) {
        const App::DocumentObject* doc = jt->first;
        ViewProvider* vp = jt->second;
        writer.Stream() << writer.ind() << "<ViewProvider name=\"" << doc->getExportName() << "\" "
                        << "expanded=\"" << (doc->testStatus(App::Expand) ? 1 : 0) << "\"";
        if (vp->hasExtensions()) {
            writer.Stream() << " Extensions=\"True\"";
        }

        writer.Stream() << ">" << std::endl;
        vp->Save(writer);
        writer.Stream() << writer.ind() << "</ViewProvider>" << std::endl;
    }
    writer.setForceXML(xml);

    writer.decInd();  // indentation for 'ViewProvider name'
    writer.Stream() << writer.ind() << "</ViewProviderData>" << std::endl;
    writer.decInd();  // indentation for 'ViewProviderData Count'
    writer.incInd();  // indentation for camera settings
    writer.Stream() << writer.ind() << "<Camera settings=\"\"/>" << std::endl;
    writer.decInd();  // indentation for camera settings
    writer.Stream() << "</Document>" << std::endl;
}

void Document::importObjects(
    const std::vector<App::DocumentObject*>& obj,
    Base::Reader& reader,
    const std::map<std::string, std::string>& nameMapping
)
{
    // We must create an XML parser to read from the input stream
    auto localreader = std::make_shared<GuiRestoreReader>(reader);
    localreader->readElement("Document");
    long scheme = localreader->getAttribute<long>("SchemaVersion");

    // At this stage all the document objects and their associated view providers exist.
    // Now we must restore the properties of the view providers only.
    //
    // SchemeVersion "1"
    if (scheme == 1) {
        // read the viewproviders itself
        localreader->readElement("ViewProviderData");
        int Cnt = localreader->getAttribute<long>("Count");
        auto it = obj.begin();
        for (int i = 0; i < Cnt && it != obj.end(); ++i, ++it) {
            // The stored name usually doesn't match with the current name anymore
            // thus we try to match by type. This should work because the order of
            // objects should not have changed
            localreader->readElement("ViewProvider");
            std::string name = localreader->getAttribute<const char*>("name");
            auto jt = nameMapping.find(name);
            if (jt != nameMapping.end()) {
                name = jt->second;
            }
            bool expanded = false;
            if (localreader->hasAttribute("expanded")) {
                const char* attr = localreader->getAttribute<const char*>("expanded");
                if (strcmp(attr, "1") == 0) {
                    expanded = true;
                }
            }
            restoreOnGui([&] {
                Gui::ViewProvider* pObj = this->getViewProviderByName(name.c_str());
                if (pObj) {
                    pObj->setStatus(Gui::isRestoring, true);
                    auto vpd = freecad_cast<ViewProviderDocumentObject*>(pObj);
                    if (vpd) {
                        vpd->startRestoring();
                    }
                    pObj->Restore(*localreader);
                    if (expanded && vpd) {
                        this->signalExpandObject(*vpd, TreeItemMode::ExpandItem, 0, 0);
                    }
                }
            });
            localreader->readEndElement("ViewProvider");
            if (it == obj.end()) {
                break;
            }
        }
        localreader->readEndElement("ViewProviderData");
    }

    localreader->readEndElement("Document");

    // In the file GuiDocument.xml new data files might be added
    if (localreader->hasFilenames()) {
        reader.initLocalReader(localreader);
    }
}

void Document::slotFinishImportObjects(const std::vector<App::DocumentObject*>& objs)
{
    (void)objs;
    // finishRestoring() is now triggered by signalFinishRestoreObject
    //
    // for(auto obj : objs) {
    //     auto vp = getViewProvider(obj);
    //     if(!vp) continue;
    //     vp->setStatus(Gui::isRestoring,false);
    //     auto vpd = freecad_cast<ViewProviderDocumentObject*>(vp);
    //     if(vpd) vpd->finishRestoring();
    // }
}

void Document::addRootObjectsToGroup(
    const std::vector<App::DocumentObject*>& obj,
    App::DocumentObjectGroup* grp
)
{
    std::map<App::DocumentObject*, bool> rootMap;
    for (const auto it : obj) {
        rootMap[it] = true;
    }
    // get the view providers and check which objects are children
    for (const auto it : obj) {
        Gui::ViewProvider* vp = getViewProvider(it);
        if (vp) {
            std::vector<App::DocumentObject*> child = vp->claimChildren();
            for (const auto& jt : child) {
                auto kt = rootMap.find(jt);
                if (kt != rootMap.end()) {
                    kt->second = false;
                }
            }
        }
    }

    // all objects that are not children of other objects can be added to the group
    for (const auto& it : rootMap) {
        if (it.second) {
            grp->addObject(it.first);
        }
    }
}

MDIView* Document::createView(const Base::Type& typeId, CreateViewMode mode)
{
    if (!typeId.isDerivedFrom(MDIView::getClassTypeId())) {
        return nullptr;
    }

    std::list<MDIView*> theViews = this->getMDIViewsOfType(typeId);
    if (typeId == View3DInventor::getClassTypeId()) {

        QOpenGLWidget* shareWidget = nullptr;
        // VBO rendering doesn't work correctly when we don't share the OpenGL widgets
        if (!theViews.empty()) {
            auto firstView = static_cast<View3DInventor*>(theViews.front());
            shareWidget = qobject_cast<QOpenGLWidget*>(firstView->getViewer()->getGLWidget());

            const std::string& camera = firstView->getCamera();
            saveCameraSettings(camera.c_str());
        }

        auto view3D = new View3DInventor(this, getMainWindow(), shareWidget);
        if (!theViews.empty()) {
            auto firstView = static_cast<View3DInventor*>(theViews.front());
            std::string overrideMode = firstView->getViewer()->getOverrideMode();
            view3D->getViewer()->setOverrideMode(overrideMode);
        }

        // attach the viewproviders. we need to make sure that we only attach the toplevel ones
        // and not viewproviders which are claimed by other providers. To ensure this we first
        // add all providers and then remove the ones already claimed

        std::vector<App::DocumentObject*> child_vps;
        for (const auto& vp : d->_ViewProviderMap) {
            view3D->getViewer()->addViewProvider(vp.second);
            std::vector<App::DocumentObject*> children = vp.second->claimChildren3D();
            child_vps.insert(child_vps.end(), children.begin(), children.end());
        }

        for (const auto& va : d->_ViewProviderMapAnnotation) {
            view3D->getViewer()->addViewProvider(va.second);
            std::vector<App::DocumentObject*> children = va.second->claimChildren3D();
            child_vps.insert(child_vps.end(), children.begin(), children.end());
        }

        for (App::DocumentObject* obj : child_vps) {
            view3D->getViewer()->removeViewProvider(getViewProvider(obj));
        }

        // When cloning the view, don't increment the window counter as the old view will be deleted
        // shortly after.
        if (mode != CreateViewMode::Clone) {
            const char* name = getDocument()->Label.getValue();
            QString title
                = QStringLiteral("%1 : %2[*]").arg(QString::fromUtf8(name)).arg(d->_iWinCount++);

            view3D->setWindowTitle(title);
        }

        view3D->setWindowModified(this->isModified());
        view3D->resize(400, 300);

        if (!cameraSettings.empty()) {
            view3D->setCamera(cameraSettings.c_str());
        }

        // When cloning the view, don't add the view to the main window. The whole purpose of the
        // workaround using cloned views is that the view can be shown in undocked/fullscreen mode
        // without having been docked before.
        if (mode != CreateViewMode::Clone) {
            getMainWindow()->addWindow(view3D);
        }

        view3D->getViewer()->redraw();
        return view3D;
    }
    return nullptr;
}

const char* Document::getCameraSettings() const
{
    return cameraSettings.c_str();
}

bool Document::saveCameraSettings(const char* settings) const
{
    if (!settings) {
        return false;
    }

    // skip starting comment lines
    bool skipping = false;
    char c = *settings;
    for (; c; c = *(++settings)) {
        if (skipping) {
            if (c == '\n') {
                skipping = false;
            }
        }
        else if (c == '#') {
            skipping = true;
        }
        else if (!std::isspace(c)) {
            break;
        }
    }

    if (!c) {
        return false;
    }

    cameraSettings = settings;
    return true;
}

void Document::attachView(Gui::BaseView* pcView, bool bPassiv)
{
    if (!bPassiv) {
        d->baseViews.push_back(pcView);
    }
    else {
        d->passiveViews.push_back(pcView);
    }
    if (auto* mdiView = dynamic_cast<MDIView*>(pcView)) {
        synchronizePresentationView(*mdiView);
    }
}

void Document::detachView(Gui::BaseView* pcView, bool bPassiv)
{
    const auto suspended = d->presentationSuspendedViews.find(pcView);
    if (suspended != d->presentationSuspendedViews.end()) {
        if (suspended->second) {
            suspended->second->setUpdatesEnabled(true);
        }
        d->presentationSuspendedViews.erase(suspended);
    }
    if (bPassiv) {
        if (find(d->passiveViews.begin(), d->passiveViews.end(), pcView) != d->passiveViews.end()) {
            d->passiveViews.remove(pcView);
        }
    }
    else {
        if (find(d->baseViews.begin(), d->baseViews.end(), pcView) != d->baseViews.end()) {
            d->baseViews.remove(pcView);
        }

        // last view?
        if (d->baseViews.empty()) {
            // decouple a passive view
            std::list<Gui::BaseView*>::iterator it = d->passiveViews.begin();
            while (it != d->passiveViews.end()) {
                (*it)->setDocument(nullptr);
                it = d->passiveViews.begin();
            }

            // is already closing the document, and is not linked by other documents
            if (!d->_isClosing && App::PropertyXLink::getDocumentInList(getDocument()).empty()) {
                d->_pcAppWnd->onLastWindowClosed(this);
            }
        }
    }
}

void Document::onUpdate()
{
#ifdef FC_LOGUPDATECHAIN
    Base::Console().log("Acti: Gui::Document::onUpdate()");
#endif

    for (auto* v : d->baseViews) {
        v->onUpdate();
    }

    for (auto* v : d->passiveViews) {
        v->onUpdate();
    }
}

void Document::onRelabel()
{
#ifdef FC_LOGUPDATECHAIN
    Base::Console().log("Acti: Gui::Document::onRelabel()");
#endif

    for (auto* v : d->baseViews) {
        v->onRelabel(this);
    }

    for (auto* v : d->passiveViews) {
        v->onRelabel(this);
    }

    d->connectChangeDocumentBlocker.unblock();
}

bool Document::isLastView()
{
    if (d->baseViews.size() <= 1) {
        return true;
    }
    return false;
}

/**
 *  This method checks if the document can be closed. It checks on
 *  the save state of the document and is able to abort the closing.
 */
bool Document::canClose(bool checkModify, bool checkLink)
{
    if (d->_isClosing) {
        return true;
    }
    if (!getDocument()->isClosable()) {
        QMessageBox::warning(
            getActiveView(),
            QObject::tr("Document not closable"),
            QObject::tr("The document is not closable for the moment.")
        );
        return false;
    }
    // else if (!Gui::Control().isAllowedAlterDocument()) {
    //     std::string name = Gui::Control().activeDialog()->getDocumentName();
    //     if (name == this->getDocument()->getName()) {
    //         QMessageBox::warning(getActiveView(),
    //             QObject::tr("Document not closable"),
    //             QObject::tr("The document is in editing mode and thus cannot be closed for the
    //             moment.\n"
    //                         "You either have to finish or cancel the editing in the task panel."));
    //         Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog();
    //         if (dlg) Gui::Control().showDialog(dlg);
    //         return false;
    //     }
    // }

    if (checkLink && !App::PropertyXLink::getDocumentInList(getDocument()).empty()) {
        return true;
    }

    if (getDocument()->testStatus(App::Document::TempDoc)) {
        return true;
    }

    bool ok = true;
    if (checkModify && isModified() && !getDocument()->testStatus(App::Document::PartialDoc)) {
        int res = getMainWindow()->confirmSave(getDocument(), getActiveView());
        switch (res) {
            case MainWindow::ConfirmSaveResult::Cancel:
                ok = false;
                break;
            case MainWindow::ConfirmSaveResult::SaveAll:
            case MainWindow::ConfirmSaveResult::Save:
                ok = save();
                if (!ok) {
                    const QString docName = QString::fromStdString(getDocument()->Label.getStrValue());
                    const QString text
                        = (!docName.isEmpty()
                               ? QObject::tr("Failed to save document '%1'. Would you like to cancel the closure?")
                                     .arg(docName)
                               : QObject::tr(
                                     "Document saving failed. Would you like to cancel the closure?"
                                 ));
                    int ret = QMessageBox::question(
                        getActiveView(),
                        QObject::tr("Unable to save document"),
                        text,
                        QMessageBox::Discard | QMessageBox::Cancel,
                        QMessageBox::Discard
                    );
                    if (ret == QMessageBox::Discard) {
                        ok = true;
                    }
                }
                break;
            case MainWindow::ConfirmSaveResult::DiscardAll:
            case MainWindow::ConfirmSaveResult::Discard:
                ok = true;
                break;
        }
    }

    if (ok) {
        // If a task dialog is open that doesn't allow other commands to modify
        // the document it must be closed by resetting the edit mode of the
        // corresponding view provider.
        if (!Gui::Control().isAllowedAlterDocument(getDocument())) {
            std::string name = Gui::Control().activeDialog(getDocument())->getDocumentName();
            if (name == this->getDocument()->getName()) {
                // getInEdit() only checks if the currently active MDI view is
                // a 3D view and that it is in edit mode. However, when closing a
                // document then the edit mode must be reset independent of the
                // active view.
                if (d->_editViewProvider) {
                    this->_resetEdit();
                }
            }
        }
    }

    return ok;
}

class Document::ClosePreparation final : public QObject
{
    struct Target {
        std::string name;
        int id;
        std::optional<std::uint64_t> approved;
    };
    std::vector<Target> targets;
    std::vector<fastsignals::scoped_connection> waiters;
    SaveCallback finished;
    std::size_t index {0};
    int policy {MainWindow::ConfirmSaveResult::Cancel}; // Ask each time until Apply to all.
    std::optional<int> answer;
    bool checkLinks;
    bool queued {false};
    bool complete {false};
    bool waitingAfterSave {false};
    bool ownsQueries {false};
    bool closeDocuments;

    Document* lookup(const Target& target) const
    {
        auto* app = App::GetApplication().getDocument(target.name.c_str());
        auto* gui = app ? Application::Instance->getDocument(app) : nullptr;
        return gui && gui->d->_iDocId == target.id ? gui : nullptr;
    }

    void finish(bool success)
    {
        if (complete) { return; }
        complete = true;
        waiters.clear();
        for (const auto& target : targets) {
            if (auto* gui = lookup(target)) {
                if (success && (!target.approved
                    || *target.approved != gui->d->modificationGeneration
                    || !gui->getDocument()->isClosable())) {
                    success = false;
                }
            }
            else { success = false; }
        }
        if (success && closeDocuments) {
            for (const auto& target : targets) {
                auto* gui = lookup(target);
                if (!gui) { continue; }
                // Earlier close observers may edit another prepared document.
                // Never discard edits which were not covered by its decision.
                try {
                    PerformanceScope closeTiming("App::Application closeDocument");
                    if (*target.approved != gui->d->modificationGeneration
                        || !gui->getDocument()->isClosable()
                        || !App::GetApplication().closeDocument(target.name.c_str())) {
                        success = false;
                    }
                }
                catch (...) {
                    Base::Console().error("Document close failed: %s\n", target.name.c_str());
                    success = false;
                }
            }
        }
        releaseQueries();
        auto callback = std::move(finished);
        deleteLater();
        if (callback) { callback(success); }
    }

    void releaseQueries()
    {
        if (!ownsQueries) { return; }
        ownsQueries = false;
        for (const auto& target : targets) {
            if (auto* gui = lookup(target)) { gui->d->closeQueryActive = false; }
        }
    }

    void approve(Document& document)
    {
        targets[index].approved = document.d->modificationGeneration;
        ++index;
        answer.reset();
        queue();
    }

    void saveFailed()
    {
        auto* box = new QMessageBox(QMessageBox::Question,
            QObject::tr("Unable to save document"),
            QObject::tr("Document saving failed. Would you like to cancel the closure?"),
            QMessageBox::Discard | QMessageBox::Cancel, getMainWindow());
        box->setDefaultButton(QMessageBox::Discard);
        box->setAttribute(Qt::WA_DeleteOnClose);
        connect(box, &QDialog::finished, this, [this](int decision) {
            if (decision != QMessageBox::Discard) { finish(false); return; }
            answer = MainWindow::ConfirmSaveResult::Discard;
            queue();
        });
        box->open();
    }

    void step()
    {
        if (complete) { return; }
        if (index == targets.size()) { finish(true); return; }
        auto* gui = lookup(targets[index]);
        if (!gui) { finish(false); return; }
        auto* document = gui->getDocument();
        if (waitingAfterSave) {
            if (document->isCooperativeMutationActive() || document->isPresentationUpdateActive()) {
                if (waiters.empty()) {
                    const auto changed = [this](const App::Document&, bool active) {
                        if (!active) { queue(); }
                    };
                    waiters.emplace_back(document->signalCooperativeMutationChanged.connect(changed));
                    waiters.emplace_back(document->signalPresentationUpdateChanged.connect(changed));
                    waiters.emplace_back(App::GetApplication().signalDeleteDocument.connect(
                        [this](const App::Document&) { queue(); }));
                }
                getMainWindow()->showMessage(QObject::tr("Finishing document display before closing..."));
                return;
            }
            waiters.clear();
            waitingAfterSave = false;
            if (gui->isModified()) {
                getMainWindow()->showMessage(QObject::tr("Document changed after saving; close cancelled"));
                finish(false);
                return;
            }
            approve(*gui);
            return;
        }
        if (!document->isClosable()) {
            getMainWindow()->showMessage(QObject::tr("Document work is active; close cancelled"));
            finish(false);
            return;
        }
        const bool needsSave = gui->isModified()
            && !document->testStatus(App::Document::PartialDoc)
            && !document->testStatus(App::Document::TempDoc)
            && !(checkLinks && !App::PropertyXLink::getDocumentInList(document).empty());
        if (!needsSave) {
            const bool wasModified = gui->isModified();
            if (!gui->canClose(false, checkLinks)) { finish(false); return; }
            gui = lookup(targets[index]);
            if (!gui) { finish(false); }
            else if (!wasModified && gui->isModified()) { queue(); }
            else { approve(*gui); }
            return;
        }
        if (!answer) {
            if (policy == MainWindow::ConfirmSaveResult::SaveAll
                || policy == MainWindow::ConfirmSaveResult::DiscardAll) {
                answer = policy;
            }
            else {
                QPointer<ClosePreparation> guard(this);
                getMainWindow()->confirmSaveAsync(document, [guard](int decision) {
                    if (!guard || guard->complete) { return; }
                    guard->answer = decision;
                    guard->queue();
                }, getMainWindow(), targets.size() > 1);
                return;
            }
        }
        const int decision = *answer;
        if (decision == MainWindow::ConfirmSaveResult::Cancel) { finish(false); return; }
        if (decision == MainWindow::ConfirmSaveResult::SaveAll
            || decision == MainWindow::ConfirmSaveResult::DiscardAll) {
            policy = decision;
        }
        if (!gui->canClose(false, checkLinks)) { finish(false); return; }
        gui = lookup(targets[index]);
        if (!gui) { finish(false); return; }
        if (decision == MainWindow::ConfirmSaveResult::Discard
            || decision == MainWindow::ConfirmSaveResult::DiscardAll) {
            approve(*gui);
            return;
        }
        QPointer<ClosePreparation> guard(this);
        if (!gui->saveAsync([guard](bool saved) {
                if (!guard || guard->complete) { return; }
                if (!saved) { guard->saveFailed(); return; }
                guard->waitingAfterSave = true;
                guard->queue();
            })) {
            finish(false);
        }
    }

public:
    ClosePreparation(const std::vector<Document*>& documents, SaveCallback callback,
                     bool checkLinks, bool closeDocuments = false)
        : QObject(getMainWindow()), finished(std::move(callback)), checkLinks(checkLinks),
          closeDocuments(closeDocuments)
    {
        for (auto* document : documents) {
            if (document && std::none_of(targets.begin(), targets.end(), [&](const Target& target) {
                    return target.id == document->d->_iDocId;
                })) {
                targets.push_back({document->getDocument()->getName(), document->d->_iDocId, {}});
            }
        }
    }

    ~ClosePreparation() override { releaseQueries(); }

    void start()
    {
        for (const auto& target : targets) {
            if (auto* gui = lookup(target); gui && gui->d->closeQueryActive) {
                finish(false);
                return;
            }
        }
        for (const auto& target : targets) {
            if (auto* gui = lookup(target)) { gui->d->closeQueryActive = true; }
        }
        ownsQueries = true;
        queue();
    }

    void queue()
    {
        if (complete || queued) { return; }
        queued = true;
        // Always resume after the signal emitter returns; reconnecting from
        // inside a completion signal can otherwise prevent its final release.
        if (!dispatchToGuiFrame(this, [this] {
                queued = false;
                try { step(); }
                catch (const std::exception& error) {
                    Base::Console().error("Document close preparation failed: %s\n", error.what());
                    finish(false);
                }
                catch (...) { finish(false); }
            })) {
            finish(false);
        }
    }
};

void Document::prepareCloseAsync(const std::vector<Document*>& documents,
                                 SaveCallback finished, bool checkLinks)
{
    (new ClosePreparation(documents, std::move(finished), checkLinks))->start();
}

void Document::closeDocumentsAsync(const std::vector<Document*>& documents, SaveCallback finished)
{
    (new ClosePreparation(documents, std::move(finished), false, true))->start();
}

std::list<MDIView*> Document::getMDIViews(bool includePassive) const
{
    std::list<MDIView*> views;
    for (auto* v : d->baseViews) {
        auto view = dynamic_cast<MDIView*>(v);
        if (view) {
            views.push_back(view);
        }
    }

    if (includePassive) {
        for (auto* v : d->passiveViews) {
            auto view = dynamic_cast<MDIView*>(v);
            if (view) {
                views.push_back(view);
            }
        }
    }

    return views;
}

std::list<MDIView*> Document::getMDIViewsOfType(const Base::Type& typeId, bool includePassive) const
{
    std::list<MDIView*> views;
    for (auto* v : d->baseViews) {
        auto view = dynamic_cast<MDIView*>(v);
        if (view && view->isDerivedFrom(typeId)) {
            views.push_back(view);
        }
    }

    if (includePassive) {
        for (auto* v : d->passiveViews) {
            auto view = dynamic_cast<MDIView*>(v);
            if (view && view->isDerivedFrom(typeId)) {
                views.push_back(view);
            }
        }
    }

    return views;
}

/// send messages to the active view
bool Document::sendMsgToViews(const char* pMsg)
{
    for (auto* v : d->baseViews) {
        if (v->onMsg(pMsg)) {
            return true;
        }
    }

    for (auto* v : d->passiveViews) {
        if (v->onMsg(pMsg)) {
            return true;
        }
    }

    return false;
}

bool Document::sendMsgToFirstView(const Base::Type& typeId, const char* pMsg)
{
    // first try the active view
    Gui::MDIView* view = getActiveView();
    if (view && view->isDerivedFrom(typeId)) {
        if (view->onMsg(pMsg)) {
            return true;
        }
    }

    // now try the other views
    std::list<Gui::MDIView*> views = getMDIViewsOfType(typeId);
    for (const auto& it : views) {
        if ((it != view) && it->onMsg(pMsg)) {
            return true;
        }
    }

    return false;
}

/// Getter for the active view
MDIView* Document::getActiveView() const
{
    // get the main window's active view
    MDIView* active = getMainWindow()->activeWindow();

    // get all MDI views of the document
    std::list<MDIView*> mdis = getMDIViews(true);

    // check whether the active view is part of this document
    bool ok = false;
    for (const auto& mdi : mdis) {
        if (mdi == active) {
            ok = true;
            break;
        }
    }

    if (ok) {
        return active;
    }

    // the active view is not part of this document, just use the last view
    const auto& windows = Gui::getMainWindow()->windows();
    for (auto rit = mdis.rbegin(); rit != mdis.rend(); ++rit) {
        // Some view is removed from window list for some reason, e.g. TechDraw
        // hidden page has view but not in the list. By right, the view will
        // self delete, but not the case for TechDraw, especially during
        // document restore.
        if (windows.contains(*rit) || (*rit)->isDerivedFrom<View3DInventor>()) {
            return *rit;
        }
    }
    return nullptr;
}

MDIView* Document::setActiveView(const ViewProviderDocumentObject* vp, Base::Type typeId)
{
    MDIView* view = nullptr;
    if (!vp) {
        view = getActiveView();
    }
    else {
        view = vp->getMDIView();
        if (!view) {
            auto obj = vp->getObject();
            if (!obj) {
                view = getActiveView();
            }
            else {
                auto linked = obj->getLinkedObject(true);
                if (linked != obj) {
                    auto vpLinked = freecad_cast<ViewProviderDocumentObject*>(
                        Application::Instance->getViewProvider(linked)
                    );
                    if (vpLinked) {
                        view = vpLinked->getMDIView();
                    }
                }

                if (!view && typeId.isBad()) {
                    MDIView* active = getActiveView();
                    if (active && active->containsViewProvider(vp)) {
                        view = active;
                    }
                    else {
                        typeId = View3DInventor::getClassTypeId();
                    }
                }
            }
        }
    }

    if (!view || (!typeId.isBad() && !view->isDerivedFrom(typeId))) {
        view = nullptr;
        for (auto* v : d->baseViews) {
            if (v->isDerivedFrom<MDIView>() && (typeId.isBad() || v->isDerivedFrom(typeId))) {
                view = static_cast<MDIView*>(v);
                break;
            }
        }
    }

    if (!view && !typeId.isBad()) {
        view = createView(typeId);
    }

    if (view) {
        getMainWindow()->setActiveWindow(view);
    }

    return view;
}

/**
 * @brief Document::setActiveWindow
 * If this document is active and the view is part of it then it will be
 * activated. If the document is not active or if the view is already active
 * nothing is done.
 * @param view
 */
void Document::setActiveWindow(Gui::MDIView* view)
{
    // get the main window's active view
    MDIView* active = getMainWindow()->activeWindow();

    // view is already active
    if (active == view) {
        return;
    }

    // get all MDI views of the document
    std::list<MDIView*> mdis = getMDIViews(true);

    // this document is not active
    if (std::ranges::find(mdis, active) == mdis.end()) {
        return;
    }

    // the view is not part of the document
    if (std::ranges::find(mdis, view) == mdis.end()) {
        return;
    }

    getMainWindow()->setActiveWindow(view);
}

Gui::MDIView* Document::getViewOfNode(SoNode* node) const
{
    std::list<MDIView*> mdis = getMDIViewsOfType(View3DInventor::getClassTypeId());
    for (const auto& mdi : mdis) {
        auto view = static_cast<View3DInventor*>(mdi);
        if (view->getViewer()->searchNode(node)) {
            return mdi;
        }
    }

    return nullptr;
}

Gui::MDIView* Document::getViewOfViewProvider(const Gui::ViewProvider* vp) const
{
    return getViewOfNode(vp->getRoot());
}

Gui::MDIView* Document::getEditingViewOfViewProvider(Gui::ViewProvider* vp) const
{
    std::list<MDIView*> mdis = getMDIViewsOfType(View3DInventor::getClassTypeId());
    for (const auto& mdi : mdis) {
        auto view = static_cast<View3DInventor*>(mdi);
        View3DInventorViewer* viewer = view->getViewer();
        // there is only one 3d view which is in edit mode
        if (viewer->hasViewProvider(vp) && viewer->isEditingViewProvider()) {
            return mdi;
        }
    }

    return nullptr;
}

//--------------------------------------------------------------------------
// UNDO REDO transaction handling
//--------------------------------------------------------------------------
/** Open a new Undo transaction on the active document
 *  This method opens a new UNDO transaction on the active document. This transaction
 *  will later appear in the UNDO/REDO dialog with the name of the command. If the user
 *  recall the transaction everything changed on the document between OpenCommand() and
 *  CommitCommand will be undone (or redone). You can use an alternative name for the
 *  operation default is the command name.
 *  @see CommitCommand(),AbortCommand()
 */
int Document::openCommand(const char* sName)
{
    // A user gesture may enter through a generic tree/ribbon boundary which
    // has already opened its exact transaction before dispatching a legacy
    // ViewProvider. Reuse that command-owned transaction. Opening a second
    // transaction here would implicitly commit the provisional first one
    // before the task panel can adopt it, splitting one human action into two
    // undo records and making Cancel unable to remove created geometry.
    const int enclosingTransaction =
        TaskView::TaskDialog::ownedEnclosingTransactionId(
            getDocument()
        );
    if (enclosingTransaction != App::NullTransaction) {
        return enclosingTransaction;
    }
    return getDocument()->openTransaction(App::TransactionName {.name = sName, .temporary = false});
}

void Document::commitCommand()
{
    const int transactionId =
        getDocument()->getBookedTransactionID();
    getDocument()->commitTransaction();
    if (transactionId != App::NullTransaction
        && !App::GetApplication().transactionIsActive(
            transactionId
        )) {
        TaskView::TaskDialog::recordCommandTransactionCompletion(
            getDocument(),
            transactionId
        );
    }
}

void Document::abortCommand()
{
    // Generic command rollback is not a task-panel Cancel signal. In
    // particular, a failed sub-command while editing must not poison a later
    // OK by marking the entire adopted edit transaction for rollback. The
    // transaction lock makes this a no-op when the command is owned by the
    // live editor; TaskView::reject() and cancelEdit() are the explicit Cancel
    // boundaries that mark exact rollback after ViewProvider teardown.
    const int transactionId =
        getDocument()->getBookedTransactionID();
    getDocument()->abortTransaction();
    if (transactionId != App::NullTransaction
        && !App::GetApplication().transactionIsActive(
            transactionId
        )) {
        TaskView::TaskDialog::recordCommandTransactionCompletion(
            getDocument(),
            transactionId
        );
    }
}

bool Document::hasPendingCommand() const
{
    return getDocument()->hasPendingTransaction();
}

/// Get a string vector with the 'Undo' actions
std::vector<std::string> Document::getUndoVector() const
{
    return getDocument()->getAvailableUndoNames();
}

/// Get a string vector with the 'Redo' actions
std::vector<std::string> Document::getRedoVector() const
{
    return getDocument()->getAvailableRedoNames();
}

bool Document::checkTransactionID(bool undo, int iSteps)
{
    if (!iSteps) {
        return false;
    }

    std::vector<int> ids;
    for (int i = 0; i < iSteps; i++) {
        int id = getDocument()->getTransactionID(undo, i);
        if (!id) {
            break;
        }
        ids.push_back(id);
    }
    std::set<App::Document*> prompts;
    std::map<App::Document*, int> dmap;
    for (auto doc : App::GetApplication().getDocuments()) {
        if (doc == getDocument()) {
            continue;
        }
        for (auto id : ids) {
            int steps = undo ? doc->getAvailableUndos(id) : doc->getAvailableRedos(id);
            if (!steps) {
                continue;
            }
            int& currentSteps = dmap[doc];
            if (currentSteps + 1 != steps) {
                prompts.insert(doc);
            }
            if (currentSteps < steps) {
                currentSteps = steps;
            }
        }
    }
    if (!prompts.empty()) {
        std::ostringstream str;
        int i = 0;
        for (auto doc : prompts) {
            if (i++ == 5) {
                str << "...\n";
                break;
            }
            str << "    " << doc->getName() << "\n";
        }
        int ret = QMessageBox::warning(
            getMainWindow(),
            undo ? QObject::tr("Undo") : QObject::tr("Redo"),
            QStringLiteral("%1,\n%2%3")
                .arg(
                    QObject::tr(
                        "There are grouped transactions in the following documents with "
                        "other preceding transactions"
                    ),
                    QString::fromStdString(str.str()),
                    QObject::tr(
                        "Choose 'Yes' to roll back all preceding transactions.\n"
                        "Choose 'No' to roll back in the active document only.\n"
                        "Choose 'Abort' to abort"
                    )
                ),
            QMessageBox::Yes | QMessageBox::No | QMessageBox::Abort,
            QMessageBox::Yes
        );
        if (ret == QMessageBox::Abort) {
            return false;
        }
        if (ret == QMessageBox::No) {
            return true;
        }
    }
    for (auto& v : dmap) {
        for (int i = 0; i < v.second; ++i) {
            if (undo) {
                v.first->undo();
            }
            else {
                v.first->redo();
            }
        }
    }
    return true;
}

bool Document::isPerformingTransaction() const
{
    return d->_isTransacting;
}

/// Will UNDO one or more steps
void Document::undo(int iSteps)
{
    Base::FlagToggler<> flag(d->_isTransacting);

    if (!checkTransactionID(true, iSteps)) {
        return;
    }

    for (int i = 0; i < iSteps; i++) {
        getDocument()->undo();
    }
    App::GetApplication().signalUndo();
}

/// Will REDO one or more steps
void Document::redo(int iSteps)
{
    Base::FlagToggler<> flag(d->_isTransacting);

    if (!checkTransactionID(false, iSteps)) {
        return;
    }

    for (int i = 0; i < iSteps; i++) {
        getDocument()->redo();
    }
    App::GetApplication().signalRedo();

    for (auto it : d->_redoViewProviders) {
        handleChildren3D(it);
    }
    d->_redoViewProviders.clear();
}

PyObject* Document::getPyObject()
{
    _pcDocPy->IncRef();
    return _pcDocPy;
}

void Document::handleChildren3D(ViewProvider* viewProvider, bool deleting)
{
    // check for children
    if (viewProvider && viewProvider->getChildRoot()) {
        std::vector<App::DocumentObject*> children = viewProvider->claimChildren3D();
        SoGroup* childGroup = viewProvider->getChildRoot();
        SoGroup* frontGroup = viewProvider->getFrontRoot();
        SoGroup* backGroup = viewProvider->getFrontRoot();

        // size not the same -> build up the list new
        if (deleting || childGroup->getNumChildren() != static_cast<int>(children.size())) {

            std::set<ViewProviderDocumentObject*> oldChildren;
            for (int i = 0, count = childGroup->getNumChildren(); i < count; ++i) {
                auto it = d->_CoinMap.find(static_cast<SoSeparator*>(childGroup->getChild(i)));
                if (it == d->_CoinMap.end()) {
                    continue;
                }
                oldChildren.insert(it->second);
            }

            Gui::coinRemoveAllChildren(childGroup);
            Gui::coinRemoveAllChildren(frontGroup);
            Gui::coinRemoveAllChildren(backGroup);

            if (!deleting) {
                for (const auto& it : children) {
                    if (auto ChildViewProvider
                        = dynamic_cast<ViewProviderDocumentObject*>(getViewProvider(it))) {
                        auto itOld = oldChildren.find(ChildViewProvider);
                        if (itOld != oldChildren.end()) {
                            oldChildren.erase(itOld);
                        }

                        if (SoSeparator* childRootNode = ChildViewProvider->getRoot()) {
                            if (childRootNode == childGroup) {
                                Base::Console().warning(
                                    "Document::handleChildren3D: Do not add "
                                    "group of '%s' to itself\n",
                                    it->getNameInDocument()
                                );
                            }
                            else if (childGroup) {
                                childGroup->addChild(childRootNode);
                            }
                        }

                        if (SoSeparator* childFrontNode = ChildViewProvider->getFrontRoot()) {
                            if (childFrontNode == frontGroup) {
                                Base::Console().warning(
                                    "Document::handleChildren3D: Do not add "
                                    "foreground group of '%s' to itself\n",
                                    it->getNameInDocument()
                                );
                            }
                            else if (frontGroup) {
                                frontGroup->addChild(childFrontNode);
                            }
                        }

                        if (SoSeparator* childBackNode = ChildViewProvider->getBackRoot()) {
                            if (childBackNode == backGroup) {
                                Base::Console().warning(
                                    "Document::handleChildren3D: Do not add "
                                    "background group of '%s' to itself\n",
                                    it->getNameInDocument()
                                );
                            }
                            else if (backGroup) {
                                backGroup->addChild(childBackNode);
                            }
                        }

                        // cycling to all views of the document to remove the viewprovider from the
                        // viewer itself
                        for (Gui::BaseView* vIt : d->baseViews) {
                            auto activeView = dynamic_cast<View3DInventor*>(vIt);
                            if (activeView
                                && activeView->getViewer()->hasViewProvider(ChildViewProvider)) {
                                // @Note hasViewProvider()
                                // remove the viewprovider serves the purpose of detaching the
                                // inventor nodes from the top level root in the viewer. However, if
                                // some of the children were grouped beneath the object earlier they
                                // are not anymore part of the toplevel inventor node. we need to
                                // check for that.
                                activeView->getViewer()->removeViewProvider(ChildViewProvider);
                            }
                        }
                    }
                }
            }

            // add the remaining old children back to toplevel invertor node
            for (auto vpd : oldChildren) {
                auto obj = vpd->getObject();
                if (!obj || !obj->isAttachedToDocument()) {
                    continue;
                }

                for (BaseView* view : d->baseViews) {
                    auto activeView = dynamic_cast<View3DInventor*>(view);
                    if (activeView && !activeView->getViewer()->hasViewProvider(vpd)) {
                        activeView->getViewer()->addViewProvider(vpd);
                    }
                }
            }
        }
    }
}

void Document::toggleInSceneGraph(ViewProvider* vp)
{
    // FIXME: What's the point of having this function?
    //
    for (auto view : d->baseViews) {
        auto activeView = dynamic_cast<View3DInventor*>(view);
        if (!activeView) {
            continue;
        }

        auto root = vp->getRoot();
        if (!root) {
            continue;
        }

        auto scenegraph = dynamic_cast<SoGroup*>(activeView->getViewer()->getSceneGraph());
        if (!scenegraph) {
            continue;
        }

        // If it cannot be added then only check the top-level nodes
        if (!vp->canAddToSceneGraph()) {
            int idx = scenegraph->findChild(root);
            if (idx >= 0) {
                scenegraph->removeChild(idx);
            }
        }
        else {
            // Do a deep search of the scene because the root node
            // isn't necessarily a top-level node when claimed by
            // another view provider.
            // This is to avoid to add a node twice to the scene.
            SoSearchAction sa;
            sa.setNode(root);
            sa.setSearchingAll(false);
            sa.apply(scenegraph);

            SoPath* path = sa.getPath();
            if (!path) {
                scenegraph->addChild(root);
            }
        }
    }
}

void Document::slotChangePropertyEditor(const App::Document& doc, const App::Property& Prop)
{
    if (getDocument() == &doc) {
        FC_LOG(Prop.getFullName() << " editor changed");
        setModified(true);
        getMainWindow()->setUserSchema(doc.UnitSystem.getValue());
    }
}

std::vector<App::DocumentObject*> Document::getTreeRootObjects() const
{
    std::vector<App::DocumentObject*> docObjects = d->_pcDocument->getObjects();
    std::unordered_map<App::DocumentObject*, bool> rootMap;
    for (auto it : docObjects) {
        rootMap[it] = true;
    }

    for (auto obj : docObjects) {
        ViewProvider* vp = Application::Instance->getViewProvider(obj);
        if (!vp) {
            continue;
        }

        std::vector<App::DocumentObject*> children = vp->claimChildren();
        for (auto child : children) {
            rootMap[child] = false;
        }
    }

    std::vector<App::DocumentObject*> rootObjs;
    for (const auto& it : rootMap) {
        if (it.second) {
            rootObjs.push_back(it.first);
        }
    }
    return rootObjs;
}
