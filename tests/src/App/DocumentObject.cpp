// SPDX-License-Identifier: LGPL-2.1-or-later

#include "gtest/gtest.h"
#include <gmock/gmock.h>
#include <chrono>

#include <src/App/InitApplication.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentObject.h>
#include <App/DocumentObjectGroup.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/PropertyLinks.h>
#include <Base/Interpreter.h>

using namespace App;

// NOLINTBEGIN(readability-magic-numbers,cppcoreguidelines-avoid-magic-numbers)

namespace App
{
class CleanupCountingLink: public PropertyLink
{
public:
    size_t cleanupCalls {0};
    void breakLink(DocumentObject* object, bool clear) override
    {
        ++cleanupCalls;
        PropertyLink::breakLink(object, clear);
    }
};

class LinkCleanupTestObject: public DocumentObject
{
    PROPERTY_HEADER_WITH_OVERRIDE(App::LinkCleanupTestObject);

public:
    LinkCleanupTestObject()
    {
        ADD_PROPERTY(Link, (nullptr));
        ADD_PROPERTY(HiddenLink, (nullptr));
        HiddenLink.setScope(LinkScope::Hidden);
    }
    CleanupCountingLink Link;
    CleanupCountingLink HiddenLink;
};
PROPERTY_SOURCE(App::LinkCleanupTestObject, App::DocumentObject)
}

class DocumentObjectTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
        App::LinkCleanupTestObject::init();
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

    // NOLINTBEGIN(cppcoreguidelines-non-private-member-variables-in-classes)
    std::string _docName {};
    App::Document* _doc {};
    // NOLINTEND(cppcoreguidelines-non-private-member-variables-in-classes)
};


TEST_F(DocumentObjectTest, getSubObjectList)
{
    // Arrange

    // The name of a Part::Box added to the document
    auto boxName {_doc->addObject("Part::Box")->getNameInDocument()};
    // The name of a Part::Cylinder added to the document
    auto cylName {_doc->addObject("Part::Cylinder")->getNameInDocument()};
    // An App::Part object added to the document
    auto part {_doc->addObject("App::Part")};
    // The name of the App::Part added to the document
    auto partName {part->getNameInDocument()};
    // The name of the object used as argument for the calls of DocumentObject::getSubObjectList()
    auto subName {std::string()};

    // A vector of int used as argument for the call of DocumentObject::getSubObjectList() with
    // argument flatten not set (or set to false)
    auto sizesNoFlatten {std::vector<int>()};
    // A vector of int used as argument for the call of DocumentObject::getSubObjectList() with
    // argument flatten set to true
    auto sizesFlatten {std::vector<int>()};
    // A helper string used to compose the argument of some calls of
    // Base::Interpreter().runString()
    auto cmd {std::string()};

    // Performing a fusion to create an object that will be searched with the calls of
    // DocumentObject::getSubObjectList()
    Base::Interpreter().runString("from BOPTools import BOPFeatures");
    Base::Interpreter().runString("bp = BOPFeatures.BOPFeatures(App.activeDocument())");
    cmd = "bp.make_multi_fuse([\"";
    cmd += boxName;
    cmd += "\", \"";
    cmd += cylName;
    cmd += "\", ])";
    Base::Interpreter().runString(cmd.c_str());
    Base::Interpreter().runString("App.ActiveDocument.recompute()");
    // The name of the fusion object
    auto fuseName {_doc->getObject("Fusion")->getNameInDocument()};

    // Defining the name of the object that will be searched with the calls of
    // DocumentObject::getSubObjectList()
    subName = fuseName;
    subName += ".";
    subName += boxName;
    subName += ".Edge1";

    // Adding the fusion to the App::Part object to test the differences between calling
    // DocumentObject::getSubObjectList() with the flatten argument set to false or set to true
    cmd = "App.ActiveDocument.getObject(\"";
    cmd += partName;
    cmd += "\").addObject(App.ActiveDocument.getObject(\"";
    cmd += fuseName;
    cmd += "\"))";
    Base::Interpreter().runString(cmd.c_str());
    Base::Interpreter().runString("App.ActiveDocument.recompute()");

    // A vector of DocumentObjects used to store the result of the call to
    // DocumentObject::getSubObjectList() without the subname parameter
    auto docSubObjsNoSubName {std::vector<DocumentObject*>()};
    // A vector of DocumentObjects used to store the result of the call to
    // DocumentObject::getSubObjectList() with only the subname parameter
    auto docSubObjsWithSubName {std::vector<DocumentObject*>()};
    // A vector of DocumentObjects used to store the result of the call to
    // DocumentObject::getSubObjectList() with the parameters subname and subsizes
    auto docSubObjsWithSubSizes {std::vector<DocumentObject*>()};
    // A vector of DocumentObjects used to store the result of the call to
    // DocumentObject::getSubObjectList() with the flatten parameter set to true
    auto docSubObjsFlatten {std::vector<DocumentObject*>()};

    // Act
    docSubObjsNoSubName = part->getSubObjectList(nullptr);
    docSubObjsWithSubName = part->getSubObjectList(subName.c_str());
    docSubObjsWithSubSizes = part->getSubObjectList(subName.c_str(), &sizesNoFlatten);
    docSubObjsFlatten = part->getSubObjectList(subName.c_str(), &sizesFlatten, true);

    // Assert

    // If DocumentObject::getSubObjectList() is called without giving the subname argument it
    // returns a vector with only one entry, corresponding to the object that called the method
    EXPECT_EQ(docSubObjsNoSubName.size(), 1);
    EXPECT_EQ(docSubObjsNoSubName[0]->getID(), _doc->getObject(partName)->getID());

    // If DocumentObject::getSubObjectList() is called with the subname argument it returns a vector
    // with one entry for each sub object in the subname plus one entry with the object that called
    // the method
    EXPECT_EQ(docSubObjsWithSubName.size(), 3);
    EXPECT_EQ(docSubObjsWithSubName[0]->getID(), _doc->getObject(partName)->getID());
    EXPECT_EQ(docSubObjsWithSubName[1]->getID(), _doc->getObject(fuseName)->getID());
    EXPECT_EQ(docSubObjsWithSubName[2]->getID(), _doc->getObject(boxName)->getID());

    // If DocumentObject::getSubObjectList() is called with the subsizes argument it returns the
    // same vector of the previous case and the subsizes argument stores the positions in the
    // subname string corresponding to start of the sub objects names.
    // The position takes into account also the '.' in the subname
    EXPECT_EQ(docSubObjsWithSubSizes.size(), 3);
    EXPECT_EQ(docSubObjsWithSubSizes[0]->getID(), _doc->getObject(partName)->getID());
    EXPECT_EQ(docSubObjsWithSubSizes[1]->getID(), _doc->getObject(fuseName)->getID());
    EXPECT_EQ(docSubObjsWithSubSizes[2]->getID(), _doc->getObject(boxName)->getID());
    EXPECT_EQ(sizesNoFlatten.size(), 3);
    EXPECT_EQ(sizesNoFlatten[0], 0);
    EXPECT_EQ(sizesNoFlatten[1], strlen(fuseName) + 1);
    EXPECT_EQ(sizesNoFlatten[2], strlen(fuseName) + strlen(boxName) + 2);

    // If DocumentObject::getSubObjectList() is called with the flattened argument set to true, it
    // returns a vector with all the sub objects in the subname that don't belong to the same
    // App::GeoFeatureGroupExtension object, plus one entry with the object that called the method
    EXPECT_EQ(docSubObjsFlatten.size(), 2);
    EXPECT_EQ(docSubObjsFlatten[0]->getID(), _doc->getObject(partName)->getID());
    EXPECT_EQ(docSubObjsFlatten[1]->getID(), _doc->getObject(boxName)->getID());
    EXPECT_EQ(sizesFlatten.size(), 2);
    EXPECT_EQ(sizesFlatten[0], 0);
    EXPECT_EQ(sizesFlatten[1], strlen(fuseName) + strlen(boxName) + 2);
}

