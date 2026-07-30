// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2022 Uwe Stöhr <uwestoehr@lyx.org>                      *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <memory>
#include <string_view>
#include <unordered_set>

#include <Inventor/actions/SoGetBoundingBoxAction.h>
#include <Inventor/nodes/SoCamera.h>
#include <Inventor/nodes/SoOrthographicCamera.h>
#include <QCoreApplication>
#include <QDialog>
#include <QDockWidget>
#include <QDoubleSpinBox>
#include <QSignalBlocker>
#include <QSlider>
#include <QToolTip>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/Link.h>
#include <App/Part.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/UnitsApi.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/DockWindowManager.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/MainWindow.h>
#include <Gui/PrefWidgets.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Gui/ViewProviderDocumentObject.h>
#include <Gui/ViewProviderGeometryObject.h>
#include <Mod/Part/App/BodyBase.h>
#include <Mod/Part/App/DatumFeature.h>
#include <Mod/Part/App/FeatureCompound.h>
#include <Mod/Part/App/FeaturePartBox.h>
#include <Mod/Part/App/FeaturePartCommon.h>
#include <Mod/Part/App/FeaturePartCut.h>
#include <Mod/Part/App/FeaturePartFuse.h>
#include <Mod/Part/App/Part2DObject.h>
#include <Mod/Part/App/PartFeatures.h>

#include "ModelingSelection.h"
#include "SectionCutting.h"
#include "TaskResultValidation.h"
#include "ui_SectionCutting.h"


using namespace PartGui;

namespace
{
struct Refresh
{
    static const bool notXValue = false;
    static const bool notYValue = false;
    static const bool notZValue = false;
    static const bool notXRange = false;
    static const bool notYRange = false;
    static const bool notZRange = false;
    static const bool XValue = true;
    static const bool YValue = true;
    static const bool ZValue = true;
    static const bool XRange = true;
    static const bool YRange = true;
    static const bool ZRange = true;
};

constexpr const char* SectionCutSchemaProperty = "VibeCADSectionCutSchema";
constexpr long SectionCutSchemaVersion = 1;
constexpr const char* SectionCutLinksProperty =
    "VibeCADSectionCutLinks";

struct SectionResourceDescriptor
{
    const char* objectRole;
    const char* propertyName;
};

constexpr std::array<SectionResourceDescriptor, 7>
    SectionResourceDescriptors {{
        {"SectionCutCompound", "VibeCADSectionCutCompound"},
        {"SectionCutBoxX", "VibeCADSectionCutBoxX"},
        {"SectionCutX", "VibeCADSectionCutX"},
        {"SectionCutBoxY", "VibeCADSectionCutBoxY"},
        {"SectionCutY", "VibeCADSectionCutY"},
        {"SectionCutBoxZ", "VibeCADSectionCutBoxZ"},
        {"SectionCutZ", "VibeCADSectionCutZ"},
    }};

int sectionResourceIndex(const char* objectRole)
{
    if (!objectRole) {
        return -1;
    }
    const auto found = std::ranges::find_if(
        SectionResourceDescriptors,
        [objectRole](const SectionResourceDescriptor& descriptor) {
            return std::strcmp(
                       descriptor.objectRole,
                       objectRole
                   )
                == 0;
        }
    );
    return found == SectionResourceDescriptors.end()
        ? -1
        : static_cast<int>(
              std::distance(
                  SectionResourceDescriptors.begin(),
                  found
              )
          );
}

bool isExactSectionCutOwner(const App::DocumentObject* object)
{
    const auto* schema = object
        ? dynamic_cast<const App::PropertyInteger*>(
              object->getPropertyByName(SectionCutSchemaProperty)
          )
        : nullptr;
    const auto* role = object
        ? dynamic_cast<const App::PropertyString*>(
              object->getPropertyByName(App::DocumentTimeline::RolePropertyName)
          )
        : nullptr;
    return object && object->isDerivedFrom<Part::Compound>() && schema
        && schema->getValue() == SectionCutSchemaVersion && role
        && std::string_view(role->getValue()) == App::DocumentTimeline::OperationRole;
}

bool isSectionCutPresentationSource(const App::DocumentObject* object)
{
    if (!object) {
        return false;
    }
    if (object->isDerivedFrom<App::Part>()) {
        return true;
    }
    if (object->isDerivedFrom<Part::BodyBase>()) {
        return object->getPropertyByName("Shape") != nullptr;
    }
    if (object->isDerivedFrom<Part::Feature>()
        && object->getPropertyByName("Shape")
        && !object->isDerivedFrom<Part::Part2DObject>()
        && !object->isDerivedFrom<Part::Datum>()
        && !object->isDerivedFrom<Part::Compound>()) {
        return App::GeoFeatureGroupExtension::getGroupOfObject(object)
            == nullptr;
    }
    const auto* link = freecad_cast<const App::Link*>(object);
    const auto* linked = link ? link->getLink() : nullptr;
    return linked
        && (linked->isDerivedFrom<Part::Feature>()
            || linked->isDerivedFrom<Part::BodyBase>())
        && linked->getPropertyByName("Shape");
}

void makeSectionCutMetadataInternal(App::Property* property)
{
    if (!property) {
        return;
    }
    property->setStatus(App::Property::Hidden, true);
    property->setStatus(App::Property::LockDynamic, true);
    property->setStatus(App::Property::NoRecompute, true);
}
}  // namespace

// NOLINTBEGIN(cppcoreguidelines-avoid-magic-numbers,readability-magic-numbers)
void SectionCut::ExactObjectIdentity::capture(
    App::DocumentObject* object
)
{
    if (!object || !object->getNameInDocument()
        || !object->isAttachedToDocument()) {
        clear();
        return;
    }
    objectId = object->getID();
    documentUid =
        object->getDocument()->Uid.getValueStr();
    objectName = object->getNameInDocument();
}

void SectionCut::ExactObjectIdentity::clear()
{
    objectId = -1;
    documentUid.clear();
    objectName.clear();
}

App::DocumentObject* SectionCut::ExactObjectIdentity::resolve(
    App::Document* document
) const
{
    if (!document || objectId <= 0 || documentUid.empty()
        || documentUid != document->Uid.getValueStr()
        || objectName.empty()) {
        return nullptr;
    }
    auto* object = document->getObjectByID(objectId);
    return object && object->getDocument() == document
        && document->containsObject(object)
        && object->getNameInDocument()
        && objectName == object->getNameInDocument()
        ? object
        : nullptr;
}

SectionCut::SectionCut(QWidget* parent)
    : QDialog(parent)
    , ui(new Ui_SectionCut)
{
    // create widgets
    ui->setupUi(this);
    initSpinBoxes();

    // get all objects in the document
    auto docGui = Gui::Application::Instance->activeDocument();
    if (!docGui) {
        throw Base::RuntimeError("Section cut error: there is no document");
    }
    doc = docGui->getDocument();
    if (!doc) {
        throw Base::RuntimeError("Section cut error: there is no document");
    }
    documentUid = doc->Uid.getValueStr();

    std::vector<App::DocumentObject*> ObjectsList = doc->getObjects();
    if (ObjectsList.empty()) {
        throw Base::RuntimeError("Section cut error: there are no objects in the document");
    }

    loadSemanticSources();
    if (!findSemanticOwner()) {
        captureLegacyResources();
    }
    if (ObjectsListVisible.empty()) {
        // A new section cut replaces exactly the shapes which were visible
        // when the command opened. Timeline infrastructure and owned helper
        // objects are never modeling inputs.
        for (auto anObject : ObjectsList) {
            if (anObject->Visibility.getValue()
                && PartGui::isModelingObjectActive(anObject)
                && isSectionCutPresentationSource(anObject)
                && !anObject->isDerivedFrom<App::DocumentTimeline>()
                && !App::DocumentTimeline::hasTimelineResourceRole(anObject)
                && !isExactSectionCutOwner(anObject)) {
                ObjectsListVisible.emplace_back(anObject);
            }
        }
    }

    // if we can have existing cut boxes, take their values
    // to access the flip state we must compare the bounding boxes of the cutbox and the compound
    Base::BoundBox3d BoundCompound = collectObjects();
    initControls(BoundCompound);

    initCutRanges();

    setupConnections();

    tryStartCutting();
}

bool SectionCut::atomicChangeActive() const
{
    return atomicChangeDepth != 0;
}

bool SectionCut::ownsLiveDocument() const
{
    if (!doc || documentUid.empty()) {
        return false;
    }
    const auto& documents = App::GetApplication().getDocuments();
    return std::ranges::find(documents, doc) != documents.end()
        && doc->Uid.getValueStr() == documentUid;
}

bool SectionCut::runAtomicChange(
    const char* transactionName,
    const std::function<void()>& change,
    bool publishResult
)
{
    if (atomicChangeActive()) {
        change();
        return true;
    }
    auto* activeGuiDocument = Gui::Application::Instance
        ? Gui::Application::Instance->activeDocument()
        : nullptr;
    if (!ownsLiveDocument() || !activeGuiDocument
        || activeGuiDocument->getDocument() != doc
        || !PartGui::canStartRetainedModelingTask(doc)) {
        Base::Console().warning(
            "Section cut edit was skipped because its document cannot own "
            "an exact modeling transaction\n"
        );
        return false;
    }

    std::unique_ptr<Gui::ExactTransaction> transaction;
    try {
        newResourceIdentities.clear();
        transaction = std::make_unique<Gui::ExactTransaction>(
            *doc,
            transactionName
        );
        ++atomicChangeDepth;
        change();
        if (publishResult) {
            // Link and compound view providers may hide their inputs while
            // the result graph is assembled. Restore the exact visible input
            // state before crossing the pre-creation History boundary.
            restoreVisibility();
            publishSemanticResult();
        }
        doc->recompute();
        if (publishResult) {
            validateSemanticResult();
            // Metadata classification itself publishes synchronous
            // view-provider callbacks. Reassert the pre-creation input state
            // at the final boundary as well.
            restoreVisibility();
            finalizeSemanticTimeline();
            // Publication must see the exact pre-creation accepted state.
            // Apply the replacement's final viewport state only after the
            // semantic block has crossed that boundary.
            setAcceptedVisibility(true);
        }
        --atomicChangeDepth;
        if (!transaction->commit()) {
            Base::Console().warning(
                "The section cut transaction is waiting for its exact commit "
                "boundary\n"
            );
        }
        return true;
    }
    catch (Base::Exception& error) {
        if (atomicChangeDepth != 0) {
            --atomicChangeDepth;
        }
        if (transaction) {
            (void)transaction->abort();
        }
        newResourceIdentities.clear();
        error.reportException();
    }
    catch (const std::exception& error) {
        if (atomicChangeDepth != 0) {
            --atomicChangeDepth;
        }
        if (transaction) {
            (void)transaction->abort();
        }
        newResourceIdentities.clear();
        Base::Console().error("Section cut edit failed: %s\n", error.what());
    }
    catch (...) {
        if (atomicChangeDepth != 0) {
            --atomicChangeDepth;
        }
        if (transaction) {
            (void)transaction->abort();
        }
        newResourceIdentities.clear();
        Base::Console().error("Section cut edit failed with an unknown exception\n");
    }
    return false;
}

Part::Compound* SectionCut::findSemanticOwner() const
{
    if (!doc) {
        return nullptr;
    }

    if (auto* cached = dynamic_cast<Part::Compound*>(
            semanticOwnerIdentity.resolve(doc)
        );
        isExactSectionCutOwner(cached)) {
        return cached;
    }
    semanticOwnerIdentity.clear();

    auto* timeline = App::DocumentTimeline::get(doc);
    if (!timeline) {
        return nullptr;
    }
    Part::Compound* result = nullptr;
    for (auto* object : timeline->Operations.getValues()) {
        if (!isExactSectionCutOwner(object)) {
            continue;
        }
        auto* candidate = static_cast<Part::Compound*>(object);
        if (result && result != candidate) {
            throw Base::RuntimeError(
                "The document contains more than one persistent section cut"
            );
        }
        result = candidate;
    }
    semanticOwnerIdentity.capture(result);
    return result;
}

std::vector<App::DocumentObject*> SectionCut::exactSourceObjects() const
{
    std::vector<App::DocumentObject*> result;
    result.reserve(ObjectsListVisible.size());
    for (const auto& handle : ObjectsListVisible) {
        auto* object = handle.getObject();
        if (!object || !doc || object->getDocument() != doc
            || !doc->containsObject(object)
            || std::ranges::find(result, object) != result.end()) {
            continue;
        }
        result.push_back(object);
    }
    return result;
}

void SectionCut::loadSemanticSources()
{
    ObjectsListVisible.clear();
    auto* owner = findSemanticOwner();
    if (!owner) {
        return;
    }
    ensureSemanticResourceProperties(*owner);
    clearLegacyResources();

    const auto* property = dynamic_cast<const App::PropertyLinkListHidden*>(
        owner->getPropertyByName(
            App::DocumentTimeline::ReplacedInputsPropertyName
        )
    );
    if (!property) {
        throw Base::RuntimeError(
            "The persistent section cut has no exact source contract"
        );
    }
    std::unordered_set<long> sourceIds;
    for (auto* source : property->getValues()) {
        if (!source || source == owner || source->getDocument() != doc
            || !doc->containsObject(source)
            || !sourceIds.insert(source->getID()).second) {
            throw Base::RuntimeError(
                "The persistent section cut has an invalid source contract"
            );
        }
        ObjectsListVisible.emplace_back(source);
    }
    if (ObjectsListVisible.empty()) {
        throw Base::RuntimeError(
            "The persistent section cut has no live source objects"
        );
    }
}

Part::Compound* SectionCut::ensureSemanticOwner()
{
    if (auto* owner = findSemanticOwner()) {
        ensureSemanticResourceProperties(*owner);
        return owner;
    }
    if (!doc) {
        throw Base::RuntimeError(
            "Cannot create a persistent section cut without a document"
        );
    }

    auto* owner = doc->addObject<Part::Compound>("SectionCut");
    if (!owner) {
        throw Base::RuntimeError(
            "The persistent section cut output could not be created"
        );
    }
    owner->Label.setValue(QCoreApplication::translate("PartGui", "Section Cut").toUtf8().constData());

    auto* schema = dynamic_cast<App::PropertyInteger*>(
        owner->addDynamicProperty(
            "App::PropertyInteger",
            SectionCutSchemaProperty,
            "Timeline",
            "Persistent section-cut schema",
            App::Prop_NoRecompute,
            true,
            true
        )
    );
    auto* role = dynamic_cast<App::PropertyString*>(
        owner->addDynamicProperty(
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName,
            "Timeline",
            "Document timeline classification",
            App::Prop_NoRecompute,
            true,
            true
        )
    );
    auto* replaced = dynamic_cast<App::PropertyLinkListHidden*>(
        owner->addDynamicProperty(
            "App::PropertyLinkListHidden",
            App::DocumentTimeline::ReplacedInputsPropertyName,
            "Timeline",
            "Visible input objects hidden by this operation",
            App::Prop_NoRecompute,
            true,
            true
        )
    );
    auto* editCommand = dynamic_cast<App::PropertyString*>(
        owner->addDynamicProperty(
            "App::PropertyString",
            App::DocumentTimeline::EditCommandPropertyName,
            "Timeline",
            "Approved command which edits this operation",
            App::Prop_NoRecompute,
            true,
            true
        )
    );
    if (!schema || !role || !replaced || !editCommand) {
        throw Base::RuntimeError(
            "The persistent section cut metadata could not be created"
        );
    }
    for (auto* property : std::array<App::Property*, 4> {
             schema,
             role,
             replaced,
             editCommand,
         }) {
        makeSectionCutMetadataInternal(property);
    }

    const auto sources = exactSourceObjects();
    if (sources.empty()) {
        throw Base::RuntimeError(
            "A persistent section cut requires at least one exact source"
        );
    }
    schema->setValue(SectionCutSchemaVersion);
    role->setValue(App::DocumentTimeline::OperationRole);
    replaced->setValues(sources);
    editCommand->setValue("Part_SectionCut");
    semanticOwnerIdentity.capture(owner);
    ensureSemanticResourceProperties(*owner);
    clearLegacyResources();

    if (auto* view = dynamic_cast<Gui::ViewProviderDocumentObject*>(
            Gui::Application::Instance->getViewProvider(owner)
        )) {
        view->ShowInTree.setValue(true);
    }
    return owner;
}

