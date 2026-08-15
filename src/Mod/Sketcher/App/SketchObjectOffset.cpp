// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <algorithm>
#include <cmath>
#include <memory>
#include <set>
#include <utility>
#include <vector>

#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepOffsetAPI_MakeOffset.hxx>
#include <Geom_Circle.hxx>
#include <Geom_Ellipse.hxx>
#include <Geom_TrimmedCurve.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <gp_Pln.hxx>

#include <Base/Exception.h>
#include <Base/Tools.h>

#include "GeoEnum.h"
#include "GeometryFacade.h"
#include "SketchObject.h"
#include "SketchObjectOffsetInternal.h"

using namespace Sketcher;

namespace
{

bool isOffsetGeometry(const Part::Geometry* geometry)
{
    return geometry
        && (geometry->is<Part::GeomLineSegment>() || geometry->is<Part::GeomCircle>()
            || geometry->is<Part::GeomArcOfCircle>())
        && !GeometryFacade::isInternalAligned(geometry);
}

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

class OffsetOperation
{
public:
    OffsetOperation(
        SketchObject& sketch,
        const std::vector<int>& geometryIds,
        double offsetLength,
        OffsetJoinType joinType,
        OffsetSourceMode sourceMode
    )
        : sketch(sketch)
        , geometryIds(geometryIds)
        , offsetLength(offsetLength)
        , joinType(joinType)
        , sourceMode(sourceMode)
        , firstCreatedGeometry(sketch.Geometry.getSize())
    {}

    int apply()
    {
        if (!validate() || !generateSourceWires() || !createOffsetGeometry()) {
            return -1;
        }
        joinOffsetCurves();
        if (sourceMode == OffsetSourceMode::Delete) {
            if (sketch.delGeometries(geometryIds) != 0) {
                return -1;
            }
        }
        else if (sourceMode == OffsetSourceMode::Constrain) {
            OffsetInternal::constrainOffset(
                sketch,
                geometryIds,
                offsetGeometryIds,
                offsetLength
            );
        }
        sketch.solve();
        return static_cast<int>(offsetGeometryIds.size());
    }

private:
    bool validate() const
    {
        if (geometryIds.empty() || !std::isfinite(offsetLength)
            || std::abs(offsetLength) <= Precision::Confusion()
            || (joinType != OffsetJoinType::Arc
                && joinType != OffsetJoinType::Intersection)
            || (sourceMode != OffsetSourceMode::Keep
                && sourceMode != OffsetSourceMode::Delete
                && sourceMode != OffsetSourceMode::Constrain)) {
            return false;
        }
        const std::set<int> uniqueIds(geometryIds.begin(), geometryIds.end());
        if (uniqueIds.size() != geometryIds.size()) {
            return false;
        }
        return std::ranges::all_of(geometryIds, [this](int geometryId) {
            return (geometryId >= 0 || geometryId <= GeoEnum::RefExt)
                && isOffsetGeometry(sketch.getGeometry(geometryId));
        });
    }

    bool generateSourceWires()
    {
        sourceCurves = OffsetInternal::connectedCurves(sketch, geometryIds);
        for (const auto& curve : sourceCurves) {
            BRepBuilderAPI_MakeWire wireBuilder;
            for (const int geometryId : curve) {
                auto geometry = std::unique_ptr<Part::Geometry>(
                    sketch.getGeometry(geometryId)->copy());
                geometry->reverseIfReversed();
                const auto shape = geometry->toShape();
                if (shape.IsNull() || shape.ShapeType() != TopAbs_EDGE) {
                    return false;
                }
                wireBuilder.Add(TopoDS::Edge(shape));
            }
            if (!wireBuilder.IsDone()) {
                return false;
            }
            TopoDS_Wire wire = wireBuilder.Wire();
            if (wire.Closed()) {
                BRepBuilderAPI_MakeFace faceBuilder(wire);
                if (faceBuilder.IsDone()) {
                    BRepAdaptor_Surface surface(faceBuilder.Face());
                    if (surface.GetType() == GeomAbs_Plane
                        && surface.Plane().Axis().Direction().Z() < 0) {
                        wire.Reverse();
                    }
                }
            }
            if (curve.size() == 1
                && sketch.getGeometry(curve.front())->is<Part::GeomLineSegment>()) {
                sourceWires.push_back(wire);
            }
            else {
                sourceWires.insert(sourceWires.begin(), wire);
                onlySingleLines = false;
            }
        }
        return !sourceWires.empty();
    }

    TopoDS_Shape makeOffsetShape() const
    {
        BRepOffsetAPI_MakeOffset offsetBuilder;
        if (onlySingleLines) {
            offsetBuilder = BRepOffsetAPI_MakeOffset(
                BRepBuilderAPI_MakeFace(gp_Pln(gp::Origin(), gp::DZ())));
        }
        const auto join = joinType == OffsetJoinType::Arc ? GeomAbs_Arc
                                                          : GeomAbs_Intersection;
        offsetBuilder.Init(join, false);
        for (const auto& wire : sourceWires) {
            offsetBuilder.AddWire(wire);
        }
        try {
            offsetBuilder.Perform(offsetLength);
        }
        catch (const Standard_Failure&) {
            throw;
        }
        catch (...) {
            throw Base::CADKernelError(
                "BRepOffsetAPI_MakeOffset crashed while creating Sketch Offset geometry.");
        }
        auto result = offsetBuilder.Shape();
        return result.IsNull() ? result : BRepBuilderAPI_Copy(result).Shape();
    }

