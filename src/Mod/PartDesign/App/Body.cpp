// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2010 Juergen Riegel <FreeCAD@juergen-riegel.net>        *
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


#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/GeoFeature.h>
#include <App/VarSet.h>
#include <App/Origin.h>
#include <Base/Console.h>
#include <Base/Placement.h>
#include <Base/Uuid.h>

#include "Body.h"
#include "BodyPy.h"
#include "DesignFeature.h"
#include "FeatureBase.h"
#include "FeatureSketchBased.h"
#include "FeatureSolid.h"
#include "FeatureTransformed.h"
#include "ShapeBinder.h"

using namespace PartDesign;


PROPERTY_SOURCE(PartDesign::Body, Part::BodyBase)

Body::Body()
{
    ADD_PROPERTY_TYPE(AllowCompound, (true), "Base", App::Prop_None, "Allow multiple solids in Body");
    Base::Uuid bodyId;
    ADD_PROPERTY_TYPE(
        VibeCADBodyId,
        (bodyId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly),
        "Persistent identity of this physical Body"
    );
    Base::Uuid designId;
    ADD_PROPERTY_TYPE(
        DesignId,
        (designId),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Persistent identity of the Design which owns this Body"
    );
    ADD_PROPERTY_TYPE(
        ComponentId,
        (""),
        "VibeCAD Design",
        static_cast<App::PropertyType>(App::Prop_ReadOnly | App::Prop_Hidden),
        "Persistent identity of the Component containing this Body"
    );

    _GroupTouched.setStatus(App::Property::Output, true);
}

App::DocumentObject* Body::getModelingState()
{
    return designBodyStateBefore(this, nullptr);
}

const App::DocumentObject* Body::getModelingState() const
{
    return designBodyStateBefore(this, nullptr);
}

const App::DocumentObject* Body::getModelingPresentation() const
{
    if (const auto* publication = findDesignBodyPublication(this)) {
        return publication;
    }
    return Part::BodyBase::getModelingPresentation();
}

bool Body::containsModelingState(const App::DocumentObject* object) const
{
    if (Part::BodyBase::containsModelingState(object)) {
        return true;
    }
    const auto* state = freecad_cast<const DesignBodyState*>(object);
    return state && state->getDocument() == getDocument()
        && state->BodyId.getValueStr() == VibeCADBodyId.getValueStr();
}

/*
// Note: The following code will catch Python Document::removeObject() modifications. If the object
removed is
// a member of the Body::Group, then it will be automatically removed from the Group property which
triggers the
// following two methods
// But since we require the Python user to call both Document::addObject() and Body::addObject(), we
should
// also require calling both Document::removeObject and Body::removeFeature() in order to be
consistent void Body::onBeforeChange(const App::Property *prop)
{
    // Remember the feature before the current Tip. If the Tip is already at the first feature,
remember the next feature if (prop == &Group) { std::vector<App::DocumentObject*> features =
Group.getValues(); if (features.empty()) { rememberTip = NULL; } else {
            std::vector<App::DocumentObject*>::iterator it = std::find(features.begin(),
features.end(), Tip.getValue()); if (it == features.begin()) { it++; if (it == features.end())
rememberTip = NULL; else rememberTip = *it; } else { it--; rememberTip = *it;
            }
        }
    }

    return Part::Feature::onBeforeChange(prop);
}

void Body::onChanged(const App::Property *prop)
{
    if (prop == &Group) {
        std::vector<App::DocumentObject*> features = Group.getValues();
        if (features.empty()) {
            Tip.setValue(NULL);
        } else {
            std::vector<App::DocumentObject*>::iterator it = std::find(features.begin(),
features.end(), Tip.getValue()); if (it == features.end()) {
                // Tip feature was deleted
                Tip.setValue(rememberTip);
            }
        }
    }

    return Part::Feature::onChanged(prop);
}
*/

