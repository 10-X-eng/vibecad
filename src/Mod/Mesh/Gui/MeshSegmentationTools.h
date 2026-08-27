// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <Mod/Mesh/App/SegmentationTools.h>

#include <Mod/Mesh/MeshGlobal.h>


namespace MeshGui
{

using CurvatureSegmentKind = Mesh::CurvatureSegmentKind;
using CurvatureSegmentRequest = Mesh::CurvatureSegmentRequest;
using BestFitSegmentRequest = Mesh::BestFitSegmentRequest;
using DetectedMeshSegment = Mesh::DetectedMeshSegment;

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
