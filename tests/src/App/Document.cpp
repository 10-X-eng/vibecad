// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>
#include <gmock/gmock.h>

#include <string_view>
#include <unordered_map>

#include "App/Application.h"
#include "App/Document.h"
#include "App/DocumentTimeline.h"
#include "App/Link.h"
#include "App/PropertyLinks.h"
#include "App/PropertyStandard.h"
#include "App/StringHasher.h"
#include "App/SuppressibleExtension.h"
#include "Base/Exception.h"
#include "Base/FileInfo.h"
#include "Base/Interpreter.h"
#include "Base/Writer.h"
#include <src/App/InitApplication.h>

using ::testing::Eq;
using ::testing::Ne;

// NOLINTBEGIN(readability-magic-numbers)

namespace App
{

class TimelineThrowingSetupTestObject: public DocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(App::TimelineThrowingSetupTestObject);

public:
    void setupObject() override
    {
        DocumentObject::setupObject();
        throw Base::RuntimeError("intentional timeline setup failure");
    }
};

PROPERTY_SOURCE(App::TimelineThrowingSetupTestObject, App::DocumentObject)

class TimelineSuppressibleTestObject: public DocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(App::TimelineSuppressibleTestObject);

public:
    TimelineSuppressibleTestObject()
    {
        suppressible.initExtension(this);
    }

private:
    SuppressibleExtension suppressible;
};

PROPERTY_SOURCE(App::TimelineSuppressibleTestObject, App::DocumentObject)

class RepeatedRestoreLabelTestObject: public DocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(App::RepeatedRestoreLabelTestObject);

protected:
    void onDocumentRestored() override
    {
        DocumentObject::onDocumentRestored();
        // Python-backed document objects can emit this second notification
        // while rebuilding their proxy state, even though Label is unchanged.
        onChanged(&Label);
    }
};

PROPERTY_SOURCE(App::RepeatedRestoreLabelTestObject, App::DocumentObject)

}  // namespace App

class FakeWriter: public Base::Writer
{
    void writeFiles() override
    {}
    std::ostream& Stream() override
    {
        return std::cout;
    }
};

class DocumentTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
        App::TimelineThrowingSetupTestObject::init();
        App::TimelineSuppressibleTestObject::init();
        App::RepeatedRestoreLabelTestObject::init();
    }

    void SetUp() override
    {
        _docName = App::GetApplication().getUniqueDocumentName("test");
        _doc = App::GetApplication().newDocument(_docName.c_str(), "testUser");
    }

    void TearDown() override
    {
        App::GetApplication().closeDocument(_docName.c_str());
    }

    App::Document* doc()
    {
        return _doc;
    }

private:
    std::string _docName;
    App::Document* _doc {};
};

namespace
{

App::DocumentObject* addTimelineTestFeature(App::Document* document, const char* name)
{
    return document->addObject("App::FeaturePython", name, true, "Gui::ViewProviderDocumentObject");
}

App::DocumentObject* addTimelineSuppressibleTestFeature(
    App::Document* document,
    const char* name
)
{
    return document->addObject(
        "App::TimelineSuppressibleTestObject",
        name,
        true,
        "Gui::ViewProviderDocumentObject"
    );
}

void markTimelineTestMetadata(App::Property* property)
{
    ASSERT_NE(property, nullptr);
    property->setStatus(App::Property::Hidden, true);
    property->setStatus(App::Property::LockDynamic, true);
    property->setStatus(App::Property::NoRecompute, true);
}

unsigned long timelineTestMetadataPolicyStatus(const App::Property* property)
{
    constexpr unsigned long policyMask = (1UL << App::Property::ReadOnly)
        | (1UL << App::Property::Hidden) | (1UL << App::Property::LockDynamic)
        | (1UL << App::Property::NoRecompute);
    return property->getStatus() & policyMask;
}

Base::FileInfo timelineTestFile(const char* stem)
{
    return Base::FileInfo(Base::FileInfo::getTempFileName(stem) + ".FCStd");
}

App::PropertyString* markTimelineTestOperation(App::DocumentObject* object)
{
    auto* role = dynamic_cast<App::PropertyString*>(
        object->getDynamicPropertyByName(App::DocumentTimeline::RolePropertyName)
    );
    if (!role) {
        role = static_cast<App::PropertyString*>(
            object->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
        );
    }
    role->setValue(App::DocumentTimeline::OperationRole);
    markTimelineTestMetadata(role);
    return role;
}

std::pair<App::PropertyString*, App::PropertyLinkHidden*> markTimelineTestResource(
    App::DocumentObject* resource,
    App::DocumentObject* owner
)
{
    auto* ownerLink = dynamic_cast<App::PropertyLinkHidden*>(
        resource->getDynamicPropertyByName(App::DocumentTimeline::OwnerPropertyName)
    );
    if (!ownerLink) {
        ownerLink = static_cast<App::PropertyLinkHidden*>(resource->addDynamicProperty(
            "App::PropertyLinkHidden",
            App::DocumentTimeline::OwnerPropertyName
        ));
    }
    ownerLink->setValue(owner);
    markTimelineTestMetadata(ownerLink);

    auto* role = dynamic_cast<App::PropertyString*>(
        resource->getDynamicPropertyByName(App::DocumentTimeline::RolePropertyName)
    );
    if (!role) {
        role = static_cast<App::PropertyString*>(
            resource->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
        );
    }
    role->setValue(App::DocumentTimeline::ResourceRole);
    markTimelineTestMetadata(role);
    return {role, ownerLink};
}

struct TimelineTestState
{
    std::vector<App::DocumentObject*> operations;
    boost::dynamic_bitset<> visibility;
    boost::dynamic_bitset<> suppression;
    long position {0};
};

TimelineTestState captureTimelineTestState(const App::DocumentTimeline* timeline)
{
    return {
        .operations = timeline->Operations.getValues(),
        .visibility = timeline->VisibilityAtEnd.getValues(),
        .suppression = timeline->SuppressionAtEnd.getValues(),
        .position = timeline->Position.getValue(),
    };
}

void expectTimelineTestState(const App::DocumentTimeline* timeline, const TimelineTestState& expected)
{
    EXPECT_EQ(timeline->Operations.getValues(), expected.operations);
    EXPECT_EQ(timeline->VisibilityAtEnd.getValues(), expected.visibility);
    EXPECT_EQ(timeline->SuppressionAtEnd.getValues(), expected.suppression);
    EXPECT_EQ(timeline->Position.getValue(), expected.position);
}

}  // namespace


TEST_F(DocumentTest, addStringHasherIndicatesUnwrittenWhenNew)
{
    // Arrange
    App::StringHasherRef hasher(new App::StringHasher);

    // Act
    auto addResult = doc()->addStringHasher(hasher);

    // Assert
    EXPECT_TRUE(addResult.first);
    EXPECT_THAT(addResult.second, Ne(-1));
}

TEST_F(DocumentTest, addStringHasherIndicatesAlreadyWritten)
{
    // Arrange
    App::StringHasherRef hasher(new App::StringHasher);
    doc()->addStringHasher(hasher);

    // Act
    auto addResult = doc()->addStringHasher(hasher);

    // Assert
    EXPECT_FALSE(addResult.first);
}

TEST_F(DocumentTest, getStringHasherGivesExpectedHasher)
{
    // Arrange
    App::StringHasherRef hasher(new App::StringHasher);
    auto pair = doc()->addStringHasher(hasher);
    int index = pair.second;

    // Act
    auto foundHasher = doc()->getStringHasher(index);

    // Assert
    EXPECT_EQ(hasher, foundHasher);
}

TEST_F(DocumentTest, timelineApplyingStateIsExposedThroughTheDocument)
{
    EXPECT_FALSE(doc()->isApplyingTimelineState());

    auto* timeline = App::DocumentTimeline::ensure(doc());
    EXPECT_FALSE(doc()->isApplyingTimelineState());

    timeline->beginApplying();
    EXPECT_TRUE(doc()->isApplyingTimelineState());
    timeline->endApplying();

    EXPECT_FALSE(doc()->isApplyingTimelineState());
}

TEST_F(DocumentTest, semanticCopyClosureIncludesOwnedResourcesAndReplacementBlocks)
{
    const auto addFeature = [this](const char* name) {
        return doc()->addObject("App::FeaturePython", name, true, "Gui::ViewProviderDocumentObject");
    };
    const auto markOperation = [](App::DocumentObject* object) {
        markTimelineTestOperation(object);
    };
    const auto markResource = [](App::DocumentObject* resource, App::DocumentObject* owner) {
        markTimelineTestResource(resource, owner);
    };

    auto* source = addFeature("SourceOperation");
    markOperation(source);
    auto* sourceResource = addFeature("SourceResource");
    markResource(sourceResource, source);

    auto* result = addFeature("ResultOperation");
    markOperation(result);
    auto* editor = addFeature("ResultEditor");
    markResource(editor, result);
    auto* nestedResource = addFeature("NestedResource");
    markResource(nestedResource, editor);

    auto* editorLink = static_cast<App::PropertyLinkHidden*>(
        result->addDynamicProperty("App::PropertyLinkHidden", App::DocumentTimeline::EditorPropertyName)
    );
    editorLink->setValue(editor);
    markTimelineTestMetadata(editorLink);
    auto* replacements = static_cast<App::PropertyLinkListHidden*>(result->addDynamicProperty(
        "App::PropertyLinkListHidden",
        App::DocumentTimeline::ReplacedInputsPropertyName
    ));
    replacements->setValues({source});
    markTimelineTestMetadata(replacements);

    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);

    EXPECT_THAT(
        timeline->semanticCopyClosure({nestedResource}),
        ::testing::ElementsAre(source, sourceResource, result, editor, nestedResource)
    );
}

TEST_F(DocumentTest, futureTimelineSchemaIsNotNormalizedOrDowngraded)
{
    auto* first =
        doc()->addObject("App::FeaturePython", "FutureFirst", true, "Gui::ViewProviderDocumentObject");
    auto* second = doc()->addObject(
        "App::FeaturePython",
        "FutureSecond",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);

    const long futureSchema = App::DocumentTimeline::CurrentSchemaVersion + 1;
    const boost::dynamic_bitset<> futureVisibility(1, 1);
    const boost::dynamic_bitset<> futureSuppression(4, 0b1010);
    timeline->beginApplying();
    timeline->Operations.setValues({second, first, second});
    timeline->VisibilityAtEnd.setValues(futureVisibility);
    timeline->SuppressionAtEnd.setValues(futureSuppression);
    timeline->Position.setValue(99);
    timeline->SchemaVersion.setValue(futureSchema);
    timeline->endApplying();
    const auto expected = captureTimelineTestState(timeline);

    timeline->normalizeAfterRestore();

    expectTimelineTestState(timeline, expected);
    EXPECT_EQ(timeline->SchemaVersion.getValue(), futureSchema);
}

TEST_F(DocumentTest, semanticCopyClosureRejectsStaleEditorMetadata)
{
    auto* operation
        = doc()->addObject("App::FeaturePython", "Operation", true, "Gui::ViewProviderDocumentObject");
    auto* unrelated
        = doc()->addObject("App::FeaturePython", "Unrelated", true, "Gui::ViewProviderDocumentObject");
    auto* role = static_cast<App::PropertyString*>(
        operation->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
    );
    role->setValue(App::DocumentTimeline::OperationRole);
    markTimelineTestMetadata(role);
    auto* editor = static_cast<App::PropertyLinkHidden*>(operation->addDynamicProperty(
        "App::PropertyLinkHidden",
        App::DocumentTimeline::EditorPropertyName
    ));
    editor->setValue(unrelated);
    markTimelineTestMetadata(editor);

    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    EXPECT_THROW(timeline->semanticCopyClosure({operation}), Base::RuntimeError);
}

TEST_F(DocumentTest, importedTimelineAdoptionIsAtomicAndPreservesTheMarker)
{
    const auto addFeature = [this](const char* name) {
        return doc()->addObject("App::FeaturePython", name, true, "Gui::ViewProviderDocumentObject");
    };
    auto* first = addFeature("ExistingFirst");
    auto* future = addFeature("ExistingFuture");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    timeline->beginApplying();
    timeline->Position.setValue(1);

    auto* source = addFeature("ImportedSource");
    auto* result = addFeature("ImportedResult");
    auto* editor = addFeature("ImportedEditor");

    const auto markOperation = [](App::DocumentObject* object) {
        markTimelineTestOperation(object);
    };
    markOperation(source);
    markOperation(result);
    auto* resourceRole = static_cast<App::PropertyString*>(
        editor->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
    );
    resourceRole->setValue(App::DocumentTimeline::ResourceRole);
    markTimelineTestMetadata(resourceRole);
    auto* owner = static_cast<App::PropertyLinkHidden*>(
        editor->addDynamicProperty("App::PropertyLinkHidden", App::DocumentTimeline::OwnerPropertyName)
    );
    owner->setValue(result);
    markTimelineTestMetadata(owner);
    auto* editorLink = static_cast<App::PropertyLinkHidden*>(
        result->addDynamicProperty("App::PropertyLinkHidden", App::DocumentTimeline::EditorPropertyName)
    );
    editorLink->setValue(editor);
    markTimelineTestMetadata(editorLink);
    auto* replaced = static_cast<App::PropertyLinkListHidden*>(result->addDynamicProperty(
        "App::PropertyLinkListHidden",
        App::DocumentTimeline::ReplacedInputsPropertyName
    ));
    replaced->setValues({source});
    markTimelineTestMetadata(replaced);
    auto* editCommand = static_cast<App::PropertyString*>(
        result->addDynamicProperty("App::PropertyString", App::DocumentTimeline::EditCommandPropertyName)
    );
    editCommand->setValue("PartDesign_Test");
    markTimelineTestMetadata(editCommand);
    timeline->endApplying();

    const auto visibilityBefore = timeline->VisibilityAtEnd.getValues();
    const auto suppressionBefore = timeline->SuppressionAtEnd.getValues();
    doc()->openTransaction("Adopt imported timeline block");
    timeline->adoptImportedOperations({source, result, editor}, {source, editor, result});
    doc()->commitTransaction();

    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(first, source, editor, result, future)
    );
    EXPECT_EQ(timeline->Position.getValue(), 4);
    ASSERT_EQ(timeline->VisibilityAtEnd.getSize(), 5);
    ASSERT_EQ(timeline->SuppressionAtEnd.getSize(), 5);
    EXPECT_EQ(timeline->VisibilityAtEnd.getValues().test(4), visibilityBefore.test(1));
    EXPECT_EQ(timeline->SuppressionAtEnd.getValues().test(4), suppressionBefore.test(1));
    for (auto* property : {
             source->getPropertyByName(App::DocumentTimeline::RolePropertyName),
             result->getPropertyByName(App::DocumentTimeline::RolePropertyName),
             editor->getPropertyByName(App::DocumentTimeline::RolePropertyName),
             editor->getPropertyByName(App::DocumentTimeline::OwnerPropertyName),
             result->getPropertyByName(App::DocumentTimeline::EditorPropertyName),
             result->getPropertyByName(App::DocumentTimeline::ReplacedInputsPropertyName),
             result->getPropertyByName(App::DocumentTimeline::EditCommandPropertyName),
         }) {
        ASSERT_NE(property, nullptr);
        EXPECT_TRUE(property->testStatus(App::Property::Hidden));
        EXPECT_FALSE(property->testStatus(App::Property::ReadOnly));
        EXPECT_TRUE(property->testStatus(App::Property::LockDynamic));
        EXPECT_TRUE(property->testStatus(App::Property::NoRecompute));
    }
}

