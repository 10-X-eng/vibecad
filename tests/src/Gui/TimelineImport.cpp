// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <src/App/InitApplication.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Gui/TimelineImport.h>

namespace
{

class TimelineImportTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }

    void SetUp() override
    {
        App::DocumentInitFlags flags;
        flags.createView = false;
        documentName = App::GetApplication().getUniqueDocumentName("timeline_import_test");
        document = App::GetApplication().newDocument(documentName.c_str(), "testUser", flags);
    }

    void TearDown() override
    {
        if (App::GetApplication().getDocument(documentName.c_str())) {
            App::GetApplication().closeDocument(documentName.c_str());
        }
    }

    std::string documentName;
    App::Document* document {};
};

}  // namespace

TEST_F(TimelineImportTest, partialExportCarriesAcceptedStateFromRolledBackSource)
{
    auto* operation = document->addObject(
        "App::FeaturePython",
        "FutureOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* timeline = App::DocumentTimeline::get(document);
    ASSERT_NE(timeline, nullptr);
    document->commitTransaction();

    timeline->beginApplying();
    timeline->VisibilityAtEnd.setValues(boost::dynamic_bitset<>(1, 1));
    timeline->SuppressionAtEnd.setValues(boost::dynamic_bitset<>(1, 0));
    timeline->Position.setValue(0);
    operation->Visibility.setValue(false);
    timeline->endApplying();

    const auto plan = Gui::prepareTimelineExport({operation}, false);

    ASSERT_EQ(plan.sourceOrderNames.size(), 1);
    ASSERT_EQ(plan.sourceVisibility.size(), 1);
    ASSERT_EQ(plan.sourceSuppression.size(), 1);
    EXPECT_EQ(plan.sourceOrderNames.front(), operation->getExportName(true));
    EXPECT_TRUE(plan.sourceVisibility.front());
    EXPECT_FALSE(plan.sourceSuppression.front());
}

TEST_F(TimelineImportTest, sourceDeletionRemovesLegacyRootFirstSemanticResources)
{
    auto* operation = document->addObject(
        "App::FeaturePython",
        "LegacyRoot",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* operationRole = static_cast<App::PropertyString*>(
        operation->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
    );
    operationRole->setValue(App::DocumentTimeline::OperationRole);

    auto* resource = document->addObject(
        "App::FeaturePython",
        "LegacyResource",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* owner = static_cast<App::PropertyLinkHidden*>(
        resource->addDynamicProperty("App::PropertyLinkHidden", App::DocumentTimeline::OwnerPropertyName)
    );
    owner->setValue(operation);
    auto* resourceRole = static_cast<App::PropertyString*>(
        resource->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
    );
    resourceRole->setValue(App::DocumentTimeline::ResourceRole);

    auto* timeline = App::DocumentTimeline::get(document);
    ASSERT_NE(timeline, nullptr);
    ASSERT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(operation, resource));
    const auto source = Gui::prepareTimelineExport({operation}, false);
    ASSERT_EQ(source.objects.size(), 2);

    document->openTransaction("Delete moved semantic source");
    Gui::deleteTimelineExportSource(source);
    document->commitTransaction();

    EXPECT_EQ(document->getObject("LegacyRoot"), nullptr);
    EXPECT_EQ(document->getObject("LegacyResource"), nullptr);
    EXPECT_TRUE(timeline->Operations.getValues().empty());
}
