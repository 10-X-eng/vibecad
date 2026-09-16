// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <chrono>
#include <future>

#include "App/Application.h"
#include "App/Document.h"
#include "App/DocumentTimeline.h"
#include "App/PropertyLinks.h"
#include "App/PropertyStandard.h"
#include "App/GroupExtension.h"
#include "App/OriginGroupExtension.h"
#include "App/Origin.h"
#include "App/HostRuntime.h"
#include "App/GeoFeature.h"
#include "Gui/ModelTreeBrowser.h"
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

TEST_F(DocumentProjectionTest, BrowserGraphSurvivesDisplayChangesButRejectsStructuralEdits)
{
    auto* object = static_cast<App::GeoFeature*>(
        _document->addObject("App::GeoFeature", "MovingPart"));
    const Gui::ModelTreeBrowserProjection snapshot(_document);
    const auto fullRevision = _document->getObjectChangeGeneration();
    for (int index = 0; index < 50; ++index) {
        object->Visibility.setValue(index % 2 == 0);
        object->Placement.setValue(Base::Placement(
            Base::Vector3d(index, 0, 0), Base::Rotation()));
        EXPECT_TRUE(snapshot.isCurrent());
    }
    EXPECT_GT(_document->getObjectChangeGeneration(), fullRevision);

    auto* metadata = static_cast<App::PropertyString*>(
        object->addDynamicProperty("App::PropertyString", "VibeCADTreeRole"));
    EXPECT_FALSE(snapshot.isCurrent());
    const Gui::ModelTreeBrowserProjection withProperty(_document);
    metadata->setValue("meshes");
    EXPECT_FALSE(withProperty.isCurrent());

    auto* group = _document->addObject("App::DocumentObjectGroup", "Group");
    const Gui::ModelTreeBrowserProjection beforeLink(_document);
    group->getExtensionByType<App::GroupExtension>()->addObject(object);
    EXPECT_FALSE(beforeLink.isCurrent());
    const Gui::ModelTreeBrowserProjection beforeDelete(_document);
    _document->removeObject(object->getNameInDocument());
    EXPECT_FALSE(beforeDelete.isCurrent());
}

TEST_F(DocumentProjectionTest, BrowserPreparationDoesNotReadLiveObjectsOnWorker)
{
    auto* group = _document->addObject("App::DocumentObjectGroup", "Group");
    auto* member = _document->addObject("App::FeaturePython", "Member");
    group->getExtensionByType<App::GroupExtension>()->addObject(member);
    auto* role = dynamic_cast<App::PropertyString*>(
        member->getPropertyByName(App::DocumentTimeline::RolePropertyName));
    if (!role) {
        role = static_cast<App::PropertyString*>(member->addDynamicProperty(
            "App::PropertyString", App::DocumentTimeline::RolePropertyName));
    }
    auto* owner = dynamic_cast<App::PropertyLink*>(
        member->getPropertyByName(App::DocumentTimeline::OwnerPropertyName));
    if (!owner) {
        owner = static_cast<App::PropertyLink*>(member->addDynamicProperty(
            "App::PropertyLinkHidden", App::DocumentTimeline::OwnerPropertyName));
    }
    ASSERT_NE(role, nullptr);
    ASSERT_NE(owner, nullptr);
    role->setValue(App::DocumentTimeline::ResourceRole);
    owner->setValue(group);
    Gui::ModelTreeBrowserProjection::Preparation preparation(_document);
    while (!preparation.captureNext()) {}
    EXPECT_TRUE(preparation.isCurrent());

    // Invalidate the source after capture. Preparation must use its immutable
    // graph, not consult the live Group property during worker classification.
    group->getExtensionByType<App::GroupExtension>()->removeObject(member);
    owner->setValue(nullptr);
    EXPECT_FALSE(preparation.isCurrent());
    auto& runtime = App::GetApplication().hostRuntime();
    auto computed = runtime.submit(
        [preparation = std::move(preparation), &runtime](std::stop_token) mutable {
            return std::move(preparation).finish(&runtime);
        });
    const auto snapshot = runtime.wait(computed);
    EXPECT_FALSE(snapshot.isCurrent());
    ASSERT_NE(snapshot.find(member), nullptr);
    EXPECT_EQ(snapshot.find(member)->group, group);
    EXPECT_EQ(snapshot.find(member)->logicalParent, group);
    EXPECT_EQ(snapshot.timelineRoots().at(member), group);
    const Gui::ModelTreeBrowserProjection current(_document);
    EXPECT_TRUE(current.isCurrent());
    ASSERT_NE(current.find(member), nullptr);
    EXPECT_EQ(current.find(member)->group, nullptr);
    EXPECT_EQ(current.timelineRoots().at(member), nullptr);
}

TEST_F(DocumentProjectionTest, CapturedParentGraphPreservesPartGroupAndOriginOwnership)
{
    auto* part = _document->addObject("App::Part", "Component");
    auto* group = _document->addObject("App::DocumentObjectGroup", "Organization");
    auto* member = _document->addObject("App::GeoFeature", "Member");
    auto* component = part->getExtensionByType<App::OriginGroupExtension>();
    component->addObject(group);
    group->getExtensionByType<App::GroupExtension>()->addObject(member);
    ASSERT_EQ(App::GeoFeatureGroupExtension::getGroupOfObject(group), part);
    auto* origin = component->getOrigin();
    ASSERT_FALSE(origin->OriginFeatures.getValues().empty());
    auto* datum = origin->OriginFeatures.getValues().front();
    ASSERT_NE(datum, nullptr);

    Gui::ModelTreeBrowserProjection::Preparation preparation(_document);
    while (!preparation.captureNext()) {}
    // Change the live relationships after capture. Worker parent resolution
    // must retain the captured graph, not call the live extension methods.
    group->getExtensionByType<App::GroupExtension>()->removeObject(member);
    auto& runtime = App::GetApplication().hostRuntime();
    auto ready = runtime.submit([input = std::move(preparation), &runtime](std::stop_token) mutable {
        return std::move(input).finish(&runtime);
    });
    const auto projection = runtime.wait(ready);
    ASSERT_NE(projection.find(member), nullptr);
    EXPECT_EQ(projection.find(member)->group, group);
    EXPECT_EQ(projection.find(member)->component, part);
    ASSERT_NE(projection.find(origin), nullptr);
    EXPECT_EQ(projection.find(origin)->component, part);
    ASSERT_NE(projection.find(datum), nullptr);
    EXPECT_EQ(projection.find(datum)->logicalParent, origin);
    EXPECT_EQ(projection.find(datum)->role, Gui::ModelTreeBrowserProjection::Role::OriginFeature);
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

TEST_F(DocumentProjectionTest, PresentationCompletionIncludesStableObserverWork)
{
    using namespace std::chrono_literals;

    ASSERT_NE(_document, nullptr);
    auto connection = _document->signalBecameStable.connect(
        [this](const App::Document&) { _document->beginPresentationUpdate(); }
    );

    _document->beginCooperativeMutation();
    _document->endCooperativeMutation();

    EXPECT_TRUE(_document->isPresentationUpdateActive());
    EXPECT_FALSE(_document->isClosable());
    auto completion = std::async(std::launch::async, [this] {
        _document->waitForPresentationReady();
    });
    EXPECT_EQ(completion.wait_for(20ms), std::future_status::timeout);

    _document->endPresentationUpdate();
    EXPECT_EQ(completion.wait_for(1s), std::future_status::ready);
    completion.get();
    EXPECT_FALSE(_document->isPresentationUpdateActive());
    EXPECT_TRUE(_document->isClosable());
}
