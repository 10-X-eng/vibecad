// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <memory>
#include <string>

#include <Inventor/SoDB.h>
#include <Inventor/nodes/SoSeparator.h>
#include <src/App/InitApplication.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Gui/Application.h>
#include <Gui/ViewProviderDocumentObject.h>

namespace
{

std::unique_ptr<Gui::Application> guiApplication;

class ViewProviderTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
        if (Gui::ViewProviderDocumentObject::getClassTypeId().isBad()) {
            Gui::Application::initApplication();
        }
        if (!SoDB::isInitialized()) {
            Gui::Application::initOpenInventor();
        }
        if (!Gui::Application::Instance) {
            guiApplication = std::make_unique<Gui::Application>(true);
        }
    }

    void SetUp() override
    {
        App::DocumentInitFlags flags;
        flags.createView = false;
        documentName = App::GetApplication().getUniqueDocumentName("temporary_visibility_test");
        document = App::GetApplication().newDocument(documentName.c_str(), "testUser", flags);
        object = document->addObject("App::FeaturePython", "Feature");
    }

    void TearDown() override
    {
        if (App::GetApplication().getDocument(documentName.c_str())) {
            App::GetApplication().closeDocument(documentName.c_str());
        }
    }

    std::string documentName;
    App::Document* document {};
    App::DocumentObject* object {};
};

}  // namespace

TEST_F(ViewProviderTest, temporaryVisibilityDoesNotChangePersistentVisibility)
{
    Gui::ViewProviderDocumentObject provider;
    provider.attach(object);
    provider.addDisplayMaskMode(new SoSeparator(), "Test");
    provider.setDisplayMaskMode("Test");

    provider.Visibility.setValue(true);
    provider.setTemporaryVisibility(false);
    EXPECT_FALSE(provider.isShow());
    EXPECT_TRUE(provider.Visibility.getValue());

    provider.setTemporaryVisibility(true);
    EXPECT_TRUE(provider.isShow());
    EXPECT_TRUE(provider.Visibility.getValue());

    provider.Visibility.setValue(false);
    provider.setTemporaryVisibility(true);
    EXPECT_TRUE(provider.isShow());
    EXPECT_FALSE(provider.Visibility.getValue());

    provider.setTemporaryVisibility(false);
    EXPECT_FALSE(provider.isShow());
    EXPECT_FALSE(provider.Visibility.getValue());
}
