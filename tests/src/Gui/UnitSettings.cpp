// SPDX-License-Identifier: LGPL-2.1-or-later

#include <memory>
#include <string>

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

    void changingDefaultUnitsUpdatesAnOpenDocument()
    {
        auto units = preferences();
        originalSchema = units->GetInt("UserSchema", 0);
        originalIgnoreProjectSchema = units->GetBool("IgnoreProjectSchema", false);
        units->SetInt("UserSchema", 0);
        units->SetBool("IgnoreProjectSchema", false);
        Base::UnitsApi::setSchema(0);

        documentName = App::GetApplication().getUniqueDocumentName("unit_settings_test");
        auto* document = App::GetApplication().newDocument(documentName.c_str());
        QVERIFY(document != nullptr);
        document->UnitSystem.setValue(0L);

        auto* guiDocument = guiApplication->getDocument(document);
        QVERIFY(guiDocument != nullptr);
        guiApplication->setActiveDocument(guiDocument);

        Gui::Dialog::DlgSettingsGeneral dialog;
        dialog.loadSettings();
        auto* unitSystem = dialog.findChild<QComboBox*>(QStringLiteral("comboBox_UnitSystem"));
        QVERIFY(unitSystem != nullptr);
        QVERIFY(unitSystem->count() > 1);
        unitSystem->setCurrentIndex(3);

        dialog.saveSettings();

        QCOMPARE(document->UnitSystem.getValue(), 3);
        QCOMPARE(units->GetInt("UserSchema", 0), 3);
        const auto translated = Base::UnitsApi::schemaTranslate(Base::Quantity(25.4, "mm"));
        QVERIFY(QString::fromStdString(translated).contains(QStringLiteral("in")));
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