    static std::unique_ptr<Part::Geometry> lineGeometry(const BRepAdaptor_Curve& curve)
    {
        double first = curve.FirstParameter();
        double last = curve.LastParameter();
        if (std::abs(first) > 1.0e99) {
            first = -10000.0;
        }
        if (std::abs(last) > 1.0e99) {
            last = 10000.0;
        }
        const gp_Pnt start = curve.Value(first);
        const gp_Pnt end = curve.Value(last);
        auto geometry = std::make_unique<Part::GeomLineSegment>();
        geometry->setPoints(
            Base::Vector3d(start.X(), start.Y(), start.Z()),
            Base::Vector3d(end.X(), end.Y(), end.Z())
        );
        GeometryFacade::setConstruction(geometry.get(), false);
        return geometry;
    }

    static std::unique_ptr<Part::Geometry> circleGeometry(const BRepAdaptor_Curve& curve)
    {
        const gp_Circ circle = curve.Circle();
        const gp_Pnt start = curve.Value(curve.FirstParameter());
        const gp_Pnt end = curve.Value(curve.LastParameter());
        const gp_Pnt center = circle.Location();
        if (start.SquareDistance(end) < Precision::Confusion()) {
            auto geometry = std::make_unique<Part::GeomCircle>();
            geometry->setRadius(circle.Radius());
            geometry->setCenter(Base::Vector3d(center.X(), center.Y(), center.Z()));
            GeometryFacade::setConstruction(geometry.get(), false);
            return geometry;
        }
        auto geometry = std::make_unique<Part::GeomArcOfCircle>();
        Handle(Geom_Circle) handle = new Geom_Circle(circle);
        geometry->setHandle(new Geom_TrimmedCurve(
            handle,
            curve.FirstParameter(),
            curve.LastParameter()
        ));
        geometry->reverseIfReversed();
        GeometryFacade::setConstruction(geometry.get(), false);
        return geometry;
    }

    static std::unique_ptr<Part::Geometry> ellipseGeometry(const BRepAdaptor_Curve& curve)
    {
        const gp_Elips ellipse = curve.Ellipse();
        const gp_Pnt start = curve.Value(curve.FirstParameter());
        const gp_Pnt end = curve.Value(curve.LastParameter());
        Handle(Geom_Ellipse) handle = new Geom_Ellipse(ellipse);
        if (start.SquareDistance(end) < Precision::Confusion()) {
            auto geometry = std::make_unique<Part::GeomEllipse>();
            geometry->setHandle(handle);
            geometry->reverseIfReversed();
            GeometryFacade::setConstruction(geometry.get(), false);
            return geometry;
        }
        auto geometry = std::make_unique<Part::GeomArcOfEllipse>();
        geometry->setHandle(new Geom_TrimmedCurve(
            handle,
            curve.FirstParameter(),
            curve.LastParameter()
        ));
        geometry->reverseIfReversed();
        GeometryFacade::setConstruction(geometry.get(), false);
        return geometry;
    }

    bool createOffsetGeometry()
    {
        const TopoDS_Shape shape = makeOffsetShape();
        if (shape.IsNull()) {
            return false;
        }
        std::vector<std::unique_ptr<Part::Geometry>> geometries;
        for (TopExp_Explorer explorer(shape, TopAbs_EDGE); explorer.More(); explorer.Next()) {
            BRepAdaptor_Curve curve(TopoDS::Edge(explorer.Current()));
            std::unique_ptr<Part::Geometry> geometry;
            if (curve.GetType() == GeomAbs_Line) {
                geometry = lineGeometry(curve);
            }
            else if (curve.GetType() == GeomAbs_Circle) {
                geometry = circleGeometry(curve);
            }
            else if (curve.GetType() == GeomAbs_Ellipse) {
                geometry = ellipseGeometry(curve);
            }
            else {
                return false;
            }
            geometries.push_back(std::move(geometry));
        }
        if (geometries.empty()) {
            return false;
        }
        std::vector<Part::Geometry*> values;
        values.reserve(geometries.size());
        offsetGeometryIds.reserve(geometries.size());
        for (std::size_t index = 0; index < geometries.size(); ++index) {
            values.push_back(geometries[index].get());
            offsetGeometryIds.push_back(firstCreatedGeometry + static_cast<int>(index));
        }
        sketch.addGeometry(values);
        return true;
    }

