// SPDX-License-Identifier: LGPL-2.1-or-later
// SPDX-FileCopyrightText: 2026 Morten Vajhøj
// SPDX-FileNotice: Part of the FreeCAD project.

/******************************************************************************
 *                                                                            *
 *   FreeCAD is free software: you can redistribute it and/or modify          *
 *   it under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1            *
 *   of the License, or (at your option) any later version.                   *
 *                                                                            *
 *   FreeCAD is distributed in the hope that it will be useful,               *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty              *
 *   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                  *
 *   See the GNU Lesser General Public License for more details.              *
 *                                                                            *
 *   You should have received a copy of the GNU Lesser General Public         *
 *   License along with FreeCAD. If not, see https://www.gnu.org/licenses     *
 *                                                                            *
 ******************************************************************************/


#include "MassPropertiesObject.h"

#include "MassPropertiesOccurrence.h"
#include "MassPropertiesResult.h"

#include <cmath>
#include <string_view>

#include <App/Application.h>
#include <App/Datums.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <App/PropertyGeo.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Base/Exception.h>
#include <Base/Precision.h>
#include <Base/UnitsApi.h>
#include <Mod/Part/App/PartFeature.h>

PROPERTY_SOURCE(Measure::Result, App::DocumentObject)

namespace
{
constexpr const char* SourcesProperty = "MassPropertySources";
constexpr const char* SourceParentsProperty = "MassPropertySourceParents";
constexpr const char* OccurrencesProperty = "MassPropertyOccurrences";
constexpr const char* OccurrenceDependenciesProperty =
    "MassPropertyOccurrenceDependencies";
constexpr const char* ReferenceProperty = "MassPropertyReference";
constexpr const char* ReferenceParentProperty = "MassPropertyReferenceParent";
constexpr const char* ReferenceOccurrenceProperty =
    "MassPropertyReferenceOccurrence";
constexpr const char* HasReferenceProperty = "MassPropertyHasReference";
constexpr const char* UnitsSchemaProperty = "MassPropertyUnitsSchema";
constexpr const char* CenterOfGravityProperty = "MassPropertyCenterOfGravity";
constexpr const char* CenterOfVolumeProperty = "MassPropertyCenterOfVolume";
constexpr const char* PrincipalAxis1Property = "MassPropertyPrincipalAxis1";
constexpr const char* PrincipalAxis2Property = "MassPropertyPrincipalAxis2";
constexpr const char* PrincipalAxis3Property = "MassPropertyPrincipalAxis3";
constexpr const char* ShowPrincipalAxesProperty = "MassPropertyShowPrincipalAxes";

bool isUsableSource(
    const App::DocumentObject* source,
    const App::Document* document
) noexcept
{
    if (!source || !document || source->getDocument() != document
        || !document->containsObject(source)) {
        return false;
    }
    try {
        if (!App::DocumentTimeline::
                isObjectUsableAtCurrentPosition(source)) {
            return false;
        }

        const auto* linked = source->getLinkedObject(true);
        return !linked || linked == source
            || App::DocumentTimeline::
                   isObjectUsableAtCurrentPosition(linked);
    }
    catch (...) {
        return false;
    }
}

Base::Placement directPlacement(const App::DocumentObject* object)
{
    if (!object) {
        return {};
    }
    const auto* property = dynamic_cast<const App::PropertyPlacement*>(
        object->getPropertyByName("Placement")
    );
    return property ? property->getValue() : Base::Placement();
}

template<typename PropertyType>
PropertyType* property(App::DocumentObject& object, const char* name)
{
    return dynamic_cast<PropertyType*>(object.getPropertyByName(name));
}

void setStringOutput(
    App::DocumentObject& object,
    const char* name,
    const char* group,
    const Base::Quantity& quantity,
    int unitsSchema
)
{
    auto* output = property<App::PropertyString>(object, name);
    if (!output) {
        output = dynamic_cast<App::PropertyString*>(
            object.addDynamicProperty(
                "App::PropertyString",
                name,
                group,
                "Calculated mass-property value",
                App::Prop_Output
            )
        );
    }
    if (!output) {
        throw Base::TypeError(
            "A mass-properties output has an incompatible property type"
        );
    }

    Base::Quantity normalized(quantity);
    if (std::fabs(normalized.getValue())
        < Base::Precision::Confusion()) {
        normalized.setValue(0.0);
    }
    std::string text;
    auto schema = unitsSchema >= 0
        ? Base::UnitsApi::createSchema(
              static_cast<std::size_t>(unitsSchema)
          )
        : nullptr;
    if (schema) {
        text = schema->translate(normalized);
    }
    else {
        text = Base::UnitsApi::schemaTranslate(normalized);
    }
    output->setValue(text.c_str());
    output->setReadOnly(true);
}

void setVectorOutput(
    App::DocumentObject& object,
    const char* name,
    const Base::Vector3d& value
)
{
    auto* output = property<App::PropertyVector>(object, name);
    if (!output) {
        output = dynamic_cast<App::PropertyVector*>(
            object.addDynamicProperty(
                "App::PropertyVector",
                name,
                "Visualization",
                "Persistent mass-properties visualization value",
                App::Prop_Output,
                true
            )
        );
    }
    if (!output) {
        throw Base::TypeError(
            "A mass-properties visualization output has an incompatible type"
        );
    }
    output->setValue(value);
    output->setReadOnly(true);
}

void setBoolOutput(
    App::DocumentObject& object,
    const char* name,
    bool value
)
{
    auto* output = property<App::PropertyBool>(object, name);
    if (!output) {
        output = dynamic_cast<App::PropertyBool*>(
            object.addDynamicProperty(
                "App::PropertyBool",
                name,
                "Visualization",
                "Persistent mass-properties visualization state",
                App::Prop_Output,
                true
            )
        );
    }
    if (!output) {
        throw Base::TypeError(
            "A mass-properties visualization state has an incompatible type"
        );
    }
    output->setValue(value);
    output->setReadOnly(true);
}
}  // namespace

