// SPDX-License-Identifier: LGPL-2.1-or-later

#include "NativePointOperations.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <set>

#include <App/ComplexGeoData.h>
#include <Base/Exception.h>


namespace
{

constexpr std::size_t maximumStructuredPoints = 100'000'000;

bool finitePoint(const Base::Vector3d& point)
{
    return std::isfinite(point.x) && std::isfinite(point.y) && std::isfinite(point.z);
}

std::vector<double> clusteredCoordinates(std::vector<double> values, double tolerance)
{
    std::ranges::sort(values);
    std::vector<double> result;
    for (const double value : values) {
        if (result.empty() || value - result.back() > tolerance) {
            result.push_back(value);
        }
        else {
            result.back() = 0.5 * (result.back() + value);
        }
    }
    return result;
}

std::size_t closestCoordinate(
    const std::vector<double>& coordinates,
    double value,
    double tolerance
)
{
    auto upper = std::ranges::lower_bound(coordinates, value);
    auto best = upper;
    if (upper == coordinates.end()
        || (upper != coordinates.begin()
            && std::fabs(*std::prev(upper) - value) <= std::fabs(*upper - value))) {
        best = std::prev(upper);
    }
    if (best == coordinates.end() || std::fabs(*best - value) > tolerance) {
        throw Base::ValueError("A point lies outside the inferred structured grid tolerance");
    }
    return static_cast<std::size_t>(std::distance(coordinates.begin(), best));
}

struct PolygonBasis
{
    Base::Vector3d origin;
    Base::Vector3d xAxis;
    Base::Vector3d yAxis;
    std::vector<std::pair<double, double>> polygon;
    double tolerance {};
};

PolygonBasis polygonBasis(const std::vector<Base::Vector3d>& polygon)
{
    if (polygon.size() < 3) {
        throw Base::ValueError("a point-cloud polygon requires at least three vertices");
    }
    Base::Vector3d normal;
    double extent = 0.0;
    for (std::size_t index = 0; index < polygon.size(); ++index) {
        const auto& first = polygon[index];
        const auto& second = polygon[(index + 1) % polygon.size()];
        if (!finitePoint(first)) {
            throw Base::ValueError("point-cloud polygon coordinates must be finite");
        }
        normal.x += (first.y - second.y) * (first.z + second.z);
        normal.y += (first.z - second.z) * (first.x + second.x);
        normal.z += (first.x - second.x) * (first.y + second.y);
        extent = std::max(extent, (second - first).Length());
    }
    if (normal.Length() <= std::max(1.0e-12, extent * extent * 1.0e-12)) {
        throw Base::ValueError("point-cloud polygon vertices are collinear");
    }
    normal.Normalize();
    Base::Vector3d xAxis;
    for (std::size_t index = 1; index < polygon.size(); ++index) {
        xAxis = polygon[index] - polygon.front();
        xAxis -= normal * xAxis.Dot(normal);
        if (xAxis.Length() > 1.0e-12) {
            break;
        }
    }
    if (xAxis.Length() <= 1.0e-12) {
        throw Base::ValueError("point-cloud polygon has no usable edge");
    }
    xAxis.Normalize();
    Base::Vector3d yAxis = normal.Cross(xAxis);
    yAxis.Normalize();
    const double tolerance = std::max(1.0e-9, extent * 1.0e-9);
    std::vector<std::pair<double, double>> projected;
    projected.reserve(polygon.size());
    for (const auto& point : polygon) {
        const auto relative = point - polygon.front();
        if (std::fabs(relative.Dot(normal)) > std::max(1.0e-7, extent * 1.0e-8)) {
            throw Base::ValueError("point-cloud polygon vertices must be coplanar");
        }
        projected.emplace_back(relative.Dot(xAxis), relative.Dot(yAxis));
    }
    return {polygon.front(), xAxis, yAxis, std::move(projected), tolerance};
}

bool pointOnSegment(
    double x,
    double y,
    const std::pair<double, double>& first,
    const std::pair<double, double>& second,
    double tolerance
)
{
    const double dx = second.first - first.first;
    const double dy = second.second - first.second;
    const double cross = (x - first.first) * dy - (y - first.second) * dx;
    if (std::fabs(cross) > tolerance * std::max(1.0, std::hypot(dx, dy))) {
        return false;
    }
    return x >= std::min(first.first, second.first) - tolerance
        && x <= std::max(first.first, second.first) + tolerance
        && y >= std::min(first.second, second.second) - tolerance
        && y <= std::max(first.second, second.second) + tolerance;
}

bool containsPoint(const PolygonBasis& basis, const Base::Vector3d& point)
{
    const auto relative = point - basis.origin;
    const double x = relative.Dot(basis.xAxis);
    const double y = relative.Dot(basis.yAxis);
    bool inside = false;
    for (std::size_t index = 0, previous = basis.polygon.size() - 1;
         index < basis.polygon.size();
         previous = index++) {
        const auto& first = basis.polygon[previous];
        const auto& second = basis.polygon[index];
        if (pointOnSegment(x, y, first, second, basis.tolerance)) {
            return true;
        }
        const bool crosses = (first.second > y) != (second.second > y);
        if (crosses
            && x
                < (second.first - first.first) * (y - first.second)
                        / (second.second - first.second)
                    + first.first) {
            inside = !inside;
        }
    }
    return inside;
}

}  // namespace

