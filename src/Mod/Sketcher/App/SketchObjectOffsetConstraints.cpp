// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <algorithm>
#include <cmath>
#include <memory>
#include <utility>
#include <vector>

#include <Base/Tools.h>

#include "GeoEnum.h"
#include "GeometryFacade.h"
#include "SketchObject.h"
#include "SketchObjectOffsetInternal.h"

using namespace Sketcher;

namespace
{

struct CoincidencePoints
{
    PointPos first1 {PointPos::none};
    PointPos second1 {PointPos::none};
    PointPos first2 {PointPos::none};
    PointPos second2 {PointPos::none};
};

std::unique_ptr<Constraint> relation(
    ConstraintType type,
    int first,
    PointPos firstPos,
    int second = GeoEnum::GeoUndef,
    PointPos secondPos = PointPos::none
)
{
    auto constraint = std::make_unique<Constraint>();
    constraint->Type = type;
    constraint->First = first;
    constraint->FirstPos = firstPos;
    constraint->Second = second;
    constraint->SecondPos = secondPos;
    return constraint;
}

std::unique_ptr<Constraint> relation(ConstraintType type, int first, int second)
{
    return relation(type, first, PointPos::none, second, PointPos::none);
}

std::unique_ptr<Constraint> dimension(
    int first,
    PointPos firstPos,
    int second,
    PointPos secondPos,
    double value
)
{
    auto constraint = relation(Distance, first, firstPos, second, secondPos);
    constraint->setValue(value);
    return constraint;
}

std::unique_ptr<Constraint> dimension(int first, int second, double value)
{
    return dimension(first, PointPos::none, second, PointPos::none, value);
}

std::unique_ptr<Constraint> dimension(int first, double value)
{
    return dimension(first, PointPos::none, GeoEnum::GeoUndef, PointPos::none, value);
}

void addConstraints(
    SketchObject& sketch,
    const std::vector<std::unique_ptr<Constraint>>& constraints
)
{
    if (constraints.empty()) {
        return;
    }
    std::vector<Constraint*> values;
    values.reserve(constraints.size());
    for (const auto& constraint : constraints) {
        values.push_back(constraint.get());
    }
    sketch.addConstraints(values);
}

CoincidencePoints coincidencePoints(
    const SketchObject& sketch,
    int first,
    int second,
    bool tangentOnly = false
)
{
    CoincidencePoints result;
    bool foundFirst = false;
    for (const auto* constraint : sketch.Constraints.getValues()) {
        if (!constraint
            || ((tangentOnly || constraint->Type != Coincident)
                && constraint->Type != Tangent)
            || constraint->FirstPos == PointPos::mid
            || constraint->FirstPos == PointPos::none
            || constraint->SecondPos == PointPos::mid
            || constraint->SecondPos == PointPos::none
            || !((constraint->First == first && constraint->Second == second)
                 || (constraint->First == second && constraint->Second == first))) {
            continue;
        }
        const auto firstPosition = constraint->First == first ? constraint->FirstPos
                                                              : constraint->SecondPos;
        const auto secondPosition = constraint->First == second ? constraint->FirstPos
                                                                 : constraint->SecondPos;
        if (!foundFirst) {
            result.first1 = firstPosition;
            result.second1 = secondPosition;
            foundFirst = true;
        }
        else {
            result.first2 = firstPosition;
            result.second2 = secondPosition;
            break;
        }
    }
    return result;
}

bool curveIsClosed(const SketchObject& sketch, const std::vector<int>& curve)
{
    if (curve.size() > 2) {
        return OffsetInternal::geometriesMeet(sketch, curve.front(), curve.back());
    }
    if (curve.size() == 2) {
        const auto points = coincidencePoints(sketch, curve.front(), curve.back());
        return points.first1 != PointPos::none && points.first2 != PointPos::none;
    }
    return false;
}

class OffsetConstraintBuilder
{
public:
    OffsetConstraintBuilder(
        SketchObject& sketch,
        const std::vector<int>& sourceGeometryIds,
        const std::vector<int>& offsetGeometryIds,
        double offsetLength
    )
        : sketch(sketch)
        , sourceGeometryIds(sourceGeometryIds)
        , offsetGeometryIds(offsetGeometryIds)
        , offsetLength(offsetLength)
        , connectorBase(sketch.Geometry.getSize() - 1)
    {}

