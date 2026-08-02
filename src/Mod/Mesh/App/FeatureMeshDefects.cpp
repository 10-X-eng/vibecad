// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2006 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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
#include <limits>
#include <ranges>

#include "Core/Degeneration.h"
#include "Core/Definitions.h"
#include "Core/Triangulation.h"
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Exception.h>
#include <Base/Tools.h>

#include "FeatureMeshDefects.h"


using namespace Mesh;


namespace
{

bool sameMeshState(const Mesh::MeshObject& first, const Mesh::MeshObject& second)
{
    if (first.getTransform() != second.getTransform()
        || first.countSegments() != second.countSegments()) {
        return false;
    }

    const auto& firstKernel = first.getKernel();
    const auto& secondKernel = second.getKernel();
    const auto& firstPoints = firstKernel.GetPoints();
    const auto& secondPoints = secondKernel.GetPoints();
    const auto& firstFacets = firstKernel.GetFacets();
    const auto& secondFacets = secondKernel.GetFacets();
    if (firstPoints.size() != secondPoints.size() || firstFacets.size() != secondFacets.size()
        || !std::ranges::equal(firstPoints, secondPoints)
        || !std::ranges::equal(
            firstFacets,
            secondFacets,
            [](const MeshCore::MeshFacet& left, const MeshCore::MeshFacet& right) {
                return left._aulPoints[0] == right._aulPoints[0]
                    && left._aulPoints[1] == right._aulPoints[1]
                    && left._aulPoints[2] == right._aulPoints[2];
            }
        )) {
        return false;
    }

    for (unsigned long index = 0; index < first.countSegments(); ++index) {
        if (first.getSegment(index).getIndices() != second.getSegment(index).getIndices()) {
            return false;
        }
    }
    return true;
}

}  // namespace


//===========================================================================
// Defects Feature
//===========================================================================

PROPERTY_SOURCE(Mesh::FixDefects, Mesh::Feature)

FixDefects::FixDefects()
{
    ADD_PROPERTY(Source, (nullptr));
    ADD_PROPERTY(Epsilon, (0));
    suppressibleExt.initExtension(this);
    suppressibleExt.setTimelineResultVisibleWhenSuppressed(true);
}

short FixDefects::mustExecute() const
{
    auto* source = Source.getValue();
    auto* sourceMesh = source ? source->getPropertyByName("Mesh") : nullptr;
    if (Source.isTouched() || Epsilon.isTouched() || suppressibleExt.Suppressed.isTouched()
        || (source && source->isTouched()) || (sourceMesh && sourceMesh->isTouched())) {
        return 1;
    }
    return Mesh::Feature::mustExecute();
}

App::DocumentObjectExecReturn* FixDefects::execute()
{
    return App::DocumentObject::StdReturn;
}

App::DocumentObjectExecReturn* FixDefects::loadSourceMesh(MeshObject& mesh) const
{
    auto* source = Source.getValue();
    if (!source || source == this || !getDocument()
        || source->getDocument() != getDocument()
        || !source->getNameInDocument()
        || !getDocument()->containsObject(source)
        || !App::DocumentTimeline::isObjectUsableAtCurrentPosition(
            source
        )) {
        return new App::DocumentObjectExecReturn(
            "Source must link to a usable mesh in this document"
        );
    }

    auto* property = dynamic_cast<Mesh::PropertyMeshKernel*>(source->getPropertyByName("Mesh"));
    if (!property) {
        return new App::DocumentObjectExecReturn("The linked source does not provide mesh geometry");
    }

    mesh = property->getValue();
    return nullptr;
}

bool FixDefects::isSuppressed() const
{
    if (suppressibleExt.Suppressed.getValue()) {
        return true;
    }
    const auto* timeline = App::DocumentTimeline::get(getDocument());
    return timeline && !timeline->isOperationActive(this);
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::Repair, Mesh::FixDefects)

const App::PropertyIntegerConstraint::Constraints Repair::nonNegativeInteger = {
    0,
    std::numeric_limits<int>::max(),
    1,
};
const App::PropertyIntegerConstraint::Constraints Repair::iterationRange = {1, 100, 1};