void SectionCut::ensureSemanticResourceProperties(
    Part::Compound& owner
)
{
    for (const auto& descriptor : SectionResourceDescriptors) {
        App::Property* property =
            owner.getPropertyByName(descriptor.propertyName);
        if (!property) {
            property = owner.addDynamicProperty(
                "App::PropertyLinkHidden",
                descriptor.propertyName,
                "Timeline",
                "Exact persistent section-cut implementation resource",
                App::Prop_NoRecompute,
                true,
                true
            );
        }
        if (!dynamic_cast<App::PropertyLinkHidden*>(property)) {
            throw Base::TypeError(
                "A persistent section-cut resource link has an "
                "incompatible type"
            );
        }
        makeSectionCutMetadataInternal(property);
    }

    App::Property* links =
        owner.getPropertyByName(SectionCutLinksProperty);
    if (!links) {
        links = owner.addDynamicProperty(
            "App::PropertyLinkListHidden",
            SectionCutLinksProperty,
            "Timeline",
            "Exact generated links owned by this persistent section cut",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    if (!dynamic_cast<App::PropertyLinkListHidden*>(links)) {
        throw Base::TypeError(
            "The persistent section-cut generated-link property has an "
            "incompatible type"
        );
    }
    makeSectionCutMetadataInternal(links);
}

App::DocumentObject* SectionCut::semanticResource(
    const char* objectRole
) const
{
    const int index = sectionResourceIndex(objectRole);
    if (index < 0 || !doc) {
        return nullptr;
    }

    if (auto* owner = findSemanticOwner()) {
        const auto* property =
            dynamic_cast<const App::PropertyLinkHidden*>(
                owner->getPropertyByName(
                    SectionResourceDescriptors[
                        static_cast<std::size_t>(index)
                    ]
                        .propertyName
                )
            );
        auto* resource = property ? property->getValue() : nullptr;
        return resource && resource != owner
            && resource->getDocument() == doc
            && doc->containsObject(resource)
            ? resource
            : nullptr;
    }

    return legacyResourceIdentities[
        static_cast<std::size_t>(index)
    ]
        .resolve(doc);
}

void SectionCut::setSemanticResource(
    const char* objectRole,
    App::DocumentObject* resource
)
{
    const int index = sectionResourceIndex(objectRole);
    if (index < 0) {
        throw Base::ValueError(
            "Unknown persistent section-cut resource role"
        );
    }
    auto* owner = ensureSemanticOwner();
    auto* property = dynamic_cast<App::PropertyLinkHidden*>(
        owner->getPropertyByName(
            SectionResourceDescriptors[
                static_cast<std::size_t>(index)
            ]
                .propertyName
        )
    );
    if (!property) {
        throw Base::TypeError(
            "The persistent section-cut resource link is unavailable"
        );
    }
    if (resource
        && (resource == owner || resource->getDocument() != doc
            || !doc->containsObject(resource))) {
        throw Base::ValueError(
            "A persistent section-cut resource must be a distinct live "
            "object from the owning document"
        );
    }
    property->setValue(resource);
    if (resource) {
        markOwnedResource(*resource, *owner);
        rememberNewResource(resource);
    }
}

std::vector<App::DocumentObject*>
SectionCut::semanticLinkResources() const
{
    if (!doc) {
        return {};
    }
    if (auto* owner = findSemanticOwner()) {
        const auto* property =
            dynamic_cast<const App::PropertyLinkListHidden*>(
                owner->getPropertyByName(SectionCutLinksProperty)
            );
        if (!property) {
            return {};
        }
        std::vector<App::DocumentObject*> result;
        for (auto* resource : property->getValues()) {
            if (!resource || resource == owner
                || resource->getDocument() != doc
                || !doc->containsObject(resource)) {
                return {};
            }
            result.push_back(resource);
        }
        return result;
    }

    std::vector<App::DocumentObject*> result;
    result.reserve(legacyLinkIdentities.size());
    for (const auto& identity : legacyLinkIdentities) {
        auto* resource = identity.resolve(doc);
        if (!resource) {
            return {};
        }
        result.push_back(resource);
    }
    return result;
}

void SectionCut::setSemanticLinkResources(
    const std::vector<App::DocumentObject*>& resources
)
{
    auto* owner = ensureSemanticOwner();
    auto* property =
        dynamic_cast<App::PropertyLinkListHidden*>(
            owner->getPropertyByName(SectionCutLinksProperty)
        );
    if (!property) {
        throw Base::TypeError(
            "The persistent section-cut generated-link property is "
            "unavailable"
        );
    }

    std::unordered_set<App::DocumentObject*> distinct;
    for (auto* resource : resources) {
        if (!resource || resource == owner
            || resource->getDocument() != doc
            || !doc->containsObject(resource)
            || !distinct.insert(resource).second) {
            throw Base::ValueError(
                "Persistent section-cut generated links must be distinct "
                "live objects from the owning document"
            );
        }
    }
    property->setValues(resources);
    for (auto* resource : resources) {
        markOwnedResource(*resource, *owner);
        rememberNewResource(resource);
    }
}

void SectionCut::rememberNewResource(
    App::DocumentObject* resource
)
{
    auto* timeline = doc ? App::DocumentTimeline::get(doc) : nullptr;
    if (!resource || !timeline
        || !timeline->isProvisionallyEnrolledByCurrentTransaction(
            resource
        )) {
        return;
    }
    if (std::ranges::any_of(
            newResourceIdentities,
            [this, resource](const ExactObjectIdentity& identity) {
                return identity.resolve(doc) == resource;
            }
        )) {
        return;
    }
    ExactObjectIdentity identity;
    identity.capture(resource);
    newResourceIdentities.push_back(std::move(identity));
}

void SectionCut::clearLegacyResources()
{
    for (auto& identity : legacyResourceIdentities) {
        identity.clear();
    }
    legacyLinkIdentities.clear();
}

void SectionCut::captureLegacyResources()
{
    clearLegacyResources();
    if (!doc || findSemanticOwner()) {
        return;
    }

    std::array<Part::Box*, 3> boxes {
        dynamic_cast<Part::Box*>(
            doc->getObject("SectionCutBoxX")
        ),
        dynamic_cast<Part::Box*>(
            doc->getObject("SectionCutBoxY")
        ),
        dynamic_cast<Part::Box*>(
            doc->getObject("SectionCutBoxZ")
        ),
    };
    std::array<Part::Cut*, 3> cuts {
        dynamic_cast<Part::Cut*>(
            doc->getObject("SectionCutX")
        ),
        dynamic_cast<Part::Cut*>(
            doc->getObject("SectionCutY")
        ),
        dynamic_cast<Part::Cut*>(
            doc->getObject("SectionCutZ")
        ),
    };

    App::DocumentObject* previous = nullptr;
    App::DocumentObject* compound = nullptr;
    bool foundAxis = false;
    for (std::size_t axis = 0; axis < cuts.size(); ++axis) {
        if (!boxes[axis] && !cuts[axis]) {
            continue;
        }
        if (!boxes[axis] || !cuts[axis]
            || cuts[axis]->Tool.getValue() != boxes[axis]) {
            clearLegacyResources();
            return;
        }
        auto* base = cuts[axis]->Base.getValue();
        if (previous) {
            if (base != previous) {
                clearLegacyResources();
                return;
            }
        }
        else {
            compound = base;
        }
        previous = cuts[axis];
        foundAxis = true;
    }
    if (!foundAxis || !compound
        || compound->getDocument() != doc
        || !doc->containsObject(compound)
        || doc->getObject("SectionCutCompound") != compound) {
        clearLegacyResources();
        return;
    }

    legacyResourceIdentities[0].capture(compound);
    for (std::size_t axis = 0; axis < cuts.size(); ++axis) {
        if (!cuts[axis]) {
            continue;
        }
        legacyResourceIdentities[axis * 2 + 1].capture(
            boxes[axis]
        );
        legacyResourceIdentities[axis * 2 + 2].capture(
            cuts[axis]
        );
    }

    std::vector<App::DocumentObject*> links;
    if (auto* partCompound =
            dynamic_cast<Part::Compound*>(compound)) {
        links = partCompound->Links.getValues();
    }
    else if (auto* property =
                 dynamic_cast<App::PropertyLinkList*>(
                     compound->getPropertyByName("Objects")
                 )) {
        links = property->getValues();
    }
    if (links.empty()) {
        clearLegacyResources();
        return;
    }
    for (auto* link : links) {
        if (!link || link->getDocument() != doc
            || !doc->containsObject(link)) {
            clearLegacyResources();
            return;
        }
        ExactObjectIdentity identity;
        identity.capture(link);
        legacyLinkIdentities.push_back(std::move(identity));
    }
}

App::DocumentObject* SectionCut::semanticTopResult() const
{
    for (const char* name : {CutZName, CutYName, CutXName, CompoundName}) {
        if (auto* object = semanticResource(name)) {
            return object;
        }
    }
    return nullptr;
}

void SectionCut::markOwnedResource(
    App::DocumentObject& resource,
    Part::Compound& owner
)
{
    if (&resource == &owner || resource.getDocument() != doc
        || !doc->containsObject(&resource)) {
        throw Base::RuntimeError(
            "A section-cut resource must be a distinct live document object"
        );
    }

    App::Property* roleProperty = resource.getPropertyByName(
        App::DocumentTimeline::RolePropertyName
    );
    if (!roleProperty) {
        roleProperty = resource.addDynamicProperty(
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName,
            "Timeline",
            "Document timeline classification",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    auto* role = dynamic_cast<App::PropertyString*>(roleProperty);

    App::Property* ownerProperty = resource.getPropertyByName(
        App::DocumentTimeline::OwnerPropertyName
    );
    if (!ownerProperty) {
        ownerProperty = resource.addDynamicProperty(
            "App::PropertyLinkHidden",
            App::DocumentTimeline::OwnerPropertyName,
            "Timeline",
            "Persistent section cut which owns this generated result",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    auto* resourceOwner = dynamic_cast<App::PropertyLinkHidden*>(ownerProperty);
    if (!role || !resourceOwner) {
        throw Base::TypeError(
            "Section-cut timeline metadata has an incompatible type"
        );
    }
    makeSectionCutMetadataInternal(role);
    makeSectionCutMetadataInternal(resourceOwner);

    resourceOwner->setValue(&owner);
    role->setValue(App::DocumentTimeline::ResourceRole);
    resource.Visibility.setValue(false);
    if (auto* view = dynamic_cast<Gui::ViewProviderDocumentObject*>(
            Gui::Application::Instance->getViewProvider(&resource)
        )) {
        view->ShowInTree.setValue(false);
    }
}

void SectionCut::markReachableResources(
    App::DocumentObject& top,
    Part::Compound& owner
)
{
    const auto sources = exactSourceObjects();
    const std::unordered_set<App::DocumentObject*> sourceSet(
        sources.begin(),
        sources.end()
    );
    std::vector<App::DocumentObject*> pending {&top};
    std::unordered_set<App::DocumentObject*> visited;
    while (!pending.empty()) {
        auto* object = pending.back();
        pending.pop_back();
        if (!object || object == &owner || sourceSet.contains(object)
            || !visited.insert(object).second) {
            continue;
        }
        if (object->getDocument() != doc || !doc->containsObject(object)) {
            throw Base::RuntimeError(
                "The section-cut result graph left its document"
            );
        }
        markOwnedResource(*object, owner);
        const auto dependencies = object->getOutList();
        pending.insert(
            pending.end(),
            dependencies.begin(),
            dependencies.end()
        );
    }
}

void SectionCut::publishSemanticResult()
{
    const auto sources = exactSourceObjects();
    if (sources.empty()) {
        throw Base::RuntimeError(
            "A persistent section cut requires live source objects"
        );
    }

    auto* top = semanticTopResult();
    auto* owner = findSemanticOwner();
    if (!top && !owner && !isCuttingEnabled()) {
        return;
    }
    owner = owner ? owner : ensureSemanticOwner();

    auto* schema = dynamic_cast<App::PropertyInteger*>(
        owner->getPropertyByName(SectionCutSchemaProperty)
    );
    auto* role = dynamic_cast<App::PropertyString*>(
        owner->getPropertyByName(
            App::DocumentTimeline::RolePropertyName
        )
    );
    auto* replaced = dynamic_cast<App::PropertyLinkListHidden*>(
        owner->getPropertyByName(
            App::DocumentTimeline::ReplacedInputsPropertyName
        )
    );
    App::Property* editProperty = owner->getPropertyByName(
        App::DocumentTimeline::EditCommandPropertyName
    );
    if (!editProperty) {
        editProperty = owner->addDynamicProperty(
            "App::PropertyString",
            App::DocumentTimeline::EditCommandPropertyName,
            "Timeline",
            "Approved command which edits this operation",
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    auto* editCommand = dynamic_cast<App::PropertyString*>(editProperty);
    if (!schema || !role || !replaced || !editCommand) {
        throw Base::TypeError(
            "The persistent section cut metadata has an incompatible type"
        );
    }
    for (auto* property : std::array<App::Property*, 4> {
             schema,
             role,
             replaced,
             editCommand,
         }) {
        makeSectionCutMetadataInternal(property);
    }
    schema->setValue(SectionCutSchemaVersion);
    role->setValue(App::DocumentTimeline::OperationRole);
    replaced->setValues(sources);
    editCommand->setValue("Part_SectionCut");

    if (top) {
        markReachableResources(*top, *owner);
        owner->Links.setValues({top});
        syncSemanticAppearance(*owner, *top);
    }
    else {
        owner->Links.setValues(sources);
    }

}

void SectionCut::validateSemanticResult() const
{
    auto* owner = findSemanticOwner();
    if (!owner) {
        if (!isCuttingEnabled()) {
            return;
        }
        throw Base::RuntimeError(
            "The section cut did not publish its semantic output"
        );
    }

    const auto sources = exactSourceObjects();
    const auto replacement =
        App::DocumentTimeline::replacementInputContract(owner);
    const auto* editCommand = dynamic_cast<const App::PropertyString*>(
        owner->getPropertyByName(
            App::DocumentTimeline::EditCommandPropertyName
        )
    );
    if (!isExactSectionCutOwner(owner) || !replacement.declared
        || !replacement.valid || replacement.inputs != sources
        || !editCommand
        || std::string_view(editCommand->getValue()) != "Part_SectionCut"
        || owner->Links.getValues().empty()
        || !hasCompleteCutGraph()) {
        throw Base::RuntimeError(
            "The section cut semantic contract is incomplete"
        );
    }

    PartGui::TaskResultValidation::validatePartResult(owner);
    for (auto* linked : owner->Links.getValues()) {
        if (!linked || linked->getDocument() != doc
            || !doc->containsObject(linked)) {
            throw Base::RuntimeError(
                "The section cut output references a stale result"
            );
        }
    }

    const std::unordered_set<App::DocumentObject*> sourceSet(
        sources.begin(),
        sources.end()
    );
    std::vector<App::DocumentObject*> pending = owner->Links.getValues();
    std::unordered_set<App::DocumentObject*> visited;
    while (!pending.empty()) {
        auto* resource = pending.back();
        pending.pop_back();
        if (!resource || resource == owner || sourceSet.contains(resource)
            || !visited.insert(resource).second) {
            continue;
        }
        if (!App::DocumentTimeline::hasTimelineResourceRole(resource)
            || App::DocumentTimeline::timelineOwner(resource) != owner
            || resource->Visibility.getValue()) {
            throw Base::RuntimeError(
                "The section cut contains an unowned or visible implementation resource"
            );
        }
        const auto dependencies = resource->getOutList();
        pending.insert(
            pending.end(),
            dependencies.begin(),
            dependencies.end()
        );
    }
}

void SectionCut::finalizeSemanticTimeline()
{
    auto* owner = findSemanticOwner();
    if (!owner || !doc || owner->getDocument() != doc
        || !doc->containsObject(owner)) {
        return;
    }
    auto* timeline = App::DocumentTimeline::ensure(doc);
    const bool newOwner =
        timeline->isProvisionallyEnrolledByCurrentTransaction(owner);

    std::vector<App::DocumentObject*> orderedNewResources;
    orderedNewResources.reserve(newResourceIdentities.size());
    for (const auto& identity : newResourceIdentities) {
        auto* candidate = identity.resolve(doc);
        if (!candidate
            || !timeline->isProvisionallyEnrolledByCurrentTransaction(
                candidate
            )
            || !App::DocumentTimeline::hasTimelineResourceRole(
                candidate
            )
            || App::DocumentTimeline::timelineOwner(candidate)
                != owner) {
            throw Base::RuntimeError(
                "The section cut lost an exact newly created resource "
                "before its history boundary"
            );
        }
        orderedNewResources.push_back(candidate);
    }
    if (newOwner) {
        owner = findSemanticOwner();
        if (!owner
            || !timeline->isProvisionallyEnrolledByCurrentTransaction(
                owner
            )) {
            throw Base::RuntimeError(
                "The section cut lost its exact newly created operation "
                "before publication"
            );
        }
        // A newly created section cut is a complete current-transaction
        // factory result. Publish it from the transaction's captured
        // pre-creation boundary so its replaced sources are necessarily
        // earlier semantic operations. The generic finalize path derives an
        // insertion point from already-reclassified resources and can place
        // this replacement block before one of its source operations.
        timeline->publishProvisionalOperationBlock(
            owner,
            orderedNewResources
        );
    }
    else if (!orderedNewResources.empty()) {
        timeline->finalizeProvisionalOperationBlock(
            owner,
            orderedNewResources
        );
    }
    newResourceIdentities.clear();
}

bool SectionCut::hasCompleteCutGraph() const
{
    auto* owner = findSemanticOwner();
    if (!owner) {
        return !isCuttingEnabled();
    }

    const auto requiresAxis = [this](
                                  bool enabled,
                                  const char* boxName,
                                  const char* cutName
                              ) {
        return enabled
            ? semanticResource(boxName)
                && semanticResource(cutName)
            : !semanticResource(boxName)
                && !semanticResource(cutName);
    };
    if (!requiresAxis(ui->groupBoxX->isChecked(), BoxXName, CutXName)
        || !requiresAxis(ui->groupBoxY->isChecked(), BoxYName, CutYName)
        || !requiresAxis(ui->groupBoxZ->isChecked(), BoxZName, CutZName)) {
        return false;
    }

    const auto links = owner->Links.getValues();
    if (!isCuttingEnabled()) {
        return links == exactSourceObjects();
    }
    auto* top = ui->groupBoxZ->isChecked()
        ? semanticResource(CutZName)
        : ui->groupBoxY->isChecked()
        ? semanticResource(CutYName)
        : semanticResource(CutXName);
    return top && links.size() == 1 && links.front() == top
        && App::DocumentTimeline::timelineOwner(top) == owner;
}

void SectionCut::syncSemanticAppearance(
    Part::Compound& owner,
    App::DocumentObject& result
) const
{
    auto* ownerView = dynamic_cast<Gui::ViewProviderGeometryObject*>(
        Gui::Application::Instance->getViewProvider(&owner)
    );
    auto* resultView = dynamic_cast<Gui::ViewProviderGeometryObject*>(
        Gui::Application::Instance->getViewProvider(&result)
    );
    if (!ownerView || !resultView) {
        throw Base::RuntimeError(
            "The section cut output has no geometric view provider"
        );
    }
    ownerView->ShapeAppearance.setValues(
        resultView->ShapeAppearance.getValues()
    );
    ownerView->Transparency.setValue(
        resultView->Transparency.getValue()
    );
}

void SectionCut::setAcceptedVisibility(bool showCut)
{
    auto* owner = findSemanticOwner();
    if (!owner) {
        for (auto* source : exactSourceObjects()) {
            source->Visibility.setValue(true);
        }
        return;
    }
    for (auto* source : exactSourceObjects()) {
        source->Visibility.setValue(!showCut);
    }
    owner->Visibility.setValue(showCut);

    const auto exactSources = exactSourceObjects();
    const std::unordered_set<App::DocumentObject*> sources(
        exactSources.begin(),
        exactSources.end()
    );
    std::vector<App::DocumentObject*> pending = owner->Links.getValues();
    std::unordered_set<App::DocumentObject*> visited;
    while (!pending.empty()) {
        auto* object = pending.back();
        pending.pop_back();
        if (!object || object == owner || sources.contains(object)
            || !visited.insert(object).second) {
            continue;
        }
        object->Visibility.setValue(false);
        const auto dependencies = object->getOutList();
        pending.insert(
            pending.end(),
            dependencies.begin(),
            dependencies.end()
        );
    }
}

void SectionCut::initSpinBoxes()
{
    constexpr int max = std::numeric_limits<int>::max();
    ui->cutX->setRange(-max, max);
    ui->cutY->setRange(-max, max);
    ui->cutZ->setRange(-max, max);
}

void SectionCut::initControls(const Base::BoundBox3d& BoundCompound)
{
    // lambda function to set color and transparency
    auto setColorTransparency = [&](Part::Box* pcBox) {
        Base::Color cutColor;
        long cutTransparency {};
        auto vpBox = dynamic_cast<Gui::ViewProviderGeometryObject*>(
            Gui::Application::Instance->getViewProvider(pcBox)
        );
        if (vpBox) {
            cutColor = vpBox->ShapeAppearance.getDiffuseColor();
            cutTransparency = vpBox->Transparency.getValue();
            ui->CutColor->setColor(cutColor.asValue<QColor>());
            ui->CutTransparencyHS->setValue(int(cutTransparency));
            ui->CutTransparencyHS->setToolTip(QString::number(cutTransparency) + QStringLiteral(" %"));
        }
    };

    initZControls(BoundCompound, setColorTransparency);
    initYControls(BoundCompound, setColorTransparency);
    initXControls(BoundCompound, setColorTransparency);
}

void SectionCut::initXControls(
    const Base::BoundBox3d& BoundCompound,
    const std::function<void(Part::Box*)>& setTransparency
)
{
    Base::BoundBox3d BoundCutBox;
    if (auto pcBox = findCutBox(BoxXName)) {
        hasBoxX = true;
        ui->groupBoxX->setChecked(true);
        BoundCutBox = pcBox->Shape.getBoundingBox();
        if (BoundCutBox.MinX > BoundCompound.MinX) {
            ui->cutX->setValue(pcBox->Placement.getValue().getPosition().x);
            ui->flipX->setChecked(true);
        }
        else {
            ui->cutX->setValue(pcBox->Length.getValue() + pcBox->Placement.getValue().getPosition().x);
            ui->flipX->setChecked(false);
        }
        setTransparency(pcBox);
    }
}

void SectionCut::initYControls(
    const Base::BoundBox3d& BoundCompound,
    const std::function<void(Part::Box*)>& setTransparency
)
{
    Base::BoundBox3d BoundCutBox;
    if (auto pcBox = findCutBox(BoxYName)) {
        hasBoxY = true;
        ui->groupBoxY->setChecked(true);
        BoundCutBox = pcBox->Shape.getBoundingBox();
        if (BoundCutBox.MinY > BoundCompound.MinY) {
            ui->cutY->setValue(pcBox->Placement.getValue().getPosition().y);
            ui->flipY->setChecked(true);
        }
        else {
            ui->cutY->setValue(pcBox->Width.getValue() + pcBox->Placement.getValue().getPosition().y);
            ui->flipY->setChecked(false);
        }
        setTransparency(pcBox);
    }
}

void SectionCut::initZControls(
    const Base::BoundBox3d& BoundCompound,
    const std::function<void(Part::Box*)>& setTransparency
)
{
    Base::BoundBox3d BoundCutBox;
    if (auto pcBox = findCutBox(BoxZName)) {
        hasBoxZ = true;
        ui->groupBoxZ->setChecked(true);
        // if z of cutbox bounding is greater than z of compound bounding
        // we know that the cutbox is in flipped state
        BoundCutBox = pcBox->Shape.getBoundingBox();
        if (BoundCutBox.MinZ > BoundCompound.MinZ) {
            ui->cutZ->setValue(pcBox->Placement.getValue().getPosition().z);
            ui->flipZ->setChecked(true);
        }
        else {
            ui->cutZ->setValue(pcBox->Height.getValue() + pcBox->Placement.getValue().getPosition().z);
            ui->flipZ->setChecked(false);
        }
        // set color and transparency
        setTransparency(pcBox);
    }
}

void SectionCut::initCutRanges()
{
    // get bounding box
    SbBox3f box = getViewBoundingBox();
    if (!box.isEmpty()) {  // NOLINT
        // if there is a cut box, perform the cut
        if (hasBoxX || hasBoxY || hasBoxZ) {
            // refresh only the range since we set the values above already
            refreshCutRanges(
                box,
                Refresh::notXValue,
                Refresh::notYValue,
                Refresh::notZValue,
                Refresh::XRange,
                Refresh::YRange,
                Refresh::ZRange
            );
        }
        else {
            refreshCutRanges(
                box,
                Refresh::XValue,
                Refresh::YValue,
                Refresh::ZValue,
                Refresh::XRange,
                Refresh::YRange,
                Refresh::ZRange
            );
        }
    }
}

void SectionCut::tryStartCutting()
{
    if (!(hasBoxX || hasBoxY || hasBoxZ)) {
        return;
    }
    ui->RefreshCutPB->setEnabled(false);

    // A current document already contains the accepted parametric graph. Do
    // not regenerate it merely because the editor was opened: that would
    // replace stable resource identities and create a false history edit.
    if (findSemanticOwner()) {
        return;
    }

    // Adopt the old persistent-cut graph once. The exact transaction turns
    // the previously visible sources into one durable semantic operation and
    // makes every implementation object an owned resource.
    runAtomicChange(
        "Adopt persistent section cut",
        [this]() {
            startCutting(true);
        }
    );
}

void SectionCut::setAutoColoringChecked(bool on)
{
    ui->autoCutfaceColorCB->setChecked(on);
    ui->autoBFColorCB->setChecked(on);
}

void SectionCut::setSlidersEnabled(bool on)
{
    ui->cutXHS->setEnabled(on);
    ui->cutYHS->setEnabled(on);
    ui->cutZHS->setEnabled(on);
}

void SectionCut::setSlidersToolTip(const QString& text)
{
    ui->cutXHS->setToolTip(text);
    ui->cutYHS->setToolTip(text);
    ui->cutZHS->setToolTip(text);
}

void SectionCut::setGroupsDisabled()
{
    ui->groupBoxX->blockSignals(true);
    ui->groupBoxY->blockSignals(true);
    ui->groupBoxZ->blockSignals(true);
    ui->groupBoxX->setChecked(false);
    ui->groupBoxY->setChecked(false);
    ui->groupBoxZ->setChecked(false);
    ui->RefreshCutPB->setEnabled(true);
    ui->groupBoxX->blockSignals(false);
    ui->groupBoxY->blockSignals(false);
    ui->groupBoxZ->blockSignals(false);
}

void SectionCut::initBooleanFragmentControls(Gui::ViewProviderGeometryObject* compoundBF)
{
    // for BooleanFragments we also need to set the checkbox, transparency and color
    ui->groupBoxIntersecting->setChecked(true);

    if (compoundBF) {
        Base::Color compoundColor = compoundBF->ShapeAppearance.getDiffuseColor();
        ui->BFragColor->setColor(compoundColor.asValue<QColor>());
        long compoundTransparency = compoundBF->Transparency.getValue();
        ui->BFragTransparencyHS->setValue(int(compoundTransparency));
        ui->BFragTransparencyHS->setToolTip(
            QString::number(compoundTransparency) + QStringLiteral(" %")
        );
        // Part::Cut ignores the cutbox transparency when it is set
        // to zero and the BooleanFragments transparency is not zero
        // therefore limit the cutbox transparency to 1 in this case
        ui->CutTransparencyHS->setMinimum(compoundTransparency > 0 ? 1 : 0);
    }
}

void SectionCut::collectAndShowLinks(const std::vector<App::DocumentObject*>& objects)
{
    const bool hasSemanticOwner = findSemanticOwner() != nullptr;
    // make parent objects of links visible to handle the case that
    // the cutting is started when only an existing cut was visible
    for (auto aCompoundObj : objects) {
        auto pcLink = dynamic_cast<App::Link*>(aCompoundObj);
        auto LinkedObject = pcLink ? pcLink->getLink() : nullptr;
        if (LinkedObject) {
            const auto alreadyTracked = std::ranges::any_of(
                ObjectsListVisible,
                [LinkedObject](const App::DocumentObjectT& object) {
                    return object.getObject() == LinkedObject;
                }
            );
            if (!alreadyTracked) {
                ObjectsListVisible.emplace_back(LinkedObject);
            }
            // Legacy cuts did not persist their original-source contract, so
            // expose their links while the one-time adoption rebuilds them.
            if (!hasSemanticOwner && !LinkedObject->Visibility.getValue()) {
                LinkedObject->Visibility.setValue(true);
            }
        }
    }
}

Base::BoundBox3d SectionCut::collectObjects()
{
    Base::BoundBox3d BoundCompound;
    if (findObject(BoxXName) || findObject(BoxYName)
        || findObject(BoxZName)) {

        // automatic coloring must be disabled
        setAutoColoringChecked(false);

        if (auto* compoundObject = findObject(CompoundName)) {
            if (!findSemanticOwner()) {
                // The legacy implementation persisted only its generated
                // dependency graph. Its direct CutLink targets are therefore
                // the authoritative set of formerly visible sources.
                ObjectsListVisible.clear();
            }
            // to later store the childs
            std::vector<App::DocumentObject*> compoundChilds =
                semanticLinkResources();

            // check if this is a BooleanFragments or a Part::Compound
            // Part::Compound is the case when there was only one object
            auto pcCompoundPart = dynamic_cast<Part::Compound*>(compoundObject);
            auto pcPartFeature = dynamic_cast<Part::Feature*>(compoundObject);
            if (!pcCompoundPart && pcPartFeature) {
                // for more security check for validity accessing its ViewProvider
                auto pcCompoundBF = Gui::Application::Instance->getViewProvider(pcPartFeature);
                if (compoundChilds.empty()) {
                    compoundChilds =
                        pcCompoundBF->claimChildren();
                }
                BoundCompound = pcPartFeature->Shape.getBoundingBox();

                auto pcCompoundBFGO = dynamic_cast<Gui::ViewProviderGeometryObject*>(pcCompoundBF);
                initBooleanFragmentControls(pcCompoundBFGO);
            }
            else if (pcCompoundPart) {
                BoundCompound = pcCompoundPart->Shape.getBoundingBox();
                if (compoundChilds.empty()) {
                    pcCompoundPart->Links.getLinks(
                        compoundChilds
                    );
                }
            }

            collectAndShowLinks(compoundChilds);
        }
    }

    return BoundCompound;
}

void SectionCut::setupConnections()
{
    // clang-format off
    connect(ui->groupBoxX, &QGroupBox::toggled,
            this, &SectionCut::onGroupBoxXtoggled);
    connect(ui->groupBoxY, &QGroupBox::toggled,
            this, &SectionCut::onGroupBoxYtoggled);
    connect(ui->groupBoxZ, &QGroupBox::toggled,
            this, &SectionCut::onGroupBoxZtoggled);
    connect(ui->cutX, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &SectionCut::onCutXvalueChanged);
    connect(ui->cutY, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &SectionCut::onCutYvalueChanged);
    connect(ui->cutZ, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &SectionCut::onCutZvalueChanged);
    connect(ui->cutXHS, &QSlider::sliderMoved,
            this, &SectionCut::onCutXHSsliderMoved);
    connect(ui->cutYHS, &QSlider::sliderMoved,
            this, &SectionCut::onCutYHSsliderMoved);
    connect(ui->cutZHS, &QSlider::sliderMoved,
            this, &SectionCut::onCutZHSsliderMoved);
    connect(ui->cutXHS, &QSlider::valueChanged,
            this, &SectionCut::onCutXHSChanged);
    connect(ui->cutYHS, &QSlider::valueChanged,
            this, &SectionCut::onCutYHSChanged);
    connect(ui->cutZHS, &QSlider::valueChanged,
            this, &SectionCut::onCutZHSChanged);
    connect(ui->flipX, &QPushButton::clicked,
            this, &SectionCut::onFlipXclicked);
    connect(ui->flipY, &QPushButton::clicked,
            this, &SectionCut::onFlipYclicked);
    connect(ui->flipZ, &QPushButton::clicked,
            this, &SectionCut::onFlipZclicked);
    connect(ui->RefreshCutPB, &QPushButton::clicked,
            this, &SectionCut::onRefreshCutPBclicked);
    connect(ui->CutColor, &QPushButton::clicked,
            this, &SectionCut::onCutColorclicked);
    connect(ui->CutTransparencyHS, &QSlider::sliderMoved,
            this, &SectionCut::onTransparencyHSMoved);
    connect(ui->CutTransparencyHS, &QSlider::valueChanged,
            this, &SectionCut::onTransparencyHSChanged);
    connect(ui->groupBoxIntersecting, &QGroupBox::toggled,
            this, &SectionCut::onGroupBoxIntersectingToggled);
    connect(ui->BFragColor, &QPushButton::clicked,
            this, &SectionCut::onBFragColorclicked);
    connect(ui->BFragTransparencyHS, &QSlider::sliderMoved,
            this, &SectionCut::onBFragTransparencyHSMoved);
    connect(ui->BFragTransparencyHS,&QSlider::valueChanged,
            this, &SectionCut::onBFragTransparencyHSChanged);
    // clang-format on
}

void SectionCut::hideCutObjects()
{
    if (auto obj = findObject(CutXName)) {
        obj->Visibility.setValue(false);
    }
    if (auto obj = findObject(CutYName)) {
        obj->Visibility.setValue(false);
    }
    if (auto obj = findObject(CutZName)) {
        obj->Visibility.setValue(false);
    }
}

// actions to be done when document was closed
void SectionCut::noDocumentActions()
{
    ui->groupBoxX->blockSignals(true);
    ui->groupBoxY->blockSignals(true);
    ui->groupBoxZ->blockSignals(true);
    doc = nullptr;
    documentUid.clear();
    semanticOwnerIdentity.clear();
    newResourceIdentities.clear();
    clearLegacyResources();
    closeFinalized = true;
    // reset the cut group boxes
    ui->groupBoxX->setChecked(false);
    ui->groupBoxY->setChecked(false);
    ui->groupBoxZ->setChecked(false);
    ui->RefreshCutPB->setEnabled(true);
    ui->groupBoxX->blockSignals(false);
    ui->groupBoxY->blockSignals(false);
    ui->groupBoxZ->blockSignals(false);
}

void SectionCut::setAutoColor(const QColor& color)
{
    if (ui->autoCutfaceColorCB->isChecked()) {
        ui->CutColor->blockSignals(true);
        ui->CutColor->setColor(color);
        ui->CutColor->blockSignals(false);
    }
    if (ui->autoBFColorCB->isChecked()) {
        ui->BFragColor->blockSignals(true);
        ui->BFragColor->setColor(color);
        ui->BFragColor->blockSignals(false);
    }
}

void SectionCut::setAutoTransparency(int value)
{
    if (ui->autoCutfaceColorCB->isChecked()) {
        ui->CutTransparencyHS->blockSignals(true);
        ui->CutTransparencyHS->setValue(value);
        ui->CutTransparencyHS->setToolTip(QString::number(value) + QStringLiteral(" %"));
        ui->CutTransparencyHS->blockSignals(false);
    }
    if (ui->autoBFColorCB->isChecked()) {
        ui->BFragTransparencyHS->blockSignals(true);
        ui->BFragTransparencyHS->setValue(value);
        ui->BFragTransparencyHS->setToolTip(QString::number(value) + QStringLiteral(" %"));
        ui->BFragTransparencyHS->blockSignals(false);
    }
}

void SectionCut::deleteObejcts()
{
    auto deleteObject = [&](const char* objectRole) {
        auto* object = findObject(objectRole);
        if (!object) {
            return;
        }
        // the deleted object might have been visible before, thus check and delete it from the list
        auto found = std::find_if(
            ObjectsListVisible.begin(),
            ObjectsListVisible.end(),
            [object](const App::DocumentObjectT& obj) {
                return obj.getObject() == object;
            }
        );
        if (found != ObjectsListVisible.end()) {
            ObjectsListVisible.erase(found);
        }
        if (auto* owner = findSemanticOwner()) {
            const int index = sectionResourceIndex(objectRole);
            auto* property =
                index >= 0
                ? dynamic_cast<App::PropertyLinkHidden*>(
                      owner->getPropertyByName(
                          SectionResourceDescriptors[
                              static_cast<std::size_t>(index)
                          ]
                              .propertyName
                      )
                  )
                : nullptr;
            if (!property || property->getValue() != object) {
                throw Base::RuntimeError(
                    "The section cut refused to delete an unbound "
                    "implementation resource"
                );
            }
            property->setValue(nullptr);
        }
        else {
            const int index = sectionResourceIndex(objectRole);
            if (index >= 0) {
                legacyResourceIdentities[
                    static_cast<std::size_t>(index)
                ]
                    .clear();
            }
        }
        ExactObjectIdentity identity;
        identity.capture(object);
        const std::string objectName =
            object->getNameInDocument();
        doc->removeObject(objectName.c_str());
        if (identity.resolve(doc)) {
            throw Base::RuntimeError(
                "A section-cut implementation resource could not be "
                "deleted"
            );
        }
    };

    int compoundTransparency = -1;
    // lambda to store the compoundTransparency
    auto storeTransparency = [&](App::DocumentObject* cutObject) {
        auto CompoundVP = dynamic_cast<Gui::ViewProviderGeometryObject*>(
            Gui::Application::Instance->getViewProvider(cutObject)
        );
        if (CompoundVP && compoundTransparency == -1) {
            compoundTransparency = int(CompoundVP->Transparency.getValue());
        }
    };

    // delete the objects we might have already created to cut
    // we must do this because we support several cuts at once and
    // it is dangerous to deal with the fact that the user is free
    // to uncheck cutting planes and to add/remove objects while this dialog is open
    // We must remove in this order because the tree hierary of the features is Z->Y->X and Cut->Box
    if (auto* cut = findObject(CutZName)) {
        // the topmost cut transparency determines the overall transparency
        storeTransparency(cut);
        deleteObject(CutZName);
    }
    if (findObject(BoxZName)) {
        deleteObject(BoxZName);
    }
    if (auto* cut = findObject(CutYName)) {
        storeTransparency(cut);
        deleteObject(CutYName);
    }
    if (findObject(BoxYName)) {
        deleteObject(BoxYName);
    }
    if (auto* cut = findObject(CutXName)) {
        storeTransparency(cut);
        deleteObject(CutXName);
    }
    if (findObject(BoxXName)) {
        deleteObject(BoxXName);
    }
}

void SectionCut::deleteCompound()
{
    if (auto* compoundObject = findObject(CompoundName)) {
        // to later store the childs
        std::vector<App::DocumentObject*> compoundChilds =
            semanticLinkResources();

        // check if this is a BooleanFragments or a Part::Compound
        auto pcCompoundDelPart = dynamic_cast<Part::Compound*>(compoundObject);
        Gui::ViewProvider* pcCompoundDelBF {};
        if (compoundChilds.empty() && !pcCompoundDelPart) {
            // check for BooleanFragments
            pcCompoundDelBF = Gui::Application::Instance->getViewProvider(compoundObject);
            if (!pcCompoundDelBF) {
                Base::Console().error(
                    "Section cut error: compound is incorrectly named, cannot proceed\n"
                );
                return;
            }
            compoundChilds = pcCompoundDelBF->claimChildren();
        }
        else if (compoundChilds.empty()) {
            pcCompoundDelPart->Links.getLinks(compoundChilds);
        }

        // first delete the compound
        auto foundObj = std::find_if(
            ObjectsListVisible.begin(),
            ObjectsListVisible.end(),
            [compoundObject](const App::DocumentObjectT& obj) {
                return obj.getObject() == compoundObject;
            }
        );
        if (foundObj != ObjectsListVisible.end()) {
            ObjectsListVisible.erase(foundObj);
        }
        std::vector<ExactObjectIdentity> childIdentities;
        childIdentities.reserve(compoundChilds.size());
        for (auto* child : compoundChilds) {
            ExactObjectIdentity identity;
            identity.capture(child);
            childIdentities.push_back(std::move(identity));
        }
        if (auto* owner = findSemanticOwner()) {
            auto* compoundProperty =
                dynamic_cast<App::PropertyLinkHidden*>(
                    owner->getPropertyByName(
                        SectionResourceDescriptors[0].propertyName
                    )
                );
            auto* linksProperty =
                dynamic_cast<App::PropertyLinkListHidden*>(
                    owner->getPropertyByName(
                        SectionCutLinksProperty
                    )
                );
            if (!compoundProperty || !linksProperty
                || compoundProperty->getValue()
                    != compoundObject) {
                throw Base::RuntimeError(
                    "The section cut refused to delete an unbound "
                    "compound graph"
                );
            }
            compoundProperty->setValue(nullptr);
            linksProperty->setValues({});
        }
        else {
            legacyResourceIdentities[0].clear();
            legacyLinkIdentities.clear();
        }
        const std::string compoundName =
            compoundObject->getNameInDocument();
        doc->removeObject(compoundName.c_str());
        // now delete the objects that have been part of the compound
        for (const auto& identity : childIdentities) {
            auto* child = identity.resolve(doc);
            if (!child) {
                continue;
            }
            auto foundObjInner = std::find_if(
                ObjectsListVisible.begin(),
                ObjectsListVisible.end(),
                [child](const App::DocumentObjectT& objInner) {
                    return objInner.getObject() == child;
                }
            );
            if (foundObjInner != ObjectsListVisible.end()) {
                ObjectsListVisible.erase((foundObjInner));
            }
            const std::string childName =
                child->getNameInDocument();
            doc->removeObject(childName.c_str());
        }
    }
}

void SectionCut::restoreVisibility()
{
    // make all objects visible that have been visible when the dialog was called
    // because we made them invisible when we created cuts
    for (auto& aVisObject : ObjectsListVisible) {
        if (aVisObject.getObject()) {
            // a formerly visible object might have been deleted
            aVisObject.getObject()->Visibility.setValue(true);
        }
        else {
            // we must refresh the ObjectsListVisible list
            // Disable this function call as modifying the container while iterating it
            // causes a crash.
            // onRefreshCutPBclicked();
        }
    }
}

Part::Box* SectionCut::createBox(const char* name, const Base::Vector3f& size)  // NOLINT
{
    // create a box
    auto pcBox = doc->addObject<Part::Box>(name);
    if (!pcBox) {
        throw Base::RuntimeError(
            std::string("Section cut error: ") + std::string(name)
            + std::string(" could not be added\n")
        );
    }

    // it appears that because of internal rounding errors, the bounding box is sometimes
    // a bit too small, for example for ellipsoids, thus make the box a bit larger
    pcBox->Length.setValue(size[0] + 1.0);
    pcBox->Width.setValue(size[1] + 1.0);
    pcBox->Height.setValue(size[2] + 1.0);
    setSemanticResource(name, pcBox);

    return pcBox;
}

Part::Box* SectionCut::tryCreateXBox(const Base::Vector3f& pos, const Base::Vector3f& size)  // NOLINT
{
    try {
        return createXBox(pos, size);
    }
    catch (const Base::Exception& e) {
        e.reportException();
        return nullptr;
    }
}

std::tuple<Part::Box*, Part::Cut*> SectionCut::tryCreateXBoxAndCut(
    const Base::Vector3f& pos,
    const Base::Vector3f& size
)
{
    auto pcBox = tryCreateXBox(pos, size);
    auto pcCut = tryCreateCut(CutXName);
    return {pcBox, pcCut};
}

std::tuple<Part::Box*, Part::Cut*> SectionCut::tryCreateYBoxAndCut(
    const Base::Vector3f& pos,
    const Base::Vector3f& size
)
{
    auto pcBox = tryCreateYBox(pos, size);
    auto pcCut = tryCreateCut(CutYName);
    return {pcBox, pcCut};
}

std::tuple<Part::Box*, Part::Cut*> SectionCut::tryCreateZBoxAndCut(
    const Base::Vector3f& pos,
    const Base::Vector3f& size
)
{
    auto pcBox = tryCreateZBox(pos, size);
    auto pcCut = tryCreateCut(CutZName);
    return {pcBox, pcCut};
}

Part::Box* SectionCut::createXBox(const Base::Vector3f& pos, const Base::Vector3f& size)  // NOLINT
{
    // create a box
    auto pcBox = createBox(BoxXName, size);
    // set the previous cut value because refreshCutRanges changed it
    // in case the there was previously no cut, nothing will actually be changed
    // the previous value might now be outside the current possible range, then reset it
    double CutPosX = ui->cutX->value();
    if (CutPosX >= ui->cutX->maximum()) {
        CutPosX = ui->cutX->maximum() - 0.1;  // short below the maximum
    }
    else if (CutPosX <= ui->cutX->minimum()) {
        CutPosX = ui->cutX->minimum() + 0.1;  // short above the minimum
    }
    // set the cut value
    ui->cutX->setValue(CutPosX);

    // we don't set the value to ui->cutX because this would refresh the cut
    // which we don't have yet, thus do this later
    // set the box position
    Base::Vector3d BoxOriginSet;
    if (!ui->flipX->isChecked()) {
        BoxOriginSet.x = CutPosX - (size[0] + 1.0);
    }
    else {
        // flipped
        BoxOriginSet.x = CutPosX;
    }
    // we made the box 1.0 larger that we can place it 0.5 below the bounding box
    BoxOriginSet.y = pos[1] - 0.5;
    BoxOriginSet.z = pos[2] - 0.5;
    Base::Placement placement;
    placement.setPosition(BoxOriginSet);
    // set box placement, color and transparency
    pcBox->Placement.setValue(placement);

    return pcBox;
}

Part::Box* SectionCut::tryCreateYBox(const Base::Vector3f& pos, const Base::Vector3f& size)  // NOLINT
{
    try {
        return createYBox(pos, size);
    }
    catch (const Base::Exception& e) {
        e.reportException();
        return nullptr;
    }
}

Part::Box* SectionCut::createYBox(const Base::Vector3f& pos, const Base::Vector3f& size)  // NOLINT
{
    auto pcBox = createBox(BoxYName, size);
    // reset previous cut value
    double CutPosY = ui->cutY->value();
    if (CutPosY >= ui->cutY->maximum()) {
        CutPosY = ui->cutY->maximum() - 0.1;  // short below the maximum
    }
    else if (CutPosY <= ui->cutY->minimum()) {
        CutPosY = ui->cutY->minimum() + 0.1;  // short above the minimum
    }

    // set the cut value
    ui->cutY->setValue(CutPosY);

    // set the box position
    Base::Vector3d BoxOriginSet;
    BoxOriginSet.x = pos[0] - 0.5;
    if (!ui->flipY->isChecked()) {
        BoxOriginSet.y = CutPosY - (size[1] + 1.0);
    }
    else {
        // flipped
        BoxOriginSet.y = CutPosY;
    }
    BoxOriginSet.z = pos[2] - 0.5;
    Base::Placement placement;
    placement.setPosition(BoxOriginSet);
    pcBox->Placement.setValue(placement);

    return pcBox;
}

Part::Box* SectionCut::tryCreateZBox(const Base::Vector3f& pos, const Base::Vector3f& size)  // NOLINT
{
    try {
        return createZBox(pos, size);
    }
    catch (const Base::Exception& e) {
        e.reportException();
        return nullptr;
    }
}

Part::Box* SectionCut::createZBox(const Base::Vector3f& pos, const Base::Vector3f& size)  // NOLINT
{
    auto pcBox = createBox(BoxZName, size);
    // reset previous cut value
    double CutPosZ = ui->cutZ->value();
    if (CutPosZ >= ui->cutZ->maximum()) {
        CutPosZ = ui->cutZ->maximum() - 0.1;  // short below the maximum
    }
    else if (CutPosZ <= ui->cutZ->minimum()) {
        CutPosZ = ui->cutZ->minimum() + 0.1;  // short above the minimum
    }

    // set the cut value
    ui->cutZ->setValue(CutPosZ);

    // set the box position
    Base::Vector3d BoxOriginSet;
    BoxOriginSet.x = pos[0] - 0.5;
    BoxOriginSet.y = pos[1] - 0.5;
    if (!ui->flipY->isChecked()) {
        BoxOriginSet.z = CutPosZ - (size[2] + 1.0);
    }
    else {
        // flipped
        BoxOriginSet.z = CutPosZ;
    }
    Base::Placement placement;
    placement.setPosition(BoxOriginSet);
    pcBox->Placement.setValue(placement);

    return pcBox;
}

Part::Cut* SectionCut::createCut(const char* name)
{
    auto pcCut = doc->addObject<Part::Cut>(name);
    if (!pcCut) {
        throw Base::RuntimeError(
            std::string("Section cut error: ") + std::string(name)
            + std::string(" could not be added\n")
        );
    }
    setSemanticResource(name, pcCut);

    return pcCut;
}

Part::Cut* SectionCut::tryCreateCut(const char* name)
{
    try {
        return createCut(name);
    }
    catch (const Base::Exception& e) {
        e.reportException();
        return nullptr;
    }
}

void SectionCut::startCutting(bool isInitial)
{
    // there might be no document
    if (!Gui::Application::Instance->activeDocument()) {
        noDocumentActions();
        return;
    }
    // the document might have been changed
    if (doc != Gui::Application::Instance->activeDocument()->getDocument()) {
        throw Base::RuntimeError(
            "Section cut error: the active document changed"
        );
    }

    deleteObejcts();

    deleteCompound();

    restoreVisibility();

    // we enable the sliders because for assemblies we disabled them
    setSlidersEnabled(true);

    startObjectCutting(isInitial);
}

bool SectionCut::findObjects(std::vector<App::DocumentObject*>& objects)
{
    bool isLinkAssembly = false;
    for (auto& aVisObject : ObjectsListVisible) {
        App::DocumentObject* object = aVisObject.getObject();
        if (!object) {
            continue;
        }
        // we need all Link objects in App::Part for example for Assembly 4
        if (auto pcPart = dynamic_cast<App::Part*>(object)) {
            // collect all its link objects
            auto groupObjects = pcPart->Group.getValue();
            for (auto aGroupObject : groupObjects) {
                if (aGroupObject->getTypeId() == Base::Type::fromName("App::Link")) {
                    objects.push_back(aGroupObject);
                    // we assume that App::Links inside a App::Part are an assembly
                    isLinkAssembly = true;
                }
            }
        }
        // get all shapes that are also Part::Features
        if (object->isDerivedFrom<Part::BodyBase>()
            && object->getPropertyByName("Shape")) {
            objects.push_back(object);
        }
        else if (object->getPropertyByName("Shape") != nullptr
                 && object->isDerivedFrom<Part::Feature>()) {
            // sort out 2D objects, datums, App:Parts, compounds and objects that are
            // part of a PartDesign body
            if (!object->isDerivedFrom<Part::Part2DObject>() && !object->isDerivedFrom<Part::Datum>()
                && !object->isDerivedFrom(Base::Type::fromName("PartDesign::Feature"))
                && !object->isDerivedFrom<Part::Compound>()
                && object->getTypeId() != Base::Type::fromName("App::Part")) {
                objects.push_back(object);
            }
        }
        // get Links that are derived from Part objects
        if (auto pcLink = dynamic_cast<App::Link*>(object)) {
            auto* linkedObject = pcLink->getLink();
            if (linkedObject != nullptr && linkedObject->isDerivedFrom<Part::Feature>()) {
                objects.push_back(object);
            }
        }
    }

    return isLinkAssembly;
}

void SectionCut::filterObjects(std::vector<App::DocumentObject*>& objects)
{
    // sort out objects that are part of Part::Boolean, Part::MultiCommon, Part::MultiFuse,
    // Part::Thickness and Part::FilletBase
    // check list of visible objects and not cut list because we want to remove from the cut list
    for (auto& aVisObject : ObjectsListVisible) {
        App::DocumentObject* object = aVisObject.getObject();
        if (!object) {
            continue;
        }
        if (object->isDerivedFrom<Part::Boolean>() || object->isDerivedFrom<Part::MultiCommon>()
            || object->isDerivedFrom<Part::MultiFuse>() || object->isDerivedFrom<Part::Thickness>()
            || object->isDerivedFrom<Part::FilletBase>()) {
            // get possible links
            auto subObjectList = object->getOutList();
            // if there are links, delete them
            if (!subObjectList.empty()) {
                for (auto aSubObj : subObjectList) {
                    for (auto itCutObj = objects.begin(); itCutObj != objects.end(); ++itCutObj) {
                        if (aSubObj == *itCutObj) {
                            objects.erase(itCutObj);
                            break;
                        }
                    }
                }
            }
        }
    }
}

void SectionCut::throwMissingObjectsError(bool isInitial)
{
    // block signals to be able to reset the cut group boxes without calling startCutting again
    setGroupsDisabled();

    if (isInitial) {
        throw Base::RuntimeError("There are no visible objects to be cut");
    }

    throw Base::RuntimeError("There are no objects in the document that can be cut");
}

bool SectionCut::isCuttingEnabled() const
{
    return ui->groupBoxX->isChecked() || ui->groupBoxY->isChecked() || ui->groupBoxZ->isChecked();
}

namespace
{
Base::Color getFirstColor(const std::vector<App::DocumentObject*>& objects)
{
    Base::Color cutColor;
    auto vpFirstObject = dynamic_cast<Gui::ViewProviderGeometryObject*>(
        Gui::Application::Instance->getViewProvider(objects.front())
    );
    if (vpFirstObject) {
        cutColor = vpFirstObject->ShapeAppearance.getDiffuseColor();
    }
    return cutColor;
}

long getFirstTransparency(const std::vector<App::DocumentObject*>& objects)
{
    long cutTransparency {0};
    auto vpFirstObject = dynamic_cast<Gui::ViewProviderGeometryObject*>(
        Gui::Application::Instance->getViewProvider(objects.front())
    );
    if (vpFirstObject) {
        cutTransparency = vpFirstObject->Transparency.getValue();
    }
    return cutTransparency;
}

bool isAutoColor(const Base::Color& color, const std::vector<App::DocumentObject*>& objects)
{
    bool autoColor = true;
    for (auto itCuts : objects) {
        auto vpObject = dynamic_cast<Gui::ViewProviderGeometryObject*>(
            Gui::Application::Instance->getViewProvider(itCuts)
        );
        if (vpObject) {
            if (color != vpObject->ShapeAppearance.getDiffuseColor()) {
                autoColor = false;
                break;
            }
        }
    }

    return autoColor;
}

bool isAutoTransparency(long transparency, const std::vector<App::DocumentObject*>& objects)
{
    bool autoTransparency = true;
    for (auto itCuts : objects) {
        auto vpObject = dynamic_cast<Gui::ViewProviderGeometryObject*>(
            Gui::Application::Instance->getViewProvider(itCuts)
        );
        if (vpObject) {
            if (transparency != vpObject->Transparency.getValue()) {
                autoTransparency = false;
                break;
            }
        }
    }

    return autoTransparency;
}

std::vector<App::DocumentObject*> createLinks(
    App::Document* doc,
    const std::vector<App::DocumentObject*>& objects
)
{
    std::vector<App::DocumentObject*> links;
    for (auto itCuts : objects) {
        // first create a link with a unique name
        std::string newName;
        // since links to normal Part objects all have the document name "Link",
        // use their label text instead
        if (itCuts->getTypeId() == Base::Type::fromName("App::Link")) {
            newName = itCuts->Label.getValue();
        }
        else {
            newName = itCuts->getNameInDocument();
        }
        newName += "_CutLink";

        auto pcLink = doc->addObject<App::Link>(newName.c_str());
        if (!pcLink) {
            throw Base::RuntimeError("'App::Link' could not be added");
        }

        // set the object to the created empty link object
        pcLink->LinkedObject.setValue(itCuts);
        // we want to get the link at the same position as the original
        pcLink->LinkTransform.setValue(true);

        // add link to list to later add this to the compound object
        links.push_back(pcLink);

        // if the object is part of an App::Part container,
        // the link needs to get the container placement
        if (auto parents = itCuts->getInList(); !parents.empty()) {
            for (auto parent : parents) {
                if (auto pcPartParent = dynamic_cast<App::Part*>(parent)) {
                    if (auto placement
                        = pcPartParent->getPropertyByName<App::PropertyPlacement>("Placement")) {
                        pcLink->Placement.setValue(placement->getValue());
                    }
                }
            }
        }

    }
    return links;
}

double getMinOrMax(QPushButton* button, QDoubleSpinBox* spinBox)
{
    return button->isChecked() ? spinBox->maximum() : spinBox->minimum();
}

void setMinOrMax(double value, QPushButton* button, QDoubleSpinBox* spinBox)
{
    if (button->isChecked()) {
        if (value < spinBox->maximum()) {
            spinBox->setMaximum(value);
        }
    }
    else {
        if (value > spinBox->minimum()) {
            spinBox->setMinimum(value);
        }
    }
}

}  // namespace

void SectionCut::setObjectsVisible(bool value)
{
    for (auto& aVisObject : ObjectsListVisible) {
        App::DocumentObject* object = aVisObject.getObject();
        if (object) {
            object->Visibility.setValue(value);
        }
    }
}

int SectionCut::getCompoundTransparency() const
{
    // if there was no compound, take the setting for the cut face
    int compoundTransparency = -1;
    if (ui->groupBoxIntersecting->isChecked()) {
        compoundTransparency = ui->BFragTransparencyHS->value();
    }
    if (compoundTransparency == -1) {
        compoundTransparency = ui->CutTransparencyHS->value();
    }

    return compoundTransparency;
}

void SectionCut::startObjectCutting(bool isInitial)
{
    // ObjectsListVisible contains all visible objects of the document, but we can only cut
    // those that have a solid shape
    std::vector<App::DocumentObject*> ObjectsListCut;
    bool isLinkAssembly = findObjects(ObjectsListCut);

    if (isLinkAssembly) {
        // we disable the sliders because for assemblies it will takes ages to do several dozen
        // recomputes
        setSlidersEnabled(false);
        setSlidersToolTip(tr("Sliders are disabled for assemblies"));
    }

    filterObjects(ObjectsListCut);

    // we might have no objects that can be cut
    if (ObjectsListCut.empty()) {
        throwMissingObjectsError(isInitial);
    }

    // disable intersection option because BooleanFragments requires at least 2 objects
    ui->groupBoxIntersecting->setEnabled(ObjectsListCut.size() > 1);

    // we cut this way:
    // 1. put all existing objects into a either a Part::Compound or a BooleanFragments object
    // 2. create a box with the size of the bounding box
    // 3. cut the box from the compound

    // depending on how many cuts should be performed, we need as many boxes
    // if nothing is yet to be cut, we can return
    if (!isCuttingEnabled()) {
        // there is no active cut, so we can enable refresh button
        ui->RefreshCutPB->setEnabled(true);
        return;
    }

    // disable refresh button
    ui->RefreshCutPB->setEnabled(false);

    createAllObjects(ObjectsListCut);
}

std::tuple<Base::Vector3f, Base::Vector3f> SectionCut::adjustRanges()
{
    // the area in which we can cut is the size of the compound
    // we get its size by its bounding box
    SbBox3f CompoundBoundingBox = getViewBoundingBox();
    if (CompoundBoundingBox.isEmpty()) {  // NOLINT
        throw Base::RuntimeError("Section cut error: the CompoundBoundingBox is empty");
    }

    // refresh all cut limits according to the new bounding box
    refreshCutRanges(
        CompoundBoundingBox,
        Refresh::notXValue,
        Refresh::notYValue,
        Refresh::notZValue,
        Refresh::XRange,
        Refresh::YRange,
        Refresh::ZRange
    );

    // prepare the cut box size according to the bounding box size
    Base::Vector3f BoundingBoxSize;
    CompoundBoundingBox.getSize(BoundingBoxSize[0], BoundingBoxSize[1], BoundingBoxSize[2]);
    // get placement of the bounding box origin
    Base::Vector3f BoundingBoxOrigin;
    CompoundBoundingBox.getOrigin(BoundingBoxOrigin[0], BoundingBoxOrigin[1], BoundingBoxOrigin[2]);

    return {BoundingBoxSize, BoundingBoxOrigin};
}

void SectionCut::adjustYRange()
{
    auto CutBoundingBox = getViewBoundingBox();
    // refresh the Y cut limits according to the new bounding box
    refreshCutRanges(
        CutBoundingBox,
        Refresh::notXValue,
        Refresh::notYValue,
        Refresh::notZValue,
        Refresh::notXRange,
        Refresh::YRange,
        Refresh::notZRange
    );
}

void SectionCut::adjustZRange()
{
    auto CutBoundingBox = getViewBoundingBox();
    refreshCutRanges(
        CutBoundingBox,
        Refresh::notXValue,
        Refresh::notYValue,
        Refresh::notZValue,
        Refresh::notXRange,
        Refresh::notYRange,
        Refresh::ZRange
    );
}

void SectionCut::resetHasBoxes()
{
    hasBoxX = false;
    hasBoxY = false;
    hasBoxZ = false;
    hasBoxCustom = false;
}

App::DocumentObject* SectionCut::getCutXBase(
    size_t num,
    App::DocumentObject* comp,
    App::DocumentObject* frag
) const
{
    if (num == 1 || !(ui->groupBoxIntersecting->isChecked())) {
        return comp;
    }

    return frag;
}

App::DocumentObject* SectionCut::getCutYBase(
    size_t num,
    App::DocumentObject* comp,
    App::DocumentObject* frag
) const
{
    // if there is already a cut, we must take it as feature to be cut
    if (hasBoxX) {
        return findObject(CutXName);
    }

    if (num == 1 || !(ui->groupBoxIntersecting->isChecked())) {
        return comp;
    }

    return frag;
}

App::DocumentObject* SectionCut::getCutZBase(
    size_t num,
    App::DocumentObject* comp,
    App::DocumentObject* frag
) const
{
    if (hasBoxY) {
        return findObject(CutYName);
    }

    if (hasBoxX && !hasBoxY) {
        return findObject(CutXName);
    }

    if (num == 1 || !(ui->groupBoxIntersecting->isChecked())) {
        return comp;
    }

    return frag;
}

void SectionCut::processXBoxAndCut(const Args& args)
{
    // create a box
    auto [pcBox, pcCut] = tryCreateXBoxAndCut(args.origin, args.size);
    if (!pcBox || !pcCut) {
        return;
    }

    // set box color and transparency
    args.boxFunc(pcBox);

    pcCut->Base.setValue(getCutXBase(args.numObjects, args.partCompound, args.boolFragment));
    pcCut->Tool.setValue(pcBox);
    // we must set the compoundTransparency also for the cut
    args.cutFunc(pcCut);

    // recomputing recursively is especially for assemblies very time-consuming
    // however there must be a final recursicve recompute and we do this at the end
    // so only recomute recursively if there are no other cuts
    pcCut->recomputeFeature(!ui->groupBoxY->isChecked() && !ui->groupBoxZ->isChecked());
    hasBoxX = true;
}

void SectionCut::processYBoxAndCut(const Args& args)
{
    auto [pcBox, pcCut] = tryCreateYBoxAndCut(args.origin, args.size);
    if (!pcBox || !pcCut) {
        return;
    }

    args.boxFunc(pcBox);

    // if there is already a cut, we must take it as feature to be cut
    pcCut->Base.setValue(getCutYBase(args.numObjects, args.partCompound, args.boolFragment));
    pcCut->Tool.setValue(pcBox);
    args.cutFunc(pcCut);

    pcCut->recomputeFeature(!ui->groupBoxZ->isChecked());
    hasBoxY = true;
}

void SectionCut::processZBoxAndCut(const Args& args)
{
    auto [pcBox, pcCut] = tryCreateZBoxAndCut(args.origin, args.size);
    if (!pcBox || !pcCut) {
        return;
    }

    args.boxFunc(pcBox);

    pcCut->Base.setValue(getCutZBase(args.numObjects, args.partCompound, args.boolFragment));
    pcCut->Tool.setValue(pcBox);
    args.cutFunc(pcCut);

    pcCut->recomputeFeature(true);
    hasBoxZ = true;
}

void SectionCut::createAllObjects(const std::vector<App::DocumentObject*>& ObjectsListCut)
{
    // store color and transparency of first object
    Base::Color cutColor = getFirstColor(ObjectsListCut);
    long cutTransparency = getFirstTransparency(ObjectsListCut);
    bool autoColor = true;
    bool autoTransparency = true;

    // check if all objects have same color and transparency
    if (ui->autoCutfaceColorCB->isChecked() || ui->autoBFColorCB->isChecked()) {
        autoColor = isAutoColor(cutColor, ObjectsListCut);
        autoTransparency = isAutoTransparency(cutTransparency, ObjectsListCut);
    }

    // create link objects for all found elements
    std::vector<App::DocumentObject*> ObjectsListLinks;
    ObjectsListLinks = createLinks(doc, ObjectsListCut);
    setSemanticLinkResources(ObjectsListLinks);

    App::DocumentObject* CutCompoundBF = nullptr;
    Part::Compound* CutCompoundPart = nullptr;

    // specify transparency for the compound
    int compoundTransparency = getCompoundTransparency();

    // create BooleanFragments and fill it
    if (ui->groupBoxIntersecting->isChecked() && ObjectsListCut.size() > 1) {
        CutCompoundBF = createBooleanFragments(ObjectsListLinks, compoundTransparency);
    }
    else {  // create Part::Compound and fill it
        // if there is only one object to be cut, we cannot create a BooleanFragments object
        CutCompoundPart = createCompound(ObjectsListLinks, compoundTransparency);
    }

    auto [BoundingBoxSize, BoundingBoxOrigin] = adjustRanges();

    // now we can create the cut boxes
    resetHasBoxes();

    // if automatic, we take this color for the cut
    if (autoColor) {
        setAutoColor(cutColor.asValue<QColor>());
    }
    if (autoTransparency) {
        setAutoTransparency(int(cutTransparency));
    }

    // read cutface color for the cut box
    Base::Color boxColor;
    boxColor.setValue<QColor>(ui->CutColor->color());
    int boxTransparency = ui->CutTransparencyHS->value();

    // lambda function to set shape color and transparency
    auto setColorTransparency = [&](Part::Box* pcBox) {
        auto vpBox = dynamic_cast<Gui::ViewProviderGeometryObject*>(
            Gui::Application::Instance->getViewProvider(pcBox)
        );
        if (vpBox) {
            vpBox->ShapeAppearance.setDiffuseColor(boxColor);
            vpBox->Transparency.setValue(boxTransparency);
        }
    };

    // lambda function to set transparency
    auto setTransparency = [&](Part::Cut* pcCut) {
        auto vpCut = dynamic_cast<Gui::ViewProviderGeometryObject*>(
            Gui::Application::Instance->getViewProvider(pcCut)
        );
        if (vpCut) {
            vpCut->Transparency.setValue(compoundTransparency);
        }
    };

    if (ui->groupBoxX->isChecked()) {
        processXBoxAndCut(
            {BoundingBoxOrigin,
             BoundingBoxSize,
             ObjectsListCut.size(),
             CutCompoundPart,
             CutCompoundBF,
             setColorTransparency,
             setTransparency}
        );
    }
    if (ui->groupBoxY->isChecked()) {
        // if there is a X cut, its size defines the possible range for the Y cut
        // the cut box size is not affected, it can be as large as the compound
        if (hasBoxX) {
            adjustYRange();
        }

        processYBoxAndCut(
            {BoundingBoxOrigin,
             BoundingBoxSize,
             ObjectsListCut.size(),
             CutCompoundPart,
             CutCompoundBF,
             setColorTransparency,
             setTransparency}
        );
    }
    if (ui->groupBoxZ->isChecked()) {
        if (hasBoxX || hasBoxY) {
            adjustZRange();
        }

        processZBoxAndCut(
            {BoundingBoxOrigin,
             BoundingBoxSize,
             ObjectsListCut.size(),
             CutCompoundPart,
             CutCompoundBF,
             setColorTransparency,
             setTransparency}
        );
    }
}

SectionCut* SectionCut::makeDockWidget(QWidget* parent)
{
    // embed this dialog into a QDockWidget
    auto sectionCut = new SectionCut(parent);
    Gui::DockWindowManager* pDockMgr = Gui::DockWindowManager::instance();
    // the dialog is designed that you can see the tree, thus put it to the right side
    QDockWidget* dw = pDockMgr->addDockWindow("Section cutting", sectionCut, Qt::RightDockWidgetArea);
    dw->setFeatures(QDockWidget::DockWidgetMovable | QDockWidget::DockWidgetFloatable);
    // dw->setFloating(true);
    dw->show();

    return sectionCut;
}

void SectionCut::refreshForActiveDocument()
{
    onRefreshCutPBclicked();
}

bool SectionCut::finalizeClose()
{
    if (closeFinalized) {
        return true;
    }
    if (!ownsLiveDocument()) {
        closeFinalized = true;
        return true;
    }

    const bool showCut =
        ui->keepOnlyCutCB->isChecked() && findSemanticOwner();
    if (!runAtomicChange(
            "Finish persistent section cut",
            [this, showCut]() {
                setAcceptedVisibility(showCut);
            },
            false
        )) {
        return false;
    }
    closeFinalized = true;
    return true;
}

/** Destroys the object and frees any allocated resources */
SectionCut::~SectionCut()
{
    if (closeFinalized || !ownsLiveDocument()) {
        return;
    }
    if (!finalizeClose()) {
        Base::Console().warning(
            "Persistent section-cut visibility could not be finalized while "
            "its editor was being destroyed\n"
        );
    }
}

void SectionCut::reject()
{
    if (!finalizeClose()) {
        return;
    }
    QDialog::reject();
    auto dw = qobject_cast<QDockWidget*>(parent());
    if (dw) {
        dw->deleteLater();
    }
}

void SectionCut::onGroupBoxXtoggled()
{
    runAtomicChange(
        "Edit persistent section cut",
        [this]() {
            startCutting();
        }
    );
}

void SectionCut::onGroupBoxYtoggled()
{
    runAtomicChange(
        "Edit persistent section cut",
        [this]() {
            startCutting();
        }
    );
}

void SectionCut::onGroupBoxZtoggled()
{
    runAtomicChange(
        "Edit persistent section cut",
        [this]() {
            startCutting();
        }
    );
}

// helper function for the onFlip_clicked signal
void SectionCut::CutValueHelper(double value, QDoubleSpinBox* SpinBox, QSlider* Slider)
{
    // there might be no document
    if (!Gui::Application::Instance->activeDocument()) {
        noDocumentActions();
        return;
    }
    // refresh objects and return in case the document was changed
    if (doc != Gui::Application::Instance->activeDocument()->getDocument()) {
        onRefreshCutPBclicked();
        return;
    }
    // update slider position and tooltip
    // the slider value is % of the cut range
    if (Slider->isEnabled()) {
        Slider->blockSignals(true);
        Slider->setValue(
            int((value - SpinBox->minimum()) / (SpinBox->maximum() - SpinBox->minimum()) * 100.0)
        );
        Slider->setToolTip(QString::number(value, 'g', Base::UnitsApi::getDecimals()));
        Slider->blockSignals(false);
    }

    // we cannot cut to the edge because then the result is an empty shape
    // we chose purposely not to simply set the range for cutX previously
    // because everything is allowed just not the min/max
    if (SpinBox->value() == SpinBox->maximum()) {
        SpinBox->setValue(SpinBox->maximum() - 0.1);
        return;
    }
    if (SpinBox->value() == SpinBox->minimum()) {
        SpinBox->setValue(SpinBox->minimum() + 0.1);
        return;
    }
}

double SectionCut::getPosX(Part::Box* box) const
{
    double value {};
    if (!ui->flipX->isChecked()) {
        value = ui->cutX->value() - box->Length.getValue();
    }
    else {
        // flipped
        value = ui->cutX->value();
    }

    return value;
}

double SectionCut::getPosY(Part::Box* box) const
{
    double value {};
    if (!ui->flipY->isChecked()) {
        value = ui->cutY->value() - box->Width.getValue();
    }
    else {
        // flipped
        value = ui->cutY->value();
    }

    return value;
}

double SectionCut::getPosZ(Part::Box* box) const
{
    double value {};
    if (!ui->flipZ->isChecked()) {
        value = ui->cutZ->value() - box->Height.getValue();
    }
    else {
        // flipped
        value = ui->cutZ->value();
    }

    return value;
}

void SectionCut::adjustYZRanges(SbBox3f CutBoundingBox)
{
    if (hasBoxY) {
        refreshCutRanges(
            CutBoundingBox,
            Refresh::notXValue,
            Refresh::notYValue,
            Refresh::notZValue,
            Refresh::notXRange,
            Refresh::YRange,
            Refresh::ZRange
        );
        // the value of Y or Z can now be outside or at the limit, in this case reset the value too
        if ((ui->cutY->value() >= ui->cutY->maximum()) || (ui->cutY->value() <= ui->cutY->minimum())) {
            refreshCutRanges(
                CutBoundingBox,
                Refresh::notXValue,
                Refresh::YValue,
                Refresh::notZValue,
                Refresh::notXRange,
                Refresh::YRange,
                Refresh::ZRange
            );
        }
        if ((ui->cutZ->value() >= ui->cutZ->maximum()) || (ui->cutZ->value() <= ui->cutZ->minimum())) {
            refreshCutRanges(
                CutBoundingBox,
                Refresh::notXValue,
                Refresh::notYValue,
                Refresh::ZValue,
                Refresh::notXRange,
                Refresh::YRange,
                Refresh::ZRange
            );
        }
    }
    else {
        // there is no Y cut yet so we can set the Y value too
        refreshCutRanges(
            CutBoundingBox,
            Refresh::notXValue,
            Refresh::YValue,
            Refresh::notZValue,
            Refresh::notXRange,
            Refresh::YRange,
            Refresh::ZRange
        );
        // the value of Z can now be outside or at the limit, in this case reset the value too
        if ((ui->cutZ->value() >= ui->cutZ->maximum()) || (ui->cutZ->value() <= ui->cutZ->minimum())) {
            refreshCutRanges(
                CutBoundingBox,
                Refresh::notXValue,
                Refresh::YValue,
                Refresh::ZValue,
                Refresh::notXRange,
                Refresh::YRange,
                Refresh::ZRange
            );
        }
    }
}

void SectionCut::onCutXvalueChanged(double val)
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Move persistent section cut",
            [this, val]() {
                onCutXvalueChanged(val);
            }
        );
        return;
    }
    CutValueHelper(val, ui->cutX, ui->cutXHS);

    // get the cut box
    auto CutBox = findObject(BoxXName);
    // when the value has been set after resetting the compound bounding box
    // there is not yet a cut and we do nothing
    if (!CutBox) {
        return;
    }
    auto pcBox = dynamic_cast<Part::Box*>(CutBox);
    if (!pcBox) {
        Base::Console().error((std::string("Section cut error: ") + std::string(BoxXName)
                               + std::string(" is no Part::Box object. Cannot proceed.\n"))
                                  .c_str());
        return;
    }
    // get its placement and size
    Base::Placement placement = pcBox->Placement.getValue();
    Base::Vector3d BoxPosition = placement.getPosition();
    BoxPosition.x = getPosX(pcBox);

    placement.setPosition(BoxPosition);
    pcBox->Placement.setValue(placement);

    auto CutObject = findOrCreateObject(CutXName);
    // there should be a box, but maybe the user deleted it meanwhile
    if (!CutObject) {
        return;
    }

    // if there is another cut, we must recalculate it too
    // we might have cut so that the range for Y and Z is now smaller
    // the hierarchy is always Z->Y->X
    if (hasBoxY && !hasBoxZ) {  // only Y
        auto CutFeatureY = findOrCreateObject(CutYName);
        if (!CutFeatureY) {
            return;
        }
        // refresh the Y and Z cut limits according to the new bounding box of the cut result
        // make the SectionCutY invisible
        CutFeatureY->Visibility.setValue(false);
        // make SectionCutX visible
        CutObject->Visibility.setValue(true);
        // get new bounding box
        auto CutBoundingBox = getViewBoundingBox();
        // refresh Y limits and Z limits + Z value
        refreshCutRanges(
            CutBoundingBox,
            Refresh::notXValue,
            Refresh::notYValue,
            Refresh::ZValue,
            Refresh::notXRange,
            Refresh::YRange,
            Refresh::ZRange
        );
        // the value of Y can now be outside or at the limit, in this case reset the value too
        if ((ui->cutY->value() >= ui->cutY->maximum()) || (ui->cutY->value() <= ui->cutY->minimum())) {
            refreshCutRanges(
                CutBoundingBox,
                Refresh::notXValue,
                Refresh::YValue,
                Refresh::ZValue,
                Refresh::notXRange,
                Refresh::YRange,
                Refresh::ZRange
            );
        }
        // make the SectionCutY visible again
        CutFeatureY->Visibility.setValue(true);
        // make SectionCutX invisible again
        CutObject->Visibility.setValue(false);
        // recompute the cut
        CutFeatureY->recomputeFeature(true);
    }
    else if (hasBoxZ) {  // at least Z
        // the main cut is Z, no matter if there is a cut in Y
        auto CutFeatureZ = findOrCreateObject(CutZName);
        if (!CutFeatureZ) {
            return;
        }
        // refresh the Y and Z cut limits according to the new bounding box of the cut result
        // make the SectionCutZ invisible
        CutFeatureZ->Visibility.setValue(false);
        // make SectionCutX visible
        CutObject->Visibility.setValue(true);
        // refresh Y and Z limits
        adjustYZRanges(getViewBoundingBox());
        // make the SectionCutZ visible again
        CutFeatureZ->Visibility.setValue(true);
        // make SectionCutX invisible again
        CutObject->Visibility.setValue(false);
        // recompute the cut
        CutFeatureZ->recomputeFeature(true);
    }
    else {  // just X
        // refresh Y and Z limits + values
        auto CutBoundingBox = getViewBoundingBox();
        refreshCutRanges(
            CutBoundingBox,
            Refresh::notXValue,
            Refresh::YValue,
            Refresh::ZValue,
            Refresh::notXRange,
            Refresh::YRange,
            Refresh::ZRange
        );
        // recompute the cut
        auto pcCut = dynamic_cast<Part::Cut*>(CutObject);
        if (!pcCut) {
            Base::Console().error((std::string("Section cut error: ") + std::string(CutZName)
                                   + std::string(" is no Part::Cut object. Cannot proceed.\n"))
                                      .c_str());
            return;
        }
        pcCut->recomputeFeature(true);
    }
}

void SectionCut::onCutXHSsliderMoved(int val)
{
    // we cannot cut to the edge because then the result is an empty shape
    // we chose purposely not to simply set the range for cutXHS previously
    // because everything is allowed just not the min/max
    // we set it one slider step below the min/max
    if (val == ui->cutXHS->maximum()) {
        ui->cutXHS->setValue(ui->cutXHS->maximum() - ui->cutXHS->singleStep());
        return;
    }
    if (val == ui->cutXHS->minimum()) {
        ui->cutXHS->setValue(ui->cutXHS->minimum() + ui->cutXHS->singleStep());
        return;
    }
    // the slider value is % of the cut range
    double NewCutValue = ui->cutX->minimum()
        + val / 100.0 * (ui->cutX->maximum() - ui->cutX->minimum());
    ui->cutXHS->setToolTip(QString::number(NewCutValue, 'g', Base::UnitsApi::getDecimals()));
    ui->cutX->setValue(NewCutValue);
}

void SectionCut::onCutXHSChanged(int val)
{
    onCutXHSsliderMoved(val);
}

void SectionCut::onCutYvalueChanged(double val)
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Move persistent section cut",
            [this, val]() {
                onCutYvalueChanged(val);
            }
        );
        return;
    }
    CutValueHelper(val, ui->cutY, ui->cutYHS);

    auto CutBox = findObject(BoxYName);
    if (!CutBox) {
        return;
    }
    auto pcBox = dynamic_cast<Part::Box*>(CutBox);
    if (!pcBox) {
        Base::Console().error((std::string("Section cut error: ") + std::string(BoxYName)
                               + std::string(" is no Part::Box object. Cannot proceed.\n"))
                                  .c_str());
        return;
    }
    Base::Placement placement = pcBox->Placement.getValue();
    Base::Vector3d BoxPosition = placement.getPosition();
    BoxPosition.y = getPosY(pcBox);

    placement.setPosition(BoxPosition);
    pcBox->Placement.setValue(placement);

    auto CutObject = findOrCreateObject(CutYName);
    if (!CutObject) {
        return;
    }

    // if there is another cut, we must recalculate it too
    // we might have cut so that the range for Z is now smaller
    // we only need to check for Z since the hierarchy is always Z->Y->X
    if (hasBoxZ) {
        auto CutFeatureZ = findObject(CutZName);
        if (!CutFeatureZ) {
            Base::Console().error((std::string("Section cut error: there is no ")
                                   + std::string(CutZName) + std::string("\n"))
                                      .c_str());
            return;
        }
        // refresh the Z cut limits according to the new bounding box of the cut result
        // make the SectionCutZ invisible
        CutFeatureZ->Visibility.setValue(false);
        // make SectionCutX visible
        CutObject->Visibility.setValue(true);
        // get new bounding box
        auto CutBoundingBox = getViewBoundingBox();
        // refresh Z limits
        refreshCutRanges(
            CutBoundingBox,
            Refresh::notXValue,
            Refresh::notYValue,
            Refresh::notZValue,
            Refresh::notXRange,
            Refresh::notYRange,
            Refresh::ZRange
        );
        // the value of Z can now be outside or at the limit, in this case reset the value too
        if ((ui->cutZ->value() >= ui->cutZ->maximum()) || (ui->cutZ->value() <= ui->cutZ->minimum())) {
            refreshCutRanges(
                CutBoundingBox,
                Refresh::notXValue,
                Refresh::notYValue,
                Refresh::ZValue,
                Refresh::notXRange,
                Refresh::notYRange,
                Refresh::ZRange
            );
        }
        // make the SectionCutZ visible again
        CutFeatureZ->Visibility.setValue(true);
        // make SectionCutX invisible again
        CutObject->Visibility.setValue(false);
        // recompute the cut
        CutFeatureZ->recomputeFeature(true);
    }
    else {  // just Y
        // refresh Z limits + values
        auto CutBoundingBox = getViewBoundingBox();
        refreshCutRanges(
            CutBoundingBox,
            Refresh::notXValue,
            Refresh::notYValue,
            Refresh::ZValue,
            Refresh::notXRange,
            Refresh::notYRange,
            Refresh::ZRange
        );
        // recompute the cut
        auto pcCut = dynamic_cast<Part::Cut*>(CutObject);
        if (!pcCut) {
            Base::Console().error((std::string("Section cut error: ") + std::string(CutZName)
                                   + std::string(" is no Part::Cut object. Cannot proceed.\n"))
                                      .c_str());
            return;
        }
        pcCut->recomputeFeature(true);
        // refresh X limits
        // this is done by
        // first making the cut X box visible, then setting the limits only for X
        // if x-limit in box direction is larger than object, reset value to saved limit
        if (hasBoxX) {
            auto CutBoxX = findObject(BoxXName);
            if (!CutBoxX) {
                return;
            }
            // first store the values
            double storedX = getMinOrMax(ui->flipX, ui->cutX);

            // show the cutting box
            CutBoxX->Visibility.setValue(true);
            // set new XRange
            auto CutBoundingBox = getViewBoundingBox();
            refreshCutRanges(
                CutBoundingBox,
                Refresh::notXValue,
                Refresh::notYValue,
                Refresh::notZValue,
                Refresh::XRange,
                Refresh::notYRange,
                Refresh::notZRange
            );
            // hide cutting box and compare resultwith stored value
            CutBoxX->Visibility.setValue(false);
            setMinOrMax(storedX, ui->flipX, ui->cutX);
        }
    }
}

void SectionCut::onCutYHSsliderMoved(int val)
{
    // we cannot cut to the edge because then the result is an empty shape
    if (val == ui->cutYHS->maximum()) {
        ui->cutYHS->setValue(ui->cutYHS->maximum() - ui->cutYHS->singleStep());
        return;
    }
    if (val == ui->cutYHS->minimum()) {
        ui->cutYHS->setValue(ui->cutYHS->minimum() + ui->cutYHS->singleStep());
        return;
    }
    // the slider value is % of the cut range
    double NewCutValue = ui->cutY->minimum()
        + val / 100.0 * (ui->cutY->maximum() - ui->cutY->minimum());
    ui->cutYHS->setToolTip(QString::number(NewCutValue, 'g', Base::UnitsApi::getDecimals()));
    ui->cutY->setValue(NewCutValue);
}

void SectionCut::onCutYHSChanged(int val)
{
    onCutYHSsliderMoved(val);
}

void SectionCut::onCutZvalueChanged(double val)
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Move persistent section cut",
            [this, val]() {
                onCutZvalueChanged(val);
            }
        );
        return;
    }
    CutValueHelper(val, ui->cutZ, ui->cutZHS);

    auto CutBox = findObject(BoxZName);
    if (!CutBox) {
        return;
    }
    auto pcBox = dynamic_cast<Part::Box*>(CutBox);
    if (!pcBox) {
        Base::Console().error((std::string("Section cut error: ") + std::string(BoxZName)
                               + std::string(" is no Part::Box object. Cannot proceed.\n"))
                                  .c_str());
        return;
    }
    Base::Placement placement = pcBox->Placement.getValue();
    Base::Vector3d BoxPosition = placement.getPosition();
    BoxPosition.z = getPosZ(pcBox);

    placement.setPosition(BoxPosition);
    pcBox->Placement.setValue(placement);

    auto CutObject = findOrCreateObject(CutZName);
    if (!CutObject) {
        return;
    }
    auto pcCut = dynamic_cast<Part::Cut*>(CutObject);
    if (!pcCut) {
        Base::Console().error((std::string("Section cut error: ") + std::string(CutZName)
                               + std::string(" is no Part::Cut object. Cannot proceed.\n"))
                                  .c_str());
        return;
    }
    pcCut->recomputeFeature(true);
    // refresh X and Y limits
    // this is done e.g. for X by
    // first making the cut X box visible, then setting the limits only for X
    // if x-limit in box direction is larger than object, reset value to saved limit
    SbBox3f CutBoundingBox;
    if (hasBoxX) {
        auto CutBoxX = findObject(BoxXName);
        if (!CutBoxX) {
            return;
        }

        // first store the values
        double storedX = getMinOrMax(ui->flipX, ui->cutX);

        // show the cutting box
        CutBoxX->Visibility.setValue(true);
        // set new XRange
        CutBoundingBox = getViewBoundingBox();
        refreshCutRanges(
            CutBoundingBox,
            Refresh::notXValue,
            Refresh::notYValue,
            Refresh::notZValue,
            Refresh::XRange,
            Refresh::notYRange,
            Refresh::notZRange
        );
        // hide cutting box and compare resultwith stored value
        CutBoxX->Visibility.setValue(false);

        setMinOrMax(storedX, ui->flipX, ui->cutX);
    }
    if (hasBoxY) {
        auto CutBoxY = findObject(BoxYName);
        if (!CutBoxY) {
            return;
        }
        double storedY = getMinOrMax(ui->flipY, ui->cutY);
        CutBoxY->Visibility.setValue(true);
        CutBoundingBox = getViewBoundingBox();
        refreshCutRanges(
            CutBoundingBox,
            Refresh::notXValue,
            Refresh::notYValue,
            Refresh::notZValue,
            Refresh::notXRange,
            Refresh::YRange,
            Refresh::notZRange
        );
        CutBoxY->Visibility.setValue(false);
        setMinOrMax(storedY, ui->flipY, ui->cutY);
    }
}