namespace Points
{

NativePointSample sampleNativeGeometry(
    const Data::ComplexGeoData& geometry,
    double maximumDistance
)
{
    if (!std::isfinite(maximumDistance) || maximumDistance <= 0.0) {
        throw Base::ValueError("maximum point distance must be finite and positive");
    }
    std::vector<Base::Vector3d> sampled;
    std::vector<Base::Vector3d> normals;
    geometry.getPoints(sampled, normals, maximumDistance);
    if (sampled.empty()) {
        throw Base::ValueError("the selected geometry produced no point data");
    }
    NativePointSample result;
    result.points.reserve(sampled.size());
    for (const auto& point : sampled) {
        if (!finitePoint(point)) {
            throw Base::ValueError("the selected geometry produced a non-finite point");
        }
        result.points.push_back(point);
    }
    if (normals.size() == sampled.size()) {
        result.normals.reserve(normals.size());
        for (const auto& normal : normals) {
            result.normals.emplace_back(normal.x, normal.y, normal.z);
        }
    }
    return result;
}

NativePointStructure structureNativePointCloud(
    const PointKernel& points,
    double coordinateTolerance
)
{
    if (!std::isfinite(coordinateTolerance) || coordinateTolerance <= 0.0) {
        throw Base::ValueError("grid coordinate tolerance must be finite and positive");
    }
    if (points.size() < 4) {
        throw Base::ValueError("a structured point cloud requires at least four points");
    }
    std::vector<double> xValues;
    std::vector<double> yValues;
    xValues.reserve(points.size());
    yValues.reserve(points.size());
    for (std::size_t index = 0; index < points.size(); ++index) {
        const auto point = points.getPoint(static_cast<int>(index));
        if (!finitePoint(point)) {
            continue;
        }
        xValues.push_back(point.x);
        yValues.push_back(point.y);
    }
    const auto columns = clusteredCoordinates(std::move(xValues), coordinateTolerance);
    const auto rows = clusteredCoordinates(std::move(yValues), coordinateTolerance);
    if (columns.size() < 2 || rows.size() < 2) {
        throw Base::ValueError(
            "a structured point cloud requires at least two distinct X and Y coordinates"
        );
    }
    if (columns.size() > maximumStructuredPoints / rows.size()) {
        throw Base::ValueError("the inferred structured point grid is too large");
    }
    const std::size_t gridSize = columns.size() * rows.size();
    if (gridSize > maximumStructuredPoints || gridSize > points.size() * 16) {
        throw Base::ValueError(
            "the inferred grid is too sparse; increase coordinate tolerance or use scattered points"
        );
    }
    const double nan = std::numeric_limits<double>::quiet_NaN();
    NativePointStructure result;
    result.width = columns.size();
    result.height = rows.size();
    result.points.resize(gridSize);
    result.sourceIndices.assign(gridSize, -1);
    for (std::size_t index = 0; index < gridSize; ++index) {
        result.points.setPoint(static_cast<int>(index), Base::Vector3d(nan, nan, nan));
    }
    for (std::size_t index = 0; index < points.size(); ++index) {
        const auto point = points.getPoint(static_cast<int>(index));
        if (!finitePoint(point)) {
            continue;
        }
        const auto column = closestCoordinate(columns, point.x, coordinateTolerance);
        const auto row = closestCoordinate(rows, point.y, coordinateTolerance);
        const std::size_t target = row * columns.size() + column;
        if (result.sourceIndices[target] >= 0) {
            throw Base::ValueError(
                "multiple points occupy one inferred structured grid cell"
            );
        }
        result.points.setPoint(static_cast<int>(target), point);
        result.sourceIndices[target] = static_cast<std::ptrdiff_t>(index);
    }
    return result;
}

NativePointSubset selectNativePointCloud(
    const PointKernel& points,
    const Base::Placement& placement,
    const std::vector<Base::Vector3d>& polygon,
    bool keepInside
)
{
    const auto basis = polygonBasis(polygon);
    NativePointSubset result;
    result.points.reserve(points.size());
    result.sourceIndices.reserve(points.size());
    for (std::size_t index = 0; index < points.size(); ++index) {
        const auto local = points.getPoint(static_cast<int>(index));
        if (!finitePoint(local)) {
            continue;
        }
        Base::Vector3d global;
        placement.multVec(local, global);
        if (containsPoint(basis, global) == keepInside) {
            result.points.push_back(local);
            result.sourceIndices.push_back(index);
        }
    }
    return result;
}

NativePointMerge mergeNativePointClouds(
    const std::vector<const PointKernel*>& clouds,
    const std::vector<Base::Placement>& placements
)
{
    if (clouds.size() < 2 || clouds.size() != placements.size()) {
        throw Base::ValueError("merge requires matching point clouds and placements");
    }
    std::size_t reserve = 0;
    for (const auto* cloud : clouds) {
        if (!cloud) {
            throw Base::ValueError("merge received an invalid point cloud");
        }
        reserve += cloud->size();
    }
    NativePointMerge result;
    result.points.reserve(reserve);
    result.sourceIndices.reserve(reserve);
    for (std::size_t source = 0; source < clouds.size(); ++source) {
        for (std::size_t index = 0; index < clouds[source]->size(); ++index) {
            const auto local = clouds[source]->getPoint(static_cast<int>(index));
            if (!finitePoint(local)) {
                continue;
            }
            Base::Vector3d global;
            placements[source].multVec(local, global);
            result.points.push_back(global);
            result.sourceIndices.emplace_back(source, index);
        }
    }
    if (result.points.size() == 0) {
        throw Base::ValueError("the merged point clouds contain no finite points");
    }
    return result;
}

}  // namespace Points