short Body::mustExecute() const
{
    if (Tip.isTouched()) {
        return 1;
    }
    return Part::BodyBase::mustExecute();
}

App::DocumentObject* Body::getPrevResultFeature(App::DocumentObject* start)
{
    if (!start) {  // default to tip
        start = Tip.getValue();
    }

    if (!start) {  // No Tip
        return nullptr;
    }

    if (!hasObject(start)) {
        return nullptr;
    }

    const std::vector<App::DocumentObject*>& features = Group.getValues();

    auto startIt = std::find(features.rbegin(), features.rend(), start);
    if (startIt == features.rend()) {  // object not found
        return nullptr;
    }

    auto rvIt = std::find_if(startIt + 1, features.rend(), isResultFeature);
    if (rvIt != features.rend()) {  // the solid found in model list
        return *rvIt;
    }
    return nullptr;
}

App::DocumentObject* Body::getPrevSolidFeature(App::DocumentObject* start)
{
    return getPrevResultFeature(start);
}

App::DocumentObject* Body::getNextResultFeature(App::DocumentObject* start)
{
    if (!start) {  // default to tip
        start = Tip.getValue();
    }

    if (!start || !hasObject(start)) {  // no or faulty tip
        return nullptr;
    }

    const std::vector<App::DocumentObject*>& features = Group.getValues();
    std::vector<App::DocumentObject*>::const_iterator startIt;

    startIt = std::find(features.begin(), features.end(), start);
    if (startIt == features.end()) {  // object not found
        return nullptr;
    }

    startIt++;
    if (startIt == features.end()) {  // features list has only one element
        return nullptr;
    }

    auto rvIt = std::find_if(startIt, features.end(), isResultFeature);
    if (rvIt != features.end()) {  // the solid found in model list
        return *rvIt;
    }
    return nullptr;
}

App::DocumentObject* Body::getNextSolidFeature(App::DocumentObject* start)
{
    return getNextResultFeature(start);
}

bool Body::isAfterInsertPoint(App::DocumentObject* feature)
{
    App::DocumentObject* nextSolid = getNextResultFeature();
    assert(feature);

    if (feature == nextSolid) {
        return true;
    }
    else if (!nextSolid) {  // the tip is last solid, we can't be placed after it
        return false;
    }
    else {
        return isAfter(feature, nextSolid);
    }
}

bool Body::isSolidFeature(const App::DocumentObject* obj)
{
    if (!obj) {
        return false;
    }

    // Design History controllers and exact Body-state resources describe the
    // global state graph.  They are PartDesign::Feature subclasses so they can
    // reuse shape execution and ViewProvider infrastructure, but they are not
    // sequential Body features and must never become a Body result or Tip.
    if (dynamic_cast<const DesignOperationProperties*>(obj)
        || freecad_cast<const DesignBodyState*>(obj)) {
        return false;
    }

    if (obj->isDerivedFrom<PartDesign::Feature>()) {
        if (PartDesign::Feature::isDatum(obj)) {
            // Datum objects are not solid
            return false;
        }
        if (auto transFeature = freecad_cast<PartDesign::Transformed*>(obj)) {
            // Transformed Features inside a MultiTransform are not solid features
            return !transFeature->isMultiTransformChild();
        }
        return true;
    }
    return false;
}