void SectionCut::onCutZHSsliderMoved(int val)
{
    // we cannot cut to the edge because then the result is an empty shape
    if (val == ui->cutZHS->maximum()) {
        ui->cutZHS->setValue(ui->cutZHS->maximum() - ui->cutZHS->singleStep());
        return;
    }
    if (val == ui->cutZHS->minimum()) {
        ui->cutZHS->setValue(ui->cutZHS->minimum() + ui->cutZHS->singleStep());
        return;
    }
    // the slider value is % of the cut range
    double NewCutValue = ui->cutZ->minimum()
        + val / 100.0 * (ui->cutZ->maximum() - ui->cutZ->minimum());
    ui->cutZHS->setToolTip(QString::number(NewCutValue, 'g', Base::UnitsApi::getDecimals()));
    ui->cutZ->setValue(NewCutValue);
}

void SectionCut::onCutZHSChanged(int val)
{
    onCutZHSsliderMoved(val);
}

// helper function for the onFlip_clicked signal
void SectionCut::FlipClickedHelper(const char* BoxName)
{
    // there might be no document
    if (!Gui::Application::Instance->activeDocument()) {
        noDocumentActions();
        return;
    }
    // refresh objects and return in case the document was changed
    if (doc != Gui::Application::Instance->activeDocument()->getDocument()) {
        onRefreshCutPBclicked();
        return;
    }
    // we must move the box e.g. in y-direction by its Width
    auto CutBox = findOrCreateObject(BoxName);
    // there should be a box, but maybe the user deleted it meanwhile
    if (!CutBox) {
        return;
    }
    auto pcBox = dynamic_cast<Part::Box*>(CutBox);
    if (!pcBox) {
        Base::Console().error((std::string("Section cut error: ") + std::string(BoxName)
                               + std::string(" is no Part::Box object. Cannot proceed.\n"))
                                  .c_str());
        return;
    }
    // get its placement and size
    Base::Placement placement = pcBox->Placement.getValue();
    Base::Vector3d BoxPosition = placement.getPosition();
    // flip the box
    switch (std::string(BoxName).back()) {
        case 'X':
            if (ui->flipX->isChecked()) {
                BoxPosition.x = BoxPosition.x + pcBox->Length.getValue();
            }
            else {
                BoxPosition.x = BoxPosition.x - pcBox->Length.getValue();
            }
            break;
        case 'Y':
            if (ui->flipY->isChecked()) {
                BoxPosition.y = BoxPosition.y + pcBox->Width.getValue();
            }
            else {
                BoxPosition.y = BoxPosition.y - pcBox->Width.getValue();
            }
            break;
        case 'Z':
            if (ui->flipZ->isChecked()) {
                BoxPosition.z = BoxPosition.z + pcBox->Height.getValue();
            }
            else {
                BoxPosition.z = BoxPosition.z - pcBox->Height.getValue();
            }
            break;
    }
    placement.setPosition(BoxPosition);
    pcBox->Placement.setValue(placement);
}

