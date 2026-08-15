// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <algorithm>
#include <cstddef>
#include <map>
#include <memory>
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

constexpr std::size_t MaxExposedBSplineGeometry = 4096;

template<typename Value>
int mappedIndex(const std::vector<Value>& oldValues,
                const std::vector<Value>& newValues,
                int oldIndex)
{
    if (oldIndex < 0 || oldIndex >= static_cast<int>(oldValues.size())) {
        return -1;
    }
    const auto found = std::ranges::find(newValues, oldValues[oldIndex]);
    return found == newValues.end() ? -1 : static_cast<int>(found - newValues.begin());
}

std::unique_ptr<Part::GeomBSplineCurve> changedCurve(
    const SketchObject& sketch,
    int geometryId,
    int knotIndex,
    int multiplicityIncrement,
    BSplineKnotMultiplicityDiagnostic* result
)
{
    if (geometryId < 0 || geometryId > sketch.getHighestCurveIndex() || knotIndex < 0
        || (multiplicityIncrement != 1 && multiplicityIncrement != -1)) {
        return {};
    }
    const Part::Geometry* geometry = sketch.getGeometry(geometryId);
    if (!geometry || !geometry->is<Part::GeomBSplineCurve>()) {
        return {};
    }
    const auto* original = static_cast<const Part::GeomBSplineCurve*>(geometry);
    if (knotIndex >= original->countKnots()) {
        return {};
    }
    const int occKnotIndex = knotIndex + 1;
    const int degree = original->getDegree();
    const int oldMultiplicity = original->getMultiplicity(occKnotIndex);
    const int newMultiplicity = oldMultiplicity + multiplicityIncrement;
    if (newMultiplicity < 0 || newMultiplicity > degree) {
        return {};
    }
    const auto knotParameter = original->getKnots()[knotIndex];
    auto changed = std::unique_ptr<Part::GeomBSplineCurve>(
        static_cast<Part::GeomBSplineCurve*>(original->clone())
    );
    try {
        if (multiplicityIncrement > 0) {
            changed->increaseMultiplicity(occKnotIndex, newMultiplicity);
        }
        else if (!changed->removeKnot(occKnotIndex, newMultiplicity, 1.0e6)) {
            return {};
        }
    }
    catch (const Base::Exception&) {
        return {};
    }
    const auto exposedGeometry = static_cast<std::size_t>(
        changed->countPoles() + changed->countKnots()
    );
    if (exposedGeometry > MaxExposedBSplineGeometry) {
        return {};
    }
    if (newMultiplicity > 0) {
        if (changed->countKnots() != original->countKnots()
            || changed->getMultiplicity(occKnotIndex) != newMultiplicity
            || changed->getKnots()[knotIndex] != knotParameter) {
            return {};
        }
    }
    else {
        const auto knots = changed->getKnots();
        if (changed->countKnots() != original->countKnots() - 1
            || std::ranges::find(knots, knotParameter) != knots.end()) {
            return {};
        }
    }
    if (result) {
        result->geometryId = geometryId;
        result->knotIndex = knotIndex;
        result->knotParameter = knotParameter;
        result->degree = degree;
        result->oldMultiplicity = oldMultiplicity;
        result->newMultiplicity = newMultiplicity;
    }
    return changed;
}

}  // namespace

