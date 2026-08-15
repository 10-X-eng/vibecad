// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <algorithm>
#include <set>

#include "GeoEnum.h"
#include "GeometryFacade.h"
#include "SketchObject.h"

using namespace Sketcher;

namespace
{

bool isSymmetryGeometry(const Part::Geometry* geometry)
{
    return geometry
        && (geometry->is<Part::GeomLineSegment>() || geometry->is<Part::GeomCircle>()
            || geometry->is<Part::GeomArcOfCircle>() || geometry->is<Part::GeomEllipse>()
            || geometry->is<Part::GeomArcOfEllipse>()
            || geometry->is<Part::GeomArcOfHyperbola>()
            || geometry->is<Part::GeomArcOfParabola>()
            || geometry->is<Part::GeomBSplineCurve>() || geometry->is<Part::GeomPoint>());
}

bool exposesReferencePoint(const Part::Geometry* geometry, PointPos position)
{
    if (!geometry || position == PointPos::none) {
        return false;
    }
    if (geometry->is<Part::GeomPoint>()) {
        return position == PointPos::start;
    }
    if (geometry->is<Part::GeomLineSegment>() || geometry->is<Part::GeomBSplineCurve>()) {
        return position == PointPos::start || position == PointPos::end;
    }
    if (geometry->is<Part::GeomCircle>() || geometry->is<Part::GeomEllipse>()) {
        return position == PointPos::mid;
    }
    if (geometry->is<Part::GeomArcOfCircle>() || geometry->is<Part::GeomArcOfEllipse>()
        || geometry->is<Part::GeomArcOfHyperbola>()
        || geometry->is<Part::GeomArcOfParabola>()) {
        return position == PointPos::start || position == PointPos::end
            || position == PointPos::mid;
    }
    return false;
}

bool isValidSourceMode(SymmetrySourceMode sourceMode)
{
    return sourceMode == SymmetrySourceMode::Keep || sourceMode == SymmetrySourceMode::Delete
        || sourceMode == SymmetrySourceMode::Constrain;
}

}  // namespace

int SketchObject::symmetryExact(
    const std::vector<int>& geometryIds,
    int refGeoId,
    PointPos refPosId,
    SymmetrySourceMode sourceMode
)
{
    return symmetryPrepared(geometryIds, refGeoId, refPosId, sourceMode);
}

int SketchObject::symmetryPrepared(
    const std::vector<int>& geometryIds,
    int refGeoId,
    PointPos refPosId,
    SymmetrySourceMode sourceMode
)
{
    if (geometryIds.empty() || !isValidSourceMode(sourceMode)) {
        return -1;
    }
    const std::set<int> selected(geometryIds.begin(), geometryIds.end());
    if (selected.size() != geometryIds.size()) {
        return -1;
    }

    const auto& constraints = Constraints.getValues();
    for (const int geometryId : geometryIds) {
        if (geometryId == GeoEnum::HAxis || geometryId == GeoEnum::VAxis
            || geometryId == GeoEnum::GeoUndef) {
            return -1;
        }
        const auto* geometry = getGeometry(geometryId);
        if (!isSymmetryGeometry(geometry)) {
            return -1;
        }
        if (!GeometryFacade::isInternalAligned(geometry)) {
            continue;
        }
        const auto relation = std::ranges::find_if(constraints, [geometryId](const auto* item) {
            return item && item->Type == Sketcher::InternalAlignment
                && item->First == geometryId;
        });
        if (relation == constraints.end() || !selected.contains((*relation)->Second)) {
            return -1;
        }
    }

    if (refPosId == PointPos::none) {
        const auto* reference = getGeometry(refGeoId);
        if (!reference || !reference->is<Part::GeomLineSegment>()) {
            return -1;
        }
    }
    else if (refGeoId == GeoEnum::RtPnt && refPosId == PointPos::start) {
        // The root point shares geometry ID -1 with the horizontal axis.
    }
    else {
        if (refGeoId == GeoEnum::VAxis || refGeoId == GeoEnum::GeoUndef
            || !exposesReferencePoint(getGeometry(refGeoId), refPosId)) {
            return -1;
        }
    }

    const int initialGeometryCount = Geometry.getSize();
    const bool constrain = sourceMode == SymmetrySourceMode::Constrain;
    addSymmetric(geometryIds, refGeoId, refPosId, constrain);
    const int createdGeometryCount = Geometry.getSize() - initialGeometryCount;
    if (createdGeometryCount <= 0) {
        return -1;
    }
    if (sourceMode == SymmetrySourceMode::Delete && delGeometries(geometryIds) != 0) {
        return -1;
    }
    solve();
    return createdGeometryCount;
}

std::unique_ptr<SymmetryDiagnostic> SketchObject::diagnoseSymmetry(
    const std::vector<int>& geometryIds,
    int refGeoId,
    PointPos refPosId,
    SymmetrySourceMode sourceMode
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

    auto result = std::make_unique<SymmetryDiagnostic>();
    result->geometryIds = geometryIds;
    result->referenceGeometryId = refGeoId;
    result->referencePosition = refPosId;
    result->sourceMode = sourceMode;
    result->deletedOriginals = sourceMode == SymmetrySourceMode::Delete;
    result->constrainedSymmetry = sourceMode == SymmetrySourceMode::Constrain;
    if (diagnostic->symmetryPrepared(geometryIds, refGeoId, refPosId, sourceMode) < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}
