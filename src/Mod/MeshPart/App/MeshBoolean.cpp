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
#include <utility>
#include <vector>

#include <BRepClass3d_SolidClassifier.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRep_Tool.hxx>
#include <BRep_Builder.hxx>
#include <BRepGProp.hxx>
#include <BRepLib.hxx>
#include <GProp_GProps.hxx>
#include <Precision.hxx>
#include <Standard_Failure.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopAbs_State.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS_Solid.hxx>
#include <TopoDS_Vertex.hxx>

#include <App/ComplexGeoData.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Exception.h>
#include <Base/Vector3D.h>
#include <Mod/Mesh/App/Mesh.h>
#include <Mod/Part/App/FCBRepAlgoAPI_Common.h>
#include <Mod/Part/App/FCBRepAlgoAPI_Cut.h>
#include <Mod/Part/App/FCBRepAlgoAPI_Fuse.h>
#include <Mod/Part/App/TopoShape.h>

#include "MeshBoolean.h"
#include "Mesher.h"


namespace
{

constexpr double sewingTolerance = 1.0e-6;

struct ClosedShell
{
    TopoDS_Shell outwardShell;
    TopoDS_Solid enclosedVolume;
    gp_Pnt samplePoint;
    std::vector<bool> containedBy;
    std::size_t depth = 0;
};

std::string sourceName(const App::DocumentObject& source)
{
    const char* label = source.Label.getValue();
    if (label && *label) {
        return label;
    }
    const char* name = source.getNameInDocument();
    return name ? name : "mesh";
}

TopoDS_Shape meshSolidShape(const Mesh::Feature& source, const char* propertyName)
{
    const Mesh::MeshObject& mesh = source.Mesh.getValue();
    const std::string name = sourceName(source);
    if (mesh.countFacets() == 0) {
        throw Base::ValueError(
            std::string(propertyName) + " '" + name + "' is empty. Select a closed mesh with facets."
        );
    }
    if (!mesh.isSolid()) {
        throw Base::ValueError(
            std::string(propertyName) + " '" + name
            + "' is open or non-manifold. Repair it with the Mesh analysis "
              "tools before using a boolean."
        );
    }
    if (mesh.hasSelfIntersections()) {
        throw Base::ValueError(
            std::string(propertyName) + " '" + name
            + "' has self-intersections. Repair it with the Mesh analysis "
              "tools before using a boolean."
        );
    }

    std::vector<Base::Vector3d> points;
    std::vector<Data::ComplexGeoData::Facet> facets;
    mesh.getFaces(points, facets, 0.0);
    if (points.empty() || facets.empty()) {
        throw Base::ValueError(
            std::string(propertyName) + " '" + name + "' did not provide usable triangle topology."
        );
    }

    Part::TopoShape faces;
    faces.setFaces(points, facets, sewingTolerance);
    if (faces.isNull()) {
        throw Base::RuntimeError(
            std::string(propertyName) + " '" + name + "' could not be converted to OCC faces."
        );
    }
    faces.sewShape(sewingTolerance);
    if (faces.isNull()) {
        throw Base::RuntimeError(
            std::string(propertyName) + " '" + name + "' could not be sewn into a shell."
        );
    }

    TopExp_Explorer shells(faces.getShape(), TopAbs_SHELL);
    if (!shells.More()) {
        throw Base::ValueError(
            std::string(propertyName) + " '" + name
            + "' did not form a closed shell. Repair its boundaries before "
              "using a boolean."
        );
    }
    std::vector<ClosedShell> closedShells;
    while (shells.More()) {
        const TopoDS_Shell shell = TopoDS::Shell(shells.Current());
        if (!shell.Closed()) {
            throw Base::ValueError(
                std::string(propertyName) + " '" + name
                + "' produced an open OCC shell. Repair the source mesh "
                  "before using a boolean."
            );
        }

        BRepBuilderAPI_MakeSolid solidMaker(shell);
        if (!solidMaker.IsDone()) {
            throw Base::RuntimeError(
                std::string(propertyName) + " '" + name
                + "' contains a shell that could not be promoted to a "
                  "solid."
            );
        }
        TopoDS_Solid enclosedVolume = solidMaker.Solid();
        if (enclosedVolume.IsNull() || !BRepLib::OrientClosedSolid(enclosedVolume)
            || !BRepCheck_Analyzer(enclosedVolume).IsValid()) {
            throw Base::RuntimeError(
                std::string(propertyName) + " '" + name
                + "' produced an invalid OCC solid. Repair "
                  "self-intersections and non-manifold facets before using "
                  "a boolean."
            );
        }

        TopExp_Explorer orientedShells(enclosedVolume, TopAbs_SHELL);
        if (!orientedShells.More()) {
            throw Base::RuntimeError(
                std::string(propertyName) + " '" + name
                + "' contains a closed volume without a usable shell."
            );
        }
        const TopoDS_Shell outwardShell = TopoDS::Shell(orientedShells.Current());
        orientedShells.Next();
        if (orientedShells.More()) {
            throw Base::RuntimeError(
                std::string(propertyName) + " '" + name
                + "' produced an ambiguous shell during solid "
                  "classification."
            );
        }

        TopExp_Explorer vertices(outwardShell, TopAbs_VERTEX);
        if (!vertices.More()) {
            throw Base::RuntimeError(
                std::string(propertyName) + " '" + name + "' contains a shell without vertices."
            );
        }
        const gp_Pnt samplePoint = BRep_Tool::Pnt(TopoDS::Vertex(vertices.Current()));
        closedShells.push_back({
            outwardShell,
            std::move(enclosedVolume),
            samplePoint,
            {},
            0,
        });
        shells.Next();
    }

    for (std::size_t inner = 0; inner < closedShells.size(); ++inner) {
        auto& classified = closedShells[inner];
        classified.containedBy.assign(closedShells.size(), false);
        for (std::size_t outer = 0; outer < closedShells.size(); ++outer) {
            if (inner == outer) {
                continue;
            }
            BRepClass3d_SolidClassifier classifier(closedShells[outer].enclosedVolume);
            classifier.Perform(classified.samplePoint, sewingTolerance);
            if (classifier.State() == TopAbs_ON) {
                throw Base::ValueError(
                    std::string(propertyName) + " '" + name
                    + "' contains touching or coincident closed shells. "
                      "Repair the mesh into unambiguous solid regions "
                      "(shell "
                    + std::to_string(inner + 1) + " against shell " + std::to_string(outer + 1) + ")."
                );
            }
            if (classifier.State() == TopAbs_IN) {
                classified.containedBy[outer] = true;
                ++classified.depth;
            }
        }
    }

    std::vector<TopoDS_Solid> solids;
    for (std::size_t outer = 0; outer < closedShells.size(); ++outer) {
        const auto& boundary = closedShells[outer];
        if (boundary.depth % 2 != 0) {
            continue;
        }

        BRepBuilderAPI_MakeSolid solidMaker(boundary.outwardShell);
        for (std::size_t inner = 0; inner < closedShells.size(); ++inner) {
            const auto& cavity = closedShells[inner];
            if (cavity.depth == boundary.depth + 1 && cavity.containedBy[outer]) {
                solidMaker.Add(TopoDS::Shell(cavity.outwardShell.Reversed()));
            }
        }
        if (!solidMaker.IsDone()) {
            throw Base::RuntimeError(
                std::string(propertyName) + " '" + name
                + "' could not be assembled from its nested shells."
            );
        }
        TopoDS_Solid solid = solidMaker.Solid();
        if (solid.IsNull() || !BRepLib::OrientClosedSolid(solid)
            || !BRepCheck_Analyzer(solid).IsValid()) {
            throw Base::RuntimeError(
                std::string(propertyName) + " '" + name
                + "' produced an invalid solid while preserving nested "
                  "cavities."
            );
        }
        solids.push_back(std::move(solid));
    }

    if (solids.size() == 1) {
        return solids.front();
    }
    BRep_Builder builder;
    TopoDS_Compound compound;
    builder.MakeCompound(compound);
    for (const auto& solid : solids) {
        builder.Add(compound, solid);
    }
    if (!BRepCheck_Analyzer(compound).IsValid()) {
        throw Base::RuntimeError(
            std::string(propertyName) + " '" + name + "' produced invalid disconnected solid topology."
        );
    }
    return compound;
}

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
        || suppressibleExt.Suppressed.isTouched() || sourceTouched(Source1.getValue())
        || sourceTouched(Source2.getValue())) {
        return 1;
    }
    return Mesh::Feature::mustExecute();
}

App::DocumentObjectExecReturn* MeshPart::Boolean::execute()
{
    try {
        if (isSuppressed()) {
            Mesh.setValue(Mesh::MeshObject());
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

        const TopoDS_Shape first = meshSolidShape(*source1, "Source1");
        const TopoDS_Shape second = meshSolidShape(*source2, "Source2");
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
