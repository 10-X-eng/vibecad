// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <FCConfig.h>

#include <algorithm>
#include <App/Application.h>
#include <App/Document.h>
#include <App/Expression.h>
#include <App/ObjectIdentifier.h>
#include <App/GeoFeature.h>
#include <App/PropertyStandard.h>
#include <App/PropertyLinks.h>
#include <chrono>
#include <thread>
#include <Mod/Assembly/App/AssemblyObject.h>
#include <Mod/Assembly/App/JointGroup.h>
#include <OndselSolver/ASMTPart.h>
#include <src/App/InitApplication.h>

class AssemblyObjectTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }

    void SetUp() override
    {
        _docName = App::GetApplication().getUniqueDocumentName("test");
        auto _doc = App::GetApplication().newDocument(_docName.c_str(), "testUser");
        _assemblyObj = _doc->addObject<Assembly::AssemblyObject>();
        _jointGroupObj = _assemblyObj->addObject<Assembly::JointGroup>("jointGroupTest");
    }

    void TearDown() override
    {
        App::GetApplication().closeDocument(_docName.c_str());
    }

    Assembly::AssemblyObject* getObject()
    {
        return _assemblyObj;
    }

private:
    // TODO: use shared_ptr or something else here?
    Assembly::AssemblyObject* _assemblyObj;
    Assembly::JointGroup* _jointGroupObj;
    std::string _docName;
};

TEST_F(AssemblyObjectTest, createAssemblyObject)  // NOLINT
{
    // Arrange

    // Act

    // Assert
}

TEST_F(AssemblyObjectTest, SolverStatusRefreshIsCoalescedDuringPublicationAndRollback)
{
    auto* assembly = getObject();
    auto* document = assembly->getDocument();
    auto* grounded = assembly->addObject<App::GeoFeature>("Grounded");
    grounded->Placement.setStatus(App::Property::ReadOnly, true);
    ASSERT_EQ(assembly->solve(), 0);
    const auto originalGroup = assembly->Group.getValues();
    const auto originalDoF = assembly->getLastDoF();
    document->setUndoMode(1);

    size_t updates = 0;
    bool updatedDuringReplay = false;
    fastsignals::scoped_connection connection = assembly->signalSolverUpdate.connect([&] {
        ++updates;
        updatedDuringReplay |= document->isPerformingTransaction();
    });
    document->openTransaction("Create components to roll back");
    for (size_t index = 0; index < 50; ++index) {
        assembly->addObject<App::GeoFeature>("TemporaryComponent");
    }
    updates = 0;
    document->abortTransaction();
    EXPECT_FALSE(updatedDuringReplay);
    EXPECT_EQ(updates, 1);
    EXPECT_EQ(assembly->Group.getValues(), originalGroup);
    EXPECT_EQ(assembly->getLastDoF(), originalDoF);

    updates = 0;
    document->beginCooperativeMutation();
    document->openTransaction("Publish component batch");
    for (size_t index = 0; index < 50; ++index) {
        assembly->addObject<App::GeoFeature>("PublishedComponent");
    }
    EXPECT_EQ(updates, 0);
    document->commitTransaction();
    EXPECT_EQ(updates, 0);
    document->endCooperativeMutation();
    EXPECT_EQ(updates, 1);
    EXPECT_EQ(assembly->getLastDoF(), originalDoF + 50 * 6);

    updates = 0;
    assembly->addObject<App::GeoFeature>("NormalComponent");
    EXPECT_EQ(updates, 1);  // Ordinary edits retain immediate status updates.
}

TEST_F(AssemblyObjectTest, DeferredStatusRefreshDoesNotStartAnImplicitSolve)
{
    auto* document = getObject()->getDocument();
    document->beginCooperativeMutation();
    auto* assembly = document->addObject<Assembly::AssemblyObject>("NewAssembly");
    auto* component = assembly->addObject<App::GeoFeature>("UngroundedComponent");
    ASSERT_EQ(assembly->getLastSolverStatus(), 0);
    document->endCooperativeMutation();
    // A status refresh after loading/publication is not a request to solve.
    // In particular it must not auto-ground a component as a solver side effect.
    EXPECT_EQ(assembly->getLastSolverStatus(), 0);
    EXPECT_TRUE(assembly->getLastSolverMessage().empty());
    EXPECT_FALSE(component->Placement.testStatus(App::Property::ReadOnly));
    EXPECT_EQ(assembly->getLastDoF(), 12);
}

