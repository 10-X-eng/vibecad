// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <algorithm>
#include <map>
#include <memory>
#include <set>
#include <vector>

#include <Base/Tools.h>

#include "GeoEnum.h"
#include "SketchObject.h"

using namespace Sketcher;

namespace
{

bool validExactTargets(const SketchObject& sketch, const std::vector<int>& geometryIds)
{
    if (geometryIds.empty()) {
        return false;
    }
    const std::set<int> uniqueIds(geometryIds.begin(), geometryIds.end());
    if (uniqueIds.size() != geometryIds.size()) {
        return false;
    }
    return std::ranges::all_of(geometryIds, [&sketch](int geometryId) {
        return geometryId >= 0 && sketch.getGeometry(geometryId) != nullptr;
    });
}

bool isWholeLineAlignment(const Constraint& constraint)
{
    return (constraint.Type == Horizontal || constraint.Type == Vertical)
        && constraint.FirstPos == PointPos::none && constraint.SecondPos == PointPos::none;
}

bool isAxisSymmetry(const Constraint& constraint)
{
    return constraint.Type == Symmetric
        && (constraint.Third == GeoEnum::HAxis || constraint.Third == GeoEnum::VAxis)
        && constraint.ThirdPos == PointPos::none;
}

bool isPointOnAxis(const Constraint& constraint)
{
    return constraint.Type == PointOnObject
        && (constraint.Second == GeoEnum::HAxis || constraint.Second == GeoEnum::VAxis)
        && constraint.SecondPos == PointPos::none;
}

}  // namespace

int SketchObject::removeAxesAlignmentPrepared(
    const std::vector<int>& geometryIds,
    AxisAlignmentRemovalDiagnostic* result
)
{
    if (geometryIds.empty()) {
        return 0;
    }

    Base::StateLocker lock(managedoperation, true);
    const std::set<int> selected(geometryIds.begin(), geometryIds.end());
    const auto& currentConstraints = Constraints.getValues();
    std::vector<std::unique_ptr<Constraint>> replacements;
    replacements.reserve(currentConstraints.size());
    std::map<ConstraintType, int> referenceGeometry {
        {Horizontal, GeoEnum::GeoUndef},
        {Vertical, GeoEnum::GeoUndef},
    };
    int changed = 0;

    for (const auto* constraint : currentConstraints) {
        if (!constraint) {
            continue;
        }
        const bool involvesSelection = std::ranges::any_of(selected, [constraint](int geometryId) {
            return constraint->involvesGeoId(geometryId);
        });
        if (!involvesSelection) {
            replacements.emplace_back(constraint->clone());
            continue;
        }

        if (isWholeLineAlignment(*constraint)) {
            ++changed;
            if (result) {
                if (constraint->Type == Horizontal) {
                    ++result->removedHorizontalConstraints;
                }
                else {
                    ++result->removedVerticalConstraints;
                }
            }
            auto& reference = referenceGeometry[constraint->Type];
            if (reference == GeoEnum::GeoUndef) {
                reference = constraint->First;
                continue;
            }
            auto parallel = std::make_unique<Constraint>();
            parallel->Type = Parallel;
            parallel->First = reference;
            parallel->Second = constraint->First;
            replacements.push_back(std::move(parallel));
            if (result) {
                ++result->createdParallelConstraints;
            }
            continue;
        }

        if (isAxisSymmetry(*constraint)) {
            ++changed;
            if (result) {
                ++result->removedAxisSymmetryConstraints;
            }
            continue;
        }

        if (isPointOnAxis(*constraint)) {
            ++changed;
            if (result) {
                ++result->removedPointOnAxisConstraints;
            }
            continue;
        }

        if (constraint->Type == DistanceX || constraint->Type == DistanceY) {
            ++changed;
            auto distance = std::unique_ptr<Constraint>(constraint->clone());
            distance->Type = Distance;
            replacements.push_back(std::move(distance));
            if (result) {
                ++result->convertedDistanceConstraints;
            }
            continue;
        }

        replacements.emplace_back(constraint->clone());
    }

    if (changed == 0) {
        return 0;
    }
    std::vector<Constraint*> values;
    values.reserve(replacements.size());
    for (const auto& constraint : replacements) {
        values.push_back(constraint.get());
    }
    Constraints.setValues(values);
    return changed;
}

int SketchObject::removeAxesAlignmentExact(const std::vector<int>& geometryIds)
{
    if (!validExactTargets(*this, geometryIds)) {
        return -1;
    }
    const int changed = removeAxesAlignmentPrepared(geometryIds, nullptr);
    if (changed <= 0) {
        return -1;
    }
    solve();
    return changed;
}

std::unique_ptr<AxisAlignmentRemovalDiagnostic>
SketchObject::diagnoseRemoveAxesAlignment(const std::vector<int>& geometryIds) const
{
    if (!validExactTargets(*this, geometryIds)) {
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

    auto result = std::make_unique<AxisAlignmentRemovalDiagnostic>();
    result->geometryIds = geometryIds;
    if (diagnostic->removeAxesAlignmentPrepared(geometryIds, result.get()) <= 0) {
        return {};
    }
    diagnostic->solve();
    result->sketch = std::move(diagnostic);
    return result;
}