short Measure::Result::mustExecute() const
{
    const auto* sources = dynamic_cast<const App::PropertyLinkSubList*>(
        getPropertyByName(SourcesProperty)
    );
    if (!sources) {
        return App::DocumentObject::mustExecute();
    }
    if (sources->isTouched()) {
        return 1;
    }
    for (auto* source : sources->getValues()) {
        if (source && source->isTouched()) {
            return 1;
        }
    }
    const auto* occurrences =
        dynamic_cast<const App::PropertyLinkSubList*>(
            getPropertyByName(OccurrencesProperty)
        );
    if (occurrences && occurrences->isTouched()) {
        return 1;
    }
    if (occurrences) {
        for (auto* root : occurrences->getValues()) {
            if (root && root->isTouched()) {
                return 1;
            }
        }
    }
    const auto* occurrenceDependencies =
        dynamic_cast<const App::PropertyLinkList*>(
            getPropertyByName(OccurrenceDependenciesProperty)
        );
    if (occurrenceDependencies
        && occurrenceDependencies->isTouched()) {
        return 1;
    }
    if (occurrenceDependencies) {
        for (auto* dependency :
             occurrenceDependencies->getValues()) {
            if (dependency && dependency->isTouched()) {
                return 1;
            }
        }
    }
    const auto* reference = dynamic_cast<const App::PropertyLink*>(
        getPropertyByName(ReferenceProperty)
    );
    if (reference
        && (reference->isTouched()
            || (reference->getValue()
                && reference->getValue()->isTouched()))) {
        return 1;
    }
    const auto* referenceOccurrence =
        dynamic_cast<const App::PropertyLinkSub*>(
            getPropertyByName(ReferenceOccurrenceProperty)
        );
    if (referenceOccurrence
        && (referenceOccurrence->isTouched()
            || (referenceOccurrence->getValue()
                && referenceOccurrence->getValue()->isTouched()))) {
        return 1;
    }
    for (const char* name : {
             SourceParentsProperty,
             ReferenceParentProperty,
             HasReferenceProperty,
             UnitsSchemaProperty,
             "Mode",
         }) {
        const auto* configuration = getPropertyByName(name);
        if (configuration && configuration->isTouched()) {
            return 1;
        }
    }
    return App::DocumentObject::mustExecute();
}

