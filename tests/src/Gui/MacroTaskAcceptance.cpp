// SPDX-License-Identifier: LGPL-2.1-or-later

#include <stdexcept>

#include <QTest>

#include <src/App/InitApplication.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/Macro.h>
#include <Gui/MainWindow.h>
#include <Gui/TaskView/TaskDialog.h>
#include <Gui/TaskView/TaskDialogPython.h>
#include <Gui/TaskView/TaskView.h>

namespace
{

class ExposedTaskView: public Gui::TaskView::TaskView
{
public:
    using Gui::TaskView::TaskView::TaskView;

    bool install(
        Gui::TaskView::TaskDialog* dialog,
        App::Document* document
    )
    {
        return showDialog(dialog, document);
    }

    void acceptDocument(App::Document* document)
    {
        accept(document);
    }
};

class DurableAcceptDialog: public Gui::TaskView::TaskDialog
{
public:
    explicit DurableAcceptDialog(App::DocumentObject* result)
        : result(result)
    {}

    bool accept() override
    {
        result->Label.setValue("Accepted result");
        Gui::Application::Instance->macroManager()->addLine(
            Gui::MacroManager::App,
            "App.ActiveDocument.recompute()"
        );
        return true;
    }

private:
    App::DocumentObject* result;
};

}  // namespace

class MacroTaskAcceptanceTest: public QObject
{
    Q_OBJECT

private Q_SLOTS:
    void initTestCase()
    {
        tests::initApplication();
    }

    void throwingOuterRedirectCannotUndoAcceptedGeometry()
    {
        Gui::Application::initApplication();
        Gui::Application::initOpenInventor();
        Gui::Application guiApplication(true);
        Gui::MainWindow mainWindow;
        ExposedTaskView taskView(&mainWindow);

        const std::string documentName =
            App::GetApplication().getUniqueDocumentName(
                "macro_publication_failure"
            );
        auto* document = App::GetApplication().newDocument(
            documentName.c_str()
        );
        QVERIFY(document);
        auto* guiDocument = guiApplication.getDocument(document);
        QVERIFY(guiDocument);
        guiApplication.setActiveDocument(guiDocument);

        Gui::TaskView::TaskDialog::beginCommandInvocation();
        const int transactionId =
            document->openTransaction("Create durable test result");
        QVERIFY(transactionId != App::NullTransaction);
        auto* result = document->addObject(
            "App::FeaturePython",
            "DurableResult"
        );
        QVERIFY(result);
        result->Label.setValue("Provisional result");
        guiApplication.macroManager()->addLine(
            Gui::MacroManager::App,
            "App.ActiveDocument.addObject('App::FeaturePython',"
            "'DurableResult')"
        );

        auto* dialog = new DurableAcceptDialog(result);
        QVERIFY(taskView.install(dialog, document));
        Gui::TaskView::TaskDialog::endCommandInvocation();

        int publicationAttempts = 0;
        {
            Gui::MacroManager::MacroRedirector throwingRedirect(
                [&publicationAttempts](
                    Gui::MacroManager::LineType,
                    const char*
                ) {
                    ++publicationAttempts;
                    throw std::runtime_error(
                        "intentional macro publication failure"
                    );
                }
            );

            try {
                taskView.acceptDocument(document);
            }
            catch (const std::exception& error) {
                QFAIL(
                    qPrintable(
                        QStringLiteral(
                            "Accepted task propagated macro failure: %1"
                        ).arg(QString::fromUtf8(error.what()))
                    )
                );
            }
            catch (...) {
                QFAIL("Accepted task propagated an unknown macro failure");
            }
        }

        QVERIFY(publicationAttempts > 0);
        QCOMPARE(
            document->getBookedTransactionID(),
            App::NullTransaction
        );
        QVERIFY(!document->hasPendingTransaction());
        auto* durableResult = document->getObject("DurableResult");
        QVERIFY(durableResult);
        QCOMPARE(
            QString::fromUtf8(durableResult->Label.getValue()),
            QStringLiteral("Accepted result")
        );
        QVERIFY(taskView.dialog(document) == nullptr);

        App::GetApplication().closeDocument(documentName.c_str());
    }

    void pythonCallbackExceptionsDeclineTaskClosure()
    {
        Base::PyGILStateLocker lock;
        PyObject* main = PyImport_AddModule("__main__");
        QVERIFY(main);
        Py::Dict scope(PyDict_Copy(PyModule_GetDict(main)), true);
        PyObject* result = PyRun_String(
            "class ThrowingTask:\n"
            "    def accept(self):\n"
            "        raise RuntimeError('accept failed')\n"
            "    def reject(self):\n"
            "        raise RuntimeError('reject failed')\n"
            "task = ThrowingTask()\n",
            Py_file_input,
            scope.ptr(),
            scope.ptr()
        );
        if (!result) {
            PyErr_Print();
        }
        QVERIFY(result);
        Py_DECREF(result);

        Gui::TaskView::TaskDialogPython dialog(scope.getItem("task"));
        QVERIFY(!dialog.accept());
        QVERIFY(!dialog.reject());
    }
};

QTEST_MAIN(MacroTaskAcceptanceTest)

#include "MacroTaskAcceptance.moc"
