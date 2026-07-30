// SPDX-License-Identifier: LGPL-2.1-or-later

/******************************************************************************
 *   Copyright (c) 2012 Jan Rheinländer <jrheinlaender@users.sourceforge.net> *
 *                                                                            *
 *   This file is part of the FreeCAD CAx development system.                 *
 *                                                                            *
 *   This library is free software; you can redistribute it and/or            *
 *   modify it under the terms of the GNU Library General Public              *
 *   License as published by the Free Software Foundation; either             *
 *   version 2 of the License, or (at your option) any later version.         *
 *                                                                            *
 *   This library  is distributed in the hope that it will be useful,         *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of           *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the            *
 *   GNU Library General Public License for more details.                     *
 *                                                                            *
 *   You should have received a copy of the GNU Library General Public        *
 *   License along with this library; see the file COPYING.LIB. If not,       *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,            *
 *   Suite 330, Boston, MA  02111-1307, USA                                   *
 *                                                                            *
 ******************************************************************************/


#include <BRepGProp.hxx>
#include <GProp_GProps.hxx>
#include <Precision.hxx>

#include <unordered_set>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Exception.h>

#include "FeatureMultiTransform.h"
#include "FeatureAddSub.h"
#include "FeatureScaled.h"


using namespace PartDesign;