TEST_F(DocumentTest, importedTimelineAdoptionReordersSameTransactionProvisionalOperations)
{
    const auto addFeature = [this](const char* name) {
        return doc()->addObject("App::FeaturePython", name, true, "Gui::ViewProviderDocumentObject");
    };
    auto* first = addFeature("ExistingFirst");
    auto* future = addFeature("ExistingFuture");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    doc()->commitTransaction();

    doc()->openTransaction("Set import marker");
    timeline->Position.setValue(1);
    doc()->commitTransaction();

    doc()->openTransaction("Import operations");
    auto* importedFirst = addFeature("ImportedFirst");
    auto* importedSecond = addFeature("ImportedSecond");
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(first, importedFirst, importedSecond, future)
    );

    timeline->adoptImportedOperations({importedFirst, importedSecond}, {importedSecond, importedFirst});
    doc()->commitTransaction();

    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(first, importedSecond, importedFirst, future)
    );
    EXPECT_EQ(timeline->Position.getValue(), 3);
}

TEST_F(DocumentTest, importedStateApplicationRejectsLaterIdentityRemovedByVisibilityCallback)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->commitTransaction();
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Reject imported visibility callback mutation");
    auto* first = addTimelineSuppressibleTestFeature(doc(), "ImportedVisibilityFirst");
    addTimelineSuppressibleTestFeature(doc(), "ImportedVisibilityLater");

    bool callbackInvoked = false;
    auto connection = doc()->signalChangedObject.connect(
        [this, first, &callbackInvoked](
            const App::DocumentObject& changed,
            const App::Property& property
        ) {
            if (!callbackInvoked && &changed == first
                && std::string_view(property.getName()) == "Visibility") {
                callbackInvoked = true;
                doc()->removeObject("ImportedVisibilityLater");
            }
        }
    );
    EXPECT_THROW(
        timeline->adoptImportedOperations(
            {
                first,
                doc()->getObject("ImportedVisibilityLater"),
            },
            {
                first,
                doc()->getObject("ImportedVisibilityLater"),
            },
            {false, false},
            {false, false}
        ),
        Base::RuntimeError
    );
    connection.disconnect();

    EXPECT_TRUE(callbackInvoked);
    doc()->abortTransaction();
    expectTimelineTestState(timeline, baseline);
}

TEST_F(DocumentTest, importedStateApplicationRejectsLaterIdentityRemovedBySuppressionCallback)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->commitTransaction();
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Reject imported suppression callback mutation");
    auto* first = addTimelineSuppressibleTestFeature(doc(), "ImportedSuppressionFirst");
    auto* later = addTimelineSuppressibleTestFeature(doc(), "ImportedSuppressionLater");
    first->Visibility.setValue(false);
    later->Visibility.setValue(false);

    bool callbackInvoked = false;
    auto connection = doc()->signalChangedObject.connect(
        [this, first, &callbackInvoked](
            const App::DocumentObject& changed,
            const App::Property& property
        ) {
            if (!callbackInvoked && &changed == first
                && std::string_view(property.getName()) == "Suppressed") {
                callbackInvoked = true;
                doc()->removeObject("ImportedSuppressionLater");
            }
        }
    );
    EXPECT_THROW(
        timeline->adoptImportedOperations(
            {first, later},
            {first, later},
            {false, false},
            {true, true}
        ),
        Base::RuntimeError
    );
    connection.disconnect();

    EXPECT_TRUE(callbackInvoked);
    doc()->abortTransaction();
    expectTimelineTestState(timeline, baseline);
}

TEST_F(DocumentTest, importedTimelineAdoptionRejectsPreExistingTimelineOverlap)
{
    auto* existing = doc()->addObject(
        "App::FeaturePython",
        "ExistingOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    doc()->commitTransaction();

    const auto operationsBefore = timeline->Operations.getValues();
    doc()->openTransaction("Reject pre-existing import overlap");
    EXPECT_THROW(timeline->adoptImportedOperations({existing}, {existing}), Base::RuntimeError);
    EXPECT_EQ(timeline->Operations.getValues(), operationsBefore);
    doc()->abortTransaction();
}

TEST_F(DocumentTest, importedTimelineAdoptionValidatesAndOrdersProvisionalSemanticBlock)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->openTransaction("Import semantic operation");
    auto* operation = doc()->addObject(
        "App::FeaturePython",
        "ImportedOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* resource = doc()->addObject(
        "App::FeaturePython",
        "ImportedResource",
        true,
        "Gui::ViewProviderDocumentObject"
    );

    auto* operationRole = markTimelineTestOperation(operation);
    const auto [resourceRole, owner] = markTimelineTestResource(resource, operation);

    timeline->adoptImportedOperations({operation, resource}, {resource, operation});
    doc()->commitTransaction();

    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(resource, operation));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(resource), operation);
    EXPECT_TRUE(operationRole->testStatus(App::Property::Hidden));
    EXPECT_TRUE(resourceRole->testStatus(App::Property::Hidden));
    EXPECT_TRUE(owner->testStatus(App::Property::Hidden));
}

TEST_F(DocumentTest, timelineMetadataOnLinksIsOccurrenceLocalAndSurvivesReopen)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());

    doc()->openTransaction("Create linked timeline source");
    auto* source = addTimelineTestFeature(doc(), "LinkedTimelineSource");
    auto* sourceRole = markTimelineTestOperation(source);
    timeline->finalizeProvisionalOperationBlock(source, {source});
    doc()->commitTransaction();

    doc()->openTransaction("Publish linked occurrence as resource");
    auto* owner = addTimelineTestFeature(doc(), "LinkedOccurrenceOwner");
    markTimelineTestOperation(owner);
    auto* resourceOccurrence = dynamic_cast<App::Link*>(doc()->addObject(
        "App::Link",
        "ResourceOccurrence",
        true,
        "Gui::ViewProviderLink"
    ));
    ASSERT_NE(resourceOccurrence, nullptr);
    resourceOccurrence->LinkedObject.setValue(source);

    // Ordinary Link lookup intentionally sees the source property. History
    // classification must not: the occurrence has not declared any local
    // timeline role yet.
    EXPECT_EQ(
        resourceOccurrence->getPropertyByName(App::DocumentTimeline::RolePropertyName),
        sourceRole
    );
    EXPECT_EQ(
        resourceOccurrence->getDynamicPropertyByName(
            App::DocumentTimeline::RolePropertyName
        ),
        nullptr
    );
    EXPECT_FALSE(App::DocumentTimeline::hasTimelineOperationRole(resourceOccurrence));
    EXPECT_FALSE(App::DocumentTimeline::hasTimelineResourceRole(resourceOccurrence));

    const auto [resourceRole, resourceOwner] =
        markTimelineTestResource(resourceOccurrence, owner);
    timeline->finalizeProvisionalOperationBlock(owner, {resourceOccurrence, owner});
    doc()->commitTransaction();

    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(source));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(owner));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineResourceRole(resourceOccurrence));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(resourceOccurrence), owner);
    EXPECT_EQ(
        sourceRole,
        source->getDynamicPropertyByName(App::DocumentTimeline::RolePropertyName)
    );
    EXPECT_EQ(
        resourceRole,
        resourceOccurrence->getDynamicPropertyByName(
            App::DocumentTimeline::RolePropertyName
        )
    );
    EXPECT_EQ(
        resourceOwner,
        resourceOccurrence->getDynamicPropertyByName(
            App::DocumentTimeline::OwnerPropertyName
        )
    );

    doc()->openTransaction("Publish linked occurrence as operation");
    auto* operationOccurrence = dynamic_cast<App::Link*>(doc()->addObject(
        "App::Link",
        "OperationOccurrence",
        true,
        "Gui::ViewProviderLink"
    ));
    ASSERT_NE(operationOccurrence, nullptr);
    operationOccurrence->LinkedObject.setValue(source);
    EXPECT_FALSE(App::DocumentTimeline::hasTimelineOperationRole(operationOccurrence));
    auto* occurrenceRole = markTimelineTestOperation(operationOccurrence);
    timeline->finalizeProvisionalOperationBlock(operationOccurrence, {operationOccurrence});
    doc()->commitTransaction();

    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(source));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(operationOccurrence));
    EXPECT_NE(sourceRole, occurrenceRole);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(source, resourceOccurrence, owner, operationOccurrence)
    );

    Base::FileInfo saved = timelineTestFile("timeline-link-local-metadata");
    ASSERT_TRUE(doc()->saveCopy(saved.filePath().c_str()));
    auto* reopened = App::GetApplication().openDocument(saved.filePath().c_str());
    ASSERT_NE(reopened, nullptr);
    auto* reopenedTimeline = App::DocumentTimeline::get(reopened);
    auto* reopenedSource = reopened->getObject("LinkedTimelineSource");
    auto* reopenedOwner = reopened->getObject("LinkedOccurrenceOwner");
    auto* reopenedResource = reopened->getObject("ResourceOccurrence");
    auto* reopenedOperation = reopened->getObject("OperationOccurrence");
    ASSERT_NE(reopenedTimeline, nullptr);
    ASSERT_NE(reopenedSource, nullptr);
    ASSERT_NE(reopenedOwner, nullptr);
    ASSERT_NE(reopenedResource, nullptr);
    ASSERT_NE(reopenedOperation, nullptr);

    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(reopenedSource));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(reopenedOwner));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineResourceRole(reopenedResource));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(reopenedResource), reopenedOwner);
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(reopenedOperation));
    EXPECT_THAT(
        reopenedTimeline->Operations.getValues(),
        ::testing::ElementsAre(
            reopenedSource,
            reopenedResource,
            reopenedOwner,
            reopenedOperation
        )
    );

    const std::string reopenedName = reopened->getName();
    EXPECT_TRUE(App::GetApplication().closeDocument(reopenedName.c_str()));
    saved.deleteFile();
}

TEST_F(DocumentTest, timelineResourceOwnershipRequiresExactLiveAcyclicRoot)
{
    auto* operation = addTimelineTestFeature(doc(), "OwnershipOperation");
    auto* otherOperation = addTimelineTestFeature(doc(), "OtherOwnershipOperation");
    auto* parent = addTimelineTestFeature(doc(), "OwnershipParent");
    auto* leaf = addTimelineTestFeature(doc(), "OwnershipLeaf");
    auto* ordinary = addTimelineTestFeature(doc(), "OrdinaryObject");
    markTimelineTestOperation(operation);
    markTimelineTestOperation(otherOperation);
    const auto [parentRole, parentOwner] =
        markTimelineTestResource(parent, operation);
    const auto [leafRole, leafOwner] =
        markTimelineTestResource(leaf, parent);
    (void)parentRole;
    (void)parentOwner;
    (void)leafRole;

    EXPECT_TRUE(
        App::DocumentTimeline::isTimelineResourceOwnedBy(parent, operation)
    );
    EXPECT_TRUE(
        App::DocumentTimeline::isTimelineResourceOwnedBy(leaf, operation)
    );
    EXPECT_FALSE(
        App::DocumentTimeline::isTimelineResourceOwnedBy(
            leaf,
            otherOperation
        )
    );
    EXPECT_FALSE(
        App::DocumentTimeline::isTimelineResourceOwnedBy(
            ordinary,
            operation
        )
    );
    EXPECT_FALSE(
        App::DocumentTimeline::isTimelineResourceOwnedBy(
            operation,
            operation
        )
    );

    leafOwner->setValue(nullptr);
    EXPECT_FALSE(
        App::DocumentTimeline::isTimelineResourceOwnedBy(leaf, operation)
    );
    leafOwner->setValue(parent);
}

TEST_F(DocumentTest, objectUsabilityIsOneExactCrossWorkbenchHistoryContract)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    auto* operation =
        addTimelineSuppressibleTestFeature(doc(), "UsableOperation");
    auto* resource =
        addTimelineSuppressibleTestFeature(doc(), "UsableResource");
    auto* legacy =
        addTimelineSuppressibleTestFeature(doc(), "UsableLegacyObject");
    auto* internal =
        addTimelineTestFeature(doc(), "UnusableInternalObject");

    markTimelineTestOperation(operation);
    auto [resourceRole, resourceOwner] =
        markTimelineTestResource(resource, operation);
    auto* internalRole = static_cast<App::PropertyString*>(
        internal->addDynamicProperty(
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName
        )
    );
    internalRole->setValue(App::DocumentTimeline::InternalRole);

    timeline->Operations.setValues({resource, operation});
    timeline->VisibilityAtEnd.setValues(
        boost::dynamic_bitset<>(2)
    );
    timeline->SuppressionAtEnd.setValues(
        boost::dynamic_bitset<>(2)
    );
    timeline->Position.setValue(2);
    operation->Visibility.setValue(false);
    resource->Visibility.setValue(false);

    EXPECT_FALSE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(nullptr)
    );
    EXPECT_TRUE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(operation)
    );
    EXPECT_TRUE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(resource)
    );
    EXPECT_TRUE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(legacy)
    );
    EXPECT_FALSE(operation->Visibility.getValue());
    EXPECT_FALSE(resource->Visibility.getValue());
    EXPECT_TRUE(
        doc()->isObjectUsableAtCurrentTimelinePosition(operation)
    );
    EXPECT_FALSE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(internal)
    );

    boost::dynamic_bitset<> suppression(2);
    suppression.set(1);
    timeline->SuppressionAtEnd.setValues(suppression);
    EXPECT_FALSE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(operation)
    );
    EXPECT_FALSE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(resource)
    );

    suppression.reset();
    suppression.set(0);
    timeline->SuppressionAtEnd.setValues(suppression);
    EXPECT_TRUE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(operation)
    );
    EXPECT_FALSE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(resource)
    );

    suppression.reset();
    timeline->SuppressionAtEnd.setValues(suppression);
    timeline->Position.setValue(1);
    EXPECT_FALSE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(operation)
    );
    EXPECT_FALSE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(resource)
    );

    timeline->Position.setValue(2);
    auto* legacySuppressible =
        legacy->getExtensionByType<App::SuppressibleExtension>(true);
    ASSERT_NE(legacySuppressible, nullptr);
    legacySuppressible->Suppressed.setValue(true);
    EXPECT_FALSE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(legacy)
    );
    legacySuppressible->Suppressed.setValue(false);

    resourceOwner->setValue(nullptr);
    EXPECT_FALSE(
        App::DocumentTimeline::isObjectUsableAtCurrentPosition(resource)
    );
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineResourceRole(resource));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineInternalRole(internal));
    EXPECT_FALSE(App::DocumentTimeline::hasTimelineInternalRole(operation));
    (void)resourceRole;
}

TEST_F(DocumentTest, provisionalTimelineFinalizationCanonicalizesNewBlockAndRetainsFuture)
{
    const auto addFeature = [this](const char* name) {
        return doc()->addObject("App::FeaturePython", name, true, "Gui::ViewProviderDocumentObject");
    };
    const auto markOperation = [](App::DocumentObject* object) {
        markTimelineTestOperation(object);
    };
    const auto markResource = [](App::DocumentObject* resource, App::DocumentObject* owner) {
        markTimelineTestResource(resource, owner);
    };

    auto* source = addFeature("ExistingSource");
    auto* future = addFeature("RetainedFuture");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    timeline->beginApplying();
    timeline->Position.setValue(1);
    timeline->endApplying();

    doc()->openTransaction("Create canonical semantic block");
    auto* operation = addFeature("NewOperation");
    auto* resource = addFeature("NewResource");
    markOperation(operation);
    markResource(resource, operation);
    auto* replacements = static_cast<App::PropertyLinkListHidden*>(operation->addDynamicProperty(
        "App::PropertyLinkListHidden",
        App::DocumentTimeline::ReplacedInputsPropertyName
    ));
    replacements->setValues({source});
    markTimelineTestMetadata(replacements);

    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(source, operation, resource, future)
    );
    timeline->finalizeProvisionalOperationBlock(operation, {resource, operation});
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(source, resource, operation, future)
    );
    EXPECT_EQ(timeline->Position.getValue(), 3);
    ASSERT_EQ(timeline->VisibilityAtEnd.getSize(), 4);
    ASSERT_EQ(timeline->SuppressionAtEnd.getSize(), 4);
    EXPECT_TRUE(operation->getPropertyByName(App::DocumentTimeline::RolePropertyName)
                    ->testStatus(App::Property::Hidden));
    EXPECT_TRUE(operation->getPropertyByName(App::DocumentTimeline::RolePropertyName)
                    ->testStatus(App::Property::LockDynamic));
    EXPECT_TRUE(operation->getPropertyByName(App::DocumentTimeline::RolePropertyName)
                    ->testStatus(App::Property::NoRecompute));
    EXPECT_TRUE(resource->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
                    ->testStatus(App::Property::Hidden));
    doc()->commitTransaction();
}