bool Body::isResultFeature(const App::DocumentObject* obj)
{
    if (!obj) {
        return false;
    }
    // Preserve the established Part Design distinction between a sequential result and a support
    // feature.  In particular, datums and the internal Transformed children owned by a
    // MultiTransform derive from PartDesign::Feature (and therefore Part::Feature), but must never
    // become the Body Tip or enter the BaseFeature chain.
    if (obj->isDerivedFrom<PartDesign::Feature>()) {
        return isSolidFeature(obj);
    }
    // Shape binders are reference/support geometry.  They intentionally derive directly from
    // Part::Feature, so exclude them before accepting ordinary Part results.
    if (obj->isDerivedFrom<PartDesign::ShapeBinder>()
        || obj->isDerivedFrom<PartDesign::SubShapeBinder>()) {
        return false;
    }
    // Part Design features are sequential material operations, while ordinary Part features carry
    // their dependencies explicitly (Base/Tool/Shapes/etc.). Both are valid result nodes in the
    // consolidated modeling body. Part2D objects and body/group containers remain support or
    // ownership objects rather than result features.
    return obj->isDerivedFrom<Part::Feature>()
        && !obj->isDerivedFrom<Part::Part2DObject>() && !obj->isDerivedFrom<Part::BodyBase>()
        && !obj->isDerivedFrom<Part::Datum>();
}

bool Body::isAllowed(const App::DocumentObject* obj)
{
    if (!obj) {
        return false;
    }

    if (dynamic_cast<const DesignOperationProperties*>(obj)
        || freecad_cast<const DesignBodyState*>(obj)) {
        return false;
    }

    return (
        (obj->isDerivedFrom<Part::Feature>() && !obj->isDerivedFrom<Part::BodyBase>())
        || obj->isDerivedFrom<Part::Datum>() ||
        // TODO Shouldn't we replace it with Sketcher::SketchObject? (2015-08-13, Fat-Zer)
        obj->isDerivedFrom<Part::Part2DObject>() || obj->isDerivedFrom<PartDesign::ShapeBinder>()
        || obj->isDerivedFrom<PartDesign::SubShapeBinder>() ||
        // TODO Why this lines was here? why should we allow anything of those? (2015-08-13,
        // Fat-Zer) obj->isDerivedFrom<Part::FeaturePython>() // trouble with this line on Windows!?
        // Linker fails to find getClassTypeId() of the Part::FeaturePython...
        // obj->isDerivedFrom<Part::Feature>()
        // allow VarSets for parameterization
        obj->isDerivedFrom<App::VarSet>() || obj->isDerivedFrom<App::DatumElement>()
        || obj->isDerivedFrom<App::LocalCoordinateSystem>()
    );
}


Body* Body::findBodyOf(const App::DocumentObject* feature)
{
    if (!feature) {
        return nullptr;
    }

    return static_cast<Body*>(BodyBase::findBodyOf(feature));
}


std::vector<App::DocumentObject*> Body::addObject(App::DocumentObject* feature)
{
    if (!isAllowed(feature)) {
        Base::Console().error(
            "Body '%s' rejected object '%s' of type '%s'\n",
            getNameInDocument() ? getNameInDocument() : "<detached>",
            feature && feature->getNameInDocument()
                ? feature->getNameInDocument()
                : "<detached>",
            feature ? feature->getTypeId().getName() : "<null>"
        );
        throw Base::ValueError("Body: object is not allowed");
    }

    // TODO: features should not add all links

    // only one group per object. If it is in a body the single feature will be removed
    auto* group = App::GroupExtension::getGroupOfObject(feature);
    if (group && group != getExtendedObject()) {
        group->getExtensionByType<GroupExtension>()->removeObject(feature);
    }


    insertObject(feature, getNextResultFeature(), /*after = */ false);
    // Move the Tip if we added a result feature.
    if (isResultFeature(feature)) {
        Tip.setValue(feature);
    }

    if (feature->Visibility.getValue() && isResultFeature(feature)) {
        for (auto obj : Group.getValues()) {
            if (obj->Visibility.getValue() && obj != feature && isResultFeature(obj)) {
                obj->Visibility.setValue(false);
            }
        }
    }

    std::vector<App::DocumentObject*> result = {feature};
    return result;
}

std::vector<App::DocumentObject*> Body::addObjects(std::vector<App::DocumentObject*> objs)
{

    for (auto obj : objs) {
        addObject(obj);
    }

    return objs;
}