    void apply()
    {
        for (const auto& curve : OffsetInternal::connectedCurves(sketch, offsetGeometryIds)) {
            constrainCurve(curve);
        }
        finish();
    }

private:
    void addConnector(
        const Base::Vector3d& first,
        const Base::Vector3d& second,
        int offsetId,
        int sourceId
    )
    {
        auto connector = std::make_unique<Part::GeomLineSegment>();
        connector->setPoints(first, second);
        GeometryFacade::setConstruction(connector.get(), true);
        connectors.push_back(std::move(connector));
        const int connectorId = connectorBase + static_cast<int>(connectors.size());
        connectorConstraints.push_back(relation(Perpendicular, connectorId, offsetId));
        connectorConstraints.push_back(
            relation(PointOnObject, connectorId, PointPos::start, offsetId));
        connectorConstraints.push_back(
            relation(PointOnObject, connectorId, PointPos::end, sourceId));
        candidateOffset = offsetId;
        candidateSource = sourceId;
    }

    bool constrainCircles(int offsetId, int sourceId)
    {
        const auto* offsetGeometry = sketch.getGeometry(offsetId);
        const auto* sourceGeometry = sketch.getGeometry(sourceId);
        if (!offsetGeometry->is<Part::GeomCircle>()
            || !sourceGeometry->is<Part::GeomCircle>()) {
            return false;
        }
        const auto* offsetCircle = static_cast<const Part::GeomCircle*>(offsetGeometry);
        const auto* sourceCircle = static_cast<const Part::GeomCircle*>(sourceGeometry);
        if ((offsetCircle->getCenter() - sourceCircle->getCenter()).Length()
                >= Precision::Confusion()) {
            return false;
        }
        constraints.push_back(
            relation(Coincident, offsetId, PointPos::mid, sourceId, PointPos::mid));
        auto first = offsetCircle->getCenter();
        auto second = sourceCircle->getCenter();
        first.x += offsetCircle->getRadius();
        second.x += sourceCircle->getRadius();
        addConnector(first, second, offsetId, sourceId);
        return true;
    }

    bool constrainLines(
        int offsetId,
        int sourceId,
        bool createConnector,
        bool forceCreate,
        bool rerunningFirst
    )
    {
        const auto* offsetGeometry = sketch.getGeometry(offsetId);
        const auto* sourceGeometry = sketch.getGeometry(sourceId);
        if (!offsetGeometry->is<Part::GeomLineSegment>()
            || !sourceGeometry->is<Part::GeomLineSegment>()) {
            return false;
        }
        const auto* offsetLine =
            static_cast<const Part::GeomLineSegment*>(offsetGeometry);
        const auto* sourceLine =
            static_cast<const Part::GeomLineSegment*>(sourceGeometry);
        const Base::Vector3d offsetStart = offsetLine->getStartPoint();
        const Base::Vector3d offsetEnd = offsetLine->getEndPoint();
        const Base::Vector3d sourceStart = sourceLine->getStartPoint();
        const Base::Vector3d sourceEnd = sourceLine->getEndPoint();
        if (((offsetEnd - offsetStart) % (sourceEnd - sourceStart)).Length()
            >= Precision::Intersection()) {
            return false;
        }
        Base::Vector3d projected;
        projected.ProjectToLine(offsetStart - sourceStart, sourceEnd - sourceStart);
        if (projected.Length() - std::abs(offsetLength) >= Precision::Confusion()) {
            return false;
        }
        if (!forceCreate && !rerunningFirst) {
            constraints.push_back(relation(Parallel, offsetId, sourceId));
        }
        if (createConnector) {
            addConnector(offsetStart, offsetStart + projected, offsetId, sourceId);
        }
        return true;
    }