Repair::Repair()
{
    ADD_PROPERTY_TYPE(
        HarmonizeNormals,
        (false),
        "Repair",
        App::Prop_None,
        "Make adjacent facet normals consistently oriented"
    );
    ADD_PROPERTY_TYPE(
        RemoveDuplicates,
        (false),
        "Repair",
        App::Prop_None,
        "Remove duplicate points and duplicate facets"
    );
    ADD_PROPERTY_TYPE(RemoveNonManifolds, (false), "Repair", App::Prop_None, "Remove non-manifold edges");
    ADD_PROPERTY_TYPE(
        RemoveNonManifoldPoints,
        (true),
        "Repair",
        App::Prop_None,
        "Also remove non-manifold points when non-manifolds are repaired"
    );
    ADD_PROPERTY_TYPE(
        FixIndices,
        (false),
        "Repair",
        App::Prop_None,
        "Repair invalid mesh indices and neighbourhood data"
    );
    ADD_PROPERTY_TYPE(
        FixDegenerations,
        (false),
        "Repair",
        App::Prop_None,
        "Remove degenerated facets using Epsilon"
    );
    ADD_PROPERTY_TYPE(
        FixSelfIntersections,
        (false),
        "Repair",
        App::Prop_None,
        "Repair self-intersecting facets"
    );
    ADD_PROPERTY_TYPE(
        RemoveFolds,
        (false),
        "Repair",
        App::Prop_None,
        "Remove folds and fold-overs on the mesh surface"
    );
    ADD_PROPERTY_TYPE(
        FillHolesMaxEdges,
        (0),
        "Repair",
        App::Prop_None,
        "Fill boundary holes with no more than this many edges; zero disables hole filling"
    );
    ADD_PROPERTY_TYPE(
        Repeat,
        (false),
        "Repair",
        App::Prop_None,
        "Repeat the enabled repair passes for MaxIterations"
    );
    ADD_PROPERTY_TYPE(
        MaxIterations,
        (10),
        "Repair",
        App::Prop_None,
        "Maximum number of complete repair passes when Repeat is enabled"
    );
    FillHolesMaxEdges.setConstraints(&nonNegativeInteger);
    MaxIterations.setConstraints(&iterationRange);
    Epsilon.setValue(MeshCore::MeshDefinitions::_fMinPointDistanceP2);
}

short Repair::mustExecute() const
{
    if (HarmonizeNormals.isTouched() || RemoveDuplicates.isTouched()
        || RemoveNonManifolds.isTouched() || RemoveNonManifoldPoints.isTouched()
        || FixIndices.isTouched() || FixDegenerations.isTouched()
        || FixSelfIntersections.isTouched() || RemoveFolds.isTouched()
        || FillHolesMaxEdges.isTouched() || Repeat.isTouched() || MaxIterations.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* Repair::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (isSuppressed()) {
        Mesh.setValue(mesh);
        return App::DocumentObject::StdReturn;
    }

    const bool selected = HarmonizeNormals.getValue() || RemoveDuplicates.getValue()
        || RemoveNonManifolds.getValue() || FixIndices.getValue() || FixDegenerations.getValue()
        || FixSelfIntersections.getValue() || RemoveFolds.getValue()
        || FillHolesMaxEdges.getValue() > 0;
    if (!selected) {
        return new App::DocumentObjectExecReturn("Enable at least one mesh repair pass");
    }

    const int iterations = Repeat.getValue() ? MaxIterations.getValue() : 1;
    try {
        for (int iteration = 0; iteration < iterations; ++iteration) {
            const MeshObject before = mesh;
            if (HarmonizeNormals.getValue()) {
                mesh.harmonizeNormals();
            }
            if (RemoveDuplicates.getValue()) {
                mesh.removeDuplicatedPoints();
                mesh.removeDuplicatedFacets();
            }
            if (RemoveNonManifolds.getValue()) {
                mesh.removeNonManifolds();
                if (RemoveNonManifoldPoints.getValue()) {
                    mesh.removeNonManifoldPoints();
                }
            }
            if (FixIndices.getValue()) {
                mesh.validateIndices();
            }
            if (FixDegenerations.getValue()) {
                mesh.validateDegenerations(static_cast<float>(Epsilon.getValue()));
            }
            if (FixSelfIntersections.getValue()) {
                mesh.removeSelfIntersections();
            }
            if (RemoveFolds.getValue()) {
                mesh.removeFoldsOnSurface();
            }
            if (FillHolesMaxEdges.getValue() > 0) {
                MeshCore::FlatTriangulator triangulator;
                triangulator.SetVerifier(new MeshCore::TriangulationVerifierV2);
                mesh.fillupHoles(
                    static_cast<unsigned long>(FillHolesMaxEdges.getValue()),
                    0,
                    triangulator
                );
            }
            if (sameMeshState(before, mesh)) {
                break;
            }
        }
    }
    catch (const Base::Exception& error) {
        return new App::DocumentObjectExecReturn(error.what());
    }

    if (mesh.countFacets() == 0) {
        return new App::DocumentObjectExecReturn("Mesh repair produced an empty result");
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::HarmonizeNormals, Mesh::FixDefects)

HarmonizeNormals::HarmonizeNormals() = default;

App::DocumentObjectExecReturn* HarmonizeNormals::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        mesh.harmonizeNormals();
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::FlipNormals, Mesh::FixDefects)

FlipNormals::FlipNormals() = default;

App::DocumentObjectExecReturn* FlipNormals::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        mesh.flipNormals();
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::FixNonManifolds, Mesh::FixDefects)

