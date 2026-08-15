// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <cmath>
#include <map>
#include <set>
#include <utility>

#include <boost/uuid/uuid_io.hpp>

#include <App/Expression.h>
#include <Base/Exception.h>
#include <Base/Tools.h>

#include "GeoEnum.h"
#include "GeometryFacade.h"
#include "SketchObject.h"

using namespace Sketcher;

namespace
{

constexpr int MaxTransformCopies = 9999;

int selectedIndex(const std::vector<int>& geometryIds, int geometryId)
{
    if (geometryId == GeoEnum::GeoUndef) {
        return GeoEnum::GeoUndef;
    }
    const auto found = std::ranges::find(geometryIds, geometryId);
    return found == geometryIds.end()
        ? -1
        : static_cast<int>(std::distance(geometryIds.begin(), found));
}

bool isTransformableGeometry(const Part::Geometry* geometry)
{
    return geometry
        && (geometry->is<Part::GeomCircle>() || geometry->is<Part::GeomArcOfCircle>()
            || geometry->is<Part::GeomEllipse>() || geometry->is<Part::GeomArcOfEllipse>()
            || geometry->is<Part::GeomArcOfHyperbola>()
            || geometry->is<Part::GeomArcOfParabola>()
            || geometry->is<Part::GeomLineSegment>()
            || geometry->is<Part::GeomBSplineCurve>() || geometry->is<Part::GeomPoint>());
}

bool constraintReferencesGeometry(const Constraint* constraint, int geometryId)
{
    return constraint->First == geometryId
        || (constraint->Second == geometryId && constraint->Type != Sketcher::Radius
            && constraint->Type != Sketcher::Diameter && constraint->Type != Sketcher::Weight);
}

struct PendingExpression
{
    std::string constraintTag;
    std::string expression;
};

}  // namespace

std::vector<TranslateExpressionSource>
SketchObject::translateSourceExpressions(const std::vector<int>& geometryIds) const
{
    std::map<int, TranslateExpressionSource> expressions;
    const auto& constraints = Constraints.getValues();
    for (const int geometryId : geometryIds) {
        for (std::size_t index = 0; index < constraints.size(); ++index) {
            const auto* constraint = constraints[index];
            if (!constraint || !constraint->isDriving || !constraint->isDimensional()
                || !constraintReferencesGeometry(constraint, geometryId)) {
                continue;
            }
            const App::ObjectIdentifier path = Constraints.createPath(static_cast<int>(index));
            const auto info = getExpression(path);
            if (info.expression) {
                expressions[static_cast<int>(index)] = {
                    static_cast<int>(index),
                    geometryId,
                    info.expression->toString(),
                };
            }
        }
    }

    std::vector<TranslateExpressionSource> result;
    result.reserve(expressions.size());
    for (auto& [index, expression] : expressions) {
        (void)index;
        result.push_back(std::move(expression));
    }
    return result;
}

int SketchObject::translateExact(
    const std::vector<int>& geometryIds,
    const Base::Vector3d& firstVector,
    int copyCount,
    const Base::Vector3d& secondVector,
    int rowCount,
    bool equalizeDimensionalConstraints
)
{
    const auto sourceExpressions = translateSourceExpressions(geometryIds);
    return translatePrepared(
        geometryIds,
        firstVector,
        copyCount,
        secondVector,
        rowCount,
        equalizeDimensionalConstraints,
        sourceExpressions,
        true,
        nullptr
    );
}

