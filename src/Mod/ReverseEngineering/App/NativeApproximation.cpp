// SPDX-License-Identifier: LGPL-2.1-or-later

#include "NativeApproximation.h"

#include <algorithm>
#include <cmath>
#include <iterator>
#include <limits>
#include <numeric>
#include <vector>

#include <Geom_BezierSurface.hxx>
#include <TColgp_Array2OfPnt.hxx>

#include <App/ComplexGeoData.h>
#include <Base/Converter.h>
#include <Base/CoordinateSystem.h>
#include <Base/Exception.h>
#include <Mod/Mesh/App/Core/Approximation.h>
#include <Mod/Mesh/App/Core/Elements.h>
#include <Mod/Part/App/Geometry.h>
#include <Mod/Points/App/Points.h>


namespace
{

bool finitePoint(const Base::Vector3f& point) noexcept
{
    return std::isfinite(point.x) && std::isfinite(point.y) && std::isfinite(point.z);
}

double squaredDistance(const Base::Vector3f& first, const Base::Vector3f& second) noexcept
{
    const double x = static_cast<double>(first.x) - second.x;
    const double y = static_cast<double>(first.y) - second.y;
    const double z = static_cast<double>(first.z) - second.z;
    return x * x + y * y + z * z;
}

std::vector<Base::Vector3f> geometryPoints(
    const Data::ComplexGeoData& geometry,
    std::vector<Base::Vector3d>* normals = nullptr
)
{
    std::vector<Base::Vector3d> sourcePoints;
    std::vector<Base::Vector3d> sourceNormals;
    geometry.getPoints(sourcePoints, sourceNormals, 0.01f);
    std::vector<Base::Vector3f> points;
    points.reserve(sourcePoints.size());
    std::ranges::transform(sourcePoints, std::back_inserter(points), [](const auto& point) {
        return Base::convertTo<Base::Vector3f>(point);
    });
    if (normals) {
        *normals = std::move(sourceNormals);
    }
    return points;
}

void requireFinitePoints(const std::vector<Base::Vector3f>& points, std::size_t minimum)
{
    if (points.size() < minimum) {
        throw Base::ValueError("the source does not contain enough points for this fit");
    }
    if (!std::ranges::all_of(points, finitePoint)) {
        throw Base::ValueError("the source contains non-finite point coordinates");
    }
}

}  // namespace