void SectionCut::onFlipXclicked()
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Flip persistent section cut",
            [this]() {
                onFlipXclicked();
            }
        );
        return;
    }
    FlipClickedHelper(BoxXName);

    if (auto CutObject = findOrCreateObject(CutXName)) {
        // if there is another cut, we must recalculate it too
        // the hierarchy is always Z->Y->X
        if (hasBoxY && !hasBoxZ) {
            // only Y
            CutObject = findOrCreateObject(CutYName);
        }
        else if ((!hasBoxY && hasBoxZ) || (hasBoxY && hasBoxZ)) {
            // at least Z
            CutObject = findOrCreateObject(CutZName);
        }
        if (auto cut = dynamic_cast<Part::Cut*>(CutObject)) {
            // only do this when there is no other box to save recomputes
            cut->recomputeFeature(true);
        }
    }
}

void SectionCut::onFlipYclicked()
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Flip persistent section cut",
            [this]() {
                onFlipYclicked();
            }
        );
        return;
    }
    FlipClickedHelper(BoxYName);

    if (auto CutObject = findOrCreateObject(CutYName)) {
        // if there is another cut, we must recalculate it too
        // we only need to check for Z since the hierarchy is always Z->Y->X
        if (hasBoxZ) {
            CutObject = findObject(CutZName);
        }
        if (auto cut = dynamic_cast<Part::Cut*>(CutObject)) {
            cut->recomputeFeature(true);
        }
    }
}

