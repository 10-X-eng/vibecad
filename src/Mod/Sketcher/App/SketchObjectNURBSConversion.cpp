// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <cstddef>
#include <set>
#include <vector>

#include <Mod/Part/App/Geometry.h>

#include "GeoEnum.h"
#include "SketchObject.h"

using namespace Sketcher;

namespace
{

constexpr std::size_t MaxConversionTargets = 256;
constexpr std::size_t MaxExposedInternalGeometry = 4096;

bool validConversionTargets(const SketchObject& sketch, const std::vector<int>& geometryIds)
{
    if (geometryIds.empty() || geometryIds.size() > MaxConversionTargets) {
        return false;
    }
    const std::set<int> uniqueIds(geometryIds.begin(), geometryIds.end());
    if (uniqueIds.size() != geometryIds.size()) {
        return false;
    }
    std::size_t existingBSplineInternals = 0;
    for (const int geometryId : geometryIds) {
        if (geometryId == GeoEnum::HAxis || geometryId == GeoEnum::VAxis
            || geometryId > sketch.getHighestCurveIndex()
            || (geometryId < 0
                && -geometryId > static_cast<int>(sketch.ExternalGeo.getSize()))) {
            return false;
        }
        const Part::Geometry* geometry = sketch.getGeometry(geometryId);
        if (!geometry || geometry->is<Part::GeomPoint>()) {
            return false;
        }
        if (geometryId >= 0 && geometry->is<Part::GeomBSplineCurve>()) {
            const auto* bspline = static_cast<const Part::GeomBSplineCurve*>(geometry);
            existingBSplineInternals += bspline->countPoles() + bspline->countKnots();
            if (existingBSplineInternals > MaxExposedInternalGeometry) {
                return false;
            }
        }
    }
    return true;
}

}  // namespace

int SketchObject::convertToNURBSPrepared(
    const std::vector<int>& geometryIds,
    NURBSConversionDiagnostic* result
)
{
    if (!validConversionTargets(*this, geometryIds)) {
        return -1;
    }

    if (result) {
        result->geometryIds = geometryIds;
        result->convertedGeometryIds.clear();
        result->convertedGeometryIds.reserve(geometryIds.size());
        result->exposedInternalGeometryCount = 0;
    }

    // Match the human command's two passes exactly: convert every selected edge first, then
    // expose control points and knots for converted internal edges. An external edge is copied
    // into the Sketch by convertToNURBS(), while exposeInternalGeometry() intentionally ignores
    // its negative source ID.
    for (const int geometryId : geometryIds) {
        if (!convertToNURBS(geometryId)) {
            return -1;
        }
        if (result) {
            result->convertedGeometryIds.push_back(
                geometryId >= 0 ? geometryId : getHighestCurveIndex()
            );
        }
    }
    for (const int geometryId : geometryIds) {
        const int exposed = exposeInternalGeometry(geometryId);
        if (geometryId >= 0) {
            if (exposed < 0) {
                return -1;
            }
            if (result) {
                result->exposedInternalGeometryCount += exposed;
            }
        }
    }
    solve();
    return static_cast<int>(geometryIds.size());
}

int SketchObject::convertToNURBSExact(const std::vector<int>& geometryIds)
{
    // Prove the complete ordered selection before touching the live Sketch. This closes the
    // legacy command's partial-mutation failure mode without changing its public behavior.
    if (!diagnoseConvertToNURBS(geometryIds)) {
        return -1;
    }
    return convertToNURBSPrepared(geometryIds, nullptr);
}

std::unique_ptr<NURBSConversionDiagnostic>
SketchObject::diagnoseConvertToNURBS(const std::vector<int>& geometryIds) const
{
    if (!validConversionTargets(*this, geometryIds)) {
        return {};
    }
    auto diagnostic = makeGeometryMutationDiagnosticClone();
    diagnostic->Placement.setValue(Placement.getValue());
    diagnostic->ArcFitTolerance.setValue(ArcFitTolerance.getValue());
    diagnostic->ExternalGeometry.setAllowExternal(true);
    diagnostic->ExternalGeometry.setValues(
        ExternalGeometry.getValues(),
        ExternalGeometry.getSubValues()
    );
    diagnostic->ExternalTypes.setValues(ExternalTypes.getValues());
    diagnostic->externalGeoRef = externalGeoRef;
    diagnostic->externalGeoMap = externalGeoMap;
    diagnostic->externalGeoRefMap = externalGeoRefMap;
    auto result = std::make_unique<NURBSConversionDiagnostic>();
    if (diagnostic->convertToNURBSPrepared(geometryIds, result.get()) < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}
