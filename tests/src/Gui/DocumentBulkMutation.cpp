// SPDX-License-Identifier: LGPL-2.1-or-later

#include <algorithm>
#include <atomic>
#include <future>
#include <sstream>
#include <thread>
#include <vector>

#include <QCoreApplication>
#include <QCloseEvent>
#include <QElapsedTimer>
#include <QMouseEvent>
#include <QMessageBox>
#include <QMdiSubWindow>
#include <QAbstractButton>
#include <QListWidget>
#include <QTest>
#include <QThread>
#include <QTimer>
#include <QTemporaryDir>
#include <QFile>
#include <QScopeGuard>
#include <QTreeWidget>
#include <QTreeWidgetItemIterator>

#include <src/App/InitApplication.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentObserverPython.h>
#include <App/DocumentTimeline.h>
#include <App/HostRuntime.h>
#include <App/HostWorkflow.h>
#include <App/GroupExtension.h>
#include <App/Link.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Interpreter.h>
#include <Base/ConsoleObserver.h>
#include <Base/Reader.h>
#include <Base/Sequencer.h>
#include <Base/Writer.h>
#include <CXX/Objects.hxx>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/FrameBudget.h>
#include <Gui/FrameSequence.h>
#include <Gui/ModelTreeBrowser.h>
#include <Gui/MainWindow.h>
#include <Gui/MDIView.h>
#include <Gui/ProgressBar.h>
#include <Gui/Selection/Selection.h>
#include <Gui/ViewProviderDocumentObject.h>

#ifdef _MSC_VER
#include <Windows.h>
#include <DbgHelp.h>
#include <zipios++/zipios-config.h>
#endif
#include <zipios++/zipinputstream.h>

namespace
{

// Undo keeps the removed object alive, allowing the test to detect stale tree
// dereferences deterministically instead of relying on freed-memory contents.
class RemovedObjectProbe final: public App::DocumentObject
{
public:
    mutable int attachmentQueries {0};

    bool isAttachedToDocument() const override
    {
        ++attachmentQueries;
        return App::DocumentObject::isAttachedToDocument();
    }
};

#ifdef _MSC_VER
LONG CALLBACK captureAccessViolation(EXCEPTION_POINTERS* exception)
{
    if (exception->ExceptionRecord->ExceptionCode != EXCEPTION_ACCESS_VIOLATION) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    static std::atomic_flag captured;
    const auto path = qEnvironmentVariable("VIBECAD_TEST_ACCESS_VIOLATION_DUMP");
    if (path.isEmpty() || captured.test_and_set()) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    // Capture first-chance corruption without attaching a debugger, whose
    // exception-event overhead changes the timing of asynchronous restore.
    HMODULE library = LoadLibraryW(L"dbghelp.dll");
    auto writeDump = library ? reinterpret_cast<decltype(&MiniDumpWriteDump)>(
        GetProcAddress(library, "MiniDumpWriteDump")) : nullptr;
    HANDLE file = CreateFileW(reinterpret_cast<LPCWSTR>(path.utf16()), GENERIC_WRITE,
        0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (writeDump && file != INVALID_HANDLE_VALUE) {
        MINIDUMP_EXCEPTION_INFORMATION information {GetCurrentThreadId(), exception, FALSE};
        writeDump(GetCurrentProcess(), GetCurrentProcessId(), file, MiniDumpWithFullMemory,
            &information, nullptr, nullptr);
    }
    if (file != INVALID_HANDLE_VALUE) { CloseHandle(file); }
    if (library) { FreeLibrary(library); }
    return EXCEPTION_CONTINUE_SEARCH;
}
#endif

App::HostWorkflow<bool> restoreGuiArchive(Gui::Document* document, std::string data)
{
    co_yield App::HostWorkflowStep {App::HostRuntime::Lane::Document, [&](std::stop_token) {
        if (QThread::currentThread() == qApp->thread()) {
            throw std::runtime_error("Archive preparation ran on the GUI owner");
        }
        Base::PyGILStateLocker python;
        std::istringstream input(data);
        zipios::ZipInputStream zip(input);
        Base::Reader reader(zip, "GuiDocument.xml", 1);
        document->RestoreDocFile(reader);
        auto properties = reader.getLocalReader();
        if (!properties || properties->FileList.empty()) {
            throw std::runtime_error("GUI archive did not register binary properties");
        }
        properties->readFiles(zip);
        for (const auto& file : properties->FileList) {
            if (properties->hasReadFailed(file.FileName)) {
                throw std::runtime_error("Embedded GUI property failed to restore");
            }
        }
    }};
    co_return QThread::currentThread() == qApp->thread();
}

App::HostWorkflow<std::string> saveGuiArchive(Gui::Document* document)
{
    std::ostringstream archive;
    co_yield App::HostWorkflowStep {App::HostRuntime::Lane::Document, [&](std::stop_token) {
                                        Base::PyGILStateLocker python;
                                        Base::ZipWriter writer(archive);
                                        writer.putNextEntry("GuiDocument.xml");
                                        document->SaveDocFile(writer);
                                        writer.writeFiles();
                                    }};
    co_return archive.str();
}

App::HostWorkflow<bool> saveDocumentCopy(App::Document* document, std::string filename)
{
    bool saved = false;
    co_yield App::HostWorkflowStep {App::HostRuntime::Lane::Document, [&](std::stop_token) {
        Base::PyGILStateLocker python;
        saved = document->saveCopy(filename.c_str());
    }};
    co_return saved;
}

class MousePressProbe final: public QWidget
{
public:
    bool receivedMousePress {false};

protected:
    void mousePressEvent(QMouseEvent* event) override
    {
        receivedMousePress = true;
        QWidget::mousePressEvent(event);
    }
};

class PostedEventProbe final: public QObject
{
public:
    explicit PostedEventProbe(QEvent::Type eventType)
        : eventType(eventType)
    {}

    int received {0};

protected:
    bool event(QEvent* event) override
    {
        if (event->type() == eventType) {
            ++received;
            return true;
        }
        return QObject::event(event);
    }

private:
    QEvent::Type eventType;
};

class CloseRequestProbe final: public QObject
{
public:
    int requests {0};

protected:
    bool eventFilter(QObject*, QEvent* event) override
    {
        if (event->type() != QEvent::Close) {
            return false;
        }
        ++requests;
        event->ignore();
        return true; // Observe application shutdown without quitting the test runner.
    }
};

}  // namespace

class DocumentBulkMutationTest: public QObject
{
    Q_OBJECT

    std::unique_ptr<Gui::Application> application;
    std::unique_ptr<Gui::MainWindow> window;
    std::unique_ptr<Base::ConsoleObserverStd> console;
#ifdef _MSC_VER
    void* exceptionHandler {nullptr};
#endif

private Q_SLOTS:
    void treeDoesNotDereferenceRemovedObjectWithoutViewProvider()
    {
        auto& app = App::GetApplication();
        auto* document = app.newDocument("RemovedUnpresentedObject");
        auto* object = new RemovedObjectProbe;
        document->addObject(object, "InternalObject");
        QVERIFY(!application->getDocument(document)->getViewProvider(object));
        QTRY_VERIFY(document->isClosable());

        QTreeWidget* tree = nullptr;
        for (auto* candidate : window->findChildren<QTreeWidget*>()) {
            if (candidate->inherits("Gui::TreeWidget")) {
                tree = candidate;
                break;
            }
        }
        QVERIFY(tree);
        document->setUndoMode(1);
        document->openTransaction("Remove internal object");
        object->touch();
        document->removeObject("InternalObject");
        document->commitTransaction();
        QVERIFY(!document->containsObject(object));
        object->attachmentQueries = 0;

        QVERIFY(QMetaObject::invokeMethod(tree, "onUpdateStatus", Qt::DirectConnection));
        bool dispatched = false;
        QVERIFY(Gui::dispatchToGuiFrame([&] { dispatched = true; }));
        QTRY_VERIFY(dispatched);
        QTRY_VERIFY(document->isClosable());
        const int queries = object->attachmentQueries;
        QVERIFY(app.closeDocument("RemovedUnpresentedObject"));
        QCOMPARE(queries, 0);
    }

    void closeRemainsRejectedWhenAnObserverStartsFinalization()
    {
        auto& guiApplication = *application;
        auto& mainWindow = *window;
        auto& application = App::GetApplication();
        auto* document = application.newDocument("close_observer_finalization");
        const std::string name = document->getName();
        guiApplication.getDocument(document)->setModified(false);
        fastsignals::scoped_connection connection = application.signalBeforeCloseDocument.connect(
            [&](const App::Document& closing) {
                if (closing.getName() == name) {
                    closing.beginPresentationUpdate();
                }
            });

        QVERIFY(!mainWindow.closeAllDocuments(true));
        QCOMPARE(application.getDocument(name.c_str()), document);
        document->endPresentationUpdate();

        QCloseEvent close;
        guiApplication.tryClose(&close);
        QVERIFY(!close.isAccepted());
        QVERIFY(!guiApplication.isClosing());
        QCOMPARE(application.getDocument(name.c_str()), document);
        QTRY_VERIFY_WITH_TIMEOUT(document->isPresentationUpdateActive(), 5000);
        connection.disconnect();
        document->endPresentationUpdate();
        QVERIFY(application.closeDocument(name.c_str()));
    }