void SectionCut::onFlipZclicked()
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Flip persistent section cut",
            [this]() {
                onFlipZclicked();
            }
        );
        return;
    }
    FlipClickedHelper(BoxZName);

    if (auto CutObject = findOrCreateObject(CutZName)) {
        CutObject->recomputeFeature(true);
    }
}

Part::Box* SectionCut::findCutBox(const char* name) const
{
    if (auto obj = semanticResource(name)) {
        auto pcBox = dynamic_cast<Part::Box*>(obj);
        if (!pcBox) {
            throw Base::RuntimeError("Section cut error: cut box is incorrectly named, cannot proceed");
        }

        return pcBox;
    }

    return nullptr;
}

App::DocumentObject* SectionCut::findObject(const char* objName) const
{
    return semanticResource(objName);
}

App::DocumentObject* SectionCut::findOrCreateObject(const char* objName)
{
    auto object = findObject(objName);
    if (!object) {
        Base::Console().warning((std::string("Section cut warning: there is no ")
                                 + std::string(objName) + std::string(", trying to recreate it\n"))
                                    .c_str());
        startCutting();
        return nullptr;
    }

    return object;
}

// changes the cutface color
void SectionCut::onCutColorclicked()
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Change persistent section cut appearance",
            [this]() {
                onCutColorclicked();
            }
        );
        return;
    }
    if (ui->groupBoxX->isChecked() || ui->groupBoxY->isChecked() || ui->groupBoxZ->isChecked()) {
        changeCutBoxColors();
    }
}

