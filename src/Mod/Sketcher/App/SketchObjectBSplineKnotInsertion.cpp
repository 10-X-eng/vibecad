// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <cmath>
#include <cstddef>
#include <memory>
#include <utility>

#include <Precision.hxx>

#include <Base/Exception.h>
#include <Mod/Part/App/Geometry.h>

#include "SketchObject.h"

using namespace Sketcher;

namespace
{

constexpr std::size_t MaxExposedBSplineGeometry = 4096;

struct InsertedKnot
{
    std::unique_ptr<Part::GeomBSplineCurve> curve;
    int knotIndex {-1};
    double knotParameter {0.0};
    int degree {0};
    int oldMultiplicity {0};
    int newMultiplicity {0};
};

int matchingKnot(const std::vector<double>& knots, double parameter)
{
    int result = -1;
    for (std::size_t index = 0; index < knots.size(); ++index) {
        if (std::abs(knots[index] - parameter) <= Precision::PConfusion()) {
            if (result >= 0) {
                return -1;
            }
            result = static_cast<int>(index);
        }
    }
    return result;
}

InsertedKnot insertedCurve(const SketchObject& sketch, int geometryId, double parameter)
{
    if (geometryId < 0 || geometryId > sketch.getHighestCurveIndex()
        || !std::isfinite(parameter)) {
        return {};
    }
    const Part::Geometry* geometry = sketch.getGeometry(geometryId);
    if (!geometry || !geometry->is<Part::GeomBSplineCurve>()) {
        return {};
    }
    const auto* original = static_cast<const Part::GeomBSplineCurve*>(geometry);
    if (parameter < original->getFirstParameter() || parameter > original->getLastParameter()) {
        return {};
    }
    const auto oldKnots = original->getKnots();
    const auto oldMultiplicities = original->getMultiplicities();
    const int oldIndex = matchingKnot(oldKnots, parameter);
    const int oldMultiplicity = oldIndex >= 0 ? oldMultiplicities[oldIndex] : 0;
    const int degree = original->getDegree();
    if (oldMultiplicity >= degree) {
        return {};
    }
    auto changed = std::unique_ptr<Part::GeomBSplineCurve>(
        static_cast<Part::GeomBSplineCurve*>(original->clone())
    );
    try {
        changed->insertKnot(parameter, 1);
    }
    catch (const Base::Exception&) {
        return {};
    }
    const auto newKnots = changed->getKnots();
    const auto newMultiplicities = changed->getMultiplicities();
    const int newIndex = matchingKnot(newKnots, parameter);
    const auto expectedKnotCount = oldKnots.size() + (oldIndex < 0 ? 1 : 0);
    if (newIndex < 0 || newKnots.size() != expectedKnotCount
        || newMultiplicities[newIndex] != oldMultiplicity + 1
        || changed->countPoles() != original->countPoles() + 1
        || changed->getDegree() != degree
        || static_cast<std::size_t>(changed->countPoles() + changed->countKnots())
            > MaxExposedBSplineGeometry) {
        return {};
    }
    return {
        std::move(changed),
        newIndex,
        newKnots[newIndex],
        degree,
        oldMultiplicity,
        newMultiplicities[newIndex],
    };
}

}  // namespace

int SketchObject::insertBSplineKnotPrepared(
    int geometryId,
    double parameter,
    BSplineKnotInsertionDiagnostic* result
)
{
    auto inserted = insertedCurve(*this, geometryId, parameter);
    if (!inserted.curve) {
        return -1;
    }
    if (result) {
        result->geometryId = geometryId;
        result->requestedParameter = parameter;
        result->knotIndex = inserted.knotIndex;
        result->knotParameter = inserted.knotParameter;
        result->degree = inserted.degree;
        result->oldMultiplicity = inserted.oldMultiplicity;
        result->newMultiplicity = inserted.newMultiplicity;
    }
    return replaceBSplineAndReconcileInternals(
        geometryId,
        std::move(inserted.curve),
        result ? &result->retainedInternalGeometryCount : nullptr,
        result ? &result->deletedInternalGeometryCount : nullptr,
        result ? &result->exposedInternalGeometryCount : nullptr
    );
}

int SketchObject::insertBSplineKnotExact(int geometryId, double parameter)
{
    if (!diagnoseInsertBSplineKnot(geometryId, parameter)) {
        return -1;
    }
    return insertBSplineKnotPrepared(geometryId, parameter, nullptr);
}

std::unique_ptr<BSplineKnotInsertionDiagnostic>
SketchObject::diagnoseInsertBSplineKnot(int geometryId, double parameter) const
{
    if (!insertedCurve(*this, geometryId, parameter).curve) {
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

    auto result = std::make_unique<BSplineKnotInsertionDiagnostic>();
    if (diagnostic->insertBSplineKnotPrepared(geometryId, parameter, result.get()) < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}