    bool needsTangent(int first, int second, PointPos firstPos, PointPos secondPos) const
    {
        const auto* firstGeometry = sketch.getGeometry(first);
        const auto* secondGeometry = sketch.getGeometry(second);
        if ((!firstGeometry || !firstGeometry->is<Part::GeomArcOfCircle>())
            && (!secondGeometry || !secondGeometry->is<Part::GeomArcOfCircle>())) {
            return false;
        }

        const auto tangentDirection = [](const Part::Geometry* geometry, PointPos position) {
            if (geometry->is<Part::GeomArcOfCircle>()) {
                const auto* arc = static_cast<const Part::GeomArcOfCircle*>(geometry);
                const auto point = position == PointPos::start ? arc->getStartPoint(true)
                                                               : arc->getEndPoint(true);
                const auto radius = arc->getCenter() - point;
                return Base::Vector3d(-radius.y, radius.x, 0.0);
            }
            if (geometry->is<Part::GeomLineSegment>()) {
                const auto* line = static_cast<const Part::GeomLineSegment*>(geometry);
                return line->getStartPoint() - line->getEndPoint();
            }
            return Base::Vector3d();
        };
        const auto firstDirection = tangentDirection(firstGeometry, firstPos);
        const auto secondDirection = tangentDirection(secondGeometry, secondPos);
        return firstDirection.Length() > Precision::Confusion()
            && secondDirection.Length() > Precision::Confusion()
            && (firstDirection % secondDirection).Length() < Precision::Confusion();
    }

    void joinOffsetCurves()
    {
        std::vector<std::unique_ptr<Constraint>> constraints;
        for (std::size_t firstIndex = 0; firstIndex + 1 < offsetGeometryIds.size();
             ++firstIndex) {
            for (std::size_t secondIndex = firstIndex + 1;
                 secondIndex < offsetGeometryIds.size();
                 ++secondIndex) {
                const int first = offsetGeometryIds[firstIndex];
                const int second = offsetGeometryIds[secondIndex];
                Base::Vector3d firstStart;
                Base::Vector3d firstEnd;
                Base::Vector3d secondStart;
                Base::Vector3d secondEnd;
                if (!OffsetInternal::getEndpoints(sketch, first, firstStart, firstEnd)
                    || !OffsetInternal::getEndpoints(
                        sketch,
                        second,
                        secondStart,
                        secondEnd
                    )) {
                    continue;
                }
                PointPos firstPos = PointPos::none;
                PointPos secondPos = PointPos::none;
                if ((firstStart - secondStart).Length() < Precision::Confusion()) {
                    firstPos = PointPos::start;
                    secondPos = PointPos::start;
                }
                else if ((firstStart - secondEnd).Length() < Precision::Confusion()) {
                    firstPos = PointPos::start;
                    secondPos = PointPos::end;
                }
                else if ((firstEnd - secondStart).Length() < Precision::Confusion()) {
                    firstPos = PointPos::end;
                    secondPos = PointPos::start;
                }
                else if ((firstEnd - secondEnd).Length() < Precision::Confusion()) {
                    firstPos = PointPos::end;
                    secondPos = PointPos::end;
                }
                if (firstPos == PointPos::none) {
                    continue;
                }
                constraints.push_back(relation(
                    needsTangent(first, second, firstPos, secondPos) ? Tangent : Coincident,
                    first,
                    firstPos,
                    second,
                    secondPos
                ));
            }
        }
        addConstraints(sketch, constraints);
    }

private:
    SketchObject& sketch;
    const std::vector<int>& geometryIds;
    double offsetLength;
    OffsetJoinType joinType;
    OffsetSourceMode sourceMode;
    int firstCreatedGeometry;
    bool onlySingleLines {true};
    std::vector<std::vector<int>> sourceCurves;
    std::vector<TopoDS_Wire> sourceWires;
    std::vector<int> offsetGeometryIds;
};

}  // namespace

int SketchObject::offsetExact(
    const std::vector<int>& geometryIds,
    double offsetLength,
    OffsetJoinType joinType,
    OffsetSourceMode sourceMode
)
{
    return offsetPrepared(geometryIds, offsetLength, joinType, sourceMode);
}

int SketchObject::offsetPrepared(
    const std::vector<int>& geometryIds,
    double offsetLength,
    OffsetJoinType joinType,
    OffsetSourceMode sourceMode
)
{
    Base::StateLocker lock(managedoperation, true);
    return OffsetOperation(*this, geometryIds, offsetLength, joinType, sourceMode).apply();
}

std::unique_ptr<OffsetDiagnostic> SketchObject::diagnoseOffset(
    const std::vector<int>& geometryIds,
    double offsetLength,
    OffsetJoinType joinType,
    OffsetSourceMode sourceMode
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

    auto result = std::make_unique<OffsetDiagnostic>();
    result->geometryIds = geometryIds;
    result->offsetLength = offsetLength;
    result->joinType = joinType;
    result->sourceMode = sourceMode;
    result->deletedOriginals = sourceMode == OffsetSourceMode::Delete;
    result->constrainedOffset = sourceMode == OffsetSourceMode::Constrain;
    if (diagnostic->offsetPrepared(geometryIds, offsetLength, joinType, sourceMode) < 0) {
        return {};
    }
    result->sketch = std::move(diagnostic);
    return result;
}