TEST_F(AssemblyObjectTest, SimulationFramePlacementsSuppressTransientTouchState)
{
    auto* assembly = getObject();
    auto* component = assembly->addObject<App::GeoFeature>("AnimatedComponent");
    component->Placement.setStatus(App::Property::ReadOnly, true);
    auto* simulation = assembly->addObject<App::DocumentObject>("Simulation");
    const auto addFloat = [simulation](const char* name, double value) {
        static_cast<App::PropertyFloat*>(simulation->addDynamicProperty("App::PropertyFloat", name))
            ->setValue(value);
    };
    addFloat("aTimeStart", 0);
    addFloat("bTimeEnd", 0.1);
    addFloat("cTimeStepOutput", 0.1);
    addFloat("fGlobalErrorTolerance", 1e-6);
    addFloat("jFramesPerSecond", 30);
    const auto waitReady = [](const auto& poll) {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
        while (!poll()) {
            if (std::chrono::steady_clock::now() >= deadline) { return false; }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        return true;
    };
    const auto solve = assembly->startSimulation(simulation);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulation(solve); }));

    const bool wasApplying = assembly->setSimulationPresentation(true);
    component->Placement.setValue(
        Base::Placement(Base::Vector3d(17, 0, 0), Base::Rotation()));
    component->purgeTouched();
    assembly->setSimulationPresentation(wasApplying);

    const auto documentPlacement = component->Placement.getValue();
    const auto immediateFrame = assembly->getSimulationFrame(1);
    EXPECT_NE(
        std::ranges::find_if(immediateFrame, [&](const auto& item) {
            return item.first == component->getNameInDocument();
        }),
        immediateFrame.end()
    );
    EXPECT_TRUE(component->Placement.getValue().isSame(documentPlacement));

    const auto presentationRequest = assembly->requestSimulationFrame(1);
    std::optional<std::vector<std::pair<std::string, Base::Placement>>> presentationFrame;
    ASSERT_TRUE(waitReady([&] {
        presentationFrame = assembly->takeSimulationFrame(presentationRequest);
        return presentationFrame.has_value();
    }));
    EXPECT_NE(
        std::ranges::find_if(*presentationFrame, [&](const auto& item) {
            return item.first == component->getNameInDocument();
        }),
        presentationFrame->end()
    );
    EXPECT_TRUE(component->Placement.getValue().isSame(documentPlacement));

    size_t placementNotifications = 0;
    bool noTouchDuringNotification = true;
    fastsignals::scoped_connection connection =
        assembly->getDocument()->signalChangedObject.connect(
            [&](const App::DocumentObject& changed, const App::Property& property) {
                if (&changed != component || &property != changed.getPlacementProperty()) { return; }
                ++placementNotifications;
                noTouchDuringNotification &= changed.testStatus(App::ObjectStatus::NoTouch);
            });
    const auto frame = assembly->requestSimulationFrame(1);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulationFrame(frame); }));
    EXPECT_GT(placementNotifications, 0);
    EXPECT_TRUE(noTouchDuringNotification);
    EXPECT_FALSE(component->testStatus(App::ObjectStatus::NoTouch));
}