namespace Reen
{

NativePlaneFit fitNativePlane(const Data::ComplexGeoData& geometry)
{
    std::vector<Base::Vector3d> normals;
    const auto points = geometryPoints(geometry, &normals);
    requireFinitePoints(points, 3);

    MeshCore::PlaneFit fit;
    fit.AddPoints(points);
    const double rms = fit.Fit();
    if (!std::isfinite(rms) || rms >= std::numeric_limits<float>::max()) {
        throw Base::RuntimeError("the source points could not be fit to a plane");
    }

    Base::Vector3f base = fit.GetBase();
    Base::Vector3f directionU = fit.GetDirU();
    Base::Vector3f directionV = fit.GetDirV();
    Base::Vector3f normal = fit.GetNormal();
    if (!normals.empty()) {
        const auto reference = Base::convertTo<Base::Vector3f>(normals.front());
        if (reference * normal < 0) {
            normal = -normal;
            directionU = -directionU;
        }
    }

    float length {};
    float width {};
    fit.Dimension(length, width);
    if (!std::isfinite(length) || !std::isfinite(width) || length <= 0 || width <= 0) {
        throw Base::RuntimeError("the source points do not span a usable plane");
    }
    base -= 0.5f * length * directionU + 0.5f * width * directionV;

    Base::CoordinateSystem frame;
    frame.setPosition(Base::convertTo<Base::Vector3d>(base));
    frame.setAxes(
        Base::convertTo<Base::Vector3d>(normal),
        Base::convertTo<Base::Vector3d>(directionU)
    );
    return {
        static_cast<double>(length),
        static_cast<double>(width),
        rms,
        Base::CoordinateSystem().displacement(frame),
    };
}

NativeCylinderFit fitNativeCylinder(const Mesh::MeshObject& mesh)
{
    const auto& kernel = mesh.getKernel();
    if (kernel.CountPoints() < 3 || kernel.CountFacets() < 1) {
        throw Base::ValueError("cylinder fitting requires a non-empty Mesh");
    }
    MeshCore::CylinderFit fit;
    fit.AddPoints(kernel.GetPoints());

    std::vector<MeshCore::FacetIndex> facets(kernel.CountFacets());
    std::iota(facets.begin(), facets.end(), MeshCore::FacetIndex(0));
    const auto normals = kernel.GetFacetNormals(facets);
    const auto initialBase = fit.GetGravity();
    const auto axis = fit.GetInitialAxisFromNormals(normals);
    fit.SetInitialValues(initialBase, axis);
    const double rms = fit.Fit();
    if (!std::isfinite(rms) || rms >= std::numeric_limits<float>::max()) {
        throw Base::RuntimeError("the Mesh could not be fit to a cylinder");
    }

    Base::Vector3f base;
    Base::Vector3f top;
    fit.GetBounding(base, top);
    const double height = Base::Distance(base, top);
    const double radius = fit.GetRadius();
    if (!std::isfinite(height) || !std::isfinite(radius) || height <= 0 || radius <= 0) {
        throw Base::RuntimeError("the cylinder fit did not produce usable dimensions");
    }
    Base::Rotation rotation;
    rotation.setValue(Base::Vector3d(0, 0, 1), Base::convertTo<Base::Vector3d>(fit.GetAxis()));
    return {
        radius,
        height,
        rms,
        Base::Placement(Base::convertTo<Base::Vector3d>(base), rotation),
    };
}

NativeSphereFit fitNativeSphere(const Mesh::MeshObject& mesh)
{
    const auto& kernel = mesh.getKernel();
    if (kernel.CountPoints() < 4 || kernel.CountFacets() < 1) {
        throw Base::ValueError("sphere fitting requires a non-empty Mesh");
    }
    MeshCore::SphereFit fit;
    fit.AddPoints(kernel.GetPoints());
    const double rms = fit.Fit();
    const double radius = fit.GetRadius();
    if (!std::isfinite(rms) || rms >= std::numeric_limits<float>::max()
        || !std::isfinite(radius) || radius <= 0) {
        throw Base::RuntimeError("the Mesh could not be fit to a sphere");
    }
    return {radius, rms, Base::convertTo<Base::Vector3d>(fit.GetCenter())};
}

NativePolynomialFit fitNativePolynomial(const Mesh::MeshObject& mesh)
{
    const auto& kernel = mesh.getKernel();
    if (kernel.CountPoints() < 3 || kernel.CountFacets() < 1) {
        throw Base::ValueError("polynomial surface fitting requires a non-empty Mesh");
    }
    MeshCore::SurfaceFit fit;
    fit.AddPoints(kernel.GetPoints());
    const double rms = fit.Fit();
    if (!std::isfinite(rms) || rms >= std::numeric_limits<float>::max()) {
        throw Base::RuntimeError("the Mesh could not be fit to a polynomial surface");
    }
    const auto bounds = fit.GetBoundings();
    auto poles = fit.toBezier(bounds.MinX, bounds.MaxX, bounds.MinY, bounds.MaxY);
    if (poles.size() != 9) {
        throw Base::RuntimeError("the polynomial fit did not produce a complete control grid");
    }
    fit.Transform(poles);

    TColgp_Array2OfPnt grid(1, 3, 1, 3);
    for (Standard_Integer column = 1; column <= 3; ++column) {
        for (Standard_Integer row = 1; row <= 3; ++row) {
            const auto index = static_cast<std::size_t>((column - 1) * 3 + (row - 1));
            const auto& pole = poles.at(index);
            grid.SetValue(row, column, gp_Pnt(pole.x, pole.y, pole.z));
        }
    }
    Handle(Geom_BezierSurface) surface(new Geom_BezierSurface(grid));
    TopoDS_Shape shape = Part::GeomBezierSurface(surface).toShape();
    if (shape.IsNull()) {
        throw Base::RuntimeError("the polynomial fit produced an empty surface");
    }
    return {std::move(shape), rms};
}

Mesh::MeshObject triangulateNativeStructuredPoints(
    const Points::PointKernel& pointKernel,
    std::size_t width,
    std::size_t height
)
{
    if (width < 2 || height < 2
        || width > std::numeric_limits<std::size_t>::max() / height
        || pointKernel.size() != width * height) {
        throw Base::ValueError(
            "structured points require a complete grid of at least two rows and columns"
        );
    }
    const auto& input = pointKernel.getBasicPoints();
    constexpr auto missing = MeshCore::POINT_INDEX_MAX;
    std::vector<MeshCore::PointIndex> mapping(input.size(), missing);
    std::vector<Base::Vector3f> points;
    points.reserve(input.size());
    for (std::size_t index = 0; index < input.size(); ++index) {
        if (!finitePoint(input[index])) {
            continue;
        }
        mapping[index] = static_cast<MeshCore::PointIndex>(points.size());
        points.push_back(input[index]);
    }

    const auto cellCount = (width - 1) * (height - 1);
    if (cellCount > std::numeric_limits<std::size_t>::max() / 2) {
        throw Base::ValueError("the structured point grid is too large to triangulate");
    }
    std::vector<MeshCore::MeshFacet> facets;
    facets.reserve(cellCount * 2);
    const auto addFacet = [&facets, &mapping](std::size_t first, std::size_t second, std::size_t third) {
        if (mapping[first] != missing && mapping[second] != missing && mapping[third] != missing) {
            facets.emplace_back(mapping[first], mapping[second], mapping[third]);
        }
    };

    for (std::size_t row = 0; row < height - 1; ++row) {
        for (std::size_t column = 0; column < width - 1; ++column) {
            const auto lowerLeft = row * width + column;
            const auto lowerRight = lowerLeft + 1;
            const auto upperLeft = lowerLeft + width;
            const auto upperRight = upperLeft + 1;
            const bool hasLowerLeft = mapping[lowerLeft] != missing;
            const bool hasLowerRight = mapping[lowerRight] != missing;
            const bool hasUpperLeft = mapping[upperLeft] != missing;
            const bool hasUpperRight = mapping[upperRight] != missing;
            const unsigned int valid = static_cast<unsigned int>(hasLowerLeft)
                + static_cast<unsigned int>(hasLowerRight)
                + static_cast<unsigned int>(hasUpperLeft)
                + static_cast<unsigned int>(hasUpperRight);
            if (valid < 3) {
                continue;
            }
            if (valid == 3) {
                if (!hasLowerLeft) {
                    addFacet(lowerRight, upperRight, upperLeft);
                }
                else if (!hasLowerRight) {
                    addFacet(lowerLeft, upperRight, upperLeft);
                }
                else if (!hasUpperLeft) {
                    addFacet(lowerLeft, lowerRight, upperRight);
                }
                else {
                    addFacet(lowerLeft, lowerRight, upperLeft);
                }
                continue;
            }
            if (squaredDistance(input[lowerLeft], input[upperRight])
                <= squaredDistance(input[lowerRight], input[upperLeft])) {
                addFacet(lowerLeft, lowerRight, upperRight);
                addFacet(lowerLeft, upperRight, upperLeft);
            }
            else {
                addFacet(lowerLeft, lowerRight, upperLeft);
                addFacet(lowerRight, upperRight, upperLeft);
            }
        }
    }
    if (facets.empty()) {
        throw Base::RuntimeError("the structured point grid contains no triangulatable cells");
    }
    Mesh::MeshObject result;
    result.addFacets(facets, points, true);
    if (result.countFacets() == 0) {
        throw Base::RuntimeError("structured-point triangulation produced an empty Mesh");
    }
    return result;
}

}  // namespace Reen
