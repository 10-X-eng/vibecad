// SPDX-License-Identifier: LGPL-2.1-or-later

#include "MeshSegmentationTools.h"

std::vector<MeshGui::DetectedMeshSegment> MeshGui::detectCurvatureSegments(
    const Mesh::MeshObject& mesh,
    const std::vector<CurvatureSegmentRequest>& requests,
    unsigned int smoothingSteps
)
{
    return Mesh::detectCurvatureSegments(mesh, requests, smoothingSteps);
}

std::vector<MeshGui::DetectedMeshSegment> MeshGui::detectBestFitSegments(
    const Mesh::MeshObject& mesh,
    const std::vector<BestFitSegmentRequest>& requests
)
{
    return Mesh::detectBestFitSegments(mesh, requests);
}

std::vector<MeshGui::DetectedMeshSegment> MeshGui::detectPlanarSegments(
    const Mesh::MeshObject& mesh,
    unsigned long minimumFacets,
    float curvatureTolerance,
    float distanceTolerance,
    unsigned int smoothingSteps
)
{
    return Mesh::detectPlanarSegments(
        mesh,
        minimumFacets,
        curvatureTolerance,
        distanceTolerance,
        smoothingSteps
    );
}