TEST_F(AssemblyObjectTest, DetachedSimulationAndFrameLifecycle)
{
    auto* assembly = getObject();
    std::vector<App::GeoFeature*> components;
    for (size_t index = 0; index < 50; ++index) {
        auto* component = assembly->addObject<App::GeoFeature>("Component");
        component->Placement.setValue(Base::Placement(
            Base::Vector3d(double(index) * 2, 0, 0), Base::Rotation()));
        component->Placement.setStatus(App::Property::ReadOnly, true);
        components.push_back(component);
    }
    const auto sourceDocumentName = App::GetApplication().getUniqueDocumentName("SimulationSource");
    auto* sourceDocument = App::GetApplication().newDocument(sourceDocumentName.c_str());
    const auto closeSource = [](App::Document* document) {
        App::GetApplication().closeDocument(document->getName());
    };
    std::unique_ptr<App::Document, decltype(closeSource)> sourceLifetime(sourceDocument, closeSource);
    auto* linkedSource = sourceDocument->addObject<App::GeoFeature>("Source");
    auto* sourceLink = static_cast<App::PropertyLink*>(components[0]->addDynamicProperty(
        "App::PropertyLinkGlobal", "SimulationSource"));
    sourceLink->setAllowExternal(true);
    sourceLink->setValue(linkedSource);
    auto* simulation = assembly->addObject<App::DocumentObject>("Simulation");
    const auto addFloat = [simulation](const char* name, double value) {
        static_cast<App::PropertyFloat*>(simulation->addDynamicProperty("App::PropertyFloat", name))->setValue(value);
    };
    addFloat("aTimeStart", 0);
    addFloat("bTimeEnd", 0.2);
    addFloat("cTimeStepOutput", 0.1);
    addFloat("fGlobalErrorTolerance", 1e-6);
    addFloat("jFramesPerSecond", 30);
    const auto waitReady = [](const auto& poll) {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
        while (!poll()) {
            if (std::chrono::steady_clock::now() >= deadline) { return false; }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        return true;
    };
    const auto oldSolve = assembly->startSimulation(simulation);
    const auto solve = assembly->startSimulation(simulation);
    assembly->cancelSimulation(oldSolve);
    EXPECT_THROW(assembly->finishSimulation(oldSolve), std::runtime_error);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulation(solve); }));
    ASSERT_GE(assembly->numberOfFrames(), 3);
    // Discard in-memory reuse without changing the accepted input. Persisted
    // playback must retain the existing full solver rather than installing a
    // fake pose-only ASMT engine, and still support exact frame application.
    const auto solvedPart = assembly->getMbDPart(components[0]);
    const auto baseline = components[0]->Placement.getValue();
    components[0]->Placement.setValue(Base::Placement(Base::Vector3d(77, 0, 0), Base::Rotation()));
    components[0]->Placement.setValue(baseline);
    const auto playback = assembly->startSimulationPlayback(simulation);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulation(playback); }));
    EXPECT_EQ(assembly->getMbDPart(components[0]), solvedPart);
    ASSERT_GE(assembly->numberOfFrames(), 3);
    ASSERT_EQ(assembly->updateForFrame(1), 0);
    const auto staleCachedFrame = assembly->requestSimulationFrame(2);
    simulation->getPropertyByName<App::PropertyFloat>("bTimeEnd")->setValue(0.3);
    EXPECT_THROW(assembly->finishSimulationFrame(staleCachedFrame), std::runtime_error);
    const auto changedPlayback = assembly->startSimulationPlayback(simulation);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulation(changedPlayback); }));
    EXPECT_NE(assembly->getMbDPart(components[0]), solvedPart);
    EXPECT_GE(assembly->numberOfFrames(), 4);
    simulation->getPropertyByName<App::PropertyFloat>("bTimeEnd")->setValue(0.2);
    // A caller requesting full state after cached playback still receives a
    // complete fresh engine, not the presentation cache as a purported solve.
    const auto fullAgain = assembly->startSimulation(simulation);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulation(fullAgain); }));
    EXPECT_NE(assembly->getMbDPart(components[0]), solvedPart);
    EXPECT_EQ(assembly->getMbDPart(components[0])->xs->size(), assembly->numberOfFrames());
    // An unrelated document object is not a simulation input. Creating,
    // changing, or deleting it must not discard the already solved frames.
    auto* unrelated = assembly->getDocument()->addObject<App::GeoFeature>("Unrelated");
    unrelated->Placement.setValue(Base::Placement(Base::Vector3d(7, 8, 9), Base::Rotation()));
    const auto unchangedFrame = assembly->requestSimulationFrame(1);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulationFrame(unchangedFrame); }));
    assembly->getDocument()->removeObject(unrelated->getNameInDocument());
    const auto frameAfterUnrelatedDeletion = assembly->requestSimulationFrame(1);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulationFrame(frameAfterUnrelatedDeletion); }));
    // Visibility propagates a derived _GroupTouched notification to the parent.
    // Neither notification changes any input used by the kinematic solve.
    for (const bool visible : {false, true}) {
        components[0]->Visibility.setValue(visible);
        auto* groupTouched = assembly->getJointGroup()->getPropertyByName("_GroupTouched");
        ASSERT_NE(groupTouched, nullptr);
        groupTouched->touch();
        const auto frameAfterVisibility = assembly->requestSimulationFrame(1);
        ASSERT_TRUE(waitReady([&] { return assembly->finishSimulationFrame(frameAfterVisibility); }));
    }
    // Display cadence is not a solver input. Editing it must not throw away
    // solved channels, unlike a time-step or component-placement change.
    simulation->getPropertyByName<App::PropertyFloat>("jFramesPerSecond")->setValue(60);
    // Unchanged solved inputs must be reusable without scheduling another solve.
    const auto cachedSolve = assembly->startSimulation(simulation);
    ASSERT_TRUE(assembly->finishSimulation(cachedSolve));
    const auto cancelledReuse = assembly->startSimulation(simulation);
    assembly->cancelSimulation(cancelledReuse);
    EXPECT_THROW(assembly->finishSimulation(cancelledReuse), std::runtime_error);
    const auto savedFrame = assembly->requestSimulationFrame(1);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulationFrame(savedFrame); }));
    // Restoring a presentation pose during save is not a model edit; nested
    // owner scopes must restore their prior state exactly.
    const bool outer = assembly->setSimulationPresentation(true);
    const bool inner = assembly->setSimulationPresentation(true);
    EXPECT_FALSE(outer);
    EXPECT_TRUE(inner);
    components[0]->Placement.setValue(Base::Placement(Base::Vector3d(2, 3, 4), Base::Rotation()));
    assembly->setSimulationPresentation(inner);
    components[0]->Placement.setValue(Base::Placement());
    assembly->setSimulationPresentation(outer);
    ASSERT_EQ(assembly->updateForFrame(1), 0);
    std::vector<Base::Placement> expected;
    for (const auto* component : components) { expected.push_back(component->Placement.getValue()); }
    const auto oldFrame = assembly->requestSimulationFrame(2);
    const auto frame = assembly->requestSimulationFrame(1);
    assembly->cancelSimulationFrame(oldFrame);
    EXPECT_THROW(assembly->finishSimulationFrame(oldFrame), std::runtime_error);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulationFrame(frame); }));
    for (size_t index = 0; index < components.size(); ++index) {
        EXPECT_TRUE(components[index]->Placement.getValue().isSame(expected[index], 1e-10));
    }
    const auto cancelled = assembly->requestSimulationFrame(2);
    assembly->cancelSimulationFrame(cancelled);
    EXPECT_THROW(assembly->finishSimulationFrame(cancelled), std::runtime_error);
    const auto invalidated = assembly->requestSimulationFrame(2);
    components[0]->Placement.setValue(Base::Placement(Base::Vector3d(100, 0, 0), Base::Rotation()));
    EXPECT_THROW(assembly->finishSimulationFrame(invalidated), std::runtime_error);
    EXPECT_DOUBLE_EQ(components[0]->Placement.getValue().getPosition().x, 100);
    const auto solveWithSource = assembly->startSimulation(simulation);
    ASSERT_TRUE(waitReady([&] { return assembly->finishSimulation(solveWithSource); }));
    const auto frameWithSource = assembly->requestSimulationFrame(1);
    linkedSource->Placement.setValue(Base::Placement(Base::Vector3d(3, 4, 5), Base::Rotation()));
    EXPECT_THROW(assembly->finishSimulationFrame(frameWithSource), std::runtime_error);
    // Closing the owner while generation is active must not join the worker.
    assembly->startSimulation(simulation);
}