int SketchObject::translatePrepared(
    const std::vector<int>& geometryIds,
    const Base::Vector3d& firstVector,
    int copyCount,
    const Base::Vector3d& secondVector,
    int rowCount,
    bool equalizeDimensionalConstraints,
    const std::vector<TranslateExpressionSource>& sourceExpressions,
    bool applyExpressions,
    std::vector<TranslateExpressionDiagnostic>* expressions
)
{
    if (geometryIds.empty() || copyCount < 0 || copyCount > MaxTransformCopies || rowCount < 0
        || rowCount > MaxTransformCopies || std::abs(firstVector.z) > Precision::Confusion()
        || firstVector.Length() < Precision::Confusion()
        || std::abs(secondVector.z) > Precision::Confusion()
        || (rowCount > 1 && secondVector.Length() < Precision::Confusion())) {
        return -1;
    }

    const std::set<int> uniqueGeometryIds(geometryIds.begin(), geometryIds.end());
    if (uniqueGeometryIds.size() != geometryIds.size()) {
        return -1;
    }
    const auto& initialConstraints = Constraints.getValues();
    for (const int geometryId : geometryIds) {
        const auto* geometry = getGeometry(geometryId);
        if ((geometryId < 0 && geometryId > GeoEnum::RefExt) || !isTransformableGeometry(geometry)) {
            return -1;
        }
        if (!GeometryFacade::isInternalAligned(geometry)) {
            continue;
        }
        const auto relation = std::ranges::find_if(initialConstraints, [geometryId](const auto* c) {
            return c && c->Type == Sketcher::InternalAlignment && c->First == geometryId;
        });
        if (relation == initialConstraints.end()
            || !uniqueGeometryIds.contains((*relation)->Second)) {
            return -1;
        }
    }

    Base::StateLocker lock(managedoperation, true);
    const bool deleteOriginals = copyCount == 0;
    const int copiesToMake = deleteOriginals ? 1 : copyCount;
    const int firstCreatedGeometry = getHighestCurveIndex() + 1;
    const int geometryGroupSize = static_cast<int>(geometryIds.size());

    std::vector<std::unique_ptr<Part::Geometry>> createdGeometry;
    createdGeometry.reserve(
        static_cast<std::size_t>(geometryGroupSize)
        * static_cast<std::size_t>(copiesToMake * rowCount + rowCount - 1)
    );
    for (int row = 0; row < rowCount; ++row) {
        for (int copy = 0; copy <= copiesToMake; ++copy) {
            if (row == 0 && copy == 0) {
                continue;
            }
            const Base::Vector3d translation = firstVector * copy + secondVector * row;
            for (const int geometryId : geometryIds) {
                auto geometry = std::unique_ptr<Part::Geometry>(getGeometry(geometryId)->copy());
                geometry->translate(translation);
                createdGeometry.push_back(std::move(geometry));
            }
        }
    }

    std::vector<std::unique_ptr<Constraint>> createdConstraints;
    std::vector<int> equalizedGeometryIds;
    for (const auto* constraint : initialConstraints) {
        const int firstIndex = selectedIndex(geometryIds, constraint->First);
        const int secondIndex = selectedIndex(geometryIds, constraint->Second);
        const int thirdIndex = selectedIndex(geometryIds, constraint->Third);
        for (int row = 0; row < rowCount; ++row) {
            for (int copy = 0; copy <= copiesToMake; ++copy) {
                if (row == 0 && copy == 0) {
                    continue;
                }
                const int rowOffset = geometryGroupSize * (copiesToMake + 1) * row;
                const int copyOffset = geometryGroupSize * (copy - 1);
                const int first = firstCreatedGeometry + firstIndex + copyOffset + rowOffset;
                const int second = firstCreatedGeometry + secondIndex + copyOffset + rowOffset;
                const int third = firstCreatedGeometry + thirdIndex + copyOffset + rowOffset;

                auto translated = std::unique_ptr<Constraint>(constraint->copy());
                translated->First = first;
                if ((constraint->Type == Symmetric || constraint->Type == Tangent
                     || constraint->Type == Perpendicular || constraint->Type == Angle)
                    && firstIndex >= 0 && secondIndex >= 0 && thirdIndex >= 0) {
                    translated->Second = second;
                    translated->Third = third;
                }
                else if ((constraint->Type == Coincident || constraint->Type == Tangent
                          || constraint->Type == Symmetric
                          || constraint->Type == Perpendicular || constraint->Type == Parallel
                          || constraint->Type == Equal || constraint->Type == Angle
                          || constraint->Type == PointOnObject
                          || constraint->Type == Horizontal || constraint->Type == Vertical
                          || constraint->Type == InternalAlignment)
                         && firstIndex >= 0 && secondIndex >= 0
                         && thirdIndex == GeoEnum::GeoUndef) {
                    translated->Second = second;
                }
                else if ((constraint->Type == Radius || constraint->Type == Diameter
                          || constraint->Type == Weight)
                         && firstIndex >= 0) {
                    if (deleteOriginals || !equalizeDimensionalConstraints) {
                        translated->setValue(constraint->getValue());
                    }
                    else {
                        translated->Type = Equal;
                        translated->First = constraint->First;
                        translated->Second = first;
                    }
                }
                else if ((constraint->Type == Distance || constraint->Type == DistanceX
                          || constraint->Type == DistanceY)
                         && firstIndex >= 0 && secondIndex >= 0) {
                    if (!deleteOriginals && equalizeDimensionalConstraints
                        && constraint->First == constraint->Second) {
                        if (std::ranges::find(equalizedGeometryIds, second)
                            != equalizedGeometryIds.end()) {
                            continue;
                        }
                        translated->Type = Equal;
                        translated->First = constraint->First;
                        translated->Second = second;
                        equalizedGeometryIds.push_back(second);
                    }
                    else {
                        translated->Second = second;
                    }
                }
                else if ((constraint->Type == Block || constraint->Type == Horizontal
                          || constraint->Type == Vertical)
                         && firstIndex >= 0) {
                    translated->First = first;
                }
                else {
                    continue;
                }
                createdConstraints.push_back(std::move(translated));
            }
        }
    }

    std::vector<Part::Geometry*> geometryPointers;
    geometryPointers.reserve(createdGeometry.size());
    for (const auto& geometry : createdGeometry) {
        geometryPointers.push_back(geometry.get());
    }
    if (!geometryPointers.empty()) {
        addGeometry(geometryPointers);
    }
    for (const auto& constraint : createdConstraints) {
        addConstraint(constraint.get());
    }

    std::vector<PendingExpression> pendingExpressions;
    const auto& withCreatedConstraints = Constraints.getValues();
    const int expressionCopies = deleteOriginals ? 1 : copyCount;
    for (std::size_t constraintIndex = 0; constraintIndex < withCreatedConstraints.size();
         ++constraintIndex) {
        const auto* constraint = withCreatedConstraints[constraintIndex];
        if (!constraint->isDriving || !constraint->isDimensional()) {
            continue;
        }
        for (const auto& source : sourceExpressions) {
            const int originalIndex = selectedIndex(geometryIds, source.geometryId);
            if (originalIndex < 0) {
                continue;
            }
            bool matches = false;
            for (int row = 0; row < rowCount && !matches; ++row) {
                for (int copy = 1; copy <= expressionCopies; ++copy) {
                    const int expectedGeometry = firstCreatedGeometry + originalIndex
                        + geometryGroupSize * (copy - 1)
                        + geometryGroupSize * expressionCopies * row;
                    if (constraintReferencesGeometry(constraint, expectedGeometry)) {
                        matches = true;
                        break;
                    }
                }
            }
            if (!matches) {
                continue;
            }
            if (applyExpressions) {
                const auto path = Constraints.createPath(static_cast<int>(constraintIndex));
                auto expression = std::shared_ptr<App::Expression>(
                    App::Expression::parse(this, source.expression)
                );
                setExpression(path, std::move(expression));
            }
            else {
                pendingExpressions.push_back(
                    {boost::uuids::to_string(constraint->getTag()), source.expression}
                );
            }
            break;
        }
    }

    if (deleteOriginals && delGeometries(geometryIds) != 0) {
        return -1;
    }

    if (!applyExpressions && expressions) {
        const auto& finalConstraints = Constraints.getValues();
        for (const auto& pending : pendingExpressions) {
            const auto found = std::ranges::find_if(finalConstraints, [&pending](const auto* c) {
                return c && boost::uuids::to_string(c->getTag()) == pending.constraintTag;
            });
            if (found == finalConstraints.end()) {
                return -1;
            }
            const int index = static_cast<int>(std::distance(finalConstraints.begin(), found));
            expressions->push_back(
                {index, "Constraints[" + std::to_string(index) + "]", pending.expression}
            );
        }
    }

    solve();
    return static_cast<int>(createdGeometry.size());
}