void Body::insertObject(App::DocumentObject* feature, App::DocumentObject* target, bool after)
{
    if (target && !hasObject(target)) {
        throw Base::ValueError(
            "Body: the feature we should insert relative to is not part of that body"
        );
    }

    // ensure that all origin links are ok
    relinkToOrigin(feature);

    std::vector<App::DocumentObject*> model = Group.getValues();
    std::vector<App::DocumentObject*>::iterator insertInto;

    // Find out the position there to insert the feature
    if (!target) {
        if (after) {
            insertInto = model.begin();
        }
        else {
            insertInto = model.end();
        }
    }
    else {
        std::vector<App::DocumentObject*>::iterator targetIt
            = std::find(model.begin(), model.end(), target);
        assert(targetIt != model.end());
        if (after) {
            insertInto = targetIt + 1;
        }
        else {
            insertInto = targetIt;
        }
    }

    // Insert the new feature after the given
    model.insert(insertInto, feature);

    Group.setValues(model);

    if (feature->isDerivedFrom<PartDesign::Feature>()) {
        static_cast<PartDesign::Feature*>(feature)->_Body.setValue(this);
    }

    // Set the BaseFeature property
    setBaseProperty(feature);
}

void Body::setBaseProperty(App::DocumentObject* feature)
{
    if (Body::isResultFeature(feature)) {
        // Set BaseFeature property to previous feature (this might be the Tip feature)
        App::DocumentObject* prevSolidFeature = getPrevResultFeature(feature);
        // NULL is ok here, it just means we made the current one feature the base solid. Ordinary
        // Part features keep their own explicit input links and therefore need no BaseFeature.
        if (feature->isDerivedFrom<PartDesign::Feature>()) {
            static_cast<PartDesign::Feature*>(feature)->BaseFeature.setValue(prevSolidFeature);
        }

        // Reroute the next solid feature's BaseFeature property to this feature
        App::DocumentObject* nextSolidFeature = getNextResultFeature(feature);
        if (nextSolidFeature && nextSolidFeature->isDerivedFrom<PartDesign::Feature>()) {
            static_cast<PartDesign::Feature*>(nextSolidFeature)->BaseFeature.setValue(feature);
        }
    }
}

std::vector<App::DocumentObject*> Body::removeObject(App::DocumentObject* feature)
{
    // This method must be called BEFORE the feature is removed from the Document!

    App::DocumentObject* nextSolidFeature = getNextResultFeature(feature);
    App::DocumentObject* prevSolidFeature = getPrevResultFeature(feature);

    // It's ok to remove the first solid feature, that just mean the next feature become the base one

    if (nextSolidFeature && nextSolidFeature->isDerivedFrom(PartDesign::Feature::getClassTypeId())) {
        auto* nextPD = static_cast<PartDesign::Feature*>(nextSolidFeature);
        // Check if the next feature is pointing to the one being deleted
        if (nextPD->BaseFeature.getValue() == feature) {
            nextPD->BaseFeature.setValue(prevSolidFeature);
            nextPD->onBaseFeatureRerouted(feature, prevSolidFeature);
        }
    }

    std::vector<App::DocumentObject*> model = Group.getValues();
    const auto it = std::ranges::find(model, feature);

    // Adjust Tip feature if it is pointing to the deleted object
    if (Tip.getValue() == feature) {
        if (prevSolidFeature) {
            Tip.setValue(prevSolidFeature);
        }
        else {
            Tip.setValue(nextSolidFeature);
        }
    }

    // Erase feature from Group
    if (it != model.end()) {
        model.erase(it);
        Group.setValues(model);
        if (auto* partDesignFeature = freecad_cast<PartDesign::Feature*>(feature);
            partDesignFeature && partDesignFeature->_Body.getValue() == this) {
            partDesignFeature->_Body.setValue(nullptr);
        }
    }
    std::vector<App::DocumentObject*> result = {feature};
    return result;
}