// changes cutbox colors
void SectionCut::changeCutBoxColors()
{
    // a lambda to set box color and transparency
    auto setColorTransparency = [&](App::DocumentObject* boxObject) {
        auto boxVP = Gui::Application::Instance->getViewProvider(boxObject);
        auto boxVPGO = dynamic_cast<Gui::ViewProviderGeometryObject*>(boxVP);
        if (boxVPGO) {
            Base::Color boxColor;
            boxColor.setValue<QColor>(ui->CutColor->color());
            boxVPGO->ShapeAppearance.setDiffuseColor(boxColor);
            int boxTransparency = ui->CutTransparencyHS->value();
            boxVPGO->Transparency.setValue(boxTransparency);
        }
    };
    if (auto* box = findObject(BoxXName)) {
        setColorTransparency(box);
    }
    if (auto* box = findObject(BoxYName)) {
        setColorTransparency(box);
    }
    if (auto* box = findObject(BoxZName)) {
        setColorTransparency(box);
    }

    // we must recompute the topmost cut to make the color visible
    // we must hereby first recompute ewvery cut non-recursively in the order X -> Y -> Z
    // eventually recompute the topmost cut recursively
    if (auto* cut = findObject(CutXName)) {
        cut->recomputeFeature(false);
    }
    if (auto* cut = findObject(CutYName)) {
        cut->recomputeFeature(false);
    }
    if (auto* cut = findObject(CutZName)) {
        cut->recomputeFeature(false);
    }
    if (auto* cut = findObject(CutZName)) {
        cut->recomputeFeature(true);
    }
    else if (auto* cut = findObject(CutYName)) {
        cut->recomputeFeature(true);
    }
    else if (auto* cut = findObject(CutXName)) {
        cut->recomputeFeature(true);
    }
}

