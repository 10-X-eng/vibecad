// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                               *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 ***************************************************************************/

#include <cmath>
#include <exception>
#include <memory>
#include <numbers>
#include <string>

#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <GProp_GProps.hxx>
#include <Precision.hxx>
#include <Standard_Failure.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS_Shape.hxx>

#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Exception.h>
#include <Mod/Mesh/App/Mesh.h>
#include <Mod/Part/App/FCBRepAlgoAPI_Common.h>
#include <Mod/Part/App/FCBRepAlgoAPI_Cut.h>
#include <Mod/Part/App/FCBRepAlgoAPI_Fuse.h>

#include "MeshBoolean.h"
#include "MeshSolidShape.h"
#include "Mesher.h"


namespace
{

TopoDS_Shape booleanShape(const TopoDS_Shape& first, const TopoDS_Shape& second, const char* operation)
{
    TopoDS_Shape result;
    if (std::string(operation) == "Union") {
        FCBRepAlgoAPI_Fuse algorithm(first, second);
        if (!algorithm.IsDone()) {
            throw Base::RuntimeError("Open CASCADE could not compute the mesh union.");
        }
        result = algorithm.Shape();
    }
    else if (std::string(operation) == "Intersection") {
        FCBRepAlgoAPI_Common algorithm(first, second);
        if (!algorithm.IsDone()) {
            throw Base::RuntimeError("Open CASCADE could not compute the mesh intersection.");
        }
        result = algorithm.Shape();
    }
    else if (std::string(operation) == "Difference") {
        FCBRepAlgoAPI_Cut algorithm(first, second);
        if (!algorithm.IsDone()) {
            throw Base::RuntimeError("Open CASCADE could not compute the mesh difference.");
        }
        result = algorithm.Shape();
    }
    else {
        throw Base::ValueError("Operation must be Union, Intersection, or Difference.");
    }

    if (result.IsNull()) {
        throw Base::RuntimeError(
            std::string("The mesh ") + operation
            + " produced no shape. Check that the source solids overlap as "
              "required by the selected operation."
        );
    }
    TopExp_Explorer solids(result, TopAbs_SOLID);
    if (!solids.More()) {
        throw Base::RuntimeError(
            std::string("The mesh ") + operation
            + " produced no solid volume. Check that the source solids "
              "overlap as required by the selected operation."
        );
    }
    if (!BRepCheck_Analyzer(result).IsValid()) {
        throw Base::RuntimeError(
            std::string("The mesh ") + operation
            + " produced invalid OCC topology. Repair the source meshes and "
              "retry."
        );
    }

    GProp_GProps properties;
    BRepGProp::VolumeProperties(result, properties);
    if (!std::isfinite(properties.Mass())
        || std::abs(properties.Mass()) <= std::pow(Precision::Confusion(), 3)) {
        throw Base::RuntimeError(
            std::string("The mesh ") + operation
            + " produced zero solid volume. Check the relative placement of "
              "the source meshes."
        );
    }
    return result;
}

}  // namespace


const char* MeshPart::Boolean::OperationEnums[] = {"Union", "Intersection", "Difference", nullptr};

PROPERTY_SOURCE(MeshPart::Boolean, Mesh::Feature)

MeshPart::Boolean::Boolean()
{
    suppressibleExt.initExtension(this);
    ADD_PROPERTY_TYPE(
        Source1,
        (nullptr),
        "Boolean",
        App::Prop_None,
        "First closed mesh. Difference keeps this mesh and removes Source2."
    );
    ADD_PROPERTY_TYPE(Source2, (nullptr), "Boolean", App::Prop_None, "Second closed mesh.");
    ADD_PROPERTY_TYPE(
        Operation,
        (0L),
        "Boolean",
        App::Prop_None,
        "Solid operation to apply to Source1 and Source2."
    );
    Operation.setEnums(OperationEnums);
    ADD_PROPERTY_TYPE(
        LinearDeflection,
        (0.1),
        "Meshing",
        App::Prop_None,
        "Maximum linear tessellation deflection. When Relative is true, "
        "this value is interpreted as a relative coefficient."
    );
    ADD_PROPERTY_TYPE(
        AngularDeflection,
        (0.5),
        "Meshing",
        App::Prop_None,
        "Maximum angular tessellation deflection in radians."
    );
    ADD_PROPERTY_TYPE(
        Relative,
        (false),
        "Meshing",
        App::Prop_None,
        "Interpret LinearDeflection relative to the result size."
    );
    ADD_PROPERTY_TYPE(
        UpdateFromSource,
        (true),
        "Boolean",
        App::Prop_None,
        "Recompute the solid boolean when either linked Mesh changes."
    );

    static const App::PropertyFloatConstraint::Constraints angularRange = {0.0, std::numbers::pi, 0.01};
    AngularDeflection.setConstraints(&angularRange);
    Source1.setScope(App::LinkScope::Global);
    Source2.setScope(App::LinkScope::Global);
}

bool MeshPart::Boolean::isSuppressed() const
{
    if (suppressibleExt.Suppressed.getValue()) {
        return true;
    }
    const auto* timeline = App::DocumentTimeline::get(getDocument());
    return timeline && !timeline->isOperationActive(this);
}

