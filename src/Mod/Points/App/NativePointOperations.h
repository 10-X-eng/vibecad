// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <cstddef>
#include <utility>
#include <vector>

#include <Base/Placement.h>
#include <Base/Vector3D.h>

#include "Points.h"


namespace Data
{
class ComplexGeoData;
}

namespace Points
{

struct NativePointSample
{
    PointKernel points;
    std::vector<Base::Vector3f> normals;
};

struct NativePointStructure
{
    PointKernel points;
    std::size_t width {};
    std::size_t height {};
    std::vector<std::ptrdiff_t> sourceIndices;
};

struct NativePointSubset
{
    PointKernel points;
    std::vector<std::size_t> sourceIndices;
};

struct NativePointMerge
{
    PointKernel points;
    std::vector<std::pair<std::size_t, std::size_t>> sourceIndices;
};

PointsExport NativePointSample sampleNativeGeometry(
    const Data::ComplexGeoData& geometry,
    double maximumDistance
);

PointsExport NativePointStructure structureNativePointCloud(
    const PointKernel& points,
    double coordinateTolerance
);

PointsExport NativePointSubset selectNativePointCloud(
    const PointKernel& points,
    const Base::Placement& placement,
    const std::vector<Base::Vector3d>& polygon,
    bool keepInside
);

PointsExport NativePointMerge mergeNativePointClouds(
    const std::vector<const PointKernel*>& clouds,
    const std::vector<Base::Placement>& placements
);

}  // namespace Points