void SectionCut::onTransparencyHSMoved(int val)
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Change persistent section cut appearance",
            [this, val]() {
                onTransparencyHSMoved(val);
            }
        );
        return;
    }
    ui->CutTransparencyHS->setToolTip(QString::number(val) + QStringLiteral(" %"));
    // highlight the tooltip
    QToolTip::showText(QCursor::pos(), QString::number(val) + QStringLiteral(" %"), nullptr);
    if (ui->groupBoxX->isChecked() || ui->groupBoxY->isChecked() || ui->groupBoxZ->isChecked()) {
        changeCutBoxColors();
    }
}

void SectionCut::onTransparencyHSChanged(int val)
{
    onTransparencyHSMoved(val);
}

// change from/to BooleanFragments compound
void SectionCut::onGroupBoxIntersectingToggled()
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Edit persistent section cut",
            [this]() {
                onGroupBoxIntersectingToggled();
            }
        );
        return;
    }
    // re-cut
    if (ui->groupBoxX->isChecked() || ui->groupBoxY->isChecked() || ui->groupBoxZ->isChecked()) {
        startCutting();
    }
}

// changes the BooleanFragments color
void SectionCut::onBFragColorclicked()
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Change persistent section cut appearance",
            [this]() {
                onBFragColorclicked();
            }
        );
        return;
    }
    // when there is no cut yet, there is nothing to do
    if (!(ui->groupBoxX->isChecked() || ui->groupBoxY->isChecked() || ui->groupBoxZ->isChecked())) {
        return;
    }

    setBooleanFragmentsColor();
    // we must recompute the topmost cut to make the color visible
    if (auto* cut = findObject(CutZName)) {
        cut->recomputeFeature(true);
    }
    else if (auto* cut = findObject(CutYName)) {
        cut->recomputeFeature(true);
    }
    else if (auto* cut = findObject(CutXName)) {
        cut->recomputeFeature(true);
    }
}

