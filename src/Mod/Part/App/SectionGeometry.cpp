// SPDX-License-Identifier: LGPL-2.1-or-later
#include "SectionGeometry.h"

#include <stdexcept>
#include <algorithm>
#include <cmath>
#include <limits>
#include <BRepAdaptor_CompCurve.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <GCPnts_UniformDeflection.hxx>
#include <Poly_Triangulation.hxx>
#include <TopoDS_Face.hxx>
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

Part::SectionDisplayGeometry Part::prepareSectionDisplay(
    const TopoDS_Shape& faces, const Base::Vector3d& origin,
    const Base::Vector3d& normal, double spacing, std::stop_token stop)
{
    checkCancellation(stop);
    if (!std::isfinite(spacing) || spacing <= 0.0) {
        throw std::invalid_argument("Hatch spacing must be finite and positive");
    }
    auto direction = normal;
    if (!std::isfinite(direction.Length()) || direction.Length() <= gp::Resolution()) {
        throw std::invalid_argument("Section plane requires a finite nonzero normal");
    }
    direction.Normalize();
    const auto helper = std::abs(direction.z) < 0.9 ? Base::Vector3d(0, 0, 1)
                                                   : Base::Vector3d(0, 1, 0);
    auto u = direction.Cross(helper);
    u.Normalize();
    auto v = direction.Cross(u);
    v.Normalize();
    const auto offset = direction * -0.05;
    SectionDisplayGeometry result;
    if (faces.IsNull()) { return result; }

    // Triangulation modifies topology caches. Give it private topology while
    // sharing the read-only analytic curves/surfaces of these detached faces.
    const auto display = BRepBuilderAPI_Copy(faces, Standard_False, Standard_False).Shape();
    BRepMesh_IncrementalMesh mesher(display, 0.25);
    using Point2 = std::array<double, 2>;
    std::vector<std::vector<Point2>> rings;
    const auto point3 = [](const gp_Pnt& point) {
        return Base::Vector3d(point.X(), point.Y(), point.Z());
    };
    for (TopExp_Explorer faceIterator(display, TopAbs_FACE); faceIterator.More(); faceIterator.Next()) {
        checkCancellation(stop);
        const auto face = TopoDS::Face(faceIterator.Current());
        TopLoc_Location location;
        const auto triangulation = BRep_Tool::Triangulation(face, location);
        if (!triangulation.IsNull()) {
            for (int index = 1; index <= triangulation->NbTriangles(); ++index) {
                checkCancellation(stop);
                int a, b, c;
                triangulation->Triangle(index).Get(a, b, c);
                if (face.Orientation() == TopAbs_REVERSED) { std::swap(b, c); }
                result.triangles.push_back({
                    point3(triangulation->Node(a).Transformed(location.Transformation())) + offset,
                    point3(triangulation->Node(b).Transformed(location.Transformation())) + offset,
                    point3(triangulation->Node(c).Transformed(location.Transformation())) + offset
                });
            }
        }
        for (TopExp_Explorer wires(face, TopAbs_WIRE); wires.More(); wires.Next()) {
            checkCancellation(stop);
            BRepAdaptor_CompCurve curve(TopoDS::Wire(wires.Current()));
            GCPnts_UniformDeflection discretizer(curve, 0.25, curve.FirstParameter(), curve.LastParameter());
            if (!discretizer.IsDone()) {
                throw std::runtime_error("Section outline discretization failed");
            }
            std::vector<Base::Vector3d> points;
            for (int index = 1; index <= discretizer.NbPoints(); ++index) {
                points.push_back(point3(discretizer.Value(index)));
            }
            if (points.size() > 1 && (points.front() - points.back()).Length() < 1e-9) {
                points.pop_back();
            }
            if (points.size() < 3) { continue; }
            std::vector<Point2> ring;
            for (std::size_t index = 0; index < points.size(); ++index) {
                const auto relative = points[index] - origin;
                ring.push_back({relative.Dot(u), relative.Dot(v)});
                result.outlines.push_back({points[index] + offset,
                                           points[(index + 1) % points.size()] + offset});
            }
            rings.push_back(std::move(ring));
        }
    }
    if (rings.empty()) { return result; }

    // Rotate plane coordinates into 45-degree hatch coordinates, then pair
    // crossings with an even/odd fill rule. Half-open edges handle vertices
    // shared by adjacent segments without filling holes or tangent contacts.
    constexpr double diagonal = 0.70710678118654752440;
    double minimum = std::numeric_limits<double>::infinity();
    double maximum = -minimum;
    for (auto& ring : rings) {
        for (auto& point : ring) {
            point = {(point[0] + point[1]) * diagonal, (-point[0] + point[1]) * diagonal};
            minimum = std::min(minimum, point[1]);
            maximum = std::max(maximum, point[1]);
        }
    }
    double across = std::floor(minimum / spacing) * spacing;
    if (!std::isfinite(across)) { throw std::invalid_argument("Hatch spacing is too small"); }
    while (across <= maximum + 1e-9) {
        checkCancellation(stop);
        std::vector<double> hits;
        for (const auto& ring : rings) {
            for (std::size_t index = 0; index < ring.size(); ++index) {
                const auto& a = ring[index];
                const auto& b = ring[(index + 1) % ring.size()];
                if ((a[1] <= across && across < b[1]) || (b[1] <= across && across < a[1])) {
                    hits.push_back(a[0] + (b[0] - a[0]) * (across - a[1]) / (b[1] - a[1]));
                }
            }
        }
        std::sort(hits.begin(), hits.end());
        const auto unproject = [&](double along) {
            return origin + u * ((along - across) * diagonal)
                          + v * ((along + across) * diagonal) + offset;
        };
        for (std::size_t index = 1; index < hits.size(); index += 2) {
            if (hits[index] - hits[index - 1] > 1e-9) {
                result.hatch.push_back({unproject(hits[index - 1]), unproject(hits[index])});
            }
        }
        const double next = across + spacing;
        if (!(next > across)) { throw std::invalid_argument("Hatch spacing is too small"); }
        across = next;
    }
    return result;
}