namespace PartDesign
{


PROPERTY_SOURCE(PartDesign::MultiTransform, PartDesign::Transformed)

MultiTransform::MultiTransform()
{
    ADD_PROPERTY_TYPE(
        LastFailingChildIndex,
        (-1),
        "Transformation diagnostics",
        App::Prop_ReadOnly,
        "Index of the first child that failed while generating transformations"
    );
    ADD_PROPERTY_TYPE(
        LastFailingChild,
        (""),
        "Transformation diagnostics",
        App::Prop_ReadOnly,
        "Name of the first child that failed while generating transformations"
    );
    ADD_PROPERTY(Transformations, (nullptr));
    Transformations.setSize(0);
}

void MultiTransform::synchronizeTimelineResources()
{
    auto* document = getDocument();
    if (!document) {
        return;
    }

    const auto transformationValues = Transformations.getValues();
    const std::unordered_set<App::DocumentObject*> currentResources(
        transformationValues.begin(),
        transformationValues.end()
    );
    const auto ensureProperty = [](App::DocumentObject* object,
                                   const char* type,
                                   const char* name,
                                   const char* description) {
        auto* property = object->getPropertyByName(name);
        if (!property) {
            property = object->addDynamicProperty(
                type,
                name,
                "Timeline",
                description,
                App::Prop_NoRecompute,
                true,
                true
            );
        }
        property->setStatus(App::Property::Hidden, true);
        property->setStatus(App::Property::LockDynamic, true);
        property->setStatus(App::Property::NoRecompute, true);
        return property;
    };

    auto* operationRole = dynamic_cast<App::PropertyString*>(ensureProperty(
        this,
        "App::PropertyString",
        App::DocumentTimeline::RolePropertyName,
        "Document timeline classification"
    ));
    auto* operationOwner =
        dynamic_cast<App::PropertyLinkHidden*>(ensureProperty(
            this,
            "App::PropertyLinkHidden",
            App::DocumentTimeline::OwnerPropertyName,
            "Owning timeline operation"
        ));
    if (!operationRole || !operationOwner) {
        throw Base::TypeError(
            "MultiTransform timeline metadata properties have incompatible types"
        );
    }
    operationOwner->setValue(nullptr);
    operationRole->setValue(App::DocumentTimeline::OperationRole);

    for (auto* resource : currentResources) {
        if (!resource || resource == this
            || resource->getDocument() != document) {
            throw Base::ValueError(
                "A MultiTransform timeline resource must be a distinct object "
                "in the same document"
            );
        }
        auto* role = dynamic_cast<App::PropertyString*>(ensureProperty(
            resource,
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName,
            "Document timeline classification"
        ));
        auto* owner = dynamic_cast<App::PropertyLinkHidden*>(ensureProperty(
            resource,
            "App::PropertyLinkHidden",
            App::DocumentTimeline::OwnerPropertyName,
            "MultiTransform operation which owns this internal transformation"
        ));
        if (!role || !owner) {
            throw Base::TypeError(
                "MultiTransform timeline metadata properties have incompatible types"
            );
        }

        owner->setValue(this);
        role->setValue(App::DocumentTimeline::ResourceRole);
    }

    // A child removed from Transformations is once again an independent
    // operation. Clear only ownership assigned to this MultiTransform; never
    // disturb a resource that has since been adopted by another operation.
    for (auto* object : document->getObjects()) {
        if (!object || currentResources.contains(object)) {
            continue;
        }
        auto* owner = dynamic_cast<App::PropertyLinkHidden*>(
            object->getPropertyByName(App::DocumentTimeline::OwnerPropertyName)
        );
        if (!owner || owner->getValue() != this) {
            continue;
        }
        owner->setStatus(App::Property::Hidden, true);
        owner->setStatus(App::Property::LockDynamic, true);
        owner->setStatus(App::Property::NoRecompute, true);
        auto* role = dynamic_cast<App::PropertyString*>(
            object->getPropertyByName(App::DocumentTimeline::RolePropertyName)
        );
        if (!role) {
            throw Base::TypeError(
                "MultiTransform timeline metadata properties have incompatible types"
            );
        }
        role->setStatus(App::Property::Hidden, true);
        role->setStatus(App::Property::LockDynamic, true);
        role->setStatus(App::Property::NoRecompute, true);
        owner->setValue(nullptr);
        role->setValue(App::DocumentTimeline::OperationRole);
    }
}

void MultiTransform::onChanged(const App::Property* property)
{
    Transformed::onChanged(property);
    if (property == &Transformations && !isRestoring()) {
        synchronizeTimelineResources();
    }
}

void MultiTransform::onDocumentRestored()
{
    Transformed::onDocumentRestored();
    synchronizeTimelineResources();
}

void MultiTransform::positionBySupport()
{
    PartDesign::Transformed::positionBySupport();
    std::vector<App::DocumentObject*> transFeatures = Transformations.getValues();
    for (auto f : transFeatures) {
        auto transFeature = freecad_cast<PartDesign::Transformed*>(f);
        if (!transFeature) {
            throw Base::TypeError("Transformation features must be subclasses of Transformed");
        }

        transFeature->Placement.setValue(this->Placement.getValue());

        // To avoid that a linked transform feature stays touched after a recompute
        // we have to purge the touched state
        if (this->isRecomputing()) {
            transFeature->purgeTouched();
        }
    }
}

short MultiTransform::mustExecute() const
{
    if (Transformations.isTouched()) {
        return 1;
    }
    return Transformed::mustExecute();
}

const std::list<gp_Trsf> MultiTransform::getTransformations(
    const std::vector<App::DocumentObject*> originals
)
{
    std::vector<App::DocumentObject*> transFeatures = Transformations.getValues();
    LastFailingChildIndex.setValue(-1);
    LastFailingChild.setValue("");

    gp_Pnt cog;
    if (!originals.empty()) {
        // Find centre of gravity of first original
        // FIXME: This method will NOT give the expected result for more than one original!
        if (auto addFeature = freecad_cast<PartDesign::FeatureAddSub*>(originals.front())) {
            TopoDS_Shape original = addFeature->AddSubShape.getShape().getShape();

            GProp_GProps props;
            BRepGProp::VolumeProperties(original, props);
            cog = props.CentreOfMass();
        }
    }

    std::list<gp_Trsf> result;
    std::list<gp_Pnt> cogs;

    int childIndex = 0;
    for (auto const& f : transFeatures) {
        LastFailingChildIndex.setValue(childIndex);
        LastFailingChild.setValue(f && f->getNameInDocument() ? f->getNameInDocument() : "");
        auto transFeature = freecad_cast<PartDesign::Transformed*>(f);
        if (!transFeature) {
            LastFailingChildIndex.setValue(childIndex);
            LastFailingChild.setValue(f && f->getNameInDocument() ? f->getNameInDocument() : "");
            throw Base::TypeError("Transformation features must be subclasses of Transformed");
        }

        std::list<gp_Trsf> newTransformations;
        try {
            newTransformations = transFeature->getTransformations(originals);
            transFeature->GeneratedOccurrenceCount.setValue(
                static_cast<int>(newTransformations.size()));
        }
        catch (...) {
            LastFailingChildIndex.setValue(childIndex);
            LastFailingChild.setValue(
                transFeature->getNameInDocument() ? transFeature->getNameInDocument() : "");
            throw;
        }
        if (result.empty()) {
            // First transformation Feature
            result = newTransformations;
            for (auto nt : newTransformations) {
                cogs.push_back(cog.Transformed(nt));
            }
        }
        else {
            // Retain a copy of the first set of transformations for iterator ot
            // We can't iterate through result if we are also adding elements with push_back()!
            std::list<gp_Trsf> oldTransformations;
            result.swap(oldTransformations);  // empty result to receive new transformations
            std::list<gp_Pnt> oldCogs;
            cogs.swap(oldCogs);  // empty cogs to receive new cogs

            if (transFeature->is<PartDesign::Scaled>()) {
                // Diagonal method
                // Multiply every element in the old transformations' slices with the corresponding
                // element in the newTransformations. Example:
                // a11 a12 a13 a14          b1    a11*b1 a12*b1 a13*b1 a14*b1
                // a21 a22 a23 a24   diag   b2  = a21*b2 a22*b2 a23*b2 a24*b1
                // a31 a23 a33 a34          b3    a31*b3 a23*b3 a33*b3 a34*b1
                // In other words, the length of the result vector is equal to the length of the
                // oldTransformations vector

                if (newTransformations.empty()) {
                    throw Base::ValueError(
                        "Number of occurrences must be a divisor of previous "
                        "number of occurrences"
                    );
                }
                if (oldTransformations.size() % newTransformations.size() != 0) {
                    throw Base::ValueError(
                        "Number of occurrences must be a divisor of previous "
                        "number of occurrences"
                    );
                }

                unsigned sliceLength = oldTransformations.size() / newTransformations.size();
                auto ot = oldTransformations.begin();
                auto oc = oldCogs.begin();

                for (auto const& nt : newTransformations) {
                    for (unsigned s = 0; s < sliceLength; s++) {
                        gp_Trsf trans;
                        double factor = nt.ScaleFactor();  // extract scale factor

                        if (factor > Precision::Confusion()) {
                            trans.SetScale(*oc, factor);  // recreate the scaled transformation to
                                                          // use the correct COG
                            trans = trans * (*ot);
                            cogs.push_back(*oc);  // Scaling does not affect the COG
                        }
                        else {
                            trans = nt * (*ot);
                            cogs.push_back(oc->Transformed(nt));
                        }
                        result.push_back(trans);
                        ++ot;
                        ++oc;
                    }
                }
            }
            else {
                // Multiplication method: Combine the new transformations with the old ones.
                // All old transformations are multiplied with all new ones, so that the length of
                // the result vector is the length of the old and new transformations multiplied.
                // a11 a12         b1    a11*b1 a12*b1 a11*b2 a12*b2 a11*b3 a12*b3
                // a21 a22   mul   b2  = a21*b1 a22*b1 a21*b2 a22*b2 a21*b3 a22*b3
                //                 b3
                for (auto const& nt : newTransformations) {
                    auto oc = oldCogs.begin();

                    for (auto const& ot : oldTransformations) {
                        result.push_back(nt * ot);
                        cogs.push_back(oc->Transformed(nt));
                        ++oc;
                    }
                }
            }
            // What about the Additive method: Take the last (set of) transformations and use them
            // as "originals" for the next transformationFeature, so that something similar to a
            // sweep for transformations could be put together?
        }
        ++childIndex;
    }

    LastFailingChildIndex.setValue(-1);
    LastFailingChild.setValue("");

    return result;
}

}  // namespace PartDesign
