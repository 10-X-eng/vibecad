// SPDX-License-Identifier: LGPL-2.1-or-later

#include "FeatureCrossSections.h"

#include <cmath>
#include <utility>
#include <vector>

#include <Precision.hxx>
#include <Standard_Failure.hxx>

#include <App/Document.h>
#include <Base/Exception.h>

#include "CrossSection.h"

using namespace Part;

PROPERTY_SOURCE(Part::CrossSections, Part::Feature)

CrossSections::CrossSections()
{
    ADD_PROPERTY_TYPE(
        Source,
        (nullptr),
        "Cross Section",
        App::Prop_None,
        "Source object and exact selected subelements"
    );
    ADD_PROPERTY_TYPE(
        PlaneNormal,
        (Base::Vector3d(0.0, 0.0, 1.0)),
        "Cross Section",
        App::Prop_None,
        "Direction normal to every section plane"
    );
    ADD_PROPERTY_TYPE(
        PlanePositions,
        (std::vector<double> {}),
        "Cross Section",
        App::Prop_None,
        "Signed section-plane distances along the normalized plane normal"
    );
    allowCrossContainerLink(Source);
}

App::DocumentObjectExecReturn* CrossSections::execute()
{
    try {
        auto* sourceObject = Source.getValue();
        if (!sourceObject) {
            return new App::DocumentObjectExecReturn(
                "No cross-section source is assigned"
            );
        }

        auto normal = PlaneNormal.getValue();
        const double normalLength = normal.Length();
        if (!std::isfinite(normalLength)
            || normalLength <= Precision::Confusion()) {
            return new App::DocumentObjectExecReturn(
                "Cross-section plane normal must be non-zero"
            );
        }
        normal /= normalLength;

        const auto positions = PlanePositions.getValues();
        if (positions.empty()) {
            return new App::DocumentObjectExecReturn(
                "At least one cross-section plane position is required"
            );
        }
        for (double position : positions) {
            if (!std::isfinite(position)) {
                return new App::DocumentObjectExecReturn(
                    "Cross-section plane positions must be finite"
                );
            }
        }

        const auto options =
            ShapeOption::ResolveLink | ShapeOption::Transform;
        const auto& subElements = Source.getSubValues();
        TopoShape sourceShape;
        if (subElements.empty()) {
            sourceShape = Feature::getTopoShape(sourceObject, options);
        }
        else {
            std::vector<TopoShape> selectedShapes;
            selectedShapes.reserve(subElements.size());
            for (const auto& subElement : subElements) {
                auto selectedShape = Feature::getTopoShape(
                    sourceObject,
                    options | ShapeOption::NeedSubElement,
                    subElement.c_str()
                );
                if (selectedShape.isNull() || !selectedShape.isValid()) {
                    return new App::DocumentObjectExecReturn(
                        "A cross-section source subelement is invalid"
                    );
                }
                selectedShapes.push_back(std::move(selectedShape));
            }
            sourceShape.makeElementCompound(
                selectedShapes,
                0,
                TopoShape::SingleShapeCompoundCreationPolicy::returnShape
            );
        }

        if (sourceShape.isNull() || !sourceShape.isValid()) {
            return new App::DocumentObjectExecReturn(
                "Cross-section source shape is invalid"
            );
        }

        std::vector<TopoShape> sectionWires;
        TopoCrossSection crossSection(
            normal.x,
            normal.y,
            normal.z,
            sourceShape,
            "CrossSection"
        );
        for (std::size_t index = 0; index < positions.size(); ++index) {
            crossSection.slice(
                static_cast<int>(index),
                positions[index],
                sectionWires
            );
        }
        if (sectionWires.empty()) {
            return new App::DocumentObjectExecReturn(
                "The configured planes do not intersect the source"
            );
        }

        TopoShape result(0, getDocument()->getStringHasher());
        result.makeElementCompound(
            sectionWires,
            0,
            TopoShape::SingleShapeCompoundCreationPolicy::returnShape
        );
        if (result.isNull() || !result.isValid()) {
            return new App::DocumentObjectExecReturn(
                "Cross-section geometry is invalid"
            );
        }

        Shape.setValue(result);
        copyMaterial(sourceObject);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (const Standard_Failure& error) {
        return new App::DocumentObjectExecReturn(error.GetMessageString());
    }
}