    void sharesPreparedProjectionAndWakesAfterMutation()
    {
        auto& app = App::GetApplication();
        auto* document = app.newDocument("SharedProjectionCache");
        for (int index = 0; index < 50; ++index) {
            document->addObject("App::FeaturePython", "Source");
        }
        Gui::ModelTreeBrowserCache cache(document);
        QObject firstReceiver;
        QObject secondReceiver;
        std::shared_ptr<const Gui::ModelTreeBrowserProjection> first;
        std::shared_ptr<const Gui::ModelTreeBrowserProjection> second;
        std::string error;
        document->beginCooperativeMutation();
        cache.request(&firstReceiver, [&](auto result, std::string failure) {
            first = std::move(result);
            error = std::move(failure);
        });
        cache.request(&secondReceiver, [&](auto result, std::string failure) {
            second = std::move(result);
            error = std::move(failure);
        });
        QCoreApplication::processEvents();
        QVERIFY(!first && !second);
        document->endCooperativeMutation();
        QTRY_VERIFY_WITH_TIMEOUT(first && second, 10000);
        QVERIFY(error.empty());
        QCOMPARE(first.get(), second.get());
        auto offOwnerRequest = std::async(std::launch::async, [&] {
            try {
                cache.request(&firstReceiver, [](auto, std::string) {});
            }
            catch (const std::exception&) {
                return true;
            }
            return false;
        });
        QVERIFY(offOwnerRequest.get());
        int invalidations = 0;
        cache.observeInvalidation(&firstReceiver, [&] { ++invalidations; });
        second.reset();
        cache.request(&secondReceiver, [&](auto result, std::string) { second = std::move(result); });
        QTRY_VERIFY(second);
        QCOMPARE(first.get(), second.get());
        document->beginCooperativeMutation();
        for (auto* object : document->getObjects()) {
            object->Label.setValue("Changed source");
        }
        document->endCooperativeMutation();
        QCoreApplication::processEvents();
        QCOMPARE(invalidations, 0); // Local property-specific handlers own refresh policy.
        auto* added = document->addObject("App::FeaturePython", "NewSource");
        second.reset();
        cache.request(&secondReceiver, [&](auto result, std::string) { second = std::move(result); });
        QTRY_VERIFY_WITH_TIMEOUT(second, 10000);
        QVERIFY(second.get() != first.get());
        QVERIFY(second->find(added));
        QCOMPARE(invalidations, 0);
        auto* linkedDocument = app.newDocument("ProjectionLinkedSource");
        auto* linkedOwner = linkedDocument->addObject("App::FeaturePython", "LinkedOwner");
        // Timeline ownership is document-local. Exercise a supported external
        // reference instead: its incoming relationship is part of the capture.
        auto* externalReference = static_cast<App::PropertyLink*>(
            linkedOwner->addDynamicProperty("App::PropertyLink", "ReferencedSource"));
        externalReference->setAllowExternal(true);
        externalReference->setValue(added);
        second.reset();
        cache.request(&secondReceiver, [&](auto result, std::string) { second = std::move(result); });
        QTRY_VERIFY_WITH_TIMEOUT(second, 10000);
        const auto sources = second->sourceDocuments();
        QVERIFY(std::ranges::find(sources, linkedDocument) != sources.end());
        const auto beforeLinkedChange = invalidations;
        for (int index = 0; index < 50; ++index) {
            linkedOwner->Label.setValue(("Linked source " + std::to_string(index)).c_str());
        }
        QTRY_COMPARE(invalidations, beforeLinkedChange + 1);
        QVERIFY(!second->isCurrent());
        app.closeDocument(document->getName());
        app.closeDocument(linkedDocument->getName());
    }

    void projectionSchemaChangesInvalidateWithoutValueChanges()
    {
        auto& app = App::GetApplication();
        auto* document = app.newDocument("ProjectionSchemaChanges");
        auto* object = document->addObject("App::FeaturePython", "Source");
        Gui::ModelTreeBrowserCache cache(document);
        QObject receiver;
        Gui::ModelTreeBrowserCache::Result snapshot;
        int invalidations = 0;
        cache.observeInvalidation(&receiver, [&] { ++invalidations; });
        const auto refresh = [&] {
            snapshot.reset();
            cache.request(&receiver, [&](auto result, std::string) { snapshot = std::move(result); });
        };
        refresh();
        QTRY_VERIFY_WITH_TIMEOUT(snapshot, 10000);

        // No property value is set: schema edits themselves must wake the
        // consumer, including removal where the old property no longer exists.
        auto* property = object->addDynamicProperty("App::PropertyString", "Metadata");
        QVERIFY(property);
        QTRY_COMPARE(invalidations, 1);
        QVERIFY(!snapshot->isCurrent());
        refresh();
        QTRY_VERIFY_WITH_TIMEOUT(snapshot, 10000);
        QVERIFY(object->renameDynamicProperty(property, "RenamedMetadata"));
        QTRY_COMPARE(invalidations, 2);
        QVERIFY(!snapshot->isCurrent());
        refresh();
        QTRY_VERIFY_WITH_TIMEOUT(snapshot, 10000);
        QVERIFY(object->removeDynamicProperty("RenamedMetadata"));
        QTRY_COMPARE(invalidations, 3);
        QVERIFY(!snapshot->isCurrent());
        refresh();
        QTRY_VERIFY_WITH_TIMEOUT(snapshot, 10000);

        // Use the same exported type factory as document restore and Python
        // addExtension; the template's type methods are not exported on Windows.
        auto* extension = static_cast<App::Extension*>(
            Base::Type::fromName("App::GroupExtensionPython").createInstance());
        QVERIFY(extension);
        extension->initExtension(object); // Container owns Python extensions.
        QTRY_COMPARE(invalidations, 4);
        QVERIFY(!snapshot->isCurrent());
        refresh();
        QTRY_VERIFY_WITH_TIMEOUT(snapshot, 10000);
        app.closeDocument(document->getName());
    }

    void initTestCase()
    {
        tests::initApplication();
#ifdef _MSC_VER
        if (qEnvironmentVariableIsSet("VIBECAD_TEST_ACCESS_VIOLATION_DUMP")) {
            exceptionHandler = AddVectoredExceptionHandler(1, captureAccessViolation);
        }
#endif
        if (qEnvironmentVariableIsSet("VIBECAD_RESTORE_DETAIL_TRACE")) {
            console = std::make_unique<Base::ConsoleObserverStd>();
            Base::Console().attachObserver(console.get());
        }
        Gui::Application::initApplication();
        Gui::Application::initOpenInventor();
        application = std::make_unique<Gui::Application>(true);
        window = std::make_unique<Gui::MainWindow>();
        // Native GUI commands use the application's normal console aliases.
        Base::Interpreter().runString("import FreeCAD as App\nimport FreeCADGui as Gui\n");
    }

    void cleanupTestCase()
    {
        App::GetApplication().closeAllDocuments();
        window.reset();
        application.reset();
        if (console) { Base::Console().detachObserver(console.get()); }
#ifdef _MSC_VER
        if (exceptionHandler) { RemoveVectoredExceptionHandler(exceptionHandler); }
#endif
    }

    void visibilityBurstUpdatesFolderWithoutReplacingHierarchy()
    {
        auto& guiApplication = *application;
        auto& mainWindow = *window;
        auto& app = App::GetApplication();
        auto* document = app.newDocument("FolderVisibilityBurst");
        std::vector<App::DocumentObject*> objects;
        for (int index = 0; index < 50; ++index) {
            objects.push_back(document->addObject("App::FeaturePython", "VisibilitySource"));
            objects.back()->Visibility.setValue(true);
        }
        QTreeWidget* tree = nullptr;
        for (auto* candidate : mainWindow.findChildren<QTreeWidget*>()) {
            if (candidate->inherits("Gui::TreeWidget")) { tree = candidate; break; }
        }
        QVERIFY(tree);
        const auto folderWithTip = [&](const QString& tip) -> QTreeWidgetItem* {
            for (QTreeWidgetItemIterator it(tree); *it; ++it) {
                if ((*it)->toolTip(0) == tip) { return *it; }
            }
            return nullptr;
        };
        QTRY_VERIFY_WITH_TIMEOUT(folderWithTip(QStringLiteral("50 of 50 items visible")), 10000);
        auto* folder = folderWithTip(QStringLiteral("50 of 50 items visible"));
        constexpr int marker = Qt::UserRole + 123;
        folder->setData(0, marker, QStringLiteral("retained hierarchy"));
        for (auto* object : objects) { object->Visibility.setValue(false); }
        QTRY_VERIFY_WITH_TIMEOUT(folderWithTip(QStringLiteral("0 of 50 items visible")), 10000);
        auto* hiddenFolder = folderWithTip(QStringLiteral("0 of 50 items visible"));
        QCOMPARE(hiddenFolder->data(0, marker).toString(), QStringLiteral("retained hierarchy"));
        for (int index = 0; index < 25; ++index) { objects[index]->Visibility.setValue(true); }
        QTRY_VERIFY_WITH_TIMEOUT(folderWithTip(QStringLiteral("25 of 50 items visible")), 10000);
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);