TEST_F(DocumentTest, resourceFirstTimelinePublicationPreservesProvisionalEnrollments)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->openTransaction("Publish resource-first semantic block");

    auto* resource = addTimelineTestFeature(doc(), "ResourceCreatedFirst");
    auto* operation = addTimelineTestFeature(doc(), "OperationCreatedSecond");
    ASSERT_TRUE(timeline->isProvisionallyEnrolledByCurrentTransaction(resource));
    ASSERT_TRUE(timeline->isProvisionallyEnrolledByCurrentTransaction(operation));

    markTimelineTestOperation(operation);
    markTimelineTestResource(resource, operation);

    // Role and owner callbacks must not consume or move the creation proof
    // needed by the block finalizer.
    ASSERT_TRUE(timeline->isProvisionallyEnrolledByCurrentTransaction(resource));
    ASSERT_TRUE(timeline->isProvisionallyEnrolledByCurrentTransaction(operation));
    timeline->finalizeProvisionalOperationBlock(operation, {resource, operation});
    doc()->commitTransaction();

    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(resource, operation)
    );
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineResourceRole(resource));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(resource), operation);
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(operation));
}

TEST_F(DocumentTest, copiedObjectsRetainExactCreationProofForSemanticPublication)
{
    doc()->setUndoMode(1);
    doc()->openTransaction("Create copy source");
    auto* source = addTimelineTestFeature(doc(), "CopySource");
    doc()->commitTransaction();

    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Publish copied semantic block");
    const auto copied = doc()->copyObject({source}, false, false);
    ASSERT_EQ(copied.size(), 1);
    auto* resource = copied.front();
    ASSERT_NE(resource, nullptr);
    ASSERT_NE(resource, source);

    auto* operation = addTimelineTestFeature(doc(), "CopyOperation");
    doc()->publishProvisionalTimelineOperationBlock(operation, {resource});

    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(source, resource, operation)
    );
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(resource), operation);
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineResourceRole(resource));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(operation));

    const std::string copiedName = resource->getNameInDocument();
    doc()->abortTransaction();
    EXPECT_EQ(doc()->getObject(copiedName.c_str()), nullptr);
    EXPECT_EQ(doc()->getObject("CopyOperation"), nullptr);
    expectTimelineTestState(timeline, baseline);
}

TEST_F(DocumentTest, adoptedCopyProofRebasesTheNextCreationGeneration)
{
    doc()->setUndoMode(1);
    auto* source = addTimelineTestFeature(doc(), "AdoptedCopySource");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    doc()->commitTransaction();
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Adopt copy then publish");
    const auto copied = doc()->copyObject({source}, false, false);
    ASSERT_EQ(copied.size(), 1);
    auto* adopted = copied.front();
    ASSERT_NE(adopted, nullptr);
    const std::string adoptedName = adopted->getNameInDocument();
    ASSERT_NE(adopted, source);
    ASSERT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(source));

    timeline->adoptImportedOperations({adopted}, {adopted});
    ASSERT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(source, adopted));

    auto* operation = doc()->addObject(
        "App::DocumentObjectGroup",
        "PostAdoptionOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    doc()->publishProvisionalTimelineOperationBlock(operation, {});

    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(source, adopted, operation)
    );
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(operation));

    doc()->abortTransaction();
    EXPECT_EQ(doc()->getObject(adoptedName.c_str()), nullptr);
    EXPECT_EQ(doc()->getObject("PostAdoptionOperation"), nullptr);
    expectTimelineTestState(timeline, baseline);
}

TEST_F(DocumentTest, adoptedCopyDoesNotInvalidateUnrelatedPendingCreationProof)
{
    doc()->setUndoMode(1);
    auto* source = addTimelineTestFeature(doc(), "MixedAdoptedCopySource");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    doc()->commitTransaction();
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Preserve mixed copy provenance");
    auto* pending = addTimelineTestFeature(doc(), "PendingBeforeAdoption");
    ASSERT_TRUE(doc()->isProvisionallyEnrolledInTimelineByCurrentTransaction(pending));
    const auto copied = doc()->copyObject({source}, false, false);
    ASSERT_EQ(copied.size(), 1);
    auto* adopted = copied.front();
    ASSERT_NE(adopted, nullptr);
    const std::string adoptedName = adopted->getNameInDocument();

    timeline->adoptImportedOperations({adopted}, {adopted});
    ASSERT_TRUE(doc()->isProvisionallyEnrolledInTimelineByCurrentTransaction(pending));
    ASSERT_TRUE(doc()->isProvisionallyEnrolledInTimelineByCurrentTransaction(adopted));
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(source, pending, adopted)
    );

    doc()->publishProvisionalTimelineOperationBlock(pending, {});
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(source, pending, adopted)
    );
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(pending));

    doc()->publishProvisionalTimelineOperationBlock(adopted, {});
    auto* later = doc()->addObject(
        "App::DocumentObjectGroup",
        "AfterMixedAdoption",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    doc()->publishProvisionalTimelineOperationBlock(later, {});
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(source, pending, adopted, later)
    );

    doc()->abortTransaction();
    EXPECT_EQ(doc()->getObject("PendingBeforeAdoption"), nullptr);
    EXPECT_EQ(doc()->getObject(adoptedName.c_str()), nullptr);
    EXPECT_EQ(doc()->getObject("AfterMixedAdoption"), nullptr);
    expectTimelineTestState(timeline, baseline);
}

TEST_F(DocumentTest, provisionalTimelineFinalizationAddsResourcesToExistingBlockWithoutANewStep)
{
    const auto addFeature = [this](const char* name) {
        return doc()->addObject("App::FeaturePython", name, true, "Gui::ViewProviderDocumentObject");
    };
    const auto markOperation = [](App::DocumentObject* object) {
        markTimelineTestOperation(object);
    };
    const auto markResource = [](App::DocumentObject* resource, App::DocumentObject* owner) {
        markTimelineTestResource(resource, owner);
    };

    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->openTransaction("Create existing semantic operation");
    auto* operation = addFeature("ExistingOperation");
    auto* existingResource = addFeature("ExistingResource");
    markOperation(operation);
    markResource(existingResource, operation);
    timeline->finalizeProvisionalOperationBlock(operation, {existingResource, operation});
    doc()->commitTransaction();

    auto* future = addFeature("RetainedFuture");
    timeline->beginApplying();
    timeline->Position.setValue(2);
    timeline->endApplying();

    doc()->openTransaction("Add operation resource");
    auto* newResource = addFeature("NewResource");
    markResource(newResource, operation);
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(existingResource, operation, newResource, future)
    );

    timeline->finalizeProvisionalOperationBlock(operation, {newResource});
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(existingResource, newResource, operation, future)
    );
    EXPECT_EQ(timeline->Position.getValue(), 3);
    const auto finalizedOperations = timeline->Operations.getValues();
    EXPECT_EQ(std::count(finalizedOperations.begin(), finalizedOperations.end(), operation), 1);
    doc()->commitTransaction();
}

TEST_F(DocumentTest, provisionalTimelineFinalizationRejectsObjectsFromAnEarlierTransaction)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    auto* operation = doc()->addObject(
        "App::FeaturePython",
        "ExistingOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* role = static_cast<App::PropertyString*>(
        operation->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
    );
    role->setValue(App::DocumentTimeline::OperationRole);
    markTimelineTestMetadata(role);
    doc()->commitTransaction();

    const auto operationsBefore = timeline->Operations.getValues();
    const auto visibilityBefore = timeline->VisibilityAtEnd.getValues();
    const auto suppressionBefore = timeline->SuppressionAtEnd.getValues();
    const auto positionBefore = timeline->Position.getValue();
    doc()->openTransaction("Reject non-provisional object");
    EXPECT_THROW(timeline->finalizeProvisionalOperationBlock(operation, {operation}), Base::ValueError);
    EXPECT_EQ(timeline->Operations.getValues(), operationsBefore);
    EXPECT_EQ(timeline->VisibilityAtEnd.getValues(), visibilityBefore);
    EXPECT_EQ(timeline->SuppressionAtEnd.getValues(), suppressionBefore);
    EXPECT_EQ(timeline->Position.getValue(), positionBefore);
    doc()->abortTransaction();
}

TEST_F(DocumentTest, lockedDynamicPropertyCreationAbortsUndoesAndRedoesExactly)
{
    doc()->setUndoMode(1);
    auto* object = addTimelineTestFeature(doc(), "LockedDynamicPropertyOwner");
    doc()->commitTransaction();

    doc()->openTransaction("Abort locked dynamic property");
    auto* aborted = object->addDynamicProperty("App::PropertyString", "LockedMetadata");
    aborted->setStatus(App::Property::LockDynamic, true);
    doc()->abortTransaction();
    object = doc()->getObject("LockedDynamicPropertyOwner");
    ASSERT_NE(object, nullptr);
    EXPECT_EQ(object->getPropertyByName("LockedMetadata"), nullptr);

    doc()->openTransaction("Commit locked dynamic property");
    auto* committed = object->addDynamicProperty("App::PropertyString", "LockedMetadata");
    committed->setStatus(App::Property::LockDynamic, true);
    doc()->commitTransaction();
    ASSERT_NE(object->getPropertyByName("LockedMetadata"), nullptr);

    ASSERT_TRUE(doc()->undo());
    object = doc()->getObject("LockedDynamicPropertyOwner");
    ASSERT_NE(object, nullptr);
    EXPECT_EQ(object->getPropertyByName("LockedMetadata"), nullptr);

    ASSERT_TRUE(doc()->redo());
    object = doc()->getObject("LockedDynamicPropertyOwner");
    ASSERT_NE(object, nullptr);
    auto* restored = object->getPropertyByName("LockedMetadata");
    ASSERT_NE(restored, nullptr);
    EXPECT_TRUE(restored->testStatus(App::Property::LockDynamic));
    EXPECT_FALSE(object->removeDynamicProperty("LockedMetadata"));
    EXPECT_EQ(object->getPropertyByName("LockedMetadata"), restored);
}

TEST_F(DocumentTest, frozenNewObjectRestoresLinksAcrossUndoAndRedo)
{
    doc()->setUndoMode(1);
    doc()->openTransaction("Create frozen-object sources");
    auto* primary = addTimelineTestFeature(doc(), "FrozenPrimarySource");
    auto* secondary = addTimelineTestFeature(doc(), "FrozenSecondarySource");
    doc()->commitTransaction();

    doc()->openTransaction("Create frozen linked object");
    auto* result = addTimelineTestFeature(doc(), "FrozenLinkedResult");
    auto* source = static_cast<App::PropertyLink*>(
        result->addDynamicProperty("App::PropertyLink", "Source"));
    auto* references = static_cast<App::PropertyLinkList*>(
        result->addDynamicProperty("App::PropertyLinkList", "References"));
    ASSERT_NE(source, nullptr);
    ASSERT_NE(references, nullptr);
    source->setValue(primary);
    references->setValues({primary, secondary});
    result->freeze();
    doc()->commitTransaction();

    ASSERT_TRUE(result->isFreezed());
    EXPECT_EQ(source->getValue(), primary);
    EXPECT_THAT(references->getValues(), ::testing::ElementsAre(primary, secondary));

    ASSERT_TRUE(doc()->undo());
    EXPECT_EQ(doc()->getObject("FrozenLinkedResult"), nullptr);

    ASSERT_TRUE(doc()->redo());
    result = doc()->getObject("FrozenLinkedResult");
    ASSERT_NE(result, nullptr);
    ASSERT_TRUE(result->isFreezed());
    source = static_cast<App::PropertyLink*>(result->getPropertyByName("Source"));
    references = static_cast<App::PropertyLinkList*>(
        result->getPropertyByName("References"));
    ASSERT_NE(source, nullptr);
    ASSERT_NE(references, nullptr);
    EXPECT_EQ(source->getValue(), doc()->getObject("FrozenPrimarySource"));
    EXPECT_THAT(
        references->getValues(),
        ::testing::ElementsAre(doc()->getObject("FrozenPrimarySource"),
                               doc()->getObject("FrozenSecondarySource")));
}

TEST_F(DocumentTest, frozenOwnerRestoresLinksWhenTargetsAreDeletedFirst)
{
    doc()->setUndoMode(1);
    doc()->openTransaction("Create frozen deletion graph");
    auto* primary = addTimelineTestFeature(doc(), "FrozenDeletePrimary");
    auto* secondary = addTimelineTestFeature(doc(), "FrozenDeleteSecondary");
    auto* owner = addTimelineTestFeature(doc(), "FrozenDeleteOwner");
    auto* source = static_cast<App::PropertyLink*>(
        owner->addDynamicProperty("App::PropertyLink", "Source"));
    auto* references = static_cast<App::PropertyLinkList*>(
        owner->addDynamicProperty("App::PropertyLinkList", "References"));
    ASSERT_NE(source, nullptr);
    ASSERT_NE(references, nullptr);
    source->setValue(primary);
    references->setValues({primary, secondary});
    owner->freeze();
    doc()->commitTransaction();
    doc()->clearUndos();

    doc()->openTransaction("Delete frozen graph targets first");
    doc()->removeObject(primary);
    doc()->removeObject(secondary);
    doc()->removeObject(owner);
    doc()->commitTransaction();
    EXPECT_EQ(doc()->getObject("FrozenDeletePrimary"), nullptr);
    EXPECT_EQ(doc()->getObject("FrozenDeleteSecondary"), nullptr);
    EXPECT_EQ(doc()->getObject("FrozenDeleteOwner"), nullptr);

    ASSERT_TRUE(doc()->undo());
    primary = doc()->getObject("FrozenDeletePrimary");
    secondary = doc()->getObject("FrozenDeleteSecondary");
    owner = doc()->getObject("FrozenDeleteOwner");
    ASSERT_NE(primary, nullptr);
    ASSERT_NE(secondary, nullptr);
    ASSERT_NE(owner, nullptr);
    ASSERT_TRUE(owner->isFreezed());
    source = static_cast<App::PropertyLink*>(owner->getPropertyByName("Source"));
    references = static_cast<App::PropertyLinkList*>(
        owner->getPropertyByName("References"));
    ASSERT_NE(source, nullptr);
    ASSERT_NE(references, nullptr);
    EXPECT_EQ(source->getValue(), primary);
    EXPECT_THAT(references->getValues(), ::testing::ElementsAre(primary, secondary));

    ASSERT_TRUE(doc()->redo());
    EXPECT_EQ(doc()->getObject("FrozenDeletePrimary"), nullptr);
    EXPECT_EQ(doc()->getObject("FrozenDeleteSecondary"), nullptr);
    EXPECT_EQ(doc()->getObject("FrozenDeleteOwner"), nullptr);
}

