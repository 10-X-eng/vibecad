// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <vector>

#include <Base/Vector3D.h>

namespace Sketcher
{
class SketchObject;

namespace OffsetInternal
{

bool getEndpoints(
    const SketchObject& sketch,
    int geometryId,
    Base::Vector3d& start,
    Base::Vector3d& end
);
bool geometriesMeet(const SketchObject& sketch, int first, int second);
std::vector<std::vector<int>> connectedCurves(
    const SketchObject& sketch,
    const std::vector<int>& geometryIds
);
void constrainOffset(
    SketchObject& sketch,
    const std::vector<int>& sourceGeometryIds,
    const std::vector<int>& offsetGeometryIds,
    double offsetLength
);

}  // namespace OffsetInternal
}  // namespace Sketcher
