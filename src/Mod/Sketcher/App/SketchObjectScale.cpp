// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <algorithm>
#include <cmath>
#include <set>
#include <utility>

#include <Base/Tools.h>

#include "GeoEnum.h"
#include "GeometryFacade.h"
#include "SketchObject.h"

using namespace Sketcher;

namespace
{

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

bool isScalableGeometry(const Part::Geometry* geometry)
{
    return geometry
        && (geometry->is<Part::GeomCircle>() || geometry->is<Part::GeomArcOfCircle>()
            || geometry->is<Part::GeomEllipse>() || geometry->is<Part::GeomArcOfEllipse>()
            || geometry->is<Part::GeomArcOfHyperbola>()
            || geometry->is<Part::GeomArcOfParabola>()
            || geometry->is<Part::GeomLineSegment>()
            || geometry->is<Part::GeomBSplineCurve>() || geometry->is<Part::GeomPoint>());
}

bool referencesAxis(const Constraint* constraint)
{
    return constraint->First == GeoEnum::VAxis || constraint->First == GeoEnum::HAxis
        || constraint->Second == GeoEnum::VAxis || constraint->Second == GeoEnum::HAxis
        || constraint->Third == GeoEnum::VAxis || constraint->Third == GeoEnum::HAxis;
}

bool referencesExternal(const Constraint* constraint)
{
    return (constraint->First != GeoEnum::GeoUndef && constraint->First <= GeoEnum::RefExt)
        || (constraint->Second != GeoEnum::GeoUndef && constraint->Second <= GeoEnum::RefExt)
        || (constraint->Third != GeoEnum::GeoUndef && constraint->Third <= GeoEnum::RefExt);
}

Base::Vector3d scaledPoint(
    Base::Vector3d point,
    const Base::Vector3d& center,
    double scaleFactor
)
{
    point.x = (point.x - center.x) * scaleFactor + center.x;
    point.y = (point.y - center.y) * scaleFactor + center.y;
    return point;
}

void scaleGeometry(
    Part::Geometry* geometry,
    const Base::Vector3d& center,
    double scaleFactor
)
{
    if (geometry->is<Part::GeomCircle>()) {
        auto* circle = static_cast<Part::GeomCircle*>(geometry);
        circle->setRadius(circle->getRadius() * scaleFactor);
        circle->setCenter(scaledPoint(circle->getCenter(), center, scaleFactor));
    }
    else if (geometry->is<Part::GeomArcOfCircle>()) {
        auto* arc = static_cast<Part::GeomArcOfCircle*>(geometry);
        arc->setRadius(arc->getRadius() * scaleFactor);
        arc->setCenter(scaledPoint(arc->getCenter(), center, scaleFactor));
    }
    else if (geometry->is<Part::GeomLineSegment>()) {
        auto* line = static_cast<Part::GeomLineSegment*>(geometry);
        line->setPoints(
            scaledPoint(line->getStartPoint(), center, scaleFactor),
            scaledPoint(line->getEndPoint(), center, scaleFactor)
        );
    }
    else if (geometry->is<Part::GeomEllipse>()) {
        auto* ellipse = static_cast<Part::GeomEllipse*>(geometry);
        if (scaleFactor < 1.0) {
            ellipse->setMinorRadius(ellipse->getMinorRadius() * scaleFactor);
            ellipse->setMajorRadius(ellipse->getMajorRadius() * scaleFactor);
        }
        else {
            ellipse->setMajorRadius(ellipse->getMajorRadius() * scaleFactor);
            ellipse->setMinorRadius(ellipse->getMinorRadius() * scaleFactor);
        }
        ellipse->setCenter(scaledPoint(ellipse->getCenter(), center, scaleFactor));
    }
    else if (geometry->is<Part::GeomArcOfEllipse>()) {
        auto* arc = static_cast<Part::GeomArcOfEllipse*>(geometry);
        if (scaleFactor < 1.0) {
            arc->setMinorRadius(arc->getMinorRadius() * scaleFactor);
            arc->setMajorRadius(arc->getMajorRadius() * scaleFactor);
        }
        else {
            arc->setMajorRadius(arc->getMajorRadius() * scaleFactor);
            arc->setMinorRadius(arc->getMinorRadius() * scaleFactor);
        }
        arc->setCenter(scaledPoint(arc->getCenter(), center, scaleFactor));
    }
    else if (geometry->is<Part::GeomArcOfHyperbola>()) {
        auto* arc = static_cast<Part::GeomArcOfHyperbola*>(geometry);
        arc->setMajorRadius(arc->getMajorRadius() * scaleFactor);
        arc->setMinorRadius(arc->getMinorRadius() * scaleFactor);
        arc->setCenter(scaledPoint(arc->getCenter(), center, scaleFactor));
    }
    else if (geometry->is<Part::GeomArcOfParabola>()) {
        auto* arc = static_cast<Part::GeomArcOfParabola*>(geometry);
        arc->setFocal(arc->getFocal() * scaleFactor);
        double start = 0.0;
        double end = 0.0;
        arc->getRange(start, end, true);
        arc->setRange(start * scaleFactor, end * scaleFactor, true);
        arc->setCenter(scaledPoint(arc->getCenter(), center, scaleFactor));
    }
    else if (geometry->is<Part::GeomBSplineCurve>()) {
        auto* spline = static_cast<Part::GeomBSplineCurve*>(geometry);
        auto poles = spline->getPoles();
        for (auto& pole : poles) {
            pole = scaledPoint(std::move(pole), center, scaleFactor);
        }
        spline->setPoles(poles);
    }
    else if (geometry->is<Part::GeomPoint>()) {
        auto* point = static_cast<Part::GeomPoint*>(geometry);
        point->setPoint(scaledPoint(point->getPoint(), center, scaleFactor));
    }
}

}  // namespace