    bool constrainArc(
        int offsetId,
        int sourceId,
        bool createConnector,
        bool forceCreate
    )
    {
        const auto* offsetGeometry = sketch.getGeometry(offsetId);
        if (!offsetGeometry->is<Part::GeomArcOfCircle>()) {
            return false;
        }
        const auto* offsetArc =
            static_cast<const Part::GeomArcOfCircle*>(offsetGeometry);
        const Base::Vector3d offsetCenter = offsetArc->getCenter();
        const auto* sourceGeometry = sketch.getGeometry(sourceId);
        if (sourceGeometry->is<Part::GeomArcOfCircle>()) {
            const auto* sourceArc =
                static_cast<const Part::GeomArcOfCircle*>(sourceGeometry);
            const Base::Vector3d sourceCenter = sourceArc->getCenter();
            const Base::Vector3d sourceStart = sourceArc->getStartPoint(true);
            const Base::Vector3d sourceEnd = sourceArc->getEndPoint(true);
            if ((offsetCenter - sourceCenter).Length() < Precision::Confusion()) {
                constraints.push_back(
                    relation(Coincident, offsetId, PointPos::mid, sourceId, PointPos::mid));
                if (createConnector) {
                    auto first = offsetCenter;
                    auto second = sourceCenter;
                    first.x += offsetArc->getRadius();
                    second.x += sourceArc->getRadius();
                    addConnector(first, second, offsetId, sourceId);
                }
                return true;
            }
            PointPos sourcePosition = PointPos::none;
            if ((offsetCenter - sourceStart).Length() < Precision::Confusion()) {
                sourcePosition = PointPos::start;
            }
            else if ((offsetCenter - sourceEnd).Length() < Precision::Confusion()) {
                sourcePosition = PointPos::end;
            }
            if (sourcePosition == PointPos::none) {
                return false;
            }
            constraints.push_back(
                relation(Coincident, offsetId, PointPos::mid, sourceId, sourcePosition));
            if (forceCreate) {
                auto radius = relation(Radius, offsetId, GeoEnum::GeoUndef);
                radius->setValue(offsetLength);
                constraints.push_back(std::move(radius));
            }
            return true;
        }

        Base::Vector3d sourceStart;
        Base::Vector3d sourceEnd;
        if (!OffsetInternal::getEndpoints(
                sketch,
                sourceId,
                sourceStart,
                sourceEnd
            )) {
            return false;
        }
        PointPos sourcePosition = PointPos::none;
        if ((offsetCenter - sourceStart).Length() < Precision::Confusion()) {
            sourcePosition = PointPos::start;
        }
        else if ((offsetCenter - sourceEnd).Length() < Precision::Confusion()) {
            sourcePosition = PointPos::end;
        }
        if (sourcePosition == PointPos::none) {
            return false;
        }
        constraints.push_back(
            relation(Coincident, offsetId, PointPos::mid, sourceId, sourcePosition));
        candidateOffset = offsetId;
        candidateSource = sourceId;
        return true;
    }

    void constrainGeometry(
        int offsetId,
        bool createConnector,
        bool forceCreate,
        bool rerunningFirst
    )
    {
        const auto* offsetGeometry = sketch.getGeometry(offsetId);
        for (const int sourceId : sourceGeometryIds) {
            const auto* sourceGeometry = sketch.getGeometry(sourceId);
            bool matched = false;
            if (offsetGeometry->is<Part::GeomCircle>()
                && sourceGeometry->is<Part::GeomCircle>()) {
                matched = constrainCircles(offsetId, sourceId);
            }
            else if (offsetGeometry->is<Part::GeomLineSegment>()
                     && sourceGeometry->is<Part::GeomLineSegment>()) {
                matched = constrainLines(
                    offsetId,
                    sourceId,
                    createConnector,
                    forceCreate,
                    rerunningFirst
                );
            }
            else if (offsetGeometry->is<Part::GeomArcOfCircle>()) {
                matched = constrainArc(offsetId, sourceId, createConnector, forceCreate);
            }
            if (matched) {
                break;
            }
        }
    }