std::unique_ptr<TranslateDiagnostic> SketchObject::diagnoseTranslate(
    const std::vector<int>& geometryIds,
    const Base::Vector3d& firstVector,
    int copyCount,
    const Base::Vector3d& secondVector,
    int rowCount,
    bool equalizeDimensionalConstraints
) const
{
    const auto sourceExpressions = translateSourceExpressions(geometryIds);
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

    auto result = std::make_unique<TranslateDiagnostic>();
    result->geometryIds = geometryIds;
    result->firstVector = firstVector;
    result->secondVector = secondVector;
    result->copyCount = copyCount;
    result->rowCount = rowCount;
    result->equalizeDimensionalConstraints = equalizeDimensionalConstraints;
    result->deletedOriginals = copyCount == 0;
    if (diagnostic->translatePrepared(
            geometryIds,
            firstVector,
            copyCount,
            secondVector,
            rowCount,
            equalizeDimensionalConstraints,
            sourceExpressions,
            false,
            &result->expressions
        ) < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}

int SketchObject::rotateExact(
    const std::vector<int>& geometryIds,
    const Base::Vector3d& center,
    double totalAngleRadians,
    int copyCount,
    bool equalizeDimensionalConstraints
)
{
    const auto sourceExpressions = translateSourceExpressions(geometryIds);
    return rotatePrepared(
        geometryIds,
        center,
        totalAngleRadians,
        copyCount,
        equalizeDimensionalConstraints,
        sourceExpressions,
        true,
        nullptr
    );
}

int SketchObject::rotatePrepared(
    const std::vector<int>& geometryIds,
    const Base::Vector3d& center,
    double totalAngleRadians,
    int copyCount,
    bool equalizeDimensionalConstraints,
    const std::vector<RotateExpressionSource>& sourceExpressions,
    bool applyExpressions,
    std::vector<RotateExpressionDiagnostic>* expressions
)
{
    if (geometryIds.empty() || copyCount < 0 || copyCount > MaxTransformCopies
        || !std::isfinite(center.x) || !std::isfinite(center.y) || !std::isfinite(center.z)
        || !std::isfinite(totalAngleRadians) || std::abs(center.z) > Precision::Confusion()
        || std::abs(totalAngleRadians) < Precision::Confusion()) {
        return -1;
    }

    const std::set<int> uniqueGeometryIds(geometryIds.begin(), geometryIds.end());
    if (uniqueGeometryIds.size() != geometryIds.size()) {
        return -1;
    }
    const auto& initialConstraints = Constraints.getValues();
    for (const int geometryId : geometryIds) {
        const auto* geometry = getGeometry(geometryId);
        if ((geometryId < 0 && geometryId > GeoEnum::RefExt)
            || !isTransformableGeometry(geometry)) {
            return -1;
        }
        if (!GeometryFacade::isInternalAligned(geometry)) {
            continue;
        }
        const auto relation = std::ranges::find_if(initialConstraints, [geometryId](const auto* c) {
            return c && c->Type == Sketcher::InternalAlignment && c->First == geometryId;
        });
        if (relation == initialConstraints.end()
            || !uniqueGeometryIds.contains((*relation)->Second)) {
            return -1;
        }
    }

    Base::StateLocker lock(managedoperation, true);
    const bool deleteOriginals = copyCount == 0;
    const int copiesToMake = deleteOriginals ? 1 : copyCount;
    const double individualAngle = totalAngleRadians / copiesToMake;
    const int firstCreatedGeometry = getHighestCurveIndex() + 1;
    const int geometryGroupSize = static_cast<int>(geometryIds.size());

    std::vector<std::unique_ptr<Part::Geometry>> createdGeometry;
    createdGeometry.reserve(
        static_cast<std::size_t>(geometryGroupSize) * static_cast<std::size_t>(copiesToMake)
    );
    for (int copy = 1; copy <= copiesToMake; ++copy) {
        const double angle = individualAngle * copy;
        const Base::Matrix4D matrix(center, Base::Vector3d(0.0, 0.0, 1.0), angle);
        for (const int geometryId : geometryIds) {
            auto geometry = std::unique_ptr<Part::Geometry>(getGeometry(geometryId)->copy());
            geometry->reverseIfReversed();
            geometry->transform(matrix);
            createdGeometry.push_back(std::move(geometry));
        }
    }

    std::vector<std::unique_ptr<Constraint>> createdConstraints;
    std::vector<int> equalizedGeometryIds;
    for (const auto* constraint : initialConstraints) {
        const int firstIndex = selectedIndex(geometryIds, constraint->First);
        const int secondIndex = selectedIndex(geometryIds, constraint->Second);
        const int thirdIndex = selectedIndex(geometryIds, constraint->Third);
        for (int copy = 0; copy < copiesToMake; ++copy) {
            const int first = firstCreatedGeometry + firstIndex + geometryGroupSize * copy;
            const int second = firstCreatedGeometry + secondIndex + geometryGroupSize * copy;
            const int third = firstCreatedGeometry + thirdIndex + geometryGroupSize * copy;

            auto rotated = std::unique_ptr<Constraint>(constraint->copy());
            rotated->First = first;
            if ((constraint->Type == Symmetric || constraint->Type == Tangent
                 || constraint->Type == Perpendicular || constraint->Type == Angle)
                && firstIndex >= 0 && secondIndex >= 0 && thirdIndex >= 0) {
                rotated->Second = second;
                rotated->Third = third;
            }
            else if ((constraint->Type == Coincident || constraint->Type == Tangent
                      || constraint->Type == Symmetric || constraint->Type == Perpendicular
                      || constraint->Type == Parallel || constraint->Type == Equal
                      || constraint->Type == Angle || constraint->Type == PointOnObject
                      || constraint->Type == InternalAlignment)
                     && firstIndex >= 0 && secondIndex >= 0
                     && thirdIndex == GeoEnum::GeoUndef) {
                rotated->Second = second;
            }
            else if ((constraint->Type == Radius || constraint->Type == Diameter
                      || constraint->Type == Weight)
                     && firstIndex >= 0) {
                if (deleteOriginals || !equalizeDimensionalConstraints) {
                    rotated->setValue(constraint->getValue());
                }
                else {
                    rotated->Type = Equal;
                    rotated->First = constraint->First;
                    rotated->Second = first;
                }
            }
            else if ((constraint->Type == Distance || constraint->Type == DistanceX
                      || constraint->Type == DistanceY)
                     && firstIndex >= 0) {
                if (!deleteOriginals && equalizeDimensionalConstraints
                    && (constraint->First == constraint->Second || secondIndex < 0)) {
                    if (std::ranges::find(equalizedGeometryIds, first)
                        != equalizedGeometryIds.end()) {
                        continue;
                    }
                    rotated->Type = Equal;
                    rotated->First = constraint->First;
                    rotated->Second = first;
                    equalizedGeometryIds.push_back(first);
                }
                else if (constraint->Type == Distance) {
                    if (secondIndex >= 0) {
                        rotated->Second = second;
                    }
                }
                else {
                    continue;
                }
            }
            else if (constraint->Type == Block && firstIndex >= 0) {
                rotated->First = first;
            }
            else {
                continue;
            }
            createdConstraints.push_back(std::move(rotated));
        }
    }

    std::vector<Part::Geometry*> geometryPointers;
    geometryPointers.reserve(createdGeometry.size());
    for (const auto& geometry : createdGeometry) {
        geometryPointers.push_back(geometry.get());
    }
    if (!geometryPointers.empty()) {
        addGeometry(geometryPointers);
    }
    for (const auto& constraint : createdConstraints) {
        addConstraint(constraint.get());
    }

    std::vector<PendingExpression> pendingExpressions;
    const auto& withCreatedConstraints = Constraints.getValues();
    for (std::size_t constraintIndex = 0; constraintIndex < withCreatedConstraints.size();
         ++constraintIndex) {
        const auto* constraint = withCreatedConstraints[constraintIndex];
        if (!constraint->isDriving || !constraint->isDimensional()) {
            continue;
        }
        for (const auto& source : sourceExpressions) {
            const int originalIndex = selectedIndex(geometryIds, source.geometryId);
            if (originalIndex < 0) {
                continue;
            }
            bool matches = false;
            for (int copy = 0; copy < copiesToMake; ++copy) {
                const int expectedGeometry = firstCreatedGeometry + originalIndex
                    + geometryGroupSize * copy;
                if (constraintReferencesGeometry(constraint, expectedGeometry)) {
                    matches = true;
                    break;
                }
            }
            if (!matches) {
                continue;
            }
            if (applyExpressions) {
                const auto path = Constraints.createPath(static_cast<int>(constraintIndex));
                auto expression = std::shared_ptr<App::Expression>(
                    App::Expression::parse(this, source.expression)
                );
                setExpression(path, std::move(expression));
            }
            else {
                pendingExpressions.push_back(
                    {boost::uuids::to_string(constraint->getTag()), source.expression}
                );
            }
            break;
        }
    }

    if (deleteOriginals && delGeometries(geometryIds) != 0) {
        return -1;
    }

    if (!applyExpressions && expressions) {
        const auto& finalConstraints = Constraints.getValues();
        for (const auto& pending : pendingExpressions) {
            const auto found = std::ranges::find_if(finalConstraints, [&pending](const auto* c) {
                return c && boost::uuids::to_string(c->getTag()) == pending.constraintTag;
            });
            if (found == finalConstraints.end()) {
                return -1;
            }
            const int index = static_cast<int>(std::distance(finalConstraints.begin(), found));
            expressions->push_back(
                {index, "Constraints[" + std::to_string(index) + "]", pending.expression}
            );
        }
    }

    solve();
    return static_cast<int>(createdGeometry.size());
}

std::unique_ptr<RotateDiagnostic> SketchObject::diagnoseRotate(
    const std::vector<int>& geometryIds,
    const Base::Vector3d& center,
    double totalAngleRadians,
    int copyCount,
    bool equalizeDimensionalConstraints
) const
{
    const auto sourceExpressions = translateSourceExpressions(geometryIds);
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

    auto result = std::make_unique<RotateDiagnostic>();
    result->geometryIds = geometryIds;
    result->center = center;
    result->totalAngleRadians = totalAngleRadians;
    result->copyCount = copyCount;
    result->equalizeDimensionalConstraints = equalizeDimensionalConstraints;
    result->deletedOriginals = copyCount == 0;
    if (diagnostic->rotatePrepared(
            geometryIds,
            center,
            totalAngleRadians,
            copyCount,
            equalizeDimensionalConstraints,
            sourceExpressions,
            false,
            &result->expressions
        ) < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}
