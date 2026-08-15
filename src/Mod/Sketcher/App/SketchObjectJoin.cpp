// SPDX-License-Identifier: LGPL-2.1-or-later

#include "PreCompiled.h"

#include <cstddef>
#include <memory>

#include <Precision.hxx>

#include <Base/Exception.h>
#include <Mod/Part/App/Geometry.h>

#include "SketchObject.h"

using namespace Sketcher;

namespace
{

constexpr int MaxJoinGeneratedGeometry = 4096;

bool isEndpoint(PointPos position)
{
    return position == PointPos::start || position == PointPos::end;
}

bool isExactJoinTarget(
    const SketchObject& sketch,
    int firstGeometry,
    PointPos firstEndpoint,
    int secondGeometry,
    PointPos secondEndpoint
)
{
    if (!isEndpoint(firstEndpoint) || !isEndpoint(secondEndpoint)
        || firstGeometry == secondGeometry || firstGeometry < 0 || secondGeometry < 0
        || firstGeometry > sketch.getHighestCurveIndex()
        || secondGeometry > sketch.getHighestCurveIndex()) {
        return false;
    }
    const auto* first = dynamic_cast<const Part::GeomCurve*>(
        sketch.getGeometry(firstGeometry));
    const auto* second = dynamic_cast<const Part::GeomCurve*>(
        sketch.getGeometry(secondGeometry));
    if (!first || !second
        || GeometryFacade::getConstruction(first)
            != GeometryFacade::getConstruction(second)) {
        return false;
    }
    try {
        const std::unique_ptr<Part::GeomBSplineCurve> firstSpline(
            first->toNurbs(first->getFirstParameter(), first->getLastParameter()));
        const std::unique_ptr<Part::GeomBSplineCurve> secondSpline(
            second->toNurbs(second->getFirstParameter(), second->getLastParameter()));
        if (!firstSpline || !secondSpline) {
            return false;
        }
        const auto firstClosure = firstSpline->getStartPoint() - firstSpline->getEndPoint();
        const auto secondClosure = secondSpline->getStartPoint() - secondSpline->getEndPoint();
        return !firstSpline->isPeriodic() && !secondSpline->isPeriodic()
            && firstClosure.Length() > Precision::Confusion()
            && secondClosure.Length() > Precision::Confusion();
    }
    catch (const Base::Exception&) {
        return false;
    }
}

int endpointContinuity(
    const SketchObject& sketch,
    int firstGeometry,
    PointPos firstEndpoint,
    int secondGeometry,
    PointPos secondEndpoint
)
{
    for (const auto* constraint : sketch.Constraints.getValues()) {
        if (constraint && constraint->Type == ConstraintType::Tangent
            && ((constraint->First == firstGeometry
                 && constraint->FirstPos == firstEndpoint
                 && constraint->Second == secondGeometry
                 && constraint->SecondPos == secondEndpoint)
                || (constraint->First == secondGeometry
                    && constraint->FirstPos == secondEndpoint
                    && constraint->Second == firstGeometry
                    && constraint->SecondPos == firstEndpoint))) {
            return 1;
        }
    }
    return 0;
}

bool generatedGeometryIsBounded(const SketchObject& before, const SketchObject& after)
{
    const int growth = after.Geometry.getSize() - before.Geometry.getSize();
    return growth <= MaxJoinGeneratedGeometry;
}

}  // namespace

std::unique_ptr<SketchObject> SketchObject::diagnoseJoinCurves(
    int firstGeometry,
    PointPos firstEndpoint,
    int secondGeometry,
    PointPos secondEndpoint
) const
{
    if (!isExactJoinTarget(
            *this,
            firstGeometry,
            firstEndpoint,
            secondGeometry,
            secondEndpoint)) {
        return nullptr;
    }
    const int continuity = endpointContinuity(
        *this,
        firstGeometry,
        firstEndpoint,
        secondGeometry,
        secondEndpoint);
    auto diagnostic = makeGeometryMutationDiagnosticClone();
    try {
        if (diagnostic->join(
                firstGeometry,
                firstEndpoint,
                secondGeometry,
                secondEndpoint,
                continuity)
            != 0
            || !generatedGeometryIsBounded(*this, *diagnostic)) {
            return nullptr;
        }
        diagnostic->solve(true);
    }
    catch (const Base::Exception& error) {
        throw Base::RuntimeError(
            std::string("Unable to create the Join Curves diagnostic state: ")
            + error.what());
    }
    return diagnostic;
}

int SketchObject::joinCurvesExact(
    int firstGeometry,
    PointPos firstEndpoint,
    int secondGeometry,
    PointPos secondEndpoint
)
{
    if (!diagnoseJoinCurves(
            firstGeometry,
            firstEndpoint,
            secondGeometry,
            secondEndpoint)) {
        return -1;
    }
    const int continuity = endpointContinuity(
        *this,
        firstGeometry,
        firstEndpoint,
        secondGeometry,
        secondEndpoint);
    return join(
               firstGeometry,
               firstEndpoint,
               secondGeometry,
               secondEndpoint,
               continuity)
            == 0
        ? 1
        : -1;
}