    void constrainCurve(const std::vector<int>& curve)
    {
        const bool closed = curveIsClosed(sketch, curve);
        bool atLeastOneConnector = false;
        bool rerunFirstAfterThis = false;
        bool rerunningFirst = false;
        bool inTangentGroup = false;

        for (std::size_t index = 0; index < curve.size(); ++index) {
            bool createConnector = true;
            bool forceCreate = false;
            if (!inTangentGroup && (!closed || index != 0 || rerunningFirst)) {
                atLeastOneConnector = true;
            }
            else {
                createConnector = false;
            }
            if (index + 1 < curve.size()) {
                inTangentGroup = coincidencePoints(
                                     sketch,
                                     curve[index],
                                     curve[index + 1],
                                     true
                ).first1
                    != PointPos::none;
            }
            else if (closed) {
                inTangentGroup = coincidencePoints(
                                     sketch,
                                     curve[index],
                                     curve.front(),
                                     true
                ).first1
                    != PointPos::none;
                if (inTangentGroup && !atLeastOneConnector) {
                    createConnector = true;
                    forceCreate = true;
                }
                else if (!inTangentGroup) {
                    rerunFirstAfterThis = true;
                }
            }

            constrainGeometry(curve[index], createConnector, forceCreate, rerunningFirst);
            if (static_cast<int>(connectors.size()) != previousConnectorCount) {
                previousConnectorCount = static_cast<int>(connectors.size());
                if (previousConnectorCount != 1) {
                    constraints.push_back(relation(
                        Equal,
                        connectorBase + previousConnectorCount,
                        connectorBase + 1
                    ));
                }
            }
            if (rerunningFirst) {
                break;
            }
            if (rerunFirstAfterThis) {
                index = static_cast<std::size_t>(-1);
                rerunningFirst = true;
            }
        }
    }

    void finish()
    {
        if (connectors.size() >= 2) {
            constraints.push_back(dimension(connectorBase + 1, std::abs(offsetLength)));
            std::vector<Part::Geometry*> values;
            values.reserve(connectors.size());
            for (const auto& connector : connectors) {
                values.push_back(connector.get());
            }
            sketch.addGeometry(values);
            addConstraints(sketch, connectorConstraints);
        }
        else if (candidateOffset != GeoEnum::GeoUndef
                 && candidateSource != GeoEnum::GeoUndef) {
            const auto* offsetGeometry = sketch.getGeometry(candidateOffset);
            const auto* sourceGeometry = sketch.getGeometry(candidateSource);
            if (offsetGeometry->is<Part::GeomCircle>()) {
                constraints.push_back(dimension(
                    candidateOffset,
                    candidateSource,
                    std::abs(offsetLength)
                ));
            }
            else if (offsetGeometry->is<Part::GeomLineSegment>()) {
                constraints.push_back(dimension(
                    candidateOffset,
                    PointPos::start,
                    candidateSource,
                    PointPos::none,
                    std::abs(offsetLength)
                ));
            }
            else if (offsetGeometry->is<Part::GeomArcOfCircle>()) {
                constraints.push_back(dimension(
                    candidateOffset,
                    sourceGeometry->is<Part::GeomArcOfCircle>() ? PointPos::start
                                                               : PointPos::mid,
                    sourceGeometry->is<Part::GeomArcOfCircle>() ? candidateSource
                                                               : candidateOffset,
                    PointPos::start,
                    std::abs(offsetLength)
                ));
            }
        }
        addConstraints(sketch, constraints);
    }

private:
    SketchObject& sketch;
    const std::vector<int>& sourceGeometryIds;
    const std::vector<int>& offsetGeometryIds;
    double offsetLength;
    int connectorBase;
    std::vector<std::unique_ptr<Part::Geometry>> connectors;
    std::vector<std::unique_ptr<Constraint>> connectorConstraints;
    std::vector<std::unique_ptr<Constraint>> constraints;
    int candidateOffset {GeoEnum::GeoUndef};
    int candidateSource {GeoEnum::GeoUndef};
    int previousConnectorCount {0};
};

}  // namespace

