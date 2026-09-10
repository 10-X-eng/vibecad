// SPDX-License-Identifier: LGPL-2.1-or-later
#include "SectionGeometry.h"

#include <stdexcept>
#include <BRepBuilderAPI_Copy.hxx>
#include <BRep_Tool.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Wire.hxx>
#include <gp.hxx>

#include "FaceMakerBullseye.h"
#include "TopoShape.h"

namespace
{
void checkCancellation(std::stop_token stop)
{
    if (stop.stop_requested()) {
        throw std::runtime_error("Section face preparation cancelled");
    }
}
}

TopoDS_Shape Part::prepareSectionFaces(
    const TopoDS_Shape& source, const Base::Matrix4D& displayedTransform,
    const Base::Vector3d& origin, const Base::Vector3d& normal, std::stop_token stop)
{
    checkCancellation(stop);
    if (source.IsNull() || !TopExp_Explorer(source, TopAbs_SOLID).More()) {
        return {};
    }
    if (normal.Length() <= gp::Resolution()) {
        throw std::invalid_argument("Section plane requires a nonzero normal");
    }
    auto direction = normal;
    direction.Normalize();
    const double distance = direction.Dot(origin);

    // Do all copying and kernel work on the compute worker. Only root location
    // is replaced: locations of children in a compound remain part of geometry.
    auto local = BRepBuilderAPI_Copy(source, Standard_True, Standard_False).Shape();
    local.Location(TopLoc_Location());
    checkCancellation(stop);
    TopoShape world(local);
    // Preserve analytic surfaces for rigid placements; use a general transform
    // only when instance scaling actually requires one.
    world.transformShape(displayedTransform, false, true);

    for (const double offset : {0.0, 1e-4, -1e-4}) {
        checkCancellation(stop);
        const auto section = world.makeElementSlice(direction, distance + offset);
        FaceMakerBullseye maker;
        maker.MyElementMapPolicy = ElementMapPolicy::Drop;
        bool hasWire = false;
        for (TopExp_Explorer wires(section.getShape(), TopAbs_WIRE); wires.More(); wires.Next()) {
            checkCancellation(stop);
            const auto wire = TopoDS::Wire(wires.Current());
            if (BRep_Tool::IsClosed(wire)) {
                maker.addWire(wire);
                hasWire = true;
            }
        }
        if (hasWire) {
            maker.Build();
            checkCancellation(stop);
            return maker.Shape();
        }
    }
    return {};
}