TEST_F(DocumentTest, repeatedRestoreLabelNotificationDoesNotReserveLabelTwice)
{
    auto* original = doc()->addObject(
        "App::RepeatedRestoreLabelTestObject",
        "RepeatedRestoreLabel"
    );
    ASSERT_NE(original, nullptr);
    original->Label.setValue("Restored Python result");

    Base::FileInfo saved = timelineTestFile("repeated-restore-label");
    ASSERT_TRUE(doc()->saveCopy(saved.filePath().c_str()));
    auto* reopened = App::GetApplication().openDocument(saved.filePath().c_str());
    ASSERT_NE(reopened, nullptr);
    auto* restored = reopened->getObject("RepeatedRestoreLabel");
    ASSERT_NE(restored, nullptr);
    EXPECT_EQ(restored->Label.getStrValue(), "Restored Python result");

    reopened->removeObject(restored);
    auto* replacement = addTimelineTestFeature(reopened, "ReplacementResult");
    ASSERT_NE(replacement, nullptr);
    replacement->Label.setValue("Restored Python result");
    EXPECT_EQ(replacement->Label.getStrValue(), "Restored Python result");

    const std::string reopenedName = reopened->getName();
    EXPECT_TRUE(App::GetApplication().closeDocument(reopenedName.c_str()));
    saved.deleteFile();
}

TEST_F(DocumentTest, stagedExistingResourceAdoptionRequiresExactSelectionAndRollsBack)
{
    const auto markOperation = [](App::DocumentObject* object) {
        markTimelineTestOperation(object);
    };
    const auto markResource = [](App::DocumentObject* resource, App::DocumentObject* owner) {
        markTimelineTestResource(resource, owner);
    };

    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->openTransaction("Create existing operation");
    auto* existing = doc()->addObject(
        "App::FeaturePython",
        "ExistingTransform",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    markOperation(existing);
    auto* existingSecond = doc()->addObject(
        "App::FeaturePython",
        "ExistingTransformSecond",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    markOperation(existingSecond);
    auto* unstaged = doc()->addObject(
        "App::FeaturePython",
        "UnstagedOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    markOperation(unstaged);
    doc()->commitTransaction();
    const auto stateBeforeAdoption = [&]() {
        std::unordered_map<long, std::pair<bool, bool>> state;
        const auto operations = timeline->Operations.getValues();
        const auto visibility = timeline->VisibilityAtEnd.getValues();
        const auto suppression = timeline->SuppressionAtEnd.getValues();
        for (std::size_t index = 0; index < operations.size(); ++index) {
            state.emplace(
                operations[index]->getID(),
                std::pair {
                    visibility.test(index),
                    suppression.test(index),
                }
            );
        }
        return state;
    }();

    doc()->openTransaction("Cancel deliberate adoption");
    auto* cancelledOwner = doc()->addObject(
        "App::FeaturePython",
        "CancelledOwner",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    ASSERT_TRUE(timeline->isProvisionallyEnrolledByCurrentTransaction(cancelledOwner));
    ASSERT_FALSE(timeline->isProvisionallyEnrolledByCurrentTransaction(existing));
    timeline->stageExistingOperationResources(cancelledOwner, {existing, existingSecond});
    markOperation(cancelledOwner);
    markResource(existing, cancelledOwner);
    markResource(existingSecond, cancelledOwner);
    existing->Visibility.setValue(false);
    const auto timelineBeforeWrongOrder = timeline->Operations.getValues();
    const auto visibilityBeforeWrongOrder = timeline->VisibilityAtEnd.getValues();
    const auto suppressionBeforeWrongOrder = timeline->SuppressionAtEnd.getValues();
    const auto positionBeforeWrongOrder = timeline->Position.getValue();
    EXPECT_THROW(
        timeline->finalizeProvisionalOperationBlock(cancelledOwner, {cancelledOwner}, {existing, unstaged}),
        Base::RuntimeError
    );
    EXPECT_THROW(
        timeline->finalizeProvisionalOperationBlock(
            cancelledOwner,
            {cancelledOwner},
            {existingSecond, existing}
        ),
        Base::RuntimeError
    );
    EXPECT_EQ(timeline->Operations.getValues(), timelineBeforeWrongOrder);
    EXPECT_EQ(timeline->VisibilityAtEnd.getValues(), visibilityBeforeWrongOrder);
    EXPECT_EQ(timeline->SuppressionAtEnd.getValues(), suppressionBeforeWrongOrder);
    EXPECT_EQ(timeline->Position.getValue(), positionBeforeWrongOrder);
    timeline->finalizeProvisionalOperationBlock(
        cancelledOwner,
        {cancelledOwner},
        {existing, existingSecond}
    );
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(existing, existingSecond, cancelledOwner, unstaged)
    );
    const auto cancelledVisibility = timeline->VisibilityAtEnd.getValues();
    EXPECT_FALSE(cancelledVisibility.test(0));
    doc()->abortTransaction();

    EXPECT_EQ(doc()->getObject("CancelledOwner"), nullptr);
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(existing, existingSecond, unstaged)
    );
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(existing));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(existingSecond));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(existing), nullptr);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(existingSecond), nullptr);
    EXPECT_EQ(existing->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    EXPECT_EQ(existingSecond->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    EXPECT_TRUE(existing->Visibility.getValue());
    ASSERT_EQ(timeline->VisibilityAtEnd.getSize(), 3);
    ASSERT_EQ(timeline->SuppressionAtEnd.getSize(), 3);
    for (std::size_t index = 0; index < 3; ++index) {
        const auto* operation = timeline->Operations.getValues()[index];
        EXPECT_EQ(
            timeline->VisibilityAtEnd.getValues().test(index),
            stateBeforeAdoption.at(operation->getID()).first
        );
        EXPECT_EQ(
            timeline->SuppressionAtEnd.getValues().test(index),
            stateBeforeAdoption.at(operation->getID()).second
        );
    }

    doc()->openTransaction("Accept deliberate adoption");
    auto* acceptedOwner = doc()->addObject(
        "App::FeaturePython",
        "AcceptedOwner",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    timeline->stageExistingOperationResources(acceptedOwner, {existing, existingSecond});
    markOperation(acceptedOwner);
    markResource(existing, acceptedOwner);
    markResource(existingSecond, acceptedOwner);
    existing->Visibility.setValue(false);
    timeline->finalizeProvisionalOperationBlock(
        acceptedOwner,
        {acceptedOwner},
        {existing, existingSecond}
    );
    doc()->commitTransaction();

    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(existing, existingSecond, acceptedOwner, unstaged)
    );
    EXPECT_EQ(timeline->Position.getValue(), 4);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(existing), acceptedOwner);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(existingSecond), acceptedOwner);
    EXPECT_FALSE(timeline->VisibilityAtEnd.getValues().test(0));
    EXPECT_EQ(
        timeline->SuppressionAtEnd.getValues().test(0),
        stateBeforeAdoption.at(existing->getID()).second
    );
    EXPECT_TRUE(existing->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
                    ->testStatus(App::Property::Hidden));

    ASSERT_TRUE(doc()->undo());
    existing = doc()->getObject("ExistingTransform");
    existingSecond = doc()->getObject("ExistingTransformSecond");
    ASSERT_NE(existing, nullptr);
    ASSERT_NE(existingSecond, nullptr);
    EXPECT_EQ(doc()->getObject("AcceptedOwner"), nullptr);
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(existing));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(existing), nullptr);
    EXPECT_EQ(existing->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    EXPECT_TRUE(existing->Visibility.getValue());
    EXPECT_EQ(timeline->Position.getValue(), 3);

    ASSERT_TRUE(doc()->redo());
    existing = doc()->getObject("ExistingTransform");
    existingSecond = doc()->getObject("ExistingTransformSecond");
    acceptedOwner = doc()->getObject("AcceptedOwner");
    ASSERT_NE(existing, nullptr);
    ASSERT_NE(existingSecond, nullptr);
    ASSERT_NE(acceptedOwner, nullptr);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(existing, existingSecond, acceptedOwner, unstaged)
    );
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(existing), acceptedOwner);
    EXPECT_FALSE(timeline->VisibilityAtEnd.getValues().test(0));

    Base::FileInfo saved = timelineTestFile("staged-resource-timeline");
    ASSERT_TRUE(doc()->saveCopy(saved.filePath().c_str()));
    auto* reopened = App::GetApplication().openDocument(saved.filePath().c_str());
    ASSERT_NE(reopened, nullptr);
    auto* reopenedTimeline = App::DocumentTimeline::get(reopened);
    auto* reopenedFirst = reopened->getObject("ExistingTransform");
    auto* reopenedSecond = reopened->getObject("ExistingTransformSecond");
    auto* reopenedOwner = reopened->getObject("AcceptedOwner");
    auto* reopenedUnstaged = reopened->getObject("UnstagedOperation");
    ASSERT_NE(reopenedTimeline, nullptr);
    ASSERT_NE(reopenedFirst, nullptr);
    ASSERT_NE(reopenedSecond, nullptr);
    ASSERT_NE(reopenedOwner, nullptr);
    ASSERT_NE(reopenedUnstaged, nullptr);
    EXPECT_THAT(
        reopenedTimeline->Operations.getValues(),
        ::testing::ElementsAre(reopenedFirst, reopenedSecond, reopenedOwner, reopenedUnstaged)
    );
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(reopenedFirst), reopenedOwner);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(reopenedSecond), reopenedOwner);
    EXPECT_TRUE(reopenedFirst->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
                    ->testStatus(App::Property::Hidden));
    EXPECT_EQ(reopenedTimeline->Position.getValue(), 4);
    EXPECT_FALSE(reopenedTimeline->VisibilityAtEnd.getValues().test(0));
    const std::string reopenedName = reopened->getName();
    EXPECT_TRUE(App::GetApplication().closeDocument(reopenedName.c_str()));
    saved.deleteFile();
}

TEST_F(DocumentTest, abortedProvisionalEnrollmentCannotAuthorizeANewTransaction)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->openTransaction("Aborted import");
    auto* aborted = doc()->addObject(
        "App::FeaturePython",
        "ImportedOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    ASSERT_NE(aborted, nullptr);
    doc()->abortTransaction();
    ASSERT_EQ(doc()->getObject("ImportedOperation"), nullptr);

    doc()->openTransaction("Create replacement import");
    auto* replacement = doc()->addObject(
        "App::FeaturePython",
        "ImportedOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    ASSERT_NE(replacement, nullptr);
    doc()->commitTransaction();

    doc()->openTransaction("Try stale provisional identity");
    EXPECT_THROW(timeline->adoptImportedOperations({replacement}, {replacement}), Base::RuntimeError);
    doc()->abortTransaction();
}

TEST_F(DocumentTest, importedTimelineAdoptionRejectsIncompleteBlocksWithoutMutation)
{
    auto* baseline
        = doc()->addObject("App::FeaturePython", "Baseline", true, "Gui::ViewProviderDocumentObject");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    timeline->beginApplying();
    auto* operation = doc()->addObject(
        "App::FeaturePython",
        "ImportedOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* resource = doc()->addObject(
        "App::FeaturePython",
        "ImportedResource",
        true,
        "Gui::ViewProviderDocumentObject"
    );

    auto* operationRole = static_cast<App::PropertyString*>(
        operation->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
    );
    operationRole->setValue(App::DocumentTimeline::OperationRole);
    markTimelineTestMetadata(operationRole);
    auto* resourceRole = static_cast<App::PropertyString*>(
        resource->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
    );
    resourceRole->setValue(App::DocumentTimeline::ResourceRole);
    markTimelineTestMetadata(resourceRole);
    auto* owner = static_cast<App::PropertyLinkHidden*>(
        resource->addDynamicProperty("App::PropertyLinkHidden", App::DocumentTimeline::OwnerPropertyName)
    );
    owner->setValue(operation);
    markTimelineTestMetadata(owner);
    timeline->endApplying();

    const auto operationsBefore = timeline->Operations.getValues();
    const auto visibilityBefore = timeline->VisibilityAtEnd.getValues();
    const auto suppressionBefore = timeline->SuppressionAtEnd.getValues();
    const auto positionBefore = timeline->Position.getValue();
    doc()->openTransaction("Reject incomplete import");
    EXPECT_THROW(
        timeline->adoptImportedOperations({operation, resource}, {operation}),
        Base::RuntimeError
    );
    EXPECT_EQ(timeline->Operations.getValues(), operationsBefore);
    EXPECT_EQ(timeline->VisibilityAtEnd.getValues(), visibilityBefore);
    EXPECT_EQ(timeline->SuppressionAtEnd.getValues(), suppressionBefore);
    EXPECT_EQ(timeline->Position.getValue(), positionBefore);
    EXPECT_TRUE(operationRole->testStatus(App::Property::Hidden));
    doc()->abortTransaction();
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(baseline));
}

TEST_F(DocumentTest, importedSourceTimelineIsValidatedAndConsumed)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    timeline->beginApplying();
    auto* operation = doc()->addObject(
        "App::FeaturePython",
        "ImportedOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* transport = doc()->addObject<App::DocumentTimeline>("ImportedTimeline");
    timeline->endApplying();

    transport->beginApplying();
    transport->Operations.setValues({operation});
    transport->VisibilityAtEnd.setValues(boost::dynamic_bitset<>(1, 1));
    transport->SuppressionAtEnd.setValues(boost::dynamic_bitset<>(1, 0));
    transport->Position.setValue(1);
    transport->SchemaVersion.setValue(App::DocumentTimeline::CurrentSchemaVersion);
    transport->endApplying();
    const std::string transportName = transport->getNameInDocument();

    doc()->openTransaction("Consume imported timeline");
    timeline->adoptImportedOperations({operation, transport});
    doc()->commitTransaction();

    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(operation));
    EXPECT_EQ(doc()->getObject(transportName.c_str()), nullptr);
}

TEST_F(DocumentTest, importedRolledBackTimelineRestoresAcceptedEndVisibility)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    timeline->beginApplying();
    auto* operation = doc()->addObject(
        "App::FeaturePython",
        "ImportedFutureOperation",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    operation->Visibility.setValue(false);
    auto* transport = doc()->addObject<App::DocumentTimeline>("ImportedRolledBackTimeline");
    timeline->endApplying();

    transport->beginApplying();
    transport->Operations.setValues({operation});
    transport->VisibilityAtEnd.setValues(boost::dynamic_bitset<>(1, 1));
    transport->SuppressionAtEnd.setValues(boost::dynamic_bitset<>(1, 0));
    transport->Position.setValue(0);
    transport->SchemaVersion.setValue(App::DocumentTimeline::CurrentSchemaVersion);
    transport->endApplying();

    doc()->openTransaction("Adopt rolled-back source");
    timeline->adoptImportedOperations({operation, transport});
    doc()->commitTransaction();

    EXPECT_TRUE(operation->Visibility.getValue());
    EXPECT_TRUE(timeline->VisibilityAtEnd.getValues().test(0));
    EXPECT_EQ(timeline->Position.getValue(), 1);
}

TEST_F(DocumentTest, emptyImportedSourceTimelineIsStillConsumed)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    timeline->beginApplying();
    auto* transport = doc()->addObject<App::DocumentTimeline>("EmptyImportedTimeline");
    timeline->endApplying();
    transport->beginApplying();
    transport->Operations.setValues({});
    transport->VisibilityAtEnd.setValues({});
    transport->SuppressionAtEnd.setValues({});
    transport->Position.setValue(0);
    transport->SchemaVersion.setValue(App::DocumentTimeline::CurrentSchemaVersion);
    transport->endApplying();
    const std::string transportName = transport->getNameInDocument();

    doc()->openTransaction("Consume empty imported timeline");
    timeline->adoptImportedOperations({transport});
    doc()->commitTransaction();

    EXPECT_TRUE(timeline->Operations.getValues().empty());
    EXPECT_EQ(doc()->getObject(transportName.c_str()), nullptr);
}