bool OffsetInternal::getEndpoints(
    const SketchObject& sketch,
    int geometryId,
    Base::Vector3d& start,
    Base::Vector3d& end
)
{
    const auto* geometry = sketch.getGeometry(geometryId);
    if (geometry && geometry->is<Part::GeomLineSegment>()) {
        const auto* line = static_cast<const Part::GeomLineSegment*>(geometry);
        start = line->getStartPoint();
        end = line->getEndPoint();
        return true;
    }
    if (geometry && geometry->is<Part::GeomArcOfCircle>()) {
        const auto* arc = static_cast<const Part::GeomArcOfCircle*>(geometry);
        start = arc->getStartPoint(true);
        end = arc->getEndPoint(true);
        return true;
    }
    return false;
}

bool OffsetInternal::geometriesMeet(const SketchObject& sketch, int first, int second)
{
    Base::Vector3d firstStart;
    Base::Vector3d firstEnd;
    Base::Vector3d secondStart;
    Base::Vector3d secondEnd;
    if (!getEndpoints(sketch, first, firstStart, firstEnd)
        || !getEndpoints(sketch, second, secondStart, secondEnd)) {
        return false;
    }
    return (firstStart - secondStart).Length() < Precision::Confusion()
        || (firstStart - secondEnd).Length() < Precision::Confusion()
        || (firstEnd - secondStart).Length() < Precision::Confusion()
        || (firstEnd - secondEnd).Length() < Precision::Confusion();
}

std::vector<std::vector<int>> OffsetInternal::connectedCurves(
    const SketchObject& sketch,
    const std::vector<int>& geometryIds
)
{
    std::vector<std::vector<int>> curves;
    for (const int geometryId : geometryIds) {
        const auto* geometry = sketch.getGeometry(geometryId);
        if (geometry && geometry->is<Part::GeomCircle>()) {
            curves.push_back({geometryId});
            continue;
        }

        bool inserted = false;
        int insertedIn = -1;
        for (std::size_t curveIndex = 0; curveIndex < curves.size(); ++curveIndex) {
            for (std::size_t memberIndex = 0; memberIndex < curves[curveIndex].size();
                 ++memberIndex) {
                if (!geometriesMeet(sketch, geometryId, curves[curveIndex][memberIndex])) {
                    continue;
                }
                if (inserted && insertedIn != static_cast<int>(curveIndex)) {
                    if (curves[insertedIn].front() == geometryId) {
                        if (memberIndex == 0) {
                            std::ranges::reverse(curves[curveIndex]);
                        }
                        curves[curveIndex].insert(
                            curves[curveIndex].end(),
                            curves[insertedIn].begin(),
                            curves[insertedIn].end()
                        );
                        curves.erase(curves.begin() + insertedIn);
                    }
                    else {
                        if (memberIndex != 0) {
                            std::ranges::reverse(curves[curveIndex]);
                        }
                        curves[insertedIn].insert(
                            curves[insertedIn].end(),
                            curves[curveIndex].begin(),
                            curves[curveIndex].end()
                        );
                        curves.erase(curves.begin() + static_cast<std::ptrdiff_t>(curveIndex));
                    }
                    --curveIndex;
                }
                else {
                    if (memberIndex == curves[curveIndex].size() - 1) {
                        curves[curveIndex].push_back(geometryId);
                    }
                    else {
                        curves[curveIndex].insert(
                            curves[curveIndex].begin()
                                + static_cast<std::ptrdiff_t>(memberIndex),
                            geometryId
                        );
                    }
                    insertedIn = static_cast<int>(curveIndex);
                    inserted = true;
                }
                break;
            }
        }
        if (!inserted) {
            curves.push_back({geometryId});
        }
    }
    return curves;
}

void OffsetInternal::constrainOffset(
    SketchObject& sketch,
    const std::vector<int>& sourceGeometryIds,
    const std::vector<int>& offsetGeometryIds,
    double offsetLength
)
{
    OffsetConstraintBuilder(
        sketch,
        sourceGeometryIds,
        offsetGeometryIds,
        offsetLength
    ).apply();
}