int SketchObject::replaceBSplineAndReconcileInternals(
    int geometryId,
    std::unique_ptr<Part::GeomBSplineCurve> changed,
    int* retainedInternalGeometryCount,
    int* deletedInternalGeometryCount,
    int* exposedInternalGeometryCount
)
{
    if (!changed || geometryId < 0 || geometryId > getHighestCurveIndex()) {
        return -1;
    }
    const Part::Geometry* geometryValue = getGeometry(geometryId);
    if (!geometryValue || !geometryValue->is<Part::GeomBSplineCurve>()) {
        return -1;
    }
    const auto* original = static_cast<const Part::GeomBSplineCurve*>(geometryValue);
    const auto oldPoles = original->getPoles();
    const auto oldKnots = original->getKnots();
    const auto newPoles = changed->getPoles();
    const auto newKnots = changed->getKnots();
    std::vector<int> deletedHelpers;
    std::set<int> encounteredHelpers;
    std::set<std::pair<InternalAlignmentType, int>> encounteredRoles;
    std::map<const Constraint*, int> retainedMappings;

    for (const auto* constraint : Constraints.getValues()) {
        if (constraint->Type != InternalAlignment || constraint->Second != geometryId) {
            continue;
        }
        const auto role = std::pair(constraint->AlignmentType, constraint->InternalAlignmentIndex);
        if (constraint->First <= geometryId || constraint->First > getHighestCurveIndex()
            || !encounteredHelpers.insert(constraint->First).second
            || !encounteredRoles.insert(role).second) {
            return -1;
        }
        int newIndex = -1;
        if (constraint->AlignmentType == BSplineControlPoint) {
            newIndex = mappedIndex(oldPoles, newPoles, constraint->InternalAlignmentIndex);
        }
        else if (constraint->AlignmentType == BSplineKnotPoint) {
            newIndex = mappedIndex(oldKnots, newKnots, constraint->InternalAlignmentIndex);
        }
        else {
            return -1;
        }
        if (newIndex < 0) {
            deletedHelpers.push_back(constraint->First);
        }
        else {
            retainedMappings.emplace(constraint, newIndex);
        }
    }

    std::vector<Constraint*> constraints;
    constraints.reserve(Constraints.getSize());
    for (auto* constraint : Constraints.getValues()) {
        const auto retained = retainedMappings.find(constraint);
        if (retained != retainedMappings.end()) {
            auto* mapped = constraint->clone();
            mapped->InternalAlignmentIndex = retained->second;
            constraints.push_back(mapped);
        }
        else if (constraint->Type != InternalAlignment || constraint->Second != geometryId) {
            constraints.push_back(constraint);
        }
    }

    if (retainedInternalGeometryCount) {
        *retainedInternalGeometryCount =
            static_cast<int>(encounteredHelpers.size() - deletedHelpers.size());
    }
    if (deletedInternalGeometryCount) {
        *deletedInternalGeometryCount = static_cast<int>(deletedHelpers.size());
    }
    if (exposedInternalGeometryCount) {
        *exposedInternalGeometryCount = 0;
    }

    Base::StateLocker lock(managedoperation, true);
    std::vector<Part::Geometry*> geometry(getInternalGeometry());
    GeometryFacade::copyId(original, changed.get());
    geometry[geometryId] = changed.release();
    {
        Base::StateLocker preventUpdate(internaltransaction, true);
        Geometry.setValues(std::move(geometry));
        Constraints.setValues(std::move(constraints));
    }
    Geometry.touch();
    if (!deletedHelpers.empty() && delGeometriesExclusiveList(deletedHelpers) < 0) {
        return -1;
    }
    const int exposed = exposeInternalGeometry(geometryId);
    if (exposed < 0) {
        return -1;
    }
    if (exposedInternalGeometryCount) {
        *exposedInternalGeometryCount = exposed;
    }
    solve();
    return 1;
}

int SketchObject::changeBSplineKnotMultiplicityPrepared(
    int geometryId,
    int knotIndex,
    int multiplicityIncrement,
    BSplineKnotMultiplicityDiagnostic* result
)
{
    auto changed = changedCurve(
        *this,
        geometryId,
        knotIndex,
        multiplicityIncrement,
        result
    );
    if (!changed) {
        return -1;
    }
    return replaceBSplineAndReconcileInternals(
        geometryId,
        std::move(changed),
        result ? &result->retainedInternalGeometryCount : nullptr,
        result ? &result->deletedInternalGeometryCount : nullptr,
        result ? &result->exposedInternalGeometryCount : nullptr
    );
}

int SketchObject::increaseBSplineKnotMultiplicityExact(int geometryId, int knotIndex)
{
    if (!diagnoseIncreaseBSplineKnotMultiplicity(geometryId, knotIndex)) {
        return -1;
    }
    return changeBSplineKnotMultiplicityPrepared(geometryId, knotIndex, 1, nullptr);
}

std::unique_ptr<BSplineKnotMultiplicityIncreaseDiagnostic>
SketchObject::diagnoseIncreaseBSplineKnotMultiplicity(int geometryId, int knotIndex) const
{
    return diagnoseChangeBSplineKnotMultiplicity(geometryId, knotIndex, 1);
}

int SketchObject::decreaseBSplineKnotMultiplicityExact(int geometryId, int knotIndex)
{
    if (!diagnoseDecreaseBSplineKnotMultiplicity(geometryId, knotIndex)) {
        return -1;
    }
    return changeBSplineKnotMultiplicityPrepared(geometryId, knotIndex, -1, nullptr);
}

std::unique_ptr<BSplineKnotMultiplicityDecreaseDiagnostic>
SketchObject::diagnoseDecreaseBSplineKnotMultiplicity(int geometryId, int knotIndex) const
{
    return diagnoseChangeBSplineKnotMultiplicity(geometryId, knotIndex, -1);
}

std::unique_ptr<BSplineKnotMultiplicityDiagnostic>
SketchObject::diagnoseChangeBSplineKnotMultiplicity(
    int geometryId,
    int knotIndex,
    int multiplicityIncrement
) const
{
    if (!changedCurve(*this, geometryId, knotIndex, multiplicityIncrement, nullptr)) {
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

    auto result = std::make_unique<BSplineKnotMultiplicityDiagnostic>();
    if (diagnostic->changeBSplineKnotMultiplicityPrepared(
            geometryId,
            knotIndex,
            multiplicityIncrement,
            result.get()
        )
        < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}
