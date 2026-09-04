// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include "App/Application.h"
#include "App/Document.h"
#include "Gui/Document.h"
#include <src/App/InitApplication.h>

class DocumentProjectionTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }

    void SetUp() override
    {
        _documentName = App::GetApplication().getUniqueDocumentName("projection_refresh");
        _document = App::GetApplication().newDocument(_documentName.c_str(), "testUser");
    }

    void TearDown() override
    {
        if (_document && App::GetApplication().getDocument(_documentName.c_str())) {
            App::GetApplication().closeDocument(_documentName.c_str());
        }
    }

    std::string _documentName;
    App::Document* _document {};
};

TEST_F(DocumentProjectionTest, NormalTransactionKeepsIncrementalProjectionLive)
{
    ASSERT_NE(_document, nullptr);
    EXPECT_FALSE(Gui::Document::projectionRefreshBlocked(_document));
    EXPECT_FALSE(Gui::Document::historyMutationBlocked(_document));

    _document->openTransaction("test projection batching");
    EXPECT_FALSE(Gui::Document::projectionRefreshBlocked(_document));
    EXPECT_TRUE(Gui::Document::historyMutationBlocked(_document));

    _document->commitTransaction();
    EXPECT_FALSE(Gui::Document::projectionRefreshBlocked(_document));
    EXPECT_FALSE(Gui::Document::historyMutationBlocked(_document));
}

TEST_F(DocumentProjectionTest, NormalEditLockKeepsIncrementalProjectionLive)
{
    ASSERT_NE(_document, nullptr);
    _document->lockTransaction();
    EXPECT_FALSE(Gui::Document::projectionRefreshBlocked(_document));
    EXPECT_TRUE(Gui::Document::historyMutationBlocked(_document));

    _document->unlockTransaction();
    EXPECT_FALSE(Gui::Document::projectionRefreshBlocked(_document));
    EXPECT_FALSE(Gui::Document::historyMutationBlocked(_document));
}

TEST_F(DocumentProjectionTest, CooperativeMutationDefersProjectionUntilOutermostEnd)
{
    ASSERT_NE(_document, nullptr);
    int transitions = 0;
    bool lastActive = false;
    auto connection = _document->signalCooperativeMutationChanged.connect(
        [&transitions, &lastActive](const App::Document&, bool active) {
            ++transitions;
            lastActive = active;
        }
    );

    _document->beginCooperativeMutation();
    _document->beginCooperativeMutation();
    EXPECT_TRUE(_document->isCooperativeMutationActive());
    EXPECT_TRUE(Gui::Document::projectionRefreshBlocked(_document));
    EXPECT_TRUE(Gui::Document::historyMutationBlocked(_document));
    EXPECT_FALSE(_document->isClosable());
    EXPECT_EQ(transitions, 1);
    EXPECT_TRUE(lastActive);

    _document->endCooperativeMutation();
    EXPECT_TRUE(_document->isCooperativeMutationActive());
    EXPECT_EQ(transitions, 1);

    _document->endCooperativeMutation();
    EXPECT_FALSE(_document->isCooperativeMutationActive());
    EXPECT_FALSE(Gui::Document::projectionRefreshBlocked(_document));
    EXPECT_TRUE(_document->isClosable());
    EXPECT_EQ(transitions, 2);
    EXPECT_FALSE(lastActive);
}

TEST_F(DocumentProjectionTest, CooperativeMutationRefusesDocumentClose)
{
    ASSERT_NE(_document, nullptr);
    _document->beginCooperativeMutation();

    EXPECT_FALSE(App::GetApplication().closeDocument(_documentName.c_str()));
    EXPECT_EQ(App::GetApplication().getDocument(_documentName.c_str()), _document);

    _document->endCooperativeMutation();
}

TEST_F(DocumentProjectionTest, CooperativeMutationRefusesUndoUntilPublicationEnds)
{
    ASSERT_NE(_document, nullptr);
    _document->setUndoMode(1);
    _document->openTransaction("create protected object");
    auto* protectedObject = _document->addObject("App::FeaturePython", "ProtectedObject");
    ASSERT_NE(protectedObject, nullptr);
    _document->commitTransaction();
    ASSERT_GT(_document->getAvailableUndos(), 0);

    _document->beginCooperativeMutation();
    EXPECT_FALSE(_document->undo());
    EXPECT_EQ(_document->getObject("ProtectedObject"), protectedObject);
    EXPECT_GT(_document->getAvailableUndos(), 0);

    _document->endCooperativeMutation();
    EXPECT_TRUE(_document->undo());
    EXPECT_EQ(_document->getObject("ProtectedObject"), nullptr);
}
