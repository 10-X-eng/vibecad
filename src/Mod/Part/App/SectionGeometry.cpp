// SPDX-License-Identifier: LGPL-2.1-or-later
#include "SectionGeometry.h"

#include <stdexcept>
#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <set>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRep_Builder.hxx>
#include <TopoDS_Compound.hxx>
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

// MSVC instantiates the exported FaceDriller destructor at the class definition.
#include "WireJoiner.h"
#include "FaceMakerBullseye.h"
#include "TopoShape.h"
#include "RenderMesh.h"

namespace
{
void checkCancellation(std::stop_token stop)
{
    if (stop.stop_requested()) {
        throw std::runtime_error("Section face preparation cancelled");
    }
}
}

TopoDS_Shape Part::prepareSectionMeshFaces(
    const RenderMesh& mesh, const Base::Matrix4D& transform,
    const Base::Vector3d& origin, const Base::Vector3d& normal, std::stop_token stop)
{
    checkCancellation(stop);
    auto direction = normal;
    if (!std::isfinite(direction.Length()) || direction.Length() <= gp::Resolution()) {
        throw std::invalid_argument("Section plane requires a finite nonzero normal");
    }
    direction.Normalize();
    if (mesh.vertices.size() % 3 || mesh.triangleIndices.size() % 4) {
        throw std::invalid_argument("Malformed section mesh buffers");
    }
    std::vector<Base::Vector3d> vertices;
    std::vector<double> distances;
    vertices.reserve(mesh.vertexCount());
    distances.reserve(mesh.vertexCount());
    double low = std::numeric_limits<double>::infinity();
    double high = -low;
    double scale = 1.0;
    for (std::size_t i = 0; i < mesh.vertexCount(); ++i) {
        if (i % 256 == 0) { checkCancellation(stop); }
        Base::Vector3d point;
        transform.multVec(Base::Vector3d(mesh.vertices[3*i], mesh.vertices[3*i+1], mesh.vertices[3*i+2]), point);
        point -= origin;
        if (!std::isfinite(point.Length())) {
            throw std::invalid_argument("Section mesh requires finite coordinates");
        }
        scale = std::max(scale, point.Length());
        const double distance = point.Dot(direction);
        low = std::min(low, distance);
        high = std::max(high, distance);
        vertices.push_back(point);
        distances.push_back(distance);
    }
    const double tolerance = std::max(1e-7, scale * 4 * std::numeric_limits<float>::epsilon());
    if (low > tolerance || high < -tolerance || vertices.empty()) { return {}; }

    using TriangleRange = std::pair<std::size_t, std::size_t>;
    const auto prepareGroup = [&](const std::vector<TriangleRange>& ranges) -> TopoDS_Shape {
        using Cell = std::array<std::int64_t, 3>;
        using Edge = std::pair<std::size_t, std::size_t>;
        std::map<Cell, std::vector<std::size_t>> cells;
        std::vector<Base::Vector3d> points;
        std::set<Edge> edges;
        const auto vertexId = [&](const Base::Vector3d& point) {
            Cell cell {static_cast<std::int64_t>(std::floor(point.x / tolerance)),
                       static_cast<std::int64_t>(std::floor(point.y / tolerance)),
                       static_cast<std::int64_t>(std::floor(point.z / tolerance))};
            // Search adjacent buckets too: rounding alone splits coincident seams
            // at bucket boundaries and leaves otherwise closed contours open.
            for (int x = -1; x <= 1; ++x) {
                for (int y = -1; y <= 1; ++y) {
                    for (int z = -1; z <= 1; ++z) {
                        const auto found = cells.find({cell[0]+x, cell[1]+y, cell[2]+z});
                        if (found == cells.end()) { continue; }
                        for (const auto index : found->second) {
                            if ((points[index] - point).Length() <= tolerance) { return index; }
                        }
                    }
                }
            }
            const auto index = points.size();
            points.push_back(point);
            cells[cell].push_back(index);
            return index;
        };
        for (const auto& [first, last] : ranges) {
            for (std::size_t i = first; i < last; i += 4) {
                if (i % 1024 == 0) { checkCancellation(stop); }
                if (mesh.triangleIndices[i+3] != -1) {
                    throw std::invalid_argument("Section mesh requires triangle delimiters");
                }
                std::array<std::size_t, 3> ids;
                std::array<double, 3> signedDistance;
                for (std::size_t j = 0; j < 3; ++j) {
                    const auto index = mesh.triangleIndices[i+j];
                    if (index < 0 || static_cast<std::size_t>(index) >= vertices.size()) {
                        throw std::invalid_argument("Invalid section triangle index");
                    }
                    ids[j] = static_cast<std::size_t>(index);
                    signedDistance[j] = std::abs(distances[ids[j]]) <= tolerance ? 0 : distances[ids[j]];
                }
                if (signedDistance[0] == 0 && signedDistance[1] == 0 && signedDistance[2] == 0) {
                    continue; // Adjacent non-coplanar triangles supply the perimeter.
                }
                // At a step or seam exactly on the plane, the two neighboring
                // surfaces can have different outlines. Only the positive (kept)
                // half-space supplies an on-plane edge; combining both outlines
                // creates spurious branches or hatches the removed side's footprint.
                if (std::count(signedDistance.begin(), signedDistance.end(), 0.0) == 2
                    && *std::min_element(signedDistance.begin(), signedDistance.end()) < 0.0) {
                    continue;
                }
                std::set<std::size_t> cut;
                for (std::size_t j = 0; j < 3; ++j) {
                    const auto next = (j+1) % 3;
                    if (signedDistance[j] == 0) {
                        const auto point = vertices[ids[j]] - direction * distances[ids[j]];
                        cut.insert(vertexId(point));
                    }
                    else if ((signedDistance[j] < 0 && signedDistance[next] > 0)
                             || (signedDistance[j] > 0 && signedDistance[next] < 0)) {
                        const double ratio = signedDistance[j] / (signedDistance[j] - signedDistance[next]);
                        cut.insert(vertexId(vertices[ids[j]] + (vertices[ids[next]] - vertices[ids[j]]) * ratio));
                    }
                }
                if (cut.size() == 2) { edges.emplace(*cut.begin(), *cut.rbegin()); }
            }
        }
        if (edges.empty()) { return {}; }
        std::vector<std::vector<std::size_t>> adjacent(points.size());
        for (const auto& [a, b] : edges) {
            adjacent[a].push_back(b);
            adjacent[b].push_back(a);
        }
        for (const auto& neighbors : adjacent) {
            if (!neighbors.empty() && neighbors.size() != 2) {
                throw std::runtime_error("Section mesh contains an open or branching contour");
            }
        }
        FaceMakerBullseye maker;
        maker.MyElementMapPolicy = ElementMapPolicy::Drop;
        while (!edges.empty()) {
            checkCancellation(stop);
            auto [first, current] = *edges.begin();
            auto previous = first;
            edges.erase(edges.begin());
            BRepBuilderAPI_MakePolygon polygon;
            const auto add = [&](std::size_t index) {
                const auto point = points[index] + origin;
                polygon.Add(gp_Pnt(point.x, point.y, point.z));
            };
            add(first);
            while (current != first) {
                checkCancellation(stop);
                add(current);
                const auto& neighbors = adjacent[current];
                const auto next = neighbors[0] == previous ? neighbors[1] : neighbors[0];
                if (!edges.erase(std::minmax(current, next))) {
                    throw std::runtime_error("Section mesh contour cannot be closed");
                }
                previous = current;
                current = next;
            }
            polygon.Close();
            if (!polygon.IsDone()) { throw std::runtime_error("Invalid section mesh polygon"); }
            maker.addWire(polygon.Wire());
        }
        maker.Build();
        checkCancellation(stop);
        return maker.Shape();
    };
    if (mesh.solidFaceIndices.empty()) {
        return prepareGroup({{0, mesh.triangleIndices.size()}});
    }

    // Transform each vertex once, then assemble independent solid contours.
    // Welding different solids together invents branches at valid contacts.
    std::vector<std::size_t> offsets {0};
    for (const auto count : mesh.faceTriangleCounts) {
        if (count < 0 || static_cast<std::size_t>(count) > (mesh.triangleIndices.size() - offsets.back()) / 4) {
            throw std::invalid_argument("Invalid section face triangle counts");
        }
        offsets.push_back(offsets.back() + static_cast<std::size_t>(count) * 4);
    }
    if (offsets.back() != mesh.triangleIndices.size()) {
        throw std::invalid_argument("Incomplete section face triangle counts");
    }
    BRep_Builder builder;
    TopoDS_Compound combined;
    builder.MakeCompound(combined);
    bool haveFaces = false;
    for (const auto& faces : mesh.solidFaceIndices) {
        checkCancellation(stop);
        std::vector<TriangleRange> ranges;
        ranges.reserve(faces.size());
        for (const auto index : faces) {
            if (index < 0 || static_cast<std::size_t>(index) >= mesh.faceTriangleCounts.size()) {
                throw std::invalid_argument("Invalid section solid face index");
            }
            ranges.emplace_back(offsets[index], offsets[index + 1]);
        }
        auto prepared = prepareGroup(ranges);
        if (mesh.solidFaceIndices.size() == 1) { return prepared; }
        if (!prepared.IsNull()) {
            builder.Add(combined, prepared);
            haveFaces = true;
        }
    }
    return haveFaces ? TopoDS_Shape(combined) : TopoDS_Shape();
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
