// SPDX-License-Identifier: LGPL-2.1-or-later

#include "NativeInspection.h"

#include <algorithm>
#include <ranges>

#include <Base/Converter.h>

#include "Core/Degeneration.h"
#include "Core/Evaluation.h"
#include "Core/MeshKernel.h"
#include "Mesh.h"

namespace
{

template<typename Index>
Mesh::NativeInspectionFinding finding(
    const std::vector<Index>& indices,
    std::size_t sampleLimit
)
{
    Mesh::NativeInspectionFinding result;
    result.count = indices.size();
    const std::size_t count = std::min(indices.size(), sampleLimit);
    result.sampleIndices.reserve(count);
    std::ranges::transform(
        indices | std::views::take(count),
        std::back_inserter(result.sampleIndices),
        [](Index index) {
            return static_cast<unsigned long>(index);
        }
    );
    return result;
}

Mesh::NativeInspectionPairFinding pairFinding(
    const std::vector<std::pair<MeshCore::FacetIndex, MeshCore::FacetIndex>>& pairs,
    std::size_t sampleLimit
)
{
    Mesh::NativeInspectionPairFinding result;
    result.count = pairs.size();
    const std::size_t count = std::min(pairs.size(), sampleLimit);
    result.samplePairs.reserve(count);
    std::ranges::transform(
        pairs | std::views::take(count),
        std::back_inserter(result.samplePairs),
        [](const auto& pair) {
            return std::pair<unsigned long, unsigned long> {
                static_cast<unsigned long>(pair.first),
                static_cast<unsigned long>(pair.second),
            };
        }
    );
    return result;
}

template<typename Evaluation>
auto evaluatedIndices(Evaluation& evaluation)
{
    (void)evaluation.Evaluate();
    return evaluation.GetIndices();
}

}  // namespace

Mesh::NativeMeshInspection Mesh::inspectNativeMesh(
    const MeshObject& mesh,
    float degenerationTolerance,
    std::size_t sampleLimit
)
{
    const MeshCore::MeshKernel& kernel = mesh.getKernel();
    NativeMeshInspection result;
    result.pointCount = kernel.CountPoints();
    result.edgeCount = kernel.CountEdges();
    result.facetCount = kernel.CountFacets();
    result.componentCount = mesh.countComponents();
    result.surfaceArea = mesh.getSurface();
    result.volume = mesh.getVolume();

    MeshCore::MeshEvalSolid solid(kernel);
    result.solid = solid.Evaluate();
    for (const auto& facet : kernel.GetFacets()) {
        result.openEdgeCount += facet.CountOpenEdges();
    }

    MeshCore::MeshEvalOrientation orientation(kernel);
    result.nonUniformOrientation = finding(orientation.GetIndices(), sampleLimit);

    MeshCore::MeshEvalTopology topology(kernel);
    (void)topology.Evaluate();
    result.nonManifoldEdges = pairFinding(topology.GetIndices(), sampleLimit);

    MeshCore::MeshEvalPointManifolds pointManifolds(kernel);
    result.nonManifoldPoints = finding(evaluatedIndices(pointManifolds), sampleLimit);

    MeshCore::MeshEvalRangeFacet facetRange(kernel);
    result.facetIndicesOutOfRange = finding(evaluatedIndices(facetRange), sampleLimit);

    MeshCore::MeshEvalRangePoint pointRange(kernel);
    result.pointIndicesOutOfRange = finding(evaluatedIndices(pointRange), sampleLimit);

    MeshCore::MeshEvalCorruptedFacets corrupted(kernel);
    result.corruptedFacets = finding(evaluatedIndices(corrupted), sampleLimit);

    MeshCore::MeshEvalNeighbourhood neighbourhood(kernel);
    result.invalidNeighbourhood = finding(evaluatedIndices(neighbourhood), sampleLimit);

    MeshCore::MeshEvalDegeneratedFacets degenerated(kernel, degenerationTolerance);
    result.degeneratedFacets = finding(evaluatedIndices(degenerated), sampleLimit);

    MeshCore::MeshEvalDuplicateFacets duplicatedFacets(kernel);
    result.duplicatedFacets = finding(evaluatedIndices(duplicatedFacets), sampleLimit);

    MeshCore::MeshEvalDuplicatePoints duplicatedPoints(kernel);
    result.duplicatedPoints = finding(evaluatedIndices(duplicatedPoints), sampleLimit);

    MeshCore::MeshEvalNaNPoints nanPoints(kernel);
    result.nanPoints = finding(evaluatedIndices(nanPoints), sampleLimit);

    MeshCore::MeshEvalSelfIntersection selfIntersection(kernel);
    std::vector<std::pair<MeshCore::FacetIndex, MeshCore::FacetIndex>> intersections;
    selfIntersection.GetIntersections(intersections);
    result.selfIntersections = pairFinding(intersections, sampleLimit);

    MeshCore::MeshEvalFoldsOnSurface surfaceFolds(kernel);
    result.surfaceFolds = finding(evaluatedIndices(surfaceFolds), sampleLimit);

    MeshCore::MeshEvalFoldsOnBoundary boundaryFolds(kernel);
    result.boundaryFolds = finding(evaluatedIndices(boundaryFolds), sampleLimit);

    MeshCore::MeshEvalFoldOversOnSurface foldOvers(kernel);
    result.surfaceFoldOvers = finding(evaluatedIndices(foldOvers), sampleLimit);
    return result;
}

std::vector<Mesh::NativeFacetInspection> Mesh::inspectNativeFacets(
    const MeshObject& mesh,
    const std::vector<MeshCore::FacetIndex>& indices
)
{
    const MeshCore::MeshKernel& kernel = mesh.getKernel();
    const auto& facets = kernel.GetFacets();
    const Base::Matrix4D transform = mesh.getTransform();
    std::vector<NativeFacetInspection> result;
    result.reserve(indices.size());
    for (const MeshCore::FacetIndex index : indices) {
        if (index >= facets.size()) {
            throw Base::IndexError("Facet index is outside the exact Mesh topology");
        }
        const MeshCore::MeshFacet& facet = facets[index];
        const MeshCore::MeshGeomFacet triangle = kernel.GetFacet(facet);
        NativeFacetInspection item;
        item.index = index;
        for (std::size_t slot = 0; slot < 3; ++slot) {
            item.pointIndices[slot] = facet._aulPoints[slot];
            item.neighbourIndices[slot] = facet._aulNeighbours[slot] < MeshCore::FACET_INDEX_MAX
                ? static_cast<long>(facet._aulNeighbours[slot])
                : -1L;
            Base::Vector3d point = Base::convertTo<Base::Vector3d>(triangle._aclPoints[slot]);
            transform.multVec(point, item.points[slot]);
        }
        item.normal = (item.points[1] - item.points[0]).Cross(item.points[2] - item.points[0]);
        item.normal.Normalize();
        item.area = triangle.Area();
        item.aspectRatio = triangle.AspectRatio();
        item.roundness = triangle.Roundness();
        result.push_back(item);
    }
    return result;
}
