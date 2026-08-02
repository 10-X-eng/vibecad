// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <filesystem>
#include <string>
#include <utility>
#include <vector>

#include "src/App/InitApplication.h"

#include <App/Application.h>
#include <App/Datums.h>
#include <App/Document.h>
#include <App/PropertyLinks.h>
#include <Mod/Part/App/FeatureMirroring.h>
#include <Mod/Part/App/FeaturePartBox.h>
#include <Mod/Part/App/FeaturePartFuse.h>
#include <Mod/PartDesign/App/Body.h>

namespace
{
void expectGlobalLinks(
    App::DocumentObject* object,
    const std::vector<const char*>& propertyNames
)
{
    ASSERT_NE(object, nullptr);
    for (const char* propertyName : propertyNames) {
        SCOPED_TRACE(
            std::string(object->getTypeId().getName()) + "." + propertyName
        );
        auto* property = dynamic_cast<App::PropertyLinkBase*>(
            object->getPropertyByName(propertyName)
        );
        ASSERT_NE(property, nullptr);
        EXPECT_EQ(property->getScope(), App::LinkScope::Global);
    }
}

void setLinkValue(App::DocumentObject* object, const char* propertyName, App::DocumentObject* value)
{
    ASSERT_NE(object, nullptr);
    auto* property = dynamic_cast<App::PropertyLinkBase*>(object->getPropertyByName(propertyName));
    ASSERT_NE(property, nullptr);

    if (auto* link = dynamic_cast<App::PropertyLink*>(property)) {
        link->setValue(value);
    }
    else if (auto* links = dynamic_cast<App::PropertyLinkList*>(property)) {
        links->setValues({value});
    }
    else if (auto* link = dynamic_cast<App::PropertyLinkSub*>(property)) {
        link->setValue(value);
    }
    else if (auto* links = dynamic_cast<App::PropertyLinkSubList*>(property)) {
        links->setValue(value, "");
    }
    else {
        FAIL() << object->getTypeId().getName() << "." << propertyName
               << " is an unsupported link-property type";
    }
}
}  // namespace

class MixedFeatureBodyTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }

    void SetUp() override
    {
        _documentName = App::GetApplication().getUniqueDocumentName("MixedFeatureBody");
        _document = App::GetApplication().newDocument(_documentName.c_str(), "testUser");
        _body = _document->addObject<PartDesign::Body>("Body");
    }

    void TearDown() override
    {
        if (App::GetApplication().getDocument(_documentName.c_str())) {
            App::GetApplication().closeDocument(_documentName.c_str());
        }
    }

    std::string _documentName;
    App::Document* _document = nullptr;
    PartDesign::Body* _body = nullptr;
};

TEST_F(MixedFeatureBodyTest, ordinaryPartFeatureCanBeOwnedByBody)
{
    auto* box = _document->addObject<Part::Box>("Box");

    EXPECT_TRUE(PartDesign::Body::isAllowed(box));
    EXPECT_FALSE(PartDesign::Body::isSolidFeature(box));
    EXPECT_TRUE(PartDesign::Body::isResultFeature(box));

    _body->addObject(box);
    _document->recompute();

    EXPECT_TRUE(_body->hasObject(box));
    EXPECT_EQ(_body->Tip.getValue(), box);
    EXPECT_FALSE(_body->Shape.getValue().IsNull());
    EXPECT_TRUE(_body->isSolid());
    EXPECT_TRUE(_body->keepDirectChildrenInTree());
}

TEST_F(MixedFeatureBodyTest, nonSolidPartResultDoesNotMakeBodySolid)
{
    auto* feature = _document->addObject<Part::Feature>("EmptyResult");

    EXPECT_FALSE(PartDesign::Body::isSolidFeature(feature));
    EXPECT_TRUE(PartDesign::Body::isResultFeature(feature));
    _body->addObject(feature);

    EXPECT_EQ(_body->Tip.getValue(), feature);
    EXPECT_FALSE(_body->isSolid());
}

TEST_F(MixedFeatureBodyTest, supportAndInternalTransformFeaturesDoNotBecomeResults)
{
    auto* binder = _document->addObject("PartDesign::SubShapeBinder", "Binder");
    auto* scaled = _document->addObject("PartDesign::Scaled", "Scaled");

    EXPECT_TRUE(PartDesign::Body::isAllowed(binder));
    EXPECT_FALSE(PartDesign::Body::isResultFeature(binder));
    EXPECT_TRUE(PartDesign::Body::isAllowed(scaled));
    EXPECT_FALSE(PartDesign::Body::isResultFeature(scaled));

    _body->addObject(binder);
    _body->addObject(scaled);

    EXPECT_EQ(_body->Tip.getValue(), nullptr);
}