        auto* retained = folderWithTip(QStringLiteral("25 of 50 items visible"));
        retained->setExpanded(true);
        QVERIFY(retained->childCount() > 0);
        auto* selectedItem = retained->child(0);
        const auto selectedName = selectedItem->text(2);
        selectedItem->setSelected(true);
        int insertedRows = 0;
        int removedRows = 0;
        bool transferredPopulatedSubtree = false;
        const auto inspectRows = [&](const QModelIndex& parent, int first, int last) {
            for (int row = first; row <= last; ++row) {
                transferredPopulatedSubtree |= tree->model()->rowCount(
                    tree->model()->index(row, 0, parent)) != 0;
            }
        };
        const auto inserted = QObject::connect(tree->model(), &QAbstractItemModel::rowsInserted,
            tree, [&](const QModelIndex& parent, int first, int last) {
                insertedRows += last - first + 1;
                inspectRows(parent, first, last);
            });
        const auto removed = QObject::connect(tree->model(), &QAbstractItemModel::rowsAboutToBeRemoved,
            tree, [&](const QModelIndex& parent, int first, int last) {
                removedRows += last - first + 1;
                inspectRows(parent, first, last);
            });
        // A schema-only change requests a replacement hierarchy. Qt does not
        // retain expansion/visibility set on detached staging items.
        objects.front()->addDynamicProperty("App::PropertyString", "RebuildBrowser");
        QTRY_VERIFY_WITH_TIMEOUT(
            folderWithTip(QStringLiteral("25 of 50 items visible"))
                && folderWithTip(QStringLiteral("25 of 50 items visible"))->data(0, marker).isNull(),
            10000);
        auto* replacement = folderWithTip(QStringLiteral("25 of 50 items visible"));
        QVERIFY(replacement->isExpanded());
        bool selectionRetained = false;
        for (int index = 0; index < replacement->childCount(); ++index) {
            const auto* child = replacement->child(index);
            if (child->text(2) == selectedName) { selectionRetained = child->isSelected(); }
        }
        QVERIFY(selectionRetained);
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        QObject::disconnect(inserted);
        QObject::disconnect(removed);
        QVERIFY(insertedRows >= 50);
        QVERIFY(removedRows >= 50);
        QVERIFY(!transferredPopulatedSubtree);
        app.closeDocument(document->getName());
    }

    void transientLinkPlacementsDoNotScheduleTreeRefresh()
    {
        auto& app = App::GetApplication();
        auto* document = app.newDocument("TransientLinkPlacement");
        auto* link = dynamic_cast<App::Link*>(
            document->addObject("App::Link", "AnimatedLink"));
        QVERIFY(link);

        QTreeWidget* tree = nullptr;
        for (auto* candidate : window->findChildren<QTreeWidget*>()) {
            if (candidate->inherits("Gui::TreeWidget")) {
                tree = candidate;
                break;
            }
        }
        QVERIFY(tree);
        QTRY_VERIFY_WITH_TIMEOUT(
            !tree->findItems(
                     QStringLiteral("AnimatedLink"),
                     Qt::MatchStartsWith | Qt::MatchRecursive)
                 .empty(),
            5000
        );

        const auto activeTreeTimers = [tree] {
            const auto timers =
                tree->findChildren<QTimer*>(QString(), Qt::FindDirectChildrenOnly);
            return std::count_if(
                timers.cbegin(),
                timers.cend(),
                [](const QTimer* timer) { return timer->isActive(); }
            );
        };
        QTRY_COMPARE_WITH_TIMEOUT(activeTreeTimers(), 0, 5000);

        link->setStatus(App::ObjectStatus::NoTouch, true);
        link->LinkPlacement.setValue(
            Base::Placement(Base::Vector3d(1, 2, 3), Base::Rotation()));
        QCOMPARE(activeTreeTimers(), 0);
        link->setStatus(App::ObjectStatus::NoTouch, false);

        QVERIFY(app.closeDocument(document->getName()));
    }

    void restoresGuiArchivePropertiesFromDocumentWorker()
    {
        auto& guiApplication = *application;
        auto& mainWindow = *window;
        auto& application = App::GetApplication();
        auto* document = application.newDocument("worker_gui_restore");
        auto* guiDocument = guiApplication.getDocument(document);
        QVERIFY(guiDocument);
        std::vector<Gui::ViewProviderDocumentObject*> providers;
        for (int index = 0; index < 50; ++index) {
            auto* object = document->addObject("App::FeaturePython", "RestoredFeature");
            auto* provider = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                guiDocument->getViewProvider(object)
            );
            QVERIFY(provider);
            provider->Visibility.setValue(false);
            provider->setTreeRank(index);
            auto* colours = dynamic_cast<App::PropertyColorList*>(provider->addDynamicProperty(
                "App::PropertyColorList", "RestoreColours"
            ));
            QVERIFY(colours);
            colours->setValues({Base::Color(1, 0, 0, 0.5F), Base::Color(0, 1, 0, 0.5F)});
            providers.push_back(provider);
        }

        bool saved = false;
        auto archive = saveGuiArchive(guiDocument).runAsync(
            application.hostRuntime(), [](std::function<void()> resume) {
                if (!Gui::dispatchToGuiFrame(std::move(resume))) {
                    throw std::runtime_error("Owner dispatch failed");
                }
            }, [&] { saved = true; });
        QTRY_VERIFY_WITH_TIMEOUT(saved, 10000);
        std::string archiveData;
        try {
            archiveData = archive.get();
        }
        catch (const std::exception& error) {
            QFAIL(error.what());
        }
        for (auto* provider : providers) {
            provider->Visibility.setValue(true);
            provider->setTreeRank(-1);
            static_cast<App::PropertyColorList*>(provider->getPropertyByName("RestoreColours"))
                ->setValues({});
        }

