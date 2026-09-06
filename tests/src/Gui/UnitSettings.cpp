// SPDX-License-Identifier: LGPL-2.1-or-later

#include <memory>
#include <string>

#include <QCheckBox>
#include <QComboBox>
#include <QTest>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Parameter.h>
#include <Base/UnitsApi.h>
#include <Gui/Application.h>
#include <Gui/MainWindow.h>
#include <src/App/InitApplication.h>

#include <Gui/PreferencePages/DlgSettingsGeneral.h>

class UnitSettingsTest: public QObject
{
    Q_OBJECT

private Q_SLOTS:
    void initTestCase()
    {
        tests::initApplication();
        Gui::Application::initApplication();
        Gui::Application::initOpenInventor();
        guiApplication = std::make_unique<Gui::Application>(true);
        mainWindow = std::make_unique<Gui::MainWindow>();
    }

    void cleanup()
    {
        if (!documentName.empty()
            && App::GetApplication().getDocument(documentName.c_str())) {
            App::GetApplication().closeDocument(documentName.c_str());
            documentName.clear();
        }
        preferences()->SetInt("UserSchema", originalSchema);
        preferences()->SetBool("IgnoreProjectSchema", originalIgnoreProjectSchema);
        Base::UnitsApi::setSchema(originalSchema);
    }

    void cleanupTestCase()
    {
        // FreeCAD's GUI singleton is process-scoped; leave it alive until the
        // test process exits rather than tearing it down after QTest.
        mainWindow.release();
        guiApplication.release();
    }

    void savingUnitPreferences_data()
    {
        QTest::addColumn<int>("projectSchema");
        QTest::addColumn<int>("selectedSchema");
        QTest::addColumn<bool>("initialIgnore");
        QTest::addColumn<bool>("ignoreProject");
        QTest::addColumn<int>("expectedProjectSchema");
        QTest::addColumn<int>("expectedViewSchema");

        QTest::newRow("change-open-document") << 0 << 3 << false << false << 3 << 3;
        QTest::newRow("unchanged-preference") << 3 << 0 << false << false << 3 << 3;
        QTest::newRow("ignore-project") << 0 << 3 << false << true << 0 << 3;
        QTest::newRow("ignore-project-unchanged") << 3 << 0 << true << true << 3 << 0;
        QTest::newRow("restore-project-units") << 3 << 0 << true << false << 3 << 3;
        QTest::newRow("change-and-restore-project-units") << 0 << 3 << true << false << 3 << 3;
        QTest::newRow("no-document") << -1 << 3 << false << false << -1 << 3;
    }

    void savingUnitPreferences()
    {
        QFETCH(int, projectSchema);
        QFETCH(int, selectedSchema);
        QFETCH(bool, initialIgnore);
        QFETCH(bool, ignoreProject);
        QFETCH(int, expectedProjectSchema);
        QFETCH(int, expectedViewSchema);
        auto units = preferences();
        originalSchema = units->GetInt("UserSchema", 0);
        originalIgnoreProjectSchema = units->GetBool("IgnoreProjectSchema", false);
        units->SetInt("UserSchema", 0);
        units->SetBool("IgnoreProjectSchema", initialIgnore);
        Base::UnitsApi::setSchema(0);

        App::Document* document = nullptr;
        if (projectSchema >= 0) {
            documentName = App::GetApplication().getUniqueDocumentName("unit_settings_test");
            document = App::GetApplication().newDocument(documentName.c_str());
            QVERIFY(document != nullptr);
            document->UnitSystem.setValue(static_cast<long>(projectSchema));

            auto* guiDocument = guiApplication->getDocument(document);
            QVERIFY(guiDocument != nullptr);
            guiApplication->setActiveDocument(guiDocument);
        }

        Gui::Dialog::DlgSettingsGeneral dialog;
        dialog.loadSettings();
        auto* unitSystem = dialog.findChild<QComboBox*>(QStringLiteral("comboBox_UnitSystem"));
        QVERIFY(unitSystem != nullptr);
        QVERIFY(unitSystem->count() > selectedSchema);
        unitSystem->setCurrentIndex(selectedSchema);
        auto* ignore = dialog.findChild<QCheckBox*>(
            QStringLiteral("checkBox_projectUnitSystemIgnore")
        );
        QVERIFY(ignore != nullptr);
        ignore->setChecked(ignoreProject);

        dialog.saveSettings();

        if (document) {
            QCOMPARE(document->UnitSystem.getValue(), expectedProjectSchema);
        }
        QCOMPARE(units->GetInt("UserSchema", 0), selectedSchema);
        QCOMPARE(units->GetBool("IgnoreProjectSchema", false), ignoreProject);
        const auto translated = Base::UnitsApi::schemaTranslate(Base::Quantity(25.4, "mm"));
        QCOMPARE(
            QString::fromStdString(translated).contains(QStringLiteral("in")),
            expectedViewSchema == 3
        );
    }

private:
    ParameterGrp::handle preferences() const
    {
        return App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/Units"
        );
    }

    std::unique_ptr<Gui::Application> guiApplication;
    std::unique_ptr<Gui::MainWindow> mainWindow;
    std::string documentName;
    int originalSchema {0};
    bool originalIgnoreProjectSchema {false};
};

QTEST_MAIN(UnitSettingsTest)

#include "UnitSettings.moc"
