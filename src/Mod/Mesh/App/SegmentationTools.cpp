// SPDX-License-Identifier: LGPL-2.1-or-later

#include "SegmentationTools.h"

#include <limits>
#include <memory>

#include <Base/Exception.h>
#include "Core/Approximation.h"
#include "Core/Curvature.h"
#include "Core/Segmentation.h"
#include "Core/Smoothing.h"

namespace
{

using SurfaceList = std::vector<MeshCore::MeshSurfaceSegmentPtr>;

std::vector<Mesh::DetectedMeshSegment> collectSegments(const SurfaceList& surfaces)
{
    std::vector<Mesh::DetectedMeshSegment> results;
    for (const auto& surface : surfaces) {
        for (const auto& segment : surface->GetSegments()) {
            if (!segment.empty()) {
                results.push_back({surface->GetType(), {segment.begin(), segment.end()}});
            }
        }
    }
    return results;
}

void requireParameterCount(const Mesh::CurvatureSegmentRequest& request, std::size_t expected)
{
    if (request.parameters.size() != expected) {
        throw Base::ValueError("A curvature segmentation request has invalid parameters");
    }
}

std::unique_ptr<MeshCore::AbstractSurfaceFit> makeSurfaceFit(
    const Mesh::BestFitSegmentRequest& request
)
{
    const auto& values = request.initialParameters;
    if (request.kind == "Plane") {
        if (values.empty()) {
            return std::make_unique<MeshCore::PlaneSurfaceFit>();
        }
        if (values.size() != 6) {
            throw Base::ValueError("An explicit Plane fit requires point and normal vectors");
        }
        return std::make_unique<MeshCore::PlaneSurfaceFit>(
            Base::Vector3f(values[0], values[1], values[2]),
            Base::Vector3f(values[3], values[4], values[5])
        );
    }
    if (request.kind == "Cylinder") {
        if (values.empty()) {
            return std::make_unique<MeshCore::CylinderSurfaceFit>();
        }
        if (values.size() != 7) {
            throw Base::ValueError("An explicit Cylinder fit requires base, axis, and radius");
        }
        return std::make_unique<MeshCore::CylinderSurfaceFit>(
            Base::Vector3f(values[0], values[1], values[2]),
            Base::Vector3f(values[3], values[4], values[5]),
            values[6]
        );
    }
    if (request.kind == "Sphere") {
        if (values.empty()) {
            return std::make_unique<MeshCore::SphereSurfaceFit>();
        }
        if (values.size() != 4) {
            throw Base::ValueError("An explicit Sphere fit requires center and radius");
        }
        return std::make_unique<MeshCore::SphereSurfaceFit>(
            Base::Vector3f(values[0], values[1], values[2]),
            values[3]
        );
    }
    throw Base::ValueError("Best-fit segmentation supports Plane, Cylinder, and Sphere");
}

MeshCore::MeshKernel workingKernel(const Mesh::MeshObject& mesh, unsigned int smoothingSteps)
{
    if (mesh.countFacets() == 0) {
        throw Base::ValueError("Segmentation requires a nonempty mesh");
    }
    MeshCore::MeshKernel kernel(mesh.getKernel());
    if (smoothingSteps > 0) {
        MeshCore::LaplaceSmoothing smoother(kernel);
        smoother.Smooth(smoothingSteps);
    }
    return kernel;
}

}  // namespace