TEST_F(MixedFeatureBodyTest, explicitPartDependencyGraphRemainsBodyOwned)
{
    auto* left = _document->addObject<Part::Box>("Left");
    auto* right = _document->addObject<Part::Box>("Right");
    right->Placement.setValue(
        Base::Placement(Base::Vector3d(0.5, 0.0, 0.0), Base::Rotation())
    );
    auto* fuse = _document->addObject<Part::Fuse>("Fuse");
    fuse->Base.setValue(left);
    fuse->Tool.setValue(right);

    _body->addObject(left);
    _body->addObject(right);
    _body->addObject(fuse);
    _document->recompute();

    EXPECT_TRUE(_body->hasObject(left));
    EXPECT_TRUE(_body->hasObject(right));
    EXPECT_TRUE(_body->hasObject(fuse));
    EXPECT_EQ(_body->Tip.getValue(), fuse);
    EXPECT_EQ(fuse->Base.getValue(), left);
    EXPECT_EQ(fuse->Tool.getValue(), right);
    EXPECT_EQ(fuse->Base.getScope(), App::LinkScope::Local);
    EXPECT_EQ(fuse->Tool.getScope(), App::LinkScope::Local);
    EXPECT_FALSE(_body->Shape.getValue().IsNull());
    EXPECT_FALSE(left->Visibility.getValue());
    EXPECT_FALSE(right->Visibility.getValue());
    EXPECT_TRUE(fuse->Visibility.getValue());
}

TEST_F(MixedFeatureBodyTest, mixedGraphSurvivesUndoAndRedo)
{
    _document->setUndoMode(1);
    _document->openTransaction("Create mixed Body graph");

    auto* left = _document->addObject<Part::Box>("Left");
    auto* right = _document->addObject<Part::Box>("Right");
    auto* fuse = _document->addObject<Part::Fuse>("Fuse");
    fuse->Base.setValue(left);
    fuse->Tool.setValue(right);
    _body->addObject(left);
    _body->addObject(right);
    _body->addObject(fuse);

    _document->commitTransaction();
    _document->recompute();

    ASSERT_TRUE(_document->undo());
    EXPECT_EQ(_document->getObject("Left"), nullptr);
    EXPECT_EQ(_document->getObject("Right"), nullptr);
    EXPECT_EQ(_document->getObject("Fuse"), nullptr);
    EXPECT_EQ(_body->Tip.getValue(), nullptr);

    ASSERT_TRUE(_document->redo());
    _document->recompute();

    auto* restoredFuse = dynamic_cast<Part::Fuse*>(_document->getObject("Fuse"));
    ASSERT_NE(restoredFuse, nullptr);
    EXPECT_TRUE(_body->hasObject(restoredFuse));
    EXPECT_EQ(_body->Tip.getValue(), restoredFuse);
    EXPECT_EQ(restoredFuse->Base.getValue(), _document->getObject("Left"));
    EXPECT_EQ(restoredFuse->Tool.getValue(), _document->getObject("Right"));
    EXPECT_FALSE(_body->Shape.getValue().IsNull());
}

TEST_F(MixedFeatureBodyTest, mixedGraphSurvivesSaveAndReopen)
{
    auto* left = _document->addObject<Part::Box>("Left");
    auto* right = _document->addObject<Part::Box>("Right");
    auto* fuse = _document->addObject<Part::Fuse>("Fuse");
    fuse->Base.setValue(left);
    fuse->Tool.setValue(right);
    _body->addObject(left);
    _body->addObject(right);
    _body->addObject(fuse);
    _document->recompute();

    const auto file = std::filesystem::temp_directory_path() / (_documentName + ".FCStd");
    ASSERT_TRUE(_document->saveAs(file.string().c_str()));
    App::GetApplication().closeDocument(_documentName.c_str());

    _document = App::GetApplication().openDocument(file.string().c_str());
    ASSERT_NE(_document, nullptr);
    _documentName = _document->getName();
    _body = dynamic_cast<PartDesign::Body*>(_document->getObject("Body"));
    auto* restoredFuse = dynamic_cast<Part::Fuse*>(_document->getObject("Fuse"));

    ASSERT_NE(_body, nullptr);
    ASSERT_NE(restoredFuse, nullptr);
    EXPECT_TRUE(_body->hasObject(_document->getObject("Left")));
    EXPECT_TRUE(_body->hasObject(_document->getObject("Right")));
    EXPECT_TRUE(_body->hasObject(restoredFuse));
    EXPECT_EQ(_body->Tip.getValue(), restoredFuse);
    EXPECT_EQ(restoredFuse->Base.getValue(), _document->getObject("Left"));
    EXPECT_EQ(restoredFuse->Tool.getValue(), _document->getObject("Right"));
    EXPECT_FALSE(_body->Shape.getValue().IsNull());

    std::filesystem::remove(file);
}