TEST_F(DocumentObjectTest, timelineStructuralChildrenDistinguishContainmentFromDependencies)
{
    auto* group = dynamic_cast<App::DocumentObjectGroup*>(
        _doc->addObject("App::DocumentObjectGroup", "TimelineContainer")
    );
    auto* child = _doc->addObject("App::FeaturePython", "TimelineChild");
    auto* dependency = _doc->addObject("App::FeaturePython", "TimelineDependency");

    ASSERT_NE(group, nullptr);
    ASSERT_NE(child, nullptr);
    ASSERT_NE(dependency, nullptr);
    EXPECT_FALSE(group->isTimelineStructuralChild(child));
    EXPECT_FALSE(group->isTimelineStructuralChild(dependency));

    group->addObject(child);

    EXPECT_TRUE(group->isTimelineStructuralChild(child));
    EXPECT_FALSE(group->isTimelineStructuralChild(dependency));
    EXPECT_FALSE(child->isTimelineStructuralChild(group));
}

TEST_F(DocumentObjectTest, linkCleanupSkipsObjectsWithoutTheDeletedTarget)
{
    auto* anchor = _doc->addObject<App::DocumentObject>("Anchor");
    std::vector<App::LinkCleanupTestObject*> owners;
    std::vector<std::string> targets;
    for (size_t index = 0; index < 50; ++index) {
        auto* target = _doc->addObject<App::DocumentObject>("Target");
        targets.emplace_back(target->getNameInDocument());
        auto* owner = _doc->addObject<App::LinkCleanupTestObject>("Owner");
        owner->Link.setValue(anchor);
        owner->HiddenLink.setValue(target);
        owners.push_back(owner);
    }
    for (const auto& target : targets) {
        _doc->removeObject(target.c_str());
    }
    size_t calls = 0;
    for (const auto* owner : owners) {
        EXPECT_EQ(owner->Link.getValue(), anchor);
        EXPECT_EQ(owner->HiddenLink.getValue(), nullptr);
        calls += owner->Link.cleanupCalls + owner->HiddenLink.cleanupCalls;
    }
    // Each deletion affects one owner, not all 50 owners' properties. This is
    // an operation-count assertion, independent of host speed or a time budget.
    EXPECT_LE(calls, owners.size() * 2);
}