App::DocumentObjectExecReturn* Body::execute()
{
    Part::BodyBase::execute();
    /*
    Base::Console().error("Body '%s':\n", getNameInDocument());
    App::DocumentObject* tip = Tip.getValue();
    Base::Console().error("   Tip: %s\n", (tip == NULL) ? "None" : tip->getNameInDocument());
    std::vector<App::DocumentObject*> model = Group.getValues();
    Base::Console().error("   Group:\n");
    for (std::vector<App::DocumentObject*>::const_iterator m = model.begin(); m != model.end(); m++)
    { if (*m == NULL) continue; Base::Console().error("      %s", (*m)->getNameInDocument()); if
    (Body::isResultFeature(*m)) { App::DocumentObject* baseFeature =
    static_cast<PartDesign::Feature*>(*m)->BaseFeature.getValue(); Base::Console().error(", Base:
    %s\n", baseFeature == NULL ? "None" : baseFeature->getNameInDocument()); } else {
            Base::Console().error("\n");
        }
    }
    */

    App::DocumentObject* tip = Tip.getValue();

    Part::TopoShape tipShape;
    if (tip) {
        if (!tip->isDerivedFrom<Part::Feature>()) {
            return new App::DocumentObjectExecReturn(
                QT_TRANSLATE_NOOP("Exception", "Linked object is not a shape feature")
            );
        }

        // get the shape of the tip
        tipShape = static_cast<Part::Feature*>(tip)->Shape.getShape();

        if (tipShape.getShape().IsNull()) {
            // A Design Body keeps its persistent identity when History says
            // the Body is absent (before creation, after consumption, or
            // while its creation operation is suppressed). The publication
            // is the only Body tip allowed to represent that valid empty
            // state; an empty legacy/native feature remains an error.
            const auto* publication =
                freecad_cast<const DesignBodyPublication*>(tip);
            if (publication && publication->isValid()
                && !designBodyStateBefore(this, nullptr)) {
                Shape.setValue(Part::TopoShape());
                return App::DocumentObject::StdReturn;
            }
            return new App::DocumentObjectExecReturn(
                QT_TRANSLATE_NOOP("Exception", "Tip shape is empty")
            );
        }

        // We should hide here the transformation of the baseFeature
        const Base::Matrix4D transform = tipShape.getTransform();
        if (!transform.isUnity()) {
            tipShape.transformShape(transform, true);
        }
    }
    else {
        tipShape = Part::TopoShape();
    }

    const auto* tipFeature = freecad_cast<const Part::Feature*>(Tip.getValue());
    const bool unchangedRestoredTip =
        preserveRestoredShape && restoredTip == Tip.getValue() && tipFeature
        && !tipFeature->Shape.isTouched();
    const Part::TopoShape& currentShape = Shape.getShape();
    if (!unchangedRestoredTip
        && (currentShape.isNull() != tipShape.isNull()
            || (!tipShape.isNull()
                && !currentShape.getShape().IsPartner(tipShape.getShape())))) {
        Shape.setValue(tipShape);
    }
    preserveRestoredShape = false;
    restoredTip = Tip.getValue();
    return App::DocumentObject::StdReturn;
}

void Body::onSettingDocument()
{

    if (connection.connected()) {
        connection.disconnect();
    }

    Part::BodyBase::onSettingDocument();
}

