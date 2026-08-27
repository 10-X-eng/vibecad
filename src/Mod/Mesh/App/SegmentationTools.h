// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <string>
#include <vector>

#include <Mod/Mesh/App/Mesh.h>
#include <Mod/Mesh/MeshGlobal.h>

namespace Mesh
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
    std::vector<float> parameters;
};

struct BestFitSegmentRequest
{
    std::string kind;
    unsigned long minimumFacets;
    float tolerance;
    std::vector<float> initialParameters;
};

struct DetectedMeshSegment
{
    std::string kind;
    std::vector<long> facetIndices;
};

MeshExport std::vector<DetectedMeshSegment> detectCurvatureSegments(
    const MeshObject& mesh,
    const std::vector<CurvatureSegmentRequest>& requests,
    unsigned int smoothingSteps
);

MeshExport std::vector<DetectedMeshSegment> detectBestFitSegments(
    const MeshObject& mesh,
    const std::vector<BestFitSegmentRequest>& requests
);

MeshExport std::vector<DetectedMeshSegment> detectPlanarSegments(
    const MeshObject& mesh,
    unsigned long minimumFacets,
    float curvatureTolerance,
    float distanceTolerance,
    unsigned int smoothingSteps
);

}  // namespace Mesh
