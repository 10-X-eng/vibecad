// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>
#include <vector>

#include <Mod/Mesh/App/Mesh.h>

#include <Mod/Mesh/MeshGlobal.h>


namespace MeshGui
{

enum class CurvatureSegmentKind
{
    Plane,
    Cylinder,
    Sphere,
    Freeform,
};

struct CurvatureSegmentRequest
{
    CurvatureSegmentKind kind;
    unsigned long minimumFacets;
    // Plane: tolerance. Cylinder: curvature, flat tolerance, curved tolerance.
    // Sphere: curvature, tolerance. Freeform: maximum curvature, minimum
    // curvature, maximum tolerance, minimum tolerance.
    std::vector<float> parameters;
};

struct BestFitSegmentRequest
{
    std::string kind;
    unsigned long minimumFacets;
    float tolerance;
    // Empty uses automatic initialization. Explicit Plane, Cylinder, and
    // Sphere fits contain 6, 7, and 4 values respectively.
    std::vector<float> initialParameters;
};

struct DetectedMeshSegment
{
    std::string kind;
    std::vector<long> facetIndices;
};

MeshGuiExport std::vector<DetectedMeshSegment> detectCurvatureSegments(
    const Mesh::MeshObject& mesh,
    const std::vector<CurvatureSegmentRequest>& requests,
    unsigned int smoothingSteps
);

MeshGuiExport std::vector<DetectedMeshSegment> detectBestFitSegments(
    const Mesh::MeshObject& mesh,
    const std::vector<BestFitSegmentRequest>& requests
);

MeshGuiExport std::vector<DetectedMeshSegment> detectPlanarSegments(
    const Mesh::MeshObject& mesh,
    unsigned long minimumFacets,
    float curvatureTolerance,
    float distanceTolerance,
    unsigned int smoothingSteps
);

}  // namespace MeshGui