std::vector<Mesh::DetectedMeshSegment> Mesh::detectCurvatureSegments(
    const MeshObject& mesh,
    const std::vector<CurvatureSegmentRequest>& requests,
    unsigned int smoothingSteps
)
{
    if (requests.empty()) {
        throw Base::ValueError("At least one curvature surface request is required");
    }
    MeshCore::MeshKernel kernel = workingKernel(mesh, smoothingSteps);
    MeshCore::MeshCurvature curvature(kernel);
    curvature.ComputePerVertex();

    SurfaceList surfaces;
    surfaces.reserve(requests.size());
    for (const auto& request : requests) {
        if (request.minimumFacets < 1) {
            throw Base::ValueError("Minimum facets must be positive");
        }
        switch (request.kind) {
            case CurvatureSegmentKind::Plane:
                requireParameterCount(request, 1);
                surfaces.emplace_back(std::make_shared<MeshCore::MeshCurvaturePlanarSegment>(
                    curvature.GetCurvature(), request.minimumFacets, request.parameters[0]
                ));
                break;
            case CurvatureSegmentKind::Cylinder:
                requireParameterCount(request, 3);
                surfaces.emplace_back(
                    std::make_shared<MeshCore::MeshCurvatureCylindricalSegment>(
                        curvature.GetCurvature(),
                        request.minimumFacets,
                        request.parameters[1],
                        request.parameters[2],
                        request.parameters[0]
                    )
                );
                break;
            case CurvatureSegmentKind::Sphere:
                requireParameterCount(request, 2);
                surfaces.emplace_back(std::make_shared<MeshCore::MeshCurvatureSphericalSegment>(
                    curvature.GetCurvature(),
                    request.minimumFacets,
                    request.parameters[1],
                    request.parameters[0]
                ));
                break;
            case CurvatureSegmentKind::Freeform:
                requireParameterCount(request, 4);
                surfaces.emplace_back(std::make_shared<MeshCore::MeshCurvatureFreeformSegment>(
                    curvature.GetCurvature(),
                    request.minimumFacets,
                    request.parameters[3],
                    request.parameters[2],
                    request.parameters[0],
                    request.parameters[1]
                ));
                break;
        }
    }
    MeshCore::MeshSegmentAlgorithm(kernel).FindSegments(surfaces);
    return collectSegments(surfaces);
}

std::vector<Mesh::DetectedMeshSegment> Mesh::detectBestFitSegments(
    const MeshObject& mesh,
    const std::vector<BestFitSegmentRequest>& requests
)
{
    if (requests.empty()) {
        throw Base::ValueError("At least one best-fit surface request is required");
    }
    MeshCore::MeshKernel kernel = workingKernel(mesh, 0);
    SurfaceList surfaces;
    surfaces.reserve(requests.size());
    for (const auto& request : requests) {
        if (request.minimumFacets < 1 || request.tolerance < 0.0F) {
            throw Base::ValueError(
                "Best-fit minimum facets and tolerance must be positive and non-negative"
            );
        }
        auto fitter = makeSurfaceFit(request);
        surfaces.emplace_back(std::make_shared<MeshCore::MeshDistanceGenericSurfaceFitSegment>(
            fitter.release(), kernel, request.minimumFacets, request.tolerance
        ));
    }
    MeshCore::MeshSegmentAlgorithm(kernel).FindSegments(surfaces);
    return collectSegments(surfaces);
}

std::vector<Mesh::DetectedMeshSegment> Mesh::detectPlanarSegments(
    const MeshObject& mesh,
    unsigned long minimumFacets,
    float curvatureTolerance,
    float distanceTolerance,
    unsigned int smoothingSteps
)
{
    if (minimumFacets < 1 || curvatureTolerance < 0.0F || distanceTolerance < 0.0F) {
        throw Base::ValueError(
            "Planar segmentation requires positive minimum facets and non-negative tolerances"
        );
    }
    MeshCore::MeshKernel kernel = workingKernel(mesh, smoothingSteps);
    MeshCore::MeshCurvature curvature(kernel);
    curvature.ComputePerVertex();

    SurfaceList preliminary {
        std::make_shared<MeshCore::MeshCurvaturePlanarSegment>(
            curvature.GetCurvature(), minimumFacets, curvatureTolerance
        ),
    };
    MeshCore::MeshSegmentAlgorithm finder(kernel);
    finder.FindSegments(preliminary);

    SurfaceList fitted;
    for (const auto& candidate : preliminary.front()->GetSegments()) {
        const auto pointIndices = kernel.GetFacetPoints(candidate);
        MeshCore::PlaneFit fit;
        fit.AddPoints(kernel.GetPoints(pointIndices));
        if (fit.Fit() < std::numeric_limits<float>::max()) {
            fitted.emplace_back(std::make_shared<MeshCore::MeshDistanceGenericSurfaceFitSegment>(
                new MeshCore::PlaneSurfaceFit(fit.GetBase(), fit.GetNormal()),
                kernel,
                minimumFacets,
                distanceTolerance
            ));
        }
    }
    if (fitted.empty()) {
        return {};
    }
    finder.FindSegments(fitted);
    return collectSegments(fitted);
}