App::DocumentObjectExecReturn* Measure::Result::execute()
{
    auto* sources = property<App::PropertyLinkSubList>(
        *this,
        SourcesProperty
    );
    if (!sources) {
        // Legacy and preview results are intentionally static.
        return App::DocumentObject::StdReturn;
    }

    auto* sourceParents = property<App::PropertyPlacementList>(
        *this,
        SourceParentsProperty
    );
    auto* occurrences = property<App::PropertyLinkSubList>(
        *this,
        OccurrencesProperty
    );
    const auto& sourceObjects = sources->getValues();
    const auto& sourceSubNames = sources->getSubValues();
    const auto& parentPlacements = sourceParents
        ? sourceParents->getValues()
        : std::vector<Base::Placement> {};
    if (sourceObjects.empty()
        || sourceObjects.size() != sourceSubNames.size()) {
        throw Base::ValueError(
            "Mass properties require matching source inputs"
        );
    }
    if (!occurrences
        && sourceObjects.size() != parentPlacements.size()) {
        throw Base::ValueError(
            "Legacy mass properties require matching source and "
            "placement inputs"
        );
    }
    if (occurrences
        && (occurrences->getValues().size()
                != sourceObjects.size()
            || occurrences->getSubValues().size()
                != sourceObjects.size())) {
        throw Base::ValueError(
            "Mass properties require matching source and occurrence "
            "inputs"
        );
    }

    auto* document = getDocument();
    std::vector<MassPropertiesInput> inputs;
    inputs.reserve(sourceObjects.size());
    for (std::size_t index = 0; index < sourceObjects.size(); ++index) {
        auto* source = sourceObjects[index];
        if (!isUsableSource(source, document)) {
            throw Base::ValueError(
                "A mass-properties source is unavailable at the current History position"
            );
        }

        if (occurrences) {
            auto* root = occurrences->getValues()[index];
            const auto& occurrenceSubName =
                occurrences->getSubValues()[index];
            if (!isUsableSource(root, document)) {
                throw Base::ValueError(
                    "A mass-properties occurrence root is unavailable "
                    "at the current History position"
                );
            }

            Measure::Internal::ResolvedOccurrence occurrence;
            if (!Measure::Internal::resolveShapeOccurrence(
                    root,
                    occurrenceSubName,
                    occurrence
                )
                || !Measure::Internal::endpointRepresentsSource(
                    occurrence.endpoint,
                    source
                )) {
                throw Base::ValueError(
                    "A mass-properties occurrence no longer resolves "
                    "to its selected source"
                );
            }

            inputs.push_back({
                occurrence.materialOwner,
                occurrence.shape,
                Base::Placement(),
                source,
                occurrenceSubName,
                Base::Placement(),
            });
        }
        else {
            Part::ShapeOptions options =
                Part::ShapeOption::ResolveLink;
            if (!sourceSubNames[index].empty()) {
                options |= Part::ShapeOption::NeedSubElement;
            }
            App::DocumentObject* materialOwner = nullptr;
            TopoDS_Shape shape = Part::Feature::getShape(
                source,
                options,
                sourceSubNames[index].empty()
                    ? nullptr
                    : sourceSubNames[index].c_str(),
                nullptr,
                &materialOwner
            );
            if (shape.IsNull()) {
                throw Base::ValueError(
                    "A mass-properties source no longer supplies "
                    "measurable geometry"
                );
            }

            const Base::Placement placement =
                parentPlacements[index] * directPlacement(source);
            inputs.push_back({
                materialOwner ? materialOwner : source,
                shape,
                placement,
                source,
                sourceSubNames[index],
                parentPlacements[index],
            });
        }
    }

    MassPropertiesMode mode = MassPropertiesMode::CenterOfGravity;
    if (const auto* modeProperty =
            property<App::PropertyString>(*this, "Mode");
        modeProperty
        && std::string_view(modeProperty->getValue())
            == "Custom") {
        mode = MassPropertiesMode::Custom;
    }

    App::DocumentObject* reference = nullptr;
    Base::Placement referencePlacement;
    const Base::Placement* referencePlacementPointer = nullptr;
    if (mode == MassPropertiesMode::Custom) {
        auto* referenceProperty =
            property<App::PropertyLink>(*this, ReferenceProperty);
        reference = referenceProperty
            ? referenceProperty->getValue()
            : nullptr;
        if (!isUsableSource(reference, document)) {
            throw Base::ValueError(
                "The mass-properties reference is unavailable at the current History position"
            );
        }

        const auto* referenceOccurrence =
            property<App::PropertyLinkSub>(
                *this,
                ReferenceOccurrenceProperty
            );
        if (referenceOccurrence) {
            auto* root = referenceOccurrence->getValue();
            const auto& subNames =
                referenceOccurrence->getSubValues();
            if (!isUsableSource(root, document)
                || subNames.size() != 1) {
                throw Base::ValueError(
                    "The mass-properties reference occurrence is "
                    "invalid"
                );
            }
            const auto members = root->getSubObjectList(
                subNames.front().c_str(),
                nullptr,
                false
            );
            if (members.empty()
                || !Measure::Internal::endpointRepresentsSource(
                    members.back(),
                    reference
                )) {
                throw Base::ValueError(
                    "The mass-properties reference occurrence no "
                    "longer resolves to its selected source"
                );
            }
            referencePlacement =
                App::GeoFeature::getGlobalPlacement(
                    reference,
                    root,
                    subNames.front()
                );
            referencePlacementPointer = &referencePlacement;
        }
        else {
            const auto* hasReference =
                property<App::PropertyBool>(
                    *this,
                    HasReferenceProperty
                );
            const auto* referenceParent =
                property<App::PropertyPlacement>(
                    *this,
                    ReferenceParentProperty
                );
            if (hasReference && hasReference->getValue()
                && referenceParent) {
                referencePlacement =
                    referenceParent->getValue()
                    * directPlacement(reference);
                referencePlacementPointer = &referencePlacement;
            }
        }
    }

    const MassPropertiesData data = CalculateMassProperties(
        inputs,
        mode,
        reference,
        referencePlacementPointer
    );
    int unitsSchema = -1;
    if (const auto* units = property<App::PropertyInteger>(
            *this,
            UnitsSchemaProperty
        )) {
        unitsSchema = units->getValue();
    }

    setStringOutput(*this, "Volume", "Physical Properties", data.volume, unitsSchema);
    setStringOutput(*this, "Mass", "Physical Properties", data.mass, unitsSchema);
    setStringOutput(*this, "Density", "Physical Properties", data.density, unitsSchema);
    setStringOutput(
        *this,
        "SurfaceArea",
        "Physical Properties",
        data.surfaceArea,
        unitsSchema
    );
    setStringOutput(
        *this,
        "CenterOfGravityX",
        "Center of Gravity",
        Base::Quantity(data.cog.x, Base::Unit::Length),
        unitsSchema
    );
    setStringOutput(
        *this,
        "CenterOfGravityY",
        "Center of Gravity",
        Base::Quantity(data.cog.y, Base::Unit::Length),
        unitsSchema
    );
    setStringOutput(
        *this,
        "CenterOfGravityZ",
        "Center of Gravity",
        Base::Quantity(data.cog.z, Base::Unit::Length),
        unitsSchema
    );
    setStringOutput(
        *this,
        "CenterOfVolumeX",
        "Center of Volume",
        Base::Quantity(data.cov.x, Base::Unit::Length),
        unitsSchema
    );
    setStringOutput(
        *this,
        "CenterOfVolumeY",
        "Center of Volume",
        Base::Quantity(data.cov.y, Base::Unit::Length),
        unitsSchema
    );
    setStringOutput(
        *this,
        "CenterOfVolumeZ",
        "Center of Volume",
        Base::Quantity(data.cov.z, Base::Unit::Length),
        unitsSchema
    );

    const bool hasAxisSelection =
        mode == MassPropertiesMode::Custom
        && reference
        && reference->isDerivedFrom<App::Line>();
    if (hasAxisSelection) {
        setStringOutput(
            *this,
            "AxisInertia",
            "Inertia",
            Base::Quantity(data.axisInertia, Base::Unit::Inertia),
            unitsSchema
        );
    }
    else {
        setStringOutput(
            *this,
            "InertiaJox",
            "Inertia",
            Base::Quantity(data.inertiaJo.x, Base::Unit::Inertia),
            unitsSchema
        );
        setStringOutput(
            *this,
            "InertiaJoy",
            "Inertia",
            Base::Quantity(data.inertiaJo.y, Base::Unit::Inertia),
            unitsSchema
        );
        setStringOutput(
            *this,
            "InertiaJoz",
            "Inertia",
            Base::Quantity(data.inertiaJo.z, Base::Unit::Inertia),
            unitsSchema
        );
        setStringOutput(
            *this,
            "InertiaJxy",
            "Inertia",
            Base::Quantity(data.inertiaJCross.x, Base::Unit::Inertia),
            unitsSchema
        );
        setStringOutput(
            *this,
            "InertiaJzx",
            "Inertia",
            Base::Quantity(data.inertiaJCross.y, Base::Unit::Inertia),
            unitsSchema
        );
        setStringOutput(
            *this,
            "InertiaJzy",
            "Inertia",
            Base::Quantity(data.inertiaJCross.z, Base::Unit::Inertia),
            unitsSchema
        );
        setStringOutput(
            *this,
            "InertiaJx",
            "Inertia",
            Base::Quantity(data.inertiaJ.x, Base::Unit::Inertia),
            unitsSchema
        );
        setStringOutput(
            *this,
            "InertiaJy",
            "Inertia",
            Base::Quantity(data.inertiaJ.y, Base::Unit::Inertia),
            unitsSchema
        );
        setStringOutput(
            *this,
            "InertiaJz",
            "Inertia",
            Base::Quantity(data.inertiaJ.z, Base::Unit::Inertia),
            unitsSchema
        );
    }

    setVectorOutput(*this, CenterOfGravityProperty, data.cog);
    setVectorOutput(*this, CenterOfVolumeProperty, data.cov);
    setVectorOutput(*this, PrincipalAxis1Property, data.principalAxis1);
    setVectorOutput(*this, PrincipalAxis2Property, data.principalAxis2);
    setVectorOutput(*this, PrincipalAxis3Property, data.principalAxis3);
    setBoolOutput(
        *this,
        ShowPrincipalAxesProperty,
        !hasAxisSelection
    );
    return App::DocumentObject::StdReturn;
}