TEST_F(DocumentTest, temporaryDocumentsDoNotCreateUserHistory)
{
    const auto name = App::GetApplication().getUniqueDocumentName("temporary");
    auto* temporary = App::GetApplication().newDocument(
        name.c_str(),
        "temporary",
        App::DocumentInitFlags {
            .createView = false,
            .temporary = true,
        }
    );
    ASSERT_NE(temporary, nullptr);
    auto* object = temporary->addObject(
        "App::FeaturePython",
        "StagingObject",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    EXPECT_EQ(App::DocumentTimeline::get(temporary), nullptr);
    temporary->adoptImportedTimelineOperations({object}, {});
    EXPECT_EQ(App::DocumentTimeline::get(temporary), nullptr);
    App::GetApplication().closeDocument(temporary);
}

TEST_F(DocumentTest, setupFailureRetainsOnePublishableProvisionalOperation)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->commitTransaction();

    doc()->openTransaction("Retain failed setup object");
    EXPECT_THROW(
        doc()->addObject(
            "App::TimelineThrowingSetupTestObject",
            "FailedSetup",
            true,
            "Gui::ViewProviderDocumentObject"
        ),
        Base::RuntimeError
    );

    auto* failed = doc()->getObject("FailedSetup");
    ASSERT_NE(failed, nullptr);
    EXPECT_TRUE(doc()->isProvisionallyEnrolledInTimelineByCurrentTransaction(failed));
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(failed));

    doc()->publishProvisionalTimelineOperationBlock(failed, {});
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(failed));
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(failed));

    doc()->abortTransaction();
    EXPECT_EQ(doc()->getObject("FailedSetup"), nullptr);
    EXPECT_TRUE(timeline->Operations.getValues().empty());
    EXPECT_EQ(timeline->Position.getValue(), 0);
}

TEST_F(DocumentTest, timelineCannotBeRemovedByCreationCallbacks)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    const std::string timelineName = timeline->getNameInDocument();
    auto connection = doc()->signalNewObject.connect([this, &timelineName](const App::DocumentObject&) {
        doc()->removeObject(timelineName.c_str());
    });

    auto* operation = addTimelineTestFeature(doc(), "CreationCallbackOperation");
    ASSERT_NE(operation, nullptr);
    EXPECT_EQ(App::DocumentTimeline::get(doc()), timeline);
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(operation));

    connection.disconnect();
    doc()->removeObject(timelineName.c_str());
    EXPECT_EQ(App::DocumentTimeline::get(doc()), nullptr);
}

TEST_F(DocumentTest, atomicPublicationTracksFirstExcludedContainerAndNestedResources)
{
    doc()->setUndoMode(1);
    ASSERT_EQ(App::DocumentTimeline::get(doc()), nullptr);

    doc()->openTransaction("Publish excluded nested containers");
    auto* operation = doc()->addObject(
        "App::DocumentObjectGroup",
        "PublishedContainer",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* parent = doc()->addObject(
        "App::DocumentObjectGroup",
        "PublishedParentResource",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* leaf = doc()->addObject(
        "App::DocumentObjectGroup",
        "PublishedLeafResource",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    EXPECT_TRUE(timeline->Operations.getValues().empty());

    const auto beforeRejectedPublication = captureTimelineTestState(timeline);
    EXPECT_THROW(
        doc()->publishProvisionalTimelineOperationBlock(operation, {parent, leaf}, {operation, parent}),
        Base::RuntimeError
    );
    expectTimelineTestState(timeline, beforeRejectedPublication);
    for (auto* object : {operation, parent, leaf}) {
        EXPECT_EQ(object->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
        EXPECT_EQ(object->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    }

    EXPECT_THROW(
        doc()->publishProvisionalTimelineOperationBlock(operation, {leaf, parent}, {parent, leaf}),
        Base::RuntimeError
    );
    expectTimelineTestState(timeline, beforeRejectedPublication);
    for (auto* object : {operation, parent, leaf}) {
        EXPECT_EQ(object->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
        EXPECT_EQ(object->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    }

    doc()->publishProvisionalTimelineOperationBlock(operation, {leaf, parent}, {parent, operation});
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(leaf, parent, operation));
    EXPECT_EQ(timeline->Position.getValue(), 3);
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(operation));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(leaf), parent);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(parent), operation);
    for (auto* candidate : {operation, parent, leaf}) {
        const auto* role =
            candidate->getPropertyByName(App::DocumentTimeline::RolePropertyName);
        ASSERT_NE(role, nullptr);
        EXPECT_TRUE(role->testStatus(App::Property::Hidden));
        EXPECT_TRUE(role->testStatus(App::Property::LockDynamic));
        EXPECT_TRUE(role->testStatus(App::Property::NoRecompute));
    }
    for (auto* candidate : {parent, leaf}) {
        const auto* owner =
            candidate->getPropertyByName(App::DocumentTimeline::OwnerPropertyName);
        ASSERT_NE(owner, nullptr);
        EXPECT_TRUE(owner->testStatus(App::Property::Hidden));
        EXPECT_TRUE(owner->testStatus(App::Property::LockDynamic));
        EXPECT_TRUE(owner->testStatus(App::Property::NoRecompute));
    }

    auto* secondOperation = doc()->addObject(
        "App::DocumentObjectGroup",
        "SecondPublishedContainer",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    auto* secondResource = doc()->addObject(
        "App::DocumentObjectGroup",
        "SecondPublishedResource",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    doc()->publishProvisionalTimelineOperationBlock(secondOperation, {secondResource});
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(leaf, parent, operation, secondResource, secondOperation)
    );
    EXPECT_EQ(timeline->Position.getValue(), 5);
    doc()->commitTransaction();

    ASSERT_TRUE(doc()->undo());
    EXPECT_EQ(doc()->getObject("PublishedContainer"), nullptr);
    EXPECT_EQ(doc()->getObject("PublishedParentResource"), nullptr);
    EXPECT_EQ(doc()->getObject("PublishedLeafResource"), nullptr);
    EXPECT_EQ(doc()->getObject("SecondPublishedContainer"), nullptr);
    EXPECT_EQ(doc()->getObject("SecondPublishedResource"), nullptr);

    ASSERT_TRUE(doc()->redo());
    timeline = App::DocumentTimeline::get(doc());
    operation = doc()->getObject("PublishedContainer");
    parent = doc()->getObject("PublishedParentResource");
    leaf = doc()->getObject("PublishedLeafResource");
    secondOperation = doc()->getObject("SecondPublishedContainer");
    secondResource = doc()->getObject("SecondPublishedResource");
    ASSERT_NE(timeline, nullptr);
    ASSERT_NE(operation, nullptr);
    ASSERT_NE(parent, nullptr);
    ASSERT_NE(leaf, nullptr);
    ASSERT_NE(secondOperation, nullptr);
    ASSERT_NE(secondResource, nullptr);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(leaf, parent, operation, secondResource, secondOperation)
    );
    EXPECT_EQ(timeline->Position.getValue(), 5);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(leaf), parent);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(parent), operation);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(secondResource), secondOperation);

    Base::FileInfo saved = timelineTestFile("timeline-atomic-publication");
    ASSERT_TRUE(doc()->saveCopy(saved.filePath().c_str()));
    auto* reopened = App::GetApplication().openDocument(saved.filePath().c_str());
    ASSERT_NE(reopened, nullptr);
    auto* reopenedTimeline = App::DocumentTimeline::get(reopened);
    auto* reopenedOperation = reopened->getObject("PublishedContainer");
    auto* reopenedParent = reopened->getObject("PublishedParentResource");
    auto* reopenedLeaf = reopened->getObject("PublishedLeafResource");
    auto* reopenedSecondOperation = reopened->getObject("SecondPublishedContainer");
    auto* reopenedSecondResource = reopened->getObject("SecondPublishedResource");
    ASSERT_NE(reopenedTimeline, nullptr);
    ASSERT_NE(reopenedOperation, nullptr);
    ASSERT_NE(reopenedParent, nullptr);
    ASSERT_NE(reopenedLeaf, nullptr);
    ASSERT_NE(reopenedSecondOperation, nullptr);
    ASSERT_NE(reopenedSecondResource, nullptr);
    EXPECT_THAT(
        reopenedTimeline->Operations.getValues(),
        ::testing::ElementsAre(
            reopenedLeaf,
            reopenedParent,
            reopenedOperation,
            reopenedSecondResource,
            reopenedSecondOperation
        )
    );
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(reopenedLeaf), reopenedParent);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(reopenedParent), reopenedOperation);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(reopenedSecondResource), reopenedSecondOperation);
    const std::string reopenedName = reopened->getName();
    EXPECT_TRUE(App::GetApplication().closeDocument(reopenedName.c_str()));
    saved.deleteFile();

    const auto published = captureTimelineTestState(timeline);
    doc()->openTransaction("Reject reused creation provenance");
    EXPECT_THROW(
        doc()->publishProvisionalTimelineOperationBlock(operation, {leaf, parent}, {parent, operation}),
        Base::RuntimeError
    );
    expectTimelineTestState(timeline, published);
    doc()->abortTransaction();
}

TEST_F(DocumentTest, atomicPublicationSupportsSequentialPendingBlocksAndBaselineRefresh)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->commitTransaction();

    doc()->openTransaction("Publish several creation generations");
    auto* firstLeaf = addTimelineTestFeature(doc(), "FirstPendingLeaf");
    auto* firstParent = addTimelineTestFeature(doc(), "FirstPendingParent");
    auto* firstOperation = addTimelineTestFeature(doc(), "FirstPendingOperation");
    auto* secondResource = addTimelineTestFeature(doc(), "SecondPendingResource");
    auto* secondOperation = addTimelineTestFeature(doc(), "SecondPendingOperation");

    doc()->publishProvisionalTimelineOperationBlock(
        firstOperation,
        {firstLeaf, firstParent},
        {firstParent, firstOperation}
    );
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(firstLeaf, firstParent, firstOperation, secondResource, secondOperation)
    );

    doc()->publishProvisionalTimelineOperationBlock(secondOperation, {secondResource});
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(firstLeaf, firstParent, firstOperation, secondResource, secondOperation)
    );

    firstOperation->Visibility.setValue(false);
    timeline->captureVisibility();
    ASSERT_FALSE(timeline->VisibilityAtEnd.getValues().test(2));

    auto* thirdResource = addTimelineTestFeature(doc(), "ThirdGenerationResource");
    auto* thirdOperation = addTimelineTestFeature(doc(), "ThirdGenerationOperation");
    doc()->publishProvisionalTimelineOperationBlock(thirdOperation, {thirdResource});

    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(
            firstLeaf,
            firstParent,
            firstOperation,
            secondResource,
            secondOperation,
            thirdResource,
            thirdOperation
        )
    );
    EXPECT_EQ(timeline->Position.getValue(), 7);
    EXPECT_FALSE(timeline->VisibilityAtEnd.getValues().test(2));
    doc()->commitTransaction();
}

TEST_F(DocumentTest, atomicPublicationRollsBackMetadataWhenACallbackThrows)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->commitTransaction();

    doc()->openTransaction("Reject hostile publication callback");
    auto* resource = addTimelineTestFeature(doc(), "CallbackResource");
    auto* operation = addTimelineTestFeature(doc(), "CallbackOperation");
    const auto baseline = captureTimelineTestState(timeline);

    bool callbackInvoked = false;
    auto connection = App::GetApplication().signalAppendDynamicProperty.connect(
        [&callbackInvoked, operation](const App::Property& property) {
            if (!callbackInvoked && property.getContainer() == operation
                && std::string_view(property.getName()) == App::DocumentTimeline::RolePropertyName) {
                callbackInvoked = true;
                throw Base::RuntimeError("intentional hostile metadata callback");
            }
        }
    );
    EXPECT_THROW(
        doc()->publishProvisionalTimelineOperationBlock(operation, {resource}),
        Base::RuntimeError
    );
    connection.disconnect();

    EXPECT_TRUE(callbackInvoked);
    expectTimelineTestState(timeline, baseline);
    EXPECT_EQ(operation->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(operation->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    EXPECT_EQ(resource->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(resource->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    doc()->abortTransaction();
}

TEST_F(DocumentTest, atomicPublicationRejectsNonCanonicalMetadataWithoutChangingStatus)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->commitTransaction();

    doc()->openTransaction("Reject noncanonical timeline metadata");
    auto* operation = addTimelineTestFeature(doc(), "NonCanonicalMetadataOperation");
    auto* role = static_cast<App::PropertyString*>(
        operation->addDynamicProperty("App::PropertyString", App::DocumentTimeline::RolePropertyName)
    );
    role->setValue(App::DocumentTimeline::OperationRole);
    role->setStatus(App::Property::ReadOnly, true);
    const unsigned long originalStatus = role->getStatus();
    const auto baseline = captureTimelineTestState(timeline);

    EXPECT_THROW(doc()->publishProvisionalTimelineOperationBlock(operation, {}), Base::RuntimeError);
    expectTimelineTestState(timeline, baseline);
    EXPECT_EQ(role->getStatus(), originalStatus);
    EXPECT_TRUE(role->testStatus(App::Property::ReadOnly));
    EXPECT_FALSE(role->testStatus(App::Property::Hidden));
    EXPECT_FALSE(role->testStatus(App::Property::LockDynamic));
    doc()->abortTransaction();
}

TEST_F(DocumentTest, hostilePythonSequenceDeletionIsRejectedWithoutStaleTimelineAccess)
{
    Base::PyGILStateLocker gil;
    auto* victim = addTimelineTestFeature(doc(), "SequenceVictim");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    ASSERT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(victim));

    PyObject* globals = PyDict_New();
    ASSERT_NE(globals, nullptr);
    ASSERT_EQ(PyDict_SetItemString(globals, "__builtins__", PyEval_GetBuiltins()), 0);
    PyObject* pyDocument = doc()->getPyObject();
    PyObject* pyVictim = victim->getPyObject();
    ASSERT_NE(pyDocument, nullptr);
    ASSERT_NE(pyVictim, nullptr);
    ASSERT_EQ(PyDict_SetItemString(globals, "document", pyDocument), 0);
    ASSERT_EQ(PyDict_SetItemString(globals, "victim", pyVictim), 0);
    Py_DECREF(pyDocument);
    Py_DECREF(pyVictim);

    constexpr const char* hostileSequenceDefinition = R"PY(
class HostileTimelineSequence:
    def __len__(self):
        return 2

    def __getitem__(self, index):
        if index == 0:
            return victim
        if index == 1:
            document.removeObject("SequenceVictim")
            return victim
        raise IndexError
)PY";
    PyObject* definition = PyRun_String(hostileSequenceDefinition, Py_file_input, globals, globals);
    ASSERT_NE(definition, nullptr);
    Py_DECREF(definition);
    PyObject* sequenceClass = PyDict_GetItemString(globals, "HostileTimelineSequence");
    ASSERT_NE(sequenceClass, nullptr);
    PyObject* sequence = PyObject_CallNoArgs(sequenceClass);
    ASSERT_NE(sequence, nullptr);
    pyDocument = doc()->getPyObject();
    ASSERT_NE(pyDocument, nullptr);

    PyObject* result = PyObject_CallMethod(pyDocument, "semanticTimelineCopyClosure", "O", sequence);
    EXPECT_EQ(result, nullptr);
    EXPECT_NE(PyErr_Occurred(), nullptr);
    PyErr_Clear();
    Py_XDECREF(result);
    Py_DECREF(pyDocument);
    Py_DECREF(sequence);
    Py_DECREF(globals);

    EXPECT_EQ(doc()->getObject("SequenceVictim"), nullptr);
    EXPECT_TRUE(timeline->Operations.getValues().empty());
}