void Body::onChanged(const App::Property* prop)
{
    // we neither load a project nor perform undo/redo
    if (!this->isRestoring() && this->getDocument()
        && !this->getDocument()->isPerformingTransaction()) {
        if (prop == &BaseFeature) {
            FeatureBase* bf = nullptr;
            auto first = Group.getValues().empty() ? nullptr : Group.getValues().front();

            if (BaseFeature.getValue()) {
                // setup the FeatureBase if needed
                if (!first || !first->isDerivedFrom<FeatureBase>()) {
                    bf = getDocument()->addObject<FeatureBase>("BaseFeature");
                    insertObject(bf, first, false);

                    if (!Tip.getValue()) {
                        Tip.setValue(bf);
                    }
                }
                else {
                    bf = static_cast<FeatureBase*>(first);
                }
            }

            if (bf && (bf->BaseFeature.getValue() != BaseFeature.getValue())) {
                auto* source = BaseFeature.getValue();
                bf->BaseFeature.setValue(source);
                if (source) {
                    const auto bodyGlobal = App::GeoFeature::getGlobalPlacement(this);
                    const auto sourceGlobal = App::GeoFeature::getGlobalPlacement(source);
                    bf->Placement.setValue(bodyGlobal.inverse() * sourceGlobal);
                }
            }
        }
        else if (prop == &Group) {
            // if the FeatureBase was deleted we set the BaseFeature link to nullptr
            if (BaseFeature.getValue()
                && (Group.getValues().empty()
                    || !Group.getValues().front()->isDerivedFrom<FeatureBase>())) {
                BaseFeature.setValue(nullptr);
            }
        }
        else if (prop == &AllowCompound) {
            // As disallowing compounds can break the model we need to recompute the whole tree.
            // This will inform user about first place where there is more than one solid.
            // On allowing compounds we must also recompute the entire feature tree
            for (auto feature : getFullModel()) {
                feature->enforceRecompute();
            }
        }
        else if (prop == &ShapeMaterial) {
            std::vector<App::DocumentObject*> features = Group.getValues();
            if (!features.empty()) {
                for (auto it : features) {
                    auto feature = dynamic_cast<Part::Feature*>(it);
                    if (feature) {
                        if (feature->ShapeMaterial.getValue().getUUID()
                            != ShapeMaterial.getValue().getUUID()) {
                            feature->ShapeMaterial.setValue(ShapeMaterial.getValue());
                        }
                    }
                }
            }
        }
    }

    Part::BodyBase::onChanged(prop);
}

void Body::setupObject()
{
    Part::BodyBase::setupObject();
    auto* document = getDocument();
    if (!document || document->testStatus(App::Document::Restoring)) {
        return;
    }
    if (auto* timeline = App::DocumentTimeline::ensure(document)) {
        DesignId.setValue(timeline->DesignId.getValue());
    }
}

void Body::unsetupObject()
{
    Part::BodyBase::unsetupObject();
}

PyObject* Body::getPyObject()
{
    if (PythonObject.is(Py::_None())) {
        // ref counter is set to 1
        PythonObject = Py::Object(new BodyPy(this), true);
    }
    return Py::new_reference_to(PythonObject);
}

std::vector<std::string> Body::getSubObjects(int reason) const
{
    if (reason == GS_SELECT && !showTip) {
        return Part::BodyBase::getSubObjects(reason);
    }
    return {};
}

App::DocumentObject* Body::getSubObject(
    const char* subname,
    PyObject** pyObj,
    Base::Matrix4D* pmat,
    bool transform,
    int depth
) const
{
    while (subname && *subname == '.') {
        ++subname;  // skip leading .
    }

    // PartDesign::Feature now support grouping sibling features, and the user
    // is free to expand/collapse at any time. To not disrupt subname path
    // because of this, the body will peek the next two sub-objects reference,
    // and skip the first sub-object if possible.
    if (subname) {
        const char* firstDot = strchr(subname, '.');
        if (firstDot) {
            const char* secondDot = strchr(firstDot + 1, '.');
            if (secondDot) {
                auto firstObj = Group.find(std::string(subname, firstDot).c_str());
                if (!firstObj || firstObj->isDerivedFrom<PartDesign::Feature>()) {
                    auto secondObj = Group.find(std::string(firstDot + 1, secondDot).c_str());
                    if (secondObj) {
                        // we support only one level of sibling grouping, so no
                        // recursive call to our own getSubObject()
                        return Part::BodyBase::getSubObject(
                            firstDot + 1,
                            pyObj,
                            pmat,
                            transform,
                            depth + 1
                        );
                    }
                }
            }
        }
    }
#if 1
    return Part::BodyBase::getSubObject(subname, pyObj, pmat, transform, depth);
#else
    // The following code returns Body shape only if there is at least one
    // child visible in the body (when show through, not show tip). The
    // original intention is to sync visual to shape returned by
    // Part.getShape() when the body is included in some other group. But this
    // interfere with direct modeling using body shape. Therefore it is
    // disabled here.

    if (!pyObj || showTip
        || (subname && !Data::ComplexGeoData::isMappedElement(subname) && strchr(subname, '.'))) {
        return Part::BodyBase::getSubObject(subname, pyObj, pmat, transform, depth);
    }

    // We return the shape only if there are feature visible inside
    for (auto obj : Group.getValues()) {
        if (obj->Visibility.getValue() && obj->isDerivedFrom<PartDesign::Feature>()) {
            return Part::BodyBase::getSubObject(subname, pyObj, pmat, transform, depth);
        }
    }
    if (pmat && transform) {
        *pmat *= Placement.getValue().toMatrix();
    }
    return const_cast<Body*>(this);
#endif
}

