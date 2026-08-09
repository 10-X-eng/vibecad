// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <cstddef>
#include <set>
#include <utility>
#include <vector>

#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Mod/Part/App/Geometry.h>

#include "SketchObject.h"

using namespace Sketcher;

namespace
{

constexpr std::size_t MaxDegreeIncreaseTargets = 256;
constexpr std::size_t MaxExposedBSplineGeometry = 4096;

bool validateDegreeIncreaseTargets(
    const SketchObject& sketch,
    const std::vector<int>& geometryIds,
    std::vector<int>* oldDegrees = nullptr,
    std::vector<int>* newDegrees = nullptr
)
{
    if (geometryIds.empty() || geometryIds.size() > MaxDegreeIncreaseTargets) {
        return false;
    }
    const std::set<int> uniqueIds(geometryIds.begin(), geometryIds.end());
    if (uniqueIds.size() != geometryIds.size()) {
        return false;
    }

    std::size_t exposedGeometryUpperBound = 0;
    if (oldDegrees) {
        oldDegrees->clear();
        oldDegrees->reserve(geometryIds.size());
    }
    if (newDegrees) {
        newDegrees->clear();
        newDegrees->reserve(geometryIds.size());
    }
    for (const int geometryId : geometryIds) {
        if (geometryId < 0 || geometryId > sketch.getHighestCurveIndex()) {
            return false;
        }
        const Part::Geometry* geometry = sketch.getGeometry(geometryId);
        if (!geometry || !geometry->is<Part::GeomBSplineCurve>()) {
            return false;
        }
        const auto* bspline = static_cast<const Part::GeomBSplineCurve*>(geometry);
        std::unique_ptr<Part::GeomBSplineCurve> elevated(
            static_cast<Part::GeomBSplineCurve*>(bspline->clone())
        );
        const int oldDegree = elevated->getDegree();
        try {
            elevated->increaseDegree(oldDegree + 1);
        }
        catch (const Base::Exception&) {
            return false;
        }
        exposedGeometryUpperBound += elevated->countPoles() + elevated->countKnots();
        if (exposedGeometryUpperBound > MaxExposedBSplineGeometry) {
            return false;
        }
        if (oldDegrees) {
            oldDegrees->push_back(oldDegree);
        }
        if (newDegrees) {
            newDegrees->push_back(elevated->getDegree());
        }
    }
    return true;
}

}  // namespace

int SketchObject::increaseBSplineDegreePrepared(
    const std::vector<int>& geometryIds,
    BSplineDegreeIncreaseDiagnostic* result
)
{
    std::vector<int> oldDegrees;
    std::vector<int> newDegrees;
    if (!validateDegreeIncreaseTargets(*this, geometryIds, &oldDegrees, &newDegrees)) {
        return -1;
    }
    if (result) {
        result->geometryIds = geometryIds;
        result->oldDegrees = oldDegrees;
        result->newDegrees = newDegrees;
        result->exposedInternalGeometryCount = 0;
    }

    // Match the human command's ordered elevate-then-expose behavior while retaining the full
    // Sketch geometry clone. The legacy single-geometry API reconstructs from the OCC handle and
    // loses extensions such as Construction; the additive exact path must preserve them.
    Base::StateLocker lock(managedoperation, true);
    for (const int geometryId : geometryIds) {
        const auto* original = static_cast<const Part::GeomBSplineCurve*>(
            getGeometry(geometryId)
        );
        std::unique_ptr<Part::GeomBSplineCurve> elevated(
            static_cast<Part::GeomBSplineCurve*>(original->clone())
        );
        elevated->increaseDegree(elevated->getDegree() + 1);
        std::vector<Part::Geometry*> geometry(getInternalGeometry());
        geometry[geometryId] = elevated.release();
        Geometry.setValues(std::move(geometry));
        const int exposed = exposeInternalGeometry(geometryId);
        if (exposed < 0) {
            return -1;
        }
        if (result) {
            result->exposedInternalGeometryCount += exposed;
        }
    }
    solve();
    return static_cast<int>(geometryIds.size());
}

int SketchObject::increaseBSplineDegreeExact(const std::vector<int>& geometryIds)
{
    // Prove every kernel elevation and helper expansion before touching the live Sketch.
    if (!diagnoseIncreaseBSplineDegree(geometryIds)) {
        return -1;
    }
    return increaseBSplineDegreePrepared(geometryIds, nullptr);
}

std::unique_ptr<BSplineDegreeIncreaseDiagnostic>
SketchObject::diagnoseIncreaseBSplineDegree(const std::vector<int>& geometryIds) const
{
    if (!validateDegreeIncreaseTargets(*this, geometryIds)) {
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

    auto result = std::make_unique<BSplineDegreeIncreaseDiagnostic>();
    if (diagnostic->increaseBSplineDegreePrepared(geometryIds, result.get()) < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}