TEST_F(DocumentTest, abortedExcludedCreationProvenanceCannotAuthorizeReplacementIdentity)
{
    doc()->setUndoMode(1);
    doc()->openTransaction("Create then abort excluded container");
    auto* aborted = doc()->addObject(
        "App::DocumentObjectGroup",
        "ReusedContainerName",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    const long abortedId = aborted->getID();
    ASSERT_NE(App::DocumentTimeline::get(doc()), nullptr);
    doc()->abortTransaction();
    EXPECT_EQ(doc()->getObject("ReusedContainerName"), nullptr);

    doc()->openTransaction("Create exact replacement container");
    auto* replacement = doc()->addObject(
        "App::DocumentObjectGroup",
        "ReusedContainerName",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    ASSERT_NE(replacement->getID(), abortedId);
    doc()->publishProvisionalTimelineOperationBlock(replacement, {});
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(replacement));
    doc()->commitTransaction();
}

TEST_F(DocumentTest, provisionalInternalClassificationCanReenrollAndAbortExactly)
{
    doc()->setUndoMode(1);
    auto* first = addTimelineTestFeature(doc(), "FirstOperation");
    auto* future = addTimelineTestFeature(doc(), "FutureOperation");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    timeline->beginApplying();
    timeline->Position.setValue(1);
    timeline->endApplying();
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Classify provisional internal object");
    auto* internal = addTimelineTestFeature(doc(), "ProvisionalInternal");
    ASSERT_TRUE(doc()->isProvisionallyEnrolledInTimelineByCurrentTransaction(internal));
    ASSERT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(first, internal, future));
    EXPECT_EQ(timeline->Position.getValue(), 2);
    const auto beforeWrongClassifier = captureTimelineTestState(timeline);
    EXPECT_THROW(doc()->classifyExistingTimelineLeafInternalObject(internal), Base::RuntimeError);
    expectTimelineTestState(timeline, beforeWrongClassifier);

    doc()->classifyProvisionalTimelineInternalObject(internal);
    EXPECT_FALSE(doc()->isProvisionallyEnrolledInTimelineByCurrentTransaction(internal));
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(first, future));
    EXPECT_EQ(timeline->Position.getValue(), 1);
    auto* role = dynamic_cast<App::PropertyString*>(
        internal->getPropertyByName(App::DocumentTimeline::RolePropertyName)
    );
    ASSERT_NE(role, nullptr);
    EXPECT_STREQ(role->getValue(), App::DocumentTimeline::InternalRole);
    EXPECT_TRUE(role->testStatus(App::Property::Hidden));
    EXPECT_TRUE(role->testStatus(App::Property::LockDynamic));

    role->setValue(App::DocumentTimeline::OperationRole);
    EXPECT_TRUE(doc()->isProvisionallyEnrolledInTimelineByCurrentTransaction(internal));
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(first, internal, future));
    EXPECT_EQ(timeline->Position.getValue(), 2);

    doc()->abortTransaction();
    EXPECT_EQ(doc()->getObject("ProvisionalInternal"), nullptr);
    expectTimelineTestState(timeline, baseline);
}

TEST_F(DocumentTest, internalClassificationRejectsLaterIdentityRemovedByRoleCallback)
{
    doc()->setUndoMode(1);
    auto* retained = addTimelineTestFeature(doc(), "ClassificationRetained");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    doc()->commitTransaction();
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Reject hostile internal classification callback");
    auto* internal = addTimelineTestFeature(doc(), "ClassificationInternal");
    addTimelineTestFeature(doc(), "ClassificationLater");

    bool callbackInvoked = false;
    auto connection = doc()->signalChangedObject.connect(
        [this, internal, &callbackInvoked](
            const App::DocumentObject& changed,
            const App::Property& property
        ) {
            if (!callbackInvoked && &changed == internal
                && std::string_view(property.getName())
                    == App::DocumentTimeline::RolePropertyName) {
                callbackInvoked = true;
                doc()->removeObject("ClassificationLater");
            }
        }
    );
    EXPECT_THROW(
        doc()->classifyProvisionalTimelineInternalObject(internal),
        Base::RuntimeError
    );
    connection.disconnect();

    EXPECT_TRUE(callbackInvoked);
    doc()->abortTransaction();
    EXPECT_EQ(doc()->getObject("ClassificationInternal"), nullptr);
    EXPECT_EQ(doc()->getObject("ClassificationLater"), nullptr);
    expectTimelineTestState(timeline, baseline);
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(retained));
}

TEST_F(DocumentTest, existingInternalLeafMigrationAbortsUndoesRedoesAndReopens)
{
    doc()->setUndoMode(1);
    auto* existing = addTimelineTestFeature(doc(), "LegacyInternalLeaf");
    auto* retained = addTimelineTestFeature(doc(), "RetainedOperation");
    auto* timeline = App::DocumentTimeline::get(doc());
    ASSERT_NE(timeline, nullptr);
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Cancel internal leaf migration");
    doc()->classifyExistingTimelineLeafInternalObject(existing);
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(retained));
    doc()->abortTransaction();
    existing = doc()->getObject("LegacyInternalLeaf");
    ASSERT_NE(existing, nullptr);
    EXPECT_EQ(existing->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    expectTimelineTestState(timeline, baseline);

    doc()->openTransaction("Accept internal leaf migration");
    doc()->classifyExistingTimelineLeafInternalObject(existing);
    doc()->commitTransaction();
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(retained));
    auto* role = dynamic_cast<App::PropertyString*>(
        existing->getPropertyByName(App::DocumentTimeline::RolePropertyName)
    );
    ASSERT_NE(role, nullptr);
    EXPECT_STREQ(role->getValue(), App::DocumentTimeline::InternalRole);

    ASSERT_TRUE(doc()->undo());
    existing = doc()->getObject("LegacyInternalLeaf");
    retained = doc()->getObject("RetainedOperation");
    ASSERT_NE(existing, nullptr);
    ASSERT_NE(retained, nullptr);
    EXPECT_EQ(existing->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(existing, retained));

    ASSERT_TRUE(doc()->redo());
    existing = doc()->getObject("LegacyInternalLeaf");
    retained = doc()->getObject("RetainedOperation");
    ASSERT_NE(existing, nullptr);
    ASSERT_NE(retained, nullptr);
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(retained));
    role = dynamic_cast<App::PropertyString*>(
        existing->getPropertyByName(App::DocumentTimeline::RolePropertyName)
    );
    ASSERT_NE(role, nullptr);
    EXPECT_STREQ(role->getValue(), App::DocumentTimeline::InternalRole);

    Base::FileInfo saved = timelineTestFile("timeline-internal-leaf");
    ASSERT_TRUE(doc()->saveCopy(saved.filePath().c_str()));
    auto* reopened = App::GetApplication().openDocument(saved.filePath().c_str());
    ASSERT_NE(reopened, nullptr);
    auto* reopenedTimeline = App::DocumentTimeline::get(reopened);
    auto* reopenedInternal = reopened->getObject("LegacyInternalLeaf");
    auto* reopenedRetained = reopened->getObject("RetainedOperation");
    ASSERT_NE(reopenedTimeline, nullptr);
    ASSERT_NE(reopenedInternal, nullptr);
    ASSERT_NE(reopenedRetained, nullptr);
    EXPECT_THAT(reopenedTimeline->Operations.getValues(), ::testing::ElementsAre(reopenedRetained));
    auto* reopenedRole = dynamic_cast<App::PropertyString*>(
        reopenedInternal->getPropertyByName(App::DocumentTimeline::RolePropertyName)
    );
    ASSERT_NE(reopenedRole, nullptr);
    EXPECT_STREQ(reopenedRole->getValue(), App::DocumentTimeline::InternalRole);
    const std::string reopenedName = reopened->getName();
    EXPECT_TRUE(App::GetApplication().closeDocument(reopenedName.c_str()));
    saved.deleteFile();
}

TEST_F(DocumentTest, existingOperationBlockAdoptionIsExactTransactionalAndPersistent)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());

    auto* before = addTimelineTestFeature(doc(), "BeforeLegacyResult");
    auto* root = addTimelineTestFeature(doc(), "LegacyResultRoot");
    auto* parent = addTimelineTestFeature(doc(), "LegacyResultParent");
    auto* leaf = addTimelineTestFeature(doc(), "LegacyResultLeaf");
    auto* after = addTimelineTestFeature(doc(), "AfterLegacyResult");
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(before, root, parent, leaf, after)
    );

    root->Visibility.setValue(false);
    parent->Visibility.setValue(true);
    leaf->Visibility.setValue(false);
    timeline->captureVisibility();
    const auto beforeAdoption = captureTimelineTestState(timeline);

    doc()->openTransaction("Adopt legacy result graph");
    doc()->adoptExistingTimelineOperationBlock(root, {leaf, parent}, {parent, root});
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(before, leaf, parent, root, after)
    );
    EXPECT_EQ(timeline->Position.getValue(), beforeAdoption.position);
    EXPECT_EQ(timeline->VisibilityAtEnd.getValues().test(1), false);
    EXPECT_EQ(timeline->VisibilityAtEnd.getValues().test(2), true);
    EXPECT_EQ(timeline->VisibilityAtEnd.getValues().test(3), false);
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineOperationRole(root));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineResourceRole(parent));
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineResourceRole(leaf));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(parent), root);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(leaf), parent);
    for (auto* candidate : {root, parent, leaf}) {
        const auto* role =
            candidate->getPropertyByName(App::DocumentTimeline::RolePropertyName);
        ASSERT_NE(role, nullptr);
        EXPECT_TRUE(role->testStatus(App::Property::Hidden));
        EXPECT_TRUE(role->testStatus(App::Property::LockDynamic));
        EXPECT_TRUE(role->testStatus(App::Property::NoRecompute));
    }
    doc()->commitTransaction();

    ASSERT_TRUE(doc()->undo());
    root = doc()->getObject("LegacyResultRoot");
    parent = doc()->getObject("LegacyResultParent");
    leaf = doc()->getObject("LegacyResultLeaf");
    ASSERT_NE(root, nullptr);
    ASSERT_NE(parent, nullptr);
    ASSERT_NE(leaf, nullptr);
    expectTimelineTestState(timeline, beforeAdoption);
    EXPECT_EQ(root->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(parent->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(parent->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    EXPECT_EQ(leaf->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(leaf->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);

    ASSERT_TRUE(doc()->redo());
    root = doc()->getObject("LegacyResultRoot");
    parent = doc()->getObject("LegacyResultParent");
    leaf = doc()->getObject("LegacyResultLeaf");
    ASSERT_NE(root, nullptr);
    ASSERT_NE(parent, nullptr);
    ASSERT_NE(leaf, nullptr);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(
            doc()->getObject("BeforeLegacyResult"),
            leaf,
            parent,
            root,
            doc()->getObject("AfterLegacyResult")
        )
    );
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(parent), root);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(leaf), parent);

    Base::FileInfo saved = timelineTestFile("timeline-existing-block-adoption");
    ASSERT_TRUE(doc()->saveCopy(saved.filePath().c_str()));
    auto* reopened = App::GetApplication().openDocument(saved.filePath().c_str());
    ASSERT_NE(reopened, nullptr);
    auto* reopenedTimeline = App::DocumentTimeline::get(reopened);
    auto* reopenedRoot = reopened->getObject("LegacyResultRoot");
    auto* reopenedParent = reopened->getObject("LegacyResultParent");
    auto* reopenedLeaf = reopened->getObject("LegacyResultLeaf");
    ASSERT_NE(reopenedTimeline, nullptr);
    ASSERT_NE(reopenedRoot, nullptr);
    ASSERT_NE(reopenedParent, nullptr);
    ASSERT_NE(reopenedLeaf, nullptr);
    EXPECT_THAT(
        reopenedTimeline->Operations.getValues(),
        ::testing::ElementsAre(
            reopened->getObject("BeforeLegacyResult"),
            reopenedLeaf,
            reopenedParent,
            reopenedRoot,
            reopened->getObject("AfterLegacyResult")
        )
    );
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(reopenedParent), reopenedRoot);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(reopenedLeaf), reopenedParent);
    const std::string reopenedName = reopened->getName();
    EXPECT_TRUE(App::GetApplication().closeDocument(reopenedName.c_str()));
    saved.deleteFile();
}

