// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <algorithm>
#include <cstddef>
#include <map>
#include <memory>
#include <set>
#include <utility>
#include <vector>

#include <GeomAbs_Shape.hxx>
#include <Precision.hxx>

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

std::unique_ptr<Part::GeomBSplineCurve>
reducedCurve(const SketchObject& sketch, int geometryId, int& oldDegree, int& newDegree)
{
    if (geometryId < 0 || geometryId > sketch.getHighestCurveIndex()) {
        return {};
    }
    const Part::Geometry* geometry = sketch.getGeometry(geometryId);
    if (!geometry || !geometry->is<Part::GeomBSplineCurve>()) {
        return {};
    }
    const auto* original = static_cast<const Part::GeomBSplineCurve*>(geometry);
    auto reduced = std::unique_ptr<Part::GeomBSplineCurve>(
        static_cast<Part::GeomBSplineCurve*>(original->clone())
    );
    oldDegree = reduced->getDegree();
    if (oldDegree <= 1) {
        return {};
    }
    try {
        reduced->approximate(Precision::Confusion(), 20, oldDegree - 1, GeomAbs_C0);
    }
    catch (const Base::Exception&) {
        return {};
    }
    newDegree = reduced->getDegree();
    const auto exposedGeometry = static_cast<std::size_t>(
        reduced->countPoles() + reduced->countKnots()
    );
    if (newDegree != oldDegree - 1 || exposedGeometry > MaxExposedBSplineGeometry) {
        return {};
    }
    return reduced;
}

}  // namespace

int SketchObject::decreaseBSplineDegreePrepared(
    int geometryId,
    BSplineDegreeDecreaseDiagnostic* result
)
{
    int oldDegree = 0;
    int newDegree = 0;
    auto reduced = reducedCurve(*this, geometryId, oldDegree, newDegree);
    if (!reduced) {
        return -1;
    }

    const auto* original = static_cast<const Part::GeomBSplineCurve*>(getGeometry(geometryId));
    const auto oldPoles = original->getPoles();
    const auto oldKnots = original->getKnots();
    const auto newPoles = reduced->getPoles();
    const auto newKnots = reduced->getKnots();
    std::vector<int> deletedHelpers;
    std::set<int> encounteredHelpers;
    std::map<const Constraint*, int> retainedMappings;

    for (const auto* constraint : Constraints.getValues()) {
        if (constraint->Type != InternalAlignment || constraint->Second != geometryId) {
            continue;
        }
        if (constraint->First <= geometryId || constraint->First > getHighestCurveIndex()
            || !encounteredHelpers.insert(constraint->First).second) {
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
            continue;
        }
        retainedMappings.emplace(constraint, newIndex);
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

    if (result) {
        result->geometryId = geometryId;
        result->oldDegree = oldDegree;
        result->newDegree = newDegree;
        result->retainedInternalGeometryCount =
            static_cast<int>(encounteredHelpers.size() - deletedHelpers.size());
        result->deletedInternalGeometryCount = static_cast<int>(deletedHelpers.size());
        result->exposedInternalGeometryCount = 0;
    }

    Base::StateLocker lock(managedoperation, true);
    std::vector<Part::Geometry*> geometry(getInternalGeometry());
    geometry[geometryId] = reduced.release();
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
    if (result) {
        result->exposedInternalGeometryCount = exposed;
    }
    solve();
    return 1;
}

int SketchObject::decreaseBSplineDegreeExact(int geometryId)
{
    if (!diagnoseDecreaseBSplineDegree(geometryId)) {
        return -1;
    }
    return decreaseBSplineDegreePrepared(geometryId, nullptr);
}

std::unique_ptr<BSplineDegreeDecreaseDiagnostic>
SketchObject::diagnoseDecreaseBSplineDegree(int geometryId) const
{
    int oldDegree = 0;
    int newDegree = 0;
    if (!reducedCurve(*this, geometryId, oldDegree, newDegree)) {
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

    auto result = std::make_unique<BSplineDegreeDecreaseDiagnostic>();
    if (diagnostic->decreaseBSplineDegreePrepared(geometryId, result.get()) < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}