        // Exercise real XML and embedded binary restoration, including the
        // Python-owned caller case. Neither GUI adoption nor its callbacks may
        // run on the document worker or deadlock waiting for its GIL.
        std::atomic<int> wrongThread {0};
        std::atomic<int> notifications {0};
        fastsignals::scoped_connection connection = guiApplication.signalChangedObject.connect(
            [&](const Gui::ViewProvider&, const App::Property&) {
                if (QThread::currentThread() != qApp->thread()) {
                    ++wrongThread;
                }
                Base::PyGILStateLocker python;
                ++notifications;
            }
        );
        bool completionNotified = false;
        std::future<bool> restored;
        restored = restoreGuiArchive(guiDocument, std::move(archiveData)).runAsync(
            application.hostRuntime(), [](std::function<void()> resume) {
                if (!Gui::dispatchToGuiFrame(std::move(resume))) {
                    throw std::runtime_error("Owner dispatch failed");
                }
            }, [&] {
                QCOMPARE(QThread::currentThread(), qApp->thread());
                QVERIFY(restored.valid());
                QVERIFY(restored.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready);
                completionNotified = true;
            }
        );
        QTRY_VERIFY_WITH_TIMEOUT(completionNotified, 10000);
        try {
            QVERIFY(restored.get());
        }
        catch (const std::exception& error) {
            QFAIL(error.what());
        }
        QCOMPARE(wrongThread.load(), 0);
        QVERIFY(notifications.load() >= static_cast<int>(providers.size()));
        for (std::size_t index = 0; index < providers.size(); ++index) {
            auto* provider = providers[index];
            QVERIFY(!provider->Visibility.getValue());
            QCOMPARE(provider->getTreeRank(), static_cast<int>(index));
            const auto& colours = static_cast<App::PropertyColorList*>(
                provider->getPropertyByName("RestoreColours")
            )->getValues();
            QCOMPARE(colours.size(), std::size_t(2));
            QCOMPARE(colours[0].r, 1.0F);
            QCOMPARE(colours[1].g, 1.0F);
        }
        connection.disconnect();
        application.closeDocument(document->getName());
    }

    void queuesNativeDocumentOpenWithoutBlockingInput()
    {
        auto& guiApplication = *application;
        auto& mainWindow = *window;
        QTemporaryDir files;
        QVERIFY(files.isValid());
        const auto filename = files.filePath(QStringLiteral("queued-restore.FCStd")).toStdString();
        auto& application = App::GetApplication();
        auto* original = application.newDocument("queued_restore_fixture");
        for (int index = 0; index < 50; ++index) {
            original->addObject("App::FeaturePython", "RestoredFeature");
        }
        bool saveCompleted = false;
        auto saved = saveDocumentCopy(original, filename).runAsync(
            application.hostRuntime(), [](std::function<void()> resume) {
                if (!Gui::dispatchToGuiFrame(std::move(resume))) {
                    throw std::runtime_error("Owner dispatch failed");
                }
            }, [&] { saveCompleted = true; });
        QTRY_VERIFY_WITH_TIMEOUT(saveCompleted, 10000);
        try { QVERIFY(saved.get()); }
        catch (const std::exception& error) { QFAIL(error.what()); }
        QVERIFY(application.closeDocument(original->getName()));
        const auto cancelledFile = files.filePath(QStringLiteral("cancelled.FCStd"));
        QVERIFY(QFile::copy(QString::fromStdString(filename), cancelledFile));
        const auto followingFile = files.filePath(QStringLiteral("after-cancellation.FCStd"));
        QVERIFY(QFile::copy(QString::fromStdString(filename), followingFile));

        MousePressProbe input;
        input.show();
        int completed = 0;
        std::string openedName;
        std::string cancelledName;
        std::string followingName;
        std::stop_source cancellation;
        fastsignals::scoped_connection cancelDuringFinalization;
        fastsignals::scoped_connection opening = application.signalStartRestoreDocument.connect(
            [&](const App::Document& restoring) {
                if (restoring.FileName.getStrValue().find("cancelled.FCStd") != std::string::npos) {
                    cancelledName = restoring.getName();
                    cancelDuringFinalization = application.getDocument(restoring.getName())
                        ->signalFinishRestoreObject.connect(
                        [&](const App::DocumentObject&) { cancellation.request_stop(); });
                }
                QCoreApplication::postEvent(&input, new QMouseEvent(
                    QEvent::MouseButtonPress, QPointF(1, 1), QPointF(1, 1),
                    Qt::LeftButton, Qt::LeftButton, Qt::NoModifier));
            });
        App::OpenDocumentsRequest first;
        first.filenames = {filename};
        first.callback = [&](App::OpenDocumentsResult result) {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            if (result.failure) {
                try { std::rethrow_exception(result.failure); }
                catch (const Base::Exception& error) { QFAIL(error.what()); }
                catch (const std::exception& error) { QFAIL(error.what()); }
                catch (...) { QFAIL("Unknown open failure"); }
            }
            QCOMPARE(result.documents.size(), std::size_t(1));
            QVERIFY(result.documents.front());
            QVERIFY(result.errors.front().empty());
            QVERIFY(result.documents.front()->getObject("RestoredFeature049"));
            openedName = result.documents.front()->getName();
            QVERIFY(input.receivedMousePress);
            QCOMPARE(completed++, 0);
        };
        App::OpenDocumentsRequest missing;
        missing.filenames = {files.filePath(QStringLiteral("missing.FCStd")).toStdString()};
        missing.callback = [&](App::OpenDocumentsResult result) {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            QVERIFY(!result.failure);
            QCOMPARE(result.documents.size(), std::size_t(1));
            QVERIFY(!result.documents.front());
            QVERIFY(!result.errors.front().empty());
            QCOMPARE(completed++, 1);
        };
        App::OpenDocumentsRequest cancelled;
        cancelled.filenames = {cancelledFile.toStdString()};
        cancelled.cancellation = cancellation;
        cancelled.callback = [&](App::OpenDocumentsResult result) {
            cancelDuringFinalization.disconnect();
            bool aborted = false;
            if (result.failure) {
                try {
                    std::rethrow_exception(result.failure);
                }
                catch (const Base::AbortException&) {
                    aborted = true;
                }
                catch (...) {}
            }
            QVERIFY(aborted);
            QVERIFY(!cancelledName.empty());
            QVERIFY(!application.getDocument(cancelledName.c_str()));
            QCOMPARE(completed++, 2);
        };
        App::OpenDocumentsRequest following;
        following.filenames = {followingFile.toStdString()};
        following.inputFlags = {{.createView = false}};
        following.callback = [&](App::OpenDocumentsResult result) {
            QVERIFY(!result.failure);
            QCOMPARE(result.documents.size(), std::size_t(1));
            QVERIFY(result.documents.front());
            QVERIFY(result.errors.front().empty());
            QVERIFY(result.documents.front()->getObject("RestoredFeature049"));
            auto* guiDocument = guiApplication.getDocument(result.documents.front());
            QVERIFY(guiDocument);
            QVERIFY(guiDocument->getMDIViews().empty());
            followingName = result.documents.front()->getName();
            QCOMPARE(completed++, 3);
        };
        guiApplication.openNativeDocumentAsync(filename.c_str(), false, std::move(first.callback));
        application.openDocumentsAsync(std::move(missing));
        application.openDocumentsAsync(std::move(cancelled));
        application.openDocumentsAsync(std::move(following));
        QCOMPARE(completed, 0);
        QVERIFY(application.hasPendingDocumentOpens());
        QTRY_COMPARE_WITH_TIMEOUT(completed, 4, 10000);
        QVERIFY(!application.hasPendingDocumentOpens());
        // Open completion precedes the independently tracked display adoption.
        // Wait for the actual close barrier, not just the file-open callback.
        QTRY_VERIFY_WITH_TIMEOUT(application.getDocument(openedName.c_str())->isClosable(), 5000);
        QTRY_VERIFY_WITH_TIMEOUT(application.getDocument(followingName.c_str())->isClosable(), 5000);
        QVERIFY(application.closeDocument(openedName.c_str()));
        QVERIFY(application.closeDocument(followingName.c_str()));

        // Cancelling the final open does not mean a previous document's
        // asynchronous display adoption is finished. Resume close only after
        // that authoritative lease ends, not merely when the open queue empties.
        auto* displaying = application.newDocument("close_waits_for_display");
        const std::string displayingName = displaying->getName();
        displaying->beginPresentationUpdate();
        auto displayWaiter = application.hostRuntime().submit(
            App::HostRuntime::Lane::Document, [displaying](std::stop_token) {
                displaying->waitForPresentationReady();
            });
        CloseRequestProbe closeProbe;
        mainWindow.installEventFilter(&closeProbe);
        bool cancelledOpenFinished = false;
        App::OpenDocumentsRequest closingRequest;
        closingRequest.filenames = {filename};
        closingRequest.callback = [&](App::OpenDocumentsResult) { cancelledOpenFinished = true; };
        application.openDocumentsAsync(std::move(closingRequest));
        QCloseEvent close;
        guiApplication.tryClose(&close);
        QVERIFY(!close.isAccepted());
        QTRY_VERIFY_WITH_TIMEOUT(cancelledOpenFinished, 10000);
        QCoreApplication::sendPostedEvents(&mainWindow, QEvent::MetaCall);
        QCOMPARE(closeProbe.requests, 0);
        displaying->endPresentationUpdate();
        QTRY_VERIFY_WITH_TIMEOUT(
            displayWaiter.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready, 5000);
        displayWaiter.get();
        QTRY_COMPARE_WITH_TIMEOUT(closeProbe.requests, 1, 5000);
        QVERIFY(application.closeDocument(displayingName.c_str()));
    }

    void repeatedVisibilityAdoptionDoesNotDirtySavedDocument()
    {
        auto& app = App::GetApplication();
        auto* document = app.newDocument("visibility_adoption");
        auto* object = document->addObject("App::FeaturePython", "Result");
        auto* gui = application->getDocument(document);
        auto* provider = dynamic_cast<Gui::ViewProviderDocumentObject*>(gui->getViewProvider(object));
        QVERIFY(provider);
        provider->setStatus(Gui::ViewStatus::TouchDocument, true);
        provider->hide();
        gui->setModified(false);
        provider->hide();
        QVERIFY(!gui->isModified());
        QVERIFY(!provider->Visibility.getValue());
        provider->show();
        QVERIFY(gui->isModified());
        gui->setModified(false);
        provider->show();
        QVERIFY(!gui->isModified());
        QVERIFY(provider->Visibility.getValue());
        QVERIFY(app.closeDocument(document->getName()));
    }

    void queuesInteractiveSaveAndKeepsDocumentLeasedUntilCompletion()
    {
        auto& app = App::GetApplication();
        QTemporaryDir files;
        QVERIFY(files.isValid());
        auto* document = app.newDocument("queued_save");
        auto* guiDocument = application->getDocument(document);
        for (int index = 0; index < 50; ++index) {
            document->addObject("App::FeaturePython", "SavedFeature");
        }
        const auto filename = files.filePath("saved.FCStd").toStdString();
        QVERIFY(document->saveAs(filename.c_str()));
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        std::vector<std::string> expectedObjects;
        for (const auto* object : document->getObjects()) {
            expectedObjects.emplace_back(object->getNameInDocument());
        }
        std::sort(expectedObjects.begin(), expectedObjects.end());
        document->Comment.setValue("queued save preserves this edit");
        guiDocument->setModified(true);
        bool finished = false;
        bool success = false;
        QVERIFY(guiDocument->saveAsync([&](bool saved) {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            success = saved;
            finished = true;
        }));
        QVERIFY(!finished);
        QVERIFY(document->isCooperativeMutationActive());
        QVERIFY(guiDocument->isModified());
        QVERIFY(!app.closeDocument(document->getName()));
        QTRY_VERIFY_WITH_TIMEOUT(finished, 10000);
        QVERIFY(success);
        QVERIFY(!document->isCooperativeMutationActive());
        QVERIFY(!guiDocument->isModified());
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);

        // A filesystem failure must complete the request and release the lease,
        // without claiming the user's edits were saved.
        QFile blocker(files.filePath("not-a-directory"));
        QVERIFY(blocker.open(QIODevice::WriteOnly));
        blocker.close();
        document->FileName.setValue((blocker.fileName() + "/cannot-save.FCStd").toStdString());
        document->Comment.setValue("must remain modified after save failure");
        guiDocument->setModified(true);
        finished = false;
        success = true;
        QVERIFY(guiDocument->saveAsync([&](bool saved) {
            success = saved;
            finished = true;
        }));
        QTRY_VERIFY_WITH_TIMEOUT(finished, 10000);
        QVERIFY(!success);
        QVERIFY(!document->isCooperativeMutationActive());
        QVERIFY(guiDocument->isModified());
        QVERIFY(app.closeDocument(document->getName()));
        auto* restored = app.openDocument(filename.c_str());
        QVERIFY(restored);
        std::vector<std::string> restoredObjects;
        for (const auto* object : restored->getObjects()) {
            restoredObjects.emplace_back(object->getNameInDocument());
        }
        std::sort(restoredObjects.begin(), restoredObjects.end());
        QVERIFY(restoredObjects == expectedObjects);
        QCOMPARE(restored->Comment.getStrValue(), std::string("queued save preserves this edit"));
        QTRY_VERIFY_WITH_TIMEOUT(!restored->isPresentationUpdateActive(), 10000);
        QVERIFY(app.closeDocument(restored->getName()));
    }

    void versionWarningReturnsBeforeAnswerAndReportsCancellation()
    {
        auto& app = App::GetApplication();
        QTemporaryDir files;
        QVERIFY(files.isValid());
        auto* document = app.newDocument("version_warning");
        auto* gui = application->getDocument(document);
        const auto filename = files.filePath("version.FCStd").toStdString();
        QVERIFY(document->saveAs(filename.c_str()));
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        document->Comment.setValue("unsaved edit");
        gui->setModified(true);
        auto preferences = app.GetParameterGroupByPath("User parameter:BaseApp/Preferences/Document");
        const bool disabled = preferences->GetBool("DisableVersionCheckOnSave", false);
        auto& config = App::Application::Config();
        const auto major = config["BuildVersionMajor"];
        const auto restore = qScopeGuard([&] {
            preferences->SetBool("DisableVersionCheckOnSave", disabled);
            config["BuildVersionMajor"] = major;
        });
        preferences->SetBool("DisableVersionCheckOnSave", false);
        config["BuildVersionMajor"] = "999";
        bool returned = false;
        bool returnedBeforeAnswer = false;
        bool answered = false;
        int callbacks = 0;
        bool saved = true;
        // Exercise the real warning and cancel it even on the old nested-exec
        // implementation, so a regression fails instead of hanging the suite.
        QTimer::singleShot(0, window.get(), [&] {
            auto* prompt = qobject_cast<QMessageBox*>(QApplication::activeModalWidget());
            if (!prompt) { return; }
            returnedBeforeAnswer = returned;
            answered = true;
            prompt->button(QMessageBox::Cancel)->click();
        });
        const bool accepted = gui->saveAsync([&](bool success) {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            ++callbacks;
            saved = success;
        });
        returned = true;
        QTRY_VERIFY_WITH_TIMEOUT(answered, 5000);
        QVERIFY(returnedBeforeAnswer);
        QVERIFY(accepted);
        QTRY_COMPARE_WITH_TIMEOUT(callbacks, 1, 5000);
        QVERIFY(!saved);
        QVERIFY(gui->isModified());
        QVERIFY(!document->isCooperativeMutationActive());
        QVERIFY(app.closeDocument(document->getName()));
        auto* reopened = app.openDocument(filename.c_str());
        QVERIFY(reopened);
        QVERIFY(reopened->Comment.getStrValue() != "unsaved edit");
        QTRY_VERIFY_WITH_TIMEOUT(!reopened->isPresentationUpdateActive(), 10000);
        gui = application->getDocument(reopened);
        reopened->Comment.setValue("approved upgrade");
        gui->setModified(true);
        callbacks = 0;
        QVERIFY(gui->saveAsync([&](bool success) { ++callbacks; saved = success; }));
        auto* prompt = window->findChild<QMessageBox*>("confirmVersionUpgrade");
        QVERIFY(prompt);
        for (auto* button : prompt->buttons()) {
            if (prompt->buttonRole(button) == QMessageBox::AcceptRole) {
                button->click();
                break;
            }
        }
        QCOMPARE(callbacks, 0);
        QTRY_COMPARE_WITH_TIMEOUT(callbacks, 1, 10000);
        QVERIFY(saved);
        QVERIFY(!gui->isModified());
        QTRY_VERIFY_WITH_TIMEOUT(!reopened->isPresentationUpdateActive(), 10000);
        QVERIFY(app.closeDocument(reopened->getName()));
        reopened = app.openDocument(filename.c_str());
        QCOMPARE(reopened->Comment.getStrValue(), std::string("approved upgrade"));
        QTRY_VERIFY_WITH_TIMEOUT(!reopened->isPresentationUpdateActive(), 10000);
        QVERIFY(app.closeDocument(reopened->getName()));
    }

    void refusesQueuedSaveDuringAnEditingTransaction()
    {
        auto& app = App::GetApplication();
        QTemporaryDir files;
        QVERIFY(files.isValid());
        auto* document = app.newDocument("save_edit_conflict");
        auto* guiDocument = application->getDocument(document);
        QVERIFY(document->saveAs(files.filePath("editing.FCStd").toStdString().c_str()));
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        document->openTransaction("Feature edit");
        bool finished = false;
        const bool accepted = guiDocument->saveAsync([&](bool) { finished = true; });
        if (accepted) {
            QTRY_VERIFY_WITH_TIMEOUT(finished, 10000);
        }
        document->abortTransaction();
        QVERIFY(app.closeDocument(document->getName()));
        QVERIFY(!accepted);
        QVERIFY(!finished);
    }

    void archiveSaveRejectsConflictingGuiMutation()
    {
        auto& app = App::GetApplication();
        QTemporaryDir files;
        QVERIFY(files.isValid());
        auto* document = app.newDocument("save_mutation_conflict");
        auto* other = app.newDocument("other_save_document");
        auto* object = document->addObject("App::FeaturePython", "SavedFeature");
        auto* guiDocument = application->getDocument(document);
        QVERIFY(document->saveAs(files.filePath("snapshot.FCStd").toStdString().c_str()));
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        const std::string comment = document->Comment.getStrValue();
        const std::string label = object->Label.getStrValue();
        bool attempted = false;
        int rejected = 0;
        auto connection = document->signalSaveDocument.connect([&](Base::Writer&) {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            attempted = true;
            const auto attempt = [&](auto mutation) {
                try { mutation(); }
                catch (const Base::RuntimeError&) { ++rejected; }
            };
            attempt([&] { document->Comment.setValue("racing document edit"); });
            attempt([&] { object->Label.setValue("racing object edit"); });
            attempt([&] { document->addObject("App::FeaturePython", "RacingFeature"); });
            // The other document is independent of the archive being written.
            other->Comment.setValue("independent edit is allowed");
        });
        bool finished = false;
        bool success = false;
        QVERIFY(guiDocument->saveAsync([&](bool saved) {
            success = saved;
            finished = true;
        }));
        QTRY_VERIFY_WITH_TIMEOUT(finished, 10000);
        connection.disconnect();
        QVERIFY(attempted);
        QVERIFY(success);
        const bool intact = document->Comment.getStrValue() == comment
            && object->Label.getStrValue() == label && !document->getObject("RacingFeature");
        QCOMPARE(other->Comment.getStrValue(), std::string("independent edit is allowed"));
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        QVERIFY(app.closeDocument(document->getName()));
        QVERIFY(app.closeDocument(other->getName()));
        QCOMPARE(rejected, 3);
        QVERIFY(intact);
    }

    void queuedSavePreservesEditsMadeByCompletionObservers()
    {
        auto& app = App::GetApplication();
        QTemporaryDir files;
        QVERIFY(files.isValid());
        auto* document = app.newDocument("save_completion_edit");
        auto* object = document->addObject("App::FeaturePython", "SavedFeature");
        auto* guiDocument = application->getDocument(document);
        const auto filename = files.filePath("completion.FCStd").toStdString();
        QVERIFY(document->saveAs(filename.c_str()));
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        object->Label.setValue("saved label");
        guiDocument->setModified(true);
        auto connection = document->signalFinishSave.connect([&](const App::Document&, const std::string&) {
            object->Label.setValue("new edit after capture");
        });
        bool finished = false;
        bool success = false;
        QVERIFY(guiDocument->saveAsync([&](bool saved) {
            success = saved;
            finished = true;
        }));
        QTRY_VERIFY_WITH_TIMEOUT(finished, 10000);
        connection.disconnect();
        QVERIFY(success);
        const bool stillModified = guiDocument->isModified();
        QCOMPARE(object->Label.getStrValue(), std::string("new edit after capture"));
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        QVERIFY(app.closeDocument(document->getName()));
        auto* restored = app.openDocument(filename.c_str());
        QVERIFY(restored);
        QCOMPARE(restored->getObject("SavedFeature")->Label.getStrValue(), std::string("saved label"));
        QTRY_VERIFY_WITH_TIMEOUT(!restored->isPresentationUpdateActive(), 10000);
        QVERIFY(app.closeDocument(restored->getName()));
        QVERIFY(stillModified);
    }

    void queuedSaveRecomputesWorkDirtiedAfterAdmission()
    {
        auto& app = App::GetApplication();
        QTemporaryDir files;
        QVERIFY(files.isValid());
        auto* document = app.newDocument("save_recompute_order");
        auto* object = document->addObject("App::FeaturePython", "SavedFeature");
        auto* guiDocument = application->getDocument(document);
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        document->recompute();
        QVERIFY(!document->mustExecute());
        QVERIFY(document->saveAs(files.filePath("ordered.FCStd").toStdString().c_str()));
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        bool recomputed = false;
        bool injected = false;
        fastsignals::scoped_connection dirtied = document->signalCooperativeMutationChanged.connect(
            [&](const App::Document&, bool active) {
                // Introduce one dependency change at admission, not another
                // edit when the final presentation takes over the lease.
                if (active && !injected) {
                    injected = true;
                    object->touch();
                }
            });
        fastsignals::scoped_connection recompute = document->signalBeforeRecompute.connect(
            [&](const App::Document&) { recomputed = true; });
        bool finished = false;
        bool success = false;
        QVERIFY(guiDocument->saveAsync([&](bool saved) {
            success = saved;
            finished = true;
        }));
        QTRY_VERIFY_WITH_TIMEOUT(finished, 10000);
        QVERIFY(success);
        const bool clean = !document->mustExecute();
        if (!clean) {
            for (const auto* item : document->getObjects()) {
                if (item->isTouched()) {
                    qWarning("Still touched after queued save: %s", item->getNameInDocument());
                }
            }
        }
        dirtied.disconnect();
        recompute.disconnect();
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        QVERIFY(app.closeDocument(document->getName()));
        QVERIFY(recomputed);
        QVERIFY(clean);
    }

    void preparesCloseThroughTheQueuedSaveAndPresentationBoundary()
    {
        auto& app = App::GetApplication();
        QTemporaryDir files;
        QVERIFY(files.isValid());
        auto* document = app.newDocument("queued_close_save");
        auto* guiDocument = application->getDocument(document);
        for (int index = 0; index < 50; ++index) {
            document->addObject("App::FeaturePython", "CloseFeature");
        }
        const auto filename = files.filePath("close.FCStd").toStdString();
        QVERIFY(document->saveAs(filename.c_str()));
        QTRY_VERIFY_WITH_TIMEOUT(!document->isPresentationUpdateActive(), 10000);
        document->Comment.setValue("saved before closing");
        guiDocument->setModified(true);
        bool finished = false;
        bool approved = false;
        Gui::Document::prepareCloseAsync({guiDocument}, [&](bool ready) {
            QCOMPARE(QThread::currentThread(), qApp->thread());
            approved = ready;
            finished = true;
        });
        QVERIFY(!finished);
        QTRY_VERIFY_WITH_TIMEOUT(window->findChild<QMessageBox*>("confirmSave"), 5000);
        auto* prompt = window->findChild<QMessageBox*>("confirmSave");
        QVERIFY(prompt->isVisible());
        QTest::mouseClick(prompt->button(QMessageBox::Save), Qt::LeftButton);
        QTRY_VERIFY_WITH_TIMEOUT(finished, 10000);
        QVERIFY(approved);
        QVERIFY(!guiDocument->isModified());
        QVERIFY(document->isClosable());
        QVERIFY(app.closeDocument(document->getName()));
        auto* restored = app.openDocument(filename.c_str());
        QVERIFY(restored);
        QCOMPARE(restored->Comment.getStrValue(), std::string("saved before closing"));
        QTRY_VERIFY_WITH_TIMEOUT(!restored->isPresentationUpdateActive(), 10000);
        QVERIFY(app.closeDocument(restored->getName()));
    }

    void documentDestructionRemovesItsLastView()
    {
        auto& app = App::GetApplication();
        auto* document = app.newDocument("terminal_view_close");
        auto* guiDocument = application->getDocument(document);
        const std::string name = document->getName();
        auto views = guiDocument->getMDIViews();
        if (views.empty()) {
            auto* view = new Gui::MDIView(guiDocument, window.get());
            window->addWindow(view);
            views = guiDocument->getMDIViews();
        }
        QCOMPARE(views.size(), std::size_t(1));
        QPointer<Gui::MDIView> view = views.front();
        QVERIFY(window->windows().contains(view.data()));
        QTRY_VERIFY_WITH_TIMEOUT(document->isClosable(), 5000);
        QVERIFY(app.closeDocument(name.c_str()));
        QVERIFY(!app.getDocument(name.c_str()));
        QTRY_VERIFY_WITH_TIMEOUT(view.isNull(), 5000);
        QVERIFY(!window->findChild<QMessageBox*>("confirmSave"));
    }

    void approvedLastViewCloseRemovesItsMdiSubWindow()
    {
        auto& app = App::GetApplication();
        auto* document = app.newDocument("single_click_view_close");
        auto* guiDocument = application->getDocument(document);
        guiDocument->setModified(false);

        const auto views = guiDocument->getMDIViews();
        QCOMPARE(views.size(), std::size_t(1));
        QPointer<Gui::MDIView> view = views.front();
        QPointer<QMdiSubWindow> subWindow = qobject_cast<QMdiSubWindow*>(view->parentWidget());
        QVERIFY(subWindow);

        subWindow->close();

        QTRY_VERIFY_WITH_TIMEOUT(!app.getDocument("single_click_view_close"), 5000);
        QTRY_VERIFY_WITH_TIMEOUT(view.isNull(), 5000);
        QTRY_VERIFY_WITH_TIMEOUT(subWindow.isNull(), 5000);
    }

    void queuedCloseAllPreservesDocumentsCreatedWhilePrompting()
    {
        auto& app = App::GetApplication();
        auto* original = app.newDocument("close_all_original");
        const std::string name = original->getName();
        application->getDocument(original)->setModified(true);
        bool finished = false;
        bool closed = true;
        window->closeAllDocumentsAsync([&](bool success) {
            closed = success;
            finished = true;
        });
        QVERIFY(!finished);
        QTRY_VERIFY_WITH_TIMEOUT(window->findChild<QMessageBox*>("confirmSave"), 5000);
        auto* prompt = window->findChild<QMessageBox*>("confirmSave");
        auto* added = app.newDocument("created_during_close");
        const std::string addedName = added->getName();
        QTest::mouseClick(prompt->button(QMessageBox::Discard), Qt::LeftButton);
        QTRY_VERIFY_WITH_TIMEOUT(finished, 5000);
        QVERIFY(!closed);
        QVERIFY(!app.getDocument(name.c_str()));
        QCOMPARE(app.getDocument(addedName.c_str()), added);
        QTRY_VERIFY_WITH_TIMEOUT(added->isClosable(), 5000);
        QVERIFY(app.closeDocument(addedName.c_str()));
    }

    void unorganizedTreeFinishesPresentation()
    {
        auto& app = App::GetApplication();
        auto preferences = app.GetParameterGroupByPath("User parameter:BaseApp/Preferences/TreeView");
        const bool organized = preferences->GetBool("OrganizeModelByType", true);
        const auto restorePreference = qScopeGuard([&] {
            preferences->SetBool("OrganizeModelByType", organized);
        });
        preferences->SetBool("OrganizeModelByType", false);
        auto* document = app.newDocument("UnorganizedTree");
        const std::string name = document->getName();
        document->beginCooperativeMutation();
        for (int index = 0; index < 50; ++index) {
            document->addObject("App::FeaturePython", "UnorganizedFeature");
        }
        document->endCooperativeMutation();
        QTRY_VERIFY_WITH_TIMEOUT(document->isClosable(), 5000);
        QVERIFY(app.closeDocument(name.c_str()));
    }

    void pythonPresentationCallPreservesResultAndExceptionAcrossWorkerHandoff()
    {
        auto& guiApplication = *application;
        auto& mainWindow = *window;
        auto& application = App::GetApplication();
        auto* document = application.newDocument("python_presentation_handoff");
        const std::string name = document->getName();
        auto work = application.hostRuntime().submit(App::HostRuntime::Lane::Document,
            [name](std::stop_token) {
                Base::PyGILStateLocker python;
                Py::Module gui(PyImport_ImportModule("FreeCADGui"), true);
                Py::Dict globals;
                globals.setItem("__builtins__", Py::Object(PyEval_GetBuiltins()));
                globals.setItem("Gui", gui);
                Py::Object lookup(PyRun_String(
                    "lambda name: Gui.getDocument(name).Document.Name",
                    Py_eval_input, globals.ptr(), globals.ptr()), true);
                Py::Tuple args(2);
                args.setItem(0, lookup);
                args.setItem(1, Py::String(name));
                Py::Object dispatcher = gui.getAttr("runOnMainThread");
                PyObject* result = PyObject_CallObject(dispatcher.ptr(), args.ptr());
                if (!result) {
                    PyErr_Print();
                    throw std::runtime_error("GUI presentation call failed from worker");
                }
                const bool correct = PyUnicode_Check(result)
                    && name == PyUnicode_AsUTF8(result);
                Py_DECREF(result);
                if (!correct) {
                    throw std::runtime_error("GUI presentation call lost its return value");
                }
                args.setItem(1, Py::String("missing_presentation_document"));
                result = PyObject_CallObject(dispatcher.ptr(), args.ptr());
                const bool preservedError = !result && PyErr_ExceptionMatches(PyExc_NameError);
                Py_XDECREF(result);
                PyErr_Clear();
                if (!preservedError) {
                    throw std::runtime_error("GUI presentation call lost its Python exception type");
                }

                // A document's Python proxy module can register commands when
                // imported on its restore worker. Registration belongs to the
                // owner even though module loading and caller-stack metadata do not.
                Py::Object command(PyRun_String(
                    "type('RestoreCommand', (), {'GetResources': lambda self: "
                    "{'MenuText': 'Restore handoff test'}})()",
                    Py_eval_input, globals.ptr(), globals.ptr()), true);
                Py::Tuple registration(2);
                registration.setItem(0, Py::String("Test_WorkerRestoreCommand"));
                registration.setItem(1, command);
                Py::Callable addCommand(gui.getAttr("addCommand"));
                addCommand.apply(registration);
            });
        QTRY_VERIFY_WITH_TIMEOUT(
            work.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready, 10000);
        try {
            work.get();
        }
        catch (const std::exception& error) {
            QFAIL(error.what());
        }
        auto* command = guiApplication.commandManager().getCommandByName("Test_WorkerRestoreCommand");
        QVERIFY(command);
        guiApplication.commandManager().removeCommand(command);
        QVERIFY(application.closeDocument(name.c_str()));
    }

    void defersViewProviderAttachmentUntilDocumentIsStable()
    {
        auto& guiApplication = *application;
        auto& mainWindow = *window;

        const auto documentName =
            App::GetApplication().getUniqueDocumentName("bulk_view_providers");
        auto* document = App::GetApplication().newDocument(documentName.c_str());
        QVERIFY(document);
        auto* guiDocument = guiApplication.getDocument(document);
        QVERIFY(guiDocument);
        guiApplication.setActiveDocument(guiDocument);
        Base::PyGILStateLocker python;
        Py::Dict globals;
        globals.setItem("__builtins__", Py::Object(PyEval_GetBuiltins()));
        Py::List epochs;
        globals.setItem("epochs", epochs);
        Py::Object observerInstance(PyRun_String(
            "type('EpochObserver', (), {'slotCooperativeMutationChanged': "
            "lambda self, doc, active: epochs.append((doc.Name, active))})()",
            Py_eval_input, globals.ptr(), globals.ptr()), true);
        App::DocumentObserverPython observer(observerInstance);
        int publishedViewProviders = 0;
        fastsignals::scoped_connection publishedConnection = guiDocument->signalNewObject.connect(
            [&publishedViewProviders](const Gui::ViewProviderDocumentObject&) {
                ++publishedViewProviders;
            }
        );

        document->beginCooperativeMutation();
        document->beginCooperativeMutation();
        document->endCooperativeMutation();
        QCOMPARE(epochs.length(), Py_ssize_t(1));
        const Py::Tuple entered(epochs[0]);
        QCOMPARE(Py::String(entered[0]).as_std_string(), documentName);
        QVERIFY(entered[1].isTrue());
        std::vector<App::DocumentObject*> objects;
        objects.reserve(50);
        for (int index = 0; index < 50; ++index) {
            auto* object = document->addObject(
                "App::FeaturePython",
                ("BulkFeature" + std::to_string(index)).c_str()
            );
            QVERIFY(object);
            objects.push_back(object);
            auto* provider = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                guiDocument->getViewProvider(object)
            );
            QVERIFY(provider);
            QCOMPARE(provider->getObject(), object);
            QCOMPARE(publishedViewProviders, 0);
        }

        document->endCooperativeMutation();
        // GUI adoption acquires its own lease before the original end returns.
        // Its completion is asynchronous; providers must remain protected.
        QCOMPARE(epochs.length(), Py_ssize_t(3));
        const Py::Tuple left(epochs[1]);
        QCOMPARE(Py::String(left[0]).as_std_string(), documentName);
        QVERIFY(!left[1].isTrue());
        const Py::Tuple adopting(epochs[2]);
        QCOMPARE(Py::String(adopting[0]).as_std_string(), documentName);
        QVERIFY(adopting[1].isTrue());
        QVERIFY(document->isCooperativeMutationActive());
        Base::PyGILStateRelease release;
        QTRY_COMPARE_WITH_TIMEOUT(publishedViewProviders, 50, 5000);
        QTRY_VERIFY_WITH_TIMEOUT(
            std::ranges::all_of(objects, [guiDocument](const auto* object) {
                auto* provider = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                    guiDocument->getViewProvider(object)
                );
                return provider && provider->getObject() == object;
            }),
            5000
        );

        // A large stable document must never make one tree projection callback
        // monopolize the GUI thread. Queue the exact signal path used by normal
        // object creation, then require the first drain to return within one
        // human-visible frame while the remaining work continues cooperatively.
        constexpr int projectionObjectCount = 1000;
        std::vector<App::DocumentObject*> projectionObjects;
        projectionObjects.reserve(projectionObjectCount);
        for (int index = 0; index < projectionObjectCount; ++index) {
            auto* object = document->addObject(
                "App::FeaturePython",
                ("ProjectionLoad" + std::to_string(index)).c_str()
            );
            QVERIFY(object);
            auto* role = static_cast<App::PropertyString*>(object->addDynamicProperty(
                "App::PropertyString",
                App::DocumentTimeline::RolePropertyName
            ));
            QVERIFY(role);
            role->setValue(App::DocumentTimeline::OperationRole);
            role->setStatus(App::Property::Hidden, true);
            role->setStatus(App::Property::NoRecompute, true);
            projectionObjects.push_back(object);
        }
        QTreeWidget* modelTree = nullptr;
        for (auto* candidate : mainWindow.findChildren<QTreeWidget*>()) {
            if (candidate->inherits("Gui::TreeWidget")) {
                modelTree = candidate;
                break;
            }
        }
        QVERIFY(modelTree);
        QElapsedTimer heartbeatInterval;
        heartbeatInterval.start();
        qint64 maximumHeartbeatGap = 0;
        QTimer heartbeat;
        heartbeat.setInterval(0);
        connect(&heartbeat, &QTimer::timeout, [&]() {
            maximumHeartbeatGap = std::max(maximumHeartbeatGap, heartbeatInterval.restart());
        });
        heartbeat.start();

        QElapsedTimer projectionSlice;
        projectionSlice.start();
        QVERIFY(QMetaObject::invokeMethod(modelTree, "onUpdateStatus", Qt::DirectConnection));
        QVERIFY2(
            projectionSlice.elapsed() < 16,
            qPrintable(
                QStringLiteral("Tree projection blocked the GUI thread for %1 ms")
                    .arg(projectionSlice.elapsed())
            )
        );
        QTRY_VERIFY_WITH_TIMEOUT(
            modelTree->findItems(
                        QStringLiteral("ProjectionLoad"),
                        Qt::MatchStartsWith | Qt::MatchRecursive
                    )
                    .size()
                >= projectionObjectCount * 2,
            10000
        );
        heartbeat.stop();
        QVERIFY2(
            maximumHeartbeatGap < 100,
            qPrintable(
                QStringLiteral("Tree projection starved the event loop for %1 ms")
                    .arg(maximumHeartbeatGap)
            )
        );

        auto* history = App::DocumentTimeline::ensure(document);
        QVERIFY(history);
        history->Operations.setValues(projectionObjects);
        history->Position.setValue(projectionObjectCount);
        auto* featureTimeline = mainWindow.findChild<QListWidget*>(
            QStringLiteral("VibeCADFeatureTimelineItems")
        );
        QVERIFY(featureTimeline);
        QTRY_COMPARE_WITH_TIMEOUT(featureTimeline->count(), projectionObjectCount + 1, 10000);

        Gui::Selection().clearSelection();
        QTRY_COMPARE(featureTimeline->selectedItems().size(), 0);

        // Resources belonging to the same root cannot straddle an unrelated
        // operation: History must refuse that ambiguous navigation boundary.
        auto* owned = projectionObjects.at(2);
        auto* role = dynamic_cast<App::PropertyString*>(
            owned->getPropertyByName(App::DocumentTimeline::RolePropertyName));
        if (!role) {
            role = static_cast<App::PropertyString*>(owned->addDynamicProperty(
                "App::PropertyString", App::DocumentTimeline::RolePropertyName));
        }
        auto* owner = dynamic_cast<App::PropertyLink*>(
            owned->getPropertyByName(App::DocumentTimeline::OwnerPropertyName));
        if (!owner) {
            owner = static_cast<App::PropertyLinkHidden*>(owned->addDynamicProperty(
                "App::PropertyLinkHidden", App::DocumentTimeline::OwnerPropertyName));
        }
        QVERIFY(role);
        QVERIFY(owner);
        const std::string previousRole = role->getValue();
        auto* previousOwner = owner->getValue();
        owner->setValue(projectionObjects.front());
        role->setValue(App::DocumentTimeline::ResourceRole);
        history->Operations.setValues(projectionObjects);
        QTRY_VERIFY(!featureTimeline->isEnabled());
        role->setValue(previousRole);
        owner->setValue(previousOwner);
        history->Operations.setValues(projectionObjects);
        QTRY_VERIFY(featureTimeline->isEnabled());
        QTRY_COMPARE(featureTimeline->count(), projectionObjectCount + 1);
        Gui::Selection().addSelection(document->getName(), projectionObjects.front()->getNameInDocument());
        Gui::Selection().addSelection(document->getName(), projectionObjects.back()->getNameInDocument());
        // Selection observers must enqueue one aggregate update, not scan and
        // mutate every history row inline for each selected object.
        QCOMPARE(featureTimeline->selectedItems().size(), 0);
        QTRY_COMPARE(featureTimeline->selectedItems().size(), 2);
        Gui::Selection().rmvSelection(document->getName(), projectionObjects.front()->getNameInDocument());
        QTRY_COMPARE(featureTimeline->selectedItems().size(), 1);
        Gui::Selection().clearSelection();
        QTRY_COMPARE(featureTimeline->selectedItems().size(), 0);

        // A stable bulk epoch must project only identities that changed. A
        // one-object recompute in a large document previously rebuilt both
        // the complete Tree and the complete History timeline before releasing
        // the presentation barrier.
        QTRY_VERIFY_WITH_TIMEOUT(document->isClosable(), 5000);
        QElapsedTimer targetedProjection;
        targetedProjection.start();
        document->beginCooperativeMutation();
        projectionObjects.front()->touch();
        document->endCooperativeMutation();
        QTRY_VERIFY_WITH_TIMEOUT(document->isClosable(), 5000);
        QVERIFY2(
            targetedProjection.elapsed() < 100,
            qPrintable(
                QStringLiteral(
                    "One dirty identity caused a whole-document projection taking %1 ms"
                ).arg(targetedProjection.elapsed())
            )
        );

        // Progress reported by a worker must remain cancellable without
        // turning the application's global progress filter into a modal input
        // blocker. This is the path used by async document recompute and every
        // workbench operation that reports through Base::SequencerLauncher.
        Gui::SequencerBar::instance()->getProgressBar(&mainWindow);
        std::promise<void> progressStarted;
        std::promise<void> releaseProgress;
        const auto releaseSignal = releaseProgress.get_future().share();
        std::thread progressWorker([&] {
            Base::SequencerLauncher progress("Background document work", 10);
            progressStarted.set_value();
            releaseSignal.wait();
        });
        progressStarted.get_future().wait();
        QCoreApplication::processEvents();

        MousePressProbe inputProbe;
        QMouseEvent mousePress(
            QEvent::MouseButtonPress,
            QPointF(1.0, 1.0),
            QPointF(1.0, 1.0),
            Qt::LeftButton,
            Qt::LeftButton,
            Qt::NoModifier
        );
        QCoreApplication::sendEvent(&inputProbe, &mousePress);
        const bool inputReachedApplication = inputProbe.receivedMousePress;
        releaseProgress.set_value();
        progressWorker.join();
        QCoreApplication::processEvents();
        QVERIFY2(
            inputReachedApplication,
            "Background progress consumed normal GUI input as if it were modal work"
        );

        // Progress percentage is presentation, not event-loop scheduling. A
        // large operation can advance thousands of times between visible
        // percentage changes; those advances must still provide bounded GUI
        // heartbeats so paint, status, and timers remain live.
        qint64 maximumProgressHeartbeatGap = 0;
        QElapsedTimer progressHeartbeatInterval;
        progressHeartbeatInterval.start();
        QTimer progressHeartbeat;
        progressHeartbeat.setTimerType(Qt::PreciseTimer);
        progressHeartbeat.setInterval(1);
        connect(&progressHeartbeat, &QTimer::timeout, [&] {
            maximumProgressHeartbeatGap =
                std::max(maximumProgressHeartbeatGap, progressHeartbeatInterval.restart());
        });
        progressHeartbeat.start();
        {
            Base::SequencerLauncher outerProgress("Opening a large document", 1);
            Base::SequencerLauncher progress("Restoring nested document presentation", 10000);
            for (int step = 0; step < 300; ++step) {
                QThread::msleep(1);
                progress.next();
            }
        }
        QCoreApplication::processEvents();
        progressHeartbeat.stop();
        QVERIFY(maximumProgressHeartbeatGap > 0);
        QVERIFY2(
            maximumProgressHeartbeatGap < 50,
            qPrintable(
                QStringLiteral("Progress scheduling starved the GUI event loop for %1 ms")
                    .arg(maximumProgressHeartbeatGap)
            )
        );

        // A single human action can change hundreds of Visibility properties
        // (for example, Assembly hides every joint at drag start). Folder
        // aggregates are a document projection and must be coalesced rather
        // than recursively traversing the complete model once per object.
        constexpr int visibilityBurstCount = 300;
        QElapsedTimer visibilityBurst;
        visibilityBurst.start();
        for (int index = 0; index < visibilityBurstCount; ++index) {
            projectionObjects[index]->Visibility.setValue(false);
        }
        QVERIFY2(
            visibilityBurst.elapsed() < 100,
            qPrintable(
                QStringLiteral("Bulk visibility synchronously blocked the GUI thread for %1 ms")
                    .arg(visibilityBurst.elapsed())
            )
        );

        // Presentation work may finish asynchronously after the document view
        // exists. Keep that document's canvas quiet until the outermost update
        // completes, including views attached while the update is active.
        {
            Gui::MDIView existingView(guiDocument, &mainWindow);
            QVERIFY(existingView.updatesEnabled());

            document->beginPresentationUpdate();
            QVERIFY(!existingView.updatesEnabled());

            Gui::MDIView attachedDuringUpdate(guiDocument, &mainWindow);
            QVERIFY(!attachedDuringUpdate.updatesEnabled());

            document->beginPresentationUpdate();
            document->endPresentationUpdate();
            QVERIFY(!existingView.updatesEnabled());
            QVERIFY(!attachedDuringUpdate.updatesEnabled());

            document->endPresentationUpdate();
            QVERIFY(existingView.updatesEnabled());
            QVERIFY(attachedDuringUpdate.updatesEnabled());
        }

        QVERIFY(App::GetApplication().closeDocument(documentName.c_str()));
    }

    void sharedGuiAdoptionQueueYieldsBetweenReadyResults()
    {
        auto& guiApplication = *application;
        constexpr int resultCount = 20;
        int completed = 0;
        int completedAtHeartbeat = -1;

        for (int index = 0; index < resultCount; ++index) {
            // Exercise the App-to-Gui gateway used by asynchronous document
            // notifications, not only the dispatcher's direct entry point.
            App::MainThreadSignalConfig::invoke([&completed] {
                QThread::msleep(2);
                ++completed;
            }, false);
        }
        QTimer::singleShot(0, [&] { completedAtHeartbeat = completed; });

        QTRY_COMPARE_WITH_TIMEOUT(completed, resultCount, 5000);
        QVERIFY2(
            completedAtHeartbeat >= 0 && completedAtHeartbeat < resultCount,
            qPrintable(
                QStringLiteral("GUI adoption queue failed to yield; heartbeat observed at %1/%2")
                    .arg(completedAtHeartbeat)
                    .arg(resultCount)
            )
        );
    }

    void nestedPresentationSequenceResumesOnOwnerAndUnwinds()
    {
        std::vector<int> steps;
        int liveScopes = 0;
        const auto child = [&](int value) -> Gui::FrameSequence<int> {
            struct Scope {
                int& count;
                explicit Scope(int& count) : count(count) { ++count; }
                ~Scope() { --count; }
            } scope(liveScopes);
            steps.push_back(value);
            co_await std::suspend_always {};
            if (value == 2) { throw std::runtime_error("presentation failed"); }
            co_return value;
        };
        const auto parent = [&]() -> Gui::FrameSequence<> {
            if (co_await child(1) != 1) { throw std::runtime_error("wrong child result"); }
            try { co_await child(2); }
            catch (const std::runtime_error& error) {
                steps.push_back(std::string(error.what()) == "presentation failed" ? 3 : -1);
            }
        };
        auto sequence = parent();
        QVERIFY(sequence.advance());
        QCOMPARE(steps, std::vector<int> {1});
        QCOMPARE(liveScopes, 1);
        QVERIFY(sequence.advance());
        QCOMPARE(steps, (std::vector<int> {1, 2}));
        QCOMPARE(liveScopes, 1);
        QVERIFY(!sequence.advance());
        sequence.takeResult();
        QCOMPARE(steps, (std::vector<int> {1, 2, 3}));
        QCOMPARE(liveScopes, 0);
        {
            auto cancelled = parent();
            QVERIFY(cancelled.advance());
            QCOMPARE(liveScopes, 1);
        }
        QCOMPARE(liveScopes, 0); // Destroying the root unwinds its suspended child.
    }

    void workerConsoleRefreshDoesNotRunANestedGuiEventLoop()
    {
        Base::Console().enableRefresh(true);
        QCoreApplication::sendPostedEvents();

        const auto eventType = static_cast<QEvent::Type>(QEvent::registerEventType());
        PostedEventProbe probe(eventType);

        std::thread worker([] { Base::Console().refresh(); });
        worker.join();

        for (int index = 0; index < 32; ++index) {
            QCoreApplication::postEvent(&probe, new QEvent(eventType));
        }

        // Deliver only callbacks addressed to the application. A worker-side
        // console refresh must not recursively drain unrelated GUI work.
        QCoreApplication::sendPostedEvents(qApp, QEvent::MetaCall);
        QCOMPARE(probe.received, 0);
        QCoreApplication::removePostedEvents(&probe, eventType);
    }

    void blockingWorkerNotificationsShareTheGuiFrameBudget()
    {
        constexpr int notificationCount = 24;
        std::atomic<int> completed {0};
        std::atomic<int> failures {0};
        std::promise<void> start;
        const auto startSignal = start.get_future().share();
        std::vector<std::future<void>> workers;
        workers.reserve(notificationCount);

        for (int index = 0; index < notificationCount; ++index) {
            workers.push_back(std::async(std::launch::async, [&, startSignal] {
                startSignal.wait();
                const bool accepted = Gui::dispatchToGuiFrameAndWait([&] {
                    if (QThread::currentThread() != qApp->thread()) {
                        ++failures;
                    }
                    QThread::msleep(2);
                    ++completed;
                });
                if (!accepted) {
                    ++failures;
                }
            }));
        }

        start.set_value();
        QThread::msleep(20);
        int completedAtHeartbeat = -1;
        QTimer::singleShot(0, [&] { completedAtHeartbeat = completed.load(); });

        QTRY_COMPARE_WITH_TIMEOUT(completed.load(), notificationCount, 5000);
        for (auto& worker : workers) {
            worker.get();
        }
        QCOMPARE(failures.load(), 0);
        QVERIFY2(
            completedAtHeartbeat >= 0 && completedAtHeartbeat < notificationCount,
            qPrintable(
                QStringLiteral("Blocking worker notifications monopolized the GUI queue; "
                               "heartbeat observed at %1/%2")
                    .arg(completedAtHeartbeat)
                    .arg(notificationCount)
            )
        );
    }
    // Retire the real Tree after tests which need it, without exporting an
    // internal widget constructor solely for this Windows test executable.
    void destroysTreeWhileItsDocumentRemainsOpen()
    {
        QTreeWidget* tree = nullptr;
        for (auto* candidate : window->findChildren<QTreeWidget*>()) {
            if (candidate->inherits("Gui::TreeWidget")) { tree = candidate; break; }
        }
        QVERIFY(tree);
        auto& app = App::GetApplication();
        auto* document = app.newDocument("TreeWindowLifetime");
        const std::string name = document->getName();
        for (int index = 0; index < 50; ++index) {
            document->addObject("App::FeaturePython", "TeardownFeature");
        }
        QTRY_VERIFY_WITH_TIMEOUT(
            tree->findItems(QStringLiteral("TeardownFeature"),
                            Qt::MatchStartsWith | Qt::MatchRecursive).size() >= 50, 10000);
        delete tree;
        QCOMPARE(app.getDocument(name.c_str()), document);
        QTRY_VERIFY_WITH_TIMEOUT(document->isClosable(), 5000);
        QVERIFY(app.closeDocument(name.c_str()));
    }

    // Keep last: this exercises the application's actual terminal Qt signal,
    // not a resettable test-only dispatcher or a second event loop.
    void shutdownReleasesQueuedAdoptionAndItsWaiter()
    {
        bool executed = false;
        bool releasedOnOwner = false;
        bool acceptedDuringRelease = true;
        struct OwnerCapture {
            std::function<void()> release;
            ~OwnerCapture() { release(); }
        };
        auto capture = std::make_shared<OwnerCapture>();
        capture->release = [&] {
            releasedOnOwner = QThread::currentThread() == qApp->thread();
            acceptedDuringRelease = Gui::dispatchToGuiFrame([] {});
        };
        auto completion = std::make_shared<std::promise<void>>();
        auto waiting = completion->get_future();
        QVERIFY(Gui::dispatchToGuiFrame(
            [capture = std::move(capture), completion = std::move(completion), &executed] {
                executed = true;
                completion->set_value();
            }));
        QVERIFY(QMetaObject::invokeMethod(qApp, "aboutToQuit", Qt::DirectConnection));
        QCOMPARE(waiting.wait_for(std::chrono::milliseconds(0)), std::future_status::ready);
        QVERIFY_EXCEPTION_THROWN(waiting.get(), std::future_error);
        QVERIFY(releasedOnOwner);
        QVERIFY(!executed);
        QVERIFY(!acceptedDuringRelease);
        QVERIFY(!Gui::dispatchToGuiFrame([] {}));
    }
};

QTEST_MAIN(DocumentBulkMutationTest)

#include "DocumentBulkMutation.moc"