TEST_F(DocumentTest, existingOperationBlockAdoptionPreservesFutureMarkerSide)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());
    auto* active = addTimelineTestFeature(doc(), "ActiveBeforeLegacy");
    auto* root = addTimelineTestFeature(doc(), "FutureLegacyRoot");
    auto* resource = addTimelineTestFeature(doc(), "FutureLegacyResource");
    auto* later = addTimelineTestFeature(doc(), "LaterFutureOperation");
    timeline->Position.setValue(1);
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(active, root, resource, later)
    );

    doc()->openTransaction("Adopt future legacy graph");
    doc()->adoptExistingTimelineOperationBlock(root, {resource});
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(active, resource, root, later)
    );
    EXPECT_EQ(timeline->Position.getValue(), 1);
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(resource), root);
    doc()->abortTransaction();

    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(active, root, resource, later)
    );
    EXPECT_EQ(timeline->Position.getValue(), 1);
    EXPECT_EQ(resource->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(resource->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
}

TEST_F(DocumentTest, existingOperationBlockAdoptionRejectsSplitOrNoncontiguousInput)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    auto* first = addTimelineTestFeature(doc(), "LegacyFirst");
    auto* middle = addTimelineTestFeature(doc(), "LegacyMiddle");
    auto* last = addTimelineTestFeature(doc(), "LegacyLast");
    timeline->Position.setValue(1);

    doc()->openTransaction("Reject invalid legacy graph");
    const auto baseline = captureTimelineTestState(timeline);
    EXPECT_THROW(
        doc()->adoptExistingTimelineOperationBlock(first, {middle}),
        Base::RuntimeError
    );
    expectTimelineTestState(timeline, baseline);
    EXPECT_THROW(
        doc()->adoptExistingTimelineOperationBlock(first, {last}),
        Base::RuntimeError
    );
    expectTimelineTestState(timeline, baseline);
    EXPECT_EQ(first->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(middle->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(last->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    doc()->abortTransaction();
}

TEST_F(DocumentTest, existingOperationBlockAdoptionSelfRollsBackHostileMetadataCallback)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());
    auto* root = addTimelineTestFeature(doc(), "HostileLegacyRoot");
    auto* resource = addTimelineTestFeature(doc(), "HostileLegacyResource");
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Reject hostile legacy adoption callback");
    bool callbackInvoked = false;
    auto connection = App::GetApplication().signalAppendDynamicProperty.connect(
        [&callbackInvoked, root](const App::Property& property) {
            if (!callbackInvoked && property.getContainer() == root
                && std::string_view(property.getName())
                    == App::DocumentTimeline::RolePropertyName) {
                callbackInvoked = true;
                throw Base::RuntimeError("intentional hostile adoption callback");
            }
        }
    );
    EXPECT_THROW(
        doc()->adoptExistingTimelineOperationBlock(root, {resource}),
        Base::RuntimeError
    );
    connection.disconnect();

    EXPECT_TRUE(callbackInvoked);
    expectTimelineTestState(timeline, baseline);
    EXPECT_EQ(root->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(root->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    EXPECT_EQ(resource->getPropertyByName(App::DocumentTimeline::RolePropertyName), nullptr);
    EXPECT_EQ(resource->getPropertyByName(App::DocumentTimeline::OwnerPropertyName), nullptr);
    doc()->abortTransaction();
}

TEST_F(DocumentTest, semanticSegmentReplacementIsManyToManyAtomicAndPersistent)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());

    doc()->openTransaction("Create two old semantic blocks");
    auto* oldRootOne = addTimelineTestFeature(doc(), "OldRootOne");
    auto* oldResourceOne = addTimelineTestFeature(doc(), "OldResourceOne");
    markTimelineTestOperation(oldRootOne);
    markTimelineTestResource(oldResourceOne, oldRootOne);
    timeline->finalizeProvisionalOperationBlock(oldRootOne, {oldResourceOne, oldRootOne});

    auto* oldRootTwo = addTimelineTestFeature(doc(), "OldRootTwo");
    auto* oldResourceTwo = addTimelineTestFeature(doc(), "OldResourceTwo");
    markTimelineTestOperation(oldRootTwo);
    markTimelineTestResource(oldResourceTwo, oldRootTwo);
    timeline->finalizeProvisionalOperationBlock(oldRootTwo, {oldResourceTwo, oldRootTwo});
    doc()->commitTransaction();
    timeline->beginApplying();
    timeline->Position.setValue(2);
    timeline->endApplying();
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(oldResourceOne, oldRootOne, oldResourceTwo, oldRootTwo)
    );

    doc()->openTransaction("Replace two old roots with three new roots");
    timeline->stageOperationSegmentReplacement({{oldRootOne, oldRootTwo}});
    const std::string oldResourceOneName = oldResourceOne->getNameInDocument();
    const std::string oldRootOneName = oldRootOne->getNameInDocument();
    const std::string oldResourceTwoName = oldResourceTwo->getNameInDocument();
    const std::string oldRootTwoName = oldRootTwo->getNameInDocument();
    doc()->removeObject(oldResourceOneName.c_str());
    doc()->removeObject(oldRootOneName.c_str());
    doc()->removeObject(oldResourceTwoName.c_str());
    doc()->removeObject(oldRootTwoName.c_str());

    auto* newRootA = addTimelineTestFeature(doc(), "NewRootA");
    auto* newRootC = addTimelineTestFeature(doc(), "NewRootC");
    auto* newRootB = addTimelineTestFeature(doc(), "NewRootB");
    markTimelineTestOperation(newRootA);
    markTimelineTestOperation(newRootB);
    markTimelineTestOperation(newRootC);
    auto* newResourceC = addTimelineTestFeature(doc(), "NewResourceC");
    auto* newResourceA = addTimelineTestFeature(doc(), "NewResourceA");
    markTimelineTestResource(newResourceC, newRootC);
    markTimelineTestResource(newResourceA, newRootA);

    App::TimelineSegmentReplacementMapping mapping {
        .stagedSegmentIndex = 0,
        .orderedNewBlocks = {
            {newResourceA, newRootA},
            {newRootB},
            {newResourceC, newRootC},
        },
        .stateSourceIndices = {0, 1, 2, 3, 3},
        .consumerReplacementIndices = {-1, -1, -1, -1},
        .activeRootCount = 2,
    };
    auto invalid = mapping;
    invalid.orderedNewBlocks[1] = {newRootA};
    const auto beforeInvalid = captureTimelineTestState(timeline);
    EXPECT_THROW(timeline->finalizeProvisionalOperationSegmentReplacement({invalid}), Base::ValueError);
    expectTimelineTestState(timeline, beforeInvalid);

    timeline->finalizeProvisionalOperationSegmentReplacement({mapping});
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(newResourceA, newRootA, newRootB, newResourceC, newRootC)
    );
    EXPECT_EQ(timeline->Position.getValue(), 3);
    doc()->commitTransaction();

    ASSERT_TRUE(doc()->undo());
    oldRootOne = doc()->getObject("OldRootOne");
    oldResourceOne = doc()->getObject("OldResourceOne");
    oldRootTwo = doc()->getObject("OldRootTwo");
    oldResourceTwo = doc()->getObject("OldResourceTwo");
    ASSERT_NE(oldRootOne, nullptr);
    ASSERT_NE(oldResourceOne, nullptr);
    ASSERT_NE(oldRootTwo, nullptr);
    ASSERT_NE(oldResourceTwo, nullptr);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(oldResourceOne, oldRootOne, oldResourceTwo, oldRootTwo)
    );
    EXPECT_EQ(timeline->Position.getValue(), 2);

    ASSERT_TRUE(doc()->redo());
    newRootA = doc()->getObject("NewRootA");
    newResourceA = doc()->getObject("NewResourceA");
    newRootB = doc()->getObject("NewRootB");
    newRootC = doc()->getObject("NewRootC");
    newResourceC = doc()->getObject("NewResourceC");
    ASSERT_NE(newRootA, nullptr);
    ASSERT_NE(newResourceA, nullptr);
    ASSERT_NE(newRootB, nullptr);
    ASSERT_NE(newRootC, nullptr);
    ASSERT_NE(newResourceC, nullptr);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(newResourceA, newRootA, newRootB, newResourceC, newRootC)
    );
    EXPECT_EQ(timeline->Position.getValue(), 3);

    Base::FileInfo saved = timelineTestFile("timeline-segment-replacement");
    ASSERT_TRUE(doc()->saveCopy(saved.filePath().c_str()));
    auto* reopened = App::GetApplication().openDocument(saved.filePath().c_str());
    ASSERT_NE(reopened, nullptr);
    auto* reopenedTimeline = App::DocumentTimeline::get(reopened);
    ASSERT_NE(reopenedTimeline, nullptr);
    EXPECT_THAT(
        reopenedTimeline->Operations.getValues(),
        ::testing::ElementsAre(
            reopened->getObject("NewResourceA"),
            reopened->getObject("NewRootA"),
            reopened->getObject("NewRootB"),
            reopened->getObject("NewResourceC"),
            reopened->getObject("NewRootC")
        )
    );
    EXPECT_EQ(reopenedTimeline->Position.getValue(), 3);
    const std::string reopenedName = reopened->getName();
    EXPECT_TRUE(App::GetApplication().closeDocument(reopenedName.c_str()));
    saved.deleteFile();
}

TEST_F(DocumentTest, resourceReconciliationRetainsAddsRetiresAndRestoresExactGraph)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());

    doc()->openTransaction("Create nested resource graph");
    auto* owner = addTimelineTestFeature(doc(), "ResourceOwner");
    auto* parent = addTimelineTestFeature(doc(), "ParentResource");
    auto* oldLeaf = addTimelineTestFeature(doc(), "OldLeafResource");
    auto* sibling = addTimelineTestFeature(doc(), "SiblingResource");
    markTimelineTestOperation(owner);
    markTimelineTestResource(parent, owner);
    markTimelineTestResource(oldLeaf, parent);
    markTimelineTestResource(sibling, owner);
    timeline->finalizeProvisionalOperationBlock(owner, {oldLeaf, parent, sibling, owner});
    doc()->commitTransaction();

    auto* consumer = addTimelineTestFeature(doc(), "RetainedConsumer");
    markTimelineTestOperation(consumer);
    auto* consumerLink = static_cast<App::PropertyLink*>(
        consumer->addDynamicProperty("App::PropertyLink", "ResourceReference")
    );
    consumerLink->setValue(oldLeaf);
    auto* ownerEditor = static_cast<App::PropertyLinkHidden*>(
        owner->addDynamicProperty("App::PropertyLinkHidden", App::DocumentTimeline::EditorPropertyName)
    );
    ownerEditor->setValue(oldLeaf);
    markTimelineTestMetadata(ownerEditor);
    auto* consumerReplacements = static_cast<App::PropertyLinkListHidden*>(consumer->addDynamicProperty(
        "App::PropertyLinkListHidden",
        App::DocumentTimeline::ReplacedInputsPropertyName
    ));
    consumerReplacements->setValues({oldLeaf});
    markTimelineTestMetadata(consumerReplacements);
    const unsigned long ownerEditorStatus = timelineTestMetadataPolicyStatus(ownerEditor);
    const unsigned long consumerReplacementsStatus = timelineTestMetadataPolicyStatus(
        consumerReplacements
    );
    const unsigned long oldLeafRoleStatus = timelineTestMetadataPolicyStatus(
        oldLeaf->getPropertyByName(App::DocumentTimeline::RolePropertyName)
    );
    const unsigned long oldLeafOwnerStatus = timelineTestMetadataPolicyStatus(
        oldLeaf->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
    );
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(oldLeaf, parent, sibling, owner, consumer)
    );

    doc()->openTransaction("Reconcile nested resource graph");
    timeline->stageOperationResourceReconciliation(owner, {parent, sibling});
    auto* replacementLeaf = addTimelineTestFeature(doc(), "ReplacementLeaf");
    markTimelineTestResource(replacementLeaf, parent);
    consumerLink->setValue(replacementLeaf);
    ownerEditor->setValue(replacementLeaf);
    consumerReplacements->setValues({replacementLeaf});

    const auto beforeInvalidConsumer = captureTimelineTestState(timeline);
    App::TimelineResourceReconciliationMapping invalidConsumer {
        .owner = owner,
        .orderedFinalResources = {replacementLeaf, parent},
        .stateSourceIndices = {0, 1},
        .consumerReplacementIndices = {-1, -1, -1},
    };
    EXPECT_THROW(
        timeline->finalizeProvisionalOperationResourceReconciliation(invalidConsumer),
        Base::ValueError
    );
    expectTimelineTestState(timeline, beforeInvalidConsumer);
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineResourceRole(oldLeaf));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(oldLeaf), parent);

    timeline->beginApplying();
    auto* unproven = addTimelineTestFeature(doc(), "UnprovenResource");
    markTimelineTestResource(unproven, owner);
    timeline->endApplying();
    App::TimelineResourceReconciliationMapping invalidIdentity {
        .owner = owner,
        .orderedFinalResources = {unproven, parent},
        .stateSourceIndices = {-1, 1},
        .consumerReplacementIndices = {0, -1, -1},
    };
    const auto beforeInvalidIdentity = captureTimelineTestState(timeline);
    EXPECT_THROW(
        timeline->finalizeProvisionalOperationResourceReconciliation(invalidIdentity),
        Base::ValueError
    );
    expectTimelineTestState(timeline, beforeInvalidIdentity);
    const std::string unprovenName = unproven->getNameInDocument();
    doc()->removeObject(unprovenName.c_str());
    ASSERT_EQ(doc()->getObject("UnprovenResource"), nullptr);
    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(oldLeaf, parent, sibling, owner, consumer, replacementLeaf)
    );
    for (auto* candidate : timeline->Operations.getValues()) {
        ASSERT_NE(candidate, nullptr);
        ASSERT_TRUE(doc()->containsObject(candidate)) << candidate->getNameInDocument();
        EXPECT_TRUE(App::DocumentTimeline::replacementInputContract(candidate).valid)
            << candidate->getNameInDocument();
    }
    ASSERT_TRUE(timeline->isProvisionallyEnrolledByCurrentTransaction(replacementLeaf));

    App::TimelineResourceReconciliationMapping mapping {
        .owner = owner,
        .orderedFinalResources = {replacementLeaf, parent},
        .stateSourceIndices = {0, 1},
        .consumerReplacementIndices = {0, -1, -1},
    };
    timeline->finalizeProvisionalOperationResourceReconciliation(mapping);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(replacementLeaf, parent, owner, consumer)
    );
    EXPECT_EQ(timeline->Position.getValue(), 4);
    EXPECT_STREQ(
        static_cast<App::PropertyString*>(
            oldLeaf->getPropertyByName(App::DocumentTimeline::RolePropertyName)
        )
            ->getValue(),
        App::DocumentTimeline::InternalRole
    );
    EXPECT_STREQ(
        static_cast<App::PropertyString*>(
            sibling->getPropertyByName(App::DocumentTimeline::RolePropertyName)
        )
            ->getValue(),
        App::DocumentTimeline::InternalRole
    );
    EXPECT_EQ(
        static_cast<App::PropertyLinkHidden*>(
            oldLeaf->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
        )
            ->getValue(),
        nullptr
    );
    EXPECT_EQ(
        static_cast<App::PropertyLinkHidden*>(
            sibling->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
        )
            ->getValue(),
        nullptr
    );
    EXPECT_EQ(consumerLink->getValue(), replacementLeaf);
    EXPECT_EQ(ownerEditor->getValue(), replacementLeaf);
    EXPECT_THAT(consumerReplacements->getValues(), ::testing::ElementsAre(replacementLeaf));
    EXPECT_EQ(timelineTestMetadataPolicyStatus(ownerEditor), ownerEditorStatus);
    EXPECT_EQ(timelineTestMetadataPolicyStatus(consumerReplacements), consumerReplacementsStatus);
    EXPECT_EQ(
        timelineTestMetadataPolicyStatus(
            oldLeaf->getPropertyByName(App::DocumentTimeline::RolePropertyName)
        ),
        oldLeafRoleStatus
    );
    EXPECT_EQ(
        timelineTestMetadataPolicyStatus(
            oldLeaf->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
        ),
        oldLeafOwnerStatus
    );
    doc()->commitTransaction();

    ASSERT_TRUE(doc()->undo());
    owner = doc()->getObject("ResourceOwner");
    parent = doc()->getObject("ParentResource");
    oldLeaf = doc()->getObject("OldLeafResource");
    sibling = doc()->getObject("SiblingResource");
    consumer = doc()->getObject("RetainedConsumer");
    ASSERT_NE(owner, nullptr);
    ASSERT_NE(parent, nullptr);
    ASSERT_NE(oldLeaf, nullptr);
    ASSERT_NE(sibling, nullptr);
    ASSERT_NE(consumer, nullptr);
    consumerLink = static_cast<App::PropertyLink*>(consumer->getPropertyByName("ResourceReference"));
    ownerEditor = static_cast<App::PropertyLinkHidden*>(
        owner->getPropertyByName(App::DocumentTimeline::EditorPropertyName)
    );
    consumerReplacements = static_cast<App::PropertyLinkListHidden*>(
        consumer->getPropertyByName(App::DocumentTimeline::ReplacedInputsPropertyName)
    );
    ASSERT_NE(consumerLink, nullptr);
    ASSERT_NE(ownerEditor, nullptr);
    ASSERT_NE(consumerReplacements, nullptr);
    EXPECT_EQ(consumerLink->getValue(), oldLeaf);
    EXPECT_EQ(ownerEditor->getValue(), oldLeaf);
    EXPECT_THAT(consumerReplacements->getValues(), ::testing::ElementsAre(oldLeaf));
    EXPECT_EQ(timelineTestMetadataPolicyStatus(ownerEditor), ownerEditorStatus);
    EXPECT_EQ(timelineTestMetadataPolicyStatus(consumerReplacements), consumerReplacementsStatus);
    EXPECT_EQ(
        timelineTestMetadataPolicyStatus(
            oldLeaf->getPropertyByName(App::DocumentTimeline::RolePropertyName)
        ),
        oldLeafRoleStatus
    );
    EXPECT_EQ(
        timelineTestMetadataPolicyStatus(
            oldLeaf->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
        ),
        oldLeafOwnerStatus
    );
    EXPECT_TRUE(App::DocumentTimeline::hasTimelineResourceRole(oldLeaf));
    EXPECT_EQ(App::DocumentTimeline::timelineOwner(oldLeaf), parent);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(oldLeaf, parent, sibling, owner, consumer)
    );

    ASSERT_TRUE(doc()->redo());
    owner = doc()->getObject("ResourceOwner");
    parent = doc()->getObject("ParentResource");
    oldLeaf = doc()->getObject("OldLeafResource");
    sibling = doc()->getObject("SiblingResource");
    consumer = doc()->getObject("RetainedConsumer");
    replacementLeaf = doc()->getObject("ReplacementLeaf");
    ASSERT_NE(owner, nullptr);
    ASSERT_NE(parent, nullptr);
    ASSERT_NE(oldLeaf, nullptr);
    ASSERT_NE(sibling, nullptr);
    ASSERT_NE(consumer, nullptr);
    ASSERT_NE(replacementLeaf, nullptr);
    ownerEditor = static_cast<App::PropertyLinkHidden*>(
        owner->getPropertyByName(App::DocumentTimeline::EditorPropertyName)
    );
    consumerReplacements = static_cast<App::PropertyLinkListHidden*>(
        consumer->getPropertyByName(App::DocumentTimeline::ReplacedInputsPropertyName)
    );
    ASSERT_NE(ownerEditor, nullptr);
    ASSERT_NE(consumerReplacements, nullptr);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(replacementLeaf, parent, owner, consumer)
    );
    EXPECT_STREQ(
        static_cast<App::PropertyString*>(
            oldLeaf->getPropertyByName(App::DocumentTimeline::RolePropertyName)
        )
            ->getValue(),
        App::DocumentTimeline::InternalRole
    );
    EXPECT_EQ(ownerEditor->getValue(), replacementLeaf);
    EXPECT_THAT(consumerReplacements->getValues(), ::testing::ElementsAre(replacementLeaf));
    EXPECT_EQ(timelineTestMetadataPolicyStatus(ownerEditor), ownerEditorStatus);
    EXPECT_EQ(timelineTestMetadataPolicyStatus(consumerReplacements), consumerReplacementsStatus);
    EXPECT_EQ(
        timelineTestMetadataPolicyStatus(
            oldLeaf->getPropertyByName(App::DocumentTimeline::RolePropertyName)
        ),
        oldLeafRoleStatus
    );
    EXPECT_EQ(
        timelineTestMetadataPolicyStatus(
            oldLeaf->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
        ),
        oldLeafOwnerStatus
    );

    Base::FileInfo saved = timelineTestFile("timeline-resource-reconciliation");
    ASSERT_TRUE(doc()->saveCopy(saved.filePath().c_str()));
    auto* reopened = App::GetApplication().openDocument(saved.filePath().c_str());
    ASSERT_NE(reopened, nullptr);
    auto* reopenedTimeline = App::DocumentTimeline::get(reopened);
    auto* reopenedOldLeaf = reopened->getObject("OldLeafResource");
    auto* reopenedOwner = reopened->getObject("ResourceOwner");
    auto* reopenedConsumer = reopened->getObject("RetainedConsumer");
    auto* reopenedReplacement = reopened->getObject("ReplacementLeaf");
    ASSERT_NE(reopenedTimeline, nullptr);
    ASSERT_NE(reopenedOldLeaf, nullptr);
    ASSERT_NE(reopenedOwner, nullptr);
    ASSERT_NE(reopenedConsumer, nullptr);
    ASSERT_NE(reopenedReplacement, nullptr);
    EXPECT_THAT(
        reopenedTimeline->Operations.getValues(),
        ::testing::ElementsAre(
            reopened->getObject("ReplacementLeaf"),
            reopened->getObject("ParentResource"),
            reopened->getObject("ResourceOwner"),
            reopened->getObject("RetainedConsumer")
        )
    );
    EXPECT_STREQ(
        static_cast<App::PropertyString*>(
            reopenedOldLeaf->getPropertyByName(App::DocumentTimeline::RolePropertyName)
        )
            ->getValue(),
        App::DocumentTimeline::InternalRole
    );
    EXPECT_EQ(
        static_cast<App::PropertyLinkHidden*>(
            reopenedOldLeaf->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
        )
            ->getValue(),
        nullptr
    );
    auto* reopenedEditor = static_cast<App::PropertyLinkHidden*>(
        reopenedOwner->getPropertyByName(App::DocumentTimeline::EditorPropertyName)
    );
    auto* reopenedReplacements = static_cast<App::PropertyLinkListHidden*>(
        reopenedConsumer->getPropertyByName(App::DocumentTimeline::ReplacedInputsPropertyName)
    );
    ASSERT_NE(reopenedEditor, nullptr);
    ASSERT_NE(reopenedReplacements, nullptr);
    EXPECT_EQ(reopenedEditor->getValue(), reopenedReplacement);
    EXPECT_THAT(reopenedReplacements->getValues(), ::testing::ElementsAre(reopenedReplacement));
    EXPECT_EQ(timelineTestMetadataPolicyStatus(reopenedEditor), ownerEditorStatus);
    EXPECT_EQ(timelineTestMetadataPolicyStatus(reopenedReplacements), consumerReplacementsStatus);
    const std::string reopenedName = reopened->getName();
    EXPECT_TRUE(App::GetApplication().closeDocument(reopenedName.c_str()));
    saved.deleteFile();
}

