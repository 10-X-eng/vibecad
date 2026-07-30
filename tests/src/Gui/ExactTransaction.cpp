// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <src/App/InitApplication.h>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Exception.h>
#include <Gui/ExactTransaction.h>

namespace
{

class ExactTransactionTest: public ::testing::Test
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
        firstName = App::GetApplication().getUniqueDocumentName("exact_transaction_first");
        secondName = App::GetApplication().getUniqueDocumentName("exact_transaction_second");
        first = App::GetApplication().newDocument(firstName.c_str(), "testUser", flags);
        second = App::GetApplication().newDocument(secondName.c_str(), "testUser", flags);
    }

    void TearDown() override
    {
        if (App::GetApplication().getDocument(firstName.c_str())) {
            App::GetApplication().closeDocument(firstName.c_str());
        }
        if (App::GetApplication().getDocument(secondName.c_str())) {
            App::GetApplication().closeDocument(secondName.c_str());
        }
    }

    std::string firstName;
    std::string secondName;
    App::Document* first {};
    App::Document* second {};
};

}  // namespace

TEST_F(ExactTransactionTest, commitsOneExactIdentityAcrossDocuments)
{
    first->setUndoMode(0);
    second->setUndoMode(0);

    Gui::ExactTransaction transaction(
        std::vector<App::Document*> {first, second},
        "Cross-document operation"
    );
    ASSERT_TRUE(transaction.ownsCurrentTransaction());
    const int transactionId = transaction.id();
    ASSERT_NE(transactionId, App::NullTransaction);
    EXPECT_EQ(first->getBookedTransactionID(), transactionId);
    EXPECT_EQ(second->getBookedTransactionID(), transactionId);

    first->addObject("App::FeaturePython", "FirstResult");
    second->addObject("App::FeaturePython", "SecondResult");
    ASSERT_TRUE(transaction.commit());

    EXPECT_TRUE(transaction.isClosed());
    EXPECT_EQ(first->getBookedTransactionID(), App::NullTransaction);
    EXPECT_EQ(second->getBookedTransactionID(), App::NullTransaction);
    EXPECT_EQ(App::GetApplication().getGlobalTransaction(), App::NullTransaction);
    EXPECT_NE(first->getObject("FirstResult"), nullptr);
    EXPECT_NE(second->getObject("SecondResult"), nullptr);
    EXPECT_EQ(first->getUndoMode(), 0);
    EXPECT_EQ(second->getUndoMode(), 0);
}

TEST_F(ExactTransactionTest, initiatorTracksDependentOnlyMutationForGroupedUndo)
{
    first->setUndoMode(1);
    second->setUndoMode(1);

    Gui::ExactTransaction transaction(
        *first,
        std::vector<App::Document*> {first, second},
        "Edit linked definition"
    );
    ASSERT_TRUE(transaction.ownsCurrentTransaction());
    const int transactionId = transaction.id();
    ASSERT_NE(transactionId, App::NullTransaction);

    second->addObject("App::FeaturePython", "DefinitionResult");
    ASSERT_TRUE(first->hasPendingTransaction());
    ASSERT_TRUE(second->hasPendingTransaction());
    ASSERT_TRUE(transaction.commit());

    EXPECT_EQ(first->getTransactionID(true), transactionId);
    EXPECT_EQ(second->getTransactionID(true), transactionId);
    EXPECT_NE(second->getObject("DefinitionResult"), nullptr);

    EXPECT_TRUE(second->undo(transactionId));
    EXPECT_TRUE(first->undo(transactionId));
    EXPECT_EQ(second->getObject("DefinitionResult"), nullptr);

    EXPECT_TRUE(first->redo(transactionId));
    EXPECT_TRUE(second->redo(transactionId));
    EXPECT_NE(second->getObject("DefinitionResult"), nullptr);
}

TEST_F(ExactTransactionTest, documentCloseRequestRollsBackAndReleasesOwnedLocks)
{
    Gui::ExactTransaction transaction(
        *first,
        std::vector<App::Document*> {first, second},
        "Edit linked definition"
    );
    second->addObject("App::FeaturePython", "ProvisionalDefinitionResult");
    ASSERT_TRUE(first->isTransactionLocked());
    ASSERT_TRUE(second->isTransactionLocked());

    EXPECT_TRUE(App::GetApplication().closeDocument(firstName.c_str()));
    EXPECT_EQ(App::GetApplication().getDocument(firstName.c_str()), nullptr);
    EXPECT_TRUE(transaction.isClosed());
    EXPECT_EQ(second->getObject("ProvisionalDefinitionResult"), nullptr);
    EXPECT_EQ(second->getBookedTransactionID(), App::NullTransaction);
    EXPECT_FALSE(second->isTransactionLocked());
}

TEST_F(ExactTransactionTest, abortsEveryDocumentWithoutAnUndoPreference)
{
    first->setUndoMode(0);
    second->setUndoMode(0);

    Gui::ExactTransaction transaction(
        std::vector<App::Document*> {first, second},
        "Rejected cross-document operation"
    );
    first->addObject("App::FeaturePython", "FirstRejected");
    second->addObject("App::FeaturePython", "SecondRejected");
    ASSERT_TRUE(transaction.abort());

    EXPECT_EQ(first->getObject("FirstRejected"), nullptr);
    EXPECT_EQ(second->getObject("SecondRejected"), nullptr);
    EXPECT_EQ(first->getBookedTransactionID(), App::NullTransaction);
    EXPECT_EQ(second->getBookedTransactionID(), App::NullTransaction);
    EXPECT_EQ(App::GetApplication().getGlobalTransaction(), App::NullTransaction);
    EXPECT_EQ(first->getUndoMode(), 0);
    EXPECT_EQ(second->getUndoMode(), 0);
}

TEST_F(ExactTransactionTest, refusesATransactionAlreadyOwnedByAnotherCaller)
{
    const int callerTransaction = first->openTransaction("Caller-owned transaction");
    ASSERT_NE(callerTransaction, App::NullTransaction);

    EXPECT_THROW(
        Gui::ExactTransaction(std::vector<App::Document*> {first, second}, "Conflicting operation"),
        Base::RuntimeError
    );
    EXPECT_EQ(first->getBookedTransactionID(), callerTransaction);
    EXPECT_EQ(second->getBookedTransactionID(), App::NullTransaction);
    EXPECT_TRUE(App::GetApplication().transactionIsActive(callerTransaction));
    EXPECT_TRUE(App::GetApplication().abortTransaction(callerTransaction));
}