FixNonManifolds::FixNonManifolds()
{
    ADD_PROPERTY_TYPE(
        RemoveNonManifoldPoints,
        (false),
        "Repair",
        App::Prop_None,
        "Also remove non-manifold points after repairing non-manifold edges"
    );
}

short FixNonManifolds::mustExecute() const
{
    if (RemoveNonManifoldPoints.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* FixNonManifolds::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        mesh.removeNonManifolds();
        if (RemoveNonManifoldPoints.getValue()) {
            mesh.removeNonManifoldPoints();
        }
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::FixDuplicatedFaces, Mesh::FixDefects)

FixDuplicatedFaces::FixDuplicatedFaces() = default;

App::DocumentObjectExecReturn* FixDuplicatedFaces::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        mesh.removeDuplicatedFacets();
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::FixDuplicatedPoints, Mesh::FixDefects)

FixDuplicatedPoints::FixDuplicatedPoints() = default;

App::DocumentObjectExecReturn* FixDuplicatedPoints::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        mesh.removeDuplicatedPoints();
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::FixDegenerations, Mesh::FixDefects)

FixDegenerations::FixDegenerations() = default;

App::DocumentObjectExecReturn* FixDegenerations::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        mesh.validateDegenerations(static_cast<float>(Epsilon.getValue()));
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::FixDeformations, Mesh::FixDefects)

FixDeformations::FixDeformations()
{
    ADD_PROPERTY(MaxAngle, (5.0F));
}

short FixDeformations::mustExecute() const
{
    if (MaxAngle.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* FixDeformations::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        const float maxAngle = Base::toRadians(MaxAngle.getValue());
        mesh.validateDeformations(maxAngle, static_cast<float>(Epsilon.getValue()));
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::FixIndices, Mesh::FixDefects)

FixIndices::FixIndices() = default;

App::DocumentObjectExecReturn* FixIndices::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        mesh.validateIndices();
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::FillHoles, Mesh::FixDefects)

const char* FillHoles::MethodEnums[] = {"Constrained Delaunay", "Flat", nullptr};

FillHoles::FillHoles()
{
    ADD_PROPERTY(FillupHolesOfLength, (0));
    ADD_PROPERTY(MaxArea, (0.1F));
    ADD_PROPERTY_TYPE(
        Method,
        (0L),
        "Repair",
        App::Prop_None,
        "Triangulation method used to close each qualifying boundary"
    );
    Method.setEnums(MethodEnums);
}

short FillHoles::mustExecute() const
{
    if (FillupHolesOfLength.isTouched() || MaxArea.isTouched() || Method.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* FillHoles::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        if (Method.getValue() == 1) {
            MeshCore::FlatTriangulator triangulator;
            triangulator.SetVerifier(new MeshCore::TriangulationVerifierV2);
            mesh.fillupHoles(FillupHolesOfLength.getValue(), 0, triangulator);
        }
        else {
            MeshCore::ConstraintDelaunayTriangulator triangulator(
                static_cast<float>(MaxArea.getValue())
            );
            mesh.fillupHoles(FillupHolesOfLength.getValue(), 1, triangulator);
        }
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}

// ----------------------------------------------------------------------

PROPERTY_SOURCE(Mesh::RemoveComponents, Mesh::FixDefects)

RemoveComponents::RemoveComponents()
{
    ADD_PROPERTY(RemoveCompOfSize, (0));
}

short RemoveComponents::mustExecute() const
{
    if (RemoveCompOfSize.isTouched()) {
        return 1;
    }
    return FixDefects::mustExecute();
}

App::DocumentObjectExecReturn* RemoveComponents::execute()
{
    MeshObject mesh;
    if (auto* error = loadSourceMesh(mesh)) {
        return error;
    }
    if (!isSuppressed()) {
        mesh.removeComponents(RemoveCompOfSize.getValue());
    }
    Mesh.setValue(mesh);
    return App::DocumentObject::StdReturn;
}