TEST_F(DocumentObjectTest, linkCleanupTracksHiddenEditsUndoAndDynamicProperties)
{
    _doc->setUndoMode(1);
    auto* first = _doc->addObject<App::DocumentObject>("First");
    auto* second = _doc->addObject<App::DocumentObject>("Second");
    auto* unrelated = _doc->addObject<App::DocumentObject>("Unrelated");
    auto* owner = _doc->addObject<App::LinkCleanupTestObject>("Owner");
    owner->HiddenLink.setValue(first);
    // Populate cleanup queries without modifying the links.
    PropertyLinkBase::breakLinks(unrelated, _doc->getObjects(), false);
    _doc->openTransaction("Change hidden input");
    owner->HiddenLink.setValue(second);
    PropertyLinkBase::breakLinks(unrelated, _doc->getObjects(), false);
    _doc->abortTransaction();
    ASSERT_EQ(owner->HiddenLink.getValue(), first);
    _doc->removeObject(first->getNameInDocument());
    EXPECT_EQ(owner->HiddenLink.getValue(), nullptr);

    auto* dynamic = static_cast<PropertyLink*>(owner->addDynamicProperty(
        "App::PropertyLinkHidden", "LateLink"));
    dynamic->setValue(second);
    _doc->removeObject(second->getNameInDocument());
    EXPECT_EQ(dynamic->getValue(), nullptr);
    dynamic->setValue(unrelated);
    auto* probe = _doc->addObject<App::DocumentObject>("Probe");
    PropertyLinkBase::breakLinks(probe, _doc->getObjects(), false);
    ASSERT_TRUE(owner->removeDynamicProperty("LateLink"));
    owner->Link.cleanupCalls = owner->HiddenLink.cleanupCalls = 0;
    _doc->removeObject(unrelated->getNameInDocument());
    EXPECT_EQ(owner->Link.cleanupCalls + owner->HiddenLink.cleanupCalls, 0);

    auto* last = _doc->addObject<App::DocumentObject>("Last");
    owner->Link.setValue(last);
    owner->HiddenLink.setValue(last);
    // Removing an owner must clear its outgoing references even without a
    // self-link. Exercise the same clear=true contract as document deletion.
    PropertyLinkBase::breakLinks(owner, _doc->getObjects(), true);
    EXPECT_EQ(owner->Link.getValue(), nullptr);
    EXPECT_EQ(owner->HiddenLink.getValue(), nullptr);
}

TEST_F(DocumentObjectTest, membershipLookupDoesNotScanCreationOrder)
{
    auto* first = _doc->addObject<App::DocumentObject>("First");
    App::DocumentObject* last = first;
    for (size_t index = 1; index < 5000; ++index) {
        last = _doc->addObject<App::DocumentObject>("Member");
    }
    const auto measure = [&](const App::DocumentObject* object) {
        std::array<std::chrono::nanoseconds::rep, 3> samples;
        for (auto& sample : samples) {
            const auto start = std::chrono::steady_clock::now();
            size_t found = 0;
            for (size_t index = 0; index < 20000; ++index) {
                found += _doc->containsObject(object);
            }
            sample = std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - start).count();
            EXPECT_EQ(found, 20000);
        }
        std::ranges::sort(samples);
        return samples[1];
    };
    const auto firstCost = measure(first);
    const auto lastCost = measure(last);
    RecordProperty("first_lookup_ns", std::to_string(firstCost));
    RecordProperty("last_lookup_ns", std::to_string(lastCost));
    // Relative scaling, not an execution deadline: the last object must not
    // require traversing all 5,000 predecessors for every identity check.
    EXPECT_LE(lastCost, firstCost * 16);
    EXPECT_FALSE(_doc->containsObject(nullptr));
    EXPECT_FALSE(_doc->containsObject(reinterpret_cast<App::DocumentObject*>(1)));

    _doc->setUndoMode(1);
    _doc->openTransaction("Remove indexed member");
    _doc->removeObject("First");
    _doc->commitTransaction();
    EXPECT_FALSE(_doc->containsObject(first));
    _doc->undo();
    EXPECT_TRUE(_doc->containsObject(_doc->getObject("First")));
    _doc->redo();
    EXPECT_FALSE(_doc->containsObject(first));
    _doc->clearDocument();
    EXPECT_FALSE(_doc->containsObject(last));
}

// NOLINTEND(readability-magic-numbers, cppcoreguidelines-avoid-magic-numbers)