TEST_F(MixedFeatureBodyTest, retainedGeneralPartFeaturesAcceptCrossContainerLinks)
{
    auto* crossContainerSource = _document->addObject<Part::Box>("CrossContainerSource");
    _body->addObject(crossContainerSource);

    const std::vector<std::pair<const char*, std::vector<const char*>>> features = {
        {"Part::Cut", {"Base", "Tool"}},
        {"Part::MultiFuse", {"Shapes"}},
        {"Part::MultiCommon", {"Shapes"}},
        {"Part::Compound", {"Links"}},
        {"Part::Extrusion", {"Base", "DirLink"}},
        {"Part::Revolution", {"Source", "AxisLink"}},
        {"Part::Mirroring", {"Source", "MirrorPlane"}},
        {"Part::Scale", {"Base"}},
        {"Part::RuledSurface", {"Curve1", "Curve2"}},
        {"Part::Loft", {"Sections"}},
        {"Part::Sweep", {"Sections", "Spine"}},
        {"Part::Thickness", {"Faces"}},
        {"Part::Refine", {"Source"}},
        {"Part::Reverse", {"Source"}},
        {"Part::Face", {"Sources"}},
        {"Part::ProjectOnSurface", {"SupportFace", "Projection"}},
        {"Part::Fillet", {"Base", "EdgeLinks"}},
        {"Part::Chamfer", {"Base", "EdgeLinks"}},
        {"Part::Offset", {"Source"}},
        {"Part::Offset2D", {"Source"}},
        {"Part::Section", {"Base", "Tool"}},
    };

    int index = 0;
    for (const auto& [typeName, propertyNames] : features) {
        auto* feature = _document->addObject(
            typeName,
            (std::string("GeneralPartFeature") + std::to_string(index++)).c_str()
        );
        for (const char* propertyName : propertyNames) {
            setLinkValue(feature, propertyName, crossContainerSource);
        }
        expectGlobalLinks(feature, propertyNames);
    }
}

TEST_F(MixedFeatureBodyTest, datumReferencePromotesBeforeGrouping)
{
    auto* plane = _document->addObject<App::Plane>("ReferencePlane");
    auto* mirror = _document->addObject<Part::Mirroring>("DatumMirror");

    mirror->MirrorPlane.setValue(plane);

    EXPECT_EQ(mirror->MirrorPlane.getScope(), App::LinkScope::Global);
}

TEST_F(MixedFeatureBodyTest, crossBodyBooleanPreservesOperandOwnership)
{
    auto* otherBody = _document->addObject<PartDesign::Body>("OtherBody");
    auto* left = _document->addObject<Part::Box>("Left");
    auto* right = _document->addObject<Part::Box>("Right");
    right->Placement.setValue(
        Base::Placement(Base::Vector3d(0.5, 0.0, 0.0), Base::Rotation())
    );
    _body->addObject(left);
    otherBody->addObject(right);

    auto* fuse = _document->addObject<Part::Fuse>("CrossBodyFuse");
    fuse->Base.setValue(left);
    fuse->Tool.setValue(right);
    _document->recompute();

    EXPECT_TRUE(_body->hasObject(left));
    EXPECT_TRUE(otherBody->hasObject(right));
    EXPECT_FALSE(_body->hasObject(fuse));
    EXPECT_FALSE(otherBody->hasObject(fuse));
    EXPECT_EQ(fuse->Base.getScope(), App::LinkScope::Global);
    EXPECT_EQ(fuse->Tool.getScope(), App::LinkScope::Global);
    EXPECT_FALSE(fuse->isError());
    EXPECT_FALSE(fuse->Shape.getValue().IsNull());

    const auto file = std::filesystem::temp_directory_path() / (_documentName + "-cross.FCStd");
    ASSERT_TRUE(_document->saveAs(file.string().c_str()));
    App::GetApplication().closeDocument(_documentName.c_str());

    _document = App::GetApplication().openDocument(file.string().c_str());
    ASSERT_NE(_document, nullptr);
    _documentName = _document->getName();
    _body = dynamic_cast<PartDesign::Body*>(_document->getObject("Body"));
    auto* restoredOtherBody
        = dynamic_cast<PartDesign::Body*>(_document->getObject("OtherBody"));
    auto* restoredFuse = dynamic_cast<Part::Fuse*>(_document->getObject("CrossBodyFuse"));

    ASSERT_NE(_body, nullptr);
    ASSERT_NE(restoredOtherBody, nullptr);
    ASSERT_NE(restoredFuse, nullptr);
    EXPECT_TRUE(_body->hasObject(_document->getObject("Left")));
    EXPECT_TRUE(restoredOtherBody->hasObject(_document->getObject("Right")));
    EXPECT_FALSE(_body->hasObject(restoredFuse));
    EXPECT_FALSE(restoredOtherBody->hasObject(restoredFuse));
    EXPECT_EQ(restoredFuse->Base.getScope(), App::LinkScope::Global);
    EXPECT_EQ(restoredFuse->Tool.getScope(), App::LinkScope::Global);
    EXPECT_FALSE(restoredFuse->isError());
    EXPECT_FALSE(restoredFuse->Shape.getValue().IsNull());

    std::filesystem::remove(file);
}