TEST_F(DocumentTest, resourceReconciliationAcceptsAlreadyDeletedOldResources)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->openTransaction("Create replaceable old resource");
    auto* owner = addTimelineTestFeature(doc(), "DeletedOldOwner");
    auto* oldResource = addTimelineTestFeature(doc(), "DeletedOldResource");
    markTimelineTestOperation(owner);
    markTimelineTestResource(oldResource, owner);
    timeline->finalizeProvisionalOperationBlock(owner, {oldResource, owner});
    doc()->commitTransaction();

    doc()->openTransaction("Replace already deleted resource");
    timeline->stageOperationResourceReconciliation(owner, {oldResource});
    const int reconciliationTransaction = doc()->getBookedTransactionID();
    ASSERT_NE(reconciliationTransaction, App::NullTransaction);
    ASSERT_TRUE(App::GetApplication().transactionIsActive(reconciliationTransaction));
    const std::string oldResourceName = oldResource->getNameInDocument();
    doc()->removeObject(oldResourceName.c_str());
    auto* replacement = addTimelineTestFeature(doc(), "DeletedOldReplacement");
    markTimelineTestResource(replacement, owner);
    ASSERT_EQ(doc()->getBookedTransactionID(), reconciliationTransaction);
    ASSERT_TRUE(App::GetApplication().transactionIsActive(reconciliationTransaction));
    ASSERT_TRUE(timeline->isProvisionallyEnrolledByCurrentTransaction(replacement));

    App::TimelineResourceReconciliationMapping mapping {
        .owner = owner,
        .orderedFinalResources = {replacement},
        .stateSourceIndices = {0},
        .consumerReplacementIndices = {-1},
    };
    timeline->finalizeProvisionalOperationResourceReconciliation(mapping);
    EXPECT_EQ(doc()->getObject("DeletedOldResource"), nullptr);
    EXPECT_THAT(timeline->Operations.getValues(), ::testing::ElementsAre(replacement, owner));
    EXPECT_EQ(timeline->Position.getValue(), 2);
    doc()->commitTransaction();
}

TEST_F(DocumentTest, resourceReconciliationAdvancesCreationProvenanceForLaterPublication)
{
    auto* timeline = App::DocumentTimeline::ensure(doc());

    doc()->openTransaction("Create replaceable semantic resource");
    auto* owner = addTimelineTestFeature(doc(), "ProvenanceOwner");
    auto* oldResource = addTimelineTestFeature(doc(), "ProvenanceOldResource");
    markTimelineTestOperation(owner);
    markTimelineTestResource(oldResource, owner);
    timeline->finalizeProvisionalOperationBlock(owner, {oldResource, owner});
    doc()->commitTransaction();

    doc()->openTransaction("Reconcile then publish another semantic block");
    timeline->stageOperationResourceReconciliation(owner, {oldResource});
    auto* replacement = addTimelineTestFeature(doc(), "ProvenanceReplacement");
    markTimelineTestResource(replacement, owner);
    App::TimelineResourceReconciliationMapping mapping {
        .owner = owner,
        .orderedFinalResources = {replacement},
        .stateSourceIndices = {0},
        .consumerReplacementIndices = {-1},
    };
    timeline->finalizeProvisionalOperationResourceReconciliation(mapping);
    const std::string oldResourceName = oldResource->getNameInDocument();
    doc()->removeObject(oldResourceName.c_str());
    ASSERT_EQ(doc()->getObject("ProvenanceOldResource"), nullptr);

    auto* laterOperation = addTimelineTestFeature(doc(), "LaterPublishedOperation");
    auto* laterResource = addTimelineTestFeature(doc(), "LaterPublishedResource");
    markTimelineTestOperation(laterOperation);
    markTimelineTestResource(laterResource, laterOperation);
    EXPECT_NO_THROW(
        timeline->publishProvisionalOperationBlock(laterOperation, {laterResource})
    );
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(replacement, owner, laterResource, laterOperation)
    );
    EXPECT_EQ(timeline->Position.getValue(), 4);
    doc()->commitTransaction();
}

TEST_F(DocumentTest, resourceReconciliationRejectsLaterRetirementRemovedByOwnerCallback)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());
    doc()->commitTransaction();

    doc()->openTransaction("Create hostile retirement graph");
    auto* owner = addTimelineTestFeature(doc(), "HostileRetirementOwner");
    auto* first = addTimelineTestFeature(doc(), "HostileRetirementFirst");
    auto* later = addTimelineTestFeature(doc(), "HostileRetirementLater");
    markTimelineTestOperation(owner);
    markTimelineTestResource(first, owner);
    markTimelineTestResource(later, owner);
    timeline->finalizeProvisionalOperationBlock(owner, {first, later, owner});
    doc()->commitTransaction();
    const auto baseline = captureTimelineTestState(timeline);

    doc()->openTransaction("Reject hostile retirement callback");
    timeline->stageOperationResourceReconciliation(owner, {first, later});
    bool callbackInvoked = false;
    auto connection = doc()->signalChangedObject.connect(
        [this, first, &callbackInvoked](
            const App::DocumentObject& changed,
            const App::Property& property
        ) {
            if (!callbackInvoked && &changed == first
                && std::string_view(property.getName())
                    == App::DocumentTimeline::OwnerPropertyName) {
                callbackInvoked = true;
                doc()->removeObject("HostileRetirementLater");
            }
        }
    );
    App::TimelineResourceReconciliationMapping mapping {
        .owner = owner,
        .orderedFinalResources = {},
        .stateSourceIndices = {},
        .consumerReplacementIndices = {-1, -1},
    };
    EXPECT_THROW(
        timeline->finalizeProvisionalOperationResourceReconciliation(mapping),
        Base::RuntimeError
    );
    connection.disconnect();

    EXPECT_TRUE(callbackInvoked);
    doc()->abortTransaction();
    EXPECT_NE(doc()->getObject("HostileRetirementFirst"), nullptr);
    EXPECT_NE(doc()->getObject("HostileRetirementLater"), nullptr);
    expectTimelineTestState(timeline, baseline);
}

TEST_F(DocumentTest, dependencyRebaseMovesCompleteDownstreamClosureAndPersists)
{
    doc()->setUndoMode(1);
    auto* timeline = App::DocumentTimeline::ensure(doc());

    doc()->openTransaction("Create dependency rebase graph");
    auto* operation = addTimelineTestFeature(doc(), "RebasedOperation");
    auto* resource = addTimelineTestFeature(doc(), "RebasedResource");
    markTimelineTestOperation(operation);
    markTimelineTestResource(resource, operation);
    timeline->finalizeProvisionalOperationBlock(operation, {resource, operation});

    auto* unrelated = addTimelineTestFeature(doc(), "UnrelatedOperation");
    markTimelineTestOperation(unrelated);
    timeline->finalizeProvisionalOperationBlock(unrelated, {unrelated});

    auto* source = addTimelineTestFeature(doc(), "LaterSourceOperation");
    markTimelineTestOperation(source);
    timeline->finalizeProvisionalOperationBlock(source, {source});

    auto* consumer = addTimelineTestFeature(doc(), "DownstreamConsumer");
    markTimelineTestOperation(consumer);
    auto* consumerInput = static_cast<App::PropertyLink*>(
        consumer->addDynamicProperty("App::PropertyLink", "Input")
    );
    consumerInput->setValue(resource);
    timeline->finalizeProvisionalOperationBlock(consumer, {consumer});
    doc()->commitTransaction();

    ASSERT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(resource, operation, unrelated, source, consumer)
    );

    doc()->openTransaction("Add later operation dependency");
    auto* laterDependency = static_cast<App::PropertyLink*>(
        operation->addDynamicProperty("App::PropertyLink", "LaterDependency")
    );
    laterDependency->setValue(source);
    EXPECT_TRUE(
        timeline->reorderOperationDependentClosureAfter(operation, source)
    );
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(unrelated, source, resource, operation, consumer)
    );
    doc()->commitTransaction();

    ASSERT_TRUE(doc()->undo());
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(resource, operation, unrelated, source, consumer)
    );
    EXPECT_EQ(operation->getPropertyByName("LaterDependency"), nullptr);

    ASSERT_TRUE(doc()->redo());
    operation = doc()->getObject("RebasedOperation");
    resource = doc()->getObject("RebasedResource");
    unrelated = doc()->getObject("UnrelatedOperation");
    source = doc()->getObject("LaterSourceOperation");
    consumer = doc()->getObject("DownstreamConsumer");
    ASSERT_NE(operation, nullptr);
    ASSERT_NE(resource, nullptr);
    ASSERT_NE(unrelated, nullptr);
    ASSERT_NE(source, nullptr);
    ASSERT_NE(consumer, nullptr);
    laterDependency = static_cast<App::PropertyLink*>(
        operation->getPropertyByName("LaterDependency")
    );
    ASSERT_NE(laterDependency, nullptr);
    EXPECT_EQ(laterDependency->getValue(), source);
    EXPECT_THAT(
        timeline->Operations.getValues(),
        ::testing::ElementsAre(unrelated, source, resource, operation, consumer)
    );

    Base::FileInfo saved = timelineTestFile("timeline-dependency-rebase");
    ASSERT_TRUE(doc()->saveCopy(saved.filePath().c_str()));
    auto* reopened = App::GetApplication().openDocument(saved.filePath().c_str());
    ASSERT_NE(reopened, nullptr);
    auto* reopenedTimeline = App::DocumentTimeline::get(reopened);
    ASSERT_NE(reopenedTimeline, nullptr);
    EXPECT_THAT(
        reopenedTimeline->Operations.getValues(),
        ::testing::ElementsAre(
            reopened->getObject("UnrelatedOperation"),
            reopened->getObject("LaterSourceOperation"),
            reopened->getObject("RebasedResource"),
            reopened->getObject("RebasedOperation"),
            reopened->getObject("DownstreamConsumer")
        )
    );
    const std::string reopenedName = reopened->getName();
    EXPECT_TRUE(
        App::GetApplication().closeDocument(reopenedName.c_str())
    );
    saved.deleteFile();
}

TEST_F(DocumentTest, exactPreCloseObserverCanJoinDependentDocument)
{
    doc()->setUndoMode(1);
    const std::string dependentName =
        App::GetApplication().getUniqueDocumentName(
            "transactionDependent"
        );
    auto* dependent = App::GetApplication().newDocument(
        dependentName.c_str(),
        "testUser"
    );
    ASSERT_NE(dependent, nullptr);
    dependent->setUndoMode(1);

    int observedTransaction = App::NullTransaction;
    auto connection =
        App::GetApplication()
            .signalBeforeExactTransactionClose.connect(
                [&](int transactionId,
                    bool aborted,
                    const std::vector<App::Document*>& participants) {
                    if (aborted
                        || std::ranges::find(participants, doc())
                            == participants.end()) {
                        return;
                    }
                    observedTransaction = transactionId;
                    EXPECT_TRUE(
                        App::GetApplication().transactionIsActive(
                            transactionId
                        )
                    );
                    EXPECT_EQ(
                        dependent->getBookedTransactionID(),
                        App::NullTransaction
                    );
                    dependent->openTransaction(
                        "Join exact source transaction",
                        transactionId
                    );
                    dependent->addObject(
                        "App::FeaturePython",
                        "DependentResult",
                        true,
                        "Gui::ViewProviderDocumentObject"
                    );
                }
            );

    const int transactionId =
        doc()->openTransaction("Create exact source result");
    ASSERT_NE(transactionId, App::NullTransaction);
    doc()->addObject(
        "App::FeaturePython",
        "SourceResult",
        true,
        "Gui::ViewProviderDocumentObject"
    );
    doc()->commitTransaction();
    connection.disconnect();

    EXPECT_EQ(observedTransaction, transactionId);
    EXPECT_NE(doc()->getObject("SourceResult"), nullptr);
    EXPECT_NE(dependent->getObject("DependentResult"), nullptr);
    EXPECT_EQ(
        doc()->getBookedTransactionID(),
        App::NullTransaction
    );
    EXPECT_EQ(
        dependent->getBookedTransactionID(),
        App::NullTransaction
    );
    EXPECT_EQ(doc()->getTransactionID(true), transactionId);
    EXPECT_EQ(
        dependent->getTransactionID(true),
        transactionId
    );

    EXPECT_TRUE(
        App::GetApplication().closeDocument(
            dependentName.c_str()
        )
    );
}

// NOLINTEND(readability-magic-numbers)
