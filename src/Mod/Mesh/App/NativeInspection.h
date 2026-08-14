// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <array>
#include <cstddef>
#include <utility>
#include <vector>

#include <Base/Vector3D.h>

#include "Core/Definitions.h"

namespace Mesh
{

class MeshObject;

struct NativeInspectionFinding
{
    std::size_t count {};
    std::vector<unsigned long> sampleIndices;
};

struct NativeInspectionPairFinding
{
    std::size_t count {};
    std::vector<std::pair<unsigned long, unsigned long>> samplePairs;
};

struct NativeMeshInspection
{
    unsigned long pointCount {};
    unsigned long edgeCount {};
    unsigned long facetCount {};
    unsigned long componentCount {};
    double surfaceArea {};
    double volume {};
    bool solid {};
    unsigned long openEdgeCount {};

    NativeInspectionFinding nonUniformOrientation;
    NativeInspectionPairFinding nonManifoldEdges;
    NativeInspectionFinding nonManifoldPoints;
    NativeInspectionFinding facetIndicesOutOfRange;
    NativeInspectionFinding pointIndicesOutOfRange;
    NativeInspectionFinding corruptedFacets;
    NativeInspectionFinding invalidNeighbourhood;
    NativeInspectionFinding degeneratedFacets;
    NativeInspectionFinding duplicatedFacets;
    NativeInspectionFinding duplicatedPoints;
    NativeInspectionFinding nanPoints;
    NativeInspectionPairFinding selfIntersections;
    NativeInspectionFinding surfaceFolds;
    NativeInspectionFinding boundaryFolds;
    NativeInspectionFinding surfaceFoldOvers;
};

struct NativeFacetInspection
{
    MeshCore::FacetIndex index {};
    std::array<MeshCore::PointIndex, 3> pointIndices {};
    std::array<long, 3> neighbourIndices {};
    std::array<Base::Vector3d, 3> points;
    Base::Vector3d normal;
    double area {};
    double aspectRatio {};
    double roundness {};
};

NativeMeshInspection inspectNativeMesh(
    const MeshObject& mesh,
    float degenerationTolerance,
    std::size_t sampleLimit
);

std::vector<NativeFacetInspection> inspectNativeFacets(
    const MeshObject& mesh,
    const std::vector<MeshCore::FacetIndex>& indices
);

}  // namespace Mesh