int SketchObject::scaleExact(
    const std::vector<int>& geometryIds,
    const Base::Vector3d& center,
    double scaleFactor,
    bool keepOriginals,
    bool allowOriginConstraints
)
{
    return scalePrepared(
        geometryIds,
        center,
        scaleFactor,
        keepOriginals,
        allowOriginConstraints
    );
}

int SketchObject::scalePrepared(
    const std::vector<int>& geometryIds,
    const Base::Vector3d& center,
    double scaleFactor,
    bool keepOriginals,
    bool allowOriginConstraints
)
{
    if (geometryIds.empty() || !std::isfinite(center.x) || !std::isfinite(center.y)
        || !std::isfinite(center.z) || !std::isfinite(scaleFactor)
        || std::abs(center.z) > Precision::Confusion()
        || scaleFactor <= Precision::Confusion()) {
        return -1;
    }
    const std::set<int> uniqueGeometryIds(geometryIds.begin(), geometryIds.end());
    if (uniqueGeometryIds.size() != geometryIds.size()) {
        return -1;
    }
    if (allowOriginConstraints
        && (keepOriginals || std::abs(center.x) > Precision::Confusion()
            || std::abs(center.y) > Precision::Confusion()
            || geometryIds.size() != static_cast<std::size_t>(Geometry.getSize()))) {
        return -1;
    }

    const auto& initialConstraints = Constraints.getValues();
    for (const int geometryId : geometryIds) {
        const auto* geometry = getGeometry(geometryId);
        if ((geometryId < 0 && geometryId > GeoEnum::RefExt)
            || !isScalableGeometry(geometry)) {
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
    const int initialGeometryCount = Geometry.getSize();
    const int firstCreatedGeometry = initialGeometryCount;

    std::vector<std::pair<std::size_t, long>> retainedFacadeIds;
    if (!keepOriginals) {
        for (std::size_t offset = 0; offset < geometryIds.size(); ++offset) {
            long facadeId = 0;
            if (geometryIds[offset] >= 0 && getGeometryId(geometryIds[offset], facadeId) == 0) {
                retainedFacadeIds.emplace_back(offset, facadeId);
            }
        }
    }

    std::vector<std::unique_ptr<Part::Geometry>> createdGeometry;
    createdGeometry.reserve(geometryIds.size());
    for (const int geometryId : geometryIds) {
        auto geometry = std::unique_ptr<Part::Geometry>(getGeometry(geometryId)->copy());
        scaleGeometry(geometry.get(), center, scaleFactor);
        createdGeometry.push_back(std::move(geometry));
    }

    auto mappedGeometry = [&](int geometryId) {
        if (geometryId < 0) {
            return allowOriginConstraints
                    && (geometryId == GeoEnum::HAxis || geometryId == GeoEnum::VAxis)
                ? geometryId
                : GeoEnum::GeoUndef;
        }
        const int index = selectedIndex(geometryIds, geometryId);
        return index < 0 ? GeoEnum::GeoUndef : firstCreatedGeometry + index;
    };

    std::vector<std::unique_ptr<Constraint>> createdConstraints;
    for (const auto* constraint : initialConstraints) {
        if (!constraint || constraint->First == GeoEnum::GeoUndef
            || (!allowOriginConstraints && referencesAxis(constraint))
            || referencesExternal(constraint)) {
            continue;
        }
        const int first = mappedGeometry(constraint->First);
        const int second = mappedGeometry(constraint->Second);
        const int third = mappedGeometry(constraint->Third);
        auto scaled = std::unique_ptr<Constraint>(constraint->copy());
        scaled->First = first;

        if ((constraint->Type == Symmetric || constraint->Type == Tangent
             || constraint->Type == Perpendicular || constraint->Type == Angle)
            && first != GeoEnum::GeoUndef && second != GeoEnum::GeoUndef
            && third != GeoEnum::GeoUndef) {
            scaled->Second = second;
            scaled->Third = third;
        }
        else if ((constraint->Type == Coincident || constraint->Type == Tangent
                  || constraint->Type == Symmetric || constraint->Type == Perpendicular
                  || constraint->Type == Parallel || constraint->Type == Equal
                  || constraint->Type == Angle || constraint->Type == PointOnObject
                  || constraint->Type == InternalAlignment)
                 && first != GeoEnum::GeoUndef && second != GeoEnum::GeoUndef
                 && third == GeoEnum::GeoUndef) {
            scaled->Second = second;
        }
        else if (constraint->Type == Angle && first != GeoEnum::GeoUndef
                 && second == GeoEnum::GeoUndef && third == GeoEnum::GeoUndef) {
            scaled->First = first;
        }
        else if ((constraint->Type == Radius || constraint->Type == Diameter)
                 && first != GeoEnum::GeoUndef) {
            scaled->setValue(constraint->getValue() * scaleFactor);
        }
        else if ((constraint->Type == Distance || constraint->Type == DistanceX
                  || constraint->Type == DistanceY)
                 && first != GeoEnum::GeoUndef && second != GeoEnum::GeoUndef) {
            scaled->Second = second;
            scaled->setValue(constraint->getValue() * scaleFactor);
        }
        else if ((constraint->Type == Distance || constraint->Type == DistanceX
                  || constraint->Type == DistanceY)
                 && first != GeoEnum::GeoUndef && constraint->Second == GeoEnum::GeoUndef) {
            scaled->setValue(constraint->getValue() * scaleFactor);
        }
        else if ((constraint->Type == Block || constraint->Type == Weight)
                 && first != GeoEnum::GeoUndef) {
            scaled->First = first;
        }
        else if ((constraint->Type == Vertical || constraint->Type == Horizontal)
                 && first != GeoEnum::GeoUndef
                 && (constraint->Second == GeoEnum::GeoUndef
                     || second != GeoEnum::GeoUndef)) {
            scaled->Second = second;
        }
        else {
            continue;
        }

        scaled->LabelDistance = constraint->LabelDistance * scaleFactor;
        if (constraint->Type != Radius && constraint->Type != Diameter) {
            scaled->LabelPosition = constraint->LabelPosition * scaleFactor;
        }
        createdConstraints.push_back(std::move(scaled));
    }

    std::vector<Part::Geometry*> geometryPointers;
    geometryPointers.reserve(createdGeometry.size());
    for (const auto& geometry : createdGeometry) {
        geometryPointers.push_back(geometry.get());
    }
    addGeometry(geometryPointers);
    for (const auto& constraint : createdConstraints) {
        addConstraint(constraint.get());
    }

    if (!keepOriginals) {
        if (delGeometries(geometryIds) != 0) {
            return -1;
        }
        const auto deletedInternalCount = static_cast<int>(std::ranges::count_if(
            geometryIds,
            [](int geometryId) {
                return geometryId >= 0;
            }
        ));
        const int firstFinalGeometry = initialGeometryCount - deletedInternalCount;
        std::vector<std::pair<int, long>> facadeAssignments;
        facadeAssignments.reserve(retainedFacadeIds.size());
        for (const auto& [offset, facadeId] : retainedFacadeIds) {
            facadeAssignments.emplace_back(
                firstFinalGeometry + static_cast<int>(offset),
                facadeId
            );
        }
        if (!facadeAssignments.empty() && setGeometryIds(std::move(facadeAssignments)) != 0) {
            return -1;
        }
    }

    solve();
    return static_cast<int>(createdGeometry.size());
}

std::unique_ptr<ScaleDiagnostic> SketchObject::diagnoseScale(
    const std::vector<int>& geometryIds,
    const Base::Vector3d& center,
    double scaleFactor,
    bool keepOriginals,
    bool allowOriginConstraints
) const
{
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

    auto result = std::make_unique<ScaleDiagnostic>();
    result->geometryIds = geometryIds;
    result->center = center;
    result->scaleFactor = scaleFactor;
    result->keepOriginals = keepOriginals;
    result->allowOriginConstraints = allowOriginConstraints;
    result->deletedOriginals = !keepOriginals;
    if (diagnostic->scalePrepared(
            geometryIds,
            center,
            scaleFactor,
            keepOriginals,
            allowOriginConstraints
        ) < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}