void Body::onDocumentRestored()
{
    // Builds predating the Design-global ownership guard could persist the
    // final History controller as a second Body child.  That made the
    // controller's BaseFeature point back to BodyResult and created a cycle:
    // Body -> operation -> BodyResult -> Body.  Recover only that exact,
    // structurally forbidden relationship; do not reinterpret ordinary
    // legacy Body members.
    const auto restoredMembers = Group.getValues();
    std::vector<App::DocumentObject*> retainedMembers;
    retainedMembers.reserve(restoredMembers.size());
    DesignBodyPublication* publication = nullptr;
    std::size_t publicationCount = 0;
    bool removedGlobalDesignObject = false;
    for (auto* obj : restoredMembers) {
        if (auto* candidate = freecad_cast<DesignBodyPublication*>(obj)) {
            ++publicationCount;
            publication = candidate;
        }
        if (dynamic_cast<DesignOperationProperties*>(obj)
            || freecad_cast<DesignBodyState*>(obj)) {
            removedGlobalDesignObject = true;
            if (auto* feature = freecad_cast<PartDesign::Feature*>(obj)) {
                feature->_Body.setValue(nullptr);
                feature->BaseFeature.setValue(nullptr);
            }
            continue;
        }
        retainedMembers.push_back(obj);
    }
    if (removedGlobalDesignObject) {
        Group.setValues(retainedMembers);
        if (publicationCount == 1 && publication
            && std::ranges::find(retainedMembers, publication) != retainedMembers.end()) {
            Tip.setValue(publication);
        }
        enforceRecompute();
        Base::Console().warning(
            "Recovered Design-global History objects incorrectly stored in Body '%s'.\n",
            getNameInDocument() ? getNameInDocument() : "<detached>"
        );
    }

    for (auto obj : Group.getValues()) {
        if (obj->isDerivedFrom<PartDesign::Feature>()) {
            static_cast<PartDesign::Feature*>(obj)->_Body.setValue(this);
        }
    }
    _GroupTouched.setStatus(App::Property::Output, true);

    restoredTip = Tip.getValue();
    preserveRestoredShape = !Shape.getShape().isNull();

    // trigger ViewProviderBody::copyColorsfromTip
    if (Tip.getValue()) {
        Tip.touch();
    }

    DocumentObject::onDocumentRestored();
}

// A result node may be a wire, face, shell, or solid. Report solidness from actual topology rather
// than from participation in the Body's result sequence.
bool Body::isSolid()
{
    for (auto* feature : getFullModel()) {
        auto* shapeFeature = freecad_cast<Part::Feature*>(feature);
        if (shapeFeature && shapeFeature->Shape.getShape().countSubShapes(TopAbs_SOLID) > 0) {
            return true;
        }
    }
    return false;
}
