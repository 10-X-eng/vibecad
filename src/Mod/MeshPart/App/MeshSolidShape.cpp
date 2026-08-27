// SPDX-License-Identifier: LGPL-2.1-or-later

#include "MeshSolidShape.h"

#include <cmath>
#include <string>
#include <utility>
#include <vector>

#include <BRepClass3d_SolidClassifier.hxx>
#include <BRepBuilderAPI_MakeSolid.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRep_Tool.hxx>
#include <BRep_Builder.hxx>
#include <BRepLib.hxx>
#include <Precision.hxx>
#include <TopAbs_State.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Shell.hxx>
#include <TopoDS_Solid.hxx>
#include <TopoDS_Vertex.hxx>

#include <App/ComplexGeoData.h>
#include <App/DocumentObject.h>
#include <Base/Exception.h>
#include <Base/Vector3D.h>
#include <Mod/Mesh/App/Mesh.h>
#include <Mod/Mesh/App/MeshFeature.h>
#include <Mod/Part/App/TopoShape.h>


namespace
{

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

}  // namespace


TopoDS_Shape MeshPart::solidShapeFromMesh(
    const Mesh::Feature& source,
    double sewingTolerance,
    const char* propertyName
)
{
    if (!std::isfinite(sewingTolerance) || sewingTolerance < Precision::Confusion()) {
        throw Base::ValueError(
            "Mesh solid sewing tolerance must be finite and no smaller than the OCC confusion "
            "tolerance."
        );
    }
    const std::string role = propertyName && *propertyName ? propertyName : "Source";
    const Mesh::MeshObject& mesh = source.Mesh.getValue();
    const std::string name = sourceName(source);
    if (mesh.countFacets() == 0) {
        throw Base::ValueError(
            role + " '" + name + "' is empty. Select a closed mesh with facets."
        );
    }
    if (!mesh.isSolid()) {
        throw Base::ValueError(
            role + " '" + name
            + "' is open or non-manifold. Repair it with the Mesh analysis tools first."
        );
    }
    if (mesh.hasSelfIntersections()) {
        throw Base::ValueError(
            role + " '" + name
            + "' has self-intersections. Repair it with the Mesh analysis tools first."
        );
    }

    std::vector<Base::Vector3d> points;
    std::vector<Data::ComplexGeoData::Facet> facets;
    mesh.getFaces(points, facets, 0.0);
    if (points.empty() || facets.empty()) {
        throw Base::ValueError(
            role + " '" + name + "' did not provide usable triangle topology."
        );
    }

    Part::TopoShape faces;
    faces.setFaces(points, facets, sewingTolerance);
    if (faces.isNull()) {
        throw Base::RuntimeError(
            role + " '" + name + "' could not be converted to OCC faces."
        );
    }
    faces.sewShape(sewingTolerance);
    if (faces.isNull()) {
        throw Base::RuntimeError(
            role + " '" + name + "' could not be sewn into a shell."
        );
    }

    TopExp_Explorer shells(faces.getShape(), TopAbs_SHELL);
    if (!shells.More()) {
        throw Base::ValueError(
            role + " '" + name
            + "' did not form a closed shell. Repair its boundaries first."
        );
    }
    std::vector<ClosedShell> closedShells;
    while (shells.More()) {
        const TopoDS_Shell shell = TopoDS::Shell(shells.Current());
        if (!shell.Closed()) {
            throw Base::ValueError(
                role + " '" + name
                + "' produced an open OCC shell. Repair the source mesh first."
            );
        }

        BRepBuilderAPI_MakeSolid solidMaker(shell);
        if (!solidMaker.IsDone()) {
            throw Base::RuntimeError(
                role + " '" + name + "' contains a shell that could not become a solid."
            );
        }
        TopoDS_Solid enclosedVolume = solidMaker.Solid();
        if (enclosedVolume.IsNull() || !BRepLib::OrientClosedSolid(enclosedVolume)
            || !BRepCheck_Analyzer(enclosedVolume).IsValid()) {
            throw Base::RuntimeError(
                role + " '" + name
                + "' produced an invalid OCC solid. Repair its topology first."
            );
        }

        TopExp_Explorer orientedShells(enclosedVolume, TopAbs_SHELL);
        if (!orientedShells.More()) {
            throw Base::RuntimeError(
                role + " '" + name + "' contains a closed volume without a usable shell."
            );
        }
        const TopoDS_Shell outwardShell = TopoDS::Shell(orientedShells.Current());
        orientedShells.Next();
        if (orientedShells.More()) {
            throw Base::RuntimeError(
                role + " '" + name + "' produced an ambiguous shell during classification."
            );
        }

        TopExp_Explorer vertices(outwardShell, TopAbs_VERTEX);
        if (!vertices.More()) {
            throw Base::RuntimeError(
                role + " '" + name + "' contains a shell without vertices."
            );
        }
        closedShells.push_back({
            outwardShell,
            std::move(enclosedVolume),
            BRep_Tool::Pnt(TopoDS::Vertex(vertices.Current())),
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
                    role + " '" + name
                    + "' contains touching or coincident closed shells (shell "
                    + std::to_string(inner + 1) + " against shell "
                    + std::to_string(outer + 1) + ")."
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
                role + " '" + name + "' could not be assembled from its nested shells."
            );
        }
        TopoDS_Solid solid = solidMaker.Solid();
        if (solid.IsNull() || !BRepLib::OrientClosedSolid(solid)
            || !BRepCheck_Analyzer(solid).IsValid()) {
            throw Base::RuntimeError(
                role + " '" + name
                + "' produced an invalid solid while preserving nested cavities."
            );
        }
        solids.push_back(std::move(solid));
    }

    if (solids.empty()) {
        throw Base::RuntimeError(role + " '" + name + "' produced no solid volume.");
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
            role + " '" + name + "' produced invalid disconnected solid topology."
        );
    }
    return compound;
}