// sets BooleanFragments color
void SectionCut::setBooleanFragmentsColor()
{
    App::DocumentObject* compoundObject =
        findObject(CompoundName);
    if (!compoundObject) {
        Base::Console().error("Section cut error: compound is incorrectly named, cannot proceed\n");
        return;
    }
    // assure it is not a Part::Compound
    auto pcCompound = dynamic_cast<Part::Compound*>(compoundObject);
    if (!pcCompound && compoundObject) {
        // check for valid BooleanFragments by accessing its ViewProvider
        auto CompoundBFVP = Gui::Application::Instance->getViewProvider(compoundObject);
        if (!CompoundBFVP) {
            Base::Console().error("Section cut error: cannot access ViewProvider of cut compound\n");
            return;
        }
        auto CutCompoundBFGeom = dynamic_cast<Gui::ViewProviderGeometryObject*>(CompoundBFVP);
        if (CutCompoundBFGeom) {
            Base::Color BFColor;
            BFColor.setValue<QColor>(ui->BFragColor->color());
            CutCompoundBFGeom->ShapeAppearance.setDiffuseColor(BFColor);
            int BFTransparency = ui->BFragTransparencyHS->value();
            CutCompoundBFGeom->Transparency.setValue(BFTransparency);
            compoundObject->recomputeFeature(false);
        }
    }
}

void SectionCut::onBFragTransparencyHSMoved(int val)
{
    if (!atomicChangeActive()) {
        runAtomicChange(
            "Change persistent section cut appearance",
            [this, val]() {
                onBFragTransparencyHSMoved(val);
            }
        );
        return;
    }
    // lambda to set transparency
    auto setTransparency = [&](App::DocumentObject* cutObject) {
        Gui::ViewProvider* CutVP = Gui::Application::Instance->getViewProvider(cutObject);
        if (!CutVP) {
            Base::Console().error("Section cut error: cannot access ViewProvider of cut object\n");
            return;
        }
        auto CutVPGeom = dynamic_cast<Gui::ViewProviderGeometryObject*>(CutVP);
        if (CutVPGeom) {
            int BFTransparency = ui->BFragTransparencyHS->value();
            CutVPGeom->Transparency.setValue(BFTransparency);
            cutObject->recomputeFeature(true);
        }
    };

    // Part::Cut ignores the cutbox transparency when it is set
    // to zero and the BooleanFragments transparency is not zero
    // therefore limit the cutbox transparency to 1 in this case
    if (val > 0) {
        ui->CutTransparencyHS->setMinimum(1);
    }
    else {
        ui->CutTransparencyHS->setMinimum(0);
    }

    ui->BFragTransparencyHS->setToolTip(QString::number(val) + QStringLiteral(" %"));
    // highlight the tooltip
    QToolTip::showText(QCursor::pos(), QString::number(val) + QStringLiteral(" %"), nullptr);

    // when there is no cut yet, there is nothing else to do
    if (ui->groupBoxX->isChecked() || ui->groupBoxY->isChecked() || ui->groupBoxZ->isChecked()) {
        setBooleanFragmentsColor();
        // we must set the transparency to every cut and recompute in the order X -> Y -> Z
        if (auto* cut = findObject(CutXName)) {
            setTransparency(cut);
        }
        if (auto* cut = findObject(CutYName)) {
            setTransparency(cut);
        }
        if (auto* cut = findObject(CutZName)) {
            setTransparency(cut);
        }
    }
}

void SectionCut::onBFragTransparencyHSChanged(int val)
{
    onBFragTransparencyHSMoved(val);
}

// refreshes the list of document objects and the visible objects
void SectionCut::onRefreshCutPBclicked()
{
    try {
        auto* docGui = Gui::Application::Instance
            ? Gui::Application::Instance->activeDocument()
            : nullptr;
        auto* activeDocument = docGui ? docGui->getDocument() : nullptr;
        if (!activeDocument) {
            noDocumentActions();
            Base::Console().error(
                "Section cut error: there is no document\n"
            );
            return;
        }

        const auto objects = activeDocument->getObjects();
        if (objects.empty()) {
            Base::Console().error(
                "Section cut error: there are no objects in the document\n"
            );
            return;
        }

        doc = activeDocument;
        documentUid = doc->Uid.getValueStr();
        closeFinalized = false;
        semanticOwnerIdentity.clear();
        newResourceIdentities.clear();
        clearLegacyResources();
        loadSemanticSources();
        if (!findSemanticOwner()) {
            captureLegacyResources();
        }
        if (ObjectsListVisible.empty()) {
            for (auto* object : objects) {
                if (object->Visibility.getValue()
                    && PartGui::isModelingObjectActive(object)
                    && isSectionCutPresentationSource(object)
                    && !object->isDerivedFrom<App::DocumentTimeline>()
                    && !App::DocumentTimeline::hasTimelineResourceRole(
                        object
                    )
                    && !isExactSectionCutOwner(object)) {
                    ObjectsListVisible.emplace_back(object);
                }
            }
        }

        {
            const QSignalBlocker blockGroupX(ui->groupBoxX);
            const QSignalBlocker blockGroupY(ui->groupBoxY);
            const QSignalBlocker blockGroupZ(ui->groupBoxZ);
            const QSignalBlocker blockCutX(ui->cutX);
            const QSignalBlocker blockCutY(ui->cutY);
            const QSignalBlocker blockCutZ(ui->cutZ);
            const QSignalBlocker blockSliderX(ui->cutXHS);
            const QSignalBlocker blockSliderY(ui->cutYHS);
            const QSignalBlocker blockSliderZ(ui->cutZHS);
            const QSignalBlocker blockFlipX(ui->flipX);
            const QSignalBlocker blockFlipY(ui->flipY);
            const QSignalBlocker blockFlipZ(ui->flipZ);
            const QSignalBlocker blockIntersection(
                ui->groupBoxIntersecting
            );
            const QSignalBlocker blockCutColor(ui->CutColor);
            const QSignalBlocker blockCutTransparency(
                ui->CutTransparencyHS
            );
            const QSignalBlocker blockFragmentColor(ui->BFragColor);
            const QSignalBlocker blockFragmentTransparency(
                ui->BFragTransparencyHS
            );

            hasBoxX = findObject(BoxXName)
                && findObject(CutXName);
            hasBoxY = findObject(BoxYName)
                && findObject(CutYName);
            hasBoxZ = findObject(BoxZName)
                && findObject(CutZName);
            ui->groupBoxX->setChecked(hasBoxX);
            ui->groupBoxY->setChecked(hasBoxY);
            ui->groupBoxZ->setChecked(hasBoxZ);

            std::vector<App::DocumentObject*> cutObjects;
            (void)findObjects(cutObjects);
            filterObjects(cutObjects);
            ui->groupBoxIntersecting->setEnabled(
                cutObjects.size() > 1
            );
            ui->RefreshCutPB->setEnabled(
                !(hasBoxX || hasBoxY || hasBoxZ)
            );

            const Base::BoundBox3d bounds = collectObjects();
            initControls(bounds);
            initCutRanges();
        }
        tryStartCutting();
    }
    catch (Base::Exception& error) {
        error.reportException();
    }
    catch (const std::exception& error) {
        Base::Console().error(
            "Section cut refresh failed: %s\n",
            error.what()
        );
    }
}

SbBox3f SectionCut::getViewBoundingBox()
{
    SbBox3f Box;
    auto docGui = Gui::Application::Instance->activeDocument();
    if (!docGui) {
        Base::Console().error("Section cut error: there is no active document\n");
        return Box;  // return an empty box
    }
    auto view = dynamic_cast<Gui::View3DInventor*>(docGui->getActiveView());
    if (!view) {
        Base::Console().error("Section cut error: could not get the active view\n");
        return Box;  // return an empty box
    }
    Gui::View3DInventorViewer* viewer = view->getViewer();
    SoCamera* camera = viewer->getSoRenderManager()->getCamera();
    if (!camera) {
        return Box;  // return an empty box
    }
    // get scene bounding box
    SoGetBoundingBoxAction action(viewer->getSoRenderManager()->getViewportRegion());
    action.apply(viewer->getSceneGraph());
    return action.getBoundingBox();
}

void SectionCut::refreshCutRanges(
    SbBox3f BoundingBox,
    bool forXValue,
    bool forYValue,
    bool forZValue,
    bool forXRange,
    bool forYRange,
    bool forZRange
)
{
    if (!BoundingBox.isEmpty()) {  // NOLINT
        SbVec3f center = BoundingBox.getCenter();
        int minDecimals = Base::UnitsApi::getDecimals();
        float lenx {};
        float leny {};
        float lenz {};
        BoundingBox.getSize(lenx, leny, lenz);
        const int steps = 100;

        // set the ranges
        float rangeMin {};
        float rangeMax {};
        if (forXRange) {
            rangeMin = center[0] - (lenx / 2);
            rangeMax = center[0] + (lenx / 2);
            ui->cutX->setRange(rangeMin, rangeMax);
            // determine the single step values
            lenx = lenx / float(steps);
            int dim = static_cast<int>(log10(lenx));
            double singleStep = pow(10.0, dim);
            ui->cutX->setSingleStep(singleStep);
        }
        if (forYRange) {
            rangeMin = center[1] - (leny / 2);
            rangeMax = center[1] + (leny / 2);
            ui->cutY->setRange(rangeMin, rangeMax);
            leny = leny / float(steps);
            int dim = static_cast<int>(log10(leny));
            double singleStep = pow(10.0, dim);
            ui->cutY->setSingleStep(singleStep);
        }
        if (forZRange) {
            rangeMin = center[2] - (lenz / 2);
            rangeMax = center[2] + (lenz / 2);
            ui->cutZ->setRange(rangeMin, rangeMax);
            lenz = lenz / float(steps);
            int dim = static_cast<int>(log10(lenz));
            double singleStep = pow(10.0, dim);
            ui->cutZ->setSingleStep(singleStep);
        }
        if (forXValue) {
            ui->cutX->setValue(center[0]);
            ui->cutXHS->setValue(50);
        }
        if (forYValue) {
            ui->cutY->setValue(center[1]);
            ui->cutYHS->setValue(50);
        }
        if (forZValue) {
            ui->cutZ->setValue(center[2]);
            ui->cutZHS->setValue(50);
        }

        // set decimals
        ui->cutX->setDecimals(minDecimals);
        ui->cutY->setDecimals(minDecimals);
        ui->cutZ->setDecimals(minDecimals);
    }
}

App::DocumentObject* SectionCut::CreateBooleanFragments(App::Document* doc)
{
    // NOLINTBEGIN
    // create the object
    Gui::Command::doCommand(Gui::Command::Doc, "import FreeCAD");
    Gui::Command::doCommand(Gui::Command::Doc, "from BOPTools import SplitFeatures");
    auto* object = Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        *doc,
        QStringLiteral(
            "SplitFeatures.makeBooleanFragments(name=\"%1\")"
        )
            .arg(QString::fromLatin1(CompoundName))
            .toUtf8(),
        App::DocumentObject::getClassTypeId()
    );
    if (!object) {
        Base::Console().error((std::string("Section cut error: ") + std::string(CompoundName)
                               + std::string(" could not be added\n"))
                                  .c_str());
        return nullptr;
    }
    setSemanticResource(CompoundName, object);
    return object;
    // NOLINTEND
}

App::DocumentObject* SectionCut::createBooleanFragments(
    const std::vector<App::DocumentObject*>& links,
    int transparency
)
{
    App::DocumentObject* CutCompoundBF = CreateBooleanFragments(doc);
    // the BooleanFragment implementation requires to first add at least 2 objects
    // before any other setting to the BooleanFragment object can be made
    auto CutLinkList = dynamic_cast<App::PropertyLinkList*>(
        CutCompoundBF ? CutCompoundBF->getPropertyByName("Objects") : nullptr
    );
    if (!CutLinkList) {
        throw Base::RuntimeError((std::string("Section cut error: ") + std::string(CompoundName)
                                  + std::string(" could not be added\n"))
                                     .c_str());
    }
    CutLinkList->setValue(links);
    // make all objects in the BooleanFragments object invisible to later only show the cut
    for (auto aLinkObj : links) {
        aLinkObj->Visibility.setValue(false);
    }
    // set the transparency
    auto vpCompound = dynamic_cast<Gui::ViewProviderGeometryObject*>(
        Gui::Application::Instance->getViewProvider(CutCompoundBF)
    );
    vpCompound->Transparency.setValue(transparency);
    // set the color
    // setBooleanFragmentsColor also does a non-recursive recompute
    setBooleanFragmentsColor();

    return CutCompoundBF;
}

Part::Compound* SectionCut::createCompound(const std::vector<App::DocumentObject*>& links, int transparency)
{
    auto CutCompoundPart = doc->addObject<Part::Compound>(CompoundName);
    if (!CutCompoundPart) {
        throw Base::RuntimeError((std::string("Section cut error: ") + std::string(CompoundName)
                                  + std::string(" could not be added\n"))
                                     .c_str());
    }
    setSemanticResource(CompoundName, CutCompoundPart);

    // add the link to the compound
    CutCompoundPart->Links.setValue(links);
    // set the transparency
    auto vpCompound = dynamic_cast<Gui::ViewProviderGeometryObject*>(
        Gui::Application::Instance->getViewProvider(CutCompoundPart)
    );
    vpCompound->Transparency.setValue(transparency);
    CutCompoundPart->recomputeFeature();

    return CutCompoundPart;
}
// NOLINTEND(cppcoreguidelines-avoid-magic-numbers,readability-magic-numbers)

#include "moc_SectionCutting.cpp"