short MeshPart::Boolean::mustExecute() const
{
    auto sourceTouched = [](App::DocumentObject* source) {
        auto* mesh = source ? source->getPropertyByName("Mesh") : nullptr;
        auto* placement = source ? source->getPropertyByName("Placement") : nullptr;
        return (source && source->isTouched()) || (mesh && mesh->isTouched())
            || (placement && placement->isTouched());
    };
    if (Source1.isTouched() || Source2.isTouched() || Operation.isTouched()
        || LinearDeflection.isTouched() || AngularDeflection.isTouched() || Relative.isTouched()
        || UpdateFromSource.isTouched() || suppressibleExt.Suppressed.isTouched()
        || sourceTouched(Source1.getValue())
        || sourceTouched(Source2.getValue())) {
        return 1;
    }
    return Mesh::Feature::mustExecute();
}

App::DocumentObjectExecReturn* MeshPart::Boolean::execute()
{
    try {
        if (isSuppressed()) {
            if (UpdateFromSource.getValue()) {
                Mesh.setValue(Mesh::MeshObject());
            }
            return App::DocumentObject::StdReturn;
        }
        if (!UpdateFromSource.getValue()) {
            if (Mesh.getValue().countFacets() == 0) {
                throw Base::RuntimeError("The detached Mesh boolean has no cached result.");
            }
            // The process-isolated worker already validated the exact solid
            // boolean.  A document recompute must not repeat any BREP work.
            return App::DocumentObject::StdReturn;
        }
        const auto* source1 = freecad_cast<const Mesh::Feature*>(Source1.getValue());
        const auto* source2 = freecad_cast<const Mesh::Feature*>(Source2.getValue());
        auto* document = getDocument();
        if (!document) {
            throw Base::RuntimeError("The mesh boolean is not attached to a document.");
        }
        if (source1 == this || source2 == this) {
            throw Base::ValueError("A mesh boolean cannot use itself as a source.");
        }
        if (!source1 || !source2) {
            throw Base::ValueError("Source1 and Source2 must both link to meshes.");
        }
        if (source1 == source2) {
            throw Base::ValueError("Source1 and Source2 must link to distinct meshes.");
        }
        if (source1->getDocument() != document || source2->getDocument() != document
            || !document->containsObject(source1) || !document->containsObject(source2)) {
            throw Base::ValueError("Source1 and Source2 must belong to this document.");
        }
        if (!App::DocumentTimeline::isObjectUsableAtCurrentPosition(source1)
            || !App::DocumentTimeline::isObjectUsableAtCurrentPosition(source2)) {
            throw Base::ValueError("Source1 and Source2 must both be active, unsuppressed mesh "
                                   "operations at the current History position.");
        }

        const double linearDeflection = LinearDeflection.getValue();
        const double angularDeflection = AngularDeflection.getValue();
        if (!std::isfinite(linearDeflection) || linearDeflection <= 0.0) {
            throw Base::ValueError("LinearDeflection must be a finite number greater than zero.");
        }
        if (!std::isfinite(angularDeflection) || angularDeflection <= 0.0
            || angularDeflection > std::numbers::pi) {
            throw Base::ValueError("AngularDeflection must be a finite radian value greater "
                                   "than zero and no greater than pi.");
        }

        const TopoDS_Shape first = MeshPart::solidShapeFromMesh(*source1, 1.0e-6, "Source1");
        const TopoDS_Shape second = MeshPart::solidShapeFromMesh(*source2, 1.0e-6, "Source2");
        const char* operation = Operation.getValueAsString();
        const TopoDS_Shape result = booleanShape(first, second, operation);

        MeshPart::Mesher mesher(result);
        mesher.setMethod(MeshPart::Mesher::Standard);
        mesher.setDeflection(linearDeflection);
        mesher.setAngularDeflection(angularDeflection);
        mesher.setRelative(Relative.getValue());
        mesher.setRegular(true);
        std::unique_ptr<Mesh::MeshObject> output(mesher.createMesh());
        if (!output || output->countFacets() == 0) {
            throw Base::RuntimeError(
                std::string("The mesh ") + operation + " solid could not be tessellated."
            );
        }
        if (!output->isSolid()) {
            throw Base::RuntimeError(
                std::string("The mesh ") + operation
                + " tessellation is not closed. Increase meshing quality or "
                  "repair the source meshes."
            );
        }
        // Mesh::Feature keeps its Placement synchronized with the transform
        // stored on its MeshObject.  Preserve the user's independent result
        // placement when replacing the recomputed mesh; otherwise assigning
        // an identity-transform tessellation resets Placement to identity.
        output->setTransform(Placement.getValue().toMatrix());
        // Copy into the property's stable MeshObject. Replacing its pointer
        // would leave an already-created Python Mesh wrapper bound to stale
        // data after a parametric recompute.
        Mesh.setValue(*output);
        return App::DocumentObject::StdReturn;
    }
    catch (const Base::Exception& error) {
        Mesh.setValue(Mesh::MeshObject());
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (const Standard_Failure& error) {
        Mesh.setValue(Mesh::MeshObject());
        const char* message = error.GetMessageString();
        const std::string detail = std::string("Open CASCADE mesh boolean failed")
            + (message && *message ? std::string(": ") + message : ".");
        return new App::DocumentObjectExecReturn(detail.c_str());
    }
    catch (const std::exception& error) {
        Mesh.setValue(Mesh::MeshObject());
        return new App::DocumentObjectExecReturn(error.what());
    }
    catch (...) {
        Mesh.setValue(Mesh::MeshObject());
        return new App::DocumentObjectExecReturn("Mesh boolean failed unexpectedly");
    }
}
